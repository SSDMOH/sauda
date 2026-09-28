# Real platform adapters — status, flags, verification notes

Sauda's backend ships **mock adapters by default** (no network). Each platform
below has a **real adapter** that can be swapped in with an env flag. The
pattern (proven by Blinkit, see `docs/REAL_BLINKIT.md`): guest/no-login mode,
open/unsigned platform APIs issued from a real Chromium via Playwright when
datacenter HTTP is blocked, graceful degradation, honest freshness labels.
OTP account-linking is **not implemented** in any real adapter
(`link_account`/`verify_otp` raise `NotImplementedError` → HTTP 501).

**A flag can never break boot.** If the transport dependency (e.g. Playwright)
is missing, the backend logs a warning and stays on the mock. All real
adapters construct side-effect free and their `health()` never raises.

## Quick reference

| Platform | Flag | platform_id | Status | Live-verified? |
|---|---|---|---|---|
| Blinkit | `SAUDA_REAL_BLINKIT=1` | `blinkit` | ✅ working | ✅ (2026-09-28, see REAL_BLINKIT.md) |
| Zepto | `SAUDA_REAL_ZEPTO=1` | `zepto` | ✅ working | ⚠️ contract from verified reference, not live-probed here |
| Swiggy Instamart | `SAUDA_REAL_INSTAMART=1` | `instamart` | ✅ working | ⚠️ WAF behavior confirmed here; JSON not parsed live here |
| Flipkart Minutes | `SAUDA_REAL_FLIPKART_MINUTES=1` | `flipkart_minutes` | ✅ working | ⚠️ contract from verified recipe, not live-probed here |
| Swiggy (food) | `SAUDA_REAL_SWIGGY=1` | `swiggy` | ✅ working | ✅ live 2026-09-28 (315 dishes for "biryani") |
| Zomato | `SAUDA_REAL_ZOMATO=1` | `zomato` | ✅ partial (restaurants, cost-for-one) | ✅ live 2026-09-28 (9 restaurants; server cap) |
| Flipkart | `SAUDA_REAL_FLIPKART=1` | `flipkart` | ✅ working | ✅ parser live-verified 2026-09-28 (40 products end-to-end) |
| Amazon.in | `SAUDA_REAL_AMAZON=1` | `amazon` | ⚠️ partial (fragile) | ❌ blocked from this network (HTTP 503) |
| Myntra | `SAUDA_REAL_MYNTRA=1` | `myntra` | ✅ working | ❌ blocked from this network (gateway unreachable) |
| ONDC Cabs | `SAUDA_REAL_CABS=1` | `ondc_cabs` | 🛑 blocked for live data | ❌ needs BAP onboarding (see below) |

## Per-adapter notes

### Quick commerce

**Zepto** (`adapters/real_zepto.py`) — direct HTTPS, no browser. Guest BFF
gateway: `bff-gateway.zeptonow.com` serviceability + `user-search-service`
search with `x-without-bearer: true`. Tier-price precedence
`pricingEntityPrices[marketplace] > superSaverSellingPrice >
discountedSellingPrice > mrp`. Cached `productResponse` results are labelled
`freshness: "est"`, never `"live"`. GTINs: `zepto:<variant>`.

**Swiggy Instamart** (`adapters/real_instamart.py`) — Playwright required (AWS
WAF 202s plain HTTP). Bootstraps `aws-waf-token` via real page visits, then
`api/instamart/search/v2` from page context. Variation-level stock,
dual-cursor pagination, `cartAllowedQuantity == 0` dropped. Lat/lng is
reverse-geocoded via Nominatim (fallback `SAUDA_PINCODE`/110001, logged).

**Flipkart Minutes** (`adapters/real_flipkart_minutes.py`) — direct HTTPS.
`1.rome.api.flipkart.com/api/4/page/fetch` with `marketplace=HYPERLOCAL`.
Location gate (`RESPONSE.pageMeta.redirectionObject`) → honest error, never
silent empty results. No fallback to regular Flipkart. GTINs:
`flipkart_minutes:<pid>`.

### Food

**Swiggy** (`adapters/real_swiggy.py`) — unsigned public API
`www.swiggy.com/dapi/restaurants/search/v3` via plain httpx, no login. Parses
`groupedCard.cardGroupMap.DISH` → restaurant + dishes, paise→INR, live prices.

**Zomato** (`adapters/real_zomato.py`) — no usable unsigned JSON API
(`/webroutes/search/home` needs a session CSRF token; verified blocked).
Uses public SSR pages `zomato.com/<city>/delivery/dish-<dish>` and parses the
`SECTION_SEARCH_RESULT` blob. **Restaurant-level only** (server caps at 9):
price is Zomato's "cost for one" — an honest estimate, labelled
`"cost for one (Zomato)"`, never presented as a dish price. Query→dish mapping
from Zomato's canonical dish index (66 slugs, captured live 2026-09-28);
unmappable queries return `[]` without fetching.

Both food adapters: `uses_static_catalog=False` (excluded from Smart Split
Cart), `FeeTable`s are documented defaults (neither platform exposes real
per-order fees publicly).

### E-commerce

**Flipkart** (`adapters/real_flipkart.py`) — no standalone search API; reads
the raw navigation response body for server-rendered
`window.__INITIAL_STATE__` in a real Chromium (React deletes the blob on
hydration). Price selection honours `strikeOff` flags. **Parser live-verified**
2026-09-28: 40 products end-to-end (search → get_price → identity).

**Myntra** (`adapters/real_myntra.py`) — documented open frontend API
`www.myntra.com/gateway/v2/search/<query>` (cross-verified from two
independent sources). Unreachable from this workspace's network; parser
written, live run pending.

**Amazon.in** (`adapters/real_amazon.py`) — no open JSON API. Scrapes
`div.s-result-item[data-asin]` cards in real Chromium with captcha detection.
Most fragile of the three (honestly flagged in the module docstring).

**Identity discipline (all three):** `models.Product`/`PriceSnapshot` are
frozen. Each adapter exposes `IdentitySignals` (brand, variant attributes,
model code, seller, condition, delivery promise, effective price, canonical
URL, confidence, `ambiguous` + reason) via `adapter.identity(product_id)`.
Sponsored cards and accessory matches are marked ambiguous. GTINs are
namespaced (`amazon:<ASIN>`, `flipkart:<pid>`, `myntra:<styleId>`) — never
pretended to match the static catalog. Effective prices include delivery only
when the card states it; unstated fees stay `None`, never guessed. **Callers
must consult identity signals before any cart math** — cross-platform identity
resolution is still the open hard problem; these adapters supply the signals,
not the solution.

### Cabs

**ONDC Cabs** (`adapters/real_cabs.py`, `platform_id="ondc_cabs"`) — Beckn BAP
client. **Blocked for live data:** Beckn is async + trust-gated; live quotes
need (1) ONDC BAP onboarding (subscriber_id + Ed25519 keys, GSTIN for
production), (2) per-request signing, (3) a public `/on_search` callback.
No guest-accessible quote flow exists. Ola has no public API; Uber's public
estimates API was retired in 2018. Nothing is faked.

Unblock steps are in the module docstring (`SAUDA_ONDC_BAP_ID`,
`SAUDA_ONDC_BAP_URI`, `SAUDA_ONDC_GATEWAY_URL`, `SAUDA_ONDC_KEY_ID`). Without
credentials: `health()` → `down`, `/v1/cabs/compare` → honest 503. New
endpoint `POST /v1/cabs/compare {pickup_lat, pickup_lng, drop_lat, drop_lng,
departure_at?, vehicle?}` returns estimates sorted cheapest-first, every
snapshot `freshness: "est"` with quote timestamps. The generic `/v1/compare`
skips `ondc_cabs` gracefully (it raises `PriceUnavailableError` without a
drop location).

## Verification summary (this workspace, 2026-09-28)

- **191 pytest tests pass** (46-test baseline intact + 145 new adapter tests,
  all offline — fixtures/stub transports, no test hits live network).
- Live probes from here: Swiggy food ✅, Zomato ✅, Flipkart parser ✅.
  Zepto/Instamart/Minutes/Amazon/Myntra blocked by this workspace's flagged
  egress IP (Cloudflare 403 / WAF 202 / timeouts / 503) — contracts come from
  verified references; one live run from a normal Indian network is the
  remaining gate for each.
- The workspace proxy has a malformed `NO_PROXY` IPv6 entry; probes here
  needed `env -u NO_PROXY -u no_proxy`. Adapters use normal env behavior.
