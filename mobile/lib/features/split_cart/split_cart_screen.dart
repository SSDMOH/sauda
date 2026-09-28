import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/app_state.dart';
import '../../core/deeplinks.dart';
import '../../core/format.dart';
import '../../core/models.dart';
import '../../core/theme.dart';
import '../../core/widgets.dart';

/// Smart Split Cart: the optimiser's plan rendered as per-platform groups
/// with itemised bills, a consolidate toggle, and a savings receipt.
class SplitCartScreen extends StatefulWidget {
  const SplitCartScreen({super.key});

  @override
  State<SplitCartScreen> createState() => _SplitCartScreenState();
}

class _SplitCartScreenState extends State<SplitCartScreen> {
  Future<SplitPlan>? _plan;
  String _sig = '';
  bool _consolidate = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    final state = context.watch<AppState>();
    final sig =
        state.cart.map((c) => '${c.product.id}x${c.qty}').join(',');
    if (sig != _sig) {
      _sig = sig;
      _plan = sig.isEmpty
          ? null
          : state.api.optimizeCart(
              items: state.cart, lat: 28.6139, lng: 77.2090);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Split Cart',
          style: TextStyle(fontWeight: FontWeight.w900),
        ),
        actions: [
          Consumer<AppState>(
            builder: (_, state, __) => state.cart.isEmpty
                ? const SizedBox.shrink()
                : TextButton(
                    onPressed: state.clearCart,
                    child: const Text('Clear'),
                  ),
          ),
        ],
      ),
      body: Consumer<AppState>(
        builder: (context, state, _) {
          if (state.cart.isEmpty) {
            return EmptyState(
              icon: Icons.call_split_rounded,
              title: 'Your split cart is empty',
              subtitle:
                  'Add items from any comparison and Sauda will split them across platforms for the lowest true total.',
              actionLabel: 'Load demo cart',
              onAction: state.loadDemoCart,
            );
          }
          return FutureBuilder<SplitPlan>(
            future: _plan,
            builder: (context, snap) {
              if (snap.connectionState == ConnectionState.waiting) {
                return const Center(
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      CircularProgressIndicator(),
                      SizedBox(height: 12),
                      Text('Optimising across platforms…'),
                    ],
                  ),
                );
              }
              if (snap.hasError || !snap.hasData) {
                return const Center(
                    child: Text('Could not optimise right now.'));
              }
              return _planView(context, state, snap.data!);
            },
          );
        },
      ),
    );
  }

  Widget _planView(BuildContext context, AppState state, SplitPlan plan) {
    return ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
      children: [
        _receiptCard(context, plan),
        const SizedBox(height: 12),
        _cartSummary(context, state),
        const SizedBox(height: 8),
        SwitchListTile(
          value: _consolidate,
          onChanged: (v) => setState(() => _consolidate = v),
          title: const Text(
            'Consolidate to 1 delivery',
            style: TextStyle(fontWeight: FontWeight.w700),
          ),
          subtitle: Text(
            _consolidate
                ? 'One delivery, slightly higher total.'
                : 'Off: maximum savings across ${plan.groups.length} deliveries.',
            style: const TextStyle(fontSize: 12),
          ),
          activeThumbColor: SaudaColors.emerald,
        ),
        const SizedBox(height: 4),
        if (_consolidate)
          _consolidatedCard(context, state, plan)
        else
          for (final g in plan.groups) _groupCard(context, g),
        const SizedBox(height: 16),
        _orderAllButton(context, plan),
      ],
    );
  }

  // -- receipt ---------------------------------------------------------------

  Widget _receiptCard(BuildContext context, SplitPlan plan) {
    return Container(
      decoration: BoxDecoration(
        gradient: heroGradient,
        borderRadius: BorderRadius.circular(24),
      ),
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: SaudaColors.amber.withValues(alpha: 0.2),
                  borderRadius: BorderRadius.circular(14),
                ),
                child: const Icon(Icons.savings_outlined,
                    color: SaudaColors.amber, size: 26),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'Smart Split saves you',
                      style:
                          TextStyle(fontSize: 13, color: Colors.white70),
                    ),
                    Text(
                      inr(plan.savingsVsConsolidated),
                      style: tabularStyle.copyWith(
                        fontSize: 30,
                        fontWeight: FontWeight.w900,
                        color: Colors.white,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),
          _receiptRow('Split total', inr(plan.total), true),
          _receiptRow(
              'One platform (${_platformName(context, plan.consolidatedPlatformId)})',
              inr(plan.consolidatedBill.total),
              false),
          _receiptRow('Priciest option', inr(plan.total + plan.savingsVsMostExpensive), false),
          const SizedBox(height: 6),
          Text(
            'Arrives in ~${plan.maxEta} min across ${plan.groups.length} deliveries.',
            style:
                const TextStyle(fontSize: 12, color: Colors.white70),
          ),
        ],
      ),
    );
  }

  String _platformName(BuildContext context, String id) =>
      context.read<AppState>().platformOf(id).name;

  Widget _receiptRow(String label, String value, bool bold) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        children: [
          Expanded(
            child: Text(
              label,
              style: TextStyle(
                fontSize: 13,
                color: Colors.white.withValues(alpha: 0.8),
              ),
            ),
          ),
          Text(
            value,
            style: tabularStyle.copyWith(
              fontSize: bold ? 15 : 13,
              fontWeight: bold ? FontWeight.w900 : FontWeight.w600,
              color: Colors.white,
            ),
          ),
        ],
      ),
    );
  }

  // -- cart summary ------------------------------------------------------------

  Widget _cartSummary(BuildContext context, AppState state) {
    return Container(
      decoration: saudaCard(context, radius: 16),
      child: ExpansionTile(
        title: Text(
          'Your cart · ${state.cartCount} items',
          style: const TextStyle(fontWeight: FontWeight.w700),
        ),
        children: [
          for (final item in state.cart)
            Padding(
              padding:
                  const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      '${item.product.brand} ${item.product.name} · ${item.product.packSize}',
                      style: const TextStyle(fontSize: 13),
                    ),
                  ),
                  _qtyStepper(context, state, item),
                ],
              ),
            ),
          const SizedBox(height: 8),
        ],
      ),
    );
  }

  Widget _qtyStepper(BuildContext context, AppState state, CartItem item) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        IconButton(
          visualDensity: VisualDensity.compact,
          icon: const Icon(Icons.remove_circle_outline_rounded, size: 20),
          onPressed: () => state.setQty(item.product, item.qty - 1),
        ),
        Text('${item.qty}',
            style:
                tabularStyle.copyWith(fontWeight: FontWeight.w800)),
        IconButton(
          visualDensity: VisualDensity.compact,
          icon: const Icon(Icons.add_circle_outline_rounded, size: 20),
          onPressed: () => state.setQty(item.product, item.qty + 1),
        ),
      ],
    );
  }

  // -- groups ------------------------------------------------------------------

  Widget _groupCard(BuildContext context, SplitGroup g) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: saudaCard(context),
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              PlatformAvatar(platform: g.platform, radius: 18),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      g.platform.name,
                      style: const TextStyle(
                          fontWeight: FontWeight.w800, fontSize: 15),
                    ),
                    Text(
                      '${g.items.length} items · ${etaLabel(g.etaMinutes)}',
                      style: TextStyle(
                        fontSize: 12,
                        color: scheme.onSurface.withValues(alpha: 0.6),
                      ),
                    ),
                  ],
                ),
              ),
              FreshnessBadge(freshness: g.bill.freshness),
            ],
          ),
          const SizedBox(height: 10),
          for (final it in g.items)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      '${it.product.brand} ${it.product.name} · ${it.product.packSize} ×${it.qty}',
                      style: const TextStyle(fontSize: 13),
                    ),
                  ),
                  Text(
                    inr(it.lineTotal),
                    style:
                        tabularStyle.copyWith(fontWeight: FontWeight.w700),
                  ),
                ],
              ),
            ),
          Theme(
            data: Theme.of(context)
                .copyWith(dividerColor: Colors.transparent),
            child: ExpansionTile(
              title: Text(
                'Group total · ${inr(g.bill.total)}',
                style: tabularStyle.copyWith(
                  fontWeight: FontWeight.w800,
                  color: scheme.primary,
                ),
              ),
              children: [BillLinesView(bill: g.bill)],
            ),
          ),
          const SizedBox(height: 6),
          SizedBox(
            width: double.infinity,
            child: FilledButton.icon(
              onPressed: () => openPlatformShop(
                context,
                platformId: g.platform.id,
                platformName: g.platform.name,
              ),
              icon: const Icon(Icons.open_in_new_rounded, size: 16),
              label: Text('Order on ${g.platform.name}'),
              style: FilledButton.styleFrom(
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(12),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _consolidatedCard(
      BuildContext context, AppState state, SplitPlan plan) {
    final platform = state.platformOf(plan.consolidatedPlatformId);
    final bill = plan.consolidatedBill;
    return Container(
      decoration: saudaCard(context),
      padding: const EdgeInsets.all(16),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              PlatformAvatar(platform: platform, radius: 18),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  'Everything on ${platform.name}',
                  style: const TextStyle(
                      fontWeight: FontWeight.w800, fontSize: 15),
                ),
              ),
              FreshnessBadge(freshness: bill.freshness),
            ],
          ),
          const SizedBox(height: 8),
          BillLinesView(bill: bill),
          const SizedBox(height: 8),
          SizedBox(
            width: double.infinity,
            child: FilledButton.icon(
              onPressed: () => openPlatformShop(
                context,
                platformId: plan.consolidatedPlatformId,
                platformName: platform.name,
              ),
              icon: const Icon(Icons.open_in_new_rounded, size: 16),
              label: Text('Order on ${platform.name}'),
              style: FilledButton.styleFrom(
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(12),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _orderAllButton(BuildContext context, SplitPlan plan) {
    return OutlinedButton.icon(
      onPressed: () async {
        // One handoff per group, in order — the user lands in the last one.
        for (final g in plan.groups) {
          await openPlatformShop(
            context,
            platformId: g.platform.id,
            platformName: g.platform.name,
          );
        }
      },
      icon: const Icon(Icons.rocket_launch_outlined),
      label: const Text('Order all groups'),
      style: OutlinedButton.styleFrom(
        padding: const EdgeInsets.symmetric(vertical: 14),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
        ),
      ),
    );
  }
}
