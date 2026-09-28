"""Tests for the real Zepto adapter (adapters/real_zepto.py).

All tests are offline: parsing fixtures, a StubTransport, and a
MockHttpTransport that exercises the real request construction (headers,
bodies, store resolution, pagination) against canned HTTP bodies.
"""
import asyncio
import os

import pytest

from adapters import all_adapters, all_mock_adapters
from adapters.real_zepto import (
    MARKETPLACE,
    DirectTransport,
    MockHttpTransport,
    RealZeptoAdapter,
    StubTransport,
    parse_pack_size,
    parse_search_response,
    parse_serviceability,
    split_brand,
    tier_price,
)
from models import PriceUnavailableError, UserSession, utcnow

SERVICEABILITY_JSON = {
    "data": {
        "serviceable": True,
        "stores": [
            {"storeId": "secondary-1", "serviceable": True,
             "storeConstruct": "SECONDARY"},
            {"storeId": "primary-9", "serviceable": True,
             "storeConstruct": "PRIMARY"},
        ],
    }
}

SEARCH_PAGE_1 = {
    "layout": {"widgets": [{"data": {"items": [
        {"productResponse": {
            "product": {"id": "p1", "name": "Amul Taaza Toned Fresh Milk",
                        "brand": "Amul"},
            "productVariant": {"id": "v1", "formattedPacksize": "500 ml",
                               "mrp": 3000},
            "mrp": 3000,
            "pricingData": {"pricingEntityPrices": [
                {"pricingEntity": "SUPER_SAVER", "discountedSellingPrice": 2700},
                {"pricingEntity": "ZEPTO_NOW", "discountedSellingPrice": 2800}]},
            "superSaverSellingPrice": 2750,
            "outOfStock": False,
            "cached": False,
        }},
        {"productResponse": {
            "product": {"id": "p2", "name": "Amul Gold Full Cream Milk",
                        "brand": "Amul"},
            "productVariant": {"id": "v2", "formattedPacksize": "1 L",
                               "mrp": 7200},
            "mrp": 7200,
            "discountedSellingPrice": 6800,
            "outOfStock": True,          # dropped by the adapter
            "cached": False,
        }},
        {"productResponse": {
            "product": {"id": "p3", "name": "Mother Dairy Cow Milk",
                        "brand": "Mother Dairy"},
            "productVariant": {"id": "v3", "formattedPacksize": "500 ml",
                               "mrp": 3100},
            "mrp": 3100,
            "discountedSellingPrice": 2950,
            "outOfStock": False,
            "cached": True,              # search-index cache -> "est"
        }},
        {"not_a_product": True},          # skipped
    ]}}]}
}

SEARCH_PAGE_2 = {"layout": {"widgets": []}}


def _raws():
    return parse_search_response(SEARCH_PAGE_1)


def _session() -> UserSession:
    return UserSession(platform_id="zepto", phone="guest",
                       token="guest", linked_at=utcnow())


def run(coro):
    return asyncio.run(coro)


# -- parsing ---------------------------------------------------------------

def test_parse_pack_size():
    assert parse_pack_size("500 ml") == (500.0, "ml", "500 ml")
    assert parse_pack_size("1 L") == (1000.0, "ml", "1 L")
    assert parse_pack_size("5 kg") == (5000.0, "g", "5 kg")
    assert parse_pack_size("12 pcs") == (12.0, "pcs", "12 pcs")
    size, unit, _ = parse_pack_size("mystery")
    assert (size, unit) == (1.0, "pcs")


def test_parse_serviceability_picks_primary():
    assert parse_serviceability(SERVICEABILITY_JSON) == "primary-9"
    assert parse_serviceability({"data": {"serviceable": False}}) is None
    assert parse_serviceability({"data": {"serviceable": True,
                                          "stores": []}}) is None
    assert parse_serviceability(None) is None


def test_tier_price_prefers_structured_tier():
    pr = SEARCH_PAGE_1["layout"]["widgets"][0]["data"]["items"][0]["productResponse"]
    price, source = tier_price(pr, "SUPER_SAVER")
    assert price == 27.0            # pricingData tier, not superSaverSellingPrice
    assert source == "pricingData:SUPER_SAVER"
    price, _ = tier_price(pr, "ZEPTO_NOW")
    assert price == 28.0


def test_tier_price_falls_back():
    pr = SEARCH_PAGE_1["layout"]["widgets"][0]["data"]["items"][2]["productResponse"]
    price, source = tier_price(pr, "SUPER_SAVER")
    assert price == 29.5
    assert source == "discountedSellingPrice"


def test_parse_search_response_shape():
    raws = _raws()
    assert len(raws) == 3
    assert raws[0]["variant_id"] == "v1"
    assert raws[0]["price"] == 27.0
    assert raws[0]["mrp"] == 30.0
    assert raws[0]["pack_text"] == "500 ml"
    assert raws[1]["out_of_stock"] is True
    assert raws[2]["cached"] is True
    assert parse_search_response(None) == []
    assert parse_search_response({}) == []


def test_split_brand():
    assert split_brand("Amul Taaza Toned Fresh Milk") == ("Amul", "Taaza Toned Fresh Milk")


# -- mock-HTTP transport (real request/response flow, no network) -----------

def test_mock_http_transport_headers_and_body():
    async def main():
        t = MockHttpTransport(SERVICEABILITY_JSON, [SEARCH_PAGE_1])
        raws = await t.search_raw("milk", 28.6, 77.2)
        assert len(raws) == 3
        search_headers = t.request_headers[0]
        assert search_headers["tenant"] == "ZEPTO"
        assert search_headers["x-without-bearer"] == "true"
        assert search_headers["marketplace_type"] == MARKETPLACE
        assert search_headers["store_id"] == "primary-9"
        assert search_headers["store_etas"] == '{"primary-9":10}'
        body = t.request_bodies[0]
        assert body["query"] == "milk"
        assert body["mode"] == "AUTOSUGGEST"
        assert body["pageNumber"] == 0
    run(main())


def test_mock_http_transport_unserviceable_raises():
    async def main():
        t = MockHttpTransport({"data": {"serviceable": False}}, [SEARCH_PAGE_1])
        with pytest.raises(RuntimeError, match="no serviceable store"):
            await t.search_raw("milk", 28.6, 77.2)
    run(main())


# -- adapter behaviour (stub transport, no network) -------------------------

def test_search_maps_to_models_and_drops_sold_out():
    async def main():
        adapter = RealZeptoAdapter(transport=StubTransport(_raws()))
        products = await adapter.search("milk", 28.6139, 77.2090, _session())
        assert len(products) == 2                  # out-of-stock dropped
        p = products[0]
        assert p.gtin == "zepto:v1"               # namespaced, no GTIN collision
        assert p.brand == "Amul"
        assert p.pack_size == 500.0 and p.unit == "ml"
        assert p.mrp == 30.0
    run(main())


def test_freshness_honest_about_search_cache():
    async def main():
        adapter = RealZeptoAdapter(transport=StubTransport(_raws()))
        await adapter.search("milk", 28.6139, 77.2090, _session())
        live = await adapter.get_price("zepto:v1", _session())
        assert live.freshness == "live"           # cached=false
        stale = await adapter.get_price("zepto:v3", _session())
        assert stale.freshness == "est"           # cached=true: never "live"
        assert stale.price == 29.5
    run(main())


def test_get_price_serves_cached_snapshot():
    async def main():
        adapter = RealZeptoAdapter(transport=StubTransport(_raws()))
        await adapter.search("milk", 28.6139, 77.2090, _session())
        snap = await adapter.get_price("zepto:v1", _session())
        assert snap.price == 27.0
        assert snap.mrp == 30.0
        assert snap.per_unit_price == pytest.approx(27.0 / 500.0)
        assert snap.in_stock is True
    run(main())


def test_get_price_unknown_raises():
    async def main():
        adapter = RealZeptoAdapter(transport=StubTransport(_raws()))
        await adapter.search("milk", 28.6139, 77.2090, _session())
        with pytest.raises(PriceUnavailableError):
            await adapter.get_price("zepto:v2", _session())   # sold out: never cached
        with pytest.raises(PriceUnavailableError):
            await adapter.get_price("8901010000116", _session())
    run(main())


def test_linking_raises_not_implemented():
    async def main():
        adapter = RealZeptoAdapter(transport=StubTransport())
        with pytest.raises(NotImplementedError):
            await adapter.link_account("+919999999999")
        with pytest.raises(NotImplementedError):
            await adapter.verify_otp("ref", "123456")
    run(main())


def test_health_uses_transport_probe():
    async def main():
        adapter = RealZeptoAdapter(transport=StubTransport(_raws()))
        status = await adapter.health()
        assert status.platform_id == "zepto"
        assert status.status == "live"
        assert "probe" in status.message
    run(main())


def test_health_down_when_transport_fails():
    async def main():
        class Boom(StubTransport):
            async def search_raw(self, query, lat, lon):
                raise RuntimeError("network down")
        adapter = RealZeptoAdapter(transport=Boom())
        status = await adapter.health()
        assert status.status == "down"
    run(main())


def test_direct_transport_header_contract():
    h = DirectTransport.headers("store-1", 28.6, 77.2)
    assert h["x-without-bearer"] == "true"
    assert h["tenant"] == "ZEPTO"
    assert h["platform"] == "WEB"
    body = DirectTransport.search_body("milk", 0)
    assert body["mode"] == "AUTOSUGGEST"
    assert body["pageNumber"] == 0
    assert body["query"] == "milk"


# -- registry: the env flag can never break boot ----------------------------

def test_flag_off_everything_behaves_as_before():
    env = os.environ.pop("SAUDA_REAL_ZEPTO", None)
    try:
        adapters = all_adapters()
        assert type(adapters["zepto"]).__name__ == "MockZeptoAdapter"
    finally:
        if env is not None:
            os.environ["SAUDA_REAL_ZEPTO"] = env


def test_flag_set_never_breaks_boot():
    os.environ["SAUDA_REAL_ZEPTO"] = "1"
    try:
        adapters = all_adapters()   # wiring is the coordinator's job;
        assert "zepto" in adapters  # this must not raise, ever
    finally:
        del os.environ["SAUDA_REAL_ZEPTO"]


def test_mock_registry_unchanged():
    assert set(all_mock_adapters()) == {"blinkit", "zepto", "instamart", "flipkart_minutes"}
