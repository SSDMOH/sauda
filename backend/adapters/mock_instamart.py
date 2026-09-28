"""Mock Swiggy Instamart adapter — deterministic simulated data, no network calls."""
from adapters._mock_common import SeededMockAdapter
from models import FeeTable


class MockInstamartAdapter(SeededMockAdapter):
    platform_id = "instamart"
    display_name = "Swiggy Instamart"
    vertical = "grocery"
    fee_table = FeeTable(
        platform_id="instamart",
        min_order_value=149.0,       # highest minimum order of the three
        free_delivery_above=199.0,
        delivery_fee_base=30.0,
        platform_fee=6.0,
        packaging_fee=5.0,
        gst_rate=0.05,
        surge_active=False,
        eta_min=15,
        eta_max=25,
    )
