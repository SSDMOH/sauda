"""True-total pricing engine — ARCHITECTURE.md §6.

true_total = Σ item_price × qty
           + delivery_fee(subtotal, distance, surge)
           + platform_fee + packaging_fee
           + GST
           − coupon_discount

Every comparison returns the itemized breakdown so the client can render the
expandable bill ("verifiable math, not trust us" — PRD §5.2).

GST convention (v1): applied on (items subtotal + packaging fee) at the
platform's gst_rate. Delivery/platform fees are treated as services outside
the goods GST base in this simplified model.
"""
from __future__ import annotations

from dataclasses import dataclass

from models import FeeTable, Offer, UserContext


@dataclass(frozen=True)
class PricedItem:
    gtin: str
    name: str
    unit_price: float
    qty: int

    @property
    def line_total(self) -> float:
        return round(self.unit_price * self.qty, 2)


def delivery_fee(subtotal: float, distance_km: float, fees: FeeTable,
                 surge_override: bool | None = None) -> float:
    """Delivery fee for one platform order.

    Free when the items subtotal clears the platform's free-delivery bar;
    otherwise the base fee, multiplied during surge, plus a small per-km
    surcharge beyond 3 km.
    """
    if subtotal >= fees.free_delivery_above:
        return 0.0
    surge = fees.surge_active if surge_override is None else surge_override
    fee = fees.delivery_fee_base * (fees.surge_delivery_multiplier if surge else 1.0)
    fee += fees.delivery_fee_per_km_beyond_3km * max(0.0, distance_km - 3.0)
    return round(fee, 2)


def compute_true_total(items: list[PricedItem],
                       fees: FeeTable,
                       distance_km: float = 2.0,
                       coupon: Offer | None = None,
                       user: UserContext | None = None) -> dict:
    """Itemized true total for a single-platform basket.

    Returns a dict with every line of the bill plus the final total:
    items_subtotal, delivery_fee, platform_fee, packaging_fee, gst,
    discount, coupon_code, total, and per-line detail.
    """
    from offers import evaluate_offer  # deferred: offers imports models only

    items_subtotal = round(sum(i.line_total for i in items), 2)
    d_fee = delivery_fee(items_subtotal, distance_km, fees)
    gst = round((items_subtotal + fees.packaging_fee) * fees.gst_rate, 2)

    discount = 0.0
    coupon_code: str | None = None
    if coupon is not None:
        result = evaluate_offer(coupon, items_subtotal, d_fee,
                                user or UserContext())
        if result.applicable:
            discount = result.discount
            coupon_code = coupon.code

    total = round(items_subtotal + d_fee + fees.platform_fee
                  + fees.packaging_fee + gst - discount, 2)
    return {
        "items_subtotal": items_subtotal,
        "delivery_fee": d_fee,
        "platform_fee": round(fees.platform_fee, 2),
        "packaging_fee": round(fees.packaging_fee, 2),
        "gst": gst,
        "discount": discount,
        "coupon_code": coupon_code,
        "total": total,
        "lines": [
            {"gtin": i.gtin, "name": i.name, "unit_price": i.unit_price,
             "qty": i.qty, "line_total": i.line_total}
            for i in items
        ],
    }
