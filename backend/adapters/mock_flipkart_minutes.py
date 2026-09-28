"""Mock Flipkart Minutes adapter — deterministic simulated data, no network calls."""
from adapters._mock_common import SeededMockAdapter
from models import FeeTable


class MockFlipkartMinutesAdapter(SeededMockAdapter):
    platform_id = "flipkart_minutes"
    display_name = "Flipkart Minutes"
    vertical = "grocery"
    fee_table = FeeTable(
        platform_id="flipkart_minutes",
        min_order_value=99.0,
        free_delivery_above=199.0,
        delivery_fee_base=25.0,
        platform_fee=3.0,
        packaging_fee=2.0,
        gst_rate=0.05,
        eta_min=10,
        eta_max=15,
    )
