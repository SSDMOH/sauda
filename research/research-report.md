# Sauda — Research Report
**Codename:** Sauda · **Date:** 2026-09-28 · **Author:** research subagent
**Objective:** Deep research to feed the PRD + build phase for an Android-first, web-companion price-comparison app for India (food delivery, quick commerce, cabs, e-commerce) using user-linked accounts, with cart-split optimization as the headline differentiator.

**Method note:** All claims below come from public sources fetched during research (Play Store listings, news articles, GitHub repos, policy docs). Where a claim rests on a single source or could not be verified, it is flagged explicitly.

---

## 1. Comparify teardown (com.akshat.comparify)

### 1.1 Identity & traction
- **App:** "Comparify: Cabs & Groceries", publisher "Comparify" (developer Akshat Kejriwal), category Shopping.
- **Platforms:** Android since **July 2025**; iOS since **Dec 30, 2025** (App Store ID 6757003836 — confirmed via MWM store intelligence and a YouTube promo; the Aug-2025 press called it "Android-only for now").
- **Version:** 2.1.4, updated **Sept 3, 2026**. APK ~49.4 MB. **Free, contains ads** (AppBrain).
- **Traction:** ~**180,000 downloads** total, ~14,000 in the last 30 days (AppBrain, crawled ~2 days before this report). Steady growth, not viral — roughly 1 year to 180K.
- **Web:** comparify.pro — currently a thin landing page with outbound tiles to platforms (Blinkit, Instamart, Zepto, JioMart, Flipkart, BigBasket, DMart, DealShare, MilkBasket, "Amazon Now"). The real product is the mobile app.
- **Press:** india.com and newsbytesapp (both ~Aug 7–8, 2025) covered the launch with near-identical framing: link accounts via OTP → see real-time, account-specific prices (coupons, wallet balance included).

### 1.2 Feature inventory (from Play Store listing, v2.1.4)
1. **Cab fare comparison** — Uber, Ola, Rapido, Namma Yatri, Yatri Sathi, Bharat Taxi. Uses *your* account prices incl. coupons/credits. "Tap to book" with pickup/drop pre-filled (deep link into the ride app).
2. **Grocery comparison** — Blinkit, Swiggy Instamart, Zepto, BigBasket, Flipkart Minutes, JioMart, MilkBasket, DMart. **Per gram/ml pricing** to catch pack-size markups; identical-product matching across pack sizes.
3. **Food delivery comparison** (added later — not in the earliest listing) — Zomato, Swiggy, and "Toing". Shows the *final payable amount* after coupons, platform fees, delivery charges, taxes. "Build your cart once, compare the true total across apps, then transfer your cart" to the chosen app.
4. **Smart features:** real-time account-specific pricing; final prices with coupons/fees; shopping-list builder with total-savings view; **savings tracker** ("track how much you save over time"); "fast, clean interface — no clutter".
5. **Marketing claims:** "average users save ₹50–100 per cab ride and 15–30% on grocery bills."

### 1.3 How it works (confirmed by two press sources)
- User links each platform account inside Comparify via **OTP verification**; the app then fetches **real-time prices as that user** — including account-specific coupons, credits, and wallet balances. This is the entire technical moat: no public APIs are used; the app replays each platform's private/mobile endpoints with the user's own session.
- Checkout/booking is **not** done inside Comparify — it deep-links / transfers the cart into the native app ("tap to book", "cart transfer"). This matches the phased approach we planned: compare here, transact there.

### 1.4 User reviews — what users love vs. complain about
**Honest flag: I could not comprehensively verify this.** Play Store review text is JavaScript-rendered and not fetchable with available tools; AppBrain shows "0 reviews / no ratings" for the app (almost certainly a fetch failure on their side given 180K downloads, but I cannot confirm a rating number); YouTube comments were not retrievable; no Reddit/Quora/Trustpilot threads about Comparify surfaced. Treat the love/complain split below as *inferred from the product model + adjacent evidence*, not as verified review mining:
- **Likely loved:** the core value prop (one screen vs. app-hopping), account-specific true totals, per-unit grocery pricing, savings tracker gamification. 14K downloads/month a year after launch suggests word-of-mouth is working.
- **Likely complaints (structural, high confidence):** (a) **account-linking friction** — every platform needs a separate OTP link, and sessions expire → re-linking; (b) **coverage gaps** — comparison only works for platforms the user has linked, and any platform API change silently breaks that vertical until the dev ships a fix; (c) **ads** in a money-saving app create distrust ("are these 'best prices' sponsored?"); (d) food delivery is the newest vertical and "cart transfer" is inherently clunky vs. native checkout.
- **Recommendation:** before PRD lock, run a proper review-mining pass (Play Console scraping via `google-play-scraper`, or manual sampling) to replace these inferences with data.

### 1.5 Concrete gaps we can exploit
1. **No cart splitting.** Comparify compares *per-platform totals* and transfers one cart. Nobody ships "split this cart across Blinkit + Zepto for the true minimum" — not even the open-source clones (QuickCompare lists split-cart optimization as *Phase 3, planned*). This is our headline feature and it is genuinely unclaimed.
2. **No e-commerce vertical.** No Amazon / Flipkart / Myntra / Meesho / brand-site comparison. (PriceHunt, a hobby GitHub project, covers 9 platforms incl. these — proving demand, but it's not a real product.)
3. **No price alerts.** No "notify me when this fare/item drops below ₹X" (the RideGuru clone has a "Surge Drop Alert" concept; Comparify doesn't list one).
4. **Thin AI.** Their "smart" is rules-based lists. Natural-language search ("atta under ₹60"), recipe-to-cart, and AI offer parsing are open.
5. **Language & accessibility.** No Hindi/regional voice search (the GroceryCompare clone ships EN+HI voice + photo-of-handwritten-list scanning — both worth copying).
6. **Ad-supported.** A clean, ad-free experience (or transparent "we earn affiliate commission, never rank sponsors higher") is a trust differentiator.
7. **Web app is a landing page.** A full-featured web companion (like ours plans) is uncontested.
8. **Transparency when feeds break.** PriceHunt labels prices "🟢 Live" vs "~ Est." — Comparify's listing promises "real-time" with no visible fallback story. A graceful-degradation UX is a reliability win.

---

## 2. Competitor scan

### 2.1 Shipped products
| App | Coverage | Model | Notes |
|---|---|---|---|
| **Comparify** (Akshat Kejriwal) | Cabs, grocery, food | User-linked accounts (OTP), deep-link checkout | 180K downloads, ads, Android+iOS. Direct incumbent. |
| **Bob Rides** (justbobit.com, Bengaluru) | Cabs only (Ola, Uber, Rapido, Namma Yatri) | Claims **in-app booking** without leaving the app | Launched Sept 2025; **₹2 crore Microsoft grant** (~$250K incl. $150K cloud credits). Founders: Jai Adithya Poorana, Ansh Arora. No online payment/wallet — pay driver via UPI/cash. *How their in-app booking technically works is unverified* — worth a teardown during build. |

### 2.2 Open-source / hobby projects (strong signal of demand + feature ideas)
- **QuickCompare** (`swaekaa/retail-comp`) — "Skyscanner for quick commerce" (Blinkit, Zepto, Instamart, BigBasket Now, Flipkart Minutes). Next.js 15 + **FastAPI** + Postgres + Redis. Roadmap explicitly lists *Phase 3: split-cart optimization (planned)* — validates our differentiator and shows nobody has shipped it.
- **CartSavvy** (`dhruvmansotraa/cartsavvy`) — React Native + Node/Express, "smart cart optimization", platform-adapter architecture, mock data ready for real APIs.
- **GroceryCompare AI** (`sourabhsavre/grocery-compare`) — Zepto/BigBasket/Blinkit + **voice search (EN+HI)**, **photo-of-grocery-list scanning**, recipe cost estimator, monthly planner with WhatsApp share.
- **PriceHunt** (`hapy8/pricehunt`) — 9 platforms incl. **Amazon, Flipkart, Myntra, Meesho, JioMart**; "🟢 Live vs ~ Est." price labels; paste-a-product-link search.
- **Kartbot** (`hritik-74/kartbot`) — WhatsApp-based comparison; **Playwright + stealth plugin** scraping; Node 20 + TS + Postgres + Redis/BullMQ on AWS Mumbai.
- **RideGuru** (`hackathon-nareshit/rideguru-intelligent-ride-picker`) — fare comparison + **Groq LLaMA recommendations**, **surge-drop push alerts**, fare-splitter with UPI deep links, ride history + savings tracking.
- **Savvify** (`sumitbiniyadav/savvify-`) — different angle: **spend tracking via Gmail OAuth receipt parsing** (no scraping at all). Complementary feature idea: auto-detect orders from email to power the savings tracker.
- **CartFiller** (`siddhanthkapoor/cartfiller`) — Chrome extension that fills Blinkit/Zepto/Instamart carts from a recipe **using the user's own logged-in browser session** — a proven UX pattern for cart transfer without storing credentials.
- **Khanabazaar** (`rishimule/khanabazaar`) — open-source store-comparison with a documented **imputation algorithm** for fair cross-store cart comparison (see §5).

### 2.3 The ONDC factor (structural opportunity)
Namma Yatri, Yatri Sathi, and Bharat Taxi run on the **open Beckn protocol via ONDC** — meaning a registered Buyer App (BAP) can *legitimately* query fares and even transact without any ToS gray zone (CNBC TV18, Sept 2026: Namma Yatri described as "open-network mobility application built on common network standards and the open-source Beckn protocol"; Bharat Taxi ~800K drivers, 4.1M customers). ONDC also covers retail/food/grocery seller networks. **Implication:** our cab vertical (and later grocery/food) can have a fully legitimate data path via ONDC registration, while private-API adapters cover the closed platforms. This is a hedge no competitor is visibly exploiting yet.

---

## 3. Technical approach

### 3.1 How account-linked comparison works on Android
The Comparify model, reconstructed from press + public reverse-engineering work:
1. **Linking:** user enters their phone number per platform; the app proxies that platform's *own* OTP endpoints (request OTP → submit OTP) or opens the platform login in a Custom Tab / WebView and captures the resulting session (cookies or tokens). India.com explicitly describes "linking your accounts through OTP verification."
2. **Fetching:** the app replays the platform's private/mobile API calls **with the user's session attached** — i.e., it sees exactly what the native app sees, including personalized coupons, wallet balance, and surge.
3. **Storage:** session tokens live on-device (Keystore/EncryptedSharedPreferences ideally); price fetching can happen on-device or via backend proxy that holds per-user tokens.
4. **Checkout:** deep link / universal link into the native app with cart or trip pre-filled (`m.uber.com/ul/`, `ola.app.link`, etc.). Nobody in this category does credential-based auto-checkout — OTPs, 2FA, and payment auth make it operationally infeasible.

### 3.2 Per-platform endpoint notes (verified from public sources)
- **Swiggy (food):** `GET https://www.swiggy.com/dapi/restaurants/list/v5?lat=&lng=…` for listings; `GET https://www.swiggy.com/dapi/menu/pl?…&restaurantId=` for menus. Depends on **browser session state / cookies** (ShopLens: reads `swiggy_cookie.txt`, extracts device id; location cookies generatable via Playwright).
- **Zomato:** `POST https://www.zomato.com/webroutes/search/home` with `"context":"delivery"` — paginates by delivery *zone*, not coordinates (universal-agent-skills write-up).
- **Blinkit:** search API is **open and unsigned** (CartFiller, verified working); layout search endpoint returns widget snippets (`product_card_snippet_type_2`) with pagination via `next_url` (ShopLens). Easiest target.
- **Zepto:** **signs every API request** (anti-bot) — cannot be called directly; must drive the platform's own UI/session or extract signing (CartFiller, ShopLens). Needs serviceability endpoint (lat/lon → store), `storeId` headers, ETA maps.
- **Flipkart Minutes:** page-fetch API with `locationContext` (pincode) and `marketplace=HYPERLOCAL` (ShopLens).
- **Swiggy Instamart:** session-header search; product ids are canonical for deep links.
- **Uber/Ola:** no usable public fare API (Uber's old developer server-token API is long deprecated for this use). Realistic paths: user-session replay, or ONDC-registered players (Namma Yatri/Yatri Sathi/Bharat Taxi) via Beckn.

### 3.3 What breaks, how often, and maintenance reality
- **Breakage vectors:** request signing rotation (Zepto-style), endpoint renames, response-schema drift, new bot detection (Cloudflare/TLS fingerprinting), session-expiry policy changes, OTP flow changes.
- **Cadence:** no hard public data, but CartRadar's README warns *"quick-commerce APIs change frequently and some platforms block automated access"* — budget for **continuous adapter maintenance**: expect *something* to break every few weeks across a 10+ platform fleet. This is a standing engineering cost, not a one-time integration.
- **Maintenance playbook (from the wild):** one adapter per platform behind a common interface (CartSavvy, QuickCompare both do this); automated health checks per adapter; graceful degradation — PriceHunt's "🟢 Live / ~ Est." labels are the right UX; bounded concurrency + backoff to stay under rate limits (CartFiller); Playwright-with-stealth as the fallback when pure HTTP replay fails (Kartbot, ShopLens).

### 3.4 Reference architectures seen in the wild
- **QuickCompare:** Next.js 15 + Tailwind + shadcn/ui → **FastAPI (Python 3.12)** + Postgres 16 + Redis 7, Turborepo monorepo, Docker. Closest to a production shape for our backend.
- **Kartbot:** Node 20 + TS + Express, Postgres + Drizzle, Redis + BullMQ, Playwright-stealth scrapers, Docker Compose, AWS Mumbai.
- **blinkit-zepto-scraper:** **Expo (React Native)** + FastAPI + Playwright + AsyncIO — proof that Expo + Python backend is a workable combo for exactly this app.
- **ONDC BAP reference** (theastiv/paranoid): Go backend + Kotlin Android, Beckn Ed25519 signing, GCP Mumbai (DPDP data residency noted).

---

## 4. Stack recommendation

### 4.1 Mobile — recommend **Flutter**
| Need | Flutter | React Native (Expo) | Native Kotlin |
|---|---|---|---|
| Custom comparison UI (per-unit rows, savings charts, split-cart viz) | **Best** — Impeller renderer, pixel-identical iOS/Android | Good (Fabric) | Best, but 2 codebases |
| Account linking (WebView/Custom Tabs OTP) | `webview_flutter` / `url_launcher` — fine | Fine | Fine |
| Background price refresh | `workmanager` — solid | `expo-background-fetch` — iOS limits hurt | WorkManager — best |
| Deep linking into other apps | `app_links` — fine | Fine | Fine |
| Secure token storage | `flutter_secure_storage` (Keystore/Keychain) | `expo-secure-store` | Jetpack Security |
| Talent pool (India) | Large, growing | **Largest** (JS/TS) | Large |
| Web companion from same codebase | Flutter web — usable | React Native web — weaker | N/A (need separate web) |
| Code sharing | 85–95% | 80–95% | 40–60% (KMP logic only) |

**Why Flutter over the others for Sauda specifically:** (1) our UI *is* the product — dense comparison tables, per-unit pricing, savings visualizations, split-cart diagrams — and Flutter's consistent rendering + animation story is the strongest fit; (2) we need Android + iOS + web from one team; (3) background refresh + secure storage + deep links are all solved plugins. **React Native/Expo is the legitimate runner-up** — pick it instead if the founding team is JS/TS-native (faster hiring, and the one public Expo+FastAPI scraper project proves the combo). Avoid pure-native Kotlin unless we deliberately want Android-only v1 (it doubles iOS cost later; KMP only shares logic, not the UI we care about).

### 4.2 Backend — recommend **Python FastAPI + Postgres + Redis**
- **Why:** the price-ingestion layer is fundamentally a Python strength (Playwright, httpx, reverse-engineering tooling, the entire corpus of public scraper/adapter code is Python/Node). FastAPI gives us a typed API for the mobile/web clients; **Postgres** for catalog/entities/offers; **Redis** for price caches (TTL in minutes), per-user session metadata, and job queues (Celery/Dramatiq) for scheduled re-fetches and price-drop alerts.
- **Services:** `ingestion` (platform adapters, health-checked), `matching` (entity resolution), `pricing` (true-total engine), `optimizer` (cart-split), `offers` (coupon rules), `notify` (alerts). One deployable monolith first, split later.
- **Alternative:** Node.js + TypeScript (Kartbot proves it) — choose if the team is JS-only; but Python wins for the scraping/ML-adjacent work (fuzzy matching, offer NLP).

### 4.3 Web companion — **Next.js** (same API, Tailwind + shadcn/ui). QuickCompare uses exactly this pair with FastAPI.

---

## 5. UI/UX patterns worth copying
1. **Per-unit pricing in list rows, styled secondary.** Baymard Institute research: **86% of sites fail to display price-per-unit**, forcing users to do the math themselves — a top abandonment driver. Comparify does ₹/g,₹/ml; we should do it *better*: per-unit under every price, normalized across pack sizes, with the "hidden markup" callout when a bigger pack costs more per unit.
2. **Itemized true-total breakdown.** Expandable bill per platform: items → delivery fee → platform fee → packaging → GST → − coupon = **you pay**. Nobody itemizes this in a comparison context today; it turns "trust us, this is cheapest" into verifiable math.
3. **Split-cart visualization.** For the optimizer: group cart items by winning platform, show per-group totals + ETAs + one-tap deep links, and a "consolidate to 1 delivery" toggle for the convenience-vs-savings tradeoff. Khanabazaar's open-source imputation UI is a good reference pattern: `At this store ₹418 (4/5) + stays at A ₹80 (1/5) = combined ₹498`.
4. **Savings as a product surface.** Per-order "savings receipt" (you saved ₹132 vs. the most expensive option), monthly ring/streaks, lifetime total — CRED/Fi-style gamification of thrift. Comparify has a basic tracker; ours should be the home-screen hero.
5. **Price-drop / surge alerts.** "Alert me when this cab fare drops below ₹120" (RideGuru's Surge Drop Alert concept) and "atta is ₹8 cheaper on Zepto than your usual Blinkit" — push-driven re-engagement that Comparify doesn't offer.
6. **Live vs. estimated transparency.** PriceHunt's "🟢 Live / ~ Est." badges — when a platform feed is down, show the last-known price clearly labeled instead of a spinner or a lie.
7. **India-specific input.** Voice search in English + Hindi and photo-of-handwritten-list scanning (both shipped by the GroceryCompare clone) — meaningful accessibility edge in our market.

---

## 6. Risk notes

### 6.1 Google Play policy
- **Device and Network Abuse** is the sharp edge: Play policy explicitly forbids *"accessing or using any API in a way that violates that API's own Terms of Service"* (cited in the apexcore compliance checklist against official policy). Replaying private APIs against platform ToS falls squarely in this bucket.
- **Enforcement is account-level** (termination, effectively unappealable), not just app rejection — and third-party SDKs doing forbidden things make the *developer* liable.
- **Deceptive Behavior:** the listing must match the app exactly; "real-time prices" claims must hold.
- **Data Safety section** mismatches are the #1 rejection cause — every collection (location, tokens, order data) must be declared accurately.
- **Counterpoint (practical):** Comparify has operated on Play since July 2025 and Bob Rides since Sept 2025 under this exact model without removal — suggesting **tolerance in practice**, likely because access is user-consented and user-scoped. But tolerance ≠ permission; policy text gives Google grounds to act at any time.

### 6.2 Platform ToS & countermeasures
- Every target platform's ToS prohibits automated access / reverse engineering. Realistic consequences, in descending likelihood: **technical blocks** (request signing like Zepto, Cloudflare/bot walls, session invalidation, rate limits) → **account action** against linked user accounts (rare, but possible) → legal action (no public cases found in India against price-comparison apps — the NRAI/CCI fights are platform-vs-restaurant, not platform-vs-scraper).
- **Mitigations:** (a) user-consent framing everywhere (we act *as the user*, on *their* data — the Comparify precedent); (b) human-rate request patterns, bounded concurrency, backoff; (c) adapter-per-platform architecture with health monitoring + graceful degradation; (d) prefer legitimate channels where they exist — **ONDC/Beckn registration for mobility** (and later retail), **affiliate APIs** for Amazon/Flipkart where available; (e) never store raw credentials — session tokens only, in hardware-backed secure storage, with a clear privacy policy and one-tap account unlinking; (f) keep a direct-APK distribution contingency (F-Droid/GitHub-style) if Play ever objects — several open-source projects ship this dual-track model.

### 6.3 Product risks
- **Cold start:** comparison is only as good as linked accounts — onboarding must make OTP-linking feel rewarding within 60 seconds (link 1 cab app → instant fare comparison).
- **Stale-price liability:** showing a wrong "cheapest" erodes trust fast — hence Live/Est. labels and re-verification at tap-to-book time.
- **Unit economics:** scraping infra + background refresh costs scale with users × platforms; affiliate commissions (the likely revenue path) only work if platforms tolerate us — the same platforms we're arbitraging.

---

## 7. What we could not verify / open questions for build phase
1. Comparify's actual user rating and the content of its Play Store reviews (JS-rendered; needs `google-play-scraper` or manual sampling).
2. How Bob Rides implements in-app cab booking (deep links vs. proxied booking vs. ONDC) — needs an APK teardown.
3. Whether Swiggy/Zomato/Blinkit/Zepto have ever sent legal notices to comparison/scraper apps in India (no public record found; absence of evidence ≠ evidence of absence).
4. Exact OTP/session mechanics Comparify uses per platform (needs APK decompilation or traffic observation — do this in a sandbox during build).
5. Current Uber/Ola fare-estimate API availability for third parties (official API long deprecated; assume unavailable).

---

## Sources
- Play Store listing (com.akshat.comparify): https://play.google.com/store/apps/details?id=com.akshat.comparify&hl=en_IN
- AppBrain stats: https://www.AppBrain.com/app/comparify-cabs-groceries/com.akshat.comparify
- MWM store intelligence: https://mwm.ai/apps/comparify-cabs-groceries/6757003836
- india.com launch article: https://www.india.com/business/this-app-will-help-you-in-checking-grocery-rates-on-uber-ola-blinkit-zepto-and-swiggy-in-real-time-name-is-android-comparify-pro-akshat-kejriwal-8001558/
- newsbytesapp: https://www.newsbytesapp.com/news/science/this-new-app-helps-you-compare-uber-blinkit-ola-fares/tldr
- Bob Rides (Moneycontrol): https://www.moneycontrol.com/news/trends/bengaluru-app-compares-ola-uber-rapido-fares-for-cheapest-ride-got-rs-2-crore-grant-from-microsoft-13623534.html
- Bob Rides (StartupPedia): https://startuppedia.in/startup-stories/done-with-juggling-between-ola-uber-rapido-for-cheap-rides-entrepreneurs-from-blr-built-an-app-to-compare-book-rides-in-one-place-10561715
- CNBC TV18 on ONDC/open mobility networks: https://www.cnbctv18.com/business/how-saas-and-open-networks-are-reshaping-indias-ride-hailing-model-beyond-big-apps-19995978.htm
- Swiggy/Zomato endpoint write-up: https://github.com/mehanshbarthwal-lab/universal-agent-skills/blob/HEAD/skills/locality-delivery-scraper/SKILL.md
- CartFiller (Blinkit open API / Zepto signing): https://github.com/siddhanthkapoor/cartfiller
- ShopLens (per-platform scraper engineering): https://github.com/ojasmagarwal/shoplense
- CartRadar (API churn warning): https://github.com/Harsh-Gopal/CartRadar
- QuickCompare ("Skyscanner for quick commerce", FastAPI+Next.js): https://github.com/swaekaa/retail-comp
- CartSavvy (RN + adapter architecture): https://github.com/dhruvmansotraa/cartsavvy
- GroceryCompare AI (voice HI/EN, list scanning): https://github.com/sourabhsavre/grocery-compare
- PriceHunt (9 platforms, Live/Est. labels): https://github.com/hapy8/pricehunt
- Kartbot (Playwright stealth, Node+TS): https://github.com/hritik-74/kartbot
- RideGuru (surge alerts, fare splitter): https://github.com/hackathon-nareshit/rideguru-intelligent-ride-picker
- Savvify (Gmail-OAuth spend tracking): https://github.com/sumitbiniyadav/savvify-
- Khanabazaar (imputation algorithm + UI): https://github.com/rishimule/khanabazaar/blob/HEAD/docs/price_comparison.md and /appendix-store-comparison-picking.md
- Baymard on price-per-unit (86% fail): https://baymard.com/research-articles/price-per-unit
- Flutter vs RN vs KMM 2026: https://softaims.com/blog/cross-platform-mobile-react-native-flutter-kmm-2026 and https://www.javacodegeeks.com/2026/02/kotlin-multiplatform-vs-flutter-vs-react-native-the-2026-cross-platform-reality.html
- Play policy compliance notes: https://github.com/abhay-byte/apexcore/blob/HEAD/docs/Google_Play_Store_Policy_Compliance_Checklist.md and https://github.com/georgeshani/skills/blob/HEAD/skills/app-store-compliance/references/google-play.md
- ONDC BAP reference: https://github.com/theastiv/paranoid/blob/HEAD/examples/stride-example-ondc-quick-commerce.md
