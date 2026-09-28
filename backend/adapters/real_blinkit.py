"""Real Blinkit adapter — live prices via Blinkit's own search API.

Transport strategy (verified 2026-09-28, see docs/REAL_BLINKIT.md):
- Direct server-side HTTP from datacenter IPs is blocked by Cloudflare: every
  request returns 403, even with Chrome TLS impersonation (curl_cffi).
- Blinkit's search API itself (POST /v1/layout/search) is open and unsigned —
  it works when issued from a real browser session, which is exactly how the
  site's own frontend calls it (verified via the CartFiller reference).
- So this adapter drives a real Chromium via Playwright and issues the search
  API from page context: the same requests the site itself makes, at
  human-plausible pace (bounded concurrency + backoff + min request interval).

Guest mode: search + prices work WITHOUT login. Account linking (headless OTP
flow) is not implemented yet — link_account/verify_otp raise NotImplementedError
with a clear message, and the API layer converts that to HTTP 501.

Response shape (verified against CartFiller's parser):
    response.snippets[] where widget_type contains "product_card"
      data.atc_action.add_to_cart.cart_item:
        {product_id: int, product_name: str, price: float, mrp: float,
         unit: str ("500 ml"), group_id: int, image_url: str}
      data.name.text, data.variant.text, data.normal_price.text ("₹55"),
      data.is_sold_out: bool
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
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

log = logging.getLogger("sauda.real_blinkit")

# ---------------------------------------------------------------------------
# Endpoint constants (verified 2026-09-28)
# ---------------------------------------------------------------------------

SEARCH_URL = "https://blinkit.com/v1/layout/search"
APP_HEADERS = {
    "app_client": "consumer_web",
    "app_version": "1010101010",
    "web_app_version": "1008010016",
}
DEFAULT_LAT = 28.6139   # Delhi — same default as the REST API
DEFAULT_LON = 77.2090

CACHE_TTL_S = 120        # short cache; snapshots carry honest timestamps
HEALTH_TTL_S = 60
CONCURRENCY = 3          # stay under Blinkit's search rate limit
MIN_REQUEST_INTERVAL_S = 0.4


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_PACK_RE = re.compile(
    r"([\d.]+)\s*(?:x\s*([\d.]+))?\s*(kg|g|ml|ltr|l|pcs|pc|pack|packs)\b",
    re.IGNORECASE,
)


def parse_pack_size(text: str) -> tuple[float, str, str]:
    """Parse Blinkit unit text like "500 ml", "1 kg", "6 pcs", "3 x 100 g".

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


def _price_from_text(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"₹\s?([\d,]+(?:\.\d+)?)", text)
    return float(m.group(1).replace(",", "")) if m else None


def parse_search_response(payload: dict | None) -> list[dict]:
    """Parse a /v1/layout/search JSON body into raw product dicts.

    Defensive: unknown widget shapes are skipped, never fatal. Mirrors the
    field selection of CartFiller's verified parser.
    """
    if not isinstance(payload, dict):
        return []
    snippets = payload.get("response", {}).get("snippets")
    if not isinstance(snippets, list):
        return []
    out: list[dict] = []
    for snip in snippets:
        if not isinstance(snip, dict):
            continue
        if "product_card" not in str(snip.get("widget_type") or ""):
            continue
        data = snip.get("data") or {}
        ci = ((data.get("atc_action") or {}).get("add_to_cart") or {}).get("cart_item") or {}
        if not isinstance(ci.get("product_id"), int) or not isinstance(ci.get("price"), (int, float)):
            continue
        name = ci.get("product_name") or (data.get("name") or {}).get("text") or ""
        if not name:
            continue
        unit_text = ci.get("unit") or (data.get("variant") or {}).get("text") or ""
        out.append({
            "product_id": ci["product_id"],
            "name": name,
            "unit_text": unit_text,
            "price": float(ci["price"]),
            "mrp": float(ci["mrp"]) if isinstance(ci.get("mrp"), (int, float)) else _price_from_text(
                (data.get("normal_price") or {}).get("text")),
            "sold_out": data.get("is_sold_out") is True,
            "image_url": ci.get("image_url"),
            "group_id": ci.get("group_id"),
        })
    return out


def split_brand(name: str) -> tuple[str, str]:
    """Heuristic: leading token is the brand ("Amul Taaza ...") -> ("Amul", "Taaza ...")."""
    parts = name.split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return name, ""


# ---------------------------------------------------------------------------
# Transports — how the search API is reached
# ---------------------------------------------------------------------------

class BlinkitTransport(ABC):
    """Pluggable transport: given a query + coords, return raw search JSON."""

    @abstractmethod
    async def search_raw(self, query: str, lat: float, lon: float) -> dict:
        ...


class StubTransport(BlinkitTransport):
    """In-memory transport for tests and offline demos."""

    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {"response": {"snippets": []}}
        self.calls: list[tuple[str, float, float]] = []

    async def search_raw(self, query: str, lat: float, lon: float) -> dict:
        self.calls.append((query, lat, lon))
        return self.payload


def playwright_available() -> bool:
    """Eager check: is the real transport usable in this environment?"""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


class PlaywrightTransport(BlinkitTransport):
    """Real transport: issues the search API from a real Chromium page.

    A real browser passes Cloudflare's challenge where datacenter HTTP clients
    get 403. Requires the `playwright` package and a downloaded Chromium
    (`pip install playwright && playwright install chromium`).
    """

    def __init__(self, headless: bool = True, timeout_ms: int = 15000) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._sem = asyncio.Semaphore(CONCURRENCY)
        self._last_request_at = 0.0
        self._page = None
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
                "to enable live Blinkit data."
            ) from exc
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=self.headless)
        context = await browser.new_context(
            viewport={"width": 1366, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"),
            locale="en-IN",
        )
        page = await context.new_page()
        # Warm up: load the site once so Cloudflare cookies/challenge settle.
        await page.goto("https://blinkit.com/", wait_until="domcontentloaded",
                        timeout=self.timeout_ms)
        await page.wait_for_timeout(2500)
        self._page = page
        self._started = True
        self._pw = pw
        self._browser = browser

    async def aclose(self) -> None:
        if self._started:
            await self._browser.close()
            await self._pw.stop()
            self._started = False

    async def search_raw(self, query: str, lat: float, lon: float) -> dict:
        await self._ensure()
        async with self._sem:
            # human-plausible pacing between requests
            wait = MIN_REQUEST_INTERVAL_S - (time.monotonic() - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            body = {"applied_filters": None, "previous_search_query": query}
            headers = dict(APP_HEADERS)
            headers["lat"] = str(lat)
            headers["lon"] = str(lon)
            url = f"{SEARCH_URL}?q={query}&search_type=type_to_search"
            for attempt in range(7):
                result = await self._page.evaluate(
                    """async ({url, headers, body}) => {
                        const res = await fetch(url, {
                            method: 'POST', headers,
                            body: JSON.stringify(body), credentials: 'include'});
                        return {status: res.status,
                                retryAfter: res.headers.get('retry-after'),
                                text: await res.text()};
                    }""",
                    {"url": url, "headers": headers, "body": body},
                )
                status = result["status"]
                if status in (429, 503) and attempt < 6:
                    ra = result.get("retryAfter")
                    backoff = min(8.0, float(ra)) if ra and float(ra) > 0 else min(6.0, 0.5 * 2 ** attempt)
                    log.warning("Blinkit rate-limited (attempt %d); backing off %.1fs",
                                attempt, backoff)
                    await asyncio.sleep(backoff)
                    continue
                self._last_request_at = time.monotonic()
                if status != 200:
                    raise RuntimeError(f"Blinkit search API returned HTTP {status}")
                import json
                try:
                    return json.loads(result["text"])
                except ValueError as exc:
                    raise RuntimeError("Blinkit returned a non-JSON body "
                                       "(likely a challenge page)") from exc
            raise RuntimeError("Blinkit search rate-limit retries exhausted")


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    product: Product
    snapshot: PriceSnapshot
    stored_at: float


class RealBlinkitAdapter(PlatformAdapter):
    """Live Blinkit prices. platform_id stays "blinkit" so callers are unchanged."""

    platform_id = "blinkit"
    display_name = "Blinkit (live)"
    vertical = "grocery"
    uses_static_catalog = False   # prices come from live search, not catalog.py
    fee_table = FeeTable(
        platform_id="blinkit",
        min_order_value=99.0,
        free_delivery_above=199.0,
        delivery_fee_base=25.0,
        platform_fee=5.0,
        packaging_fee=4.0,
        gst_rate=0.05,
        eta_min=10,
        eta_max=15,
    )

    def __init__(self, transport: BlinkitTransport | None = None) -> None:
        self.transport = transport or PlaywrightTransport()
        self._cache: dict[str, _CacheEntry] = {}
        self._last_health: tuple[float, AdapterStatus] | None = None

    # -- account linking: not implemented for the real adapter (v1) --------

    async def link_account(self, phone: str) -> str:
        raise NotImplementedError(
            "Real Blinkit OTP linking is not implemented yet — it needs "
            "Blinkit's headless OTP flow. Search and prices work in guest mode."
        )

    async def verify_otp(self, link_ref: str, otp: str) -> UserSession:
        raise NotImplementedError(
            "Real Blinkit OTP verification is not implemented yet."
        )

    # -- catalog -----------------------------------------------------------

    @staticmethod
    def _gtin(product_id: int) -> str:
        # Namespaced: never collides with real GTINs in the static catalog.
        return f"blinkit:{product_id}"

    def _to_models(self, raw: dict) -> tuple[Product, PriceSnapshot] | None:
        if raw.get("sold_out"):
            return None
        brand, name = split_brand(raw["name"])
        pack_size, unit, pack_label = parse_pack_size(raw["unit_text"])
        mrp = raw["mrp"] if raw["mrp"] else raw["price"]
        product = Product(
            gtin=self._gtin(raw["product_id"]),
            brand=brand, name=name or raw["name"],
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
        payload = await self.transport.search_raw(query, lat, lng)
        products: list[Product] = []
        now = time.monotonic()
        for raw in parse_search_response(payload):
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
            payload = await asyncio.wait_for(
                self.transport.search_raw("salt", DEFAULT_LAT, DEFAULT_LON),
                timeout=25)
            n = len(parse_search_response(payload))
            status = AdapterStatus(
                platform_id=self.platform_id, status="live",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=f"Blinkit live search OK ({n} products in probe)")
        except Exception as exc:  # noqa: BLE001 — health must never raise
            log.warning("Blinkit health probe failed: %s", exc)
            status = AdapterStatus(
                platform_id=self.platform_id, status="down",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=f"Blinkit live probe failed: {exc}")
        self._last_health = (now, status)
        return status
