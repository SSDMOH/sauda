"""Real Zepto adapter — live prices via Zepto's app BFF API gateway.

Transport strategy:
- Zepto's WEBSITE edge (www.zeptonow.com, CloudFront) hard-403s datacenter IPs,
  BUT the app's BFF API gateway (bff-gateway.zeptonow.com, Kong) accepts guest
  (no-login) requests with `x-without-bearer: true` — no cookies, no tokens,
  no browser needed. This recipe is verified live 2026-06 by the ecom-intel
  reference (daman8271/ecom-intel, platforms/zepto/SKILL.md + scrape.js), which
  scrapes from a datacenter host with plain curl, no proxy.
- So this adapter calls the gateway directly over HTTPS with the documented
  guest headers. From THIS workspace's egress the gateway hangs (read timeout
  on 2026-09-28), so live verification here was not possible; the code follows
  the verified contract and is fully tested with stub/mock-HTTP transports.

Guest mode: store resolution + search + prices work WITHOUT login. Account
linking is not implemented — link_account/verify_otp raise NotImplementedError
(the API layer converts that to HTTP 501).

Verified endpoint contract (from ecom-intel scrape.js):
- Store resolution:
  GET https://bff-gateway.zeptonow.com/serviceability-service/api/v1/serviceability?lat=<lat>&long=<lon>
  -> {"data": {"serviceable": bool, "stores": [{"storeId", "serviceable", "storeConstruct"}]}}
  pick the serviceable PRIMARY store.
- Search:
  POST https://bff-gateway.zeptonow.com/user-search-service/api/v3/search
  body: {"query", "pageNumber", "intentId": <uuid>, "mode": "AUTOSUGGEST", "userSessionId": <uuid>}
  Walk the response for `productResponse` nodes with product.name.
- Guest headers: tenant=ZEPTO, x-without-bearer=true, platform=WEB,
  app_sub_platform=WEB, app_version=12.64.1, marketplace_type=SUPER_SAVER,
  origin/referer https://www.zeptonow.com, x-latitude/x-longitude, plus
  store_id/store_ids/storeid and store_etas once the store is known.
- Prices are in PAISE (divide by 100). The SUPER_SAVER tier is the price the
  Zepto app shows by default (verified by the reference); override with the
  SAUDA_ZEPTO_MARKETPLACE env var (e.g. ZEPTO_NOW).
- Freshness: Zepto sets `cached: true` on products served from its search-index
  cache (a known ~1-day stale-price risk). Snapshots are labelled "live" only
  when `cached` is false, otherwise "est" — never a false "live".
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass

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

log = logging.getLogger("sauda.real_zepto")

# ---------------------------------------------------------------------------
# Endpoint constants (from the verified ecom-intel recipe)
# ---------------------------------------------------------------------------

GATEWAY = "https://bff-gateway.zeptonow.com"
SERVICEABILITY_URL = f"{GATEWAY}/serviceability-service/api/v1/serviceability"
SEARCH_URL = f"{GATEWAY}/user-search-service/api/v3/search"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")
COMPAT = ("CONVENIENCE_FEE,RAIN_FEE,EXTERNAL_COUPONS,STANDSTILL,BUNDLE,"
          "MULTI_SELLER_ENABLED,PIP_V1,ROLLUPS,SCHEDULED_DELIVERY,"
          "SAMPLING_ENABLED,HOMEPAGE_V2,NEW_ETA_BANNER,SUPER_SAVER:1,"
          "PROMO_CASH:0,24X7_ENABLED_V1,HP_V4_FEED,NEW_ROLLUPS_ENABLED,"
          "PLP_ON_SEARCH,DYNAMIC_FILTERS,NEW_FEE_STRUCTURE,NEW_BILL_INFO,"
          "SUPERSTORE_V1,MARKETPLACE_REPLACEMENT")

DEFAULT_LAT = 28.6139   # Delhi — same default as the REST API
DEFAULT_LON = 77.2090

CACHE_TTL_S = 120        # short cache; snapshots carry honest timestamps
STORE_CACHE_TTL_S = 1800 # store resolution is stable per location
HEALTH_TTL_S = 60
CONCURRENCY = 3
MIN_REQUEST_INTERVAL_S = 0.4
MAX_SEARCH_PAGES = 2     # page 0 + 1: coverage without hammering the gateway

MARKETPLACE = os.environ.get("SAUDA_ZEPTO_MARKETPLACE", "SUPER_SAVER")


# ---------------------------------------------------------------------------
# Parsing (pure functions — unit-testable)
# ---------------------------------------------------------------------------

_PACK_RE = re.compile(
    r"([\d.]+)\s*(?:x\s*([\d.]+))?\s*(kg|g|ml|ltr|l|pcs|pc|pack|packs)\b",
    re.IGNORECASE,
)


def parse_pack_size(text: str) -> tuple[float, str, str]:
    """Parse Zepto formattedPacksize like "500 ml", "1 L", "5 kg", "12 pcs".

    Returns (pack_size, unit, pack_label) with unit in {"g","ml","pcs"}.
    Falls back to a single piece when the text is unparseable — documented,
    never a silent wrong number.
    """
    m = _PACK_RE.search(text or "")
    if not m:
        return 1.0, "pcs", (text or "1 pc").strip()
    qty = float(m.group(1))
    if m.group(2):
        qty *= float(m.group(2))
    unit = m.group(3).lower()
    if unit == "kg":
        return qty * 1000.0, "g", text.strip()
    if unit in ("l", "ltr"):
        return qty * 1000.0, "ml", text.strip()
    if unit == "g":
        return qty, "g", text.strip()
    if unit == "ml":
        return qty, "ml", text.strip()
    return qty, "pcs", text.strip()  # pcs | pc | pack | packs


def _paise(value) -> float | None:
    """Paise (int/float/str) -> rupees. None when unparseable."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value) / 100.0
    except (TypeError, ValueError):
        return None


def tier_price(pr: dict, marketplace: str = MARKETPLACE) -> tuple[float | None, str]:
    """Authoritative price for the requested marketplace tier (rupees).

    Prefers the structured pricingData.pricingEntityPrices tier entry — the
    exact price the app renders — then falls back down the chain the reference
    scraper uses. Returns (price, source-label) for traceability.
    """
    pe = (pr.get("pricingData") or {}).get("pricingEntityPrices") or []
    for entry in pe:
        if (isinstance(entry, dict)
                and entry.get("pricingEntity") == marketplace
                and entry.get("discountedSellingPrice") is not None):
            p = _paise(entry.get("discountedSellingPrice"))
            if p is not None:
                return p, f"pricingData:{marketplace}"
    if marketplace == "SUPER_SAVER" and pr.get("superSaverSellingPrice") is not None:
        p = _paise(pr.get("superSaverSellingPrice"))
        if p is not None:
            return p, "superSaverSellingPrice"
    for key in ("discountedSellingPrice", "sellingPrice", "superSaverSellingPrice"):
        if pr.get(key) is not None:
            p = _paise(pr.get(key))
            if p is not None:
                return p, key
    return None, "none"


def _iter_product_responses(payload: dict):
    """Yield `productResponse` nodes anywhere in the search payload."""
    stack = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            pr = node.get("productResponse")
            if isinstance(pr, dict) and isinstance((pr.get("product") or {}).get("name"), str):
                yield pr
            stack.extend(reversed(node.values()))
        elif isinstance(node, list):
            stack.extend(reversed(node))


def parse_search_response(payload: dict | None) -> list[dict]:
    """Parse a user-search-service /v3/search JSON body into raw product dicts.

    Defensive: unknown shapes are skipped, never fatal. Mirrors ecom-intel's
    toRow() field selection.
    """
    if not isinstance(payload, dict):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for pr in _iter_product_responses(payload):
        product = pr.get("product") or {}
        variant = pr.get("productVariant") or {}
        name = product.get("name") or ""
        price, price_source = tier_price(pr)
        if not name or price is None:
            continue
        variant_id = str(variant.get("id") or product.get("id") or "")
        if not variant_id or variant_id in seen:
            continue
        seen.add(variant_id)
        mrp = _paise(pr.get("mrp"))
        if mrp is None:
            mrp = _paise(variant.get("mrp"))
        out.append({
            "variant_id": variant_id,
            "product_id": str(product.get("id") or ""),
            "name": name,
            "brand": product.get("brand") or "",
            "pack_text": variant.get("formattedPacksize") or "",
            "price": price,
            "price_source": price_source,
            "mrp": mrp if mrp else price,
            "out_of_stock": pr.get("outOfStock") is True,
            "cached": pr.get("cached") is True,   # search-index cache -> "est"
            "image_url": variant.get("imageUrl") or product.get("imageUrl"),
        })
    return out


def parse_serviceability(payload: dict | None) -> str | None:
    """Pick the serviceable PRIMARY store id from a serviceability response."""
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not data.get("serviceable"):
        return None
    stores = data.get("stores")
    if not isinstance(stores, list):
        return None
    pick = (next((s for s in stores
                  if isinstance(s, dict) and s.get("serviceable")
                  and "PRIMARY" in str(s.get("storeConstruct") or "").upper()), None)
            or next((s for s in stores
                     if isinstance(s, dict) and s.get("serviceable")), None)
            or next((s for s in stores if isinstance(s, dict)), None))
    return str(pick.get("storeId")) if pick and pick.get("storeId") else None


def split_brand(name: str) -> tuple[str, str]:
    """Heuristic: leading token is the brand ("Amul Taaza ...") -> ("Amul", "Taaza ...")."""
    parts = name.split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return name, ""


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

def _env_proxies() -> str | None:
    """Proxy URL from the environment, bypassing the `no_proxy` bug.

    httpx chokes on bracketed IPv6 entries in no_proxy ("Invalid port: ':1]'");
    since every request here is external, ignoring no_proxy is safe and gives
    identical behaviour everywhere else. trust_env=False keeps it deterministic.
    """
    return (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
            or os.environ.get("ALL_PROXY") or os.environ.get("all_proxy")
            or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"))


class ZeptoTransport(ABC):
    """Pluggable transport: given a query + coords, return raw product dicts."""

    @abstractmethod
    async def search_raw(self, query: str, lat: float, lon: float) -> list[dict]:
        ...


class StubTransport(ZeptoTransport):
    """In-memory transport for tests and offline demos."""

    def __init__(self, raws: list[dict] | None = None) -> None:
        self.raws = raws if raws is not None else []
        self.calls: list[tuple[str, float, float]] = []

    async def search_raw(self, query: str, lat: float, lon: float) -> list[dict]:
        self.calls.append((query, lat, lon))
        return self.raws


class MockHttpTransport(ZeptoTransport):
    """Transport driven by canned HTTP bodies (serviceability + search pages).

    Used in tests to exercise the real request/response flow (store
    resolution, pagination, headers) without any network.
    """

    def __init__(self, serviceability: dict, search_pages: list[dict]) -> None:
        self._serviceability = serviceability
        self._search_pages = search_pages
        self.request_headers: list[dict] = []
        self.request_bodies: list[dict] = []

    async def search_raw(self, query: str, lat: float, lon: float) -> list[dict]:
        store_id = parse_serviceability(self._serviceability)
        if not store_id:
            raise RuntimeError("Zepto: no serviceable store at this location")
        out: list[dict] = []
        for page_no, page in enumerate(self._search_pages[:MAX_SEARCH_PAGES]):
            headers = DirectTransport.headers(store_id, lat, lon)
            self.request_headers.append(headers)
            self.request_bodies.append(
                DirectTransport.search_body(query, page_no))
            items = parse_search_response(page)
            if not items:
                break
            out.extend(items)
        return out


class DirectTransport(ZeptoTransport):
    """Real transport: plain HTTPS against the BFF gateway (no browser needed).

    The gateway is Kong, not Cloudflare, and accepts guest requests directly —
    verified by the ecom-intel reference from a datacenter host.
    """

    def __init__(self, timeout_s: int = 20) -> None:
        self.timeout_s = timeout_s
        self._sem = asyncio.Semaphore(CONCURRENCY)
        self._last_request_at = 0.0
        self._store_cache: dict[str, tuple[float, str]] = {}
        self._client = None

    @staticmethod
    def headers(store_id: str | None, lat: float, lon: float) -> dict:
        sid, did, rid = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        h = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9",
            "app_sub_platform": "WEB",
            "app_version": "12.64.1",
            "appversion": "12.64.1",
            "auth_revamp_flow": "v2",
            "compatible_components": COMPAT,
            "content-type": "application/json",
            "device_id": did, "deviceid": did,
            "marketplace_type": MARKETPLACE,
            "origin": "https://www.zeptonow.com",
            "platform": "WEB",
            "referer": "https://www.zeptonow.com/",
            "request_id": rid, "requestid": rid,
            "session_id": sid, "sessionid": sid,
            "tenant": "ZEPTO",
            "x-without-bearer": "true",
            "user-agent": UA,
            "x-latitude": str(lat), "x-longitude": str(lon),
            "latitude": str(lat), "longitude": str(lon),
        }
        if store_id:
            h["store_id"] = store_id
            h["store_ids"] = store_id
            h["storeid"] = store_id
            h["store_etas"] = '{"%s":10}' % store_id
        return h

    @staticmethod
    def search_body(query: str, page_number: int) -> dict:
        return {"query": query, "pageNumber": page_number,
                "intentId": str(uuid.uuid4()), "mode": "AUTOSUGGEST",
                "userSessionId": str(uuid.uuid4())}

    async def _client_get(self):
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(
                timeout=self.timeout_s, trust_env=False,
                proxy=_env_proxies())
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _throttle(self) -> None:
        wait = MIN_REQUEST_INTERVAL_S - (time.monotonic() - self._last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)

    async def _resolve_store(self, lat: float, lon: float) -> str:
        key = f"{round(lat, 3)},{round(lon, 3)}"
        hit = self._store_cache.get(key)
        if hit and time.monotonic() - hit[0] < STORE_CACHE_TTL_S:
            return hit[1]
        client = await self._client_get()
        async with self._sem:
            await self._throttle()
            r = await client.get(
                SERVICEABILITY_URL,
                params={"lat": lat, "long": lon},
                headers=self.headers(None, lat, lon))
            self._last_request_at = time.monotonic()
        if r.status_code == 429:
            raise RuntimeError("Zepto gateway rate-limited (429) on store resolution")
        if r.status_code != 200:
            raise RuntimeError(
                f"Zepto serviceability returned HTTP {r.status_code}")
        try:
            store_id = parse_serviceability(r.json())
        except ValueError as exc:
            raise RuntimeError("Zepto serviceability returned non-JSON "
                               "(likely a block page)") from exc
        if not store_id:
            raise RuntimeError("Zepto: no serviceable store at this location")
        self._store_cache[key] = (time.monotonic(), store_id)
        return store_id

    async def search_raw(self, query: str, lat: float, lon: float) -> list[dict]:
        store_id = await self._resolve_store(lat, lon)
        client = await self._client_get()
        out: list[dict] = []
        for page_no in range(MAX_SEARCH_PAGES):
            async with self._sem:
                await self._throttle()
                for attempt in range(4):
                    r = await client.post(
                        SEARCH_URL,
                        json=self.search_body(query, page_no),
                        headers=self.headers(store_id, lat, lon))
                    self._last_request_at = time.monotonic()
                    if r.status_code == 429 and attempt < 3:
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    break
            if r.status_code != 200:
                raise RuntimeError(
                    f"Zepto search returned HTTP {r.status_code}")
            try:
                page = r.json()
            except ValueError as exc:
                raise RuntimeError("Zepto search returned non-JSON "
                                   "(likely a block page)") from exc
            items = parse_search_response(page)
            if not items:
                break
            out.extend(items)
        return out


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    product: Product
    snapshot: PriceSnapshot
    stored_at: float


class RealZeptoAdapter(PlatformAdapter):
    """Live Zepto prices. platform_id stays "zepto" so callers are unchanged."""

    platform_id = "zepto"
    display_name = "Zepto (live)"
    vertical = "grocery"
    uses_static_catalog = False   # prices come from live search, not catalog.py
    fee_table = FeeTable(
        platform_id="zepto",
        min_order_value=99.0,
        free_delivery_above=149.0,   # lower free-delivery bar than rivals
        delivery_fee_base=29.0,
        platform_fee=4.0,
        packaging_fee=3.0,
        gst_rate=0.05,
        surge_active=True,           # surge pricing currently on
        surge_delivery_multiplier=1.25,
        eta_min=10,
        eta_max=12,
    )

    def __init__(self, transport: ZeptoTransport | None = None) -> None:
        self.transport = transport or DirectTransport()
        self._cache: dict[str, _CacheEntry] = {}
        self._last_health: tuple[float, AdapterStatus] | None = None

    # -- account linking: not implemented for the real adapter (v1) --------

    async def link_account(self, phone: str) -> str:
        raise NotImplementedError(
            "Real Zepto OTP linking is not implemented yet — Zepto's guest "
            "API covers search and prices without login. Search and prices "
            "work in guest mode."
        )

    async def verify_otp(self, link_ref: str, otp: str) -> UserSession:
        raise NotImplementedError(
            "Real Zepto OTP verification is not implemented yet."
        )

    # -- catalog -----------------------------------------------------------

    @staticmethod
    def _gtin(variant_id: str) -> str:
        # Namespaced: never collides with real GTINs in the static catalog.
        return f"zepto:{variant_id}"

    def _to_models(self, raw: dict) -> tuple[Product, PriceSnapshot] | None:
        if raw.get("out_of_stock"):
            return None
        brand = raw["brand"] or split_brand(raw["name"])[0]
        _, rest = split_brand(raw["name"])
        name = rest or raw["name"]
        pack_size, unit, pack_label = parse_pack_size(raw["pack_text"])
        mrp = raw["mrp"] if raw["mrp"] else raw["price"]
        product = Product(
            gtin=self._gtin(raw["variant_id"]),
            brand=brand, name=name,
            pack_size=pack_size, unit=unit, pack_label=pack_label,
            mrp=round(mrp, 2),
        )
        # Honest freshness: `cached: true` means the price came from Zepto's
        # search-index cache (~1 day stale risk) — never call that "live".
        freshness = "live" if not raw.get("cached") else "est"
        snapshot = PriceSnapshot(
            platform_id=self.platform_id,
            gtin=product.gtin,
            price=round(raw["price"], 2),
            mrp=product.mrp,
            per_unit_price=round(raw["price"] / pack_size, 4),
            in_stock=True,
            eta_minutes=self.fee_table.eta_mid,
            freshness=freshness,
            captured_at=utcnow(),
        )
        return product, snapshot

    async def search(self, query: str, lat: float, lng: float,
                     session: UserSession) -> list[Product]:
        raws = await self.transport.search_raw(query, lat, lng)
        products: list[Product] = []
        now = time.monotonic()
        for raw in raws:
            models = self._to_models(raw)
            if models is None:
                continue
            product, snapshot = models
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
            raws = await asyncio.wait_for(
                self.transport.search_raw("salt", DEFAULT_LAT, DEFAULT_LON),
                timeout=30)
            n = len(raws)
            live = sum(1 for r in raws if not r.get("cached"))
            status = AdapterStatus(
                platform_id=self.platform_id, status="live",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=(f"Zepto live search OK ({n} products in probe, "
                         f"{live} freshly fetched)"))
        except Exception as exc:  # noqa: BLE001 — health must never raise
            log.warning("Zepto health probe failed: %s", exc)
            status = AdapterStatus(
                platform_id=self.platform_id, status="down",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=f"Zepto live probe failed: {exc}")
        self._last_health = (now, status)
        return status
