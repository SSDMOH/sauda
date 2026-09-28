# Sauda — Product Requirements Document
**Version:** 0.1 · **Date:** 2026-09-28 · **Status:** Draft for build
**Input:** research report (`../research/research-report.md`)

## 1. Vision
India's bargain-hunter super-app. One place to compare **true prices** across food delivery,
quick commerce, cabs, and e-commerce — and automatically **split a cart across platforms**
so the user pays the absolute minimum. A "bargainer for online shopping."

## 2. Positioning
Comparify (180K downloads, ~14K/mo) proved the demand with account-linked comparison.
Sauda reaches parity on its core, then wins on the gaps Comparify leaves open:
no cart splitting, no e-commerce vertical, no price alerts, thin AI, ad-supported,
landing-page-only web. Our headline differentiator — **Smart Split Cart** — is
unclaimed by every competitor and open-source clone found in research.

## 3. Target users
- **Primary:** urban 18–40, orders food/grocery weekly, uses 2+ platforms, price-sensitive.
- **Secondary:** household grocery planners doing monthly stock-ups.

## 4. Platform coverage (phased)
- **v1 (MVP):** Quick-commerce grocery — Blinkit, Zepto, Swiggy Instamart.
  Rationale: barcode/GTIN matching is clean, prices are stable, optimizer shines.
- **v2:** Food delivery (Zomato, Swiggy) + cabs (ONDC/Beckn: Namma Yatri, Yatri Sathi,
  Bharat Taxi first — fully legitimate data path; Uber/Ola via user sessions).
- **v3:** E-commerce (Amazon, Flipkart, Myntra, Meesho, brand sites).

## 5. Features
### 5.1 Parity with Comparify (must-have)
- OTP-based account linking per platform; hardware-backed token storage; one-tap unlink.
- Real-time, account-specific prices (coupons, wallet, credits reflected).
- Per-unit pricing (₹/g, ₹/ml) normalized across pack sizes, with hidden-markup callouts.
- True-total per platform (all fees + taxes − coupons).
- Tap-to-book / cart-transfer deep links into native apps. **No in-app checkout in v1.**
- Savings tracker.

### 5.2 Sauda differentiators
1. **Smart Split Cart (headline):** optimizer assigns each item to the cheapest platform
   while honoring minimum-order values, delivery fees, and GST. Shows per-group totals,
   ETAs, one-tap deep links per group, and a "consolidate to one delivery" toggle for
   the convenience-vs-savings tradeoff.
2. **Itemized true-total bills:** expandable per-platform breakdown
   (items → delivery → platform fee → packaging → GST → − coupon = you pay).
   Verifiable math, not "trust us."
3. **Price-drop & surge alerts:** push when an item/fare crosses a user-set threshold.
4. **E-commerce vertical (v3):** Amazon/Flipkart/Myntra/brand-site comparison incl. shipping.
5. **AI assistant:** natural-language search ("2 kg atta under ₹120"), recipe → cart,
   coupon terms explained in plain language.
6. **India-first input:** Hindi + voice search; photo-of-handwritten-list scanning (v2).
7. **Ad-free, transparent monetization:** affiliate commissions disclosed; sponsors never
   ranked higher. Trust is the product.
8. **Full web companion** (not a landing page).
9. **Live / Estimated transparency:** every price badged 🟢 Live or ~ Est.; graceful
   degradation when a platform feed breaks instead of spinners or lies.

## 6. Key user flows
- **Onboarding (60 seconds to value):** phone number → link ONE platform via OTP →
  instant demo comparison → prompt to link more.
- **Grocery compare:** search item → rows per platform with per-unit price → true total →
  "Best" badge → tap for itemized bill → deep link to buy.
- **Split cart:** build list → optimizer → grouped plan (e.g., 4 items Blinkit ₹412,
  2 items Zepto ₹188) → per-group deep links → savings receipt.
- **Food (v2):** build cart once → true totals across Zomato/Swiggy → cart transfer.
- **Cabs (v2):** pickup/drop → fares incl. account coupons → tap to book.
- **Alerts:** set target price on item/fare → push notification on drop → one-tap compare.

## 7. Non-goals (v1)
In-app checkout or payments; storing raw passwords; iOS release (Flutter keeps it cheap
later, v1 is Android-first); fashion product matching; desktop-class web (mobile-first).

## 8. Success metrics
- ≥2 linked accounts per user within 7 days of install.
- Comparisons per weekly active user.
- Measured savings per order (savings receipt total).
- 30-day retention: alert users vs. non-alert users.

## 9. Risks & mitigations
- **Play policy gray zone** (private-API use): user-consented, user-scoped access only;
  accurate Data Safety declarations; direct-APK distribution contingency.
- **Adapter breakage:** one adapter per platform, health checks, Live/Est. UX.
- **Cold start:** 60-second onboarding to first value.
- **Stale prices:** re-verify at handoff time; never present estimates as live.
- **Unit economics:** human-rate fetching; affiliate revenue only if platforms tolerate us.
