"""Tests for the real Amazon.in adapter (adapters/real_amazon.py).

All tests use StubTransport — no network, no browser. The fixture below is a
hand-written sample mirroring the EXTRACT_JS page-context output shape
(div.s-result-item[data-asin] cards).
"""
import asyncio

import pytest

from adapters.real_amazon import (
    RealAmazonAdapter,
    StubTransport,
    extract_variant_attributes,
    is_captcha_page,
    parse_search_cards,
    query_tokens,
    split_brand,
)
from models import PriceUnavailableError, UserSession, utcnow

SAMPLE_CARDS = [
    {   # organic match, FREE delivery stated
        "asin": "B0CXYZ1234",
        "title": "Apple iPhone 15 (128 GB) - Black",
        "price_text": "₹66,999",
        "list_price_text": "₹79,900",
        "image_url": "https://m.media-amazon.com/iphone.png",
        "rating_text": "4.5 out of 5 stars",
        "sponsored": False,
        "delivery_line": "FREE delivery Wed, 1 Oct",
    },
    {   # organic match, paid delivery stated
        "asin": "B0ABC99999",
        "title": "Samsung Galaxy S24 5G (256 GB, Marble Grey)",
        "price_text": "₹62,999",
        "list_price_text": None,
        "image_url": None,
        "rating_text": None,
        "sponsored": False,
        "delivery_line": "₹40 delivery Thu, 2 Oct",
    },
    {   # sponsored card -> marked ambiguous
        "asin": "B0SPON12345",
        "title": "Noise-Cancelling Earbuds Pro",
        "price_text": "₹2,499",
        "list_price_text": "₹4,999",
        "image_url": None,
        "rating_text": None,
        "sponsored": True,
        "delivery_line": None,
    },
    {   # accessory: shares no query token with "iphone 15" -> marked ambiguous
        "asin": "B0CASE77777",
        "title": "Spigen Rugged Armor Case for Apple Mobile",
        "price_text": "₹1,499",
        "list_price_text": None,
        "image_url": None,
        "rating_text": None,
        "sponsored": False,
        "delivery_line": None,
    },
    {   # no price -> dropped by the parser
        "asin": "B0NOPRICE1",
        "title": "Mystery Product Without Price",
        "price_text": None,
        "list_price_text": None,
        "image_url": None,
        "rating_text": None,
        "sponsored": False,
        "delivery_line": None,
    },
    {   # empty ASIN (ad placeholder) -> dropped
        "asin": "",
        "title": "Ad Holder",
        "price_text": "₹100",
        "list_price_text": None,
        "image_url": None,
        "rating_text": None,
        "sponsored": True,
        "delivery_line": None,
    },
]


def _session() -> UserSession:
    return UserSession(platform_id="amazon", phone="guest",
                       token="tok", linked_at=utcnow())


def run(coro):
    return asyncio.run(coro)


# -- parsing ---------------------------------------------------------------

def test_parse_search_cards_shape():
    raws = parse_search_cards(SAMPLE_CARDS)
    assert len(raws) == 4                        # no-price + empty-ASIN cards dropped
    iphone = raws[0]
    assert iphone["asin"] == "B0CXYZ1234"
    assert iphone["price"] == 66999.0
    assert iphone["list_price"] == 79900.0
    assert iphone["delivery_fee"] == 0.0         # "FREE delivery"
    assert raws[1]["delivery_fee"] == 40.0       # "₹40 delivery"
    assert raws[2]["sponsored"] is True
    assert raws[3]["delivery_fee"] is None       # not stated -> never guessed
    assert parse_search_cards(None) == []
    assert parse_search_cards("junk") == []


def test_is_captcha_page():
    assert is_captcha_page("<html>Enter the characters you see below</html>") is True
    assert is_captcha_page("<html><body>results</body></html>") is False
    assert is_captcha_page(None) is False


def test_split_brand_and_variants():
    assert split_brand("Apple iPhone 15 (128 GB) - Black") == ("Apple", "iPhone 15 (128 GB) - Black")
    assert extract_variant_attributes("Apple iPhone 15 (128 GB) - Black") == {"storage": "128GB"}
    assert extract_variant_attributes("Plain T-Shirt") == {}
    assert query_tokens("iPhone 15") == ["iphone"]


# -- adapter behaviour (stub transport, no network) -------------------------

def test_search_maps_to_models_with_effective_price():
    async def main():
        adapter = RealAmazonAdapter(transport=StubTransport(SAMPLE_CARDS))
        products = await adapter.search("iphone 15", 28.6139, 77.2090, _session())
        assert len(products) == 4
        p = products[0]
        assert p.gtin == "amazon:B0CXYZ1234"     # namespaced, no GTIN collision
        assert p.brand == "Apple"                # leading-token heuristic
        assert p.pack_size == 1.0 and p.unit == "pcs"
        assert p.mrp == 79900.0
        snap = await adapter.get_price("amazon:B0CXYZ1234", _session())
        assert snap.price == 66999.0            # price + ₹0 stated delivery
        assert snap.freshness == "live"
        snap2 = await adapter.get_price("amazon:B0ABC99999", _session())
        assert snap2.price == 63039.0           # 62999 + ₹40 stated delivery
        assert snap2.mrp == 62999.0             # no list price -> mrp falls back to price
    run(main())


def test_identity_signals_exposed():
    async def main():
        adapter = RealAmazonAdapter(transport=StubTransport(SAMPLE_CARDS))
        await adapter.search("iphone 15", 28.6139, 77.2090, _session())
        ident = adapter.identity("amazon:B0CXYZ1234")
        assert ident is not None
        assert ident.platform_product_id == "B0CXYZ1234"
        assert ident.variant_attributes == {"storage": "128GB"}
        assert ident.delivery_promise == "FREE delivery Wed, 1 Oct"
        assert ident.delivery_fee == 0.0
        assert ident.price_includes_delivery is True
        assert ident.canonical_url == "https://www.amazon.in/dp/B0CXYZ1234"
        assert ident.ambiguous is False          # organic match on query tokens
    run(main())


def test_sponsored_card_marked_ambiguous():
    async def main():
        adapter = RealAmazonAdapter(transport=StubTransport(SAMPLE_CARDS))
        await adapter.search("earbuds", 28.6139, 77.2090, _session())
        ident = adapter.identity("amazon:B0SPON12345")
        assert ident is not None
        assert ident.ambiguous is True
        assert ident.confidence == "low"
        assert "sponsored" in ident.ambiguous_reason.lower()
    run(main())


def test_query_token_mismatch_marked_ambiguous():
    async def main():
        adapter = RealAmazonAdapter(transport=StubTransport(SAMPLE_CARDS))
        await adapter.search("iphone 15", 28.6139, 77.2090, _session())
        # "Spigen Rugged Armor Case for Apple Mobile" shares no token with "iphone 15"
        ident = adapter.identity("amazon:B0CASE77777")
        assert ident is not None
        assert ident.ambiguous is True
        assert "accessory" in ident.ambiguous_reason.lower()
    run(main())


def test_identity_unknown_returns_none():
    async def main():
        adapter = RealAmazonAdapter(transport=StubTransport(SAMPLE_CARDS))
        await adapter.search("iphone 15", 28.6139, 77.2090, _session())
        assert adapter.identity("amazon:XXXX") is None
    run(main())


def test_get_price_unknown_raises():
    async def main():
        adapter = RealAmazonAdapter(transport=StubTransport(SAMPLE_CARDS))
        await adapter.search("iphone 15", 28.6139, 77.2090, _session())
        with pytest.raises(PriceUnavailableError):
            await adapter.get_price("amazon:B0NOPRICE1", _session())  # dropped: never cached
    run(main())


def test_linking_raises_not_implemented():
    async def main():
        adapter = RealAmazonAdapter(transport=StubTransport())
        with pytest.raises(NotImplementedError):
            await adapter.link_account("+919999999999")
        with pytest.raises(NotImplementedError):
            await adapter.verify_otp("ref", "123456")
    run(main())


def test_health_uses_transport_probe():
    async def main():
        adapter = RealAmazonAdapter(transport=StubTransport(SAMPLE_CARDS))
        status = await adapter.health()
        assert status.platform_id == "amazon"
        assert status.status == "live"
        assert "probe" in status.message
    run(main())


def test_health_down_when_transport_fails():
    async def main():
        class Boom(StubTransport):
            async def search_raw(self, query):
                raise RuntimeError("captcha")
        adapter = RealAmazonAdapter(transport=Boom())
        status = await adapter.health()
        assert status.status == "down"
    run(main())


def test_adapter_metadata():
    adapter = RealAmazonAdapter(transport=StubTransport())
    assert adapter.platform_id == "amazon"
    assert adapter.vertical == "ecommerce"
    assert adapter.uses_static_catalog is False
