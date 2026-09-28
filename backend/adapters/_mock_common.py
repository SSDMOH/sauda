"""Shared implementation for the v1 mock adapters.

Real adapters will replay each platform's private mobile API using the user's
own linked session (see research notes). The mocks below speak the identical
interface with deterministic simulated data, so every caller — pricing,
optimizer, API — works unchanged when real adapters land.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime

from adapters.base import PlatformAdapter
from adapters.catalog import (
    PRODUCTS,
    PRODUCTS_BY_GTIN,
    platform_eta_minutes,
    platform_price,
)
from models import AdapterStatus, FeeTable, PriceSnapshot, Product, UserSession, utcnow

_OTP_RE = re.compile(r"^\d{6}$")


class SeededMockAdapter(PlatformAdapter):
    """Deterministic mock platform. Subclasses only set identity + fees."""

    platform_id: str = "mock"
    display_name: str = "Mock"
    vertical: str = "grocery"
    uses_static_catalog: bool = True   # prices come from adapters/catalog.py
    fee_table: FeeTable = FeeTable(platform_id="mock")

    def __init__(self) -> None:
        # link_ref -> phone for in-flight OTP verifications (in-memory, v1)
        self._pending: dict[str, str] = {}

    # -- account linking (mock OTP: any 6-digit code is accepted) ---------

    async def link_account(self, phone: str) -> str:
        ref = f"mock-link-{self.platform_id}-{uuid.uuid4().hex[:8]}"
        self._pending[ref] = phone
        return ref

    async def verify_otp(self, link_ref: str, otp: str) -> UserSession:
        if link_ref not in self._pending:
            raise ValueError("Unknown or expired link reference")
        if not _OTP_RE.match(otp):
            # Documented mock behaviour: any 6-digit OTP is accepted.
            raise ValueError("Invalid OTP — mock accepts any 6-digit code")
        phone = self._pending.pop(link_ref)
        return UserSession(
            platform_id=self.platform_id,
            phone=phone,
            token=f"mock-token-{self.platform_id}-{uuid.uuid4().hex[:12]}",
            linked_at=utcnow(),
        )

    # -- catalog -----------------------------------------------------------

    async def search(self, query: str, lat: float, lng: float,
                     session: UserSession) -> list[Product]:
        q = query.strip().lower()
        matches = [
            p for p in PRODUCTS
            if q in f"{p.brand} {p.name}".lower()
            and platform_price(self.platform_id, p) is not None
        ]
        return sorted(matches, key=lambda p: (p.brand, p.name))

    async def get_price(self, product_id: str,
                        session: UserSession) -> PriceSnapshot:
        from models import PriceUnavailableError  # local import: keeps base.py clean
        product = PRODUCTS_BY_GTIN.get(product_id)
        if product is None:
            raise PriceUnavailableError(f"Unknown product {product_id!r}")
        price = platform_price(self.platform_id, product)
        if price is None:
            raise PriceUnavailableError(
                f"{product.brand} {product.name} not stocked on {self.platform_id}")
        return PriceSnapshot(
            platform_id=self.platform_id,
            gtin=product.gtin,
            price=price,
            mrp=product.mrp,
            per_unit_price=round(price / product.pack_size, 4),
            in_stock=True,
            eta_minutes=platform_eta_minutes(
                self.platform_id, product.gtin,
                self.fee_table.eta_min, self.fee_table.eta_max),
            freshness="live",
            captured_at=utcnow(),
        )

    async def health(self) -> AdapterStatus:
        return AdapterStatus(platform_id=self.platform_id, status="live",
                             latency_ms=60, message=f"{self.display_name} mock OK")
