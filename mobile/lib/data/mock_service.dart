import '../core/api_client.dart';
import '../core/models.dart';

/// Demo implementation of [SaudaApi] with realistic, deterministic data.
///
/// Mirrors the mock-adapter idea from ARCHITECTURE.md §3: same interface as
/// the real backend, so every screen renders and every flow is clickable
/// with zero backend. Swap for the HTTP client in main.dart when ready.
class MockSaudaApi implements SaudaApi {
  // -- platforms -----------------------------------------------------------

  static const List<PlatformInfo> _platforms = [
    PlatformInfo(
      id: 'blinkit',
      name: 'Blinkit',
      brandColor: 0xFFF8CB46,
      tagline: 'Groceries in 10 minutes',
    ),
    PlatformInfo(
      id: 'zepto',
      name: 'Zepto',
      brandColor: 0xFFFF2459,
      tagline: '10-min delivery, honest prices',
    ),
    PlatformInfo(
      id: 'instamart',
      name: 'Instamart',
      brandColor: 0xFFFC8019,
      tagline: 'Swiggy\u2019s quick commerce',
    ),
  ];

  // -- fee tables (per platform; versioned per city on the backend) ----------

  static const Map<String, _FeeTable> _fees = {
    'blinkit': _FeeTable(
        deliveryFee: 25, freeAbove: 199, platformFee: 5, packagingFee: 4,
        minOrder: 99, etaMinutes: 12),
    'zepto': _FeeTable(
        deliveryFee: 25, freeAbove: 199, platformFee: 5, packagingFee: 3,
        minOrder: 99, etaMinutes: 10),
    'instamart': _FeeTable(
        deliveryFee: 30, freeAbove: 249, platformFee: 6, packagingFee: 4,
        minOrder: 149, etaMinutes: 15),
  };

  // -- catalog ---------------------------------------------------------------

  static const List<Product> _products = [
    Product(id: 'p-atta', gtin: '8901262000031', brand: 'Aashirvaad',
        name: 'Shudh Chakki Atta', packSize: '5 kg', packQty: 5, unit: 'kg'),
    Product(id: 'p-milk', gtin: '8901262000017', brand: 'Amul',
        name: 'Taaza Toned Milk', packSize: '1 L', packQty: 1, unit: 'L'),
    Product(id: 'p-salt', gtin: '8901262000024', brand: 'Tata',
        name: 'Salt', packSize: '1 kg', packQty: 1, unit: 'kg'),
    Product(id: 'p-oil', gtin: '8901262000048', brand: 'Fortune',
        name: 'Sunflower Oil', packSize: '1 L', packQty: 1, unit: 'L'),
    Product(id: 'p-maggi', gtin: '8901262000055', brand: 'Maggi',
        name: '2-Minute Noodles Family Pack', packSize: '840 g',
        packQty: 840, unit: 'g'),
    Product(id: 'p-tea', gtin: '8901262000062', brand: 'Tata',
        name: 'Tea Gold', packSize: '500 g', packQty: 500, unit: 'g'),
    Product(id: 'p-butter', gtin: '8901262000079', brand: 'Amul',
        name: 'Butter', packSize: '500 g', packQty: 500, unit: 'g'),
    Product(id: 'p-dal', gtin: '8901262000086', brand: 'Tata Sampann',
        name: 'Unpolished Toor Dal', packSize: '1 kg', packQty: 1, unit: 'kg'),
  ];

  /// price, mrp per product per platform.
  static const Map<String, Map<String, List<double>>> _priceTable = {
    'p-atta': {'blinkit': [285, 320], 'zepto': [279, 320], 'instamart': [290, 320]},
    'p-milk': {'blinkit': [72, 75], 'zepto': [71, 75], 'instamart': [74, 75]},
    'p-salt': {'blinkit': [28, 30], 'zepto': [27, 30], 'instamart': [29, 30]},
    'p-oil': {'blinkit': [142, 160], 'zepto': [145, 160], 'instamart': [140, 160]},
    'p-maggi': {'blinkit': [168, 180], 'zepto': [165, 180], 'instamart': [170, 180]},
    'p-tea': {'blinkit': [265, 290], 'zepto': [270, 290], 'instamart': [262, 290]},
    'p-butter': {'blinkit': [285, 305], 'zepto': [290, 305], 'instamart': [282, 305]},
    'p-dal': {'blinkit': [165, 185], 'zepto': [160, 185], 'instamart': [168, 185]},
  };

  /// A couple of feeds are estimates right now — the UI must say so.
  static const Map<String, Map<String, Freshness>> _freshness = {
    'p-oil': {'zepto': Freshness.est},
    'p-tea': {'instamart': Freshness.est},
  };

  static const List<Offer> _offers = [
    Offer(code: 'GROCERY10', platformId: 'blinkit', title: '10% off up to ₹100',
        terms: 'On orders above ₹299', kind: 'pct_upto',
        value: 10, maxDiscount: 100, minOrder: 299),
    Offer(code: 'ZEPTO50', platformId: 'zepto', title: 'Flat ₹50 off',
        terms: 'On orders above ₹299', kind: 'flat',
        value: 50, maxDiscount: 50, minOrder: 299),
    Offer(code: 'SWIGGYIT', platformId: 'instamart', title: 'Free delivery',
        terms: 'On orders above ₹199', kind: 'free_delivery',
        value: 0, maxDiscount: 30, minOrder: 199),
  ];

  // -- mutable demo state ----------------------------------------------------

  final Map<String, bool> _linked = {
    'blinkit': true,
    'zepto': false,
    'instamart': true,
  };
  final Map<String, String> _phones = {
    'blinkit': '•••• 4821',
    'instamart': '•••• 9034',
  };

  final List<PriceAlert> _alerts = [
    PriceAlert(
      id: 'a1', productId: 'p-atta', productName: 'Aashirvaad Shudh Chakki Atta',
      packSize: '5 kg', targetPrice: 270, currentBest: 279,
      currentBestPlatformId: 'zepto'),
    PriceAlert(
      id: 'a2', productId: 'p-oil', productName: 'Fortune Sunflower Oil',
      packSize: '1 L', targetPrice: 135, currentBest: 140,
      currentBestPlatformId: 'instamart'),
  ];

  Future<void> _latency([int ms = 550]) =>
      Future.delayed(Duration(milliseconds: ms));

  // -- interface ---------------------------------------------------------------

  @override
  Future<List<PlatformInfo>> platforms() async {
    await _latency(200);
    return _platforms;
  }

  @override
  Future<List<Product>> catalog() async {
    await _latency(200);
    return _products;
  }

  @override
  Future<List<ComparisonResult>> compare({
    required String query,
    required double lat,
    required double lng,
  }) async {
    await _latency();
    final q = query.trim().toLowerCase();
    final tokens = q.split(RegExp(r'\s+')).where((t) => t.length > 1);
    List<Product> hits = _products.where((p) {
      final hay = '${p.brand} ${p.name}'.toLowerCase();
      return tokens.any(hay.contains);
    }).toList();
    if (hits.isEmpty) hits = _products.take(3).toList();
    return hits.map(_compareProduct).toList();
  }

  ComparisonResult _compareProduct(Product p) {
    final prices = _platforms.map((pl) {
      final pr = _priceTable[p.id]![pl.id]!;
      return PlatformPrice(
        platformId: pl.id,
        price: pr[0],
        mrp: pr[1],
        freshness: _freshness[p.id]?[pl.id] ?? Freshness.live,
      );
    }).toList();
    final bills = <String, TrueTotalBill>{};
    for (final pl in _platforms) {
      final price = _priceTable[p.id]![pl.id]![0];
      bills[pl.id] =
          _bill(pl.id, [SplitItem(product: p, qty: 1, unitPrice: price)]);
    }
    final best =
        bills.entries.reduce((a, b) => a.value.total < b.value.total ? a : b).key;
    return ComparisonResult(
        product: p, prices: prices, bills: bills, bestPlatformId: best);
  }

  @override
  Future<SplitPlan> optimizeCart({
    required List<CartItem> items,
    required double lat,
    required double lng,
    int maxPlatforms = 3,
  }) async {
    await _latency(800);
    final plats = _platforms.take(maxPlatforms.clamp(1, 3).toInt()).toList();

    List<_Candidate> run({required bool enforceMinOrder}) {
      final out = <_Candidate>[];
      for (var mask = 1; mask < (1 << plats.length); mask++) {
        final subset = [
          for (var i = 0; i < plats.length; i++)
            if ((mask & (1 << i)) != 0) plats[i]
        ];
        // Assign every item to its cheapest platform in this subset.
        final byPlatform = <String, List<SplitItem>>{
          for (final pl in subset) pl.id: []
        };
        for (final ci in items) {
          PlatformInfo? cheapest;
          var bestPrice = double.infinity;
          for (final pl in subset) {
            final price = _priceTable[ci.product.id]![pl.id]![0];
            if (price < bestPrice) {
              bestPrice = price;
              cheapest = pl;
            }
          }
          byPlatform[cheapest!.id]!.add(SplitItem(
              product: ci.product, qty: ci.qty, unitPrice: bestPrice));
        }
        // Minimum-order feasibility (platforms with no items are fine).
        if (enforceMinOrder) {
          var ok = true;
          for (final e in byPlatform.entries) {
            if (e.value.isEmpty) continue;
            final sub = e.value.fold(0.0, (s, it) => s + it.lineTotal);
            if (sub < _fees[e.key]!.minOrder) {
              ok = false;
              break;
            }
          }
          if (!ok) continue;
        }
        final groups = [
          for (final e in byPlatform.entries)
            if (e.value.isNotEmpty)
              SplitGroup(
                platform: _platforms.firstWhere((p) => p.id == e.key),
                items: e.value,
                bill: _bill(e.key, e.value),
              )
        ];
        final total = groups.fold(0.0, (s, g) => s + g.bill.total);
        out.add(_Candidate(
          groups: groups,
          total: total,
          platformCount: groups.length,
          maxEta: groups.map((g) => g.etaMinutes).fold(0, (a, b) => a > b ? a : b),
        ));
      }
      out.sort((a, b) {
        final c = a.total.compareTo(b.total);
        if (c != 0) return c;
        final d = a.platformCount.compareTo(b.platformCount);
        if (d != 0) return d;
        return a.maxEta.compareTo(b.maxEta);
      });
      return out;
    }

    var feasible = run(enforceMinOrder: true);
    if (feasible.isEmpty) feasible = run(enforceMinOrder: false);
    final winner = feasible.first;

    // Cheapest single platform (the "consolidate" alternative).
    String consId = plats.first.id;
    var consTotal = double.infinity;
    for (final pl in plats) {
      final all = [
        for (final ci in items)
          SplitItem(
              product: ci.product,
              qty: ci.qty,
              unitPrice: _priceTable[ci.product.id]![pl.id]![0])
      ];
      final bill = _bill(pl.id, all);
      if (bill.total < consTotal) {
        consTotal = bill.total;
        consId = pl.id;
      }
    }
    final consItems = [
      for (final ci in items)
        SplitItem(
            product: ci.product,
            qty: ci.qty,
            unitPrice: _priceTable[ci.product.id]![consId]![0])
    ];
    final consBill = _bill(consId, consItems);
    final mostExpensive =
        feasible.map((f) => f.total).reduce((a, b) => a > b ? a : b);

    return SplitPlan(
      groups: winner.groups,
      consolidatedPlatformId: consId,
      consolidatedBill: consBill,
      savingsVsConsolidated: consBill.total - winner.total,
      savingsVsMostExpensive: mostExpensive - winner.total,
    );
  }

  /// Itemised true-total for one platform (the pricing engine, mock edition).
  TrueTotalBill _bill(String platformId, List<SplitItem> items) {
    final fee = _fees[platformId]!;
    final subtotal = items.fold(0.0, (s, i) => s + i.lineTotal);

    double delivery = subtotal >= fee.freeAbove ? 0.0 : fee.deliveryFee;
    final platformFee = fee.platformFee;
    final packaging = fee.packagingFee;

    // Best applicable coupon wins.
    double couponDiscount = 0;
    String? coupon;
    for (final o in _offers.where((o) => o.platformId == platformId)) {
      if (subtotal < o.minOrder) continue;
      final double d;
      switch (o.kind) {
        case 'flat':
          d = o.value;
        case 'pct_upto':
          d = (subtotal * o.value / 100).clamp(0.0, o.maxDiscount).toDouble();
        case 'free_delivery':
          d = delivery.clamp(0.0, o.maxDiscount).toDouble();
        default:
          d = 0;
      }
      if (d > couponDiscount) {
        couponDiscount = d;
        coupon = o.code;
      }
    }

    final gst = (delivery + platformFee + packaging) * 0.05;
    final total = subtotal + delivery + platformFee + packaging + gst - couponDiscount;

    return TrueTotalBill(
      platformId: platformId,
      lines: [
        BillLine(label: 'Items (${items.length})', amount: subtotal),
        BillLine(label: 'Delivery fee', amount: delivery),
        BillLine(label: 'Platform fee', amount: platformFee),
        BillLine(label: 'Packaging', amount: packaging),
        BillLine(label: 'GST on charges', amount: gst),
        if (couponDiscount > 0)
          BillLine(label: 'Coupon $coupon', amount: -couponDiscount),
        BillLine(label: 'You pay', amount: total, isTotal: true),
      ],
      total: total,
      etaMinutes: fee.etaMinutes,
      freshness: Freshness.live,
      appliedCoupon: coupon,
    );
  }

  @override
  Future<List<Offer>> offers({String? platformId}) async {
    await _latency(250);
    return platformId == null
        ? _offers
        : _offers.where((o) => o.platformId == platformId).toList();
  }

  @override
  Future<List<PriceAlert>> alerts() async {
    await _latency(250);
    return List.unmodifiable(_alerts);
  }

  @override
  Future<PriceAlert> createAlert({
    required String productId,
    required double targetPrice,
  }) async {
    await _latency();
    final p = _products.firstWhere((e) => e.id == productId);
    final comp = _compareProduct(p);
    final best = comp.prices.reduce((a, b) => a.price < b.price ? a : b);
    final alert = PriceAlert(
      id: 'a${DateTime.now().millisecondsSinceEpoch}',
      productId: p.id,
      productName: '${p.brand} ${p.name}',
      packSize: p.packSize,
      targetPrice: targetPrice,
      currentBest: best.price,
      currentBestPlatformId: best.platformId,
    );
    _alerts.add(alert);
    return alert;
  }

  @override
  Future<void> deleteAlert(String id) async {
    await _latency(200);
    _alerts.removeWhere((a) => a.id == id);
  }

  @override
  Future<SavingsSummary> savings() async {
    await _latency(300);
    final now = DateTime.now();
    final entries = [
      SavingsEntry(
          id: 's1', date: now.subtract(const Duration(hours: 5)),
          title: 'Weekly groceries · split cart',
          detail: 'Zepto + Blinkit · 6 items', saved: 186),
      SavingsEntry(
          id: 's2', date: now.subtract(const Duration(days: 1)),
          title: 'Aashirvaad Atta 5 kg',
          detail: 'Zepto beat Blinkit by ₹6 + coupon', saved: 56),
      SavingsEntry(
          id: 's3', date: now.subtract(const Duration(days: 3)),
          title: 'Monthly stock-up · split cart',
          detail: '3 platforms · 11 items', saved: 342),
      SavingsEntry(
          id: 's4', date: now.subtract(const Duration(days: 6)),
          title: 'Fortune Oil 1 L',
          detail: 'Instamart had the lowest true total', saved: 24),
      SavingsEntry(
          id: 's5', date: now.subtract(const Duration(days: 9)),
          title: 'Maggi family pack ×2',
          detail: 'Per-unit alert caught a markup', saved: 31),
    ];
    return SavingsSummary(
      lifetime: 4280,
      thisMonth: 862,
      monthlyGoal: 1500,
      streakDays: 14,
      last30Days: const [
        12, 0, 45, 28, 0, 63, 18, 0, 31, 186,
        22, 0, 0, 56, 41, 0, 19, 0, 342, 27,
        0, 33, 0, 24, 58, 0, 0, 31, 44, 62,
      ],
      entries: entries,
    );
  }

  @override
  Future<List<LinkedAccount>> linkedAccounts() async {
    await _latency(250);
    return _platforms
        .map((p) => LinkedAccount(
              platformId: p.id,
              linked: _linked[p.id] ?? false,
              phoneMasked: _phones[p.id],
            ))
        .toList();
  }

  @override
  Future<String> startLink({
    required String platformId,
    required String phone,
  }) async {
    await _latency(700);
    return 'link_ref_demo_$platformId';
  }

  @override
  Future<LinkedAccount> verifyOtp({
    required String linkRef,
    required String otp,
  }) async {
    await _latency(900);
    if (otp.length != 4) {
      throw Exception('Enter the 4-digit OTP');
    }
    final platformId = linkRef.split('_').last;
    _linked[platformId] = true;
    _phones[platformId] = '•••• 1234';
    return LinkedAccount(
        platformId: platformId, linked: true, phoneMasked: '•••• 1234');
  }

  @override
  Future<void> unlink({required String platformId}) async {
    await _latency(400);
    _linked[platformId] = false;
    _phones.remove(platformId);
  }
}

class _FeeTable {
  final double deliveryFee;
  final double freeAbove;
  final double platformFee;
  final double packagingFee;
  final double minOrder;
  final int etaMinutes;

  const _FeeTable({
    required this.deliveryFee,
    required this.freeAbove,
    required this.platformFee,
    required this.packagingFee,
    required this.minOrder,
    required this.etaMinutes,
  });
}

class _Candidate {
  final List<SplitGroup> groups;
  final double total;
  final int platformCount;
  final int maxEta;

  const _Candidate({
    required this.groups,
    required this.total,
    required this.platformCount,
    required this.maxEta,
  });
}
