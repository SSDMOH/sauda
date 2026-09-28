import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../core/app_state.dart';
import '../../core/format.dart';
import '../../core/models.dart';
import '../../core/theme.dart';
import '../../core/widgets.dart';
import '../accounts/accounts_screen.dart';
import '../savings/savings_screen.dart';

/// Home: savings hero, search, verticals, platforms, recent comparisons.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  late Future<SavingsSummary> _savingsFuture;

  @override
  void initState() {
    super.initState();
    _savingsFuture = context.read<AppState>().api.savings();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: _wordmark(),
        actions: [
          IconButton(
            tooltip: 'Linked accounts',
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute(builder: (_) => const AccountsScreen()),
            ),
            icon: Stack(
              children: [
                const Icon(Icons.manage_accounts_outlined),
                Positioned(
                  right: 0,
                  top: 0,
                  child: Consumer<AppState>(
                    builder: (_, state, __) => state.linkedCount == 0
                        ? const SizedBox.shrink()
                        : Container(
                            padding: const EdgeInsets.all(4),
                            decoration: const BoxDecoration(
                              color: SaudaColors.amber,
                              shape: BoxShape.circle,
                            ),
                            child: Text(
                              '${state.linkedCount}',
                              style: const TextStyle(
                                fontSize: 9,
                                fontWeight: FontWeight.w900,
                                color: SaudaColors.ink,
                              ),
                            ),
                          ),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: () async {
          setState(() {
            _savingsFuture = context.read<AppState>().api.savings();
          });
          await context.read<AppState>().refreshAll();
        },
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
          children: [
            _locationRow(context),
            const SizedBox(height: 12),
            FutureBuilder<SavingsSummary>(
              future: _savingsFuture,
              builder: (context, snap) {
                if (!snap.hasData) {
                  return const SizedBox(
                    height: 170,
                    child: Center(child: CircularProgressIndicator()),
                  );
                }
                return _heroCard(context, snap.data!);
              },
            ),
            const SizedBox(height: 14),
            _searchBar(context),
            const SectionHeader(title: 'Shop by vertical'),
            _verticalChips(context),
            const SectionHeader(title: 'Your platforms'),
            _platformsRow(context),
            SectionHeader(
              title: 'Recent comparisons',
              action: TextButton(
                onPressed: () => _openCompare(context, ''),
                child: const Text('See all'),
              ),
            ),
            _recentList(context),
          ],
        ),
      ),
    );
  }

  // -- pieces ---------------------------------------------------------------

  Widget _wordmark() {
    return RichText(
      text: const TextSpan(
        style: TextStyle(fontSize: 26, fontWeight: FontWeight.w900),
        children: [
          TextSpan(
            text: 'sauda',
            style: TextStyle(color: SaudaColors.emerald),
          ),
          TextSpan(
            text: '.',
            style: TextStyle(color: SaudaColors.amber),
          ),
        ],
      ),
    );
  }

  Widget _locationRow(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final live = context.read<AppState>().liveBackend;
    return Row(
      children: [
        Icon(Icons.location_on_outlined,
            size: 16, color: scheme.onSurface.withValues(alpha: 0.6)),
        const SizedBox(width: 4),
        Text(
          'Connaught Place, New Delhi',
          style: TextStyle(
            fontSize: 12.5,
            color: scheme.onSurface.withValues(alpha: 0.6),
          ),
        ),
        const Icon(Icons.keyboard_arrow_down_rounded, size: 16),
        const Spacer(),
        Container(
          padding:
              const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
          decoration: BoxDecoration(
            color: (live ? SaudaColors.emerald : SaudaColors.amber)
                .withValues(alpha: 0.14),
            borderRadius: BorderRadius.circular(999),
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 6,
                height: 6,
                decoration: BoxDecoration(
                  color: live ? SaudaColors.emerald : SaudaColors.amber,
                  shape: BoxShape.circle,
                ),
              ),
              const SizedBox(width: 5),
              Text(
                live ? 'LIVE BACKEND' : 'DEMO DATA',
                style: TextStyle(
                  fontSize: 10,
                  fontWeight: FontWeight.w800,
                  letterSpacing: 0.8,
                  color: live ? SaudaColors.emerald : SaudaColors.amberDeep,
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _heroCard(BuildContext context, SavingsSummary s) {
    final pct = (s.thisMonth / s.monthlyGoal).clamp(0.0, 1.0);
    return Container(
      decoration: BoxDecoration(
        gradient: heroGradient,
        borderRadius: BorderRadius.circular(24),
      ),
      padding: const EdgeInsets.all(20),
      child: Column(
        children: [
          Row(
            children: [
              SizedBox(
                width: 118,
                height: 118,
                child: Stack(
                  alignment: Alignment.center,
                  children: [
                    PieChart(
                      PieChartData(
                        sections: [
                          PieChartSectionData(
                            value: s.thisMonth,
                            color: SaudaColors.amber,
                            radius: 13,
                            showTitle: false,
                          ),
                          PieChartSectionData(
                            value: (s.monthlyGoal - s.thisMonth)
                                .clamp(0.0, double.infinity)
                                .toDouble(),
                            color: Colors.white.withValues(alpha: 0.15),
                            radius: 13,
                            showTitle: false,
                          ),
                        ],
                        centerSpaceRadius: 42,
                        startDegreeOffset: -90,
                        sectionsSpace: 0,
                      ),
                    ),
                    Column(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          inr(s.thisMonth),
                          style: tabularStyle.copyWith(
                            fontSize: 18,
                            fontWeight: FontWeight.w900,
                            color: Colors.white,
                          ),
                        ),
                        Text(
                          '${(pct * 100).round()}% of goal',
                          style: const TextStyle(
                              fontSize: 10, color: Colors.white70),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 16),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      'Saved this month',
                      style:
                          TextStyle(fontSize: 13, color: Colors.white70),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      'Bargaining is paying off.',
                      style: const TextStyle(
                        fontSize: 17,
                        fontWeight: FontWeight.w800,
                        color: Colors.white,
                      ),
                    ),
                    const SizedBox(height: 10),
                    Row(
                      children: [
                        const Icon(
                          Icons.local_fire_department,
                          size: 16,
                          color: SaudaColors.amber,
                        ),
                        const SizedBox(width: 4),
                        Text(
                          '${s.streakDays}-day savings streak',
                          style: const TextStyle(
                              fontSize: 12.5, color: Colors.white),
                        ),
                      ],
                    ),
                    const SizedBox(height: 2),
                    Text(
                      'Lifetime ${inr(s.lifetime)}',
                      style: const TextStyle(
                          fontSize: 12.5, color: Colors.white70),
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),
          SizedBox(
            width: double.infinity,
            child: FilledButton(
              style: FilledButton.styleFrom(
                backgroundColor: SaudaColors.amber,
                foregroundColor: SaudaColors.ink,
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(14),
                ),
              ),
              onPressed: () => Navigator.push(
                context,
                MaterialPageRoute(builder: (_) => const SavingsScreen()),
              ),
              child: const Text(
                'View savings ledger',
                style: TextStyle(fontWeight: FontWeight.w800),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _searchBar(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return InkWell(
      borderRadius: BorderRadius.circular(16),
      onTap: () => _openCompare(context, ''),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 18, vertical: 15),
        decoration: BoxDecoration(
          color: scheme.surface,
          borderRadius: BorderRadius.circular(16),
          border: Border.all(
            color: scheme.brightness == Brightness.dark
                ? SaudaColors.lineDark
                : SaudaColors.lineLight,
          ),
        ),
        child: Row(
          children: [
            const Icon(Icons.search_rounded),
            const SizedBox(width: 10),
            Text(
              'Search atta, milk, Maggi…',
              style: TextStyle(
                fontSize: 14.5,
                color: scheme.onSurface.withValues(alpha: 0.45),
              ),
            ),
            const Spacer(),
            Icon(Icons.mic_none_rounded,
                color: scheme.onSurface.withValues(alpha: 0.45)),
          ],
        ),
      ),
    );
  }

  void _openCompare(BuildContext context, String query) {
    // Jump to the Compare tab and run the query there — no duplicate screen.
    context.read<AppState>().openCompare(query);
  }

  Widget _verticalChips(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final bool dark = scheme.brightness == Brightness.dark;
    final verticals = [
      ('Grocery', Icons.shopping_basket_outlined, true),
      ('Food', Icons.restaurant_outlined, false),
      ('Cabs', Icons.directions_car_outlined, false),
      ('E-commerce', Icons.shopping_bag_outlined, false),
    ];
    return SizedBox(
      height: 92,
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        itemCount: verticals.length,
        separatorBuilder: (_, __) => const SizedBox(width: 10),
        itemBuilder: (context, i) {
          final (label, icon, live) = verticals[i];
          final Color bg = live
              ? (dark ? SaudaColors.emerald : SaudaColors.deepGreen)
              : scheme.surface;
          final Color fg = live
              ? (dark ? SaudaColors.ink : Colors.white)
              : scheme.onSurface.withValues(alpha: 0.55);
          return Container(
            width: 108,
            decoration: BoxDecoration(
              color: bg,
              borderRadius: BorderRadius.circular(18),
              border: live
                  ? null
                  : Border.all(
                      color: dark
                          ? SaudaColors.lineDark
                          : SaudaColors.lineLight,
                    ),
            ),
            padding: const EdgeInsets.all(12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Icon(icon, color: fg),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      label,
                      style: TextStyle(
                          fontWeight: FontWeight.w800, color: fg),
                    ),
                    Text(
                      live ? '3 platforms live' : 'Coming soon',
                      style: TextStyle(
                        fontSize: 10,
                        color: fg.withValues(alpha: 0.7),
                      ),
                    ),
                  ],
                ),
              ],
            ),
          );
        },
      ),
    );
  }

  Widget _platformsRow(BuildContext context) {
    return Consumer<AppState>(
      builder: (context, state, _) {
        if (state.platforms.isEmpty) {
          return const SizedBox(
              height: 60, child: Center(child: CircularProgressIndicator()));
        }
        return Row(
          children: [
            for (final p in state.platforms) ...[
              _platformDot(context, state, p),
              const SizedBox(width: 14),
            ],
            const Spacer(),
            Text(
              '${state.linkedCount}/${state.platforms.length} linked',
              style: TextStyle(
                fontSize: 12,
                color: Theme.of(context)
                    .colorScheme
                    .onSurface
                    .withValues(alpha: 0.55),
              ),
            ),
          ],
        );
      },
    );
  }

  Widget _platformDot(BuildContext context, AppState state, PlatformInfo p) {
    final linked = state.isLinked(p.id);
    return Column(
      children: [
        Container(
          padding: const EdgeInsets.all(3),
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            border: Border.all(
              color: linked ? SaudaColors.emerald : Colors.transparent,
              width: 2,
            ),
          ),
          child: PlatformAvatar(platform: p, radius: 24),
        ),
        const SizedBox(height: 4),
        Text(p.name, style: const TextStyle(fontSize: 11)),
      ],
    );
  }

  Widget _recentList(BuildContext context) {
    final recents = [
      ('atta', 'Aashirvaad Atta 5 kg', 'Best ₹279 · Zepto', 'Saved ₹41'),
      ('milk', 'Amul Taaza 1 L', 'Best ₹71 · Zepto', 'Saved ₹4'),
      ('maggi', 'Maggi Family Pack', 'Best ₹165 · Zepto', 'Saved ₹15'),
    ];
    return Column(
      children: [
        for (final (query, title, best, saved) in recents)
          Container(
            margin: const EdgeInsets.only(bottom: 10),
            decoration: saudaCard(context, radius: 16),
            child: ListTile(
              onTap: () => _openCompare(context, query),
              title: Text(title,
                  style: const TextStyle(fontWeight: FontWeight.w700)),
              subtitle: Text(best),
              trailing: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    saved,
                    style: tabularStyle.copyWith(
                      color: SaudaColors.emerald,
                      fontWeight: FontWeight.w800,
                    ),
                  ),
                  const SizedBox(width: 4),
                  const Icon(Icons.chevron_right_rounded),
                ],
              ),
            ),
          ),
      ],
    );
  }
}
