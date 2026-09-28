"""Mock Blinkit adapter — deterministic simulated data, no network calls."""
from adapters._mock_common import SeededMockAdapter
from models import FeeTable


class MockBlinkitAdapter(SeededMockAdapter):
    platform_id = "blinkit"
    display_name = "Blinkit"
    vertical = "grocery"
    fee_table = FeeTable(
        platform_id="blinkit",
        min_order_value=99.0,
        free_delivery_above=199.0,
        delivery_fee_base=25.0,
        platform_fee=5.0,
        packaging_fee=4.0,
        gst_rate=0.05,
        surge_active=False,          # calm right now
        eta_min=10,
        eta_max=15,
    )
