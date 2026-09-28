# Real Blinkit adapter — verification notes (2026-09-28)

## What was built
`backend/adapters/real_blinkit.py` — a live Blinkit adapter behind the existing
`PlatformAdapter` interface. Enable with:

```bash
pip install playwright curl_cffi && playwright install chromium
SAUDA_REAL_BLINKIT=1 uvicorn api:app --reload
```

Without the flag (or without `playwright` installed) the backend boots exactly
as before on the deterministic mock — the flag can never break boot.

## API contract (verified from the CartFiller reference implementation)
- `POST https://blinkit.com/v1/layout/search?q=<query>&search_type=type_to_search`
- Headers: `app_client: consumer_web`, `app_version: 1010101010`,
  `web_app_version: 1008010016`, plus `lat` / `lon`
- Body: `{"applied_filters": null, "previous_search_query": query}`
- Response: `response.snippets[]` with `widget_type` containing `product_card`;
  product fields at `data.atc_action.add_to_cart.cart_item`
  (`product_id`, `product_name`, `price`, `mrp`, `unit`, `group_id`, `image_url`),
  plus `data.is_sold_out`, `data.normal_price.text`.

## Network findings from this workspace
| Path | Result |
|---|---|
| Direct HTTPS (python `httpx`) | **403** Cloudflare challenge page |
| Direct HTTPS with Chrome TLS impersonation (`curl_cffi`) | **403** on every request |
| Real Chromium, `https://blinkit.com/s/?q=amul+milk` | **Error page** (`blinkit \| Error Page`) |

The egress IP of this workspace appears to be flagged: even a real browser
gets an error page. So **live verification from here was not possible**.
The adapter code follows the verified request/response contract and is fully
tested with a stub transport (12 tests), plus an API-level smoke test through
FastAPI (`/v1/health`, `/v1/compare?query=milk`, 501 on account linking).

To verify live data, run the backend with `SAUDA_REAL_BLINKIT=1` from a
non-flagged network (a normal residential/office connection in India works —
this is exactly the environment CartFiller's extension runs in).

## Honest limitations (v1)
- **Guest mode only.** Search + prices need no login; account linking (headless
  OTP flow → access_token) is not implemented — `/v1/accounts/*` returns 501
  for the live adapter.
- **No cross-platform GTIN matching yet.** Live products get namespaced gtins
  (`blinkit:<product_id>`) that don't exist in the static catalog, so the live
  adapter is excluded from cart-wide optimization (`build_platform_data`
  skips adapters without `uses_static_catalog`). Query-based `/v1/compare`
  works fully live.
- **Location required.** Prices are location-dependent; the adapter takes
  `lat`/`lng` per request (defaults to Delhi, same as the REST API default).
- **Rate limits respected:** max 3 concurrent searches, ≥0.4 s between
  requests, backoff honoring `Retry-After` on 429/503.
- **Short cache:** live snapshots are cached 120 s and carry honest
  `captured_at` timestamps; `freshness` is always `"live"` for this adapter.
- Blinkit may change its private API at any time — the parser is defensive
  (unknown shapes are skipped, never fatal) and `health()` reports
  live/degraded/down from a real probe.
