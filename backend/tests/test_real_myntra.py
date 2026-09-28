"""Tests for the real Myntra adapter (adapters/real_myntra.py).

All tests use StubTransport — no network, no browser. The fixture below is a
hand-written sample following the verified /gateway/v2/search response shape
(products[] with productId, productName, brand, price, mrp, landingPageUrl).
"""
import asyncio

import pytest

from adapters.real_myntra import (
    RealMyntraAdapter,
    StubTransport,
    extract_variant_attributes,
    parse_search_response,
)
from models import PriceUnavailableError, UserSession, utcnow

SAMPLE_SEARCH_JSON = {
    "totalCount": 3,
    "products": [
        {
            "productId": 12345678,
            "productName": "Nike Men Black Running Shoes",
            "brand": "Nike",
            "price": 4995.0,
            "mrp": 9995.0,
            "discount": 50,
            "rating": 4.3,
            "ratingCount": 1204,
            "searchImage": "https://assets.myntra.com/nike.png",
            "landingPageUrl": "sports-shoes/nike/nike-men-black-running-shoes/12345678/buy",
            "sizes": ["UK 7", "UK 8", "UK 9"],
            "systemAttributes": [
                {"attribute": "Gender", "value": "Men"},
                {"attribute": "Colour", "value": "Black"},
            ],
        },
        {
            "productId": 87654321,
            "productName": "Adidas Men White Sneakers",
            "brand": "Adidas",
            "price": 2999.0,
            "mrp": 4999.0,
            "discount": 40,
            "landingPageUrl": "casual-shoes/adidas/adidas-men-white-sneakers/87654321/buy",
            "sizes": ["UK 8"],
            "isPLA": True,   # sponsored -> marked ambiguous by the adapter
        },
        {
            "productId": 11111111,
            "productName": "Broken Card, No Price",   # no price -> skipped
            "brand": "Broken",
            "landingPageUrl": "x/11111111/buy",
        },
    ],
}


def _session() -> UserSession:
    return UserSession(platform_id="myntra", phone="guest",
                       token="tok", linked_at=utcnow())


def run(coro):
    return asyncio.run(coro)


# -- parsing ---------------------------------------------------------------

def test_parse_search_response_shape():
    raws = parse_search_response(SAMPLE_SEARCH_JSON)
    assert len(raws) == 2                      # broken card skipped
    nike = raws[0]
    assert nike["product_id"] == 12345678
    assert nike["price"] == 4995.0
    assert nike["mrp"] == 9995.0
    assert nike["brand"] == "Nike"
    assert nike["sizes"] == ["UK 7", "UK 8", "UK 9"]
    assert nike["sponsored"] is False
    assert raws[1]["sponsored"] is True
    assert parse_search_response(None) == []
    assert parse_search_response({}) == []


def test_extract_variant_attributes():
    attrs = extract_variant_attributes(
        [{"attribute": "Gender", "value": "Men"},
         {"attribute": "Colour", "value": "Black"}],
        ["UK 7", "UK 8"])
    assert attrs["gender"] == "Men"
    assert attrs["colour"] == "Black"
    assert attrs["size_options"] == "UK 7, UK 8"
    assert extract_variant_attributes([], []) == {}


# -- adapter behaviour (stub transport, no network) -------------------------

def test_search_maps_to_models_and_caches_identity():
    async def main():
        adapter = RealMyntraAdapter(transport=StubTransport(SAMPLE_SEARCH_JSON))
        products = await adapter.search("running shoes", 28.6139, 77.2090, _session())
        assert len(products) == 2
        p = products[0]
        assert p.gtin == "myntra:12345678"        # namespaced, no GTIN collision
        assert p.brand == "Nike"
        assert p.pack_size == 1.0 and p.unit == "pcs"
        assert p.mrp == 9995.0
        ident = adapter.identity("myntra:12345678")
        assert ident is not None
        assert ident.brand == "Nike"
        assert ident.variant_attributes["colour"] == "Black"
        assert ident.variant_attributes["size_options"] == "UK 7, UK 8, UK 9"
        assert ident.platform_product_id == "12345678"
        assert ident.ambiguous is False
        assert ident.delivery_fee is None       # not disclosed at search level
        assert ident.price_effective == 4995.0
        assert ident.canonical_url.startswith("https://www.myntra.com/")
    run(main())


def test_sponsored_card_marked_ambiguous():
    async def main():
        adapter = RealMyntraAdapter(transport=StubTransport(SAMPLE_SEARCH_JSON))
        await adapter.search("sneakers", 28.6139, 77.2090, _session())
        ident = adapter.identity("myntra:87654321")
        assert ident is not None
        assert ident.ambiguous is True
        assert ident.confidence == "low"
        assert "sponsored" in ident.ambiguous_reason.lower()
    run(main())


def test_identity_unknown_returns_none():
    async def main():
        adapter = RealMyntraAdapter(transport=StubTransport(SAMPLE_SEARCH_JSON))
        await adapter.search("sneakers", 28.6139, 77.2090, _session())
        assert adapter.identity("myntra:000") is None
    run(main())


def test_get_price_serves_cached_snapshot():
    async def main():
        adapter = RealMyntraAdapter(transport=StubTransport(SAMPLE_SEARCH_JSON))
        await adapter.search("shoes", 28.6139, 77.2090, _session())
        snap = await adapter.get_price("myntra:12345678", _session())
        assert snap.price == 4995.0
        assert snap.mrp == 9995.0
        assert snap.freshness == "live"         # listed price; delivery fee not disclosed
    run(main())


def test_get_price_unknown_raises():
    async def main():
        adapter = RealMyntraAdapter(transport=StubTransport(SAMPLE_SEARCH_JSON))
        await adapter.search("shoes", 28.6139, 77.2090, _session())
        with pytest.raises(PriceUnavailableError):
            await adapter.get_price("myntra:11111111", _session())  # no price: never cached
    run(main())


def test_linking_raises_not_implemented():
    async def main():
        adapter = RealMyntraAdapter(transport=StubTransport())
        with pytest.raises(NotImplementedError):
            await adapter.link_account("+919999999999")
        with pytest.raises(NotImplementedError):
            await adapter.verify_otp("ref", "123456")
    run(main())


def test_health_uses_transport_probe():
    async def main():
        adapter = RealMyntraAdapter(transport=StubTransport(SAMPLE_SEARCH_JSON))
        status = await adapter.health()
        assert status.platform_id == "myntra"
        assert status.status == "live"
        assert "probe" in status.message
    run(main())


def test_health_down_when_transport_fails():
    async def main():
        class Boom(StubTransport):
            async def search_raw(self, query):
                raise RuntimeError("network down")
        adapter = RealMyntraAdapter(transport=Boom())
        status = await adapter.health()
        assert status.status == "down"
    run(main())


def test_adapter_metadata():
    adapter = RealMyntraAdapter(transport=StubTransport())
    assert adapter.platform_id == "myntra"
    assert adapter.vertical == "ecommerce"
    assert adapter.uses_static_catalog is False
