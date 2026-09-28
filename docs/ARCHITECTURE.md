# Sauda — System Architecture
**Version:** 0.1 · **Date:** 2026-09-28 · **Companion:** `PRD.md`, `../research/research-report.md`

## 1. Stack
| Layer | Choice | Why |
|---|---|---|
| Mobile | **Flutter** (Android-first) | UI is the product (comparison tables, savings viz, split-cart diagrams); one codebase → iOS/web later |
| Backend | **Python 3.12, FastAPI** | Price-ingestion ecosystem is Python-native (Playwright, httpx); typed API for clients |
| Data | **Postgres 16 + Redis 7** | Catalog/entities/offers in Postgres; price cache (TTL 2–5 min), session metadata, job queues in Redis |
| Web | **Next.js 15 + Tailwind + shadcn/ui** (v2; interactive demo artifact now) | Same REST API as mobile |
| Infra | Docker Compose; prod in **Mumbai region** (DPDP data residency) | — |

## 2. Services (logical; single deployable monolith in v1, split later)
- **ingestion** — platform adapters behind a common interface, with health checks.
- **matching** — entity resolution: GTIN/barcode first, fuzzy name+pack fallback.
- **pricing** — true-total engine: per-platform, per-city fee tables (delivery, platform
  fee, packaging, GST, surge, small-order fee), versioned.
- **optimizer** — cart-split optimization (§5).
- **offers** — coupon rule objects + NLP assist for drafting rules from banner text.
- **notify** — price-drop / surge alerts via FCM; scheduled re-fetch jobs.
- **api** — REST for mobile + web.

## 3. Adapter interface
```python
class PlatformAdapter(ABC):
    platform_id: str          # "blinkit" | "zepto" | "instamart" | ...
    vertical: str             # "grocery" | "food" | "cabs" | "ecommerce"

    async def link_account(self, phone: str) -> str: ...
    async def verify_otp(self, link_ref: str, otp: str) -> UserSession: ...
    async def search(self, query: str, lat: float, lng: float,
                     session: UserSession) -> list[Product]: ...
    async def get_price(self, product_id: str,
                        session: UserSession) -> PriceSnapshot: ...
    async def health(self) -> AdapterStatus: ...   # live | degraded | down
```
- v1 ships **mock adapters** with deterministic, realistic data (same interface, so real
  adapters plug in without touching callers).
- Real adapters: human-rate fetching, bounded concurrency, exponential backoff,
  Playwright-stealth fallback when pure HTTP replay fails.

## 4. Core data model (Postgres)
- `users`, `linked_accounts` (**session tokens only, encrypted at rest — never raw passwords**)
- `platforms` (fee tables versioned per city)
- `products` (gtin, brand, name, pack_size, unit)
- `price_snapshots` (platform_id, product_id, price, mrp, per_unit_price, fees JSON,
  captured_at, freshness: `live` | `est`)
- `offers` (code, rule JSON, applicability)
- `carts`, `comparisons`, `alerts`, `savings_ledger`

## 5. Cart-split optimizer
**Input:** items with quantities; per-platform availability + unit prices; per-platform
fee structures; minimum-order values; `max_platforms` (user-set, default 3).

**Algorithm (exact for v1 scale):** enumerate platform subsets S (2^M; M ≤ 8 → ≤256).
For each S: assign every item to the cheapest platform in S that stocks it; compute
true totals via the pricing engine; **accept S only if every platform's assigned
subtotal ≥ its minimum-order value** (platforms with zero items are fine).
Pick the minimum-total feasible S. Tie-breaks: fewer platforms, then faster max ETA.

**Also compute:** best single-platform total (for the "consolidate" toggle) and
savings vs. the most expensive feasible option (for the savings receipt).
Complexity is trivial at v1 scale (M ≤ 8, N ≤ 50). Graduate to ILP/heuristics if
M or N grows 10×.

## 6. Pricing engine
```
true_total = Σ item_price×qty
           + delivery_fee(subtotal, distance, surge)
           + platform_fee + packaging_fee
           + GST
           − coupon_discount(subtotal, user, offers)
```
Fee tables per platform per city, versioned; every comparison response includes the
itemized breakdown so the client can render the expandable bill.

## 7. Offer engine
Offers as rule objects:
`{code, type: flat | pct_upto | free_delivery, value, max_discount, min_order,
first_order_only, payment_methods[], platforms[]}`.
NLP assist drafts rules from banner/terms text; human or LLM review before activation.

## 8. REST API (v1 draft)
- `POST /v1/compare` `{query|items[], lat, lng}` → per-platform results + best pick
- `POST /v1/optimize-cart` `{items[], lat, lng, max_platforms}` → split plan + receipt
- `GET /v1/offers?platform=` → applicable coupons
- `POST /v1/alerts` / `DELETE /v1/alerts/{id}` → price-drop subscriptions
- `GET /v1/savings` → ledger summary (rings, streaks, lifetime)
- `POST /v1/accounts/link` (start) / `/v1/accounts/verify` (OTP) / `DELETE` (unlink)

## 9. "Real-time", honestly
Real-time = **fresh fetch per comparison** (adapters hit platforms at request time;
Redis caches for 2–5 min to absorb repeat views). No websockets in v1. Alerts run as
scheduled re-fetch jobs comparing against user thresholds.

## 10. Security & privacy
- Tokens in `flutter_secure_storage` (Keystore/Keychain) on device; backend holds
  tokens only encrypted at rest under per-user keys, or proxied per-request.
- DPDP consent screens; one-tap unlink deletes tokens everywhere.
- Play Data Safety section must declare location, tokens, and order data accurately.

## 11. Repo layout
```
sauda/
  docs/        PRD.md, ARCHITECTURE.md
  research/    research-report.md
  backend/     FastAPI engine (adapters, pricing, optimizer, offers, API)
  mobile/      Flutter app (Android-first)
  web/         Next.js companion (v2; demo artifact stands in for now)
  web-demo/    interactive prototype sources (if any)
```

## 12. Build order
1. backend engine + mock adapters (unblocks everything)
2. mobile scaffold against the API contract (§8) using mock data service
3. web-demo interactive prototype (parallel)
4. Wire real adapters one platform at a time, starting with Blinkit (open, unsigned API)
