"""Tests for the real Swiggy Instamart adapter (adapters/real_instamart.py).

All tests are offline: parsing fixtures, a StubTransport, and an
InstamartSession driven by a fake HTTP `call` that returns canned bodies —
this exercises the real geocode -> pin-store -> paginated-search flow
(with its header and WAF handling) without any network.
"""
import asyncio
import json
import os

import pytest

from adapters import all_adapters, all_mock_adapters
from adapters.real_instamart import (
    BlockedError,
    InstamartSession,
    RealInstamartAdapter,
    StubTransport,
    extract_store_id,
    money_to_rupees,
    parse_address_widgets,
    parse_geocode_suggestions,
    parse_pack_size,
    parse_page_cursors,
    parse_search_payload,
    playwright_available,
    variation_in_stock,
)
from models import PriceUnavailableError, UserSession, utcnow

# --------------------------------------------------------------------------
# Fixtures (shaped like the real Swiggy payloads)
# --------------------------------------------------------------------------

SUGGESTIONS = {
    "data": [{"place_id": "ChIJN1t_tDeuEmsRUsoyG83frY4",
              "description": "Indiranagar, Bengaluru 560038"}]
}

ADDRESS_WIDGETS = {
    "data": {"address": {
        "location": {"latitude": 12.9784, "longitude": 77.6408},
        "metadata": {"formattedAddress": "Indiranagar, Bengaluru 560038"}}}
}

HOME_TEXT = (
    '{"payload": "x", "cta": "swiggy://instamart?storeId=1394450", '
    '"alt": "swiggy://instamart?storeId=1394450", '
    '"other": "swiggy://instamart?storeId=777"}'
)


def _search_page(page_offset=None, results_offset="32"):
    data = {
        "cards": [{
            "card": {"card": {"gridElements": {"infoWithStyle": {"items": [
                {"productId": "100",
                 "displayName": "Amul Taaza Milk",
                 "brand": "Amul",
                 "variations": [
                     {"skuId": "sku1",
                      "displayName": "Amul Taaza Toned Milk",
                      "brandName": "Amul",
                      "quantityDescription": "500 ml",
                      "price": {"mrp": {"units": "30", "nanos": 0},
                                "offerPrice": {"units": "27",
                                               "nanos": 500000000}},
                      "inventory": {"inStock": True},
                      "cartAllowedQuantity": {"allowedQuantity": 10},
                      "imageIds": ["img1"]}]},
                {"productId": "101",
                 "displayName": "Bread",
                 "variations": [
                     {"skuId": "sku2",
                      "displayName": "Britannia Bread",
                      "brandName": "Britannia",
                      "quantityDescription": "400 g",
                      "price": {"mrp": {"units": "45"},
                                "offerPrice": {"units": "40"}},
                      "inventory": {"inStock": True},
                      "cartAllowedQuantity": {"allowedQuantity": 0}}]},
                {"productId": "102",
                 "displayName": "NoPrice",
                 "variations": [
                     {"skuId": "sku3", "displayName": "Mystery",
                      "price": {},
                      "inventory": {"inStock": True}}]},
            ]}}}}}
        ],
        "searchResultsOffset": results_offset,
    }
    if page_offset is not None:
        data["pageOffset"] = {"nextOffset": page_offset}
    return {"data": data}


def run(coro):
    return asyncio.run(coro)


def _session() -> UserSession:
    return UserSession(platform_id="instamart", phone="guest",
                       token="guest", linked_at=utcnow())


# -- parsing ---------------------------------------------------------------

def test_money_to_rupees():
    assert money_to_rupees({"units": "27", "nanos": 500000000}) == 27.5
    assert money_to_rupees({"units": "45"}) == 45.0
    assert money_to_rupees(None) is None


def test_parse_pack_size():
    assert parse_pack_size("500 ml") == (500.0, "ml", "500 ml")
    assert parse_pack_size("400 g") == (400.0, "g", "400 g")
    assert parse_pack_size("1 L") == (1000.0, "ml", "1 L")
    assert parse_pack_size("mystery") == (1.0, "pcs", "mystery")


def test_variation_in_stock_exactness():
    assert variation_in_stock({"inventory": {"inStock": True},
                               "cartAllowedQuantity": {"allowedQuantity": 5}}) is True
    # cartAllowedQuantity == 0 means unbuyable even when inventory says inStock
    assert variation_in_stock({"inventory": {"inStock": True},
                               "cartAllowedQuantity": {"allowedQuantity": 0}}) is False
    assert variation_in_stock({"inventory": {"inStock": False}}) is False
    assert variation_in_stock({}) is False       # missing inventory -> not buyable


def test_extract_store_id_most_common():
    assert extract_store_id(HOME_TEXT) == "1394450"
    assert extract_store_id("no deeplink here") is None


def test_parse_geocode_and_address_widgets():
    assert parse_geocode_suggestions(SUGGESTIONS) == "ChIJN1t_tDeuEmsRUsoyG83frY4"
    assert parse_geocode_suggestions(None) is None
    place = parse_address_widgets(ADDRESS_WIDGETS)
    assert place == {"lat": 12.9784, "lng": 77.6408,
                     "address": "Indiranagar, Bengaluru 560038"}


def test_parse_search_payload():
    raws = parse_search_payload(_search_page())
    assert len(raws) == 2          # NoPrice variation has no price -> skipped
    assert raws[0]["sku_id"] == "sku1"
    assert raws[0]["price"] == 27.5
    assert raws[0]["mrp"] == 30.0
    assert raws[0]["in_stock"] is True
    assert raws[1]["in_stock"] is False   # allowedQuantity == 0 -> unbuyable
    assert parse_search_payload(None) == []


def test_parse_page_cursors():
    assert parse_page_cursors(_search_page(1)) == (1, "32")
    assert parse_page_cursors(_search_page()) == (None, "")
    assert parse_page_cursors(None) == (None, "")


# -- session flow over a fake HTTP call (no network) ------------------------

class FakeResp:
    def __init__(self, status, payload=None, text=None):
        self.status = status
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        return json.loads(self.text)


def _fake_call(responses):
    calls = []

    async def call(method, url, params=None, body=None, headers=None):
        calls.append({"method": method, "url": url, "params": params,
                      "body": body, "headers": headers})
        resp = responses.pop(0)
        return resp

    return call, calls


def test_session_full_flow():
    async def main():
        call, calls = _fake_call([
            FakeResp(200, SUGGESTIONS),
            FakeResp(200, ADDRESS_WIDGETS),
            FakeResp(200, text=HOME_TEXT),
            FakeResp(200, _search_page(1)),
            FakeResp(200, _search_page()),
        ])
        session = InstamartSession(call)
        place = await session.geocode("560038")
        assert place["lat"] == 12.9784
        store_id = await session.pin_store(place)
        assert store_id == "1394450"
        pages = []
        async for items in session.search_pages(store_id, "milk"):
            pages.extend(items)
        assert len(pages) == 4   # 2 sellable variations per page x 2 pages
        # request headers carry the browser build/device fingerprint
        for c in calls:
            assert "x-build-version" in c["headers"]
            assert "x-device-id" in c["headers"]
        search_calls = [c for c in calls if c["url"].endswith("/search/v2")]
        assert len(search_calls) == 2
        first = search_calls[0]
        assert first["params"]["storeId"] == "1394450"
        assert first["params"]["primaryStoreId"] == "1394450"
        assert first["body"]["page_type"] == "INSTAMART_SEARCH_PAGE"
        assert first["body"]["query"] == "milk"
        # second page continues BOTH cursors (offset + results_offset)
        assert search_calls[1]["params"]["offset"] == 1
        assert search_calls[1]["body"]["search_results_offset"] == "32"
    run(main())


def test_session_waf_challenge_is_blocked_error():
    async def main():
        call, _ = _fake_call([FakeResp(202, text="")] )  # plain-HTTP WAF page
        session = InstamartSession(call)
        with pytest.raises(BlockedError):
            await session.geocode("560038")
    run(main())


def test_session_non_json_is_blocked_error():
    async def main():
        call, _ = _fake_call([FakeResp(200, text="<html>challenge</html>")])
        session = InstamartSession(call)
        with pytest.raises(BlockedError):
            await session.geocode("560038")
    run(main())


def test_session_no_location_match():
    async def main():
        call, _ = _fake_call([FakeResp(200, {"data": []})])
        session = InstamartSession(call)
        with pytest.raises(LookupError):
            await session.geocode("nowhere")
    run(main())


# -- adapter behaviour (stub transport, no network) -------------------------

def _stub_raws():
    return parse_search_payload(_search_page())


def test_search_maps_to_models_and_drops_unbuyable():
    async def main():
        adapter = RealInstamartAdapter(transport=StubTransport(_stub_raws()))
        products = await adapter.search("milk", 12.9784, 77.6408, _session())
        assert len(products) == 1                 # unbuyable variation dropped
        p = products[0]
        assert p.gtin == "instamart:sku1"         # namespaced, no GTIN collision
        assert p.brand == "Amul"
        assert p.pack_size == 500.0 and p.unit == "ml"
        assert p.mrp == 30.0
    run(main())


def test_get_price_serves_cached_snapshot():
    async def main():
        adapter = RealInstamartAdapter(transport=StubTransport(_stub_raws()))
        await adapter.search("milk", 12.9784, 77.6408, _session())
        snap = await adapter.get_price("instamart:sku1", _session())
        assert snap.price == 27.5
        assert snap.per_unit_price == pytest.approx(27.5 / 500.0)
        assert snap.in_stock is True
        assert snap.freshness == "live"
    run(main())


def test_get_price_unknown_raises():
    async def main():
        adapter = RealInstamartAdapter(transport=StubTransport(_stub_raws()))
        await adapter.search("milk", 12.9784, 77.6408, _session())
        with pytest.raises(PriceUnavailableError):
            await adapter.get_price("instamart:sku2", _session())
        with pytest.raises(PriceUnavailableError):
            await adapter.get_price("8901010000116", _session())
    run(main())


def test_linking_raises_not_implemented():
    async def main():
        adapter = RealInstamartAdapter(transport=StubTransport())
        with pytest.raises(NotImplementedError):
            await adapter.link_account("+919999999999")
        with pytest.raises(NotImplementedError):
            await adapter.verify_otp("ref", "123456")
    run(main())


def test_health_uses_transport_probe():
    async def main():
        adapter = RealInstamartAdapter(transport=StubTransport(_stub_raws()))
        status = await adapter.health()
        assert status.platform_id == "instamart"
        assert status.status == "live"
    run(main())


def test_health_down_when_transport_fails():
    async def main():
        class Boom(StubTransport):
            async def search_raw(self, query, lat, lon):
                raise RuntimeError("network down")
        adapter = RealInstamartAdapter(transport=Boom())
        status = await adapter.health()
        assert status.status == "down"
    run(main())


def test_playwright_available_is_bool():
    assert isinstance(playwright_available(), bool)


# -- registry: the env flag can never break boot ----------------------------

def test_flag_off_everything_behaves_as_before():
    env = os.environ.pop("SAUDA_REAL_INSTAMART", None)
    try:
        adapters = all_adapters()
        assert type(adapters["instamart"]).__name__ == "MockInstamartAdapter"
    finally:
        if env is not None:
            os.environ["SAUDA_REAL_INSTAMART"] = env


def test_flag_set_never_breaks_boot():
    os.environ["SAUDA_REAL_INSTAMART"] = "1"
    try:
        adapters = all_adapters()   # wiring is the coordinator's job;
        assert "instamart" in adapters  # this must not raise, ever
    finally:
        del os.environ["SAUDA_REAL_INSTAMART"]


def test_mock_registry_unchanged():
    assert set(all_mock_adapters()) == {"blinkit", "zepto", "instamart", "flipkart_minutes"}
