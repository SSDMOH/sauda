"""Real Swiggy Instamart adapter — live prices via Swiggy's own Instamart APIs.

Transport strategy:
- Swiggy's web API sits behind an AWS WAF JavaScript challenge: plain HTTP
  gets a 202 + challenge page instead of JSON. The verified workaround
  (instamart-alerts, pawan67/instamart-alerts, verified 2026-08-23) is:
  headless Chromium loads swiggy.com/instamart once, the WAF script runs and
  hands over an `aws-waf-token` cookie; subsequent API calls ride that token.
- So this adapter drives a real Chromium via Playwright and issues the
  platform's own API calls FROM PAGE CONTEXT (`fetch` with
  `credentials: 'include'`), exactly like the site itself — same cookies, same
  exit, no cookie plumbing. Human-plausible pacing (bounded concurrency,
  backoff, min request interval).

Guest mode: location pinning + search + prices work WITHOUT login. Account
linking is not implemented — link_account/verify_otp raise NotImplementedError
(the API layer converts that to HTTP 501).

Verified endpoint contract (from instamart-alerts' instamart.py/session.py):
- API base: https://www.swiggy.com/api/instamart
- Headers: accept */*, x-build-version (2.367.0 at verification), x-device-id
  (uuid per session), Origin/Referer https://www.swiggy.com.
- Location pinning (prices are per dark store):
  1. GET /maps/suggestions?input=<area/pincode> -> data[].{place_id, description}
  2. GET /maps/address-widgets/v2?place_id= -> data.address.location.{latitude,longitude}
     + metadata.formattedAddress
  3. POST /home/select-location/v2
     body {"data": {"lat","lng","address","addressId":"","annotation","clientId":"INSTAMART-APP"}}
     -> the store id is only embedded in `swiggy://...?storeId=<n>` deeplinks
        in the returned home feed; take the most common one.
- Search:
  POST /search/v2?storeId=&primaryStoreId=&secondaryStoreId=&offset=
       &ageConsent=false&voiceSearchTrackingId=
  body {"facets":[],"sortAttribute":"","query":...,"search_results_offset":...,
        "page_type":"INSTAMART_SEARCH_PAGE","is_pre_search_tag":false}
  -> data.cards[].card.card.gridElements.infoWithStyle.items[].variations[]
  Pagination: data.pageOffset.nextOffset + data.searchResultsOffset travel
  together (page number alone re-serves page one).
- Item shape: variation.price.{mrp,offerPrice} are Google-style Money
  ({units: rupees, nanos}); variation.inventory.inStock is the per-variant
  flag (item-level inStock means *any* variant is in stock — misleading);
  cartAllowedQuantity.allowedQuantity == 0 means unbuyable.
- Blocked signals: HTTP 202 (challenge) or 403, or a 200 with a non-JSON body
  (the challenge interstitial wears a 200 too).

From THIS workspace's egress, Swiggy answers the maps API with HTTP 202
(empty) — the WAF challenge — as the reference predicts, so live verification
here was not possible. The code follows the verified contract and is fully
tested with a fake-transport harness (fixture-based) plus parser unit tests.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import urllib.parse
import uuid
from abc import ABC, abstractmethod
from collections import Counter
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

log = logging.getLogger("sauda.real_instamart")

# ---------------------------------------------------------------------------
# Endpoint constants (verified 2026-08-23 by instamart-alerts)
# ---------------------------------------------------------------------------

API_BASE = "https://www.swiggy.com/api/instamart"
INSTAMART_URL = "https://www.swiggy.com/instamart"
BUILD_VERSION = os.environ.get("SAUDA_SWIGGY_BUILD_VERSION", "2.367.0")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")
DEVICE_ID = str(uuid.uuid4())   # per-process guest device identity

DEFAULT_LAT = 28.6139   # Delhi — same default as the REST API
DEFAULT_LON = 77.2090
DEFAULT_PINCODE = os.environ.get("SAUDA_PINCODE", "110001")

STORE_ID_RE = re.compile(r"storeId=(\d+)")
CACHE_TTL_S = 120        # short cache; snapshots carry honest timestamps
STORE_CACHE_TTL_S = 1800 # store resolution is stable per location
HEALTH_TTL_S = 60
CONCURRENCY = 2          # stay well under the WAF's patience
MIN_REQUEST_INTERVAL_S = 1.0
MAX_SEARCH_PAGES = 3     # ~32 products/page; walk a few pages, not the aisle
WAF_BOOTSTRAP_TIMEOUT_S = 35


# ---------------------------------------------------------------------------
# Parsing (pure functions — unit-testable)
# ---------------------------------------------------------------------------

_PACK_RE = re.compile(
    r"([\d.]+)\s*(?:x\s*([\d.]+))?\s*(kg|g|ml|ltr|l|pcs|pc|pack|packs)\b",
    re.IGNORECASE,
)


def parse_pack_size(text: str) -> tuple[float, str, str]:
    """Parse Instamart quantityDescription like "500 ml", "1 L", "12 pcs".

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


def money_to_rupees(m: dict | None) -> float | None:
    """Google-style Money {units, nanos} -> rupees. None when unparseable."""
    if not isinstance(m, dict):
        return None
    try:
        units = m.get("units")
        nanos = m.get("nanos") or 0
        return int(units) + float(nanos) / 1e9
    except (TypeError, ValueError):
        return None


def variation_in_stock(variation: dict) -> bool:
    """Is this exact variant buyable right now?

    Only variation.inventory.inStock tracks the variant; item-level inStock is
    true when *any* variant is available (misleading for OOS packs).
    cartAllowedQuantity.allowedQuantity == 0 is the backstop.
    Unknown stock is treated as out of stock — a missed deal is cheaper than a
    phantom one.
    """
    inv = variation.get("inventory")
    if not isinstance(inv, dict) or "inStock" not in inv:
        return False
    if not inv["inStock"]:
        return False
    allowed = (variation.get("cartAllowedQuantity") or {}).get("allowedQuantity")
    return allowed != 0


def extract_store_id(home_feed_text: str) -> str | None:
    """The store id only appears in swiggy://...?storeId=<n> deeplinks.

    Take the most common one, like the reference implementation.
    """
    ids = STORE_ID_RE.findall(home_feed_text or "")
    if not ids:
        return None
    return Counter(ids).most_common(1)[0][0]


def parse_geocode_suggestions(payload: dict | None) -> str | None:
    """First place_id from a maps/suggestions response."""
    if not isinstance(payload, dict):
        return None
    preds = payload.get("data")
    if not isinstance(preds, list) or not preds:
        return None
    pid = preds[0].get("place_id") if isinstance(preds[0], dict) else None
    return str(pid) if pid else None


def parse_address_widgets(payload: dict | None) -> dict | None:
    """lat/lng + formatted address from a maps/address-widgets/v2 response."""
    if not isinstance(payload, dict):
        return None
    addr = ((payload.get("data") or {}).get("address")) or {}
    loc = addr.get("location") or {}
    lat, lng = loc.get("latitude"), loc.get("longitude")
    if lat is None or lng is None:
        return None
    return {
        "lat": float(lat),
        "lng": float(lng),
        "address": ((addr.get("metadata") or {}).get("formattedAddress")
                    or addr.get("subtitle") or ""),
    }


def _iter_variations(payload: dict):
    """Yield (item, variation) pairs from a search/v2 payload."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return
    for card in data.get("cards") or []:
        inner = ((card or {}).get("card") or {}).get("card") or {}
        grid = (inner.get("gridElements") or {}).get("infoWithStyle") or {}
        for item in grid.get("items") or []:
            if not isinstance(item, dict):
                continue
            for variation in item.get("variations") or []:
                if isinstance(variation, dict):
                    yield item, variation


def parse_search_payload(payload: dict | None) -> list[dict]:
    """Parse one search/v2 page into raw product dicts.

    Defensive: unknown shapes are skipped, never fatal. Mirrors the reference
    field selection (first variation = the one the storefront shows).
    """
    if not isinstance(payload, dict):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item, var in _iter_variations(payload):
        price_block = var.get("price") or {}
        mrp = money_to_rupees(price_block.get("mrp"))
        offer = money_to_rupees(price_block.get("offerPrice"))
        if not mrp or mrp <= 0 or offer is None:
            continue
        sku = str(var.get("skuId") or "")
        if not sku or sku in seen:
            continue
        seen.add(sku)
        name = var.get("displayName") or item.get("displayName") or ""
        if not name:
            continue
        out.append({
            "sku_id": sku,
            "product_id": str(item.get("productId") or ""),
            "name": name,
            "brand": var.get("brandName") or item.get("brand") or "",
            "pack_text": var.get("quantityDescription") or "",
            "price": offer,
            "mrp": mrp,
            "in_stock": variation_in_stock(var),
            "image_id": ((var.get("imageIds") or [None])[0]
                         or var.get("imageId") or ""),
        })
    return out


def parse_page_cursors(payload: dict | None) -> tuple[int | None, str]:
    """(nextOffset, searchResultsOffset) for the next search page."""
    data = (payload or {}).get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None, ""
    try:
        nxt = int((data.get("pageOffset") or {}).get("nextOffset"))
    except (TypeError, ValueError):
        return None, ""
    return nxt, str(data.get("searchResultsOffset") or "")


def split_brand(name: str) -> tuple[str, str]:
    """Heuristic: leading token is the brand ("Amul Taaza ...") -> ("Amul", "Taaza ...")."""
    parts = name.split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return name, ""


# ---------------------------------------------------------------------------
# Session orchestration — the multi-step location/search flow, transport-free
# ---------------------------------------------------------------------------

@dataclass
class _ApiResponse:
    status: int
    text: str

    def json(self) -> dict:
        return json.loads(self.text)


class BlockedError(RuntimeError):
    """Swiggy refused the request (WAF challenge / block)."""


class InstamartSession:
    """Runs the geocode -> pin store -> search flow over an async `call`.

    `call(method, url, params, body, headers)` -> _ApiResponse. The real
    transport implements `call` from a WAF-bootstrapped Playwright page;
    tests inject a fake. All Swiggy-specific knowledge lives here, once.
    """

    def __init__(self, call) -> None:
        self._call = call
        self._store_cache: dict[str, tuple[float, str]] = {}

    def _headers(self) -> dict:
        return {"accept": "*/*",
                "x-build-version": BUILD_VERSION,
                "x-device-id": DEVICE_ID}

    def _guard(self, resp: _ApiResponse, what: str) -> dict:
        """Convert WAF refusals and non-JSON bodies into honest errors."""
        if resp.status in (202, 403):
            raise BlockedError(
                f"Swiggy refused {what} (HTTP {resp.status}) — the WAF "
                "challenge/block page was served instead of JSON.")
        if resp.status != 200:
            raise RuntimeError(f"Swiggy {what} returned HTTP {resp.status}")
        try:
            payload = resp.json()
        except ValueError as exc:
            raise BlockedError(
                f"Swiggy {what} returned a non-JSON body "
                "(challenge interstitial)") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Swiggy {what} returned an unexpected body")
        return payload

    async def geocode(self, area: str) -> dict:
        """Free-text area/pincode -> {lat, lng, address}."""
        payload = self._guard(await self._call(
            "GET", f"{API_BASE}/maps/suggestions",
            params={"input": area}, body=None, headers=self._headers()),
            "maps/suggestions")
        place_id = parse_geocode_suggestions(payload)
        if not place_id:
            raise LookupError(f"no Swiggy location match for {area!r}")
        payload = self._guard(await self._call(
            "GET", f"{API_BASE}/maps/address-widgets/v2",
            params={"place_id": place_id}, body=None, headers=self._headers()),
            "maps/address-widgets/v2")
        place = parse_address_widgets(payload)
        if not place:
            raise LookupError(f"Swiggy returned no coordinates for {area!r}")
        return place

    async def pin_store(self, place: dict) -> str:
        """Pin the session to the dark store serving these coordinates."""
        key = f"{round(place['lat'], 3)},{round(place['lng'], 3)}"
        hit = self._store_cache.get(key)
        if hit and time.monotonic() - hit[0] < STORE_CACHE_TTL_S:
            return hit[1]
        body = {"data": {"lat": place["lat"], "lng": place["lng"],
                         "address": place["address"], "addressId": "",
                         "annotation": place["address"],
                         "clientId": "INSTAMART-APP"}}
        resp = await self._call("POST", f"{API_BASE}/home/select-location/v2",
                                params=None, body=body,
                                headers=self._headers())
        self._guard(resp, "home/select-location/v2")
        store_id = extract_store_id(resp.text)
        if not store_id:
            raise LookupError("select-location returned no storeId — "
                              "Instamart may not serve this area")
        self._store_cache[key] = (time.monotonic(), store_id)
        return store_id

    async def search_pages(self, store_id: str, query: str):
        """Yield raw product dicts across paginated search pages."""
        offset, results_offset = 0, "0"
        for _ in range(MAX_SEARCH_PAGES):
            params = {"offset": offset, "ageConsent": "false",
                      "voiceSearchTrackingId": "",
                      "storeId": store_id, "primaryStoreId": store_id,
                      "secondaryStoreId": ""}
            body = {"facets": [], "sortAttribute": "", "query": query,
                    "search_results_offset": results_offset,
                    "page_type": "INSTAMART_SEARCH_PAGE",
                    "is_pre_search_tag": False}
            payload = self._guard(await self._call(
                "POST", f"{API_BASE}/search/v2", params=params, body=body,
                headers=self._headers()), "search/v2")
            yield parse_search_payload(payload)
            nxt, results_offset = parse_page_cursors(payload)
            if nxt is None or nxt <= offset:
                return
            offset = nxt


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

class InstamartTransport(ABC):
    """Pluggable transport: given a query + coords, return raw product dicts."""

    @abstractmethod
    async def search_raw(self, query: str, lat: float, lon: float) -> list[dict]:
        ...


class StubTransport(InstamartTransport):
    """In-memory transport for tests and offline demos."""

    def __init__(self, raws: list[dict] | None = None) -> None:
        self.raws = raws if raws is not None else []
        self.calls: list[tuple[str, float, float]] = []

    async def search_raw(self, query: str, lat: float, lon: float) -> list[dict]:
        self.calls.append((query, lat, lon))
        return self.raws


def playwright_available() -> bool:
    """Eager check: is the real transport usable in this environment?"""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


class PlaywrightWafTransport(InstamartTransport):
    """Real transport: WAF-bootstrap in Chromium, API calls from page context.

    Swiggy's AWS WAF serves a JS challenge to plain HTTP (202). A real
    browser solves it once and holds the `aws-waf-token`; all API calls are
    then issued from that page's context with fetch(..., {credentials:
    'include'}), so the fingerprint matches the token exactly.
    """

    def __init__(self, headless: bool = True,
                 timeout_ms: int = 15000) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._sem = asyncio.Semaphore(CONCURRENCY)
        self._last_request_at = 0.0
        self._page = None
        self._session: InstamartSession | None = None
        self._started = False

    async def _ensure(self) -> None:
        if self._started:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright is not installed — run "
                "`pip install playwright && playwright install chromium` "
                "to enable live Instamart data."
            ) from exc
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled",
                  "--no-sandbox", "--disable-dev-shm-usage"])
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=UA,
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )
        page = await context.new_page()
        # WAF bootstrap: load the site so the challenge runs and the token
        # cookie lands. If the page is still a challenge after the wait, the
        # IP is challenged/blocked — surface it honestly.
        await page.goto(INSTAMART_URL, wait_until="domcontentloaded",
                        timeout=self.timeout_ms)
        deadline = time.monotonic() + WAF_BOOTSTRAP_TIMEOUT_S
        token = None
        while time.monotonic() < deadline:
            cookies = await context.cookies()
            token = next((c["value"] for c in cookies
                          if c["name"] == "aws-waf-token"), None)
            if token:
                break
            await page.wait_for_timeout(1000)
        if not token:
            html = await page.content()
            lowered = html.lower()
            if "request blocked" in lowered or "access denied" in lowered:
                raise BlockedError(
                    "Swiggy served a 'request blocked' page to this browser — "
                    "the egress IP is distrusted. Live Instamart data needs a "
                    "non-flagged network.")
            if "captcha" in lowered:
                raise BlockedError(
                    "Swiggy served an interactive CAPTCHA, which a headless "
                    "browser cannot solve. Live Instamart data needs a "
                    "non-flagged network.")
            raise BlockedError(
                "AWS WAF challenge never resolved — no aws-waf-token was "
                "issued. Live Instamart data needs a non-flagged network.")
        self._page = page
        self._session = InstamartSession(self._page_call)
        self._started = True
        self._pw = pw
        self._browser = browser

    async def aclose(self) -> None:
        if self._started:
            await self._browser.close()
            await self._pw.stop()
            self._started = False
            self._session = None

    async def _page_call(self, method: str, url: str, params: dict | None,
                         body: dict | None, headers: dict) -> _ApiResponse:
        """Issue one API call from the WAF-bootstrapped page context."""
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        async with self._sem:
            wait = MIN_REQUEST_INTERVAL_S - (time.monotonic() - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            result = await self._page.evaluate(
                """async ({url, method, headers, body}) => {
                    const res = await fetch(url, {
                        method, headers,
                        body: body ? JSON.stringify(body) : undefined,
                        credentials: 'include'});
                    return {status: res.status, text: await res.text()};
                }""",
                {"url": url, "method": method, "headers": headers,
                 "body": body},
            )
            self._last_request_at = time.monotonic()
        return _ApiResponse(status=result["status"], text=result["text"])

    async def search_raw(self, query: str, lat: float, lon: float) -> list[dict]:
        await self._ensure()
        assert self._session is not None
        pincode = await self._latlng_to_pincode(lat, lon)
        place = await self._session.geocode(pincode)
        store_id = await self._session.pin_store(place)
        out: list[dict] = []
        async for page in self._session.search_pages(store_id, query):
            if not page:
                break
            out.extend(page)
        return out

    async def _latlng_to_pincode(self, lat: float, lon: float) -> str:
        """Reverse-geocode coordinates to a pincode for Swiggy's maps API.

        Falls back to SAUDA_PINCODE / Delhi when geocoding fails — documented,
        never silent.
        """
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
                        return str(pc)
        except Exception as exc:  # noqa: BLE001 — fallback below is the point
            log.debug("Nominatim reverse-geocode failed: %s", exc)
        log.info("using fallback pincode %s for (%s, %s)",
                 DEFAULT_PINCODE, lat, lon)
        return DEFAULT_PINCODE


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    product: Product
    snapshot: PriceSnapshot
    stored_at: float


class RealInstamartAdapter(PlatformAdapter):
    """Live Swiggy Instamart prices. platform_id stays "instamart"."""

    platform_id = "instamart"
    display_name = "Swiggy Instamart (live)"
    vertical = "grocery"
    uses_static_catalog = False   # prices come from live search, not catalog.py
    fee_table = FeeTable(
        platform_id="instamart",
        min_order_value=149.0,       # highest minimum order of the three
        free_delivery_above=199.0,
        delivery_fee_base=30.0,
        platform_fee=6.0,
        packaging_fee=5.0,
        gst_rate=0.05,
        surge_active=False,
        eta_min=15,
        eta_max=25,
    )

    def __init__(self, transport: InstamartTransport | None = None) -> None:
        self.transport = transport or PlaywrightWafTransport()
        self._cache: dict[str, _CacheEntry] = {}
        self._last_health: tuple[float, AdapterStatus] | None = None

    # -- account linking: not implemented for the real adapter (v1) --------

    async def link_account(self, phone: str) -> str:
        raise NotImplementedError(
            "Real Instamart OTP linking is not implemented yet — Swiggy's "
            "guest APIs cover location, search and prices without login. "
            "Search and prices work in guest mode."
        )

    async def verify_otp(self, link_ref: str, otp: str) -> UserSession:
        raise NotImplementedError(
            "Real Instamart OTP verification is not implemented yet."
        )

    # -- catalog -----------------------------------------------------------

    @staticmethod
    def _gtin(sku_id: str) -> str:
        # Namespaced: never collides with real GTINs in the static catalog.
        return f"instamart:{sku_id}"

    def _to_models(self, raw: dict) -> tuple[Product, PriceSnapshot] | None:
        if not raw.get("in_stock"):
            return None
        brand = raw["brand"] or split_brand(raw["name"])[0]
        _, rest = split_brand(raw["name"])
        name = rest or raw["name"]
        pack_size, unit, pack_label = parse_pack_size(raw["pack_text"])
        mrp = raw["mrp"] if raw["mrp"] else raw["price"]
        product = Product(
            gtin=self._gtin(raw["sku_id"]),
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
                timeout=90)
            n = len(raws)
            status = AdapterStatus(
                platform_id=self.platform_id, status="live",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=f"Instamart live search OK ({n} products in probe)")
        except Exception as exc:  # noqa: BLE001 — health must never raise
            log.warning("Instamart health probe failed: %s", exc)
            status = AdapterStatus(
                platform_id=self.platform_id, status="down",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=f"Instamart live probe failed: {exc}")
        self._last_health = (now, status)
        return status
