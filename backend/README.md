# Sauda Backend — price-comparison + Smart Split Cart engine

Python 3.12 + FastAPI. Implements `docs/ARCHITECTURE.md` §§3,5–8 against
**deterministic mock adapters** (Blinkit, Zepto, Swiggy Instamart) — no real
network calls to any platform. A **live Blinkit adapter** is also available
(see "Live Blinkit adapter" below); it plugs into the same `PlatformAdapter`
ABC without touching callers.

## Layout

```
backend/
├── models.py            # shared dataclasses: Product, PriceSnapshot, FeeTable, Offer…
├── pricing.py           # true-total engine (§6): itemized bills
├── offers.py            # coupon rule engine (§7) + 6 sample coupons
├── optimizer.py         # cart-split optimizer (§5): exact 2^M enumeration
├── api.py               # FastAPI REST v1 (§8)
├── demo.py              # runnable split-cart demo (mock data)
├── demo_real_blinkit.py # live-Blinkit search demo (needs playwright, see below)
├── adapters/
│   ├── base.py          # PlatformAdapter ABC (§3)
│   ├── catalog.py       # 30-product grocery catalog + seeded per-platform prices
│   ├── mock_blinkit.py / mock_zepto.py / mock_instamart.py
│   ├── _mock_common.py  # shared mock implementation
│   └── real_blinkit.py  # LIVE Blinkit adapter (Playwright transport, guest mode)
└── tests/               # pytest suite (incl. test_real_blinkit.py, stub transport)
```

## Setup

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Run

```bash
# API (self-contained: in-memory stores, mock adapters — no DB needed)
.venv/bin/uvicorn api:app --reload
# → http://127.0.0.1:8000/docs

# Tests
.venv/bin/python -m pytest -q

# Demo: 6-item cart → split plan + savings receipt
.venv/bin/python demo.py
```

## Live Blinkit adapter

The mock is the default. For real Blinkit prices (guest mode, no login):

```bash
.venv/bin/pip install playwright && .venv/bin/playwright install chromium
SAUDA_REAL_BLINKIT=1 .venv/bin/uvicorn api:app --reload
# demo: .venv/bin/python demo_real_blinkit.py "amul milk"
```

How it works: Blinkit's search API (`POST /v1/layout/search`) is open and
unsigned, but Cloudflare blocks datacenter HTTP clients (403). The adapter
drives a real Chromium via Playwright and issues the site's own search API
from page context — the same requests the site itself makes, at
human-plausible pace (≤3 concurrent, backoff on 429). Notes & limits:
`docs/REAL_BLINKIT.md`. Without the env flag (or without `playwright`), the
backend boots on the mock — the flag can never break boot. Account linking
is not implemented for the live adapter (`/v1/accounts/*` → 501).

Docker (prod shape; API still runs standalone):

```bash
docker compose up --build     # api :8000, postgres :5432, redis :6379
docker compose up api         # api only
```

## Example calls

```bash
# Compare one product across platforms
curl -s -X POST localhost:8000/v1/compare \
  -H 'Content-Type: application/json' \
  -d '{"query": "atta"}' | python3 -m json.tool | head -40

# Smart split cart (use gtins from /v1/compare)
curl -s -X POST localhost:8000/v1/optimize-cart \
  -H 'Content-Type: application/json' \
  -d '{"items": [{"gtin": "8901010000017", "qty": 1},
                 {"gtin": "8901010000109", "qty": 2}],
       "max_platforms": 3}' | python3 -m json.tool

# Coupons
curl -s 'localhost:8000/v1/offers?platform=zepto' | python3 -m json.tool

# Price-drop alert
curl -s -X POST localhost:8000/v1/alerts \
  -H 'Content-Type: application/json' \
  -d '{"gtin": "8901010000017", "target_price": 220}'

# Savings ledger
curl -s localhost:8000/v1/savings | python3 -m json.tool

# Mock account linking (MOCK: any 6-digit OTP is accepted)
REF=$(curl -s -X POST localhost:8000/v1/accounts/link \
  -H 'Content-Type: application/json' \
  -d '{"platform_id": "zepto", "phone": "9810012345"}' | python3 -c 'import json,sys; print(json.load(sys.stdin)["link_ref"])')
curl -s -X POST localhost:8000/v1/accounts/verify \
  -H 'Content-Type: application/json' \
  -d "{\"link_ref\": \"$REF\", \"otp\": \"123456\"}"
curl -s -X DELETE localhost:8000/v1/accounts/zepto
```

## Notes

- Prices are **seeded-deterministic**: same `(platform, gtin)` always yields the
  same price (±5–15% off MRP). A few products are deliberately unstocked on one
  platform each so the optimizer has real choices.
- True total = items + delivery_fee(subtotal, distance, surge) + platform_fee +
  packaging + GST(5% on items+packaging) − best coupon. Every bill is itemized.
- `max_platforms` (default 3) caps the split; the optimizer also reports the
  best single-platform total and savings vs. the most expensive feasible plan.
