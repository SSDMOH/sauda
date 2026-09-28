"""Sauda backend demo — build a 6-item cart, run the split optimizer,
and print the plan + savings receipt.

Run from the backend/ directory:
    .venv/bin/python demo.py
"""
from adapters import all_mock_adapters
from adapters.catalog import PRODUCTS, platform_price
from models import UserContext
from offers import SAMPLE_OFFERS
from optimizer import OptimizerItem, PlatformData, optimize_cart

# (brand, name-substring, pack-substring|None, qty)
PICKS = [
    ("Aashirvaad", "atta", "5 kg", 1),     # staples
    ("Amul", "milk", "1 L", 2),
    ("Farm Fresh", "eggs", None, 1),       # NOT stocked on Blinkit
    ("Maggi", "noodles", None, 1),
    ("Tata Tea", "gold", None, 1),
    ("Dove", "bathing bar", None, 1),      # NOT stocked on Zepto
]


def pick_product(brand: str, name_sub: str, pack_sub: str | None):
    for p in PRODUCTS:
        if (p.brand == brand and name_sub in p.name.lower()
                and (pack_sub is None or pack_sub in p.pack_label)):
            return p
    raise AssertionError(f"no product for {(brand, name_sub, pack_sub)}")


def main() -> None:
    adapters = all_mock_adapters()
    cart = [(pick_product(*pick[:3]), pick[3]) for pick in PICKS]

    platforms: dict[str, PlatformData] = {}
    for pid, adapter in adapters.items():
        prices, products = {}, {}
        for p in PRODUCTS:
            price = platform_price(pid, p)
            if price is not None:
                prices[p.gtin] = price
                products[p.gtin] = p
        platforms[pid] = PlatformData(
            platform_id=pid, display_name=adapter.display_name,
            prices=prices, fee_table=adapter.fee_table, products=products)

    plan = optimize_cart(
        [OptimizerItem(gtin=p.gtin, qty=q) for p, q in cart],
        platforms, max_platforms=3, offers=SAMPLE_OFFERS,
        user=UserContext(user_id="demo", is_first_order=True))

    print("=" * 64)
    print("  SAUDA — Smart Split Cart demo")
    print("=" * 64)
    cart_desc = ", ".join(f"{p.brand} {p.name} {p.pack_label} x{q}"
                          for p, q in cart)
    print(f"\nCart ({len(cart)} line items): {cart_desc}\n")

    if not plan.feasible:
        print("No feasible plan:", plan.message)
        return

    for g in plan.groups:
        print(f"--- {g['display_name']}  (ETA ~{g['eta_minutes']} min) ---")
        for it in g["lines"]:
            print(f"  {it['name']:<52} x{it['qty']}  "
                  f"₹{it['unit_price']:.0f} → ₹{it['line_total']:.0f}")
        print(f"  items subtotal : ₹{g['items_subtotal']:.2f}")
        print(f"  delivery       : ₹{g['delivery_fee']:.2f}")
        print(f"  platform fee   : ₹{g['platform_fee']:.2f}")
        print(f"  packaging      : ₹{g['packaging_fee']:.2f}")
        print(f"  GST            : ₹{g['gst']:.2f}")
        if g["discount"]:
            print(f"  coupon {g['coupon_code']:<9}: −₹{g['discount']:.2f}")
        print(f"  group total    : ₹{g['total']:.2f}\n")

    print("-" * 64)
    print("  SAVINGS RECEIPT")
    print("-" * 64)
    print(f"  You pay (split)          : ₹{plan.grand_total:.2f}")
    single = plan.best_single_platform or {}
    print(f"  Best single platform     : "
          f"{single.get('display_name')} ₹{single.get('total', 0):.2f} "
          f"(you save ₹{plan.savings_vs_single_platform:.2f} by splitting)")
    print(f"  Most expensive option    : ₹{plan.most_expensive_feasible_total:.2f}")
    print(f"  ★ Total saved vs worst   : ₹{plan.savings_vs_most_expensive:.2f}")
    print("=" * 64)


if __name__ == "__main__":
    main()
