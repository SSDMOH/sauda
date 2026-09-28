"""Real Swiggy adapter — live food-delivery prices via Swiggy's open search API.

Transport strategy (verified 2026-09-28, live from this workspace):
- Swiggy's web frontend calls an UNSIGNED search API — no cookies, no session,
  no CSRF token. Plain HTTPS from a datacenter egress works (unlike Blinkit,
  Swiggy does not Cloudflare-403 the dapi endpoints):
      GET https://www.swiggy.com/dapi/restaurants/search/v3
        ?lat=<lat>&lng=<lng>&str=<query>&submitAction=ENTER
  -> {"statusCode": 0, "data": {"cards": [
        {"groupedCard": {"cardGroupMap": {"DISH": {"cards": [...]}}}}]}}
- Each DISH card with `@type` containing "DishGroup" carries one restaurant
  (`restaurant.info`: id, name, avgRating, sla.deliveryTime, costForTwo in
  paise, feeDetails) and its matching dishes
  (`dishes[].info`: id, name, price in paise, inStock, variantsV2 with
  pricingModels for size variants).
- So this adapter uses a plain-HTTP transport (httpx, lazy import). The
  transport is pluggable (SwiggyTransport ABC) — a Playwright fallback can be
  dropped in later if Swiggy ever starts bot-blocking datacenter IPs the way
  Blinkit does.

Guest mode: search + prices work WITHOUT login. Account linking (OTP flow)
is not implemented — link_account/verify_otp raise NotImplementedError with
a clear message, and the API layer converts that to HTTP 501.

Mapping notes (food is not grocery SKUs):
- One Product per (restaurant, dish): gtin "swiggy:<rest_id>:<dish_id>",
  brand = restaurant name, name = "<dish> @ <restaurant>".
- pack_size=1, unit="serving", pack_label="1 serving". Swiggy dishes expose
  no MRP, so mrp == price.
- Dish price resolution (paise -> INR): variantsV2.pricingModels[0].price
  (default size variant) -> info.price -> info.defaultPrice.
- The search API does NOT expose per-order delivery/packaging fees
  (feeDetails is empty in search responses), so get_price uses the adapter's
  documented FeeTable defaults and freshness stays "live" for the dish price
  itself — fees are labelled as the adapter default, never as live data.
- ETA comes from the restaurant's sla.deliveryTime when present.

Response shape verified against live responses captured 2026-09-28.
The parser is defensive: unknown widget shapes are skipped, never fatal.
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import quote_plus

from adapters.base import PlatformAdapter
from models import (
    AdapterStatus,
    FeeTable,
    PriceSnapshot,
    PriceUnavailableError,
    Product,
    UserSession,
    utcnow,
)

log = logging.getLogger("sauda.real_swiggy")

# ---------------------------------------------------------------------------
# Endpoint constants (verified 2026-09-28 — unsigned, no session needed)
# ---------------------------------------------------------------------------

SEARCH_URL = "https://www.swiggy.com/dapi/restaurants/search/v3"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

DEFAULT_LAT = 28.6139   # Delhi — same default as the REST API
DEFAULT_LON = 77.2090

CACHE_TTL_S = 120        # short cache; snapshots carry honest timestamps
HEALTH_TTL_S = 60
CONCURRENCY = 2
MIN_REQUEST_INTERVAL_S = 0.5
REQUEST_TIMEOUT_S = 20


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _paise_to_inr(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(value / 100.0, 2)


def dish_price_inr(info: dict) -> float | None:
    """Resolve a dish's selling price in INR from its info object.

    Priority: default size-variant model -> info.price -> info.defaultPrice.
    All Swiggy food prices are in paise. Returns None when no price is found
    (caller drops the dish rather than guessing).
    """
    if not isinstance(info, dict):
        return None
    v2 = info.get("variantsV2") or {}
    models = v2.get("pricingModels") if isinstance(v2, dict) else None
    if isinstance(models, list) and models:
        first = models[0] if isinstance(models[0], dict) else {}
        price = _paise_to_inr(first.get("price"))
        if price is not None:
            return price
    for key in ("price", "defaultPrice"):
        price = _paise_to_inr(info.get(key))
        if price is not None:
            return price
    return None


def parse_search_response(payload: dict | None) -> list[dict]:
    """Parse a /dapi/restaurants/search/v3 JSON body into raw dish dicts.

    Defensive: unknown card shapes are skipped, never fatal. Each returned
    dict has keys: dish_id, dish_name, price_inr, restaurant {id, name,
    rating, delivery_time_min, cost_for_two_inr, locality}.
    """
    if not isinstance(payload, dict):
        return []
    if payload.get("statusCode") not in (0, None):
        return []
    out: list[dict] = []
    cards = (payload.get("data") or {}).get("cards")
    if not isinstance(cards, list):
        return []
    for card in cards:
        if not isinstance(card, dict):
            continue
        group_map = ((card.get("groupedCard") or {}).get("cardGroupMap")) or {}
        dish_cards = ((group_map.get("DISH") or {}).get("cards")) or []
        for entry in dish_cards:
            inner = ((entry.get("card") or {}).get("card")) or {}
            if "DishGroup" not in str(inner.get("@type") or ""):
                continue
            rest_info = ((inner.get("restaurant") or {}).get("info")) or {}
            rest_id = str(rest_info.get("id") or "")
            rest_name = rest_info.get("name") or ""
            if not rest_id or not rest_name:
                continue
            sla = rest_info.get("sla") or {}
            restaurant = {
                "id": rest_id,
                "name": rest_name,
                "rating": rest_info.get("avgRating"),
                "delivery_time_min": sla.get("deliveryTime")
                if isinstance(sla.get("deliveryTime"), (int, float)) else None,
                "cost_for_two_inr": _paise_to_inr(rest_info.get("costForTwo")),
                "locality": rest_info.get("locality") or rest_info.get("areaName"),
            }
            dishes = inner.get("dishes")
            if not isinstance(dishes, list):
                continue
            for dish in dishes:
                info = (dish.get("info") if isinstance(dish, dict) else None) or {}
                dish_id = str(info.get("id") or "")
                dish_name = info.get("name") or ""
                if not dish_id or not dish_name:
                    continue
                if info.get("inStock") == 0:
                    continue  # out of stock -> dropped, like sold_out on Blinkit
                price = dish_price_inr(info)
                if price is None:
                    continue
                out.append({
                    "dish_id": dish_id,
                    "dish_name": dish_name,
                    "price_inr": price,
                    "restaurant": restaurant,
                })
    return out


# ---------------------------------------------------------------------------
# Transports — how the search API is reached
# ---------------------------------------------------------------------------

class SwiggyTransport(ABC):
    """Pluggable transport: given a query + coords, return raw search JSON."""

    @abstractmethod
    async def search_raw(self, query: str, lat: float, lon: float) -> dict:
        ...


class StubTransport(SwiggyTransport):
    """In-memory transport for tests and offline demos."""

    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {"statusCode": 0, "data": {"cards": []}}
        self.calls: list[tuple[str, float, float]] = []

    async def search_raw(self, query: str, lat: float, lon: float) -> dict:
        self.calls.append((query, lat, lon))
        return self.payload


class DirectTransport(SwiggyTransport):
    """Real transport: plain HTTPS to Swiggy's unsigned dapi search endpoint.

    Verified 2026-09-28: works without cookies/session from datacenter egress.
    `import httpx` is lazy so importing this module can never break backend
    boot (the SAUDA_REAL_SWIGGY flag path checks nothing at import time).
    """

    def __init__(self, timeout_s: int = REQUEST_TIMEOUT_S) -> None:
        self.timeout_s = timeout_s
        self._sem = asyncio.Semaphore(CONCURRENCY)
        self._last_request_at = 0.0

    async def search_raw(self, query: str, lat: float, lon: float) -> dict:
        import httpx  # lazy: keeps module import side-effect free

        url = (f"{SEARCH_URL}?lat={lat}&lng={lon}"
               f"&str={quote_plus(query)}&submitAction=ENTER")
        async with self._sem:
            wait = MIN_REQUEST_INTERVAL_S - (time.monotonic() - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with httpx.AsyncClient(timeout=self.timeout_s,
                                             follow_redirects=True) as client:
                    resp = await client.get(url, headers={"User-Agent": UA,
                                                          "Accept": "*/*"})
            except Exception as exc:
                raise RuntimeError(f"Swiggy search request failed: {exc}") from exc
            self._last_request_at = time.monotonic()
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Swiggy search API returned HTTP {resp.status_code}")
            try:
                return resp.json()
            except ValueError as exc:
                raise RuntimeError("Swiggy returned a non-JSON body") from exc


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    product: Product
    snapshot: PriceSnapshot
    stored_at: float


class RealSwiggyAdapter(PlatformAdapter):
    """Live Swiggy food-delivery prices. platform_id stays "swiggy"."""

    platform_id = "swiggy"
    display_name = "Swiggy (live)"
    vertical = "food"
    uses_static_catalog = False   # prices come from live search, not catalog.py
    fee_table = FeeTable(
        platform_id="swiggy",
        # Documented defaults: search v3 exposes no per-order fees, so these
        # are adapter defaults, NOT live data.
        min_order_value=149.0,
        free_delivery_above=199.0,
        delivery_fee_base=30.0,
        platform_fee=5.0,
        packaging_fee=4.0,
        gst_rate=0.05,
        eta_min=30,
        eta_max=45,
    )

    def __init__(self, transport: SwiggyTransport | None = None) -> None:
        self.transport = transport or DirectTransport()
        self._cache: dict[str, _CacheEntry] = {}
        self._last_health: tuple[float, AdapterStatus] | None = None

    # -- account linking: not implemented for the real adapter (v1) --------

    async def link_account(self, phone: str) -> str:
        raise NotImplementedError(
            "Real Swiggy OTP linking is not implemented yet — it needs "
            "Swiggy's headless OTP flow. Search and prices work in guest mode."
        )

    async def verify_otp(self, link_ref: str, otp: str) -> UserSession:
        raise NotImplementedError(
            "Real Swiggy OTP verification is not implemented yet."
        )

    # -- catalog -----------------------------------------------------------

    @staticmethod
    def _gtin(rest_id: str, dish_id: str) -> str:
        # Namespaced: never collides with real GTINs in the static catalog.
        return f"swiggy:{rest_id}:{dish_id}"

    def _to_models(self, raw: dict) -> tuple[Product, PriceSnapshot]:
        rest = raw["restaurant"]
        price = raw["price_inr"]
        product = Product(
            gtin=self._gtin(rest["id"], raw["dish_id"]),
            brand=rest["name"],
            name=f"{raw['dish_name']} @ {rest['name']}",
            pack_size=1.0,
            unit="serving",
            pack_label="1 serving",
            mrp=price,  # Swiggy dishes expose no MRP; mrp == price
        )
        eta = rest["delivery_time_min"]
        snapshot = PriceSnapshot(
            platform_id=self.platform_id,
            gtin=product.gtin,
            price=price,
            mrp=product.mrp,
            per_unit_price=price,
            in_stock=True,
            eta_minutes=int(eta) if eta else self.fee_table.eta_mid,
            freshness="live",
            captured_at=utcnow(),
        )
        return product, snapshot

    async def search(self, query: str, lat: float, lng: float,
                     session: UserSession) -> list[Product]:
        try:
            payload = await self.transport.search_raw(query, lat, lng)
        except Exception as exc:  # noqa: BLE001 — degrade, don't 500 compare
            log.warning("Swiggy search failed for %r: %s", query, exc)
            return []
        products: list[Product] = []
        now = time.monotonic()
        for raw in parse_search_response(payload):
            product, snapshot = self._to_models(raw)
            self._cache[product.gtin] = _CacheEntry(product, snapshot, now)
            products.append(product)
        # best matches first — the API already returns relevance order
        return products

    async def get_price(self, product_id: str,
                        session: UserSession) -> PriceSnapshot:
        entry = self._cache.get(product_id)
        if entry is None or time.monotonic() - entry.stored_at > CACHE_TTL_S:
            raise PriceUnavailableError(
                f"No fresh price for {product_id!r} — call search() first "
                f"(live prices are cached for {CACHE_TTL_S}s).")
        return entry.snapshot

    async def health(self) -> AdapterStatus:
        now = time.monotonic()
        if self._last_health and now - self._last_health[0] < HEALTH_TTL_S:
            return self._last_health[1]
        started = time.monotonic()
        try:
            payload = await asyncio.wait_for(
                self.transport.search_raw("tea", DEFAULT_LAT, DEFAULT_LON),
                timeout=25)
            n = len(parse_search_response(payload))
            status = AdapterStatus(
                platform_id=self.platform_id, status="live",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=f"Swiggy live search OK ({n} dishes in probe)")
        except Exception as exc:  # noqa: BLE001 — health must never raise
            log.warning("Swiggy health probe failed: %s", exc)
            status = AdapterStatus(
                platform_id=self.platform_id, status="down",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=f"Swiggy live probe failed: {exc}")
        self._last_health = (now, status)
        return status
