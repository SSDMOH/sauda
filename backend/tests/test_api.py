"""API smoke tests (FastAPI TestClient, in-process)."""
from fastapi.testclient import TestClient

from adapters.catalog import PRODUCTS
from api import app

client = TestClient(app)


def _gtin(keyword: str) -> str:
    for p in PRODUCTS:
        if keyword in f"{p.brand} {p.name}".lower():
            return p.gtin
    raise AssertionError(f"no product matching {keyword!r}")


def test_health():
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert {a["platform_id"] for a in body["adapters"]} == {
        "blinkit", "zepto", "instamart", "flipkart_minutes"}


def test_compare_query():
    r = client.post("/v1/compare", json={"query": "atta"})
    assert r.status_code == 200
    body = r.json()
    assert body["freshness"] == "live"
    assert len(body["results"]) == 4
    assert body["best_pick"] in {"blinkit", "zepto", "instamart", "flipkart_minutes"}
    for result in body["results"]:
        assert result["bill"]["total"] > 0
        assert result["items"]  # atta stocked everywhere


def test_compare_items_skips_unstocked_platform():
    # Coca-Cola is deliberately unavailable on Zepto.
    coke = _gtin("coca-cola")
    r = client.post("/v1/compare",
                    json={"items": [{"gtin": coke, "qty": 1}]})
    assert r.status_code == 200
    pids = {res["platform_id"] for res in r.json()["results"]}
    assert pids == {"blinkit", "instamart", "flipkart_minutes"}


def test_compare_requires_query_or_items():
    assert client.post("/v1/compare", json={}).status_code == 400


def test_optimize_cart():
    cart = [
        {"gtin": _gtin("atta"), "qty": 1},
        {"gtin": _gtin("milk"), "qty": 2},
        {"gtin": _gtin("noodles"), "qty": 1},
    ]
    r = client.post("/v1/optimize-cart", json={"items": cart})
    assert r.status_code == 200
    plan = r.json()
    assert plan["feasible"] is True
    assert plan["grand_total"] > 0
    assert plan["savings_vs_most_expensive"] >= 0
    assert plan["best_single_platform"] is not None


def test_optimize_rejects_unknown_gtin():
    r = client.post("/v1/optimize-cart",
                    json={"items": [{"gtin": "nope", "qty": 1}]})
    assert r.status_code == 400


def test_offers_filter():
    all_offers = client.get("/v1/offers").json()["offers"]
    assert len(all_offers) == 6
    zepto = client.get("/v1/offers", params={"platform": "zepto"}).json()["offers"]
    assert {o["code"] for o in zepto} == {"ZEPTO25", "ZFREEDEL"}


def test_alerts_crud():
    r = client.post("/v1/alerts", json={"gtin": _gtin("atta"),
                                        "target_price": 200.0})
    assert r.status_code == 201
    alert_id = r.json()["alert_id"]
    r = client.delete(f"/v1/alerts/{alert_id}")
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert client.delete(f"/v1/alerts/{alert_id}").status_code == 404


def test_savings_ledger():
    body = client.get("/v1/savings").json()
    assert body["lifetime_savings"] > 0
    assert body["current_streak_days"] >= 0
    assert len(body["recent"]) == 3


def test_accounts_mock_otp_flow():
    link = client.post("/v1/accounts/link",
                       json={"platform_id": "zepto", "phone": "9810012345"}).json()
    assert "link_ref" in link
    # any 6-digit OTP accepted (documented mock behaviour)
    ok = client.post("/v1/accounts/verify",
                     json={"link_ref": link["link_ref"], "otp": "123456"})
    assert ok.status_code == 200
    assert ok.json()["platform_id"] == "zepto"
    # bad OTP rejected
    link2 = client.post("/v1/accounts/link",
                        json={"platform_id": "zepto", "phone": "9810012345"}).json()
    bad = client.post("/v1/accounts/verify",
                      json={"link_ref": link2["link_ref"], "otp": "abc"})
    assert bad.status_code == 400
    # unlink
    un = client.delete("/v1/accounts/zepto")
    assert un.json() == {"unlinked": True, "platform_id": "zepto"}
