"""Mock Zepto adapter — deterministic simulated data, no network calls."""
from adapters._mock_common import SeededMockAdapter
from models import FeeTable


class MockZeptoAdapter(SeededMockAdapter):
    platform_id = "zepto"
    display_name = "Zepto"
    vertical = "grocery"
    fee_table = FeeTable(
        platform_id="zepto",
        min_order_value=99.0,
        free_delivery_above=149.0,   # lower free-delivery bar than rivals
        delivery_fee_base=29.0,
        platform_fee=4.0,
        packaging_fee=3.0,
        gst_rate=0.05,
        surge_active=True,           # surge pricing currently on
        surge_delivery_multiplier=1.25,
        eta_min=10,
        eta_max=12,
    )
