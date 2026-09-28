"""Shared domain models for the Sauda backend engine.

These are plain dataclasses (no framework dependency) so the pricing,
optimizer and offer engines can be used with or without the FastAPI layer.
Money is represented in INR (float, rounded to 2 decimals at boundaries).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Catalog / pricing primitives
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Product:
    """A single sellable product. GTIN is the cross-platform identity key."""
    gtin: str
    brand: str
    name: str
    pack_size: float      # quantity in base unit (g / ml / pcs)
    unit: str             # "g" | "ml" | "pcs"
    pack_label: str       # human display, e.g. "5 kg", "1 L", "6 pcs"
    mrp: float            # maximum retail price (INR)

    def per_unit_label(self, price: float) -> str:
        """Human per-unit string, e.g. '₹42.00/kg', '₹75.00/L', '₹9.17/pc'."""
        per = price / self.pack_size
        if self.unit == "g":
            return f"₹{per * 1000:.2f}/kg"
        if self.unit == "ml":
            return f"₹{per * 1000:.2f}/L"
        return f"₹{per:.2f}/pc"


@dataclass(frozen=True)
class PriceSnapshot:
    platform_id: str
    gtin: str
    price: float
    mrp: float
    per_unit_price: float   # INR per g / ml / pc (base unit)
    in_stock: bool
    eta_minutes: int
    freshness: str          # "live" | "est"
    captured_at: datetime


@dataclass(frozen=True)
class UserSession:
    platform_id: str
    phone: str
    token: str              # opaque session token (mock in v1)
    linked_at: datetime


@dataclass(frozen=True)
class AdapterStatus:
    platform_id: str
    status: str             # "live" | "degraded" | "down"
    latency_ms: int
    message: str = ""


class PriceUnavailableError(Exception):
    """Raised when a product is unknown or not stocked on a platform."""


# ---------------------------------------------------------------------------
# Fee tables (versioned per platform per city in prod; single city in v1)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FeeTable:
    platform_id: str
    city: str = "default"
    version: int = 1
    min_order_value: float = 99.0
    free_delivery_above: float = 199.0
    delivery_fee_base: float = 25.0
    delivery_fee_per_km_beyond_3km: float = 5.0
    platform_fee: float = 5.0
    packaging_fee: float = 4.0
    gst_rate: float = 0.05          # applied on (items subtotal + packaging)
    surge_active: bool = False
    surge_delivery_multiplier: float = 1.25
    eta_min: int = 10
    eta_max: int = 20

    @property
    def eta_mid(self) -> int:
        return (self.eta_min + self.eta_max) // 2


# ---------------------------------------------------------------------------
# Offers + user context
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Offer:
    """Coupon rule object (ARCHITECTURE.md §7)."""
    code: str
    type: str                       # "flat" | "pct_upto" | "free_delivery"
    value: float                    # flat INR, or percent for pct_upto
    max_discount: float = 0.0       # cap for pct_upto (0 = no cap)
    min_order: float = 0.0          # minimum items subtotal to qualify
    first_order_only: bool = False
    payment_methods: tuple[str, ...] = ()   # empty = any method
    platforms: tuple[str, ...] = ()         # platform_ids this coupon applies to


@dataclass(frozen=True)
class UserContext:
    user_id: str = "guest"
    is_first_order: bool = False
    payment_method: str = "upi"


@dataclass(frozen=True)
class OfferResult:
    applicable: bool
    discount: float
    reason: str = ""
