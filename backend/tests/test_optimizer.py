"""Tests for the cart-split optimizer on hand-verifiable fixtures.

Fixture (no GST/packaging/platform fees for clean hand math):
    A: i1=50, i2=50, i3=80 | min_order=100 | delivery=30
    B: i1=80, i2=80, i3=30 | min_order=30  | delivery=10

Subset totals (hand-computed):
    {A}:   180 items + 30 delivery                    = 210
    {B}:   190 items + 10 delivery                    = 200
    {A,B}: i1→A, i2→A (100 ≥ 100 ✓ → 130); i3→B (30 ≥ 30 ✓ → 40) = 170  ← winner

Most expensive feasible = 210 → savings 40. Best single = B @ 200.
"""
from models import FeeTable, Offer, Product, UserContext
from optimizer import OptimizerItem, PlatformData, optimize_cart

I1, I2, I3 = "i1", "i2", "i3"
ITEMS = [OptimizerItem(I1, 1), OptimizerItem(I2, 1), OptimizerItem(I3, 1)]


def _prod(gtin: str) -> Product:
    return Product(gtin=gtin, brand="T", name=f"Item {gtin}", pack_size=1000,
                   unit="g", pack_label="1 kg", mrp=100.0)


def _platform(pid: str, prices: dict[str, float], min_order: float,
              delivery: float) -> PlatformData:
    fees = FeeTable(platform_id=pid, min_order_value=min_order,
                    free_delivery_above=10 ** 9, delivery_fee_base=delivery,
                    platform_fee=0.0, packaging_fee=0.0, gst_rate=0.0,
                    surge_active=False)
    return PlatformData(platform_id=pid, display_name=pid.title(),
                        prices=prices, fee_table=fees,
                        products={g: _prod(g) for g in prices})


def _platforms(a_min_order: float = 100.0) -> dict[str, PlatformData]:
    return {
        "a": _platform("a", {I1: 50, I2: 50, I3: 80}, a_min_order, 30.0),
        "b": _platform("b", {I1: 80, I2: 80, I3: 30}, 30.0, 10.0),
    }


def test_picks_true_minimum_split():
    plan = optimize_cart(ITEMS, _platforms(), max_platforms=2)
    assert plan.feasible
    assert plan.platforms_used == ["a", "b"]
    assert plan.grand_total == 170.0
    # savings receipt
    assert plan.most_expensive_feasible_total == 210.0
    assert plan.savings_vs_most_expensive == 40.0
    assert plan.best_single_platform is not None
    assert plan.best_single_platform["platform_id"] == "b"
    assert plan.best_single_platform["total"] == 200.0
    assert plan.savings_vs_single_platform == 30.0
    # group contents
    by_pid = {g["platform_id"]: g for g in plan.groups}
    assert {i["gtin"] for i in by_pid["a"]["lines"]} == {I1, I2}
    assert {i["gtin"] for i in by_pid["b"]["lines"]} == {I3}
    assert by_pid["a"]["total"] == 130.0
    assert by_pid["b"]["total"] == 40.0


def test_min_order_constraint_respected():
    # Raise A's minimum order to 150: the {A,B} split assigns A only 100,
    # so that subset becomes infeasible and single-platform B (200) must win.
    plan = optimize_cart(ITEMS, _platforms(a_min_order=150.0), max_platforms=2)
    assert plan.feasible
    assert plan.platforms_used == ["b"]
    assert plan.grand_total == 200.0


def test_max_platforms_one_forces_consolidation():
    plan = optimize_cart(ITEMS, _platforms(), max_platforms=1)
    assert plan.feasible
    assert plan.platforms_used == ["b"]          # cheaper single platform
    assert plan.grand_total == 200.0


def test_unavailable_item_makes_subset_infeasible():
    platforms = {"a": _platform("a", {I1: 50, I2: 50}, 0.0, 30.0)}  # i3 missing
    plan = optimize_cart(ITEMS, platforms, max_platforms=1)
    assert plan.feasible is False


def test_empty_cart_infeasible():
    plan = optimize_cart([], _platforms())
    assert plan.feasible is False


def test_coupon_applied_per_group():
    offers = [Offer(code="T20", type="flat", value=20, min_order=50,
                    platforms=("a",))]
    plan = optimize_cart([OptimizerItem(I1, 1), OptimizerItem(I2, 1)],
                         _platforms(), max_platforms=1,
                         offers=offers, user=UserContext())
    assert plan.feasible and plan.platforms_used == ["a"]
    group = plan.groups[0]
    assert group["coupon_code"] == "T20"
    assert group["discount"] == 20.0
    # 100 items + 30 delivery − 20 coupon
    assert group["total"] == 110.0
