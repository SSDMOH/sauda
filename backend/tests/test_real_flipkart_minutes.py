"""Tests for the real Flipkart Minutes adapter
(adapters/real_flipkart_minutes.py).

All tests are offline: parsing fixtures, a StubTransport, and a
MockHttpTransport that walks canned page/fetch payloads with the real
request construction (body shape, HYPERLOCAL marketplace, pagination)
without any network.
"""
import asyncio
import os

import pytest

from adapters import all_adapters
from adapters.real_flipkart_minutes import (
    HEADERS,
    PAGE_FETCH_URL,
    MockHttpTransport,
    RealFlipkartMinutesAdapter,
    StubTransport,
    build_body,
    parse_pack_size,
    parse_page_fetch_payload,
    parse_pagination_context,
    split_brand,
)
from models import PriceUnavailableError, UserSession, utcnow

# --------------------------------------------------------------------------
# Fixtures (shaped like a real 1.rome.api.flipkart.com page/fetch body)
# --------------------------------------------------------------------------

PRODUCT_CARD = {
    "productInfo": {"value": {
        "id": "MOBFK123", "listingId": "LST1",
        "titles": {"title": "Amul Taaza Toned Fresh Milk",
                   "superTitle": "Amul", "subtitle": "500 ml"},
        "pricing": {"mrp": {"value": 30}, "finalPrice": {"value": 27}},
        "availability": {"displayState": "IN_STOCK"},
        "productBrand": "Amul",
        "media": {"images": [{"url": "https://x/img.jpg"}]},
    }}
}

SOLD_OUT_CARD = {
    "productInfo": {"value": {
        "id": "MOBFK124", "listingId": "LST2",
        "titles": {"title": "Amul Gold Full Cream Milk",
                   "subtitle": "1 L"},
        "pricing": {"mrp": {"value": 72}, "finalPrice": {"value": 68}},
        "availability": {"displayState": "OUT_OF_STOCK"},
    }}
}

NO_PRICE_CARD = {
    "productInfo": {"value": {
        "id": "MOBFK125",
        "titles": {"title": "Nameless thing"},
        "pricing": {},
        "availability": {"displayState": "IN_STOCK"},
    }}
}

PAGE_1 = {"RESPONSE": {"slots": [
    {"widget": {"type": "PRODUCT_SUMMARY",
                "data": {"products": [PRODUCT_CARD, SOLD_OUT_CARD,
                                      NO_PRICE_CARD,
                                      {"widget": {"type": "FILTER_SORT_OPTIONS",
                                                  "data": {}}}]}}},
    {"widget": {"type": "PRODUCT_SUMMARY",
                "data": {"products": [PRODUCT_CARD]}}},  # dup: skipped
    {"widget": {"type": "FILTER_SORT_OPTIONS", "data": {}}},
], "pageData": {"paginationContextMap": {"q": "milk"}}}}

PAGE_2 = {"RESPONSE": {"slots": [
    {"widget": {"type": "PRODUCT_SUMMARY",
                "data": {"products": [PRODUCT_CARD]}}},
]}}  # no paginationContextMap -> walk stops

REDIRECT_PAGE = {"RESPONSE": {"pageMeta": {
    "redirectionObject": {"url": "/location?marketplace=HYPERLOCAL"}}}}


def run(coro):
    return asyncio.run(coro)


def _session() -> UserSession:
    return UserSession(platform_id="flipkart_minutes", phone="guest",
                       token="guest", linked_at=utcnow())


# -- parsing ---------------------------------------------------------------

def test_parse_pack_size():
    assert parse_pack_size("500 ml") == (500.0, "ml", "500 ml")
    assert parse_pack_size("1 L") == (1000.0, "ml", "1 L")
    assert parse_pack_size("5 kg") == (5000.0, "g", "5 kg")
    assert parse_pack_size("mystery") == (1.0, "pcs", "mystery")


def test_split_brand():
    assert split_brand("Amul Taaza Toned Fresh Milk") == ("Amul", "Taaza Toned Fresh Milk")


def test_parse_page_fetch_payload():
    raws = parse_page_fetch_payload(PAGE_1)
    assert len(raws) == 2                    # no-price card skipped, dup skipped
    first = raws[0]
    assert first["pid"] == "MOBFK123"
    assert first["name"] == "Amul Taaza Toned Fresh Milk"
    assert first["brand"] == "Amul"
    assert first["pack_text"] == "500 ml"
    assert first["price"] == 27.0
    assert first["mrp"] == 30.0
    assert first["in_stock"] is True
    assert first["image_url"] == "https://x/img.jpg"
    assert raws[1]["in_stock"] is False      # explicit OUT_OF_STOCK kept
    assert parse_page_fetch_payload(None) == []
    assert parse_page_fetch_payload({}) == []


def test_redirection_is_an_honest_error():
    with pytest.raises(RuntimeError, match="location gate"):
        parse_page_fetch_payload(REDIRECT_PAGE)


def test_parse_pagination_context():
    assert parse_pagination_context(PAGE_1) == {"q": "milk"}
    assert parse_pagination_context(PAGE_2) is None
    assert parse_pagination_context(None) is None


def test_build_body_hyperlocal_contract():
    body = build_body("milk", "110001", 1, None, "ssid" + "0" * 20)
    assert "/api/4/page/fetch" in PAGE_FETCH_URL
    assert "marketplace=HYPERLOCAL" in body["pageUri"]
    assert body["requestContext"]["type"] == "BROWSE_PAGE"
    assert len(body["requestContext"]["ssid"]) == 24
    assert body["locationContext"]["pincode"] == 110001
    body2 = build_body("milk", "560038", 2, {"q": "milk"}, "ssid" + "0" * 20)
    assert "page=2" in body2["pageUri"]
    assert body2["pageContext"]["paginatedFetch"] is True
    assert body2["pageContext"]["paginationContextMap"] == {"q": "milk"}


def test_header_contract():
    assert HEADERS["flipkart_secure"] == "true"
    assert HEADERS["origin"] == "https://www.flipkart.com"
    assert "FKUA/website/41/website/Desktop" in HEADERS["x-user-agent"]


# -- mock-HTTP transport (real request flow, no network) --------------------

def test_mock_http_transport_pagination_walk():
    async def main():
        t = MockHttpTransport([PAGE_1, PAGE_2])
        raws = await t.search_raw("milk", 28.6, 77.2)
        # page 1: Amul (dup collapsed) + sold-out Amul Gold; page 2: same item
        assert len(raws) == 3
        assert len(t.request_bodies) == 2
        assert "marketplace=HYPERLOCAL" in t.request_bodies[0]["pageUri"]
        assert "page=2" in t.request_bodies[1]["pageUri"]
        assert t.request_bodies[1]["pageContext"]["paginatedFetch"] is True
    run(main())


def test_mock_http_transport_stops_when_no_pagination():
    async def main():
        t = MockHttpTransport([PAGE_2])
        raws = await t.search_raw("milk", 28.6, 77.2)
        assert len(raws) == 1
        assert len(t.request_bodies) == 1     # no paginationContextMap -> one call
    run(main())


def test_mock_http_transport_empty_results():
    async def main():
        t = MockHttpTransport([{"RESPONSE": {"slots": []}}])
        assert await t.search_raw("xyz", 28.6, 77.2) == []
    run(main())


# -- adapter behaviour (stub transport, no network) -------------------------

def _stub_raws():
    return parse_page_fetch_payload(PAGE_1)


def test_search_maps_to_models_and_drops_sold_out():
    async def main():
        adapter = RealFlipkartMinutesAdapter(transport=StubTransport(_stub_raws()))
        products = await adapter.search("milk", 28.6139, 77.2090, _session())
        assert len(products) == 1                 # sold-out dropped
        p = products[0]
        assert p.gtin == "flipkart_minutes:MOBFK123"  # namespaced
        assert p.brand == "Amul"
        assert p.name == "Taaza Toned Fresh Milk"
        assert p.pack_size == 500.0 and p.unit == "ml"
        assert p.mrp == 30.0
    run(main())


def test_get_price_serves_cached_snapshot():
    async def main():
        adapter = RealFlipkartMinutesAdapter(transport=StubTransport(_stub_raws()))
        await adapter.search("milk", 28.6139, 77.2090, _session())
        snap = await adapter.get_price("flipkart_minutes:MOBFK123", _session())
        assert snap.price == 27.0
        assert snap.per_unit_price == pytest.approx(27.0 / 500.0)
        assert snap.in_stock is True
        assert snap.freshness == "live"
        assert snap.platform_id == "flipkart_minutes"
    run(main())


def test_get_price_unknown_raises():
    async def main():
        adapter = RealFlipkartMinutesAdapter(transport=StubTransport(_stub_raws()))
        await adapter.search("milk", 28.6139, 77.2090, _session())
        with pytest.raises(PriceUnavailableError):
            await adapter.get_price("flipkart_minutes:MOBFK124", _session())
        with pytest.raises(PriceUnavailableError):
            await adapter.get_price("8901010000116", _session())
    run(main())


def test_linking_raises_not_implemented():
    async def main():
        adapter = RealFlipkartMinutesAdapter(transport=StubTransport())
        with pytest.raises(NotImplementedError):
            await adapter.link_account("+919999999999")
        with pytest.raises(NotImplementedError):
            await adapter.verify_otp("ref", "123456")
    run(main())


def test_health_uses_transport_probe():
    async def main():
        adapter = RealFlipkartMinutesAdapter(transport=StubTransport(_stub_raws()))
        status = await adapter.health()
        assert status.platform_id == "flipkart_minutes"
        assert status.status == "live"
    run(main())


def test_health_down_when_transport_fails():
    async def main():
        class Boom(StubTransport):
            async def search_raw(self, query, lat, lon):
                raise RuntimeError("network down")
        adapter = RealFlipkartMinutesAdapter(transport=Boom())
        status = await adapter.health()
        assert status.status == "down"
    run(main())


# -- registry: the env flag can never break boot ----------------------------

def test_flag_off_everything_behaves_as_before():
    env = os.environ.pop("SAUDA_REAL_FLIPKART_MINUTES", None)
    try:
        adapters = all_adapters()
        # a mock now exists for flag-off consistency with the other platforms
        assert type(adapters["flipkart_minutes"]).__name__ == \
            "MockFlipkartMinutesAdapter"
    finally:
        if env is not None:
            os.environ["SAUDA_REAL_FLIPKART_MINUTES"] = env


def test_flag_set_never_breaks_boot():
    os.environ["SAUDA_REAL_FLIPKART_MINUTES"] = "1"
    try:
        adapters = all_adapters()   # wiring is the coordinator's job;
        assert isinstance(adapters, dict)  # this must not raise, ever
    finally:
        del os.environ["SAUDA_REAL_FLIPKART_MINUTES"]
