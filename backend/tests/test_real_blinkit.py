"""Tests for the real Blinkit adapter (adapters/real_blinkit.py).

All tests use StubTransport — no network, no browser. The fixture below is a
hand-written sample following the verified /v1/layout/search response shape
(product_card snippets with atc_action.add_to_cart.cart_item).
"""
import asyncio
import os

import pytest

from adapters import all_adapters, all_mock_adapters, any_real_adapters
from adapters.real_blinkit import (
    RealBlinkitAdapter,
    StubTransport,
    parse_pack_size,
    parse_search_response,
    playwright_available,
    split_brand,
)
from models import PriceUnavailableError, UserSession, utcnow

SAMPLE_SEARCH_JSON = {
    "response": {
        "snippets": [
            {
                "widget_type": "product_card_snippet_type_2",
                "data": {
                    "name": {"text": "Amul Taaza Toned Fresh Milk"},
                    "variant": {"text": "500 ml"},
                    "normal_price": {"text": "₹30"},
                    "is_sold_out": False,
                    "atc_action": {
                        "add_to_cart": {
                            "cart_item": {
                                "product_id": 485113,
                                "product_name": "Amul Taaza Toned Fresh Milk",
                                "price": 27.0,
                                "mrp": 30.0,
                                "unit": "500 ml",
                                "group_id": 12345,
                                "image_url": "https://example.com/milk.png",
                            }
                        }
                    },
                },
            },
            {
                "widget_type": "product_card_snippet_type_2",
                "data": {
                    "name": {"text": "Amul Gold Full Cream Milk"},
                    "variant": {"text": "1 L"},
                    "normal_price": {"text": "₹72"},
                    "is_sold_out": True,   # sold out -> dropped by the adapter
                    "atc_action": {
                        "add_to_cart": {
                            "cart_item": {
                                "product_id": 999001,
                                "product_name": "Amul Gold Full Cream Milk",
                                "price": 68.0,
                                "mrp": 72.0,
                                "unit": "1 L",
                                "group_id": 12346,
                            }
                        }
                    },
                },
            },
            {
                "widget_type": "banner_snippet",   # non-product widget -> skipped
                "data": {"name": {"text": "Sale"}},
            },
            {
                "widget_type": "product_card_snippet_type_2",
                "data": {"name": {"text": "Broken card, no cart item"}},  # skipped
            },
        ]
    }
}


def _session() -> UserSession:
    return UserSession(platform_id="blinkit", phone="guest",
                       token="guest", linked_at=utcnow())


def run(coro):
    return asyncio.run(coro)


# -- parsing ---------------------------------------------------------------

def test_parse_pack_size():
    assert parse_pack_size("500 ml") == (500.0, "ml", "500 ml")
    assert parse_pack_size("1 L") == (1000.0, "ml", "1 L")
    assert parse_pack_size("5 kg") == (5000.0, "g", "5 kg")
    assert parse_pack_size("100 g") == (100.0, "g", "100 g")
    assert parse_pack_size("6 pcs") == (6.0, "pcs", "6 pcs")
    assert parse_pack_size("3 x 100 g") == (300.0, "g", "3 x 100 g")
    size, unit, _ = parse_pack_size("mystery pack")
    assert (size, unit) == (1.0, "pcs")   # honest fallback, never a wrong number


def test_parse_search_response_shape():
    raws = parse_search_response(SAMPLE_SEARCH_JSON)
    assert len(raws) == 2                      # banner + broken card skipped
    assert raws[0]["product_id"] == 485113
    assert raws[0]["price"] == 27.0
    assert raws[0]["mrp"] == 30.0
    assert raws[0]["sold_out"] is False
    assert raws[1]["sold_out"] is True
    assert parse_search_response(None) == []
    assert parse_search_response({"response": {}}) == []


def test_split_brand():
    assert split_brand("Amul Taaza Toned Fresh Milk") == ("Amul", "Taaza Toned Fresh Milk")


# -- adapter behaviour (stub transport, no network) -------------------------

def test_search_maps_to_models_and_drops_sold_out():
    async def main():
        adapter = RealBlinkitAdapter(transport=StubTransport(SAMPLE_SEARCH_JSON))
        products = await adapter.search("milk", 28.6139, 77.2090, _session())
        assert len(products) == 1                  # sold-out Amul Gold dropped
        p = products[0]
        assert p.gtin == "blinkit:485113"          # namespaced, no GTIN collision
        assert p.brand == "Amul"
        assert p.pack_size == 500.0 and p.unit == "ml"
        assert p.mrp == 30.0
    run(main())


def test_get_price_serves_cached_snapshot():
    async def main():
        adapter = RealBlinkitAdapter(transport=StubTransport(SAMPLE_SEARCH_JSON))
        await adapter.search("milk", 28.6139, 77.2090, _session())
        snap = await adapter.get_price("blinkit:485113", _session())
        assert snap.price == 27.0
        assert snap.mrp == 30.0
        assert snap.per_unit_price == pytest.approx(27.0 / 500.0)
        assert snap.in_stock is True
        assert snap.freshness == "live"
    run(main())


def test_get_price_unknown_raises():
    async def main():
        adapter = RealBlinkitAdapter(transport=StubTransport(SAMPLE_SEARCH_JSON))
        await adapter.search("milk", 28.6139, 77.2090, _session())
        with pytest.raises(PriceUnavailableError):
            await adapter.get_price("blinkit:999001", _session())  # sold out: never cached
        with pytest.raises(PriceUnavailableError):
            await adapter.get_price("8901010000116", _session())    # static gtin: unknown here
    run(main())


def test_linking_raises_not_implemented():
    async def main():
        adapter = RealBlinkitAdapter(transport=StubTransport())
        with pytest.raises(NotImplementedError):
            await adapter.link_account("+919999999999")
        with pytest.raises(NotImplementedError):
            await adapter.verify_otp("ref", "123456")
    run(main())


def test_health_uses_transport_probe():
    async def main():
        adapter = RealBlinkitAdapter(transport=StubTransport(SAMPLE_SEARCH_JSON))
        status = await adapter.health()
        assert status.platform_id == "blinkit"
        assert status.status == "live"
        assert "probe" in status.message
    run(main())


def test_health_down_when_transport_fails():
    async def main():
        class Boom(StubTransport):
            async def search_raw(self, query, lat, lon):
                raise RuntimeError("network down")
        adapter = RealBlinkitAdapter(transport=Boom())
        status = await adapter.health()
        assert status.status == "down"
    run(main())


# -- registry ---------------------------------------------------------------

def test_default_registry_is_all_mock():
    env = os.environ.pop("SAUDA_REAL_BLINKIT", None)
    try:
        adapters = all_adapters()
        assert set(adapters) == {"blinkit", "zepto", "instamart", "flipkart_minutes"}
        assert not any_real_adapters(adapters)
        assert type(adapters["blinkit"]).__name__ == "MockBlinkitAdapter"
    finally:
        if env is not None:
            os.environ["SAUDA_REAL_BLINKIT"] = env


def test_real_flag_selects_live_adapter_when_possible():
    os.environ["SAUDA_REAL_BLINKIT"] = "1"
    try:
        adapters = all_adapters()
        if playwright_available():
            assert type(adapters["blinkit"]).__name__ == "RealBlinkitAdapter"
            assert any_real_adapters(adapters)
        else:
            # graceful fallback, never a boot failure
            assert type(adapters["blinkit"]).__name__ == "MockBlinkitAdapter"
    finally:
        del os.environ["SAUDA_REAL_BLINKIT"]


def test_mock_registry_unchanged():
    assert set(all_mock_adapters()) == {"blinkit", "zepto", "instamart", "flipkart_minutes"}
