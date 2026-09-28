import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../core/app_state.dart';
import '../../core/format.dart';
import '../../core/models.dart';
import '../../core/theme.dart';
import '../../core/widgets.dart';

/// Price-drop alerts: list of watched items + a bottom sheet to set one.
class AlertsScreen extends StatelessWidget {
  const AlertsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Price alerts',
          style: TextStyle(fontWeight: FontWeight.w900),
        ),
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => _showSetAlertSheet(context),
        icon: const Icon(Icons.add_alert_outlined),
        label: const Text('Set alert'),
      ),
      body: Consumer<AppState>(
        builder: (context, state, _) {
          final alerts = state.alerts;
          if (alerts.isEmpty) {
            return EmptyState(
              icon: Icons.notifications_outlined,
              title: 'No alerts yet',
              subtitle:
                  'Set a target price on anything and Sauda will ping you the moment it drops.',
              actionLabel: 'Set your first alert',
              onAction: () => _showSetAlertSheet(context),
            );
          }
          return ListView.builder(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 90),
            itemCount: alerts.length,
            itemBuilder: (context, i) =>
                _alertCard(context, state, alerts[i]),
          );
        },
      ),
    );
  }

  Widget _alertCard(
      BuildContext context, AppState state, PriceAlert alert) {
    final scheme = Theme.of(context).colorScheme;
    final platform = state.platformOf(alert.currentBestPlatformId);
    final hit = alert.currentBest <= alert.targetPrice;
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: saudaCard(context, radius: 18),
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
                      alert.productName,
                      style: const TextStyle(
                          fontWeight: FontWeight.w800, fontSize: 15),
                    ),
                    Text(
                      alert.packSize,
                      style: TextStyle(
                        fontSize: 12,
                        color: scheme.onSurface.withValues(alpha: 0.6),
                      ),
                    ),
                  ],
                ),
              ),
              if (hit)
                Container(
                  padding: const EdgeInsets.symmetric(
                      horizontal: 10, vertical: 5),
                  decoration: BoxDecoration(
                    color: SaudaColors.emerald.withValues(alpha: 0.15),
                    borderRadius: BorderRadius.circular(999),
                  ),
                  child: const Text(
                    'TARGET HIT',
                    style: TextStyle(
                      fontSize: 10,
                      fontWeight: FontWeight.w900,
                      letterSpacing: 0.8,
                      color: SaudaColors.emerald,
                    ),
                  ),
                ),
              IconButton(
                visualDensity: VisualDensity.compact,
                icon: const Icon(Icons.delete_outline_rounded, size: 20),
                onPressed: () async {
                  await state.api.deleteAlert(alert.id);
                  await state.refreshAlerts();
                },
              ),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              _priceChip('Target', inr(alert.targetPrice), true),
              const SizedBox(width: 10),
              _priceChip(
                  'Best now · ${platform.name}',
                  inr(alert.currentBest),
                  false),
            ],
          ),
          const SizedBox(height: 10),
          ClipRRect(
            borderRadius: BorderRadius.circular(999),
            child: LinearProgressIndicator(
              value: alert.progress,
              minHeight: 8,
              backgroundColor:
                  scheme.onSurface.withValues(alpha: 0.1),
              valueColor: AlwaysStoppedAnimation<Color>(
                hit ? SaudaColors.emerald : SaudaColors.amber,
              ),
            ),
          ),
          const SizedBox(height: 6),
          Text(
            hit
                ? 'At or below your target — grab it.'
                : alert.currentBest <= 0
                    ? 'Checking the live price…'
                    : '${inr(alert.currentBest - alert.targetPrice)} to go before it hits your target.',
            style: TextStyle(
              fontSize: 12,
              color: scheme.onSurface.withValues(alpha: 0.6),
            ),
          ),
        ],
      ),
    );
  }

  Widget _priceChip(String label, String value, bool highlight) {
    return Expanded(
      child: Container(
        padding:
            const EdgeInsets.symmetric(horizontal: 12, vertical: 9),
        decoration: BoxDecoration(
          color: highlight
              ? SaudaColors.amber.withValues(alpha: 0.14)
              : Colors.transparent,
          borderRadius: BorderRadius.circular(12),
          border: highlight
              ? null
              : Border.all(color: SaudaColors.lineDark),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(label,
                style:
                    const TextStyle(fontSize: 11, color: Colors.grey)),
            Text(
              value,
              style: tabularStyle.copyWith(
                fontSize: 16,
                fontWeight: FontWeight.w800,
              ),
            ),
          ],
        ),
      ),
    );
  }

  void _showSetAlertSheet(BuildContext context) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (_) => const _SetAlertSheet(),
    );
  }
}

class _SetAlertSheet extends StatefulWidget {
  const _SetAlertSheet();

  @override
  State<_SetAlertSheet> createState() => _SetAlertSheetState();
}

class _SetAlertSheetState extends State<_SetAlertSheet> {
  final _priceCtrl = TextEditingController();
  List<Product> _catalog = [];
  Product? _selected;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    context.read<AppState>().api.catalog().then((c) {
      if (mounted) {
        setState(() {
          _catalog = c;
          _selected = c.first;
        });
      }
    });
  }

  @override
  void dispose() {
    _priceCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final state = context.read<AppState>();
    return Padding(
      padding: EdgeInsets.only(
        left: 20,
        right: 20,
        top: 20,
        bottom: MediaQuery.of(context).viewInsets.bottom + 24,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              const Expanded(
                child: Text(
                  'Set a price alert',
                  style:
                      TextStyle(fontSize: 18, fontWeight: FontWeight.w800),
                ),
              ),
              IconButton(
                icon: const Icon(Icons.close_rounded),
                onPressed: () => Navigator.pop(context),
              ),
            ],
          ),
          const SizedBox(height: 12),
          DropdownButtonFormField<Product>(
            value: _selected,
            items: _catalog
                .map((p) => DropdownMenuItem(
                      value: p,
                      child: Text(
                        '${p.brand} ${p.name} · ${p.packSize}',
                        overflow: TextOverflow.ellipsis,
                      ),
                    ))
                .toList(),
            onChanged: (p) => setState(() => _selected = p),
            decoration: const InputDecoration(labelText: 'Product'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _priceCtrl,
            keyboardType: TextInputType.number,
            inputFormatters: [FilteringTextInputFormatter.digitsOnly],
            decoration: const InputDecoration(
              labelText: 'Target price',
              prefixText: '₹ ',
            ),
          ),
          if (_error != null) ...[
            const SizedBox(height: 8),
            Text(_error!,
                style:
                    const TextStyle(color: Colors.red, fontSize: 12.5)),
          ],
          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: FilledButton(
              onPressed: (_busy || _selected == null) ? null : () => _save(state),
              style: FilledButton.styleFrom(
                padding: const EdgeInsets.symmetric(vertical: 14),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(14),
                ),
              ),
              child: _busy
                  ? const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('Create alert'),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _save(AppState state) async {
    final target = double.tryParse(_priceCtrl.text);
    if (target == null || target <= 0) {
      setState(() => _error = 'Enter a valid target price');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await state.api.createAlert(
        productId: _selected!.id,
        targetPrice: target,
      );
      await state.refreshAlerts();
      if (mounted) {
        Navigator.pop(context);
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
              content: Text('Alert set — we\u2019ll ping you on a drop.')),
        );
      }
    } catch (e) {
      setState(() => _error = e.toString().replaceFirst('Exception: ', ''));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }
}
