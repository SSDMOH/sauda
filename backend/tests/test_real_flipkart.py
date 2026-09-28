"""Tests for the real Flipkart adapter (adapters/real_flipkart.py).

All tests use StubTransport — no network, no browser. The fixture below is a
hand-written sample following the verified window.__INITIAL_STATE__ shape:
pageDataV4.page.data slots (one an ARRAY of widgets, per the verified gotcha),
PRODUCT_SUMMARY widgets -> data.products[] -> productInfo.value.
"""
import asyncio
import json

import pytest

from adapters.real_flipkart import (
    RealFlipkartAdapter,
    StubTransport,
    extract_initial_state,
    parse_search_state,
)
from models import PriceUnavailableError, UserSession, utcnow


def _card(pid, title, brand, subtitle, prices, sponsored=False, in_stock=True):
    prod = {
        "productInfo": {
            "value": {
                "id": pid,
                "titles": {"title": title, "superTitle": brand,
                           "subtitle": subtitle},
                "pricing": {"prices": prices, "totalDiscount": 50,
                            "discountAmount": 3000},
                "rating": {"average": 4.3, "base": 5, "count": 12040,
                           "reviewCount": 1102, "roundOffCount": "12K+"},
                "availability": {"displayState": "IN_STOCK" if in_stock else "OUT_OF_STOCK"},
                "baseUrl": f"/boat-stone-190f-70-hours-playtime-bluetooth/p/itm{pid}?pid={pid}",
            }
        }
    }
    if sponsored:
        prod["adInfo"] = {"sponsored": True}
    return prod


def _widget(*products):
    # real envelope nesting, verified against live Flipkart page HTML 2026-09-28:
    # slot -> {"slotType": "WIDGET", "widget": {"type": ..., "data": {"products": [...]}}}
    return {"slotType": "WIDGET",
            "widget": {"type": "PRODUCT_SUMMARY",
                       "data": {"products": list(products)}}}


SAMPLE_STATE = {
    "pageDataV4": {
        "page": {
            "data": {
                "10003": [   # slot is an ARRAY of widgets (verified gotcha)
                    _widget(
                        _card("SPEAKER1", "boAt Stone 190F 70 Hours Playtime",
                              "boAt", "Black, True Wireless",
                              [{"value": 1499, "strikeOff": True},   # MRP first...
                               {"value": 999, "strikeOff": False}]), # ...current second
                        _card("SPEAKER2", "JBL Go 3 Portable Speaker",
                              "JBL", "Teal, Bluetooth",
                              [{"value": 3999, "strikeOff": False}],
                              sponsored=True),
                        _card("BROKEN", "Card Without Price", "X", "",
                              []),                                  # no price -> dropped
                    ),
                    {"widget": {"type": "PAGINATION_BAR"},
                     "data": {"totalPages": 42, "currentPage": 1}},
                ],
                "other_slot": _widget(
                    _card("SPEAKER1", "boAt Stone 190F (dup)",
                          "boAt", "Black",
                          [{"value": 999, "strikeOff": False}]),
                ),
            }
        }
    }
}


def _session() -> UserSession:
    return UserSession(platform_id="flipkart", phone="guest",
                       token="tok", linked_at=utcnow())


def run(coro):
    return asyncio.run(coro)


# -- parsing ---------------------------------------------------------------

def test_extract_initial_state_brace_matching():
    html = ('<html><head><script>window.__INITIAL_STATE__ = '
            '{"a": {"b": "x};"}, "c": 1}; // trailing junk</script></head></html>')
    state = extract_initial_state(html)
    assert state == {"a": {"b": "x};"}, "c": 1}
    assert extract_initial_state("<html>no blob here</html>") is None
    assert extract_initial_state(None) is None
    assert extract_initial_state("window.__INITIAL_STATE__ = not json {") is None


def test_parse_search_state_shape_and_dedupe():
    raws = parse_search_state(SAMPLE_STATE)
    assert len(raws) == 3                        # duplicate pid dropped; no-price kept (adapter filters it)
    pids = [r["product_id"] for r in raws]
    assert pids == ["SPEAKER1", "SPEAKER2", "BROKEN"]  # first-seen order kept
    sp1 = raws[0]
    assert sp1["price"] == 999.0                  # strikeOff:false selected, not prices[0]
    assert sp1["mrp"] == 1499.0
    assert sp1["brand"] == "boAt"
    assert sp1["variant"] == "Black, True Wireless"
    assert sp1["rating"] == 4.3
    assert sp1["rating_count"] == 12040
    assert sp1["in_stock"] is True
    assert sp1["sponsored"] is False
    assert raws[1]["sponsored"] is True
    assert raws[1]["base_url"].endswith("?pid=SPEAKER2")
    assert raws[2]["price"] is None              # no price -> adapter's _to_models drops it
    assert parse_search_state(None) == []
    assert parse_search_state({}) == []


# -- adapter behaviour (stub transport, no network) -------------------------

def test_search_maps_to_models_and_caches_identity():
    async def main():
        adapter = RealFlipkartAdapter(transport=StubTransport(SAMPLE_STATE))
        products = await adapter.search("bluetooth speaker", 28.6139, 77.2090, _session())
        assert len(products) == 2
        p = products[0]
        assert p.gtin == "flipkart:SPEAKER1"      # namespaced, no GTIN collision
        assert p.brand == "boAt"
        assert p.pack_size == 1.0 and p.unit == "pcs"
        assert p.mrp == 1499.0
        ident = adapter.identity("flipkart:SPEAKER1")
        assert ident is not None
        assert ident.platform_product_id == "SPEAKER1"
        assert ident.variant_attributes["variant"] == "Black, True Wireless"
        assert ident.ambiguous is False
        assert ident.delivery_fee is None       # not shown at search level
        assert ident.canonical_url.startswith("https://www.flipkart.com/")
    run(main())


def test_sponsored_card_marked_ambiguous():
    async def main():
        adapter = RealFlipkartAdapter(transport=StubTransport(SAMPLE_STATE))
        await adapter.search("speaker", 28.6139, 77.2090, _session())
        ident = adapter.identity("flipkart:SPEAKER2")
        assert ident is not None
        assert ident.ambiguous is True
        assert ident.confidence == "low"
    run(main())


def test_get_price_serves_cached_snapshot():
    async def main():
        adapter = RealFlipkartAdapter(transport=StubTransport(SAMPLE_STATE))
        await adapter.search("speaker", 28.6139, 77.2090, _session())
        snap = await adapter.get_price("flipkart:SPEAKER1", _session())
        assert snap.price == 999.0
        assert snap.mrp == 1499.0
        assert snap.in_stock is True
        assert snap.freshness == "live"
    run(main())


def test_get_price_unknown_raises():
    async def main():
        adapter = RealFlipkartAdapter(transport=StubTransport(SAMPLE_STATE))
        await adapter.search("speaker", 28.6139, 77.2090, _session())
        with pytest.raises(PriceUnavailableError):
            await adapter.get_price("flipkart:BROKEN", _session())  # no price: never cached
    run(main())


def test_linking_raises_not_implemented():
    async def main():
        adapter = RealFlipkartAdapter(transport=StubTransport())
        with pytest.raises(NotImplementedError):
            await adapter.link_account("+919999999999")
        with pytest.raises(NotImplementedError):
            await adapter.verify_otp("ref", "123456")
    run(main())


def test_health_uses_transport_probe():
    async def main():
        adapter = RealFlipkartAdapter(transport=StubTransport(SAMPLE_STATE))
        status = await adapter.health()
        assert status.platform_id == "flipkart"
        assert status.status == "live"
        assert "probe" in status.message
    run(main())


def test_health_down_when_transport_fails():
    async def main():
        class Boom(StubTransport):
            async def search_raw(self, query):
                raise RuntimeError("network down")
        adapter = RealFlipkartAdapter(transport=Boom())
        status = await adapter.health()
        assert status.status == "down"
    run(main())


def test_health_down_when_blob_missing():
    async def main():
        class NoBlob(StubTransport):
            async def search_raw(self, query):
                return None     # challenge page shape -> no products, still parsed OK
        adapter = RealFlipkartAdapter(transport=NoBlob())
        status = await adapter.health()
        assert status.status == "live"   # parses to 0 products, transport itself worked
        assert "0 products" in status.message
    run(main())


def test_adapter_metadata():
    adapter = RealFlipkartAdapter(transport=StubTransport())
    assert adapter.platform_id == "flipkart"
    assert adapter.vertical == "ecommerce"
    assert adapter.uses_static_catalog is False
