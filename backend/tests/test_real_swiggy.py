"""Tests for the real Swiggy adapter (adapters/real_swiggy.py).

All tests use StubTransport — no network. The fixture below is a hand-written
sample following the verified /dapi/restaurants/search/v3 response shape
(groupedCard.cardGroupMap.DISH.cards[] with DishGroup cards; prices in paise).
"""
import asyncio

import pytest

from adapters.real_swiggy import (
    DirectTransport,
    RealSwiggyAdapter,
    StubTransport,
    dish_price_inr,
    parse_search_response,
)
from models import PriceUnavailableError, UserSession, utcnow


def _dish_group(rest_id, rest_name, dishes, delivery_time=42):
    return {
        "card": {
            "card": {
                "@type": "type.googleapis.com/swiggy.presentation.food.v2.DishGroup",
                "restaurant": {
                    "info": {
                        "id": rest_id,
                        "name": rest_name,
                        "avgRating": 4.3,
                        "sla": {"deliveryTime": delivery_time},
                        "costForTwo": 50000,
                        "locality": "Connaught Place",
                        "feeDetails": {},
                    }
                },
                "dishes": [{"info": d} for d in dishes],
            }
        }
    }


SAMPLE_SEARCH_JSON = {
    "statusCode": 0,
    "data": {
        "cards": [
            {"card": {"card": {"@type": "type.googleapis.com/swiggy.gandalf.widgets.v2.Navigation"}}},
            {
                "groupedCard": {
                    "cardGroupMap": {
                        "DISH": {
                            "cards": [
                                {"card": {"card": {"@type": "type.googleapis.com/swiggy.presentation.food.v2.SearchFilterSortWidget"}}},
                                _dish_group("51510", "Biryani By Kilo", [
                                    {"id": "d1", "name": "Chicken Dum Biryani",
                                     "price": 29900, "inStock": 1},
                                    {"id": "d2", "name": "Veg Biryani (sold out)",
                                     "price": 19900, "inStock": 0},  # dropped
                                    {"id": "d3", "name": "Mutton Biryani Family Pack",
                                     "inStock": 1,  # price via variant model
                                     "variantsV2": {"pricingModels": [
                                         {"price": 59900}, {"price": 114900}]}},
                                    {"id": "d4", "name": "Broken dish"},  # no price -> dropped
                                ]),
                                _dish_group("99999", "Curry House", [
                                    {"id": "d5", "name": "Chicken Curry",
                                     "defaultPrice": 24900, "inStock": 1},
                                ], delivery_time=35),
                                {"card": {"card": {"@type": "something.Else"}}},  # skipped
                            ]
                        }
                    }
                }
            },
        ]
    },
}


def _session() -> UserSession:
    return UserSession(platform_id="swiggy", phone="guest",
                       token="guest", linked_at=utcnow())


def run(coro):
    return asyncio.run(coro)


# -- parsing ---------------------------------------------------------------

def test_dish_price_paise_and_variants():
    assert dish_price_inr({"price": 29900}) == 299.0
    assert dish_price_inr({"variantsV2": {"pricingModels": [{"price": 59900}]}}) == 599.0
    assert dish_price_inr({"defaultPrice": 24900}) == 249.0
    assert dish_price_inr({"variantsV2": {"pricingModels": [{"price": 59900}]},
                           "price": 100}) == 599.0  # variant model wins
    assert dish_price_inr({}) is None
    assert dish_price_inr({"price": "299"}) is None  # wrong type -> None, not crash


def test_parse_search_response_shape():
    raws = parse_search_response(SAMPLE_SEARCH_JSON)
    assert len(raws) == 3  # d1, d3, d5; sold-out and broken dropped
    by_id = {r["dish_id"]: r for r in raws}
    assert by_id["d1"]["price_inr"] == 299.0
    assert by_id["d1"]["dish_name"] == "Chicken Dum Biryani"
    assert by_id["d1"]["restaurant"]["name"] == "Biryani By Kilo"
    assert by_id["d1"]["restaurant"]["delivery_time_min"] == 42
    assert by_id["d3"]["price_inr"] == 599.0  # default variant model
    assert by_id["d5"]["price_inr"] == 249.0  # defaultPrice fallback
    assert by_id["d5"]["restaurant"]["delivery_time_min"] == 35


def test_parse_search_response_defensive():
    assert parse_search_response(None) == []
    assert parse_search_response({}) == []
    assert parse_search_response({"statusCode": 7, "data": {}}) == []
    assert parse_search_response({"statusCode": 0}) == []
    assert parse_search_response({"statusCode": 0, "data": {"cards": "nope"}}) == []


# -- adapter behaviour -----------------------------------------------------

def test_search_maps_to_models():
    adapter = RealSwiggyAdapter(transport=StubTransport(SAMPLE_SEARCH_JSON))
    products = run(adapter.search("biryani", 28.6, 77.2, _session()))
    assert len(products) == 3
    p = products[0]
    assert p.gtin == "swiggy:51510:d1"
    assert p.brand == "Biryani By Kilo"
    assert p.name == "Chicken Dum Biryani @ Biryani By Kilo"
    assert (p.pack_size, p.unit, p.pack_label) == (1.0, "serving", "1 serving")
    snap = run(adapter.get_price(p.gtin, _session()))
    assert snap.price == 299.0
    assert snap.mrp == 299.0
    assert snap.per_unit_price == 299.0
    assert snap.in_stock is True
    assert snap.eta_minutes == 42
    assert snap.freshness == "live"


def test_get_price_unknown_raises():
    adapter = RealSwiggyAdapter(transport=StubTransport(SAMPLE_SEARCH_JSON))
    with pytest.raises(PriceUnavailableError):
        run(adapter.get_price("swiggy:nope:x", _session()))


def test_transport_failure_degrades_to_empty():
    class Boom(StubTransport):
        async def search_raw(self, query, lat, lon):
            raise RuntimeError("network down")

    adapter = RealSwiggyAdapter(transport=Boom())
    # A dead platform must not 500 /v1/compare: search degrades to [].
    assert run(adapter.search("biryani", 28.6, 77.2, _session())) == []


def test_search_records_transport_call():
    stub = StubTransport(SAMPLE_SEARCH_JSON)
    adapter = RealSwiggyAdapter(transport=stub)
    run(adapter.search("pizza", 12.9, 77.6, _session()))
    assert stub.calls == [("pizza", 12.9, 77.6)]


def test_linking_raises_not_implemented():
    adapter = RealSwiggyAdapter(transport=StubTransport())
    with pytest.raises(NotImplementedError):
        run(adapter.link_account("+911234567890"))
    with pytest.raises(NotImplementedError):
        run(adapter.verify_otp("ref", "123456"))


def test_health_uses_transport_probe():
    adapter = RealSwiggyAdapter(transport=StubTransport(SAMPLE_SEARCH_JSON))
    status = run(adapter.health())
    assert status.status == "live"
    assert status.platform_id == "swiggy"
    assert "3 dishes" in status.message


def test_health_down_when_transport_fails():
    class Boom(StubTransport):
        async def search_raw(self, query, lat, lon):
            raise RuntimeError("boom")

    adapter = RealSwiggyAdapter(transport=Boom())
    status = run(adapter.health())
    assert status.status == "down"
    assert "boom" in status.message


def test_health_cached_within_ttl():
    calls = []

    class Counting(StubTransport):
        async def search_raw(self, query, lat, lon):
            calls.append(query)
            return await super().search_raw(query, lat, lon)

    adapter = RealSwiggyAdapter(transport=Counting(SAMPLE_SEARCH_JSON))
    run(adapter.health())
    run(adapter.health())
    assert len(calls) == 1


def test_vertical_and_fee_defaults():
    adapter = RealSwiggyAdapter(transport=StubTransport())
    assert adapter.vertical == "food"
    assert adapter.platform_id == "swiggy"
    assert adapter.uses_static_catalog is False
    assert adapter.fee_table.delivery_fee_base == 30.0


def test_direct_transport_is_lazy_import_safe():
    # Constructing must not import httpx at module import time; the class
    # itself only needs to exist. (httpx import happens inside search_raw.)
    t = DirectTransport()
    assert isinstance(t, DirectTransport)
