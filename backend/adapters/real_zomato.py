"""Real Zomato adapter — live food-delivery data via Zomato's public pages.

Transport strategy (verified 2026-09-28, live from this workspace):
- Zomato's internal JSON API (POST /webroutes/search/home) requires a
  session-bound CSRF token — it is NOT open/unsigned, so this adapter does
  not use it.
- Zomato's server-rendered delivery dish pages ARE public and unsigned:
      GET https://www.zomato.com/<city>/delivery/dish-<dish>
  e.g. https://www.zomato.com/ncr/delivery/dish-biryani
  returns 200 with a `SECTION_SEARCH_RESULT` JSON blob embedded in the HTML
  (9 restaurant cards, the server-side cap). No cookies, no CSRF, no login.
  Plain HTTPS from datacenter egress works (verified live 2026-09-28).
- So this adapter uses a plain-HTTP transport (httpx, lazy import). The
  transport is pluggable (ZomatoTransport ABC) — a Playwright fallback can be
  dropped in later if Zomato ever starts bot-blocking datacenter IPs.

Guest mode: search + prices work WITHOUT login. Account linking (OTP flow)
is not implemented — link_account/verify_otp raise NotImplementedError with
a clear message, and the API layer converts that to HTTP 501.

Mapping notes (food is not grocery SKUs):
- Zomato's public search surface is RESTAURANT-level: each result is a
  restaurant serving the dish, with `cfo` ("cost for one"), a delivery
  rating, delivery time and online-ordering availability. Dish-level menu
  prices are NOT exposed publicly (the order-page menu loads client-side).
- One Product per restaurant: gtin "zomato:<resId>", brand = restaurant
  name, name = "<dish> @ <restaurant>". pack_size=1, unit="serving",
  pack_label="cost for one (Zomato)". price == mrp == parsed cost-for-one.
  This is an honest representative price, documented as such — never
  presented as the exact dish price.
- Cards that are not serviceable for online ordering are skipped.
- ETA comes from order.deliveryTime ("42 min"); fees come from the
  adapter's documented FeeTable defaults (Zomato exposes no per-order fees
  on the public surface).
- City: lat/lng is mapped to the nearest supported Zomato city slug
  (built-in coordinate table, defaults to "ncr"). Query: matched against
  Zomato's canonical dish index (list verified from the live homepage on
  2026-09-28). Queries with no matching dish return [] with a log line —
  honest, never invented results.

The parser is defensive: a missing marker or malformed blob yields [],
never an exception. Transport failures in search() degrade to [] with a
warning (one platform being down must not 500 the whole /v1/compare);
health() reports the real state.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
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

log = logging.getLogger("sauda.real_zomato")

# ---------------------------------------------------------------------------
# Endpoint constants (verified 2026-09-28 — public SSR pages, unsigned)
# ---------------------------------------------------------------------------

BASE_URL = "https://www.zomato.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
SEARCH_MARKER = "SECTION_SEARCH_RESULT"

DEFAULT_LAT = 28.6139   # Delhi — same default as the REST API
DEFAULT_LON = 77.2090

CACHE_TTL_S = 120        # short cache; snapshots carry honest timestamps
HEALTH_TTL_S = 60
CONCURRENCY = 2
MIN_REQUEST_INTERVAL_S = 1.5   # Zomato's WAF dislikes rapid sequential hits
REQUEST_TIMEOUT_S = 20

# City slugs for Zomato delivery (slug, lat, lng). Nearest city wins;
# default "ncr" when nothing is close (all of these are far apart anyway).
CITY_COORDS: tuple[tuple[str, float, float], ...] = (
    ("ncr", 28.6139, 77.2090),
    ("mumbai", 19.0760, 72.8777),
    ("bangalore", 12.9716, 77.5946),
    ("hyderabad", 17.3850, 78.4867),
    ("chennai", 13.0827, 80.2707),
    ("kolkata", 22.5726, 88.3639),
    ("pune", 18.5204, 73.8567),
    ("ahmedabad", 23.0225, 72.5714),
    ("jaipur", 26.9124, 75.7873),
    ("lucknow", 26.8467, 80.9462),
    ("chandigarh", 30.7333, 76.7794),
    ("kochi", 9.9312, 76.2673),
    ("indore", 22.7196, 75.8577),
    ("surat", 21.1702, 72.8311),
    ("nagpur", 21.1458, 79.0882),
    ("bhopal", 23.2599, 77.4126),
    ("patna", 25.5941, 85.1376),
    ("vadodara", 22.3072, 73.1812),
    ("ludhiana", 30.9010, 75.8573),
    ("agra", 27.1767, 78.0081),
    ("coimbatore", 11.0168, 76.9558),
    ("visakhapatnam", 17.6868, 83.2185),
    ("goa", 15.2993, 74.1240),
)

# Canonical Zomato dish slugs — captured from the live delivery homepage on
# 2026-09-28. Zomato only serves dish pages for these slugs.
DISH_SLUGS: tuple[str, ...] = (
    "dish-andhra-meal", "dish-biryani", "dish-buffet-breakfast",
    "dish-buffet-dinner", "dish-burger", "dish-cake", "dish-chicken",
    "dish-chicken-biryani", "dish-chicken-fried-rice", "dish-chicken-shawarma",
    "dish-chilli-chicken", "dish-chocolate-cake", "dish-chole-bhature",
    "dish-chowmein", "dish-coffee", "dish-cold-coffee", "dish-dal-bati",
    "dish-dal-khichdi", "dish-desserts", "dish-dinner", "dish-dosa",
    "dish-egg", "dish-fish", "dish-fish-thali", "dish-fried-rice",
    "dish-gulab-jamun", "dish-hot-chocolate", "dish-ice-cream", "dish-idli",
    "dish-jalebi", "dish-juice", "dish-kadhai-paneer", "dish-lunch-buffet",
    "dish-maggi", "dish-mandi", "dish-masala-dosa", "dish-meals",
    "dish-momos", "dish-mutton", "dish-mutton-biryani",
    "dish-north-indian-meal", "dish-pancake", "dish-paneer", "dish-paratha",
    "dish-pastry", "dish-pav-bhaji", "dish-pizza", "dish-punjabi",
    "dish-rajasthani-thali", "dish-ramen", "dish-rolls", "dish-salad",
    "dish-samosa", "dish-sandwich", "dish-sizzler", "dish-soup",
    "dish-south-indian-meal", "dish-sushi", "dish-sweets", "dish-tea",
    "dish-thali", "dish-vada-pav", "dish-veg-biryani", "dish-veg-thali",
    "dish-waffles",
)
_DISH_SLUG_SET = frozenset(DISH_SLUGS)


# ---------------------------------------------------------------------------
# Location + query mapping
# ---------------------------------------------------------------------------

def nearest_city_slug(lat: float, lng: float) -> str:
    """Map coordinates to the nearest supported Zomato city slug."""
    best, best_d = "ncr", float("inf")
    for slug, clat, clng in CITY_COORDS:
        # equirectangular approximation — fine for nearest-city selection
        d = (lat - clat) ** 2 + (math.cos(math.radians(clat)) * (lng - clng)) ** 2
        if d < best_d:
            best, best_d = slug, d
    return best


# Common query synonyms not literally in Zomato's dish index.
_QUERY_ALIASES: tuple[tuple[str, str], ...] = (
    ("chai", "dish-tea"),
)


def dish_slug_for_query(query: str) -> str | None:
    """Match a free-text query to Zomato's canonical dish slug.

    Exact slug match first ("chicken biryani" -> dish-chicken-biryani),
    then known aliases ("masala chai" -> dish-tea), then longest substring
    match ("paneer tikka" -> dish-paneer). Returns None when the query
    matches nothing — the caller returns [] rather than inventing results.
    """
    norm = re.sub(r"[^a-z0-9]+", "-", (query or "").lower()).strip("-")
    if not norm:
        return None
    if f"dish-{norm}" in _DISH_SLUG_SET:
        return f"dish-{norm}"
    for alias, slug in _QUERY_ALIASES:
        if alias in norm:
            return slug
    best: str | None = None
    for slug in DISH_SLUGS:
        key = slug[5:]
        if key in norm or norm in key:
            if best is None or len(key) > len(best) - 5:
                best = slug
    return best


def dish_display_name(slug: str) -> str:
    return slug[5:].replace("-", " ").title()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_RUPEE_RE = re.compile(r"₹\s?([\d,]+(?:\.\d+)?)")
_ETA_RE = re.compile(r"(\d+)\s*min")


def _price_from_cfo(text: str | None) -> float | None:
    """Parse Zomato 'cost for one' text like '₹250 for one'."""
    if not text:
        return None
    m = _RUPEE_RE.search(text)
    return float(m.group(1).replace(",", "")) if m else None


def _eta_from_delivery_time(text: str | None) -> int | None:
    """Parse Zomato order.deliveryTime like '42 min'."""
    if not text:
        return None
    m = _ETA_RE.search(text)
    return int(m.group(1)) if m else None


def _extract_embedded_array(html: str) -> list:
    """Extract the SECTION_SEARCH_RESULT JSON array from Zomato SSR HTML.

    The blob is embedded as an escaped JSON string; we locate the opening
    '[' after the marker, scan for its match respecting backslash escapes,
    then JSON-decode the string layer and the array layer. Returns [] when
    the marker/blob is missing or malformed — never raises.
    """
    try:
        i = html.find(SEARCH_MARKER)
        if i < 0:
            return []
        j = html.find("[", i)
        if j < 0:
            return []
        # The blob is a JSON string's *content*: every quote is backslash-
        # escaped, so a backslash always neutralises the next char —
        # regardless of whether we are "inside a string".
        depth = 0
        k = j
        in_str = False
        while k < len(html):
            ch = html[k]
            if ch == "\\":
                k += 2
                continue
            if ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch == "[":
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        break
            k += 1
        if depth != 0:
            return []
        raw = html[j:k + 1]
        # raw is a JSON string's *content*: decode the escape layer, then
        # parse the resulting JSON array.
        decoded = json.loads('"' + raw + '"')
        arr = json.loads(decoded)
        return arr if isinstance(arr, list) else []
    except Exception:  # noqa: BLE001 — malformed page => no results, not a crash
        return []


def parse_search_page(html: str | None, dish_label: str) -> list[dict]:
    """Parse a Zomato delivery dish page into raw restaurant dicts.

    Defensive: cards missing a usable price, or not serviceable for online
    ordering, are skipped. Each dict has keys: res_id, name, price_inr,
    delivery_rating, delivery_time_min, locality, order_url.
    """
    if not html:
        return []
    out: list[dict] = []
    for card in _extract_embedded_array(html):
        if not isinstance(card, dict) or card.get("type") != "restaurant":
            continue
        info = card.get("info") or {}
        order = card.get("order") or {}
        if not isinstance(order, dict):
            continue
        if not (order.get("isServiceable") and order.get("hasOnlineOrdering")):
            continue  # can't actually order delivery here -> skip
        res_id = info.get("resId")
        name = info.get("name") or ""
        if not res_id or not name:
            continue
        price = _price_from_cfo((info.get("cfo") or {}).get("text"))
        if price is None:
            continue
        rating = None
        try:
            rating = float((((info.get("ratingNew") or {}).get("ratings")
                             or {}).get("DELIVERY") or {}).get("rating") or 0) or None
        except (TypeError, ValueError):
            rating = None
        out.append({
            "res_id": str(res_id),
            "name": name,
            "price_inr": price,
            "delivery_rating": rating,
            "delivery_time_min": _eta_from_delivery_time(order.get("deliveryTime")),
            "locality": (info.get("locality") or {}).get("name"),
            "order_url": ((order.get("actionInfo") or {}).get("clickUrl")),
            "dish_label": dish_label,
        })
    return out


# ---------------------------------------------------------------------------
# Transports — how the search page is reached
# ---------------------------------------------------------------------------

class ZomatoTransport(ABC):
    """Pluggable transport: given a city slug + dish slug, return page HTML."""

    @abstractmethod
    async def fetch_page(self, city_slug: str, dish_slug: str) -> str:
        ...


class StubTransport(ZomatoTransport):
    """In-memory transport for tests and offline demos."""

    def __init__(self, html: str = "") -> None:
        self.html = html
        self.calls: list[tuple[str, str]] = []

    async def fetch_page(self, city_slug: str, dish_slug: str) -> str:
        self.calls.append((city_slug, dish_slug))
        return self.html


class DirectTransport(ZomatoTransport):
    """Real transport: plain HTTPS GET of Zomato's public SSR dish pages.

    Verified 2026-09-28: unsigned, no cookies/CSRF needed. `import httpx` is
    lazy so importing this module can never break backend boot.
    """

    def __init__(self, timeout_s: int = REQUEST_TIMEOUT_S) -> None:
        self.timeout_s = timeout_s
        self._sem = asyncio.Semaphore(CONCURRENCY)
        self._last_request_at = 0.0

    async def fetch_page(self, city_slug: str, dish_slug: str) -> str:
        import httpx  # lazy: keeps module import side-effect free

        url = f"{BASE_URL}/{quote(city_slug)}/delivery/{quote(dish_slug)}"
        async with self._sem:
            wait = MIN_REQUEST_INTERVAL_S - (time.monotonic() - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with httpx.AsyncClient(timeout=self.timeout_s,
                                             follow_redirects=True) as client:
                    resp = await client.get(url, headers={"User-Agent": UA,
                                                          "Accept": "text/html,*/*"})
            except Exception as exc:
                raise RuntimeError(f"Zomato page request failed: {exc}") from exc
            self._last_request_at = time.monotonic()
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Zomato dish page returned HTTP {resp.status_code}")
            return resp.text


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    product: Product
    snapshot: PriceSnapshot
    stored_at: float


class RealZomatoAdapter(PlatformAdapter):
    """Live Zomato delivery data. platform_id stays "zomato"."""

    platform_id = "zomato"
    display_name = "Zomato (live)"
    vertical = "food"
    uses_static_catalog = False   # prices come from live pages, not catalog.py
    fee_table = FeeTable(
        platform_id="zomato",
        # Documented defaults: Zomato's public surface exposes no per-order
        # fees, so these are adapter defaults, NOT live data.
        min_order_value=149.0,
        free_delivery_above=199.0,
        delivery_fee_base=30.0,
        platform_fee=5.0,
        packaging_fee=4.0,
        gst_rate=0.05,
        eta_min=30,
        eta_max=45,
    )

    def __init__(self, transport: ZomatoTransport | None = None) -> None:
        self.transport = transport or DirectTransport()
        self._cache: dict[str, _CacheEntry] = {}
        self._last_health: tuple[float, AdapterStatus] | None = None

    # -- account linking: not implemented for the real adapter (v1) --------

    async def link_account(self, phone: str) -> str:
        raise NotImplementedError(
            "Real Zomato OTP linking is not implemented yet — it needs "
            "Zomato's headless OTP flow. Search and prices work in guest mode."
        )

    async def verify_otp(self, link_ref: str, otp: str) -> UserSession:
        raise NotImplementedError(
            "Real Zomato OTP verification is not implemented yet."
        )

    # -- catalog -----------------------------------------------------------

    @staticmethod
    def _gtin(res_id: str) -> str:
        # Namespaced: never collides with real GTINs in the static catalog.
        return f"zomato:{res_id}"

    def _to_models(self, raw: dict) -> tuple[Product, PriceSnapshot]:
        price = raw["price_inr"]
        product = Product(
            gtin=self._gtin(raw["res_id"]),
            brand=raw["name"],
            name=f"{raw['dish_label']} @ {raw['name']}",
            pack_size=1.0,
            unit="serving",
            pack_label="cost for one (Zomato)",
            mrp=price,  # Zomato exposes cost-for-one only; mrp == price
        )
        eta = raw["delivery_time_min"]
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
        dish_slug = dish_slug_for_query(query)
        if dish_slug is None:
            log.info("Zomato: query %r matches no canonical dish slug — "
                     "returning no results (not an error)", query)
            return []
        city_slug = nearest_city_slug(lat, lng)
        try:
            html = await self.transport.fetch_page(city_slug, dish_slug)
        except Exception as exc:  # noqa: BLE001 — degrade, don't 500 compare
            log.warning("Zomato search failed for %r (%s/%s): %s",
                        query, city_slug, dish_slug, exc)
            return []
        products: list[Product] = []
        now = time.monotonic()
        for raw in parse_search_page(html, dish_display_name(dish_slug)):
            product, snapshot = self._to_models(raw)
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
            html = await asyncio.wait_for(
                self.transport.fetch_page(nearest_city_slug(DEFAULT_LAT, DEFAULT_LON),
                                          "dish-tea"),
                timeout=25)
            n = len(parse_search_page(html, "Tea"))
            status = AdapterStatus(
                platform_id=self.platform_id, status="live",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=f"Zomato live page OK ({n} restaurants in probe)")
        except Exception as exc:  # noqa: BLE001 — health must never raise
            log.warning("Zomato health probe failed: %s", exc)
            status = AdapterStatus(
                platform_id=self.platform_id, status="down",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=f"Zomato live probe failed: {exc}")
        self._last_health = (now, status)
        return status
