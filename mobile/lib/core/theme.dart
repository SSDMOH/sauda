import 'package:flutter/material.dart';

/// Sauda visual identity: deep green + warm amber, premium fintech feel
/// (CRED / Fi style). Both themes are first-class citizens.

class SaudaColors {
  SaudaColors._();

  // Brand
  static const Color deepGreen = Color(0xFF0B3B2C);
  static const Color pine = Color(0xFF0E4A37);
  static const Color emerald = Color(0xFF34D399);
  static const Color mint = Color(0xFFA7F3D0);
  static const Color amber = Color(0xFFF5A524);
  static const Color amberDeep = Color(0xFFD97706);

  // Dark surfaces
  static const Color ink = Color(0xFF0A0E0C);
  static const Color coal = Color(0xFF101613);
  static const Color cardDark = Color(0xFF16201B);
  static const Color lineDark = Color(0xFF233129);

  // Light surfaces
  static const Color paper = Color(0xFFF7F4ED);
  static const Color cardLight = Color(0xFFFFFFFF);
  static const Color lineLight = Color(0xFFE7E0D2);

  // Platform brand colours
  static const Color blinkit = Color(0xFFF8CB46);
  static const Color zepto = Color(0xFFFF2459);
  static const Color instamart = Color(0xFFFC8019);
}

/// Tabular figures for every price in the app.
const TextStyle tabularStyle =
    TextStyle(fontFeatures: [FontFeature.tabularFigures()]);

class SaudaTheme {
  SaudaTheme._();

  static const double radius = 20;

  static ThemeData get light {
    final scheme = ColorScheme.light(
      primary: SaudaColors.deepGreen,
      onPrimary: Colors.white,
      secondary: SaudaColors.amberDeep,
      onSecondary: Colors.white,
      surface: SaudaColors.cardLight,
      onSurface: const Color(0xFF1A2420),
      surfaceContainerHighest: const Color(0xFFEFE9DA),
      error: const Color(0xFFB3261E),
    );
    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      scaffoldBackgroundColor: SaudaColors.paper,
      appBarTheme: const AppBarTheme(
        backgroundColor: SaudaColors.paper,
        foregroundColor: Color(0xFF1A2420),
        elevation: 0,
        centerTitle: false,
      ),
      bottomNavigationBarTheme: BottomNavigationBarThemeData(
        backgroundColor: SaudaColors.cardLight,
        selectedItemColor: SaudaColors.deepGreen,
        unselectedItemColor: Colors.grey.shade500,
        type: BottomNavigationBarType.fixed,
        showUnselectedLabels: true,
      ),
      chipTheme: const ChipThemeData(),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: Colors.white,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: const BorderSide(color: SaudaColors.lineLight),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: const BorderSide(color: SaudaColors.lineLight),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide:
              const BorderSide(color: SaudaColors.deepGreen, width: 1.6),
        ),
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
        ),
      ),
      dividerTheme: const DividerThemeData(
        color: SaudaColors.lineLight,
        thickness: 1,
        space: 1,
      ),
      expansionTileTheme: const ExpansionTileThemeData(
        tilePadding: EdgeInsets.symmetric(horizontal: 4),
        childrenPadding: EdgeInsets.only(bottom: 8),
      ),
    );
  }

  static ThemeData get dark {
    final scheme = ColorScheme.dark(
      primary: SaudaColors.emerald,
      onPrimary: SaudaColors.ink,
      secondary: SaudaColors.amber,
      onSecondary: SaudaColors.ink,
      surface: SaudaColors.cardDark,
      onSurface: const Color(0xFFEDEFEA),
      surfaceContainerHighest: SaudaColors.coal,
      error: const Color(0xFFFF8A80),
    );
    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      scaffoldBackgroundColor: SaudaColors.ink,
      appBarTheme: const AppBarTheme(
        backgroundColor: SaudaColors.ink,
        foregroundColor: Color(0xFFEDEFEA),
        elevation: 0,
        centerTitle: false,
      ),
      bottomNavigationBarTheme: const BottomNavigationBarThemeData(
        backgroundColor: SaudaColors.coal,
        selectedItemColor: SaudaColors.emerald,
        unselectedItemColor: Color(0xFF7A8A80),
        type: BottomNavigationBarType.fixed,
        showUnselectedLabels: true,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: SaudaColors.coal,
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 18, vertical: 14),
        hintStyle: const TextStyle(color: Color(0xFF7A8A80)),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: const BorderSide(color: SaudaColors.lineDark),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide: const BorderSide(color: SaudaColors.lineDark),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(16),
          borderSide:
              const BorderSide(color: SaudaColors.emerald, width: 1.6),
        ),
      ),
      snackBarTheme: SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
        backgroundColor: SaudaColors.cardDark,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(14),
        ),
      ),
      dividerTheme: const DividerThemeData(
        color: SaudaColors.lineDark,
        thickness: 1,
        space: 1,
      ),
      expansionTileTheme: const ExpansionTileThemeData(
        tilePadding: EdgeInsets.symmetric(horizontal: 4),
        childrenPadding: EdgeInsets.only(bottom: 8),
      ),
    );
  }
}

/// Gradient used for hero / receipt cards.
const LinearGradient heroGradient = LinearGradient(
  begin: Alignment.topLeft,
  end: Alignment.bottomRight,
  colors: [SaudaColors.pine, SaudaColors.deepGreen],
);

/// Shared card decoration (works on both themes via colorScheme).
BoxDecoration saudaCard(BuildContext context, {double radius = 20}) {
  final scheme = Theme.of(context).colorScheme;
  final bool dark = scheme.brightness == Brightness.dark;
  return BoxDecoration(
    color: scheme.surface,
    borderRadius: BorderRadius.circular(radius),
    border: Border.all(
      color: dark ? SaudaColors.lineDark : SaudaColors.lineLight,
    ),
    boxShadow: dark
        ? null
        : [
            BoxShadow(
              color: Colors.black.withValues(alpha: 0.05),
              blurRadius: 18,
              offset: const Offset(0, 8),
            ),
          ],
  );
}
