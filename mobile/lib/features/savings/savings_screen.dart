import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/app_state.dart';
import '../../core/format.dart';
import '../../core/models.dart';
import '../../core/theme.dart';
import '../../core/widgets.dart';

/// Savings ledger: lifetime hero, 30-day bar chart, monthly ring, receipts.
class SavingsScreen extends StatefulWidget {
  const SavingsScreen({super.key});

  @override
  State<SavingsScreen> createState() => _SavingsScreenState();
}

class _SavingsScreenState extends State<SavingsScreen> {
  late Future<SavingsSummary> _future;

  @override
  void initState() {
    super.initState();
    _future = context.read<AppState>().api.savings();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Savings ledger',
          style: TextStyle(fontWeight: FontWeight.w900),
        ),
      ),
      body: FutureBuilder<SavingsSummary>(
        future: _future,
        builder: (context, snap) {
          if (!snap.hasData) {
            return const Center(child: CircularProgressIndicator());
          }
          final s = snap.data!;
          return ListView(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
            children: [
              _lifetimeHero(context, s),
              const SectionHeader(title: 'Last 30 days'),
              _barsCard(context, s),
              const SectionHeader(title: 'This month'),
              _monthRingCard(context, s),
              const SectionHeader(title: 'Receipts'),
              for (final e in s.entries) _receiptRow(context, e),
            ],
          );
        },
      ),
    );
  }

  // -- hero -------------------------------------------------------------------

  Widget _lifetimeHero(BuildContext context, SavingsSummary s) {
    return Container(
      decoration: BoxDecoration(
        gradient: heroGradient,
        borderRadius: BorderRadius.circular(24),
      ),
      padding: const EdgeInsets.all(22),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Text(
            'Lifetime savings',
            style: TextStyle(fontSize: 13, color: Colors.white70),
          ),
          const SizedBox(height: 4),
          Text(
            inr(s.lifetime),
            style: tabularStyle.copyWith(
              fontSize: 44,
              fontWeight: FontWeight.w900,
              color: Colors.white,
            ),
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              const Icon(Icons.local_fire_department,
                  size: 16, color: SaudaColors.amber),
              const SizedBox(width: 4),
              Text(
                '${s.streakDays}-day streak',
                style:
                    const TextStyle(fontSize: 13, color: Colors.white),
              ),
              const SizedBox(width: 16),
              const Icon(Icons.receipt_long_outlined,
                  size: 16, color: Colors.white70),
              const SizedBox(width: 4),
              Text(
                '${s.entries.length} receipts this month',
                style: const TextStyle(
                    fontSize: 13, color: Colors.white70),
              ),
            ],
          ),
        ],
      ),
    );
  }

  // -- bars --------------------------------------------------------------------

  Widget _barsCard(BuildContext context, SavingsSummary s) {
    final data = s.last30Days;
    final maxV = data.fold<double>(0, (a, b) => a > b ? a : b);
    return Container(
      decoration: saudaCard(context, radius: 18),
      padding: const EdgeInsets.fromLTRB(16, 18, 8, 12),
      child: SizedBox(
        height: 170,
        child: SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          reverse: true,
          child: SizedBox(
            width: data.length * 20.0 + 16,
            child: BarChart(
              BarChartData(
                maxY: maxV * 1.15,
                barGroups: [
                  for (var i = 0; i < data.length; i++)
                    BarChartGroupData(
                      x: i,
                      barRods: [
                        BarChartRodData(
                          toY: data[i],
                          width: 10,
                          borderRadius: BorderRadius.circular(4),
                          color: data[i] == maxV
                              ? SaudaColors.amber
                              : SaudaColors.emerald
                                  .withValues(alpha: 0.75),
                        ),
                      ],
                    ),
                ],
                titlesData: const FlTitlesData(show: false),
                gridData: const FlGridData(show: false),
                borderData: FlBorderData(show: false),
                barTouchData: BarTouchData(
                  enabled: true,
                  touchTooltipData: BarTouchTooltipData(
                    getTooltipItem: (group, _, rod, __) =>
                        BarTooltipItem(
                      inr(rod.toY),
                      const TextStyle(
                        fontWeight: FontWeight.w800,
                        color: Colors.white,
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  // -- monthly ring --------------------------------------------------------------

  Widget _monthRingCard(BuildContext context, SavingsSummary s) {
    final scheme = Theme.of(context).colorScheme;
    final remaining =
        (s.monthlyGoal - s.thisMonth).clamp(0.0, double.infinity).toDouble();
    return Container(
      decoration: saudaCard(context, radius: 18),
      padding: const EdgeInsets.all(18),
      child: Row(
        children: [
          SizedBox(
            width: 110,
            height: 110,
            child: Stack(
              alignment: Alignment.center,
              children: [
                PieChart(
                  PieChartData(
                    sections: [
                      PieChartSectionData(
                        value: s.thisMonth,
                        color: SaudaColors.emerald,
                        radius: 13,
                        showTitle: false,
                      ),
                      PieChartSectionData(
                        value: remaining,
                        color: scheme.onSurface.withValues(alpha: 0.08),
                        radius: 13,
                        showTitle: false,
                      ),
                    ],
                    centerSpaceRadius: 40,
                    startDegreeOffset: -90,
                    sectionsSpace: 0,
                  ),
                ),
                Text(
                  '${(s.thisMonth / s.monthlyGoal * 100).round()}%',
                  style: tabularStyle.copyWith(
                    fontSize: 17,
                    fontWeight: FontWeight.w900,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(width: 16),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${inr(s.thisMonth)} of ${inr(s.monthlyGoal)} goal',
                  style: const TextStyle(
                      fontWeight: FontWeight.w800, fontSize: 15),
                ),
                const SizedBox(height: 6),
                Text(
                  remaining > 0
                      ? '${inr(remaining)} to go — one more split cart should do it.'
                      : 'Goal smashed. Bargainer status: elite.',
                  style: TextStyle(
                    fontSize: 12.5,
                    color: scheme.onSurface.withValues(alpha: 0.6),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  // -- receipts -------------------------------------------------------------------

  Widget _receiptRow(BuildContext context, SavingsEntry e) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      margin: const EdgeInsets.only(bottom: 10),
      decoration: saudaCard(context, radius: 16),
      child: ListTile(
        leading: Container(
          padding: const EdgeInsets.all(9),
          decoration: BoxDecoration(
            color: SaudaColors.emerald.withValues(alpha: 0.12),
            borderRadius: BorderRadius.circular(12),
          ),
          child: const Icon(Icons.savings_outlined,
              color: SaudaColors.emerald, size: 20),
        ),
        title: Text(e.title,
            style: const TextStyle(fontWeight: FontWeight.w700)),
        subtitle: Text(
          '${e.detail} · ${dateLabel(e.date)}',
          style: TextStyle(
            fontSize: 12,
            color: scheme.onSurface.withValues(alpha: 0.6),
          ),
        ),
        trailing: Text(
          '+${inr(e.saved)}',
          style: tabularStyle.copyWith(
            color: SaudaColors.emerald,
            fontWeight: FontWeight.w900,
            fontSize: 15,
          ),
        ),
      ),
    );
  }
}
