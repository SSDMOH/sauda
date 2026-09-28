import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'core/api_client.dart';
import 'core/app_state.dart';
import 'core/theme.dart';
import 'data/http_api.dart';
import 'data/mock_service.dart';
import 'features/alerts/alerts_screen.dart';
import 'features/compare/compare_screen.dart';
import 'features/home/home_screen.dart';
import 'features/savings/savings_screen.dart';
import 'features/split_cart/split_cart_screen.dart';

/// Backend base URL. On the Android emulator, 10.0.2.2 is the host loopback;
/// on a physical device pass your machine's LAN IP:
///   flutter run --dart-define=SAUDA_API=http://192.168.1.5:8000
const _apiBaseUrl = String.fromEnvironment(
  'SAUDA_API',
  defaultValue: 'http://10.0.2.2:8000',
);

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final api = await _resolveApi();
  runApp(
    ChangeNotifierProvider(
      create: (_) =>
          AppState(api: api.$1, liveBackend: api.$2),
      child: const SaudaApp(),
    ),
  );
}

/// Prefer the live FastAPI backend; fall back to the on-device demo mock
/// when it isn't reachable (backend down, no network, fresh install).
/// Returns (api, isLive).
Future<(SaudaApi, bool)> _resolveApi() async {
  final httpApi = HttpSaudaApi(baseUrl: _apiBaseUrl);
  try {
    await httpApi.ping();
    return (httpApi, true);
  } catch (_) {
    return (MockSaudaApi(), false);
  }
}

class SaudaApp extends StatelessWidget {
  const SaudaApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Sauda',
      debugShowCheckedModeBanner: false,
      theme: SaudaTheme.light,
      darkTheme: SaudaTheme.dark,
      themeMode: ThemeMode.system,
      home: const Shell(),
    );
  }
}

/// Bottom-navigation shell. The tab index lives in [AppState] so any screen
/// can jump tabs (Home search -> Compare). Tabs stay alive in an IndexedStack.
class Shell extends StatefulWidget {
  const Shell({super.key});

  @override
  State<Shell> createState() => _ShellState();
}

class _ShellState extends State<Shell> {
  static const List<Widget> _tabs = [
    HomeScreen(),
    CompareScreen(),
    SplitCartScreen(),
    AlertsScreen(),
    SavingsScreen(),
  ];

  @override
  void initState() {
    super.initState();
    // Warm the caches (platforms, accounts, alerts, savings) so every
    // tab renders instantly on first visit.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      context.read<AppState>().refreshAll();
    });
  }

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    return Scaffold(
      body: IndexedStack(
        index: state.navIndex,
        children: _tabs,
      ),
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: state.navIndex,
        onTap: state.goToTab,
        items: [
          const BottomNavigationBarItem(
            icon: Icon(Icons.home_outlined),
            activeIcon: Icon(Icons.home_rounded),
            label: 'Home',
          ),
          const BottomNavigationBarItem(
            icon: Icon(Icons.compare_arrows_rounded),
            label: 'Compare',
          ),
          BottomNavigationBarItem(
            icon: Badge.count(
              count: state.cartCount,
              isLabelVisible: state.cartCount > 0,
              child: const Icon(Icons.call_split_rounded),
            ),
            label: 'Split Cart',
          ),
          const BottomNavigationBarItem(
            icon: Icon(Icons.notifications_outlined),
            activeIcon: Icon(Icons.notifications_rounded),
            label: 'Alerts',
          ),
          const BottomNavigationBarItem(
            icon: Icon(Icons.savings_outlined),
            label: 'Savings',
          ),
        ],
      ),
    );
  }
}
