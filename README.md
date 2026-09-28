# Sauda — Price Comparison App (Project)

**Working codename:** Sauda · **Started:** 2026-09-28
**Vision:** India's bargain-hunter super-app — compare *true* prices across food delivery,
quick commerce, cabs, and e-commerce, and automatically split carts across platforms
to minimize what you actually pay.

## What's in this folder

| Path | What it is | Status |
|---|---|---|
| `research/research-report.md` | Deep research: Comparify teardown, competitors, tech approach, stack picks, risks | ✅ Done |
| `docs/PRD.md` | Product requirements: features, user flows, MVP scope, metrics | ✅ Done |
| `docs/ARCHITECTURE.md` | System design: services, adapter interface, optimizer algorithm, API contract | ✅ Done |
| `backend/` | FastAPI engine: mock adapters (Blinkit/Zepto/Instamart), true-total pricing, **cart-split optimizer**, offer engine, 34 passing tests | ✅ Built & verified |
| `mobile/` | Flutter app scaffold (Android-first): 6 screens, mock data service, premium fintech UI | ✅ Scaffolded (needs Flutter SDK to compile) |
| Web demo | Interactive prototype (hosted artifact "Price Comparison App") | ✅ Built |

## Quick start

**Backend engine:**
```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q        # 34 tests
.venv/bin/python demo.py              # watch the split-cart optimizer work
.venv/bin/uvicorn api:app --reload    # API at localhost:8000, docs at /docs
```

**Mobile app:**
```bash
cd mobile
flutter pub get && flutter run        # needs Flutter SDK installed
```

**Web demo:** open the "Price Comparison App" artifact card in chat (sample prices, clearly labeled).

## Key design decisions (from research)
- **Account-linking model** (like Comparify): OTP-link the user's own platform accounts,
  replay private APIs as the user. No public APIs exist for this data.
- **Deep-link checkout first**, no in-app payments in v1 (OTPs/2FA make auto-checkout infeasible).
- **MVP = quick-commerce grocery** (Blinkit/Zepto/Instamart): barcode matching is clean,
  prices are stable, optimizer shines. Food → cabs (via ONDC/Beckn, the legitimate path) → e-commerce after.
- **Headline differentiator: Smart Split Cart** — exact subset-enumeration optimizer,
  unclaimed by Comparify and every open-source clone found.

## Roadmap / next steps
1. Wire Flutter `api_client` to the FastAPI backend (one-line swap; models already match).
2. Build the first **real** adapter (Blinkit — open, unsigned search API) behind the ABC.
3. Real OTP account linking + `flutter_secure_storage` token vault + DPDP consent.
4. `notify` service: scheduled re-fetch for price-drop/surge alerts.
5. Food vertical, then ONDC cab integration, then e-commerce.
6. Review-mining pass on Comparify's Play Store reviews (`google-play-scraper`) before PRD lock.
7. Bob Rides APK teardown (in a sandbox) to learn their booking mechanism.

## Honest limitations
- Demo + mocks use **simulated prices**, clearly labeled. Real data needs per-platform adapters.
- Private-API use is a **Play Policy gray zone** (tolerated in practice for user-consented access; see research §6).
- Flutter scaffold is **uncompiled** — needs `flutter analyze` on a machine with the SDK.
- GST simplified to 5% flat in the engine; per-category rates are a follow-up.
