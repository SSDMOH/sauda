"""Real Amazon.in adapter — live prices from Amazon's search pages.

Transport strategy (mirrors real_blinkit.py):
- Amazon India has NO open/unsigned search JSON API (only the suggestion
  autocomplete API is open; it returns suggestions, not products). So this
  adapter does what Amazon's own site does: loads
  ``https://www.amazon.in/s?k=<query>`` in a REAL Chromium via Playwright and
  extracts the server-rendered result cards from the RAW navigation response
  body via page-context DOM queries — never from datacenter HTTP, which
  Amazon's bot protection (Akamai) blocks outright.
- This is the most FRAGILE of the three e-com adapters: Amazon changes
  markup often, serves aggressive captchas ("Enter the characters you see
  below"), and personalises results. The parser uses structural selectors
  (data-asin cards) with multiple fallbacks and skips unknown cards rather
  than dying.
- No login needed for search: guest mode. Prices shown are the Buy-Box
  (winning-offer) price — the price Amazon itself displays.

Card shape extracted (page context, defensive):
    div.s-result-item[data-asin] (non-empty ASIN)
      title:   h2 a.a-link-normal span.a-text-normal (fallbacks included)
      price:   .a-price .a-offscreen (first = current)
      list:    .a-price[data-a-strike="true"] .a-offscreen (MRP, may be absent)
      image:   img.s-image
      rating:  span.a-icon-alt ("4.3 out of 5 stars")
      ad:      data-component-type="sp-sponsored-result" or .s-sponsored-label-info-icon
      delivery line from the card text ("FREE delivery ...", "₹40 delivery")

LIVE verification from this workspace was NOT possible (egress IP is
bot-flagged; Amazon serves captchas to flagged IPs) — status is
"unverified/blocked-from-here". Verify live from a normal Indian
residential connection with a real browser profile.

Identity signals: ASIN as platform id; brand via leading-token heuristic
(labelled medium confidence); storage/size variants extracted from the
title when present (e.g. "128 GB"); seller (Buy-Box winner) NOT disclosed
on search pages — stays None, never guessed; condition "new". Sponsored
cards and cards whose titles contain none of the query's significant tokens
(e.g. accessories matching "for <query>") are marked ambiguous so they can
never silently feed the Smart Split Cart.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
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

log = logging.getLogger("sauda.real_amazon")

# ---------------------------------------------------------------------------
# Endpoint constants
# ---------------------------------------------------------------------------

SITE_URL = "https://www.amazon.in"
SEARCH_PATH = "/s"
DP_URL = f"{SITE_URL}/dp"

CAPTCHA_MARKERS = (
    "enter the characters you see below",
    "amazon captcha",
    "sorry, we just need to make sure",
    "validatecaptcha",
)

CACHE_TTL_S = 180
HEALTH_TTL_S = 60
HEALTH_PROBE_QUERY = "pen"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_PRICE_RE = re.compile(r"₹\s?([\d,]+(?:\.\d+)?)")
_FEE_RE = re.compile(r"₹\s?([\d,]+(?:\.\d+)?)")
_STORAGE_RE = re.compile(r"(\d+\s?(?:GB|TB|MB))\b", re.IGNORECASE)


def _price_from_text(text: str | None) -> float | None:
    if not text:
        return None
    m = _PRICE_RE.search(text)
    return float(m.group(1).replace(",", "")) if m else None


def is_captcha_page(html: str | None) -> bool:
    """Detect Amazon's bot-check page from the raw response body."""
    if not html:
        return False
    lowered = html.lower()
    return any(marker in lowered for marker in CAPTCHA_MARKERS)


# Extraction runs in page context (JS); this is the script source so tests
# can pin the selectors and stubbed payloads can mirror its output shape.
EXTRACT_JS = """() => {
  const out = [];
  document.querySelectorAll('div.s-result-item[data-asin]').forEach(el => {
    const asin = (el.getAttribute('data-asin') || '').trim();
    if (!asin) return;
    const titleEl = el.querySelector(
      'h2 a.a-link-normal span.a-text-normal, h2 span.a-text-normal, ' +
      'span.a-size-base-plus.a-color-base.a-text-normal');
    const priceEl = el.querySelector('.a-price .a-offscreen');
    const listEl = el.querySelector('.a-price[data-a-strike="true"] .a-offscreen');
    const imgEl = el.querySelector('img.s-image');
    const ratingEl = el.querySelector('span.a-icon-alt');
    const sponsored = el.getAttribute('data-component-type') === 'sp-sponsored-result'
      || !!el.querySelector('.s-sponsored-label-info-icon');
    const txt = (el.innerText || '');
    let deliveryLine = null;
    for (const line of txt.split('\\n')) {
      if (/delivery/i.test(line)) { deliveryLine = line.trim(); break; }
    }
    out.push({
      asin: asin,
      title: titleEl ? titleEl.textContent.trim() : null,
      price_text: priceEl ? priceEl.textContent.trim() : null,
      list_price_text: listEl ? listEl.textContent.trim() : null,
      image_url: imgEl ? (imgEl.getAttribute('src') || null) : null,
      rating_text: ratingEl ? ratingEl.textContent.trim() : null,
      sponsored: sponsored,
      delivery_line: deliveryLine,
    });
  });
  return out;
}"""


def parse_search_cards(cards: list | None) -> list[dict]:
    """Normalise the EXTRACT_JS output into raw product dicts.

    Defensive: cards without an ASIN, title or price are dropped. Delivery
    fee is extracted only when the card states it explicitly; otherwise it
    stays None (never guessed)."""
    if not isinstance(cards, list):
        return []
    out: list[dict] = []
    for c in cards:
        if not isinstance(c, dict):
            continue
        asin = (c.get("asin") or "").strip()
        title = (c.get("title") or "").strip()
        price = _price_from_text(c.get("price_text"))
        if not asin or not title or price is None:
            continue
        delivery_line = c.get("delivery_line")
        delivery_fee: float | None = None
        if delivery_line:
            if re.search(r"\bfree\b", delivery_line, re.IGNORECASE):
                delivery_fee = 0.0
            else:
                delivery_fee = _price_from_text(delivery_line)
        out.append({
            "asin": asin,
            "title": title,
            "price": price,
            "list_price": _price_from_text(c.get("list_price_text")),
            "image_url": c.get("image_url"),
            "rating_text": c.get("rating_text"),
            "sponsored": c.get("sponsored") is True,
            "delivery_line": delivery_line,
            "delivery_fee": delivery_fee,
        })
    return out


def split_brand(title: str) -> tuple[str, str]:
    """Heuristic: leading token is the brand ("Apple iPhone 15 ...").
    Labelled medium-confidence by the adapter."""
    parts = title.split(None, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return title, ""


def extract_variant_attributes(title: str) -> dict[str, str]:
    """Best-effort variant extraction from the card title (storage sizes are
    the common Amazon.in variant axis shown on search cards)."""
    attrs: dict[str, str] = {}
    m = _STORAGE_RE.search(title)
    if m:
        attrs["storage"] = m.group(1).upper().replace(" ", "")
    return attrs


def query_tokens(query: str) -> list[str]:
    """Significant tokens of a query (>=3 chars, lowercased)."""
    return [t.lower() for t in re.findall(r"[a-zA-Z0-9]+", query) if len(t) >= 3]


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

class AmazonTransport(ABC):
    """Pluggable transport: query -> raw card list (EXTRACT_JS shape)."""

    @abstractmethod
    async def search_raw(self, query: str) -> list:
        ...


class StubTransport(AmazonTransport):
    """In-memory transport for tests and offline demos."""

    def __init__(self, cards: list | None = None) -> None:
        self.cards = cards or []
        self.calls: list[str] = []

    async def search_raw(self, query: str) -> list:
        self.calls.append(query)
        return self.cards


class PlaywrightTransport(PageContextTransport, AmazonTransport):
    """Real transport: loads the search page in a real Chromium and extracts
    result cards via page-context DOM queries."""

    def __init__(self, headless: bool = True, timeout_ms: int = 20000) -> None:
        PageContextTransport.__init__(self, SITE_URL, headless, timeout_ms)

    def _search_url(self, query: str) -> str:
        return f"{SITE_URL}{SEARCH_PATH}?k={urllib.parse.quote_plus(query)}"

    async def _request_raw(self, query: str) -> tuple[int, str, str]:
        url = self._search_url(query)
        resp = await self._page.goto(url, wait_until="domcontentloaded",
                                     timeout=self.timeout_ms)
        status = resp.status if resp else 0
        return status, url, (await resp.text()) if resp else ""

    async def search_raw(self, query: str) -> list:
        status, url, body = await PageContextTransport.search_raw(self, query)
        if status != 200:
            raise RuntimeError(f"Amazon search returned HTTP {status}")
        if is_captcha_page(body) or "validatecaptcha" in url.lower():
            raise RuntimeError("Amazon served a bot-check/captcha page — "
                               "blocked from this network/profile")
        cards = await self._page.evaluate(EXTRACT_JS)
        if not isinstance(cards, list):
            raise RuntimeError("Amazon card extraction returned no card list "
                               "(markup changed?)")
        return cards


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    product: Product
    snapshot: PriceSnapshot
    identity: IdentitySignals
    stored_at: float


class RealAmazonAdapter(PlatformAdapter):
    """Live Amazon.in prices. platform_id "amazon" (new platform — no mock)."""

    platform_id = "amazon"
    display_name = "Amazon.in (live)"
    vertical = "ecommerce"
    uses_static_catalog = False
    fee_table = FeeTable(
        platform_id="amazon",
        min_order_value=0.0,
        free_delivery_above=499.0,      # estimate: Amazon.in's typical free-delivery bar
        delivery_fee_base=40.0,         # estimate: standard non-Prime delivery
        platform_fee=0.0,
        packaging_fee=0.0,
        gst_rate=0.18,                  # estimate: typical electronics GST
        eta_min=2 * 24 * 60,
        eta_max=5 * 24 * 60,
    )

    def __init__(self, transport: AmazonTransport | None = None) -> None:
        self.transport = transport or PlaywrightTransport()
        self._cache: dict[str, _CacheEntry] = {}
        self._last_health: tuple[float, AdapterStatus] | None = None

    # -- account linking: not implemented (v1) --------------------------------

    async def link_account(self, phone: str) -> str:
        raise NotImplementedError(
            "Real Amazon OTP linking is not implemented yet. "
            "Search and prices work in guest mode."
        )

    async def verify_otp(self, link_ref: str, otp: str) -> UserSession:
        raise NotImplementedError(
            "Real Amazon OTP verification is not implemented yet."
        )

    # -- identity ------------------------------------------------------------

    @staticmethod
    def _gtin(asin: str) -> str:
        return f"amazon:{asin}"

    def identity(self, product_id: str) -> IdentitySignals | None:
        """Identity signals for a product from the last search(). None when
        unknown. Callers MUST consult this before feeding results into the
        Smart Split Cart — ``ambiguous=True`` means identity is not resolved."""
        entry = self._cache.get(product_id)
        return entry.identity if entry else None

    def _to_models(self, raw: dict, query: str) -> tuple[Product, PriceSnapshot, IdentitySignals]:
        gtin = self._gtin(raw["asin"])
        mrp = raw["list_price"] if raw["list_price"] else raw["price"]
        brand, name = split_brand(raw["title"])
        delivery_fee = raw["delivery_fee"]
        effective = round(raw["price"] + (delivery_fee or 0.0), 2)
        product = Product(
            gtin=gtin,
            brand=brand, name=name or raw["title"],
            pack_size=1.0, unit="pcs", pack_label="1 pc",
            mrp=round(mrp, 2),
        )
        snapshot = PriceSnapshot(
            platform_id=self.platform_id,
            gtin=gtin,
            price=effective,            # Buy-Box price + delivery fee when the card states one
            mrp=product.mrp,
            per_unit_price=effective,
            in_stock=True,              # cards shown are buyable; OOS cards are filtered by Amazon
            eta_minutes=self.fee_table.eta_mid,
            freshness="live",
            captured_at=utcnow(),
        )
        identity = IdentitySignals(
            platform_id=self.platform_id,
            platform_product_id=raw["asin"],
            brand=brand, name=raw["title"],
            variant_attributes=extract_variant_attributes(raw["title"]),
            seller=None,                # Buy-Box seller not shown on search cards; PDP-only
            delivery_promise=raw["delivery_line"],
            delivery_fee=delivery_fee,
            price_effective=effective,
            price_includes_delivery=delivery_fee is not None,
            canonical_url=f"{DP_URL}/{raw['asin']}",
            image_url=raw["image_url"],
            confidence="medium",        # brand is a leading-token heuristic
        )
        if raw["sponsored"]:
            identity.mark_ambiguous("Sponsored card — ranking is paid, "
                                    "not the organic top match for the query.")
        else:
            tokens = query_tokens(query)
            title_lower = raw["title"].lower()
            if tokens and not any(t in title_lower for t in tokens):
                identity.mark_ambiguous(
                    "Title shares no significant token with the query — "
                    "likely an accessory or complementary item, not the "
                    "queried product.")
        return product, snapshot, identity

    # -- catalog --------------------------------------------------------------

    async def search(self, query: str, lat: float, lng: float,
                     session: UserSession) -> list[Product]:
        cards = await self.transport.search_raw(query)
        products: list[Product] = []
        now = time.monotonic()
        for raw in parse_search_cards(cards):
            product, snapshot, identity = self._to_models(raw, query)
            self._cache[product.gtin] = _CacheEntry(product, snapshot, identity, now)
            products.append(product)
        return products   # Amazon relevance order preserved

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
            cards = await asyncio.wait_for(
                self.transport.search_raw(HEALTH_PROBE_QUERY), timeout=30)
            n = len(parse_search_cards(cards))
            status = AdapterStatus(
                platform_id=self.platform_id, status="live",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=f"Amazon.in live search OK ({n} products in probe)")
        except Exception as exc:  # noqa: BLE001 — health must never raise
            log.warning("Amazon health probe failed: %s", exc)
            status = AdapterStatus(
                platform_id=self.platform_id, status="down",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=f"Amazon.in live probe failed: {exc}")
        self._last_health = (now, status)
        return status
