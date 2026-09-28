"""Real Myntra adapter — live prices via Myntra's own gateway search API.

Transport strategy (mirrors real_blinkit.py):
- The endpoint below is the SAME unsigned JSON API Myntra's own frontend
  calls for search results (documented independently by the
  indian-e-commerce-scapers repo and the sai1099/external_sources tracker,
  both crawled Sep 2026):
      GET https://www.myntra.com/gateway/v2/search/<query>
          ?p=1&rows=50&o=0&plaEnabled=false&xdEnabled=false
- We issue it from a REAL Chromium page context via Playwright (page
  fetch, after a homepage warm-up that sets the site's cookies), because
  datacenter HTTP is flagged by Myntra's bot protection.
- No login needed for search: guest mode.

Verified API shape (from the sources above):
    {"products": [{"productId": int, "productName": str, "brand": str,
                   "price": float, "mrp": float, "discount": float,
                   "rating": float, "ratingCount": int,
                   "searchImage": str, "landingPageUrl": str,
                   "sizes": [str, ...], "systemAttributes": [...],
                   "isPLA": bool (ad flag)}],
     "totalCount": int, ...}

LIVE verification from this workspace was NOT possible (egress IP is
Cloudflare/bot-flagged) — status is "unverified", parser is defensive and
fully covered by stub-transport tests. Run from a normal Indian residential
connection to verify live.

Identity signals: every result carries IdentitySignals (see ecom_common) —
brand, variant attributes (sizes + systemAttributes), seller (not disclosed
by Myntra search), delivery promise/fee (not disclosed at search level —
stays None, never guessed), plus an honest ambiguity marker. Myntra treats
each colour as its own style id, so a query like "nike tshirt" returns many
same-style siblings: each gets its own identity, and sponsored (isPLA)
cards are marked ambiguous.
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

log = logging.getLogger("sauda.real_myntra")

# ---------------------------------------------------------------------------
# Endpoint constants
# ---------------------------------------------------------------------------

SITE_URL = "https://www.myntra.com"
SEARCH_PATH = "/gateway/v2/search"
DETAIL_PATH = "/gateway/v2/product"     # /gateway/v2/product/<styleId> -> inventoryInfo
SEARCH_PARAMS = {"p": "1", "rows": "50", "o": "0",
                 "plaEnabled": "false", "xdEnabled": "false"}

CACHE_TTL_S = 180
HEALTH_TTL_S = 60
HEALTH_PROBE_QUERY = "tshirt"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_search_response(payload: dict | None) -> list[dict]:
    """Parse a /gateway/v2/search JSON body into raw product dicts.

    Defensive: unknown shapes are skipped, never fatal.
    """
    if not isinstance(payload, dict):
        return []
    products = payload.get("products")
    if not isinstance(products, list):
        return []
    out: list[dict] = []
    for p in products:
        if not isinstance(p, dict):
            continue
        pid = p.get("productId")
        name = p.get("productName") or ""
        price = p.get("price")
        if pid is None or not name or not isinstance(price, (int, float)):
            continue
        out.append({
            "product_id": pid,
            "name": str(name),
            "brand": str(p.get("brand") or ""),
            "price": float(price),
            "mrp": float(p["mrp"]) if isinstance(p.get("mrp"), (int, float)) else None,
            "discount": p.get("discount"),
            "rating": p.get("rating"),
            "rating_count": p.get("ratingCount"),
            "image_url": p.get("searchImage"),
            "landing_url": p.get("landingPageUrl") or "",
            "sizes": [str(s) for s in p.get("sizes", []) if s] if isinstance(p.get("sizes"), list) else [],
            "system_attributes": p.get("systemAttributes") if isinstance(p.get("systemAttributes"), list) else [],
            "sponsored": p.get("isPLA") is True,
        })
    return out


def extract_variant_attributes(system_attributes: list, sizes: list[str]) -> dict[str, str]:
    """Fold Myntra's systemAttributes + sizes into a flat variant dict.

    systemAttributes entries are usually {"attribute": "Gender", "value": "Men"}
    (some builds use {"name": ..., "value": ...}); anything else is kept as a
    string under a generic key. Sizes are joined as "size_options".
    """
    attrs: dict[str, str] = {}
    for i, sa in enumerate(system_attributes):
        if isinstance(sa, dict):
            key = str(sa.get("attribute") or sa.get("name") or f"attr_{i}").strip().lower()
            val = sa.get("value")
            if key and val is not None:
                attrs[key] = str(val)
        elif sa is not None:
            attrs[f"attr_{i}"] = str(sa)
    if sizes:
        attrs["size_options"] = ", ".join(sizes)
    return attrs


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

class MyntraTransport(ABC):
    """Pluggable transport: query -> parsed search JSON payload."""

    @abstractmethod
    async def search_raw(self, query: str) -> dict:
        ...


class StubTransport(MyntraTransport):
    """In-memory transport for tests and offline demos."""

    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {"products": []}
        self.calls: list[str] = []

    async def search_raw(self, query: str) -> dict:
        self.calls.append(query)
        return self.payload


class PlaywrightTransport(PageContextTransport, MyntraTransport):
    """Real transport: calls Myntra's gateway search API from a real Chromium
    page (same request the site's frontend makes, with its cookies)."""

    def __init__(self, headless: bool = True, timeout_ms: int = 20000) -> None:
        PageContextTransport.__init__(self, SITE_URL, headless, timeout_ms)

    def _search_url(self, query: str) -> str:
        params = "&".join(f"{k}={v}" for k, v in SEARCH_PARAMS.items())
        return f"{SITE_URL}{SEARCH_PATH}/{urllib.parse.quote(query)}?{params}"

    async def _request_raw(self, query: str) -> tuple[int, str, str]:
        url = self._search_url(query)
        result = await self._page.evaluate(
            """async (url) => {
                const res = await fetch(url, {
                    headers: {"Accept": "application/json",
                              "X-Requested-With": "XMLHttpRequest",
                              "Referer": "https://www.myntra.com/"},
                    credentials: "include"});
                return {status: res.status, text: await res.text()};
            }""",
            url,
        )
        return result["status"], url, result["text"]

    async def search_raw(self, query: str) -> dict:
        # PageContextTransport.search_raw gives us paced/retried raw text;
        # parse JSON here so error shape stays consistent.
        status, url, body = await PageContextTransport.search_raw(self, query)
        if status != 200:
            raise RuntimeError(f"Myntra gateway search returned HTTP {status}")
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise RuntimeError("Myntra returned a non-JSON body "
                               "(likely a bot challenge)") from exc
        return payload

    async def fetch_detail(self, style_id: str | int) -> dict:
        """Product detail API: per-size inventory via inventoryInfo.

        v1 exposes it for future enrichment; search() does not call it
        (one call per product per search would hammer the API).
        """
        await self._ensure()
        url = f"{SITE_URL}{DETAIL_PATH}/{style_id}"
        result = await self._page.evaluate(
            """async (url) => {
                const res = await fetch(url, {
                    headers: {"Accept": "application/json",
                              "X-Requested-With": "XMLHttpRequest",
                              "Referer": "https://www.myntra.com/"},
                    credentials: "include"});
                return {status: res.status, text: await res.text()};
            }""",
            url,
        )
        if result["status"] != 200:
            raise RuntimeError(f"Myntra detail API returned HTTP {result['status']}")
        return json.loads(result["text"])


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    product: Product
    snapshot: PriceSnapshot
    identity: IdentitySignals
    stored_at: float


class RealMyntraAdapter(PlatformAdapter):
    """Live Myntra prices. platform_id "myntra" (new platform — no mock)."""

    platform_id = "myntra"
    display_name = "Myntra (live)"
    vertical = "ecommerce"
    uses_static_catalog = False
    fee_table = FeeTable(
        platform_id="myntra",
        min_order_value=0.0,
        free_delivery_above=799.0,      # estimate: Myntra's published free-shipping bar
        delivery_fee_base=49.0,         # estimate: standard delivery charge
        platform_fee=0.0,
        packaging_fee=0.0,
        gst_rate=0.12,                  # estimate: typical apparel GST
        eta_min=3 * 24 * 60,            # e-com ETAs are days, not minutes
        eta_max=5 * 24 * 60,
    )

    def __init__(self, transport: MyntraTransport | None = None) -> None:
        self.transport = transport or PlaywrightTransport()
        self._cache: dict[str, _CacheEntry] = {}
        self._last_health: tuple[float, AdapterStatus] | None = None

    # -- account linking: not implemented (v1) --------------------------------

    async def link_account(self, phone: str) -> str:
        raise NotImplementedError(
            "Real Myntra OTP linking is not implemented yet. "
            "Search and prices work in guest mode."
        )

    async def verify_otp(self, link_ref: str, otp: str) -> UserSession:
        raise NotImplementedError(
            "Real Myntra OTP verification is not implemented yet."
        )

    # -- identity ------------------------------------------------------------

    @staticmethod
    def _gtin(style_id) -> str:
        return f"myntra:{style_id}"

    def identity(self, product_id: str) -> IdentitySignals | None:
        """Identity signals for a product from the last search(). None when
        unknown. Callers MUST consult this before feeding results into the
        Smart Split Cart — ``ambiguous=True`` means identity is not resolved."""
        entry = self._cache.get(product_id)
        return entry.identity if entry else None

    def _to_models(self, raw: dict) -> tuple[Product, PriceSnapshot, IdentitySignals]:
        gtin = self._gtin(raw["product_id"])
        mrp = raw["mrp"] if raw["mrp"] else raw["price"]
        # Prices shown are after Myntra's standard discount; delivery fee is
        # NOT disclosed at search level -> effective price labelled honestly.
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
            in_stock=True,
            eta_minutes=self.fee_table.eta_mid,
            freshness="live",
            captured_at=utcnow(),
        )
        identity = IdentitySignals(
            platform_id=self.platform_id,
            platform_product_id=str(raw["product_id"]),
            brand=raw["brand"], name=raw["name"],
            variant_attributes=extract_variant_attributes(
                raw["system_attributes"], raw["sizes"]),
            delivery_fee=None,   # not disclosed by Myntra search; fee_table carries the estimate
            price_effective=round(raw["price"], 2),
            price_includes_delivery=False,
            canonical_url=f"{SITE_URL}/{raw['landing_url']}".rstrip("/"),
            image_url=raw["image_url"],
            confidence="medium",
        )
        if raw["sponsored"]:
            identity.mark_ambiguous("Sponsored (PLA) card — ranking is paid, "
                                    "not the organic top match for the query.")
        elif not raw["brand"]:
            identity.mark_ambiguous("Brand not disclosed in the search payload.")
        return product, snapshot, identity

    # -- catalog --------------------------------------------------------------

    async def search(self, query: str, lat: float, lng: float,
                     session: UserSession) -> list[Product]:
        payload = await self.transport.search_raw(query)
        products: list[Product] = []
        now = time.monotonic()
        for raw in parse_search_response(payload):
            product, snapshot, identity = self._to_models(raw)
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
            payload = await asyncio.wait_for(
                self.transport.search_raw(HEALTH_PROBE_QUERY), timeout=25)
            n = len(parse_search_response(payload))
            status = AdapterStatus(
                platform_id=self.platform_id, status="live",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=f"Myntra live search OK ({n} products in probe)")
        except Exception as exc:  # noqa: BLE001 — health must never raise
            log.warning("Myntra health probe failed: %s", exc)
            status = AdapterStatus(
                platform_id=self.platform_id, status="down",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=f"Myntra live probe failed: {exc}")
        self._last_health = (now, status)
        return status
