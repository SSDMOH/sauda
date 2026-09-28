"""Real Flipkart Minutes adapter — live prices via Flipkart's page-fetch API.

Transport strategy:
- Flipkart's Minutes storefront (marketplace=HYPERLOCAL) is a SPA whose
  search results arrive through a single "page fetch" API:
  POST https://1.rome.api.flipkart.com/api/4/page/fetch
  The reference recipe (ojasmagarwal/shoplense, flipkart_scraper.js) rebuilds
  this call with plain fetch — no login, no session cookies, just the
  documented headers + body. So this adapter calls it directly over HTTPS
  with httpx (no browser needed).
- From THIS workspace's egress the rome API hangs (read timeout on
  2026-09-28), so live verification here was not possible; the code follows
  the reference contract and is fully tested with stub/mock-HTTP transports.

Guest mode: search + prices work WITHOUT login. Account linking is not
implemented — link_account/verify_otp raise NotImplementedError (the API
layer converts that to HTTP 501).

Endpoint contract (from shoplense's flipkart_scraper.js):
- POST https://1.rome.api.flipkart.com/api/4/page/fetch?cacheFirst=false
- Headers: accept=application/json, content-type=application/json,
  flipkart_secure=true, origin=https://www.flipkart.com,
  referer=https://www.flipkart.com/, user-agent (Chrome),
  x-user-agent="<UA> FKUA/website/41/website/Desktop".
- Body:
  {"pageUri": "/search?q=<query>&otracker=search&otracker1=search"
              "&as-show=on&as=off&marketplace=HYPERLOCAL",
   "pageContext": {"trackingContext": {"context": {"eVar51": "direct_browse",
                       "eVar61": "direct_browse"}},
                   "fetchSeoData": true, "networkSpeed": 10000},
   "requestContext": {"type": "BROWSE_PAGE", "ssid": <24-char>, "sqid": <uuid>},
   "locationContext": {"pincode": <int>, "changed": false}}
- Response: RESPONSE.slots[] where widget.type == "PRODUCT_SUMMARY" ->
  widget.data.products[] -> productInfo.value:
  {id, titles {title, newTitle, superTitle, subtitle},
   pricing {mrp {value}, finalPrice {value}, prices[]},
   availability {displayState}, productBrand, media.images[{url}], listingId}
- A location gate shows up as RESPONSE.pageMeta.redirectionObject — the
  adapter raises honestly instead of returning silent empty results.
- Pagination: page 2+ reuses RESPONSE.pageData.paginationContextMap with
  paginatedFetch=true.

Location: Minutes availability is keyed off the delivery pincode. lat/lng is
reverse-geocoded to a pincode (Nominatim, short timeout, cached), falling
back to SAUDA_PINCODE / 110001 (Delhi) — documented, never silent.
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
from urllib.parse import quote

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

log = logging.getLogger("sauda.real_flipkart_minutes")

# ---------------------------------------------------------------------------
# Endpoint constants (from shoplense's flipkart_scraper.js)
# ---------------------------------------------------------------------------

PAGE_FETCH_URL = "https://1.rome.api.flipkart.com/api/4/page/fetch?cacheFirst=false"
BASE_URL = "https://www.flipkart.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "flipkart_secure": "true",
    "origin": BASE_URL,
    "referer": f"{BASE_URL}/",
    "user-agent": UA,
    "x-user-agent": f"{UA} FKUA/website/41/website/Desktop",
}

DEFAULT_LAT = 28.6139   # Delhi — same default as the REST API
DEFAULT_LON = 77.2090
DEFAULT_PINCODE = os.environ.get("SAUDA_PINCODE", "110001")

CACHE_TTL_S = 120        # short cache; snapshots carry honest timestamps
PINCODE_CACHE_TTL_S = 3600
HEALTH_TTL_S = 60
CONCURRENCY = 2
MIN_REQUEST_INTERVAL_S = 0.8
MAX_SEARCH_PAGES = 2     # page 1 + 2: coverage without hammering the API


# ---------------------------------------------------------------------------
# Parsing (pure functions — unit-testable)
# ---------------------------------------------------------------------------

_PACK_RE = re.compile(
    r"([\d.]+)\s*(?:x\s*([\d.]+))?\s*(kg|g|ml|ltr|l|pcs|pc|pack|packs)\b",
    re.IGNORECASE,
)


def parse_pack_size(text: str) -> tuple[float, str, str]:
    """Parse Flipkart subtitle like "500 ml", "1 L", "5 kg", "12 pcs".

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


def _num(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _product_nodes(payload: dict):
    """Yield productInfo.value dicts from PRODUCT_SUMMARY widget slots."""
    response = payload.get("RESPONSE") if isinstance(payload, dict) else None
    if not isinstance(response, dict):
        return
    slots = response.get("slots")
    if not isinstance(slots, list):
        return
    for slot in slots:
        widget = (slot or {}).get("widget") or {}
        if widget.get("type") != "PRODUCT_SUMMARY":
            continue
        products = (widget.get("data") or {}).get("products") or []
        for card in products:
            info = ((card or {}).get("productInfo") or {}).get("value")
            if isinstance(info, dict):
                yield info


def parse_page_fetch_payload(payload: dict | None) -> list[dict]:
    """Parse a 1.rome.api.flipkart.com page/fetch body into raw product dicts.

    Defensive: unknown shapes are skipped, never fatal. Field selection
    mirrors shoplense's normalizeProduct().
    """
    if not isinstance(payload, dict):
        return []
    if isinstance((payload.get("RESPONSE") or {}).get("pageMeta"), dict) and \
            (payload["RESPONSE"]["pageMeta"] or {}).get("redirectionObject"):
        raise RuntimeError(
            "Flipkart redirected to a location gate — the pincode was not "
            "accepted; not returning silent empty results.")
    out: list[dict] = []
    seen: set[str] = set()
    for value in _product_nodes(payload):
        pid = str(value.get("id") or "")
        titles = value.get("titles") or {}
        pricing = value.get("pricing") or {}
        name = titles.get("title") or titles.get("newTitle") or ""
        price = _num((pricing.get("finalPrice") or {}).get("value"))
        if price is None:
            for pr in pricing.get("prices") or []:
                if isinstance(pr, dict) and not pr.get("strikeOff"):
                    price = _num(pr.get("value"))
                    break
        if not pid or not name or price is None:
            continue
        if pid in seen:
            continue
        seen.add(pid)
        mrp = _num((pricing.get("mrp") or {}).get("value"))
        avail = value.get("availability") or {}
        display_state = str(avail.get("displayState") or "").upper()
        intent = str(avail.get("intent") or "").upper()
        # OUT_OF_STOCK is an explicit out; anything else on a hyperlocal
        # search card is treated as buyable (a missing field must not drop
        # sellable items).
        in_stock = (display_state != "OUT_OF_STOCK"
                    and intent != "OUT_OF_STOCK")
        images = [img.get("url") for img in (value.get("media") or {}).get("images", [])
                  if isinstance(img, dict) and img.get("url")]
        out.append({
            "pid": pid,
            "name": name,
            "brand": value.get("productBrand") or titles.get("superTitle") or "",
            "pack_text": titles.get("subtitle") or "",
            "price": price,
            "mrp": mrp if mrp else price,
            "in_stock": in_stock,
            "image_url": images[0] if images else None,
            "listing_id": str(value.get("listingId") or ""),
        })
    return out


def parse_pagination_context(payload: dict | None) -> dict | None:
    """paginationContextMap for the next page, or None when there isn't one."""
    if not isinstance(payload, dict):
        return None
    page_data = (payload.get("RESPONSE") or {}).get("pageData") or {}
    ctx = page_data.get("paginationContextMap")
    return ctx if isinstance(ctx, dict) else None


def build_body(query: str, pincode: str, page: int,
               pagination_context_map: dict | None, ssid: str) -> dict:
    """Rebuild the SPA's page/fetch request body (shoplense recipe)."""
    params = (f"q={quote(query)}&otracker=search&otracker1=search"
              f"&as-show=on&as=off&marketplace=HYPERLOCAL")
    if page > 1:
        params += f"&page={page}"
    page_context: dict = {
        "trackingContext": {"context": {"eVar51": "direct_browse",
                                        "eVar61": "direct_browse"}},
        "fetchSeoData": page == 1,
        "networkSpeed": 10000,
    }
    if page > 1 and pagination_context_map:
        page_context["fetchSeoData"] = True
        page_context["paginatedFetch"] = True
        page_context["pageNumber"] = page
        page_context["paginationContextMap"] = pagination_context_map
    return {
        "pageUri": f"/search?{params}",
        "pageContext": page_context,
        "requestContext": {
            "type": "BROWSE_PAGE",
            "ssid": ssid,
            "sqid": str(uuid.uuid4()),
        },
        "locationContext": {"pincode": int(pincode), "changed": False},
    }


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


class FlipkartMinutesTransport(ABC):
    """Pluggable transport: given a query + coords, return raw product dicts."""

    @abstractmethod
    async def search_raw(self, query: str, lat: float, lon: float) -> list[dict]:
        ...


class StubTransport(FlipkartMinutesTransport):
    """In-memory transport for tests and offline demos."""

    def __init__(self, raws: list[dict] | None = None) -> None:
        self.raws = raws if raws is not None else []
        self.calls: list[tuple[str, float, float]] = []

    async def search_raw(self, query: str, lat: float, lon: float) -> list[dict]:
        self.calls.append((query, lat, lon))
        return self.raws


class MockHttpTransport(FlipkartMinutesTransport):
    """Transport driven by canned page/fetch payloads (tests, no network).

    Exercises the real request construction (body, headers) and the
    pagination walk against fixture payloads.
    """

    def __init__(self, pages: list[dict]) -> None:
        self._pages = pages
        self.request_bodies: list[dict] = []
        self.pincodes_used: list[str] = []

    async def search_raw(self, query: str, lat: float, lon: float) -> list[dict]:
        pincode = "110001"  # deterministic in tests; real path geocodes
        ssid = "testsess" + "0" * 16
        out: list[dict] = []
        pagination_ctx = None
        for page_no in range(1, MAX_SEARCH_PAGES + 1):
            body = build_body(query, pincode, page_no, pagination_ctx, ssid)
            self.request_bodies.append(body)
            self.pincodes_used.append(pincode)
            if page_no - 1 >= len(self._pages):
                break
            payload = self._pages[page_no - 1]
            items = parse_page_fetch_payload(payload)
            if not items:
                break
            out.extend(items)
            pagination_ctx = parse_pagination_context(payload)
            if pagination_ctx is None:
                break
        return out


class DirectTransport(FlipkartMinutesTransport):
    """Real transport: plain HTTPS against 1.rome.api.flipkart.com (no browser)."""

    def __init__(self, timeout_s: int = 20) -> None:
        self.timeout_s = timeout_s
        self._sem = asyncio.Semaphore(CONCURRENCY)
        self._last_request_at = 0.0
        self._pincode_cache: dict[str, tuple[float, str]] = {}
        self._client = None

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

    async def _latlng_to_pincode(self, lat: float, lon: float) -> str:
        """Reverse-geocode coordinates to a pincode.

        Falls back to SAUDA_PINCODE / Delhi when geocoding fails — documented,
        never silent.
        """
        key = f"{round(lat, 4)},{round(lon, 4)}"
        hit = self._pincode_cache.get(key)
        if hit and time.monotonic() - hit[0] < PINCODE_CACHE_TTL_S:
            return hit[1]
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(
                    "https://nominatim.openstreetmap.org/reverse",
                    params={"lat": lat, "lon": lon, "format": "jsonv2"},
                    headers={"User-Agent": "sauda-price-compare/1.0"})
                if r.status_code == 200:
                    pc = (r.json().get("address") or {}).get("postcode")
                    if pc and re.fullmatch(r"\d{6}", str(pc)):
                        self._pincode_cache[key] = (time.monotonic(), str(pc))
                        return str(pc)
        except Exception as exc:  # noqa: BLE001 — fallback below is the point
            log.debug("Nominatim reverse-geocode failed: %s", exc)
        log.info("using fallback pincode %s for (%s, %s)",
                 DEFAULT_PINCODE, lat, lon)
        return DEFAULT_PINCODE

    async def search_raw(self, query: str, lat: float, lon: float) -> list[dict]:
        pincode = await self._latlng_to_pincode(lat, lon)
        client = await self._client_get()
        ssid = uuid.uuid4().hex[:24]
        out: list[dict] = []
        pagination_ctx = None
        for page_no in range(1, MAX_SEARCH_PAGES + 1):
            body = build_body(query, pincode, page_no, pagination_ctx, ssid)
            async with self._sem:
                await self._throttle()
                for attempt in range(4):
                    r = await client.post(PAGE_FETCH_URL, json=body,
                                          headers=HEADERS)
                    self._last_request_at = time.monotonic()
                    if r.status_code == 429 and attempt < 3:
                        await asyncio.sleep(1.5 * (attempt + 1))
                        continue
                    break
            if r.status_code != 200:
                raise RuntimeError(
                    f"Flipkart page/fetch returned HTTP {r.status_code}")
            try:
                payload = r.json()
            except ValueError as exc:
                raise RuntimeError("Flipkart page/fetch returned non-JSON "
                                   "(likely a block page)") from exc
            items = parse_page_fetch_payload(payload)
            if not items:
                break
            out.extend(items)
            pagination_ctx = parse_pagination_context(payload)
            if pagination_ctx is None:
                break
        return out


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    product: Product
    snapshot: PriceSnapshot
    stored_at: float


class RealFlipkartMinutesAdapter(PlatformAdapter):
    """Live Flipkart Minutes prices. platform_id stays "flipkart_minutes"."""

    platform_id = "flipkart_minutes"
    display_name = "Flipkart Minutes (live)"
    vertical = "grocery"
    uses_static_catalog = False   # prices come from live search, not catalog.py
    # Fee table: estimates (no mock exists for Minutes); documented as such.
    fee_table = FeeTable(
        platform_id="flipkart_minutes",
        min_order_value=99.0,
        free_delivery_above=199.0,
        delivery_fee_base=25.0,
        platform_fee=5.0,
        packaging_fee=4.0,
        gst_rate=0.05,
        surge_active=False,
        eta_min=10,
        eta_max=20,
    )

    def __init__(self, transport: FlipkartMinutesTransport | None = None) -> None:
        self.transport = transport or DirectTransport()
        self._cache: dict[str, _CacheEntry] = {}
        self._last_health: tuple[float, AdapterStatus] | None = None

    # -- account linking: not implemented for the real adapter (v1) --------

    async def link_account(self, phone: str) -> str:
        raise NotImplementedError(
            "Real Flipkart Minutes OTP linking is not implemented yet — "
            "search and prices work in guest mode without login."
        )

    async def verify_otp(self, link_ref: str, otp: str) -> UserSession:
        raise NotImplementedError(
            "Real Flipkart Minutes OTP verification is not implemented yet."
        )

    # -- catalog -----------------------------------------------------------

    @staticmethod
    def _gtin(pid: str) -> str:
        # Namespaced: never collides with real GTINs in the static catalog.
        return f"flipkart_minutes:{pid}"

    def _to_models(self, raw: dict) -> tuple[Product, PriceSnapshot] | None:
        if not raw.get("in_stock"):
            return None
        brand = raw["brand"] or split_brand(raw["name"])[0]
        _, rest = split_brand(raw["name"])
        name = rest or raw["name"]
        pack_size, unit, pack_label = parse_pack_size(raw["pack_text"])
        mrp = raw["mrp"] if raw["mrp"] else raw["price"]
        product = Product(
            gtin=self._gtin(raw["pid"]),
            brand=brand, name=name,
            pack_size=pack_size, unit=unit, pack_label=pack_label,
            mrp=round(mrp, 2),
        )
        snapshot = PriceSnapshot(
            platform_id=self.platform_id,
            gtin=product.gtin,
            price=round(raw["price"], 2),
            mrp=product.mrp,
            per_unit_price=round(raw["price"] / pack_size, 4),
            in_stock=True,
            eta_minutes=self.fee_table.eta_mid,
            freshness="live",
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
                timeout=40)
            n = len(raws)
            status = AdapterStatus(
                platform_id=self.platform_id, status="live",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=f"Flipkart Minutes live search OK ({n} products in probe)")
        except Exception as exc:  # noqa: BLE001 — health must never raise
            log.warning("Flipkart Minutes health probe failed: %s", exc)
            status = AdapterStatus(
                platform_id=self.platform_id, status="down",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=f"Flipkart Minutes live probe failed: {exc}")
        self._last_health = (now, status)
        return status
