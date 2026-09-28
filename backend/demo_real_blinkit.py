"""Live Blinkit adapter demo — search Blinkit and print real product prices.

With SAUDA_REAL_BLINKIT=1 and playwright + chromium installed, this hits the
real Blinkit search API (guest mode, no login) from a real Chromium page.

Without those, it runs against a stub transport with clearly-labeled SAMPLE
data so the adapter's mapping can be inspected offline.

Run from the backend/ directory:
    .venv/bin/python demo_real_blinkit.py [query] [lat] [lon]
"""
import asyncio
import sys

sys.path.insert(0, ".")

from adapters.real_blinkit import (
    PlaywrightTransport,
    RealBlinkitAdapter,
    StubTransport,
    playwright_available,
)
from models import UserSession, utcnow
from tests.test_real_blinkit import SAMPLE_SEARCH_JSON


async def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "amul milk"
    lat = float(sys.argv[2]) if len(sys.argv) > 2 else 28.6139
    lon = float(sys.argv[3]) if len(sys.argv) > 3 else 77.2090

    if playwright_available():
        print("transport: Playwright (live Blinkit search API)")
        transport: StubTransport | PlaywrightTransport = PlaywrightTransport()
    else:
        print("transport: STUB — sample data only (install playwright for live data)")
        transport = StubTransport(SAMPLE_SEARCH_JSON)

    adapter = RealBlinkitAdapter(transport=transport)
    session = UserSession(platform_id="blinkit", phone="guest",
                          token="guest", linked_at=utcnow())
    try:
        status = await adapter.health()
        print(f"health: {status.status} ({status.latency_ms} ms) — {status.message}\n")
        if status.status == "down":
            print("Live transport unavailable — see message above. "
                  "Run with a stub for offline inspection, or from a "
                  "non-flagged network with `playwright install chromium`.")
            return
        products = await adapter.search(query, lat, lon, session)
        print(f"query: {query!r} @ ({lat}, {lon}) -> {len(products)} products\n")
        for p in products[:10]:
            snap = await adapter.get_price(p.gtin, session)
            print(f"  {p.brand} {p.name} {p.pack_label}")
            print(f"    price ₹{snap.price:.0f}  MRP ₹{snap.mrp:.0f}  "
                  f"{p.per_unit_label(snap.price)}  eta ~{snap.eta_minutes} min")
    finally:
        if isinstance(transport, PlaywrightTransport):
            await transport.aclose()


if __name__ == "__main__":
    asyncio.run(main())
