"""Offer rule engine — ARCHITECTURE.md §7.

Offers are plain rule objects:
    {code, type: flat | pct_upto | free_delivery, value, max_discount,
     min_order, first_order_only, payment_methods[], platforms[]}

`evaluate_offer` decides applicability and computes the discount;
`best_offer` picks the highest-value applicable coupon for a basket.
"""
from __future__ import annotations

from models import Offer, OfferResult, UserContext


def evaluate_offer(offer: Offer, subtotal: float, delivery_fee: float,
                   user: UserContext) -> OfferResult:
    """Apply one coupon rule to a basket. Never fails — returns a reason."""
    if subtotal < offer.min_order:
        return OfferResult(False, 0.0,
                           f"needs ₹{offer.min_order:.0f} minimum order")
    if offer.first_order_only and not user.is_first_order:
        return OfferResult(False, 0.0, "first order only")
    if offer.payment_methods and user.payment_method not in offer.payment_methods:
        return OfferResult(False, 0.0,
                           f"needs payment via {', '.join(offer.payment_methods)}")

    if offer.type == "flat":
        discount = min(offer.value, subtotal)
        return OfferResult(True, round(discount, 2), f"flat ₹{offer.value:.0f} off")
    if offer.type == "pct_upto":
        raw = subtotal * offer.value / 100.0
        cap = offer.max_discount if offer.max_discount > 0 else raw
        return OfferResult(True, round(min(raw, cap, subtotal), 2),
                           f"{offer.value:.0f}% off up to ₹{offer.max_discount:.0f}")
    if offer.type == "free_delivery":
        return OfferResult(True, round(delivery_fee, 2), "free delivery")
    return OfferResult(False, 0.0, f"unknown offer type {offer.type!r}")


def best_offer(offers: list[Offer], platform_id: str, subtotal: float,
               delivery_fee: float, user: UserContext) -> tuple[Offer | None, float]:
    """Highest-value applicable coupon for this platform basket.

    Ties break toward the lexicographically smallest code for determinism.
    """
    best: Offer | None = None
    best_discount = 0.0
    for offer in offers:
        if offer.platforms and platform_id not in offer.platforms:
            continue
        result = evaluate_offer(offer, subtotal, delivery_fee, user)
        if not result.applicable or result.discount <= 0:
            continue
        # Higher discount wins; ties break on smallest code (deterministic).
        if (result.discount > best_discount
                or (result.discount == best_discount
                    and (best is None or offer.code < best.code))):
            best, best_discount = offer, result.discount
    return best, round(best_discount, 2)


# ---------------------------------------------------------------------------
# Sample coupons shipped with v1 (2 per platform)
# ---------------------------------------------------------------------------

SAMPLE_OFFERS: list[Offer] = [
    Offer(code="BLINKIT50", type="flat", value=50, min_order=299,
          platforms=("blinkit",)),
    Offer(code="FIRST20", type="pct_upto", value=20, max_discount=100,
          min_order=199, first_order_only=True, platforms=("blinkit",)),
    Offer(code="ZEPTO25", type="pct_upto", value=25, max_discount=75,
          min_order=199, platforms=("zepto",)),
    Offer(code="ZFREEDEL", type="free_delivery", value=0, min_order=149,
          platforms=("zepto",)),
    Offer(code="INSTA60", type="flat", value=60, min_order=349,
          platforms=("instamart",)),
    Offer(code="TRYNEW15", type="pct_upto", value=15, max_discount=120,
          min_order=249, first_order_only=True, platforms=("instamart",)),
]


def offers_for_platform(platform_id: str) -> list[Offer]:
    return [o for o in SAMPLE_OFFERS if not o.platforms or platform_id in o.platforms]
