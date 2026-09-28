"""Real Flipkart adapter — live prices from Flipkart's server-rendered search.

Transport strategy (mirrors real_blinkit.py):
- Flipkart has NO standalone search JSON API — the search page issues zero
  XHR/fetch calls for result data (verified by CDP network trace, documented
  2026-06-03 in the flipkart_com browse skill). Results are server-rendered
  into the HTML as ``window.__INITIAL_STATE__ = {...}``.
- So this adapter loads https://www.flipkart.com/search?q=<query> in a REAL
  Chromium via Playwright and reads the RAW NAVIGATION RESPONSE BODY
  (``(await page.goto(url)).text()``) — the React app deletes
  window.__INITIAL_STATE__ from the live DOM on hydration, so page.content()
  will NOT contain it. This gotcha is the whole reason for the response-body
  path; DOM scraping of cards is a documented last resort only.
- No login needed for search: guest mode.

Response-shape note: the envelope nests products at
``slot -> {"slotType": "WIDGET", "widget": {"type": "PRODUCT_SUMMARY",
"data": {"products": [...]}}}`` — VERIFIED against a real live Flipkart
search page fetched from this workspace 2026-09-28 (40 products parsed from
the raw HTML, prices/MRP/brand/variant/sponsored flags all correct).
Transport-level (Playwright) verification still needs a non-flagged network.

Parsed shape (verified):
    state.pageDataV4.page.data — slots (some are ARRAYS of widgets; flatten)
      slot envelope {"slotType": "WIDGET", "widget": {"type": ..., "data": ...}}
      widget.widget.type == "PRODUCT_SUMMARY" -> widget.widget.data.products[]
        product.productInfo.value:
          id (pid), titles.{title|newTitle, superTitle=brand, subtitle=variant},
          pricing.prices[]: current = strikeOff:false, MRP = strikeOff:true
            (integer INR, never assume prices[0] is current),
          pricing.totalDiscount (percent), pricing.discountAmount (INR),
          rating.{average, count, reviewCount}, availability.displayState,
          baseUrl -> canonical URL = "https://www.flipkart.com" + baseUrl
        product.adInfo populated -> sponsored card.

LIVE verification from this workspace was NOT possible (egress IP is
bot-flagged; Flipkart also serves transient 500s to datacenter IPs) —
status is "unverified", parser is defensive and fully covered by
stub-transport tests. Verify live from a normal Indian residential
connection.

Identity signals: pid, brand (superTitle), variant (subtitle, e.g. "Black,
True Wireless"), seller (NOT shown at search level — stays None, never
guessed), delivery (not shown at search level — fee_table carries the
estimate), sponsored cards marked ambiguous. Price shown is after Flipkart's
standard discount; coupons at checkout are not visible — effective price is
labelled honestly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import dataclass

from adapters.base import PlatformAdapter
from adapters.ecom_common import (
    IdentitySignals,
    PageContextTransport,
    playwright_available,
)
from models import (
    AdapterStatus,
    FeeTable,
    PriceSnapshot,
    PriceUnavailableError,
    Product,
    UserSession,
    utcnow,
)

log = logging.getLogger("sauda.real_flipkart")

# ---------------------------------------------------------------------------
# Endpoint constants
# ---------------------------------------------------------------------------

SITE_URL = "https://www.flipkart.com"
SEARCH_PATH = "/search"

CACHE_TTL_S = 180
HEALTH_TTL_S = 60
HEALTH_PROBE_QUERY = "pen"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def extract_initial_state(html: str | None) -> dict | None:
    """Brace-balanced extraction of ``window.__INITIAL_STATE__ = {...}`` from
    the RAW navigation response body. Returns the parsed dict or None when
    the blob is absent (challenge page, bot check, layout change)."""
    if not html:
        return None
    marker = "window.__INITIAL_STATE__ = "
    start = html.find(marker)
    if start == -1:
        return None
    i = start + len(marker)
    while i < len(html) and html[i] in " \t\n\r":
        i += 1
    if i >= len(html) or html[i] != "{":
        return None
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(html)):
        ch = html[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[i:j + 1])
                except ValueError:
                    return None
    return None


def _flatten_widgets(slot_value) -> list[dict]:
    """A slot under page.data may be a widget or an ARRAY of widgets
    (slot 10003 repeats PRODUCT_SUMMARY widgets). Flatten one level."""
    if isinstance(slot_value, dict):
        return [slot_value]
    if isinstance(slot_value, list):
        return [w for w in slot_value if isinstance(w, dict)]
    return []


def parse_search_state(state: dict | None) -> list[dict]:
    """Walk state.pageDataV4.page.data -> PRODUCT_SUMMARY widgets ->
    product cards. Dedupe by pid (cards repeat across slots). Defensive:
    unknown shapes are skipped, never fatal."""
    if not isinstance(state, dict):
        return []
    try:
        page_data = state["pageDataV4"]["page"]["data"]
    except (KeyError, TypeError):
        return []
    if not isinstance(page_data, dict):
        return []
    seen: set[str] = set()
    out: list[dict] = []
    for slot in page_data.values():
        for widget in _flatten_widgets(slot):
            if not isinstance(widget, dict):
                continue
            # widget envelope: {"slotType": "WIDGET", "widget": {"type": ...,
            #   "data": {"products": [...]}}} — verified against live page HTML
            # 2026-09-28; products live under widget.widget.data, not widget.data.
            inner = widget.get("widget") or {}
            if inner.get("type") != "PRODUCT_SUMMARY":
                continue
            products = (inner.get("data") or {}).get("products")
            if not isinstance(products, list):
                continue
            for prod in products:
                v = ((prod or {}).get("productInfo") or {}).get("value") or {}
                pid = v.get("id")
                if not pid or pid in seen or not isinstance(v, dict):
                    continue
                titles = v.get("titles") or {}
                title = titles.get("title") or titles.get("newTitle") or ""
                if not title:
                    continue
                seen.add(pid)
                prices = (v.get("pricing") or {}).get("prices") or []
                current = mrp = None
                for pe in prices:
                    if not isinstance(pe, dict) or not isinstance(pe.get("value"), (int, float)):
                        continue
                    if pe.get("strikeOff"):
                        if mrp is None:
                            mrp = float(pe["value"])
                    elif current is None:
                        current = float(pe["value"])
                rating = v.get("rating") or {}
                out.append({
                    "product_id": pid,
                    "name": str(title),
                    "brand": str(titles.get("superTitle") or ""),
                    "variant": str(titles.get("subtitle") or ""),
                    "price": current,
                    "mrp": mrp,
                    "discount_pct": (v.get("pricing") or {}).get("totalDiscount"),
                    "discount_amount": (v.get("pricing") or {}).get("discountAmount"),
                    "rating": rating.get("average"),
                    "rating_count": rating.get("count"),
                    "review_count": rating.get("reviewCount"),
                    "in_stock": (v.get("availability") or {}).get("displayState") == "IN_STOCK",
                    "base_url": v.get("baseUrl") or "",
                    "sponsored": bool((prod or {}).get("adInfo")),
                })
    return out


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

class FlipkartTransport(ABC):
    """Pluggable transport: query -> parsed __INITIAL_STATE__ dict."""

    @abstractmethod
    async def search_raw(self, query: str) -> dict | None:
        ...


class StubTransport(FlipkartTransport):
    """In-memory transport for tests and offline demos."""

    def __init__(self, state: dict | None = None) -> None:
        self.state = state or {"pageDataV4": {"page": {"data": {}}}}
        self.calls: list[str] = []

    async def search_raw(self, query: str) -> dict | None:
        self.calls.append(query)
        return self.state


class PlaywrightTransport(PageContextTransport, FlipkartTransport):
    """Real transport: loads the search page in a real Chromium and parses
    the embedded state from the raw navigation response body."""

    def __init__(self, headless: bool = True, timeout_ms: int = 20000) -> None:
        PageContextTransport.__init__(self, SITE_URL, headless, timeout_ms)

    def _search_url(self, query: str, page: int = 1) -> str:
        q = urllib.parse.quote_plus(query)
        return f"{SITE_URL}{SEARCH_PATH}?q={q}&page={page}&sort=relevance"

    async def _request_raw(self, query: str) -> tuple[int, str, str]:
        url = self._search_url(query)
        resp = await self._page.goto(url, wait_until="domcontentloaded",
                                     timeout=self.timeout_ms)
        status = resp.status if resp else 0
        return status, url, (await resp.text()) if resp else ""

    async def search_raw(self, query: str) -> dict | None:
        status, url, body = await PageContextTransport.search_raw(self, query)
        if status == 500:
            # Flipkart serves transient 500s to some IPs; one extra attempt.
            log.warning("Flipkart transient 500; retrying once after 3s")
            await asyncio.sleep(3)
            status, url, body = await PageContextTransport.search_raw(self, query)
        if status != 200:
            raise RuntimeError(f"Flipkart search returned HTTP {status}")
        state = extract_initial_state(body)
        if state is None:
            raise RuntimeError("Flipkart page had no __INITIAL_STATE__ blob "
                               "(challenge page or layout change)")
        return state


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    product: Product
    snapshot: PriceSnapshot
    identity: IdentitySignals
    stored_at: float


class RealFlipkartAdapter(PlatformAdapter):
    """Live Flipkart prices. platform_id "flipkart" (new platform — no mock)."""

    platform_id = "flipkart"
    display_name = "Flipkart (live)"
    vertical = "ecommerce"
    uses_static_catalog = False
    fee_table = FeeTable(
        platform_id="flipkart",
        min_order_value=0.0,
        free_delivery_above=500.0,      # estimate: Flipkart's typical free-delivery bar
        delivery_fee_base=40.0,         # estimate: standard delivery charge
        platform_fee=0.0,
        packaging_fee=0.0,
        gst_rate=0.18,                  # estimate: typical electronics GST
        eta_min=2 * 24 * 60,
        eta_max=4 * 24 * 60,
    )

    def __init__(self, transport: FlipkartTransport | None = None) -> None:
        self.transport = transport or PlaywrightTransport()
        self._cache: dict[str, _CacheEntry] = {}
        self._last_health: tuple[float, AdapterStatus] | None = None

    # -- account linking: not implemented (v1) --------------------------------

    async def link_account(self, phone: str) -> str:
        raise NotImplementedError(
            "Real Flipkart OTP linking is not implemented yet. "
            "Search and prices work in guest mode."
        )

    async def verify_otp(self, link_ref: str, otp: str) -> UserSession:
        raise NotImplementedError(
            "Real Flipkart OTP verification is not implemented yet."
        )

    # -- identity ------------------------------------------------------------

    @staticmethod
    def _gtin(pid: str) -> str:
        return f"flipkart:{pid}"

    def identity(self, product_id: str) -> IdentitySignals | None:
        """Identity signals for a product from the last search(). None when
        unknown. Callers MUST consult this before feeding results into the
        Smart Split Cart — ``ambiguous=True`` means identity is not resolved."""
        entry = self._cache.get(product_id)
        return entry.identity if entry else None

    def _to_models(self, raw: dict) -> tuple[Product, PriceSnapshot, IdentitySignals] | None:
        if raw["price"] is None:
            return None     # no price disclosed -> not a comparable offer
        gtin = self._gtin(raw["product_id"])
        mrp = raw["mrp"] if raw["mrp"] else raw["price"]
        product = Product(
            gtin=gtin,
            brand=raw["brand"], name=raw["name"],
            pack_size=1.0, unit="pcs", pack_label="1 pc",
            mrp=round(mrp, 2),
        )
        snapshot = PriceSnapshot(
            platform_id=self.platform_id,
            gtin=gtin,
            price=round(raw["price"], 2),
            mrp=product.mrp,
            per_unit_price=round(raw["price"], 2),
            in_stock=raw["in_stock"],
            eta_minutes=self.fee_table.eta_mid,
            freshness="live",
            captured_at=utcnow(),
        )
        variant_attrs: dict[str, str] = {}
        if raw["variant"]:
            variant_attrs["variant"] = raw["variant"]
        identity = IdentitySignals(
            platform_id=self.platform_id,
            platform_product_id=raw["product_id"],
            brand=raw["brand"], name=raw["name"],
            variant_attributes=variant_attrs,
            seller=None,            # not shown on Flipkart search cards; PDP-only
            delivery_fee=None,      # not shown at search level; fee_table carries the estimate
            price_effective=round(raw["price"], 2),
            price_includes_delivery=False,
            canonical_url=f"{SITE_URL}{raw['base_url']}" if raw["base_url"] else "",
            confidence="medium",
        )
        if raw["sponsored"]:
            identity.mark_ambiguous("Sponsored card — ranking is paid, "
                                    "not the organic top match for the query.")
        return product, snapshot, identity

    # -- catalog --------------------------------------------------------------

    async def search(self, query: str, lat: float, lng: float,
                     session: UserSession) -> list[Product]:
        state = await self.transport.search_raw(query)
        products: list[Product] = []
        now = time.monotonic()
        for raw in parse_search_state(state):
            models = self._to_models(raw)
            if models is None:
                continue
            product, snapshot, identity = models
            self._cache[product.gtin] = _CacheEntry(product, snapshot, identity, now)
            products.append(product)
        return products   # API relevance order preserved

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
            state = await asyncio.wait_for(
                self.transport.search_raw(HEALTH_PROBE_QUERY), timeout=30)
            n = len(parse_search_state(state))
            status = AdapterStatus(
                platform_id=self.platform_id, status="live",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=f"Flipkart live search OK ({n} products in probe)")
        except Exception as exc:  # noqa: BLE001 — health must never raise
            log.warning("Flipkart health probe failed: %s", exc)
            status = AdapterStatus(
                platform_id=self.platform_id, status="down",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=f"Flipkart live probe failed: {exc}")
        self._last_health = (now, status)
        return status
