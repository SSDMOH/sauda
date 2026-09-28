"""Platform adapters: common ABC + mock and real implementations."""
import logging
import os

from adapters.base import PlatformAdapter
from adapters.mock_blinkit import MockBlinkitAdapter
from adapters.mock_zepto import MockZeptoAdapter
from adapters.mock_instamart import MockInstamartAdapter
from adapters.mock_flipkart_minutes import MockFlipkartMinutesAdapter
from adapters.real_blinkit import RealBlinkitAdapter, playwright_available
from adapters.real_zepto import RealZeptoAdapter
from adapters.real_instamart import (
    RealInstamartAdapter,
    playwright_available as instamart_playwright_available,
)
from adapters.real_flipkart_minutes import RealFlipkartMinutesAdapter
from adapters.real_swiggy import RealSwiggyAdapter
from adapters.real_zomato import RealZomatoAdapter
from adapters.real_amazon import RealAmazonAdapter
from adapters.real_flipkart import RealFlipkartAdapter
from adapters.real_myntra import RealMyntraAdapter
from adapters.real_cabs import (
    RealCabsAdapter,
    real_cabs_enabled,
)
from adapters.ecom_common import playwright_available as ecom_playwright_available

log = logging.getLogger("sauda.adapters")

__all__ = [
    "PlatformAdapter",
    "MockBlinkitAdapter",
    "MockZeptoAdapter",
    "MockInstamartAdapter",
    "MockFlipkartMinutesAdapter",
    "RealBlinkitAdapter",
    "RealZeptoAdapter",
    "RealInstamartAdapter",
    "RealFlipkartMinutesAdapter",
    "RealSwiggyAdapter",
    "RealZomatoAdapter",
    "RealAmazonAdapter",
    "RealFlipkartAdapter",
    "RealMyntraAdapter",
    "RealCabsAdapter",
    "all_mock_adapters",
    "all_adapters",
    "any_real_adapters",
    "real_adapter_flags",
]


def all_mock_adapters() -> dict[str, PlatformAdapter]:
    """Instantiate one mock adapter per v1 platform, keyed by platform_id."""
    adapters = [
        MockBlinkitAdapter(),
        MockZeptoAdapter(),
        MockInstamartAdapter(),
        MockFlipkartMinutesAdapter(),
    ]
    return {a.platform_id: a for a in adapters}


def _enable(flag: str, make, label: str, adapters: dict[str, PlatformAdapter],
            pw_check=None) -> None:
    """Swap the mock for a real adapter when its env flag is set.

    Boot is never at risk: if the transport dependency is missing we log and
    stay on the mock. Construction itself must be side-effect free.
    """
    if os.environ.get(flag) != "1":
        return
    if pw_check is not None and not pw_check():
        log.warning(f"{flag}=1 but `playwright` is not installed — "
                    f"staying on the mock. `pip install playwright && "
                    f"playwright install chromium` to enable live data.")
        return
    real = make()
    adapters[real.platform_id] = real
    log.warning(f"{flag}=1 — {label} adapter is LIVE ({real.display_name})")


def all_adapters() -> dict[str, PlatformAdapter]:
    """Production adapter set.

    Default is fully mocked (no network). Set any SAUDA_REAL_* flag to swap
    that platform's mock for the live adapter. If the real transport can't
    initialise, we fall back to the mock with a loud warning rather than
    failing to boot.
    """
    adapters: dict[str, PlatformAdapter] = all_mock_adapters()
    _enable("SAUDA_REAL_BLINKIT", RealBlinkitAdapter,
            "Blinkit", adapters, pw_check=playwright_available)
    _enable("SAUDA_REAL_ZEPTO", RealZeptoAdapter,
            "Zepto", adapters)
    _enable("SAUDA_REAL_INSTAMART", RealInstamartAdapter,
            "Swiggy Instamart", adapters, pw_check=instamart_playwright_available)
    _enable("SAUDA_REAL_FLIPKART_MINUTES", RealFlipkartMinutesAdapter,
            "Flipkart Minutes", adapters)
    _enable("SAUDA_REAL_SWIGGY", RealSwiggyAdapter,
            "Swiggy", adapters)
    _enable("SAUDA_REAL_ZOMATO", RealZomatoAdapter,
            "Zomato", adapters)
    _enable("SAUDA_REAL_AMAZON", RealAmazonAdapter,
            "Amazon.in", adapters, pw_check=ecom_playwright_available)
    _enable("SAUDA_REAL_FLIPKART", RealFlipkartAdapter,
            "Flipkart", adapters, pw_check=ecom_playwright_available)
    _enable("SAUDA_REAL_MYNTRA", RealMyntraAdapter,
            "Myntra", adapters, pw_check=ecom_playwright_available)
    # Cabs is an additive platform (no mock exists): enabling only adds it.
    if real_cabs_enabled():
        real = RealCabsAdapter()
        adapters[real.platform_id] = real
        log.warning("SAUDA_REAL_CABS=1 — cabs adapter is ondc_cabs "
                    "(BLOCKED for live data without BAP credentials / "
                    "/on_search callback; see adapters/real_cabs.py)")
    return adapters


def any_real_adapters(adapters: dict[str, PlatformAdapter]) -> bool:
    real_types = (
        RealBlinkitAdapter,
        RealZeptoAdapter,
        RealInstamartAdapter,
        RealFlipkartMinutesAdapter,
        RealSwiggyAdapter,
        RealZomatoAdapter,
        RealAmazonAdapter,
        RealFlipkartAdapter,
        RealMyntraAdapter,
        RealCabsAdapter,
    )
    return any(isinstance(a, real_types) for a in adapters.values())


def real_adapter_flags() -> dict[str, str]:
    """Env flag -> platform_id for every real adapter."""
    return {
        "SAUDA_REAL_BLINKIT": "blinkit",
        "SAUDA_REAL_ZEPTO": "zepto",
        "SAUDA_REAL_INSTAMART": "instamart",
        "SAUDA_REAL_FLIPKART_MINUTES": "flipkart_minutes",
        "SAUDA_REAL_SWIGGY": "swiggy",
        "SAUDA_REAL_ZOMATO": "zomato",
        "SAUDA_REAL_AMAZON": "amazon",
        "SAUDA_REAL_FLIPKART": "flipkart",
        "SAUDA_REAL_MYNTRA": "myntra",
        "SAUDA_REAL_CABS": "ondc_cabs",
    }
