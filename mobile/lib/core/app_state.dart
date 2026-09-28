import 'package:flutter/foundation.dart';

import 'api_client.dart';
import 'models.dart';

/// App-wide state: working cart, cached accounts / alerts / savings.
///
/// Provided at the root via ChangeNotifierProvider. Screens read the
/// [SaudaApi] through here so the mock <-> HTTP swap is one line in main.dart.
class AppState extends ChangeNotifier {
  final SaudaApi api;

  /// True when [api] is the live HTTP backend (vs the on-device demo mock).
  /// Drives honest labels: OTP digit hints, "Demo data" vs "Live" badges.
  final bool liveBackend;

  AppState({required this.api, this.liveBackend = false});

  // -- navigation ------------------------------------------------------------

  /// Bottom-tab index owned here so any screen can jump tabs
  /// (e.g. Home search -> Compare tab with a query).
  int navIndex = 0;

  void goToTab(int i) {
    if (navIndex != i) {
      navIndex = i;
      notifyListeners();
    }
  }

  /// Query waiting to be run by the Compare tab; consumed once.
  String? pendingQuery;

  /// Switch to the Compare tab and run [query] there.
  void openCompare(String query) {
    pendingQuery = query;
    goToTab(1);
  }

  void consumePendingQuery() {
    pendingQuery = null;
  }

  // -- cart ---------------------------------------------------------------

  final List<CartItem> _cart = [];

  List<CartItem> get cart => List.unmodifiable(_cart);

  int get cartCount => _cart.fold(0, (sum, c) => sum + c.qty);

  void addToCart(Product product) {
    final i = _cart.indexWhere((c) => c.product.id == product.id);
    if (i >= 0) {
      _cart[i].qty++;
    } else {
      _cart.add(CartItem(product: product));
    }
    notifyListeners();
  }

  void setQty(Product product, int qty) {
    final i = _cart.indexWhere((c) => c.product.id == product.id);
    if (i < 0) return;
    if (qty <= 0) {
      _cart.removeAt(i);
    } else {
      _cart[i].qty = qty;
    }
    notifyListeners();
  }

  void clearCart() {
    _cart.clear();
    notifyListeners();
  }

  /// Demo helper: fills the cart with a realistic weekly-grocery basket.
  /// Falls back to the first few catalog products when the well-known demo
  /// ids aren't present (e.g. live backend where ids are GTINs).
  Future<void> loadDemoCart() async {
    final catalog = await api.catalog();
    if (catalog.isEmpty) return;
    Product pick(String id) =>
        catalog.firstWhere((p) => p.id == id, orElse: () => catalog.first);
    final demoIds = ['p-atta', 'p-milk', 'p-salt', 'p-oil', 'p-maggi', 'p-tea'];
    final picks = demoIds.map(pick).toList();
    final List<CartItem> items;
    if (picks.toSet().length == 1 && catalog.length > 1) {
      // ids unknown — just take the first few distinct products
      items = [
        for (var i = 0; i < catalog.length.clamp(0, 6); i++)
          CartItem(product: catalog[i]),
      ];
    } else {
      items = [
        CartItem(product: picks[0]),
        CartItem(product: picks[1], qty: 2),
        CartItem(product: picks[2]),
        CartItem(product: picks[3]),
        CartItem(product: picks[4]),
        CartItem(product: picks[5]),
      ];
    }
    _cart
      ..clear()
      ..addAll(items);
    notifyListeners();
  }

  // -- cached backend state ------------------------------------------------

  List<LinkedAccount> accounts = [];
  List<PriceAlert> alerts = [];
  SavingsSummary? savings;
  List<PlatformInfo> _platforms = [];

  List<PlatformInfo> get platforms => List.unmodifiable(_platforms);

  PlatformInfo platformOf(String id) => _platforms.firstWhere(
        (p) => p.id == id,
        orElse: () => PlatformInfo(
            id: id, name: id, brandColor: 0xFF888888, tagline: ''),
      );

  bool isLinked(String platformId) =>
      accounts.any((a) => a.platformId == platformId && a.linked);

  int get linkedCount => accounts.where((a) => a.linked).length;

  Future<void> refreshAll() async {
    final results = await Future.wait([
      api.platforms(),
      api.linkedAccounts(),
      api.alerts(),
      api.savings(),
    ]);
    _platforms = results[0] as List<PlatformInfo>;
    accounts = results[1] as List<LinkedAccount>;
    alerts = results[2] as List<PriceAlert>;
    savings = results[3] as SavingsSummary;
    notifyListeners();
  }

  Future<void> refreshAlerts() async {
    alerts = await api.alerts();
    notifyListeners();
  }

  Future<void> refreshAccounts() async {
    accounts = await api.linkedAccounts();
    notifyListeners();
  }
}
