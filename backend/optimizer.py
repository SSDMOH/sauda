"""Cart-split optimizer — ARCHITECTURE.md §5.

**Algorithm (exact, v1 scale):** enumerate every platform subset S (2^M;
M ≤ 8 → ≤256 subsets). For each S, assign every item to the cheapest
platform in S that stocks it, price each platform's basket with the true-total
engine, and accept S only if every *used* platform's assigned subtotal clears
its minimum-order value (platforms with zero items are fine). The feasible S
with the minimum grand total wins.

Tie-breaks: fewer platforms first, then faster max ETA.
Also reported: best single-platform total (the "consolidate" toggle) and
savings vs. the most expensive feasible option (the savings receipt).

Complexity is trivial at v1 scale (M ≤ 8, N ≤ 50). Graduate to ILP or
heuristics if M or N grows ~10×.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

from models import FeeTable, Offer, Product, UserContext
from offers import best_offer
from pricing import PricedItem, compute_true_total, delivery_fee


@dataclass(frozen=True)
class OptimizerItem:
    gtin: str
    qty: int = 1


@dataclass
class PlatformData:
    """Everything the optimizer needs to know about one platform."""
    platform_id: str
    display_name: str
    prices: dict[str, float]          # gtin -> unit selling price
    fee_table: FeeTable
    products: dict[str, Product] = field(default_factory=dict)  # gtin -> Product


@dataclass
class SplitPlan:
    feasible: bool
    groups: list[dict] = field(default_factory=list)
    platforms_used: list[str] = field(default_factory=list)
    grand_total: float = 0.0
    best_single_platform: dict | None = None
    most_expensive_feasible_total: float | None = None
    savings_vs_most_expensive: float = 0.0
    savings_vs_single_platform: float = 0.0
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "feasible": self.feasible,
            "groups": self.groups,
            "platforms_used": self.platforms_used,
            "grand_total": self.grand_total,
            "best_single_platform": self.best_single_platform,
            "most_expensive_feasible_total": self.most_expensive_feasible_total,
            "savings_vs_most_expensive": self.savings_vs_most_expensive,
            "savings_vs_single_platform": self.savings_vs_single_platform,
            "message": self.message,
        }


def _bill_for(platform: PlatformData, assignment: list[OptimizerItem],
              offers: list[Offer], user: UserContext,
              distance_km: float) -> dict:
    """True-total bill for one platform's share of the cart."""
    priced = []
    for item in assignment:
        product = platform.products.get(item.gtin)
        name = f"{product.brand} {product.name} {product.pack_label}" if product else item.gtin
        priced.append(PricedItem(gtin=item.gtin, name=name,
                                 unit_price=platform.prices[item.gtin],
                                 qty=item.qty))
    subtotal = round(sum(p.line_total for p in priced), 2)
    d_fee = delivery_fee(subtotal, distance_km, platform.fee_table)
    coupon, _ = best_offer(offers, platform.platform_id, subtotal, d_fee, user)
    bill = compute_true_total(priced, platform.fee_table,
                              distance_km=distance_km,
                              coupon=coupon, user=user)
    bill["platform_id"] = platform.platform_id
    bill["display_name"] = platform.display_name
    bill["eta_minutes"] = platform.fee_table.eta_mid
    return bill


def optimize_cart(items: list[OptimizerItem],
                  platforms: dict[str, PlatformData],
                  max_platforms: int = 3,
                  offers: list[Offer] | None = None,
                  user: UserContext | None = None,
                  distance_km: float = 2.0) -> SplitPlan:
    """Find the cheapest feasible split of `items` across `platforms`."""
    offers = offers or []
    user = user or UserContext()
    items = [i for i in items if i.qty >= 1]
    if not items:
        return SplitPlan(feasible=False, message="Cart is empty.")
    if not platforms:
        return SplitPlan(feasible=False, message="No platforms available.")

    pids = list(platforms)
    best_key: tuple | None = None
    best_bills: dict[str, dict] | None = None
    best_single: dict | None = None          # cheapest feasible single platform
    max_total: float | None = None           # most expensive feasible option

    for r in range(1, min(max_platforms, len(pids)) + 1):
        for subset in combinations(pids, r):
            # 1. Assign every item to its cheapest stocking platform in S.
            assignment: dict[str, list[OptimizerItem]] = {}
            for item in items:
                cheapest: tuple[float, str] | None = None
                for pid in subset:
                    price = platforms[pid].prices.get(item.gtin)
                    if price is not None and (cheapest is None or price < cheapest[0]):
                        cheapest = (price, pid)
                if cheapest is None:
                    break  # some item stocked nowhere in S → infeasible
                assignment.setdefault(cheapest[1], []).append(item)
            else:
                # 2. Minimum-order gate per *used* platform.
                feasible = True
                bills: dict[str, dict] = {}
                for pid, share in assignment.items():
                    subtotal = round(sum(platforms[pid].prices[i.gtin] * i.qty
                                         for i in share), 2)
                    if subtotal < platforms[pid].fee_table.min_order_value:
                        feasible = False
                        break
                    bills[pid] = _bill_for(platforms[pid], share, offers,
                                           user, distance_km)
                if not feasible:
                    continue

                # 3. Score the subset.
                total = round(sum(b["total"] for b in bills.values()), 2)
                max_eta = max(platforms[pid].fee_table.eta_mid for pid in bills)
                key = (total, len(bills), max_eta)
                if best_key is None or key < best_key:
                    best_key, best_bills = key, bills
                if max_total is None or total > max_total:
                    max_total = total
                if r == 1:
                    pid = subset[0]
                    if best_single is None or total < best_single["total"]:
                        best_single = {"platform_id": pid,
                                       "display_name": platforms[pid].display_name,
                                       "total": total,
                                       "bill": bills[pid]}
                continue
            # broke out: infeasible subset, try next
            continue

    if best_bills is None:
        return SplitPlan(
            feasible=False,
            message=("No feasible split: some items are unavailable on every "
                     "platform, or minimum-order values cannot be met."),
        )

    ordered = sorted(best_bills.values(), key=lambda b: b["platform_id"])
    grand_total = round(sum(b["total"] for b in ordered), 2)
    single_total = best_single["total"] if best_single else None
    return SplitPlan(
        feasible=True,
        groups=ordered,
        platforms_used=[b["platform_id"] for b in ordered],
        grand_total=grand_total,
        best_single_platform=best_single,
        most_expensive_feasible_total=max_total,
        savings_vs_most_expensive=round((max_total or grand_total) - grand_total, 2),
        savings_vs_single_platform=round((single_total or grand_total) - grand_total, 2),
        message=(f"Split across {len(ordered)} platform(s) for the lowest true total."),
    )
