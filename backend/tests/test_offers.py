"""Tests for the offer rule engine."""
from models import Offer, UserContext
from offers import SAMPLE_OFFERS, best_offer, evaluate_offer

U = UserContext(user_id="u1", is_first_order=False, payment_method="upi")
FIRST = UserContext(user_id="u2", is_first_order=True, payment_method="upi")


def test_flat_discount():
    r = evaluate_offer(Offer(code="F", type="flat", value=50, min_order=200),
                       300.0, 25.0, U)
    assert (r.applicable, r.discount) == (True, 50.0)


def test_flat_capped_at_subtotal():
    r = evaluate_offer(Offer(code="F", type="flat", value=50, min_order=0),
                       30.0, 25.0, U)
    assert (r.applicable, r.discount) == (True, 30.0)


def test_pct_upto_respects_cap():
    offer = Offer(code="P", type="pct_upto", value=20, max_discount=100,
                  min_order=0)
    assert evaluate_offer(offer, 1000.0, 0.0, U).discount == 100.0  # capped
    assert evaluate_offer(offer, 300.0, 0.0, U).discount == 60.0    # uncapped


def test_free_delivery_equals_delivery_fee():
    r = evaluate_offer(Offer(code="FD", type="free_delivery", value=0,
                             min_order=100), 150.0, 32.5, U)
    assert (r.applicable, r.discount) == (True, 32.5)


def test_min_order_blocks():
    r = evaluate_offer(Offer(code="F", type="flat", value=50, min_order=200),
                       150.0, 25.0, U)
    assert r.applicable is False and r.discount == 0.0


def test_first_order_only():
    offer = Offer(code="N", type="pct_upto", value=15, max_discount=120,
                  min_order=100, first_order_only=True)
    assert evaluate_offer(offer, 300.0, 0.0, U).applicable is False
    assert evaluate_offer(offer, 300.0, 0.0, FIRST).applicable is True


def test_payment_method_restriction():
    offer = Offer(code="H", type="flat", value=40, min_order=0,
                  payment_methods=("hdfc-card",))
    assert evaluate_offer(offer, 200.0, 0.0, U).applicable is False
    hdfc = UserContext(payment_method="hdfc-card")
    assert evaluate_offer(offer, 200.0, 0.0, hdfc).applicable is True


def test_best_offer_picks_maximum_discount():
    offers = [
        Offer(code="FLAT50", type="flat", value=50, min_order=200,
              platforms=("t",)),
        Offer(code="PCT25", type="pct_upto", value=25, max_discount=75,
              min_order=200, platforms=("t",)),
    ]
    # 25% of 400 = 100 → capped at 75 > flat 50
    offer, discount = best_offer(offers, "t", 400.0, 25.0, U)
    assert offer is not None and offer.code == "PCT25" and discount == 75.0


def test_best_offer_respects_platform_scope():
    offer, discount = best_offer(SAMPLE_OFFERS, "zepto", 400.0, 25.0, U)
    assert offer is not None and offer.code == "ZEPTO25"
    assert discount == 75.0  # 25% of 400 capped at 75


def test_best_offer_none_when_nothing_applies():
    offer, discount = best_offer(SAMPLE_OFFERS, "blinkit", 50.0, 25.0, U)
    assert offer is None and discount == 0.0


def test_sample_offers_shape():
    assert len(SAMPLE_OFFERS) == 6
    covered = {p for o in SAMPLE_OFFERS for p in o.platforms}
    assert covered == {"blinkit", "zepto", "instamart"}
    types = {o.type for o in SAMPLE_OFFERS}
    assert types == {"flat", "pct_upto", "free_delivery"}
