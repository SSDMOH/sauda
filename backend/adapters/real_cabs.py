"""Real cabs adapter — fare ESTIMATES via the ONDC/Beckn mobility network.

STATUS (verified 2026-09-28): **BLOCKED for live data.** This module ships as
a correct, fully tested Beckn BAP client (search-intent builder, on_search
quote parser, HTTP-signing helpers, provider registry) — not as a working
quote source. Nothing here fabricates fares: without BAP credentials the
adapter reports "down" and search() raises a clear error saying exactly what
is missing.

Why no live quotes (research notes, 2026-09-28):
- The Beckn protocol is asynchronous and trust-gated. A BAP posts `search`
  to a gateway and gets back only an ACK; fare quotes arrive later as
  `on_search` callbacks to the BAP's own public callback URL. Participating
  therefore requires ALL of:
    1. ONDC registry onboarding as a BAP (subscriber_id + Ed25519 key pair;
       production onboarding needs GSTIN etc. via the ONDC participant
       portal; the Beckn sandbox at registry.becknprotocol.io also requires
       registration before a subscription is issued).
    2. Signing every request with the registered private key.
    3. A publicly reachable callback endpoint (`<bap_uri>/on_search`) to
       receive quotes — a server-side backend like Sauda's can host this,
       but it does not exist yet.
- There is NO guest-accessible quote flow: Namma Yatri's rider UI endpoints
  (POST /rideSearch, GET /rideSearch/{id}/results) need a rider OTP session;
  the Beckn reference BAP/BPP (experienceguide.becknprotocol.io,
  driverinfra1.becknprotocol.io) are interactive web demos, not APIs.
- Ola and Uber have no open unsigned fare APIs (Uber retired its public
  v1.2 estimates API in 2018; Ola never had one). Their web/app endpoints
  need an authenticated user session. Documented honestly rather than faked.
- From this workspace, even the Beckn sandbox infra is unreachable
  (gateway.becknprotocol.io and registry.becknprotocol.io return empty TCP
  replies; general egress to e.g. ondc.org, api.github.com works fine), so
  live verification was not possible here.

What would unblock live quotes:
  a) Complete ONDC BAP onboarding -> subscriber_id (bap_id) + signing keys.
  b) Expose a public callback URL and implement /on_search (correlate
     transaction_id -> estimates); then HttpGatewayTransport.quote_raw can
     collect quotes with a bounded wait instead of raising.
  c) Set SAUDA_ONDC_BAP_ID / SAUDA_ONDC_BAP_URI / SAUDA_ONDC_GATEWAY_URL /
     SAUDA_ONDC_KEY_ID (+ a signing key injected into
     build_http_signature_headers' caller).

Why platform_id is "ondc_cabs": this adapter is a BAP (buyer-side app) on
the ONDC network, not one cab brand. Each quote carries its provider
(Namma Yatri today; more mobility BPPs as they join), so the platform_id
keys the *network interface* and per-quote provider info is surfaced in the
product brand/name.

Cab search convention (extends PlatformAdapter.search via kwargs):
    search(query, lat, lng, session,
           *, pickup_lat=None, pickup_lng=None,
           drop_lat=None, drop_lng=None,
           departure_at=None, vehicle=None)
- pickup defaults to (lat, lng); drop is REQUIRED (a fare quote is
  meaningless without a destination) — PriceUnavailableError otherwise.
- departure_at: None (now) | datetime | epoch seconds | ISO-8601 string.
- vehicle: optional hint among {"auto","cab","bike","suv"}; BPPs may still
  return several categories — the adapter surfaces all of them.
- `query` is ignored for routing but may carry a vehicle hint ("auto").

API-layer proposal (honest, not yet wired): /v1/compare has no
pickup/drop fields, so cabs need a cabs-aware endpoint, e.g.
    POST /v1/cabs/compare
      {pickup_lat, pickup_lng, drop_lat, drop_lng,
       departure_at?, vehicle?, max_results?}
which calls:
    adapter.search("", body.pickup_lat, body.pickup_lng, session,
                   pickup_lat=..., pickup_lng=...,
                   drop_lat=..., drop_lng=...,
                   departure_at=..., vehicle=...)
NOTE: api.py is untouched in this change; the coordinator wires registration.

Estimate labeling: every snapshot has freshness="est" (never "live"),
captured_at = quote time, and the product pack_label carries
"est. · quoted <HH:MM IST> · valid 60 s". Cab fares also change at booking
time (driver acceptance, route, surge), so results are estimates by design.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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

log = logging.getLogger("sauda.real_cabs")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FLAG_ENV = "SAUDA_REAL_CABS"          # coordinator checks this before wiring

# ONDC domain code for mobility, per the ONDC network API specifications.
ONDC_MOBILITY_DOMAIN = "nic2004:60221"
BECKN_PROTOCOL_VERSION = "2.0.0"      # negotiated at onboarding; may be 1.1.0
DEFAULT_CITY_CODE = "std:080"         # ONDC city code; Bengaluru default

QUOTE_TTL_S = 60          # fare estimates go stale fast; snapshots say so
HEALTH_TTL_S = 60
CONCURRENCY = 1           # Beckn search is async; one inflight intent at a time

VEHICLE_CATEGORY_IDS = {
    "auto": "Auto",
    "cab": "Cab",
    "bike": "Bike",
    "suv": "SUV",
}
_IST = timezone(timedelta(hours=5, minutes=30))


def real_cabs_enabled() -> bool:
    """Env-gate check for the coordinator: SAUDA_REAL_CABS=1."""
    return os.environ.get(FLAG_ENV) == "1"


# ---------------------------------------------------------------------------
# BAP credentials (all-or-nothing; nothing secret is logged or stored)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BAPConfig:
    bap_id: str        # subscriber_id issued by the ONDC registry
    bap_uri: str       # public callback base URL, e.g. https://api.sauda.in/bap
    gateway_url: str   # Beckn gateway /search endpoint
    key_id: str        # keyId registered alongside the Ed25519 public key
    city: str = DEFAULT_CITY_CODE


class BAPCredentialsError(RuntimeError):
    """Raised when the adapter is asked for live quotes without BAP creds."""


def bap_config_from_env(env: dict | None = None) -> BAPConfig | None:
    """Read BAP credentials from the environment.

    Returns None unless SAUDA_ONDC_BAP_ID, SAUDA_ONDC_BAP_URI,
    SAUDA_ONDC_GATEWAY_URL and SAUDA_ONDC_KEY_ID are ALL set — partial
    credentials are treated as missing (never used).
    """
    env = env if env is not None else os.environ
    bap_id = env.get("SAUDA_ONDC_BAP_ID")
    bap_uri = env.get("SAUDA_ONDC_BAP_URI")
    gateway_url = env.get("SAUDA_ONDC_GATEWAY_URL")
    key_id = env.get("SAUDA_ONDC_KEY_ID")
    if not all([bap_id, bap_uri, gateway_url, key_id]):
        return None
    city = env.get("SAUDA_ONDC_CITY", DEFAULT_CITY_CODE)
    return BAPConfig(bap_id=bap_id, bap_uri=bap_uri,
                     gateway_url=gateway_url, key_id=key_id, city=city)


def unblock_instructions() -> str:
    return (
        "Live ONDC cab quotes are unavailable: no BAP credentials configured. "
        "To unblock: (1) complete ONDC BAP onboarding to get a subscriber_id "
        "and signing keys; (2) host a public /on_search callback and implement "
        "quote collection; (3) set SAUDA_ONDC_BAP_ID, SAUDA_ONDC_BAP_URI, "
        "SAUDA_ONDC_GATEWAY_URL and SAUDA_ONDC_KEY_ID. "
        "See adapters/real_cabs.py module docstring."
    )


# ---------------------------------------------------------------------------
# Provider registry — ONDC mobility BPPs (sellers)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CabProvider:
    """A known ONDC mobility seller (BPP).

    bpp_id/bpp_uri are deliberately NOT hardcoded: the exact Beckn
    subscriber_id and endpoint of each seller come from the ONDC registry
    /lookup at onboarding time. Hardcoding them would be inventing network
    addresses. Register registry-resolved entries via register_provider().
    """
    provider_id: str      # stable internal key, e.g. "namma-yatri"
    display_name: str     # shown to the user
    note: str = ""


_KNOWN_PROVIDERS: dict[str, CabProvider] = {}


def register_provider(provider: CabProvider) -> CabProvider:
    if provider.provider_id in _KNOWN_PROVIDERS:
        raise ValueError(f"provider {provider.provider_id!r} already registered")
    _KNOWN_PROVIDERS[provider.provider_id] = provider
    return provider


def list_providers() -> list[CabProvider]:
    return list(_KNOWN_PROVIDERS.values())


# Seed: participants publicly known to sell mobility on ONDC. Their Beckn
# subscriber ids/URIs are resolved via registry lookup, never guessed.
register_provider(CabProvider(
    provider_id="namma-yatri",
    display_name="Namma Yatri",
    note=("ONDC's flagship mobility seller (autos/cabs; zero-commission, "
          "dynamic-offer model: on_search returns fare ESTIMATES, driver "
          "quotes arrive after select/on_select). Beckn subscriber_id and "
          "bpp_uri must be resolved via the ONDC registry /lookup."),
))


# ---------------------------------------------------------------------------
# Beckn message builders (spec-shaped; see module docstring for sources)
# ---------------------------------------------------------------------------

def normalize_departure(value: None | datetime | int | float | str) -> datetime:
    """Normalize departure_at to a tz-aware UTC datetime.

    Accepts None (now), datetime, epoch seconds, or ISO-8601 string.
    Raises ValueError on anything else — never a silent wrong time.
    """
    if value is None:
        return utcnow()
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"departure_at {value!r} is not ISO-8601") from exc
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    raise ValueError(f"departure_at must be datetime | epoch | ISO-8601 string, "
                     f"got {type(value).__name__}")


def build_mobility_intent(pickup_lat: float, pickup_lng: float,
                          drop_lat: float, drop_lng: float,
                          *, vehicle: str | None = None,
                          departure_at: datetime | None = None) -> dict:
    """Build the Beckn `search` message.intent for a mobility fare request.

    GPS is the Beckn "lat,lng" string; the vehicle category is a hint —
    sellers may still return multiple categories.
    """
    intent: dict = {
        "fulfillment": {
            "start": {"location": {"gps": f"{pickup_lat},{pickup_lng}"}},
            "end": {"location": {"gps": f"{drop_lat},{drop_lng}"}},
        }
    }
    if vehicle:
        key = vehicle.strip().lower()
        if key not in VEHICLE_CATEGORY_IDS:
            raise ValueError(f"unknown vehicle {vehicle!r}; "
                             f"expected one of {sorted(VEHICLE_CATEGORY_IDS)}")
        intent["category"] = {"id": VEHICLE_CATEGORY_IDS[key]}
    if departure_at is not None:
        intent["fulfillment"]["start"]["time"] = {
            "timestamp": departure_at.astimezone(timezone.utc).isoformat()
        }
    return intent


def build_search_envelope(config: BAPConfig, intent: dict,
                          *, transaction_id: str | None = None,
                          message_id: str | None = None,
                          ttl: str = "PT60S") -> dict:
    """Wrap an intent in a full Beckn request body (context + message)."""
    context = {
        "domain": ONDC_MOBILITY_DOMAIN,
        "country": "IND",
        "city": config.city,
        "action": "search",
        "version": BECKN_PROTOCOL_VERSION,
        "bap_id": config.bap_id,
        "bap_uri": config.bap_uri,
        "transaction_id": transaction_id or uuid.uuid4().hex,
        "message_id": message_id or uuid.uuid4().hex,
        "timestamp": utcnow().isoformat(),
        "ttl": ttl,
    }
    return {"context": context, "message": {"intent": intent}}


def build_http_signature_headers(*, key_id: str, signature: bytes,
                                 created: int | None = None,
                                 expires: int | None = None) -> dict:
    """Build the Beckn `Authorization` header for a signed request.

    The `signature` bytes are produced by the caller from their registered
    Ed25519 private key over the standard Beckn signing string
    ("(created): <ts>\\n(expires): <ts>\\n(digest): <body-digest>").
    This helper only formats the header — it never touches key material.
    """
    now = int(time.time())
    created = now if created is None else created
    expires = (now + 300) if expires is None else expires
    sig_b64 = base64.b64encode(signature).decode("ascii")
    return {
        "Authorization": (
            f'Signature keyId="{key_id}",algorithm="ed25519",'
            f'created="{created}",expires="{expires}",'
            f'headers="(created) (expires) digest",signature="{sig_b64}"'
        ),
        "Content-Digest": "sha-512=:UNSIGNED:=",  # placeholder; caller sets real digest
    }


# ---------------------------------------------------------------------------
# on_search parsing — defensive, never fatal on unknown shapes
# ---------------------------------------------------------------------------

def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def parse_on_search(payload: dict | None) -> list[dict]:
    """Parse a Beckn `on_search` body into raw fare-quote dicts.

    Walks message.catalog.providers[].items[]; each item contributes:
      provider_id, provider_name, vehicle, price (min..max), currency,
      distance_km, duration_min, eta_min, quote_id.
    Namma Yatri-style dynamic-offer sellers return ESTIMATE ranges here —
    actual driver quotes only arrive after select/on_select, so min/max are
    preserved, never collapsed silently.
    """
    if not isinstance(payload, dict):
        return []
    catalog = payload.get("message", {}).get("catalog") if isinstance(
        payload.get("message"), dict) else None
    providers = catalog.get("providers") if isinstance(catalog, dict) else None
    if not isinstance(providers, list):
        return []
    out: list[dict] = []
    for prov in providers:
        if not isinstance(prov, dict):
            continue
        prov_id = str(prov.get("id") or "")
        prov_name = ((prov.get("descriptor") or {}).get("name")
                     if isinstance(prov.get("descriptor"), dict) else "") or prov_id
        fulfillments: dict[str, dict] = {}
        for f in prov.get("fulfillments") or []:
            if isinstance(f, dict) and f.get("id"):
                fulfillments[str(f["id"])] = f
        items = prov.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            price = item.get("price") if isinstance(item.get("price"), dict) else {}
            currency = str(price.get("currency") or "INR")
            value = _as_float(price.get("value"))
            pmin = _as_float(price.get("minimum_value"))
            pmax = _as_float(price.get("maximum_value"))
            lo = pmin if pmin is not None else value
            hi = pmax if pmax is not None else value
            if lo is None or hi is None:
                continue  # a quote without a number is not a quote
            descriptor = item.get("descriptor") if isinstance(
                item.get("descriptor"), dict) else {}
            vehicle = str(descriptor.get("name") or item.get("category_id") or "ride")
            # join fulfillment info (ETA / distance live on fulfillments)
            eta = dist = dur = None
            for fid in item.get("fulfillment_ids") or []:
                f = fulfillments.get(str(fid), {})
                vehicle_cat = ((f.get("vehicle") or {}).get("category")
                               if isinstance(f.get("vehicle"), dict) else None)
                if vehicle_cat:
                    vehicle = str(vehicle_cat)
                tags = f.get("tags") if isinstance(f.get("tags"), dict) else {}
                eta = eta if eta is not None else _as_float(tags.get("eta_minutes"))
                dist = dist if dist is not None else _as_float(tags.get("distance_km"))
                dur = dur if dur is not None else _as_float(tags.get("duration_min"))
            out.append({
                "provider_id": prov_id,
                "provider_name": str(prov_name),
                "vehicle": vehicle,
                "price_min": round(lo, 2),
                "price_max": round(hi, 2),
                "currency": currency,
                "distance_km": dist,
                "duration_min": dur,
                "eta_min": int(eta) if eta is not None else None,
                "quote_id": str(item.get("id") or ""),
            })
    return out


# ---------------------------------------------------------------------------
# Transports — how a fare request reaches the network
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CabQuoteRequest:
    pickup_lat: float
    pickup_lng: float
    drop_lat: float
    drop_lng: float
    departure_at: datetime
    vehicle: str | None = None


class BecknTransport(ABC):
    """Pluggable transport: given a quote request, return a raw on_search."""

    @abstractmethod
    async def quote_raw(self, request: CabQuoteRequest) -> dict:
        ...


class StubTransport(BecknTransport):
    """In-memory transport for tests and offline demos."""

    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {"message": {"catalog": {"providers": []}}}
        self.calls: list[CabQuoteRequest] = []

    async def quote_raw(self, request: CabQuoteRequest) -> dict:
        self.calls.append(request)
        return self.payload


class UnconfiguredTransport(BecknTransport):
    """Raised whenever live quotes are requested without BAP credentials."""

    async def quote_raw(self, request: CabQuoteRequest) -> dict:
        raise BAPCredentialsError(unblock_instructions())


class HttpGatewayTransport(BecknTransport):
    """Posts a signed Beckn `search` to the gateway — PARTIAL (2026-09-28).

    Beckn `search` is asynchronous: the POST returns only an ACK and fare
    estimates arrive later on the BAP's /on_search callback URL. Collecting
    them needs that callback endpoint + a correlator (see module docstring),
    which does not exist yet — so quote_raw() raises a precise error instead
    of pretending. post_search() is provided for the day the callback exists.
    """

    def __init__(self, config: BAPConfig, wait_s: float = 20.0) -> None:
        self.config = config
        self.wait_s = wait_s
        self._sem = asyncio.Semaphore(CONCURRENCY)

    async def post_search(self, request: CabQuoteRequest,
                          sign: "callable[[bytes], bytes] | None" = None) -> dict:
        """POST one signed search; returns the gateway ACK (not quotes)."""
        import json as _json
        import urllib.request as _url

        intent = build_mobility_intent(
            request.pickup_lat, request.pickup_lng,
            request.drop_lat, request.drop_lng,
            vehicle=request.vehicle, departure_at=request.departure_at)
        envelope = build_search_envelope(self.config, intent)
        body = _json.dumps(envelope).encode()
        headers = {"Content-Type": "application/json"}
        if sign is not None:
            headers.update(build_http_signature_headers(
                key_id=self.config.key_id, signature=sign(body)))

        def _post() -> dict:
            req = _url.Request(self.config.gateway_url + "/search",
                               data=body, headers=headers, method="POST")
            with _url.urlopen(req, timeout=20) as resp:  # noqa: S310 — operator-configured URL
                return {"status": resp.status,
                        "body": _json.loads(resp.read().decode())}

        async with self._sem:
            return await asyncio.to_thread(_post)

    async def quote_raw(self, request: CabQuoteRequest) -> dict:
        raise RuntimeError(
            "Cannot collect fare quotes yet: Beckn search is asynchronous — "
            "the gateway returns an ACK and estimates arrive on the BAP's "
            f"/on_search callback ({self.config.bap_uri}/on_search), which is "
            "not implemented. Implement the callback + transaction correlator "
            "to finish this transport. See adapters/real_cabs.py docstring."
        )


def default_transport() -> BecknTransport:
    """Transport selection: configured gateway client, else honest error."""
    config = bap_config_from_env()
    if config is not None:
        return HttpGatewayTransport(config)
    return UnconfiguredTransport()


# ---------------------------------------------------------------------------
# The adapter
# ---------------------------------------------------------------------------

@dataclass
class _CacheEntry:
    product: Product
    snapshot: PriceSnapshot
    stored_at: float


class RealCabsAdapter(PlatformAdapter):
    """ONDC-network cab fare estimates. platform_id is "ondc_cabs".

    Quotes are always labeled estimates (freshness="est", quoted timestamp
    and 60 s TTL in the display label) — never locked-in fares.
    """

    platform_id = "ondc_cabs"
    display_name = "ONDC Cabs (estimates)"
    vertical = "cabs"
    uses_static_catalog = False   # quotes come from the network, not catalog.py
    # Fares are all-in per seller; Sauda adds no delivery/platform fees.
    fee_table = FeeTable(
        platform_id="ondc_cabs",
        min_order_value=0.0,
        free_delivery_above=0.0,
        delivery_fee_base=0.0,
        platform_fee=0.0,
        packaging_fee=0.0,
        gst_rate=0.0,
        eta_min=3,
        eta_max=12,
    )

    def __init__(self, transport: BecknTransport | None = None) -> None:
        self.transport = transport or default_transport()
        self._cache: dict[str, _CacheEntry] = {}
        self._last_health: tuple[float, AdapterStatus] | None = None

    # -- account linking: not applicable (guest/no-login; BAP has no rider) --

    async def link_account(self, phone: str) -> str:
        raise NotImplementedError(
            "ONDC cabs need no account: the adapter is a BAP that fetches "
            "fare estimates. No OTPs or passwords are ever handled."
        )

    async def verify_otp(self, link_ref: str, otp: str) -> UserSession:
        raise NotImplementedError(
            "ONDC cabs need no account verification."
        )

    # -- quote flow ---------------------------------------------------------

    @staticmethod
    def _route_key(pickup_lat: float, pickup_lng: float,
                   drop_lat: float, drop_lng: float) -> str:
        return (f"{pickup_lat:.4f},{pickup_lng:.4f}-"
                f"{drop_lat:.4f},{drop_lng:.4f}")

    def _gtin(self, provider_id: str, vehicle: str, route_key: str) -> str:
        safe_vehicle = "".join(
            c.lower() if c.isalnum() else "_" for c in vehicle)[:24]
        return f"ondc_cabs:{provider_id or 'unknown'}:{safe_vehicle}:{route_key}"

    def _to_models(self, raw: dict, route_key: str,
                   quoted_at: datetime) -> tuple[Product, PriceSnapshot]:
        provider = next(
            (p for p in list_providers() if p.provider_id == raw["provider_id"]),
            None)
        brand = (provider.display_name if provider
                 else raw["provider_name"] or "ONDC seller") + " (ONDC)"
        dist = (f" · {raw['distance_km']:.1f} km"
                if raw.get("distance_km") else "")
        quoted_ist = quoted_at.astimezone(_IST).strftime("%H:%M")
        product = Product(
            gtin=self._gtin(raw["provider_id"], raw["vehicle"], route_key),
            brand=brand,
            name=f"{raw['vehicle']} ride{dist}",
            pack_size=1.0,
            unit="pcs",
            pack_label=(f"1 ride · est. · quoted {quoted_ist} IST · "
                        f"valid {QUOTE_TTL_S} s"),
            mrp=raw["price_max"],
        )
        snapshot = PriceSnapshot(
            platform_id=self.platform_id,
            gtin=product.gtin,
            price=raw["price_min"],
            mrp=product.mrp,
            per_unit_price=raw["price_min"],   # per ride, not per km
            in_stock=True,
            eta_minutes=(raw["eta_min"] if raw["eta_min"] is not None
                         else self.fee_table.eta_mid),
            freshness="est",                   # ALWAYS estimates, never "live"
            captured_at=quoted_at,
        )
        return product, snapshot

    async def search(self, query: str, lat: float, lng: float,
                     session: UserSession, **kwargs) -> list[Product]:
        """Fare estimates for a route.

        kwargs: pickup_lat, pickup_lng (default: lat/lng), drop_lat, drop_lng
        (REQUIRED), departure_at (None|datetime|epoch|ISO-8601), vehicle
        (optional hint: auto|cab|bike|suv — `query` may also carry the hint).
        """
        pickup_lat = float(kwargs.get("pickup_lat", lat))
        pickup_lng = float(kwargs.get("pickup_lng", lng))
        drop_lat = kwargs.get("drop_lat")
        drop_lng = kwargs.get("drop_lng")
        if drop_lat is None or drop_lng is None:
            raise PriceUnavailableError(
                "Cab fare quotes need a destination: call search() with "
                "drop_lat= and drop_lng= kwargs (pickup defaults to lat/lng, "
                "departure_at defaults to now).")
        departure_at = normalize_departure(kwargs.get("departure_at"))
        vehicle = kwargs.get("vehicle")
        if vehicle is None and (query or "").strip().lower() in VEHICLE_CATEGORY_IDS:
            vehicle = query.strip().lower()
        request = CabQuoteRequest(
            pickup_lat=pickup_lat, pickup_lng=pickup_lng,
            drop_lat=float(drop_lat), drop_lng=float(drop_lng),
            departure_at=departure_at, vehicle=vehicle)
        payload = await self.transport.quote_raw(request)
        quoted_at = utcnow()
        route_key = self._route_key(pickup_lat, pickup_lng,
                                    float(drop_lat), float(drop_lng))
        products: list[Product] = []
        now = time.monotonic()
        for raw in parse_on_search(payload):
            product, snapshot = self._to_models(raw, route_key, quoted_at)
            self._cache[product.gtin] = _CacheEntry(product, snapshot, now)
            products.append(product)
        products.sort(key=lambda p: self._cache[p.gtin].snapshot.price)
        return products

    async def get_price(self, product_id: str,
                        session: UserSession) -> PriceSnapshot:
        entry = self._cache.get(product_id)
        if entry is None or time.monotonic() - entry.stored_at > QUOTE_TTL_S:
            raise PriceUnavailableError(
                f"No fresh estimate for {product_id!r} — call search() first "
                f"(cab estimates are cached for {QUOTE_TTL_S}s and always "
                f"labelled as estimates).")
        return entry.snapshot

    async def health(self) -> AdapterStatus:
        now = time.monotonic()
        if self._last_health and now - self._last_health[0] < HEALTH_TTL_S:
            return self._last_health[1]
        started = time.monotonic()
        transport = self.transport
        if isinstance(transport, UnconfiguredTransport):
            status = AdapterStatus(
                platform_id=self.platform_id, status="down",
                latency_ms=int((time.monotonic() - started) * 1000),
                message="ONDC BAP not configured: " + unblock_instructions())
        elif isinstance(transport, StubTransport):
            status = AdapterStatus(
                platform_id=self.platform_id, status="degraded",
                latency_ms=int((time.monotonic() - started) * 1000),
                message="Stub transport (tests/offline only) — no live quotes.")
        else:
            # Configured gateway transport: the async-callback gap means we
            # cannot complete a quote round-trip yet. Probe-free by design:
            # a health check must not fire real fare-intent traffic.
            status = AdapterStatus(
                platform_id=self.platform_id, status="degraded",
                latency_ms=int((time.monotonic() - started) * 1000),
                message=("BAP credentials present but /on_search callback "
                         "collection is not implemented — no live quotes yet."))
        self._last_health = (now, status)
        return status
