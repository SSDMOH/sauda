/// Core domain models for Sauda.
///
/// These mirror the backend REST contract (ARCHITECTURE.md §8) so the mock
/// service and the future HTTP client speak exactly the same language.
/// Every model carries a `fromJson` factory for the real API client.

// ---------------------------------------------------------------------------
// Freshness
// ---------------------------------------------------------------------------

/// How trustworthy a price is. Shown in the UI as a LIVE / EST badge.
enum Freshness { live, est }

Freshness freshnessFromJson(String value) =>
    value == 'live' ? Freshness.live : Freshness.est;

String freshnessToJson(Freshness freshness) =>
    freshness == Freshness.live ? 'live' : 'est';

// ---------------------------------------------------------------------------
// Platform
// ---------------------------------------------------------------------------

/// A platform Sauda compares. v1: quick-commerce grocery.
class PlatformInfo {
  final String id; // "blinkit" | "zepto" | "instamart"
  final String name; // "Blinkit"
  final int brandColor; // ARGB, e.g. 0xFFF8CB46
  final String tagline;

  const PlatformInfo({
    required this.id,
    required this.name,
    required this.brandColor,
    required this.tagline,
  });

  factory PlatformInfo.fromJson(Map<String, dynamic> json) => PlatformInfo(
        id: json['id'] as String,
        name: json['name'] as String,
        brandColor: json['brand_color'] as int,
        tagline: json['tagline'] as String? ?? '',
      );
}

// ---------------------------------------------------------------------------
// Product
// ---------------------------------------------------------------------------

class Product {
  final String id;
  final String gtin;
  final String brand;
  final String name;
  final String packSize; // display string, e.g. "5 kg"
  final double packQty; // numeric quantity measured in [unit]
  final String unit; // "kg" | "g" | "L" | "ml" | "pcs"

  const Product({
    required this.id,
    required this.gtin,
    required this.brand,
    required this.name,
    required this.packSize,
    required this.packQty,
    required this.unit,
  });

  String get label => '$brand $name · $packSize';

  /// Price normalised to the base unit so pack sizes compare fairly.
  double perUnit(double price) => price / _baseQty();

  String get perUnitLabel {
    switch (unit) {
      case 'kg':
        return '₹/kg';
      case 'g':
        return '₹/100 g';
      case 'L':
        return '₹/L';
      case 'ml':
        return '₹/100 ml';
      default:
        return '₹/pc';
    }
  }

  double _baseQty() {
    switch (unit) {
      case 'kg':
        return packQty;
      case 'g':
        return packQty / 100;
      case 'L':
        return packQty;
      case 'ml':
        return packQty / 100;
      default:
        return packQty;
    }
  }

  factory Product.fromJson(Map<String, dynamic> json) => Product(
        id: json['id'] as String,
        gtin: json['gtin'] as String? ?? '',
        brand: json['brand'] as String? ?? '',
        name: json['name'] as String? ?? '',
        packSize: json['pack_size'] as String? ?? '',
        packQty: (json['pack_qty'] as num? ?? 1).toDouble(),
        unit: json['unit'] as String? ?? 'pcs',
      );
}

// ---------------------------------------------------------------------------
// Pricing
// ---------------------------------------------------------------------------

class PlatformPrice {
  final String platformId;
  final double price;
  final double mrp;
  final bool inStock;
  final Freshness freshness;

  const PlatformPrice({
    required this.platformId,
    required this.price,
    required this.mrp,
    this.inStock = true,
    this.freshness = Freshness.live,
  });

  double get discountPct =>
      mrp <= 0 ? 0 : ((mrp - price) / mrp * 100).clamp(0, 100).toDouble();

  factory PlatformPrice.fromJson(Map<String, dynamic> json) => PlatformPrice(
        platformId: json['platform_id'] as String,
        price: (json['price'] as num).toDouble(),
        mrp: (json['mrp'] as num? ?? 0).toDouble(),
        inStock: json['in_stock'] as bool? ?? true,
        freshness: freshnessFromJson(json['freshness'] as String? ?? 'live'),
      );
}

/// One row of an itemised bill. Discounts carry negative amounts.
class BillLine {
  final String label;
  final double amount;
  final bool isTotal;

  const BillLine({
    required this.label,
    required this.amount,
    this.isTotal = false,
  });

  factory BillLine.fromJson(Map<String, dynamic> json) => BillLine(
        label: json['label'] as String,
        amount: (json['amount'] as num).toDouble(),
        isTotal: json['is_total'] as bool? ?? false,
      );
}

/// The verifiable true total for one platform:
/// items → delivery → platform fee → packaging → GST → − coupon = you pay.
class TrueTotalBill {
  final String platformId;
  final List<BillLine> lines;
  final double total;
  final int etaMinutes;
  final Freshness freshness;
  final String? appliedCoupon;

  const TrueTotalBill({
    required this.platformId,
    required this.lines,
    required this.total,
    required this.etaMinutes,
    required this.freshness,
    this.appliedCoupon,
  });

  factory TrueTotalBill.fromJson(Map<String, dynamic> json) => TrueTotalBill(
        platformId: json['platform_id'] as String,
        lines: (json['lines'] as List)
            .map((e) => BillLine.fromJson(e as Map<String, dynamic>))
            .toList(),
        total: (json['total'] as num).toDouble(),
        etaMinutes: json['eta_minutes'] as int? ?? 0,
        freshness: freshnessFromJson(json['freshness'] as String? ?? 'est'),
        appliedCoupon: json['applied_coupon'] as String?,
      );
}

class ComparisonResult {
  final Product product;
  final List<PlatformPrice> prices;
  final Map<String, TrueTotalBill> bills; // platformId -> itemised bill
  final String bestPlatformId; // lowest true total

  const ComparisonResult({
    required this.product,
    required this.prices,
    required this.bills,
    required this.bestPlatformId,
  });

  TrueTotalBill get bestBill => bills[bestPlatformId]!;

  /// Relative spread of per-unit prices across platforms.
  /// > 0.10 triggers the "hidden markup" callout in the UI.
  double get perUnitSpread {
    final pus = prices
        .where((p) => p.inStock)
        .map((p) => product.perUnit(p.price))
        .toList();
    if (pus.length < 2) return 0;
    pus.sort();
    return (pus.last - pus.first) / pus.first;
  }

  factory ComparisonResult.fromJson(Map<String, dynamic> json) =>
      ComparisonResult(
        product: Product.fromJson(json['product'] as Map<String, dynamic>),
        prices: (json['prices'] as List)
            .map((e) => PlatformPrice.fromJson(e as Map<String, dynamic>))
            .toList(),
        bills: (json['bills'] as Map<String, dynamic>).map(
          (k, v) => MapEntry(k, TrueTotalBill.fromJson(v as Map<String, dynamic>)),
        ),
        bestPlatformId: json['best_platform_id'] as String,
      );
}

// ---------------------------------------------------------------------------
// Cart + split plan
// ---------------------------------------------------------------------------

/// An item in the user's working cart (UI-side).
class CartItem {
  final Product product;
  int qty;

  CartItem({required this.product, this.qty = 1});
}

/// An item assigned to one platform inside a split plan.
class SplitItem {
  final Product product;
  final int qty;
  final double unitPrice;

  const SplitItem({
    required this.product,
    required this.qty,
    required this.unitPrice,
  });

  double get lineTotal => unitPrice * qty;

  factory SplitItem.fromJson(Map<String, dynamic> json) => SplitItem(
        product: Product.fromJson(json['product'] as Map<String, dynamic>),
        qty: json['qty'] as int,
        unitPrice: (json['unit_price'] as num).toDouble(),
      );
}

/// One platform's share of the split plan.
class SplitGroup {
  final PlatformInfo platform;
  final List<SplitItem> items;
  final TrueTotalBill bill;

  const SplitGroup({
    required this.platform,
    required this.items,
    required this.bill,
  });

  double get subtotal => items.fold(0.0, (s, i) => s + i.lineTotal);
  int get etaMinutes => bill.etaMinutes;

  factory SplitGroup.fromJson(Map<String, dynamic> json) => SplitGroup(
        platform: PlatformInfo.fromJson(json['platform'] as Map<String, dynamic>),
        items: (json['items'] as List)
            .map((e) => SplitItem.fromJson(e as Map<String, dynamic>))
            .toList(),
        bill: TrueTotalBill.fromJson(json['bill'] as Map<String, dynamic>),
      );
}

/// The optimiser's answer: which items to order from which platform.
class SplitPlan {
  final List<SplitGroup> groups;
  final String consolidatedPlatformId; // cheapest single platform
  final TrueTotalBill consolidatedBill;
  final double savingsVsConsolidated;
  final double savingsVsMostExpensive;

  const SplitPlan({
    required this.groups,
    required this.consolidatedPlatformId,
    required this.consolidatedBill,
    required this.savingsVsConsolidated,
    required this.savingsVsMostExpensive,
  });

  double get total => groups.fold(0.0, (s, g) => s + g.bill.total);

  int get maxEta =>
      groups.map((g) => g.etaMinutes).fold(0, (a, b) => a > b ? a : b);

  factory SplitPlan.fromJson(Map<String, dynamic> json) => SplitPlan(
        groups: (json['groups'] as List)
            .map((e) => SplitGroup.fromJson(e as Map<String, dynamic>))
            .toList(),
        consolidatedPlatformId: json['consolidated_platform_id'] as String,
        consolidatedBill:
            TrueTotalBill.fromJson(json['consolidated_bill'] as Map<String, dynamic>),
        savingsVsConsolidated:
            (json['savings_vs_consolidated'] as num).toDouble(),
        savingsVsMostExpensive:
            (json['savings_vs_most_expensive'] as num).toDouble(),
      );
}

// ---------------------------------------------------------------------------
// Offers
// ---------------------------------------------------------------------------

class Offer {
  final String code;
  final String platformId;
  final String title;
  final String terms;
  final String kind; // "flat" | "pct_upto" | "free_delivery"
  final double value;
  final double maxDiscount;
  final double minOrder;

  const Offer({
    required this.code,
    required this.platformId,
    required this.title,
    required this.terms,
    required this.kind,
    required this.value,
    required this.maxDiscount,
    required this.minOrder,
  });

  factory Offer.fromJson(Map<String, dynamic> json) => Offer(
        code: json['code'] as String,
        platformId: json['platform_id'] as String,
        title: json['title'] as String? ?? '',
        terms: json['terms'] as String? ?? '',
        kind: json['kind'] as String? ?? 'flat',
        value: (json['value'] as num? ?? 0).toDouble(),
        maxDiscount: (json['max_discount'] as num? ?? 0).toDouble(),
        minOrder: (json['min_order'] as num? ?? 0).toDouble(),
      );
}

// ---------------------------------------------------------------------------
// Alerts
// ---------------------------------------------------------------------------

class PriceAlert {
  final String id;
  final String productId;
  final String productName;
  final String packSize;
  final double targetPrice;
  final double currentBest;
  final String currentBestPlatformId;
  final bool active;

  const PriceAlert({
    required this.id,
    required this.productId,
    required this.productName,
    required this.packSize,
    required this.targetPrice,
    required this.currentBest,
    required this.currentBestPlatformId,
    this.active = true,
  });

  double get progress =>
      currentBest <= 0 ? 0 : (targetPrice / currentBest).clamp(0.0, 1.0).toDouble();

  factory PriceAlert.fromJson(Map<String, dynamic> json) => PriceAlert(
        id: json['id'] as String,
        productId: json['product_id'] as String? ?? '',
        productName: json['product_name'] as String? ?? '',
        packSize: json['pack_size'] as String? ?? '',
        targetPrice: (json['target_price'] as num).toDouble(),
        currentBest: (json['current_best'] as num? ?? 0).toDouble(),
        currentBestPlatformId: json['current_best_platform_id'] as String? ?? '',
        active: json['active'] as bool? ?? true,
      );
}

// ---------------------------------------------------------------------------
// Savings
// ---------------------------------------------------------------------------

class SavingsEntry {
  final String id;
  final DateTime date;
  final String title;
  final String detail;
  final double saved;

  const SavingsEntry({
    required this.id,
    required this.date,
    required this.title,
    required this.detail,
    required this.saved,
  });

  factory SavingsEntry.fromJson(Map<String, dynamic> json) => SavingsEntry(
        id: json['id'] as String,
        date: DateTime.parse(json['date'] as String),
        title: json['title'] as String? ?? '',
        detail: json['detail'] as String? ?? '',
        saved: (json['saved'] as num).toDouble(),
      );
}

class SavingsSummary {
  final double lifetime;
  final double thisMonth;
  final double monthlyGoal;
  final int streakDays;
  final List<double> last30Days;
  final List<SavingsEntry> entries;

  const SavingsSummary({
    required this.lifetime,
    required this.thisMonth,
    required this.monthlyGoal,
    required this.streakDays,
    required this.last30Days,
    required this.entries,
  });

  factory SavingsSummary.fromJson(Map<String, dynamic> json) => SavingsSummary(
        lifetime: (json['lifetime'] as num).toDouble(),
        thisMonth: (json['this_month'] as num).toDouble(),
        monthlyGoal: (json['monthly_goal'] as num? ?? 1500).toDouble(),
        streakDays: json['streak_days'] as int? ?? 0,
        last30Days: (json['last_30_days'] as List)
            .map((e) => (e as num).toDouble())
            .toList(),
        entries: (json['entries'] as List)
            .map((e) => SavingsEntry.fromJson(e as Map<String, dynamic>))
            .toList(),
      );
}

// ---------------------------------------------------------------------------
// Accounts
// ---------------------------------------------------------------------------

class LinkedAccount {
  final String platformId;
  final bool linked;
  final String? phoneMasked; // "•••• 4821"

  const LinkedAccount({
    required this.platformId,
    required this.linked,
    this.phoneMasked,
  });

  factory LinkedAccount.fromJson(Map<String, dynamic> json) => LinkedAccount(
        platformId: json['platform_id'] as String,
        linked: json['linked'] as bool? ?? false,
        phoneMasked: json['phone_masked'] as String?,
      );
}
