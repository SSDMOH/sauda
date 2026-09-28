"""Shared building blocks for the real e-commerce adapters.

Amazon, Flipkart and Myntra each ship their own adapter module
(real_amazon.py, real_flipkart.py, real_myntra.py) following the proven
real_blinkit.py pattern: an open/unsigned search request issued from a REAL
Chromium page context via Playwright, because datacenter HTTP gets blocked
by the platforms' bot protection.

What this module provides:
- IdentitySignals: the identity-resolution signals every e-com adapter must
  expose (brand, model/MPN, variant attributes, seller, condition, delivery
  promise, effective price, and an honest ambiguity marker). models.Product
  stays frozen; identity data lives here, keyed by gtin, and adapters expose
  it through ``identity(product_id)``. Callers MUST consult identity()
  before feeding live results into the Smart Split Cart — an ``ambiguous``
  signal means the product identity is NOT resolved.
- PageContextTransport: shared Playwright lifecycle + polite pacing
  (bounded concurrency, min request interval, backoff on 429/503).
  Subclasses only implement the platform's actual request.
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

log = logging.getLogger("sauda.ecom_common")


def playwright_available() -> bool:
    """Eager check: is the real (Playwright) transport usable here?"""
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Identity signals — the raw material for cross-platform identity resolution
# ---------------------------------------------------------------------------

@dataclass
class IdentitySignals:
    """Everything the adapter learned about a product's identity from the
    platform's own data — exposed so downstream code can decide whether two
    listings are the same sellable item BEFORE any cart math happens.

    Fields that the platform does not disclose stay None — never guessed.
    """
    platform_id: str
    platform_product_id: str   # ASIN / pid / Myntra style id
    brand: str
    name: str
    variant_attributes: dict[str, str] = field(default_factory=dict)
    # e.g. {"size": "UK 9", "colour": "Black"} | {"size": "128 GB"}
    model_code: str | None = None        # MPN / model number, when the platform shows one
    seller: str | None = None            # marketplace seller, when shown
    condition: str = "new"               # always "new" unless the platform says otherwise
    delivery_promise: str | None = None  # e.g. "Delivery by Tue" / "FREE delivery"
    delivery_fee: float | None = None    # INR, None when the platform doesn't say
    price_effective: float = 0.0         # listed price + known delivery fee
    price_includes_delivery: bool = False
    canonical_url: str = ""
    image_url: str | None = None
    confidence: str = "medium"           # "high" | "medium" | "low"
    ambiguous: bool = False              # True => identity NOT resolved; do not cart-math
    ambiguous_reason: str = ""           # why, in plain language

    def mark_ambiguous(self, reason: str) -> "IdentitySignals":
        self.ambiguous = True
        self.confidence = "low"
        self.ambiguous_reason = reason
        return self


# ---------------------------------------------------------------------------
# Page-context transport (Playwright + polite pacing)
# ---------------------------------------------------------------------------

class PageContextTransport(ABC):
    """Issues a platform's own open/unsigned search request from a real
    Chromium page, exactly like the site's frontend does. Shared browser
    lifecycle and pacing; subclasses implement the platform request."""

    concurrency: int = 2
    min_request_interval_s: float = 0.8
    max_retries: int = 5

    def __init__(self, site_url: str, headless: bool = True,
                 timeout_ms: int = 20000) -> None:
        self.site_url = site_url.rstrip("/")
        self.headless = headless
        self.timeout_ms = timeout_ms
        self._sem = asyncio.Semaphore(self.concurrency)
        self._last_request_at = 0.0
        self._started = False

    async def _ensure(self) -> None:
        if self._started:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright is not installed — run "
                "`pip install playwright && playwright install chromium` "
                "to enable live e-commerce data."
            ) from exc
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=self.headless)
        context = await browser.new_context(
            viewport={"width": 1366, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"),
            locale="en-IN",
        )
        page = await context.new_page()
        # Warm up: land on the site once so cookies / bot challenges settle.
        await page.goto(self.site_url + "/", wait_until="domcontentloaded",
                        timeout=self.timeout_ms)
        await page.wait_for_timeout(2500)
        self._page = page
        self._started = True
        self._pw = pw
        self._browser = browser

    async def aclose(self) -> None:
        if self._started:
            await self._browser.close()
            await self._pw.stop()
            self._started = False

    @abstractmethod
    async def _request_raw(self, query: str) -> tuple[int, str, str]:
        """Return (http_status, url, raw_body_text) for one search request."""
        ...

    async def search_raw(self, query: str) -> tuple[int, str, str]:
        """Paced, retried search. Never swallows HTTP errors — the adapter
        decides how to surface them (health reports down; search propagates)."""
        await self._ensure()
        async with self._sem:
            wait = self.min_request_interval_s - (time.monotonic() - self._last_request_at)
            if wait > 0:
                await asyncio.sleep(wait)
            last_exc: Exception | None = None
            for attempt in range(self.max_retries):
                try:
                    status, url, body = await self._request_raw(query)
                except Exception as exc:  # noqa: BLE001 — retried below
                    last_exc = exc
                    await asyncio.sleep(min(8.0, 1.0 * 2 ** attempt))
                    continue
                if status in (429, 503) and attempt < self.max_retries - 1:
                    backoff = min(10.0, 1.0 * 2 ** attempt)
                    log.warning("%s rate-limited (attempt %d); backing off %.1fs",
                                self.site_url, attempt, backoff)
                    await asyncio.sleep(backoff)
                    continue
                self._last_request_at = time.monotonic()
                return status, url, body
            raise RuntimeError(
                f"{self.site_url} search retries exhausted") from last_exc
