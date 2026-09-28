"""Tests for the true-total pricing engine (hand-verified arithmetic)."""
import pytest

from models import FeeTable, Offer, UserContext
from pricing import PricedItem, compute_true_total, delivery_fee

FEES = FeeTable(
    platform_id="t",
    min_order_value=0.0,
    free_delivery_above=200.0,
    delivery_fee_base=25.0,
    platform_fee=5.0,
    packaging_fee=4.0,
    gst_rate=0.05,
    surge_active=False,
)

ITEMS = [PricedItem("g1", "Item A", 100.0, 2), PricedItem("g2", "Item B", 50.0, 1)]


def test_itemized_math_hand_verified():
    # subtotal = 2*100 + 1*50 = 250 → free delivery (>= 200)
    # gst = 5% of (250 + 4 packaging) = 12.70
    # total = 250 + 0 + 5 + 4 + 12.70 = 271.70
    bill = compute_true_total(ITEMS, FEES)
    assert bill["items_subtotal"] == 250.0
    assert bill["delivery_fee"] == 0.0
    assert bill["platform_fee"] == 5.0
    assert bill["packaging_fee"] == 4.0
    assert bill["gst"] == 12.70
    assert bill["discount"] == 0.0
    assert bill["coupon_code"] is None
    assert bill["total"] == 271.70
    assert len(bill["lines"]) == 2
    assert bill["lines"][0]["line_total"] == 200.0


def test_delivery_fee_below_free_threshold():
    assert delivery_fee(150.0, 2.0, FEES) == 25.0


def test_delivery_fee_at_threshold_is_free():
    assert delivery_fee(200.0, 2.0, FEES) == 0.0


def test_delivery_fee_surge_and_distance():
    fees = FeeTable(platform_id="t", free_delivery_above=10 ** 9,
                    delivery_fee_base=25.0, surge_active=True,
                    surge_delivery_multiplier=1.25,
                    delivery_fee_per_km_beyond_3km=5.0)
    # 25 * 1.25 = 31.25 base; 5 km → 2 km beyond 3 → +10 → 41.25
    assert delivery_fee(100.0, 5.0, fees) == 41.25


def test_delivery_fee_surge_override_off():
    fees = FeeTable(platform_id="t", free_delivery_above=10 ** 9,
                    delivery_fee_base=25.0, surge_active=True,
                    surge_delivery_multiplier=1.25)
    assert delivery_fee(100.0, 2.0, fees, surge_override=False) == 25.0


def test_coupon_flows_into_bill():
    coupon = Offer(code="FLAT50", type="flat", value=50, min_order=200,
                   platforms=("t",))
    bill = compute_true_total(ITEMS, FEES, coupon=coupon, user=UserContext())
    assert bill["discount"] == 50.0
    assert bill["coupon_code"] == "FLAT50"
    assert bill["total"] == round(271.70 - 50.0, 2)


def test_inapplicable_coupon_ignored():
    coupon = Offer(code="BIG", type="flat", value=50, min_order=999,
                   platforms=("t",))
    bill = compute_true_total(ITEMS, FEES, coupon=coupon, user=UserContext())
    assert bill["discount"] == 0.0
    assert bill["coupon_code"] is None
    assert bill["total"] == 271.70
