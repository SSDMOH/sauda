"""Tests for the real cabs adapter (adapters/real_cabs.py).

STATUS: the adapter is BLOCKED for live data (no BAP credentials exist), so
all tests use StubTransport with a hand-written fixture following the Beckn
`on_search` shape — no network, no browser, no invented endpoints.
"""
import asyncio
import time

import pytest

from adapters.real_cabs import (
    QUOTE_TTL_S,
    CabProvider,
    RealCabsAdapter,
    StubTransport,
    UnconfiguredTransport,
    HttpGatewayTransport,
    _CacheEntry,
    bap_config_from_env,
    build_http_signature_headers,
    build_mobility_intent,
    build_search_envelope,
    list_providers,
    normalize_departure,
    parse_on_search,
    real_cabs_enabled,
    register_provider,
    unblock_instructions,
)
from models import PriceUnavailableError, UserSession, utcnow

SAMPLE_ON_SEARCH = {
    "context": {
        "domain": "nic2004:60221", "country": "IND", "city": "std:080",
        "action": "on_search", "version": "2.0.0",
        "bap_id": "sauda.bap.example", "bap_uri": "https://api.sauda.example/bap",
        "transaction_id": "txn-1", "message_id": "msg-1",
        "timestamp": "2026-09-28T11:30:00+00:00", "ttl": "PT60S",
    },
    "message": {
        "catalog": {
            "providers": [
                {
                    "id": "namma-yatri",
                    "descriptor": {"name": "Namma Yatri"},
                    "fulfillments": [
                        {"id": "f1", "vehicle": {"category": "Auto"},
                         "tags": {"eta_minutes": 4, "distance_km": 8.2,
                                  "duration_min": 22}},
                        {"id": "f2", "vehicle": {"category": "Cab"},
                         "tags": {"eta_minutes": 6, "distance_km": 8.2,
                                  "duration_min": 22}},
                    ],
                    "items": [
                        {"id": "q1",
                         "descriptor": {"name": "Auto Rickshaw"},
                         "price": {"currency": "INR",
                                   "minimum_value": "120", "maximum_value": "145"},
                         "fulfillment_ids": ["f1"]},
                        {"id": "q2",
                         "descriptor": {"name": "Sedan"},
                         "price": {"currency": "INR", "value": "210"},
                         "fulfillment_ids": ["f2"]},
                        {"id": "q3", "descriptor": {"name": "Broken"},
                         "fulfillment_ids": ["f1"]},   # no price -> skipped
                    ],
                },
                {"id": "broken-provider", "descriptor": {}},  # no items -> skipped
                "not-a-dict",                                  # skipped
            ]
        }
    }
}

PICKUP = (12.9716, 77.5946)   # Bengaluru
DROP = (12.9352, 77.6245)


def _session() -> UserSession:
    return UserSession(platform_id="ondc_cabs", phone="guest",
                       token="guest", linked_at=utcnow())


def _adapter(payload: dict | None = None) -> RealCabsAdapter:
    return RealCabsAdapter(transport=StubTransport(
        payload if payload is not None else SAMPLE_ON_SEARCH))


def run(coro):
    return asyncio.run(coro)


def _search_kwargs() -> dict:
    return {"drop_lat": DROP[0], "drop_lng": DROP[1]}


# -- parsing -----------------------------------------------------------------

def test_parse_on_search_shape():
    quotes = parse_on_search(SAMPLE_ON_SEARCH)
    assert len(quotes) == 2                     # broken item/provider skipped
    auto, cab = quotes
    assert auto["provider_id"] == "namma-yatri"
    assert auto["vehicle"] == "Auto"            # joined from fulfillment
    assert auto["price_min"] == 120.0
    assert auto["price_max"] == 145.0           # range preserved, not collapsed
    assert auto["currency"] == "INR"
    assert auto["distance_km"] == 8.2
    assert auto["eta_min"] == 4
    assert cab["price_min"] == cab["price_max"] == 210.0
    assert parse_on_search(None) == []
    assert parse_on_search({}) == []
    assert parse_on_search({"message": {}}) == []


# -- Beckn message builders ----------------------------------------------------

def test_build_mobility_intent():
    dep = utcnow()
    intent = build_mobility_intent(*PICKUP, *DROP, vehicle="auto",
                                   departure_at=dep)
    assert intent["fulfillment"]["start"]["location"]["gps"] == "12.9716,77.5946"
    assert intent["fulfillment"]["end"]["location"]["gps"] == "12.9352,77.6245"
    assert intent["category"]["id"] == "Auto"
    assert intent["fulfillment"]["start"]["time"]["timestamp"].startswith("2026-")
    plain = build_mobility_intent(*PICKUP, *DROP)
    assert "category" not in plain and "time" not in plain["fulfillment"]["start"]
    with pytest.raises(ValueError):
        build_mobility_intent(*PICKUP, *DROP, vehicle="spaceship")


def test_build_search_envelope():
    config = bap_config_from_env({
        "SAUDA_ONDC_BAP_ID": "sauda.bap.example",
        "SAUDA_ONDC_BAP_URI": "https://api.sauda.example/bap",
        "SAUDA_ONDC_GATEWAY_URL": "https://gateway.example",
        "SAUDA_ONDC_KEY_ID": "key-1",
    })
    intent = build_mobility_intent(*PICKUP, *DROP)
    env_body = build_search_envelope(config, intent)
    ctx = env_body["context"]
    assert ctx["domain"] == "nic2004:60221"
    assert ctx["action"] == "search"
    assert ctx["country"] == "IND"
    assert ctx["bap_id"] == "sauda.bap.example"
    assert ctx["transaction_id"] and ctx["message_id"]
    assert env_body["message"]["intent"] == intent


def test_build_http_signature_headers():
    headers = build_http_signature_headers(key_id="key-1",
                                           signature=b"\x01\x02\x03")
    auth = headers["Authorization"]
    assert 'keyId="key-1"' in auth
    assert 'algorithm="ed25519"' in auth
    assert 'signature="AQID"' in auth       # base64 of the raw bytes


def test_normalize_departure():
    assert normalize_departure(None).tzinfo is not None
    epoch = normalize_departure(1759051200)   # 2025-09-28 00:00 UTC
    assert (epoch.year, epoch.month, epoch.day) == (2025, 9, 28)
    iso = normalize_departure("2026-09-28T17:30:00+05:30")
    assert iso.utcoffset().total_seconds() == 19800
    naive = normalize_departure(__import__("datetime").datetime(2026, 9, 28, 12, 0))
    assert naive.tzinfo is not None        # naive assumed UTC, never dropped
    with pytest.raises(ValueError):
        normalize_departure("next tuesday")
    with pytest.raises(ValueError):
        normalize_departure(object())


# -- provider registry ---------------------------------------------------------

def test_provider_registry_seeded():
    providers = list_providers()
    assert any(p.provider_id == "namma-yatri" for p in providers)


def test_register_provider_roundtrip():
    p = register_provider(CabProvider(
        provider_id="test-provider-xyz", display_name="Test",
        note="fixture"))
    try:
        assert p in list_providers()
        with pytest.raises(ValueError):
            register_provider(CabProvider(provider_id="test-provider-xyz",
                                          display_name="Dup"))
    finally:
        from adapters import real_cabs
        real_cabs._KNOWN_PROVIDERS.pop("test-provider-xyz", None)


# -- BAP config / env gating ----------------------------------------------------

def test_bap_config_from_env_partial_is_missing(monkeypatch):
    env = {"SAUDA_ONDC_BAP_ID": "x", "SAUDA_ONDC_BAP_URI": "y"}
    assert bap_config_from_env(env) is None
    env["SAUDA_ONDC_GATEWAY_URL"] = "z"
    env["SAUDA_ONDC_KEY_ID"] = "k"
    config = bap_config_from_env(env)
    assert config is not None and config.bap_id == "x"
    assert config.city == "std:080"        # documented default


def test_real_cabs_enabled_flag(monkeypatch):
    monkeypatch.delenv("SAUDA_REAL_CABS", raising=False)
    assert real_cabs_enabled() is False
    monkeypatch.setenv("SAUDA_REAL_CABS", "1")
    assert real_cabs_enabled() is True


# -- adapter behaviour (stub transport, no network) ------------------------------

def test_search_returns_estimates_sorted():
    adapter = _adapter()
    products = run(adapter.search("", *PICKUP, _session(), **_search_kwargs()))
    assert len(products) == 2
    assert all(p.gtin.startswith("ondc_cabs:") for p in products)
    snaps = [run(adapter.get_price(p.gtin, _session())) for p in products]
    assert [s.price for s in snaps] == [120.0, 210.0]   # cheapest first
    for p, s in zip(products, snaps):
        assert s.freshness == "est"                    # NEVER "live"
        assert "(ONDC)" in p.brand
        assert "est." in p.pack_label and "quoted" in p.pack_label
        assert "valid 60 s" in p.pack_label
        assert s.platform_id == "ondc_cabs"
    assert products[0].mrp == 145.0       # mrp = top of the estimate range
    assert products[0].name == "Auto ride · 8.2 km"
    assert snaps[0].eta_minutes == 4      # ETA joined from fulfillment


def test_search_requires_drop_location():
    adapter = _adapter()
    with pytest.raises(PriceUnavailableError) as exc:
        run(adapter.search("", *PICKUP, _session()))
    assert "drop_lat" in str(exc.value)


def test_search_pickup_defaults_and_overrides():
    adapter = _adapter()
    run(adapter.search("", *PICKUP, _session(), **_search_kwargs()))
    req = adapter.transport.calls[0]
    assert (req.pickup_lat, req.pickup_lng) == PICKUP   # defaults to lat/lng
    assert (req.drop_lat, req.drop_lng) == DROP
    assert req.departure_at is not None                 # None -> now

    adapter2 = _adapter()
    run(adapter2.search("", 0.0, 0.0, _session(), pickup_lat=1.0,
                        pickup_lng=2.0, drop_lat=3.0, drop_lng=4.0))
    req2 = adapter2.transport.calls[0]
    assert (req2.pickup_lat, req2.pickup_lng) == (1.0, 2.0)


def test_search_vehicle_hint_from_query_and_kwarg():
    adapter = _adapter()
    run(adapter.search("auto", *PICKUP, _session(), **_search_kwargs()))
    assert adapter.transport.calls[0].vehicle == "auto"
    adapter2 = _adapter()
    run(adapter2.search("", *PICKUP, _session(), vehicle="cab",
                        **_search_kwargs()))
    assert adapter2.transport.calls[0].vehicle == "cab"


def test_gtin_stable_per_route():
    adapter = _adapter()
    a = run(adapter.search("", *PICKUP, _session(), **_search_kwargs()))
    b = run(adapter.search("", *PICKUP, _session(), **_search_kwargs()))
    assert [p.gtin for p in a] == [p.gtin for p in b]
    c = run(adapter.search("", *PICKUP, _session(),
                           drop_lat=13.0, drop_lng=77.7))
    assert {p.gtin for p in c}.isdisjoint({p.gtin for p in a})


def test_get_price_cache_and_expiry():
    adapter = _adapter()
    products = run(adapter.search("", *PICKUP, _session(), **_search_kwargs()))
    snap = run(adapter.get_price(products[0].gtin, _session()))
    assert snap.price == 120.0
    with pytest.raises(PriceUnavailableError):
        run(adapter.get_price("ondc_cabs:nope:auto:x", _session()))
    # force expiry -> honest miss, never a stale fare
    entry = adapter._cache[products[0].gtin]
    adapter._cache[products[0].gtin] = _CacheEntry(
        entry.product, entry.snapshot,
        stored_at=time.monotonic() - QUOTE_TTL_S - 1)
    with pytest.raises(PriceUnavailableError):
        run(adapter.get_price(products[0].gtin, _session()))


def test_link_account_not_implemented():
    adapter = _adapter()
    with pytest.raises(NotImplementedError):
        run(adapter.link_account("+91-9999999999"))
    with pytest.raises(NotImplementedError):
        run(adapter.verify_otp("ref", "123456"))


def test_stub_health_is_degraded_never_raises():
    adapter = _adapter()
    status = run(adapter.health())
    assert status.status == "degraded"
    assert "no live quotes" in status.message


# -- unconfigured / blocked behaviour -------------------------------------------

def _clear_bap_env(monkeypatch):
    for var in ("SAUDA_ONDC_BAP_ID", "SAUDA_ONDC_BAP_URI",
                "SAUDA_ONDC_GATEWAY_URL", "SAUDA_ONDC_KEY_ID"):
        monkeypatch.delenv(var, raising=False)


def test_unconfigured_search_raises_with_unblock_steps(monkeypatch):
    _clear_bap_env(monkeypatch)
    adapter = RealCabsAdapter()          # no transport arg -> env decides
    assert isinstance(adapter.transport, UnconfiguredTransport)
    with pytest.raises(Exception) as exc:
        run(adapter.search("", *PICKUP, _session(), **_search_kwargs()))
    assert "SAUDA_ONDC_BAP_ID" in str(exc.value)


def test_unconfigured_health_down_with_instructions(monkeypatch):
    _clear_bap_env(monkeypatch)
    adapter = RealCabsAdapter()
    status = run(adapter.health())
    assert status.status == "down"
    assert "BAP credentials" in status.message
    assert "SAUDA_ONDC_BAP_ID" in unblock_instructions()


def test_gateway_transport_quote_raw_explains_callback_gap(monkeypatch):
    env = {"SAUDA_ONDC_BAP_ID": "x", "SAUDA_ONDC_BAP_URI": "https://cb.example",
           "SAUDA_ONDC_GATEWAY_URL": "https://gw.example",
           "SAUDA_ONDC_KEY_ID": "k"}
    adapter = RealCabsAdapter(transport=HttpGatewayTransport(
        bap_config_from_env(env)))
    with pytest.raises(RuntimeError) as exc:
        run(adapter.search("", *PICKUP, _session(), **_search_kwargs()))
    assert "/on_search" in str(exc.value)


def test_health_never_raises(monkeypatch):
    _clear_bap_env(monkeypatch)
    adapter = RealCabsAdapter()
    status = run(adapter.health())      # must return a status, not raise
    assert status.platform_id == "ondc_cabs"
