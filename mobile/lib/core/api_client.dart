import 'models.dart';

/// The Sauda backend contract as a Dart interface.
///
/// Maps 1:1 to the REST API (ARCHITECTURE.md §8):
/// - `POST /v1/compare`            -> [compare]
/// - `POST /v1/optimize-cart`      -> [optimizeCart]
/// - `GET  /v1/offers`             -> [offers]
/// - `POST /v1/alerts` / `DELETE`  -> [createAlert] / [deleteAlert]
/// - `GET  /v1/savings`            -> [savings]
/// - `POST /v1/accounts/link`     -> [startLink]
/// - `POST /v1/accounts/verify`   -> [verifyOtp]
/// - `DELETE /v1/accounts/{id}`   -> [unlink]
///
/// This is a real interface — no `UnimplementedError` stubs. The app ships
/// with [MockSaudaApi] implementing it; swapping in the HTTP client later
/// touches no call sites.
abstract class SaudaApi {
  /// Platforms Sauda can compare (v1: Blinkit, Zepto, Swiggy Instamart).
  Future<List<PlatformInfo>> platforms();

  /// Searchable product catalog (used for pickers and demo carts).
  Future<List<Product>> catalog();

  /// Compare true prices for [query] at the user's location.
  /// `POST /v1/compare {query, lat, lng}`.
  Future<List<ComparisonResult>> compare({
    required String query,
    required double lat,
    required double lng,
  });

  /// Split [items] across platforms for the minimum true total.
  /// `POST /v1/optimize-cart {items, lat, lng, max_platforms}`.
  Future<SplitPlan> optimizeCart({
    required List<CartItem> items,
    required double lat,
    required double lng,
    int maxPlatforms = 3,
  });

  /// Coupons applicable right now, optionally filtered to one platform.
  /// `GET /v1/offers?platform=`.
  Future<List<Offer>> offers({String? platformId});

  /// The user's price-drop alerts. `GET /v1/alerts`.
  Future<List<PriceAlert>> alerts();

  /// Subscribe to a price drop. `POST /v1/alerts`.
  Future<PriceAlert> createAlert({
    required String productId,
    required double targetPrice,
  });

  /// Remove an alert. `DELETE /v1/alerts/{id}`.
  Future<void> deleteAlert(String id);

  /// Savings ledger summary (rings, streaks, lifetime).
  /// `GET /v1/savings`.
  Future<SavingsSummary> savings();

  /// Linked platform accounts and their status.
  Future<List<LinkedAccount>> linkedAccounts();

  /// Start OTP linking for [platformId]. Returns a link reference.
  /// `POST /v1/accounts/link`.
  Future<String> startLink({
    required String platformId,
    required String phone,
  });

  /// Verify the OTP; on success the account is linked.
  /// `POST /v1/accounts/verify`.
  Future<LinkedAccount> verifyOtp({
    required String linkRef,
    required String otp,
  });

  /// Unlink a platform (tokens deleted everywhere).
  /// `DELETE /v1/accounts/{platformId}`.
  Future<void> unlink({required String platformId});
}
