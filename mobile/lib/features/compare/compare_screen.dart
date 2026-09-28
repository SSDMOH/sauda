import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/app_state.dart';
import '../../core/deeplinks.dart';
import '../../core/format.dart';
import '../../core/models.dart';
import '../../core/theme.dart';
import '../../core/widgets.dart';

/// Compare results: platform rows with per-unit pricing, BEST badge,
/// LIVE/EST badges, expandable itemised true-total bills.
class CompareScreen extends StatefulWidget {
  final String initialQuery;

  const CompareScreen({super.key, this.initialQuery = ''});

  @override
  State<CompareScreen> createState() => _CompareScreenState();
}

class _CompareScreenState extends State<CompareScreen> {
  late final TextEditingController _controller;
  Future<List<ComparisonResult>>? _future;
  String? _handledQuery;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.initialQuery);
    if (widget.initialQuery.isNotEmpty) _search();
  }

  /// Picks up queries sent from other tabs (Home search -> Compare tab).
  /// The query is consumed once so tab switches don't re-trigger it.
  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final query = context.watch<AppState>().pendingQuery;
    if (query != null && query != _handledQuery) {
      _handledQuery = query;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (!mounted) return;
        context.read<AppState>().consumePendingQuery();
        _controller.text = query;
        _search();
      });
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _search() {
    final q = _controller.text.trim();
    if (q.isEmpty) return;
    FocusScope.of(context).unfocus();
    setState(() {
      _future = context.read<AppState>().api.compare(
            query: q,
            lat: 28.6139,
            lng: 77.2090,
          );
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: TextField(
          controller: _controller,
          autofocus: widget.initialQuery.isEmpty,
          textInputAction: TextInputAction.search,
          onSubmitted: (_) => _search(),
          decoration: const InputDecoration(
            hintText: 'Search atta, milk, Maggi…',
            prefixIcon: Icon(Icons.search_rounded),
            border: InputBorder.none,
            enabledBorder: InputBorder.none,
            focusedBorder: InputBorder.none,
            filled: false,
          ),
        ),
        actions: [
          IconButton(
            icon: const Icon(Icons.search_rounded),
            onPressed: _search,
          ),
        ],
      ),
      body: _future == null ? _suggestions(context) : _results(),
    );
  }

  // -- idle suggestions -------------------------------------------------------

  Widget _suggestions(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    const trending = ['Atta', 'Milk', 'Maggi', 'Tea', 'Cooking oil', 'Toor dal'];
    return ListView(
      padding: const EdgeInsets.all(20),
      children: [
        Text(
          'Trending near you',
          style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w800),
        ),
        const SizedBox(height: 4),
        Text(
          'Live prices across Blinkit, Zepto and Instamart.',
          style: TextStyle(
            fontSize: 13,
            color: scheme.onSurface.withValues(alpha: 0.6),
          ),
        ),
        const SizedBox(height: 16),
        Wrap(
          spacing: 10,
          runSpacing: 10,
          children: [
            for (final t in trending)
              ActionChip(
                label: Text(t),
                avatar: const Icon(Icons.trending_up_rounded, size: 16),
                onPressed: () {
                  _controller.text = t;
                  _search();
                },
              ),
          ],
        ),
      ],
    );
  }

  // -- results -----------------------------------------------------------------

  Widget _results() {
    return FutureBuilder<List<ComparisonResult>>(
      future: _future,
      builder: (context, snap) {
        if (snap.connectionState == ConnectionState.waiting) {
          return const Center(child: CircularProgressIndicator());
        }
        if (snap.hasError) {
          return Center(child: Text('Something went wrong.\n${snap.error}'));
        }
        final results = snap.data ?? [];
        if (results.isEmpty) {
          return const Center(child: Text('No matches found.'));
        }
        return ListView.builder(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          itemCount: results.length,
          itemBuilder: (context, i) => _resultCard(context, results[i]),
        );
      },
    );
  }

  Widget _resultCard(BuildContext context, ComparisonResult r) {
    final state = context.read<AppState>();
    final sorted = [...r.prices]
      ..sort((a, b) => r.bills[a.platformId]!.total
          .compareTo(r.bills[b.platformId]!.total));
    return Container(
      margin: const EdgeInsets.only(bottom: 16),
      decoration: saudaCard(context),
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${r.product.brand} ${r.product.name}',
                      style: const TextStyle(
                          fontSize: 16, fontWeight: FontWeight.w800),
                    ),
                    Text(
                      r.product.packSize,
                      style: TextStyle(
                        fontSize: 12.5,
                        color: Theme.of(context)
                            .colorScheme
                            .onSurface
                            .withValues(alpha: 0.6),
                      ),
                    ),
                  ],
                ),
              ),
              OutlinedButton.icon(
                onPressed: () {
                  state.addToCart(r.product);
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(
                        content: Text(
                            '${r.product.name} added to split cart')),
                  );
                },
                icon: const Icon(Icons.add_rounded, size: 18),
                label: const Text('Add'),
                style: OutlinedButton.styleFrom(
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 6),
          if (r.perUnitSpread > 0.10) _markupCallout(context, r),
          const SizedBox(height: 6),
          for (final price in sorted)
            _platformRow(context, state, r, price),
        ],
      ),
    );
  }

  Widget _markupCallout(BuildContext context, ComparisonResult r) {
    final pct = (r.perUnitSpread * 100).round();
    return Container(
      margin: const EdgeInsets.only(bottom: 8),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
      decoration: BoxDecoration(
        color: SaudaColors.amber.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(12),
      ),
      child: Row(
        children: [
          const Icon(Icons.info_outline_rounded,
              size: 16, color: SaudaColors.amberDeep),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              'Pack-size markup spotted: per-unit prices differ by $pct% across platforms.',
              style: const TextStyle(
                  fontSize: 12, color: SaudaColors.amberDeep),
            ),
          ),
        ],
      ),
    );
  }

  Widget _platformRow(BuildContext context, AppState state,
      ComparisonResult r, PlatformPrice price) {
    final platform = state.platformOf(price.platformId);
    final bill = r.bills[price.platformId]!;
    final bool isBest = price.platformId == r.bestPlatformId;
    final scheme = Theme.of(context).colorScheme;
    final perUnit =
        '${inr(r.product.perUnit(price.price))} ${r.product.perUnitLabel}';

    return Container(
      margin: const EdgeInsets.only(top: 10),
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: isBest
            ? SaudaColors.emerald.withValues(alpha: 0.08)
            : scheme.surfaceContainerHighest.withValues(alpha: 0.5),
        borderRadius: BorderRadius.circular(14),
        border: isBest
            ? Border.all(color: SaudaColors.emerald.withValues(alpha: 0.5))
            : null,
      ),
      child: Column(
        children: [
          Row(
            children: [
              PlatformAvatar(platform: platform, radius: 17),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        Text(
                          platform.name,
                          style: const TextStyle(
                              fontWeight: FontWeight.w700, fontSize: 14),
                        ),
                        const SizedBox(width: 8),
                        if (isBest) const BestBadge(),
                      ],
                    ),
                    const SizedBox(height: 4),
                    FreshnessBadge(freshness: price.freshness),
                  ],
                ),
              ),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(
                    inr(price.price),
                    style: tabularStyle.copyWith(
                      fontSize: 19,
                      fontWeight: FontWeight.w900,
                    ),
                  ),
                  Text(
                    perUnit,
                    style: TextStyle(
                      fontSize: 11.5,
                      color: scheme.onSurface.withValues(alpha: 0.6),
                    ),
                  ),
                  if (price.mrp > price.price)
                    Text(
                      '${price.discountPct.round()}% off MRP',
                      style: const TextStyle(
                        fontSize: 11,
                        color: SaudaColors.emerald,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                ],
              ),
            ],
          ),
          Theme(
            data: Theme.of(context)
                .copyWith(dividerColor: Colors.transparent),
            child: ExpansionTile(
              title: Text(
                'True total · ${inr(bill.total)}',
                style: tabularStyle.copyWith(
                  fontSize: 13.5,
                  fontWeight: FontWeight.w700,
                  color: scheme.primary,
                ),
              ),
              subtitle: Text(
                'incl. delivery, fees, GST${bill.appliedCoupon == null ? '' : ' · coupon ${bill.appliedCoupon}'}',
                style: TextStyle(
                  fontSize: 11.5,
                  color: scheme.onSurface.withValues(alpha: 0.55),
                ),
              ),
              children: [
                BillLinesView(bill: bill),
                const SizedBox(height: 8),
                SizedBox(
                  width: double.infinity,
                  child: FilledButton.icon(
                    onPressed: () => openPlatformShop(
                      context,
                      platformId: price.platformId,
                      platformName: platform.name,
                    ),
                    icon: const Icon(Icons.open_in_new_rounded, size: 16),
                    label: Text('Buy on ${platform.name}'),
                    style: FilledButton.styleFrom(
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(12),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
