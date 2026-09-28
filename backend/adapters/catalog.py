"""Shared v1 grocery catalog + deterministic per-platform pricing.

The catalog is the cross-platform source of truth (GTIN = identity key, the
stand-in for real barcode matching). Each mock platform derives its selling
price from a seeded RNG keyed by (platform_id, gtin), so prices are stable
across runs but differ plausibly (±5–15% off MRP) between platforms.

A few products are deliberately unavailable on one platform each so the
cart-split optimizer has interesting choices to make.
"""
from __future__ import annotations

import random

from models import Product


def _row(gtin: str, brand: str, name: str, pack_size: float,
         unit: str, pack_label: str, mrp: float) -> Product:
    return Product(gtin=gtin, brand=brand, name=name, pack_size=pack_size,
                   unit=unit, pack_label=pack_label, mrp=mrp)


PRODUCTS: list[Product] = [
    _row("8901010000017", "Aashirvaad", "Shudh Chakki Whole Wheat Atta", 5000, "g", "5 kg", 285),
    _row("8901010000024", "Aashirvaad", "Shudh Chakki Whole Wheat Atta", 10000, "g", "10 kg", 545),
    _row("8901010000031", "India Gate", "Rozzana Basmati Rice", 5000, "g", "5 kg", 425),
    _row("8901010000048", "Daawat", "Daily Basmati Rice", 1000, "g", "1 kg", 145),
    _row("8901010000055", "Tata Sampann", "Unpolished Toor Dal", 1000, "g", "1 kg", 185),
    _row("8901010000062", "Tata Sampann", "Moong Dal Chilka", 500, "g", "500 g", 95),
    _row("8901010000079", "Tata", "Iodised Salt", 1000, "g", "1 kg", 28),
    _row("8901010000086", "Fortune", "Sunflower Oil", 1000, "ml", "1 L", 145),
    _row("8901010000093", "Fortune", "Kachi Ghani Mustard Oil", 1000, "ml", "1 L", 210),
    _row("8901010000109", "Amul", "Taaza Toned Fresh Milk", 1000, "ml", "1 L", 75),
    _row("8901010000116", "Amul", "Taaza Toned Fresh Milk", 500, "ml", "500 ml", 30),
    _row("8901010000123", "Amul", "Masti Dahi Curd Cup", 400, "g", "400 g", 35),
    _row("8901010000130", "Amul", "Pasteurised Butter", 100, "g", "100 g", 60),
    _row("8901010000147", "Amul", "Cheese Slices", 200, "g", "200 g", 145),
    _row("8901010000154", "Farm Fresh", "White Eggs", 6, "pcs", "6 pcs", 55),
    _row("8901010000161", "Harvest Gold", "Whole Wheat Bread", 400, "g", "400 g", 50),
    _row("8901010000178", "Parle", "Parle-G Gold Biscuits", 200, "g", "200 g", 25),
    _row("8901010000185", "Maggi", "2-Minute Instant Noodles", 840, "g", "12 pack", 168),
    _row("8901010000192", "Tata Tea", "Gold", 500, "g", "500 g", 285),
    _row("8901010000208", "Nescafe", "Classic Instant Coffee", 50, "g", "50 g", 165),
    _row("8901010000215", "Coca-Cola", "Soft Drink", 750, "ml", "750 ml", 40),
    _row("8901010000222", "Frooti", "Mango Drink", 1000, "ml", "1 L", 115),
    _row("8901010000239", "Lay's", "India's Magic Masala Potato Chips", 73, "g", "73 g", 20),
    _row("8901010000246", "Haldiram's", "Aloo Bhujia", 400, "g", "400 g", 95),
    _row("8901010000253", "Dove", "Cream Beauty Bathing Bar", 100, "g", "100 g", 48),
    _row("8901010000260", "Colgate", "MaxFresh Toothpaste", 150, "g", "150 g", 145),
    _row("8901010000277", "Surf Excel", "Easy Wash Detergent Powder", 1000, "g", "1 kg", 130),
    _row("8901010000284", "Dabur", "Pure Honey", 500, "g", "500 g", 230),
    _row("8901010000291", "Real", "Fruit Power Orange Juice", 1000, "ml", "1 L", 130),
    _row("8901010000307", "Vim", "Lemon Dishwash Bar", 300, "g", "3 x 100 g", 78),
]

PRODUCTS_BY_GTIN: dict[str, Product] = {p.gtin: p for p in PRODUCTS}

# Deliberate stock gaps — 3-4 products missing per platform, so the optimizer
# must route around them (and min-order rules still have to hold).
UNAVAILABLE: dict[str, set[str]] = {
    "zepto": {"8901010000215", "8901010000253", "8901010000246"},          # coke, dove, haldiram's
    "blinkit": {"8901010000154", "8901010000222", "8901010000260"},        # eggs, frooti, colgate
    "instamart": {"8901010000208", "8901010000147", "8901010000277",       # nescafe, cheese slices,
                  "8901010000239"},                                       # surf excel, lay's
}


def seeded_rng(platform_id: str, gtin: str) -> random.Random:
    """Deterministic RNG per (platform, product). Same seed → same prices."""
    return random.Random(f"sauda-v1:{platform_id}:{gtin}")


def platform_price(platform_id: str, product: Product) -> float | None:
    """Selling price on a platform, or None when not stocked.

    Discount off MRP is drawn uniformly from 5–15% (seeded), rounded to the
    nearest rupee — the typical quick-commerce band in India.
    """
    if product.gtin in UNAVAILABLE.get(platform_id, set()):
        return None
    discount = seeded_rng(platform_id, product.gtin).uniform(0.05, 0.15)
    return float(round(product.mrp * (1 - discount)))


def platform_eta_minutes(platform_id: str, gtin: str,
                         eta_min: int, eta_max: int) -> int:
    rng = seeded_rng(platform_id, gtin)
    return eta_min + rng.randint(0, max(0, eta_max - eta_min))
