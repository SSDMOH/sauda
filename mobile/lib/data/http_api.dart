import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import '../core/api_client.dart';
import '../core/models.dart';

/// HTTP implementation of [SaudaApi] against the FastAPI backend
/// (`~/workspace/sauda/backend`, served at [baseUrl]).
///
/// The backend speaks a platform-major dialect (per-platform baskets,
/// backend bill dicts); this client translates everything into the
/// product-major app models so no call site changes between mock and live.
///
/// Honest v1 limitations (all documented, none silent):
/// - `alerts()` has no server-side list endpoint yet, so alerts live in
///   memory on the client: they work end-to-end but do not survive an app
///   restart. The backend persists them once `GET /v1/alerts` ships.
/// - `linkedAccounts()` merges server link state with a client-side record
///   for the same reason.
/// - `catalog()` seeds itself from a few compare queries on first use.
class HttpSaudaApi implements SaudaApi {
  final String baseUrl;
  final double lat;
  final double lng;
  final Duration timeout;

  HttpSaudaApi({
    required this.baseUrl,
    this.lat = 28.6139,
    this.lng = 77.2090,
    this.timeout = const Duration(seconds: 12),
  });

  // -- low-level ---------------------------------------------------------

  Uri _uri(String path) => Uri.parse('$baseUrl$path');

  Future<String> _get(String path) async {
    final res = await http.get(_uri(path)).timeout(timeout);
    if (res.statusCode != 200) {
      throw Exception('GET $path failed (${res.statusCode})');
    }
    return res.body;
  }

  Future<String> _post(String path, Map<String, dynamic> body) async {
    final res = await http
        .post(_uri(path),
            headers: {'Content-Type': 'application/json'},
            body: jsonEncode(body))
        .timeout(timeout);
    if (res.statusCode < 200 || res.statusCode >= 300) {
      throw Exception(
          'POST $path failed (${res.statusCode}): ${_short(res.body)}');
    }
    return res.body;
  }

  Future<void> _delete(String path) async {
    final res = await http.delete(_uri(path)).timeout(timeout);
    if (res.statusCode != 200) {
      throw Exception('DELETE $path failed (${res.statusCode})');
    }
  }

  String _short(String body) =>
      body.length > 160 ? '${body.substring(0, 160)}…' : body;

  /// Connectivity probe used at startup to decide mock vs live.
  Future<void> ping() async {
    final res = await http.get(_uri('/v1/health')).timeout(
          const Duration(seconds: 3),
        );
    if (res.statusCode != 200) throw Exception('unhealthy');
  }

  // -- platforms ----------------------------------------------------------

  static const Map<String, int> _brandColors = {
    'blinkit': 0xFFF8CB46,
    'zepto': 0xFFFF2459,
    'instamart': 0xFFFC8019,
  };

  Map<String, PlatformInfo>? _platformsCache;

  Future<Map<String, PlatformInfo>> _platformMap() async {
    if (_platformsCache != null) return _platformsCache!;
    final body = jsonDecode(await _get('/v1/health')) as Map<String, dynamic>;
    final adapters = (body['adapters'] as List).cast<Map<String, dynamic>>();
    _platformsCache = {
      for (final a in adapters)
        (a['platform_id'] as String): PlatformInfo(
          id: a['platform_id'] as String,
          name: a['display_name'] as String? ?? a['platform_id'] as String,
          brandColor: _brandColors[a['platform_id']] ?? 0xFF888888,
          tagline: (a['status'] as String? ?? '') == 'ok'
              ? 'Live'
              : (a['message'] as String? ?? ''),
        ),
    };
    return _platformsCache!;
  }

  @override
  Future<List<PlatformInfo>> platforms() async =>
      (await _platformMap()).values.toList();

  // -- bill translation ----------------------------------------------------

  /// Turns a backend bill dict
  /// (`items_subtotal/delivery_fee/platform_fee/packaging_fee/gst/discount/`
  /// `coupon_code/total/lines[]`) into the app's itemised [TrueTotalBill].
  TrueTotalBill _billFromBackend(
    Map<String, dynamic> json, {
    required String platformId,
    int etaMinutes = 0,
    Freshness freshness = Freshness.live,
  }) {
    final lines = <BillLine>[];
    for (final e in (json['lines'] as List? ?? const [])) {
      final m = e as Map<String, dynamic>;
      final qty = (m['qty'] as num? ?? 1).toInt();
      lines.add(BillLine(
        label: '${m['name']} ×$qty',
        amount: (m['line_total'] as num).toDouble(),
      ));
    }
    void fee(String label, dynamic value) {
      final amount = (value as num? ?? 0).toDouble();
      if (amount != 0) lines.add(BillLine(label: label, amount: amount));
    }

    fee('Delivery fee', json['delivery_fee']);
    fee('Platform fee', json['platform_fee']);
    fee('Packaging fee', json['packaging_fee']);
    fee('GST', json['gst']);
    final discount = (json['discount'] as num? ?? 0).toDouble();
    final coupon = json['coupon_code'] as String?;
    if (discount > 0) {
      lines.add(BillLine(
        label: coupon == null ? 'Coupon discount' : 'Coupon $coupon',
        amount: -discount,
      ));
    }
    final total = (json['total'] as num).toDouble();
    lines.add(BillLine(label: 'You pay', amount: total, isTotal: true));
    return TrueTotalBill(
      platformId: platformId,
      lines: lines,
      total: total,
      etaMinutes: etaMinutes,
      freshness: freshness,
      appliedCoupon: coupon,
    );
  }

  Product _productFromItem(Map<String, dynamic> it) {
    final gtin = it['gtin'] as String;
    return Product(
      id: gtin,
      gtin: gtin,
      brand: it['brand'] as String? ?? '',
      name: it['name'] as String? ?? '',
      packSize: it['pack_label'] as String? ?? '',
      packQty: (it['pack_size'] as num? ?? 1).toDouble(),
      unit: it['unit'] as String? ?? 'pcs',
    );
  }

  final Map<String, Product> _productCache = {};

  // -- compare ---------------------------------------------------------------

  @override
  Future<List<ComparisonResult>> compare({
    required String query,
    required double lat,
    required double lng,
  }) async {
    final body = jsonDecode(
      await _post('/v1/compare', {'query': query, 'lat': lat, 'lng': lng}),
    ) as Map<String, dynamic>;
    final results =
        (body['results'] as List).cast<Map<String, dynamic>>();
    final bestPick = body['best_pick'] as String?;

    final products = <String, Product>{};
    final prices = <String, List<PlatformPrice>>{};
    final bills = <String, Map<String, TrueTotalBill>>{};

    for (final r in results) {
      final pid = r['platform_id'] as String;
      final freshness =
          freshnessFromJson(r['freshness'] as String? ?? 'live');
      final bill = _billFromBackend(
        r['bill'] as Map<String, dynamic>,
        platformId: pid,
        freshness: freshness,
      );
      for (final e in (r['items'] as List).cast<Map<String, dynamic>>()) {
        final gtin = e['gtin'] as String;
        products.putIfAbsent(gtin, () {
          final p = _productFromItem(e);
          _productCache[gtin] = p;
          return p;
        });
        prices.putIfAbsent(gtin, () => []).add(PlatformPrice(
              platformId: pid,
              price: (e['price'] as num).toDouble(),
              mrp: (e['mrp'] as num? ?? 0).toDouble(),
              inStock: e['in_stock'] as bool? ?? true,
              freshness: freshness,
            ));
        bills.putIfAbsent(gtin, () => {})[pid] = bill;
      }
    }

    final out = <ComparisonResult>[];
    for (final gtin in products.keys) {
      final pBills = bills[gtin]!;
      final sortedPids = pBills.keys.toList()
        ..sort((a, b) => pBills[a]!.total.compareTo(pBills[b]!.total));
      final best = (bestPick != null && pBills.containsKey(bestPick))
          ? bestPick
          : sortedPids.first;
      out.add(ComparisonResult(
        product: products[gtin]!,
        prices: prices[gtin]!,
        bills: pBills,
        bestPlatformId: best,
      ));
    }
    out.sort((a, b) => a.bestBill.total.compareTo(b.bestBill.total));
    return out;
  }

  // -- catalog (self-seeding) ---------------------------------------------------

  @override
  Future<List<Product>> catalog() async {
    if (_productCache.isEmpty) {
      for (final q in const ['atta', 'milk', 'rice']) {
        try {
          await compare(query: q, lat: lat, lng: lng);
        } catch (_) {
          // keep going — whatever we gathered is what we've got
        }
      }
    }
    return _productCache.values.toList();
  }

  // -- split cart ---------------------------------------------------------------

  @override
  Future<SplitPlan> optimizeCart({
    required List<CartItem> items,
    required double lat,
    required double lng,
    int maxPlatforms = 3,
  }) async {
    final productByGtin = {for (final c in items) c.product.gtin: c.product};
    final body = jsonDecode(
      await _post('/v1/optimize-cart', {
        'items': [
          for (final c in items) {'gtin': c.product.gtin, 'qty': c.qty}
        ],
        'lat': lat,
        'lng': lng,
        'max_platforms': maxPlatforms,
      }),
    ) as Map<String, dynamic>;
    if (body['feasible'] != true) {
      throw Exception(body['message'] as String? ?? 'No feasible split.');
    }
    final platforms = await _platformMap();
    PlatformInfo platformOf(String pid) => platforms[pid] ??
        PlatformInfo(id: pid, name: pid, brandColor: 0xFF888888, tagline: '');

    final groups = <SplitGroup>[];
    for (final g in (body['groups'] as List).cast<Map<String, dynamic>>()) {
      final pid = g['platform_id'] as String;
      final splitItems = <SplitItem>[];
      for (final e in (g['lines'] as List).cast<Map<String, dynamic>>()) {
        final gtin = e['gtin'] as String;
        splitItems.add(SplitItem(
          product: productByGtin[gtin] ??
              Product(
                id: gtin,
                gtin: gtin,
                brand: '',
                name: e['name'] as String? ?? gtin,
                packSize: '',
                packQty: 1,
                unit: 'pcs',
              ),
          qty: (e['qty'] as num? ?? 1).toInt(),
          unitPrice: (e['unit_price'] as num).toDouble(),
        ));
      }
      groups.add(SplitGroup(
        platform: platformOf(pid),
        items: splitItems,
        bill: _billFromBackend(
          g,
          platformId: pid,
          etaMinutes: (g['eta_minutes'] as num? ?? 0).toInt(),
        ),
      ));
    }

    final bestSingle = body['best_single_platform'] as Map<String, dynamic>?;
    if (bestSingle == null) throw Exception('No single-platform fallback.');
    final cPid = bestSingle['platform_id'] as String;
    return SplitPlan(
      groups: groups,
      consolidatedPlatformId: cPid,
      consolidatedBill: _billFromBackend(
        bestSingle['bill'] as Map<String, dynamic>,
        platformId: cPid,
        etaMinutes:
            ((bestSingle['bill'] as Map<String, dynamic>)['eta_minutes']
                        as num? ??
                    0)
                .toInt(),
      ),
      savingsVsConsolidated:
          (body['savings_vs_single_platform'] as num? ?? 0).toDouble(),
      savingsVsMostExpensive:
          (body['savings_vs_most_expensive'] as num? ?? 0).toDouble(),
    );
  }

  // -- offers ---------------------------------------------------------------------

  @override
  Future<List<Offer>> offers({String? platformId}) async {
    final body = jsonDecode(await _get(
      platformId == null ? '/v1/offers' : '/v1/offers?platform=$platformId',
    )) as Map<String, dynamic>;
    return (body['offers'] as List).cast<Map<String, dynamic>>().map((o) {
      final platforms = (o['platforms'] as List? ?? const []).cast<String>();
      final minOrder = (o['min_order'] as num? ?? 0).toDouble();
      return Offer(
        code: o['code'] as String,
        platformId: platforms.isNotEmpty ? platforms.first : 'all',
        title: o['code'] as String,
        terms: [
          if (minOrder > 0) 'min order ₹${minOrder.toStringAsFixed(0)}',
          if (o['first_order_only'] == true) 'first order only',
        ].join(' · '),
        kind: o['type'] as String? ?? 'flat',
        value: (o['value'] as num? ?? 0).toDouble(),
        maxDiscount: (o['max_discount'] as num? ?? 0).toDouble(),
        minOrder: minOrder,
      );
    }).toList();
  }

  // -- alerts (client-side list; server has no list endpoint in v1) -----------------

  final List<PriceAlert> _alerts = [];

  @override
  Future<List<PriceAlert>> alerts() async => List.unmodifiable(_alerts);

  @override
  Future<PriceAlert> createAlert({
    required String productId,
    required double targetPrice,
  }) async {
    final body = jsonDecode(await _post('/v1/alerts', {
      'gtin': productId,
      'target_price': targetPrice,
    })) as Map<String, dynamic>;

    double best = 0;
    String bestPid = '';
    try {
      final cmp = await compare(
        query: (body['product'] as String? ?? productId),
        lat: lat,
        lng: lng,
      );
      if (cmp.isNotEmpty) {
        best = cmp.first.bestBill.total;
        bestPid = cmp.first.bestPlatformId;
      }
    } catch (_) {
      // leave best at 0 — the UI shows "checking live price"
    }

    final alert = PriceAlert(
      id: body['alert_id'] as String? ?? 'al_${_alerts.length}',
      productId: productId,
      productName: body['product'] as String? ?? productId,
      packSize: '',
      targetPrice: targetPrice,
      currentBest: best,
      currentBestPlatformId: bestPid,
    );
    _alerts.add(alert);
    return alert;
  }

  @override
  Future<void> deleteAlert(String id) async {
    try {
      await _delete('/v1/alerts/$id');
    } catch (_) {
      // server may have restarted and forgotten it — drop locally anyway
    }
    _alerts.removeWhere((a) => a.id == id);
  }

  // -- savings ----------------------------------------------------------------------

  @override
  Future<SavingsSummary> savings() async {
    final body =
        jsonDecode(await _get('/v1/savings')) as Map<String, dynamic>;
    final weekSaved = (body['week_saved'] as num? ?? 0).toDouble();
    final entries = <SavingsEntry>[];
    for (final e in (body['recent'] as List? ?? const [])
        .cast<Map<String, dynamic>>()) {
      final platforms = (e['platforms'] as List? ?? const []).join(', ');
      entries.add(SavingsEntry(
        id: e['date'] as String? ?? '',
        date: DateTime.tryParse(e['date'] as String? ?? '') ?? DateTime.now(),
        title: '${e['items'] ?? 0} items · $platforms',
        detail: 'Paid ₹${(e['paid'] as num? ?? 0).toStringAsFixed(0)}',
        saved: (e['saved'] as num? ?? 0).toDouble(),
      ));
    }
    // v1 backend tracks a *weekly* ledger; map it onto the monthly summary.
    return SavingsSummary(
      lifetime: (body['lifetime_savings'] as num? ?? 0).toDouble(),
      thisMonth: weekSaved,
      monthlyGoal: (body['week_goal'] as num? ?? 0).toDouble(),
      streakDays: (body['current_streak_days'] as num? ?? 0).toInt(),
      last30Days: [weekSaved],
      entries: entries,
    );
  }

  // -- accounts (client-side link record; server has no list endpoint in v1) ----------

  final Map<String, String> _linkedPhones = {}; // platformId -> phone

  String _mask(String phone) => phone.length <= 4
      ? '•••• $phone'
      : '•••• ${phone.substring(phone.length - 4)}';

  @override
  Future<List<LinkedAccount>> linkedAccounts() async {
    final platforms = await _platformMap();
    return platforms.values
        .map((p) => LinkedAccount(
              platformId: p.id,
              linked: _linkedPhones.containsKey(p.id),
              phoneMasked: _linkedPhones.containsKey(p.id)
                  ? _mask(_linkedPhones[p.id]!)
                  : null,
            ))
        .toList();
  }

  @override
  Future<String> startLink({
    required String platformId,
    required String phone,
  }) async {
    final body = jsonDecode(await _post('/v1/accounts/link', {
      'platform_id': platformId,
      'phone': phone,
    })) as Map<String, dynamic>;
    _pendingPhones[body['link_ref'] as String] = phone;
    return body['link_ref'] as String;
  }

  final Map<String, String> _pendingPhones = {}; // linkRef -> phone

  @override
  Future<LinkedAccount> verifyOtp({
    required String linkRef,
    required String otp,
  }) async {
    final body = jsonDecode(await _post('/v1/accounts/verify', {
      'link_ref': linkRef,
      'otp': otp,
    })) as Map<String, dynamic>;
    final pid = body['platform_id'] as String;
    final phone =
        body['phone'] as String? ?? _pendingPhones.remove(linkRef) ?? '';
    _linkedPhones[pid] = phone;
    return LinkedAccount(
        platformId: pid, linked: true, phoneMasked: _mask(phone));
  }

  @override
  Future<void> unlink({required String platformId}) async {
    try {
      await _delete('/v1/accounts/$platformId');
    } catch (_) {
      // already gone server-side — drop locally anyway
    }
    _linkedPhones.remove(platformId);
  }
}
