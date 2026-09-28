"""Tests for the real Zomato adapter (adapters/real_zomato.py).

All tests use StubTransport — no network. The fixture HTML is a hand-built
page mimicking Zomato's SSR escaping: the SECTION_SEARCH_RESULT array is
embedded as an escaped JSON string (verified shape from the live
/ncr/delivery/dish-biryani page, 2026-09-28).
"""
import asyncio
import json

import pytest

from adapters.real_zomato import (
    DirectTransport,
    RealZomatoAdapter,
    StubTransport,
    _eta_from_delivery_time,
    _price_from_cfo,
    dish_display_name,
    dish_slug_for_query,
    nearest_city_slug,
    parse_search_page,
)
from models import PriceUnavailableError, UserSession, utcnow


def _card(res_id, name, cfo_text="₹250 for one", serviceable=True,
          delivery_time="42 min", rating="4.3"):
    return {
        "type": "restaurant",
        "info": {
            "resId": res_id,
            "name": name,
            "cfo": {"text": cfo_text},
            "ratingNew": {"ratings": {"DELIVERY": {"rating": rating}}},
            "locality": {"name": "Connaught Place, New Delhi"},
        },
        "order": {
            "deliveryTime": delivery_time,
            "isServiceable": serviceable,
            "hasOnlineOrdering": True,
            "actionInfo": {"clickUrl": f"/ncr/x/order"},
        },
        "distance": "1.2 km",
    }


def zomato_page(cards) -> str:
    """Build fixture HTML with Zomato-style escaped SECTION_SEARCH_RESULT."""
    blob = json.dumps(cards).replace("\\", "\\\\").replace('"', '\\"')
    return ('<html><body><script>window.data='
            f'{{\\"SECTION_SEARCH_RESULT\\": {blob}}};'
            "</script></body></html>")


SAMPLE_HTML = zomato_page([
    _card(18382360, "Local"),
    _card(21209117, "Drama", cfo_text="₹1,150 for one",
          delivery_time="55 min", rating="4.1"),
    _card(999, "Closed Place", serviceable=False),      # skipped: not serviceable
    _card(1000, "Priceless", cfo_text=""),              # skipped: no price
    {"type": "ad", "info": {}},                          # skipped: not a restaurant
])


def _session() -> UserSession:
    return UserSession(platform_id="zomato", phone="guest",
                       token="guest", linked_at=utcnow())


def run(coro):
    return asyncio.run(coro)


# -- query / city mapping --------------------------------------------------------

def test_dish_slug_exact_and_multiword():
    assert dish_slug_for_query("biryani") == "dish-biryani"
    assert dish_slug_for_query("Chicken Biryani") == "dish-chicken-biryani"
    assert dish_slug_for_query("  PIZZA ") == "dish-pizza"


def test_dish_slug_substring_fallback():
    assert dish_slug_for_query("paneer tikka") == "dish-paneer"
    assert dish_slug_for_query("masala chai") == "dish-tea"


def test_dish_slug_no_match():
    assert dish_slug_for_query("sushi burrito xyzzy") == "dish-sushi"  # substring
    assert dish_slug_for_query("") is None
    assert dish_slug_for_query("qwertyuiop") is None


def test_dish_display_name():
    assert dish_display_name("dish-chicken-biryani") == "Chicken Biryani"
    assert dish_display_name("dish-tea") == "Tea"


def test_nearest_city_slug():
    assert nearest_city_slug(28.61, 77.21) == "ncr"
    assert nearest_city_slug(19.07, 72.87) == "mumbai"
    assert nearest_city_slug(12.97, 77.59) == "bangalore"
    assert nearest_city_slug(17.38, 78.48) == "hyderabad"


# -- parsing -------------------------------------------------------------------

def test_price_and_eta_helpers():
    assert _price_from_cfo("₹250 for one") == 250.0
    assert _price_from_cfo("₹1,150 for one") == 1150.0
    assert _price_from_cfo("") is None
    assert _price_from_cfo(None) is None
    assert _price_from_cfo("for one") is None
    assert _eta_from_delivery_time("42 min") == 42
    assert _eta_from_delivery_time("") is None
    assert _eta_from_delivery_time(None) is None


def test_parse_search_page_shape():
    raws = parse_search_page(SAMPLE_HTML, "Biryani")
    assert len(raws) == 2  # unserviceable / priceless / ad dropped
    first, second = raws
    assert first["res_id"] == "18382360"
    assert first["name"] == "Local"
    assert first["price_inr"] == 250.0
    assert first["delivery_rating"] == 4.3
    assert first["delivery_time_min"] == 42
    assert first["locality"] == "Connaught Place, New Delhi"
    assert second["price_inr"] == 1150.0
    assert second["delivery_time_min"] == 55


def test_parse_search_page_defensive():
    assert parse_search_page("", "Biryani") == []
    assert parse_search_page(None, "Biryani") == []
    assert parse_search_page("<html>no marker here</html>", "Biryani") == []
    assert parse_search_page("<html>SECTION_SEARCH_RESULT: [broken", "Biryani") == []


# -- adapter behaviour ---------------------------------------------------------

def test_search_maps_to_models():
    stub = StubTransport(SAMPLE_HTML)
    adapter = RealZomatoAdapter(transport=stub)
    products = run(adapter.search("biryani", 28.61, 77.21, _session()))
    assert stub.calls == [("ncr", "dish-biryani")]
    assert len(products) == 2
    p = products[0]
    assert p.gtin == "zomato:18382360"
    assert p.brand == "Local"
    assert p.name == "Biryani @ Local"
    assert (p.pack_size, p.unit) == (1.0, "serving")
    assert p.pack_label == "cost for one (Zomato)"
    snap = run(adapter.get_price(p.gtin, _session()))
    assert snap.price == 250.0
    assert snap.mrp == 250.0
    assert snap.per_unit_price == 250.0
    assert snap.in_stock is True
    assert snap.eta_minutes == 42
    assert snap.freshness == "live"


def test_search_unknown_dish_returns_empty_without_fetch():
    stub = StubTransport(SAMPLE_HTML)
    adapter = RealZomatoAdapter(transport=stub)
    assert run(adapter.search("qwertyuiop", 28.61, 77.21, _session())) == []
    assert stub.calls == []  # no page fetched for an unmappable query


def test_transport_failure_degrades_to_empty():
    class Boom(StubTransport):
        async def fetch_page(self, city_slug, dish_slug):
            raise RuntimeError("network down")

    adapter = RealZomatoAdapter(transport=Boom())
    assert run(adapter.search("biryani", 28.61, 77.21, _session())) == []


def test_get_price_unknown_raises():
    adapter = RealZomatoAdapter(transport=StubTransport(SAMPLE_HTML))
    with pytest.raises(PriceUnavailableError):
        run(adapter.get_price("zomato:nope", _session()))


def test_linking_raises_not_implemented():
    adapter = RealZomatoAdapter(transport=StubTransport())
    with pytest.raises(NotImplementedError):
        run(adapter.link_account("+911234567890"))
    with pytest.raises(NotImplementedError):
        run(adapter.verify_otp("ref", "123456"))


def test_health_uses_transport_probe():
    adapter = RealZomatoAdapter(transport=StubTransport(SAMPLE_HTML))
    status = run(adapter.health())
    assert status.status == "live"
    assert status.platform_id == "zomato"
    assert "2 restaurants" in status.message


def test_health_down_when_transport_fails():
    class Boom(StubTransport):
        async def fetch_page(self, city_slug, dish_slug):
            raise RuntimeError("boom")

    adapter = RealZomatoAdapter(transport=Boom())
    status = run(adapter.health())
    assert status.status == "down"
    assert "boom" in status.message


def test_health_cached_within_ttl():
    calls = []

    class Counting(StubTransport):
        async def fetch_page(self, city_slug, dish_slug):
            calls.append((city_slug, dish_slug))
            return await super().fetch_page(city_slug, dish_slug)

    adapter = RealZomatoAdapter(transport=Counting(SAMPLE_HTML))
    run(adapter.health())
    run(adapter.health())
    assert len(calls) == 1


def test_vertical_and_fee_defaults():
    adapter = RealZomatoAdapter(transport=StubTransport())
    assert adapter.vertical == "food"
    assert adapter.platform_id == "zomato"
    assert adapter.uses_static_catalog is False
    assert adapter.fee_table.delivery_fee_base == 30.0


def test_direct_transport_is_lazy_import_safe():
    t = DirectTransport()
    assert isinstance(t, DirectTransport)
