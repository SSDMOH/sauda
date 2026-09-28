"""Sauda backend API — REST v1 (ARCHITECTURE.md §8).

Default run is fully self-contained: all state is in-memory and all platform
data comes from deterministic mock adapters. Postgres + Redis (see
docker-compose.yml) are the prod shape; the API does not require them.

Run:  uvicorn api:app --reload      (from this directory)
Docs: http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import itertools
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from adapters import all_adapters, any_real_adapters
from adapters.catalog import PRODUCTS_BY_GTIN, platform_price, PRODUCTS
from models import (
    FeeTable,
    PriceUnavailableError,
    UserContext,
    UserSession,
    utcnow,
)
from offers import SAMPLE_OFFERS, best_offer
from optimizer import OptimizerItem, PlatformData, optimize_cart
from pricing import PricedItem, compute_true_total, delivery_fee

app = FastAPI(title="Sauda Backend", version="0.1.0",
              description="Price-comparison + smart split-cart engine (v1, mock data).")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

ADAPTERS = all_adapters()
REAL_MODE = any_real_adapters(ADAPTERS)

# ---------------------------------------------------------------------------
# In-memory v1 stores (Postgres/Redis in prod)
# ---------------------------------------------------------------------------
ALERTS: dict[str, dict] = {}
_ALERT_SEQ = itertools.count(1)
LINKED_ACCOUNTS: dict[str, dict] = {}
_PENDING_LINKS: dict[str, tuple[str, str]] = {}   # link_ref -> (platform_id, phone)

SAVINGS_LEDGER = {
    "lifetime_savings": 1247.0,
    "orders_compared": 38,
    "current_streak_days": 6,
    "week_goal": 300.0,
    "week_saved": 212.0,
    "recent": [
        {"date": "2026-09-27", "platforms": ["zepto", "blinkit"],
         "items": 6, "paid": 598.0, "saved": 86.0},
        {"date": "2026-09-25", "platforms": ["instamart"],
         "items": 4, "paid": 412.0, "saved": 64.0},
        {"date": "2026-09-22", "platforms": ["blinkit", "zepto", "instamart"],
         "items": 9, "paid": 911.0, "saved": 143.0},
    ],
}

GUEST = UserContext(user_id="guest", is_first_order=False, payment_method="upi")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _guest_session(platform_id: str) -> UserSession:
    return UserSession(platform_id=platform_id, phone="guest",
                       token="guest", linked_at=utcnow())


def build_platform_data() -> dict[str, PlatformData]:
    """Optimizer-ready view of every platform: prices, fees, product names.

    Only adapters backed by the static catalog participate — the live Blinkit
    adapter prices real products (namespaced gtins) that don't exist in the
    catalog, so it is excluded from cart-wide optimization in v1.
    """
    data: dict[str, PlatformData] = {}
    for pid, adapter in ADAPTERS.items():
        if not getattr(adapter, "uses_static_catalog", False):
            continue
        prices, products = {}, {}
        for product in PRODUCTS:
            price = platform_price(pid, product)
            if price is not None:
                prices[product.gtin] = price
                products[product.gtin] = product
        fee_table: FeeTable = adapter.fee_table  # type: ignore[attr-defined]
        data[pid] = PlatformData(platform_id=pid,
                                 display_name=adapter.display_name,  # type: ignore[attr-defined]
                                 prices=prices, fee_table=fee_table,
                                 products=products)
    return data


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CartItemIn(BaseModel):
    gtin: str
    qty: int = Field(default=1, ge=1, le=99)


class CompareIn(BaseModel):
    query: str | None = None
    items: list[CartItemIn] | None = None
    lat: float = 28.6139
    lng: float = 77.2090


class OptimizeIn(BaseModel):
    items: list[CartItemIn]
    lat: float = 28.6139
    lng: float = 77.2090
    max_platforms: int = Field(default=3, ge=1, le=8)


class AlertIn(BaseModel):
    gtin: str
    target_price: float = Field(gt=0)
    platform_id: str | None = None


class LinkIn(BaseModel):
    platform_id: str
    phone: str = Field(min_length=10, max_length=15)


class VerifyIn(BaseModel):
    link_ref: str
    otp: str


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return {"service": "sauda-backend", "version": "0.1.0",
            "docs": "/docs", "health": "/v1/health"}


@app.get("/v1/health")
async def health():
    real_labels = [a.display_name  # type: ignore[attr-defined]
                   for a in ADAPTERS.values()
                   if type(a).__module__.startswith("adapters.real_")]
    return {
        "status": "ok",
        "storage": "in-memory (postgres + redis in prod)",
        "network": (("real adapters enabled: " + ", ".join(real_labels))
                    if real_labels else "none — mock adapters only"),
        "adapters": [{**asdict(await a.health()),
                      "display_name": a.display_name}  # type: ignore[attr-defined]
                     for a in ADAPTERS.values()],
    }


# ---------------------------------------------------------------------------
# Compare — per-platform results + best pick
# ---------------------------------------------------------------------------

@app.post("/v1/compare")
async def compare(body: CompareIn):
    if not body.query and not body.items:
        raise HTTPException(400, "Provide `query` or `items`.")
    if body.query and body.items:
        raise HTTPException(400, "Provide either `query` or `items`, not both.")

    results = []
    for pid, adapter in ADAPTERS.items():
        session = _guest_session(pid)
        found: list[tuple] = []  # (Product, PriceSnapshot, qty)
        if body.query:
            try:
                products = await adapter.search(body.query, body.lat, body.lng, session)
            except PriceUnavailableError:
                # Adapter can't answer a bare query (e.g. cabs needs a drop
                # location) — skip it honestly rather than 500ing.
                continue
            for product in products:
                snap = await adapter.get_price(product.gtin, session)
                found.append((product, snap, 1))
        else:
            for item in body.items or []:
                product = PRODUCTS_BY_GTIN.get(item.gtin)
                if product is None:
                    continue
                try:
                    snap = await adapter.get_price(item.gtin, session)
                except PriceUnavailableError:
                    continue  # not stocked here — simply absent from this platform
                found.append((product, snap, item.qty))
        if not found:
            continue

        priced = [PricedItem(gtin=p.gtin,
                             name=f"{p.brand} {p.name} {p.pack_label}",
                             unit_price=s.price, qty=q)
                  for p, s, q in found]
        subtotal = round(sum(p.line_total for p in priced), 2)
        d_fee = delivery_fee(subtotal, 2.0, adapter.fee_table)  # type: ignore[attr-defined]
        coupon, _ = best_offer(SAMPLE_OFFERS, pid, subtotal, d_fee, GUEST)
        bill = compute_true_total(priced, adapter.fee_table,  # type: ignore[attr-defined]
                                  coupon=coupon, user=GUEST)
        results.append({
            "platform_id": pid,
            "display_name": adapter.display_name,  # type: ignore[attr-defined]
            "items": [{
                "gtin": p.gtin, "brand": p.brand, "name": p.name,
                "pack_label": p.pack_label, "pack_size": p.pack_size,
                "unit": p.unit, "mrp": p.mrp, "price": s.price,
                "per_unit": p.per_unit_label(s.price),
                "in_stock": s.in_stock, "eta_minutes": s.eta_minutes,
                "qty": q, "line_total": round(s.price * q, 2),
            } for p, s, q in found],
            "bill": bill,
            "freshness": "live",
        })

    results.sort(key=lambda r: r["bill"]["total"])
    return {
        "results": results,
        "best_pick": results[0]["platform_id"] if results else None,
        "freshness": "live",
    }


# ---------------------------------------------------------------------------
# Cabs — fare estimate comparison (ONDC/Beckn-first)
# ---------------------------------------------------------------------------

class CabsCompareIn(BaseModel):
    pickup_lat: float
    pickup_lng: float
    drop_lat: float
    drop_lng: float
    departure_at: str | None = None   # ISO-8601, defaults to now
    vehicle: str | None = None        # auto | cab | bike | suv


@app.post("/v1/cabs/compare")
async def cabs_compare(body: CabsCompareIn):
    """Compare cab fare ESTIMATES across ONDC network providers.

    Fares are quotes, never locked-in prices — every result is labelled
    `freshness: "est"` with a quote timestamp. Requires SAUDA_REAL_CABS=1;
    without BAP credentials the adapter reports down and this returns 503.
    """
    adapter = ADAPTERS.get("ondc_cabs")
    if adapter is None:
        raise HTTPException(404, "Cabs adapter not enabled (SAUDA_REAL_CABS=1)")
    session = _guest_session("ondc_cabs")
    try:
        products = await adapter.search(
            body.vehicle or "",
            body.pickup_lat, body.pickup_lng, session,
            pickup_lat=body.pickup_lat, pickup_lng=body.pickup_lng,
            drop_lat=body.drop_lat, drop_lng=body.drop_lng,
            departure_at=body.departure_at, vehicle=body.vehicle,
        )
    except PriceUnavailableError as e:
        raise HTTPException(503, f"Cab quotes unavailable: {e}")
    except Exception as e:  # transport / credentials failures degrade to 503, never 500
        raise HTTPException(503, f"Cab quotes unavailable: {e}")
    results = []
    for p in products:
        try:
            snap = await adapter.get_price(p.gtin, session)
        except PriceUnavailableError:
            continue
        results.append({
            "gtin": p.gtin,
            "provider": p.brand,
            "vehicle": p.name,
            "pack_label": p.pack_label,
            "price": snap.price,          # bottom of the estimate range
            "mrp": p.mrp,                 # top of the estimate range
            "eta_minutes": snap.eta_minutes,
            "freshness": snap.freshness,  # always "est"
            "quoted_at": snap.captured_at,
        })
    results.sort(key=lambda r: r["price"])
    return {
        "results": results,
        "best_pick": results[0]["provider"] if results else None,
        "freshness": "est",
        "note": "Estimates only — final fare is set at booking and can change.",
    }


# ---------------------------------------------------------------------------
# Optimize — smart split cart
# ---------------------------------------------------------------------------

@app.post("/v1/optimize-cart")
async def optimize_cart_ep(body: OptimizeIn):
    unknown = [i.gtin for i in body.items if i.gtin not in PRODUCTS_BY_GTIN]
    if unknown:
        raise HTTPException(400, f"Unknown gtin(s): {', '.join(unknown)}")
    plan = optimize_cart(
        [OptimizerItem(gtin=i.gtin, qty=i.qty) for i in body.items],
        build_platform_data(),
        max_platforms=body.max_platforms,
        offers=SAMPLE_OFFERS,
        user=GUEST,
    )
    return plan.to_dict()


# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------

@app.get("/v1/offers")
async def list_offers(platform: str | None = None):
    offers = [o for o in SAMPLE_OFFERS
              if not platform or not o.platforms or platform in o.platforms]
    return {"offers": [asdict(o) for o in offers]}


# ---------------------------------------------------------------------------
# Alerts (in-memory, v1)
# ---------------------------------------------------------------------------

@app.post("/v1/alerts", status_code=201)
async def create_alert(body: AlertIn):
    if body.gtin not in PRODUCTS_BY_GTIN:
        raise HTTPException(400, "Unknown gtin")
    if body.platform_id and body.platform_id not in ADAPTERS:
        raise HTTPException(400, "Unknown platform")
    alert_id = f"al_{next(_ALERT_SEQ)}"
    product = PRODUCTS_BY_GTIN[body.gtin]
    ALERTS[alert_id] = {
        "alert_id": alert_id,
        "gtin": body.gtin,
        "product": f"{product.brand} {product.name} {product.pack_label}",
        "target_price": body.target_price,
        "platform_id": body.platform_id,
        "created_at": utcnow().isoformat(),
    }
    return ALERTS[alert_id]


@app.delete("/v1/alerts/{alert_id}")
async def delete_alert(alert_id: str):
    if alert_id not in ALERTS:
        raise HTTPException(404, "Alert not found")
    del ALERTS[alert_id]
    return {"deleted": True, "alert_id": alert_id}


# ---------------------------------------------------------------------------
# Savings ledger (seeded demo data, v1)
# ---------------------------------------------------------------------------

@app.get("/v1/savings")
async def savings():
    return SAVINGS_LEDGER


# ---------------------------------------------------------------------------
# Accounts — OTP linking (MOCK: any 6-digit OTP is accepted)
# ---------------------------------------------------------------------------

@app.post("/v1/accounts/link")
async def link_account(body: LinkIn):
    adapter = ADAPTERS.get(body.platform_id)
    if adapter is None:
        raise HTTPException(404, f"Unknown platform {body.platform_id!r}")
    try:
        link_ref = await adapter.link_account(body.phone)
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc))
    _PENDING_LINKS[link_ref] = (body.platform_id, body.phone)
    return {
        "link_ref": link_ref,
        "platform_id": body.platform_id,
        "expires_in_seconds": 300,
        "message": "Mock OTP sent. Any 6-digit code is accepted (mock documented).",
    }


@app.post("/v1/accounts/verify")
async def verify_account(body: VerifyIn):
    pending = _PENDING_LINKS.get(body.link_ref)
    if pending is None:
        raise HTTPException(400, "Unknown or expired link_ref")
    platform_id, _ = pending
    try:
        session = await ADAPTERS[platform_id].verify_otp(body.link_ref, body.otp)
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    del _PENDING_LINKS[body.link_ref]
    LINKED_ACCOUNTS[platform_id] = {
        "platform_id": platform_id,
        "phone": session.phone,
        "token": session.token,
        "linked_at": session.linked_at.isoformat(),
    }
    return LINKED_ACCOUNTS[platform_id]


@app.delete("/v1/accounts/{platform_id}")
async def unlink_account(platform_id: str):
    LINKED_ACCOUNTS.pop(platform_id, None)
    # drop any pending links for this platform too
    for ref, (pid, _) in list(_PENDING_LINKS.items()):
        if pid == platform_id:
            del _PENDING_LINKS[ref]
    return {"unlinked": True, "platform_id": platform_id}
