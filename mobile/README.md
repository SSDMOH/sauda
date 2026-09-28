# Sauda — Mobile App (Flutter, Android-first)

India's bargain-hunter super-app: compare **true prices** across food delivery,
quick commerce, cabs and e-commerce — and **split a cart across platforms** to
pay the absolute minimum.

Implements the full v1 grocery experience from `../docs/PRD.md` (features §5)
against the API contract in `../docs/ARCHITECTURE.md` (§8). The app **talks to
the live FastAPI backend when it's reachable** and falls back to an on-device
demo mock otherwise — the Home screen always shows which mode it's in
(`LIVE BACKEND` / `DEMO DATA` badge).

## What's in here

```
mobile/
├── pubspec.yaml                 # provider, http, url_launcher, flutter_secure_storage, fl_chart
├── README.md
├── android/                     # com.sauda.app, minSdk 24, generated launcher icons
├── tooling/make_icon.py         # launcher-icon generator (PIL)
└── lib/
    ├── main.dart                # async startup: probe backend → live or mock;
    │                            #   bottom-nav shell (5 tabs, IndexedStack, cart badge)
    ├── core/
    │   ├── models.dart          # Product, PlatformPrice, TrueTotalBill, SplitPlan,
    │   │                        # Offer, PriceAlert, SavingsEntry/Summary, LinkedAccount…
    │   ├── api_client.dart      # SaudaApi — abstract interface mirroring the REST contract
    │   ├── app_state.dart       # ChangeNotifier: cart, navIndex/pendingQuery (tab jumps),
    │   │                        #   cached accounts/alerts/savings, demo-cart seeding
    │   ├── theme.dart           # deep-green + warm-amber fintech theme (light + dark)
    │   ├── format.dart          # INR formatting with lakh/crore grouping (₹1,24,500)
    │   ├── deeplinks.dart       # platform handoff (url_launcher, external browser)
    │   └── widgets.dart         # FreshnessBadge, BestBadge, BillLinesView, …
    ├── data/
    │   ├── mock_service.dart    # MockSaudaApi: 8 grocery products × 3 platforms,
    │   │                        #   fee tables, coupons, subset-enumeration optimiser,
    │   │                        #   savings ledger, OTP-link simulation (any 4 digits)
    │   └── http_api.dart        # HttpSaudaApi: live backend client. Translates the
    │                            #   backend's platform-major JSON into app models.
    │                            #   Verified end-to-end against the real backend
    │                            #   (22/22 integration checks pass — see below).
    └── features/
        ├── home/                # savings hero ring + streak, search (jumps to Compare
        │                        #   tab with the query), vertical chips, platform link
        │                        #   status, recent comparisons, LIVE/DEMO data badge
        ├── compare/             # per-platform rows, ₹/kg-₹/ml per-unit pricing, BEST badge,
        │                        #   LIVE / EST badges, hidden-markup callouts,
        │                        #   expandable itemised true-total bills, Add-to-cart +
        │                        #   real "Buy on …" platform handoff
        ├── split_cart/          # optimiser plan: groups per platform + ETAs + bills,
        │                        #   "Consolidate to 1 delivery" toggle, per-group order
        │                        #   buttons, savings receipt card, cart qty steppers
        ├── accounts/            # per-platform link cards + OTP bottom-sheet flow
        │                        #   (4 digits on mock, 6 on live backend)
        ├── alerts/              # price-drop alerts with target-vs-best progress + set-alert sheet
        └── savings/             # lifetime hero, 30-day bar chart (fl_chart), monthly
                                #   goal ring, per-order receipts
```

## Screen inventory

| Tab / screen | What it does |
|---|---|
| Home | Savings ring vs goal, streak, search entry (jumps to Compare tab), vertical chips (Grocery live; Food/Cabs/E-commerce "soon"), linked-platform dots, recent comparisons, LIVE/DEMO badge |
| Compare | Search → product cards with platform rows sorted by true total, per-unit pricing, BEST / LIVE / EST badges, markup callouts, expandable itemised bills, Add-to-cart + real Buy handoff |
| Split Cart | Optimised plan grouped by winning platform (items, ETAs, itemised bills), consolidate toggle, savings receipt (vs one platform, vs priciest), per-group order buttons, tab badge with cart count |
| Accounts | Link/unlink Blinkit, Zepto, Instamart via OTP sheet (any 4 digits on mock, any 6 on live backend) |
| Alerts | Active price-drop alerts with progress bars + target-hit badges, set-alert bottom sheet |
| Savings | Lifetime total, 30-day savings bar chart, monthly goal ring, receipt ledger |

## How to run

```bash
# 1. backend (terminal 1)
cd ~/workspace/sauda/backend
.venv/bin/uvicorn api:app --reload          # serves http://127.0.0.1:8000

# 2. app (terminal 2)
cd ~/workspace/sauda/mobile
flutter pub get
flutter run                                 # emulator: app probes http://10.0.2.2:8000

# physical device — point at your machine's LAN IP:
flutter run --dart-define=SAUDA_API=http://192.168.1.5:8000
```

No backend running? The app boots into the on-device demo mock automatically
and says `DEMO DATA` on Home — every screen still works.

> No Flutter SDK is installed in this workspace, so the project has not been
> compiled here. What *was* verified here: `dart analyze` is clean on all
> pure-Dart files (`models`, `format`, `api_client`, `mock_service`), and
> `http_api.dart` is clean plus **22/22 integration checks pass against the
> real backend** (platforms, compare, split-cart, offers, alerts, savings,
> link/verify/unlink, catalog) — see `../tooling/apicheck/bin/check.dart`.
> On a machine with Flutter, `flutter analyze` + `flutter run` are the
> remaining gates.

## Regenerating the launcher icon

```bash
python3 tooling/make_icon.py   # writes mipmap-{mdpi…xxxhdpi}/ic_launcher.png
```

## What remains (before production)

1. **Real OTP linking** — `verifyOtp` is simulated on both mock and backend;
   store returned session tokens in `flutter_secure_storage`
   (Keystore/Keychain), never raw passwords; one-tap unlink must delete
   tokens server-side too.
2. **Cart-transfer deep links** — `core/deeplinks.dart` currently opens each
   platform's storefront in the external browser; replace with per-platform
   cart-transfer links (with Play-Store fallback) for true tap-to-order.
3. **FCM for alerts** — register the device token; scheduled re-fetch jobs
   live server-side (§9). Client-side alerts don't survive app restart yet
   (no `GET /v1/alerts` on the backend in v1).
4. **Location** — replace the hard-coded lat/lng (Connaught Place) with the
   geolocator flow + DPDP consent screen.
5. **v2 verticals** — Food (Zomato/Swiggy), Cabs (ONDC/Beckn first), then
   E-commerce; each is a new feature folder + platform adapters server-side.
6. **Polish** — splash screen, Hindi strings, voice search, Play Data Safety
   declaration (location, tokens, order data).
