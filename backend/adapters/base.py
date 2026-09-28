"""Platform adapter interface — ARCHITECTURE.md §3.

v1 ships mock adapters with deterministic, realistic data behind this exact
interface, so real adapters (private-API replay / Playwright fallback) can be
plugged in later without touching any caller.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from models import AdapterStatus, PriceSnapshot, Product, UserSession


class PlatformAdapter(ABC):
    platform_id: str          # "blinkit" | "zepto" | "instamart" | ...
    vertical: str             # "grocery" | "food" | "cabs" | "ecommerce"

    @abstractmethod
    async def link_account(self, phone: str) -> str:
        """Start OTP account linking. Returns an opaque link_ref."""
        ...

    @abstractmethod
    async def verify_otp(self, link_ref: str, otp: str) -> UserSession:
        """Verify the OTP for a pending link_ref. Returns an authenticated session."""
        ...

    @abstractmethod
    async def search(self, query: str, lat: float, lng: float,
                     session: UserSession) -> list[Product]:
        """Search the platform catalog near (lat, lng)."""
        ...

    @abstractmethod
    async def get_price(self, product_id: str,
                        session: UserSession) -> PriceSnapshot:
        """Live price snapshot for one product. Raises PriceUnavailableError
        when the product is unknown or not stocked."""
        ...

    @abstractmethod
    async def health(self) -> AdapterStatus:
        """Adapter health: live | degraded | down."""
        ...
