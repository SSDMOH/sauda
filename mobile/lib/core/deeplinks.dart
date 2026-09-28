import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

/// Hands the user off to the platform's own app / site to check out.
///
/// v1 opens the platform storefront in the external browser (which usually
/// offers to open the installed native app). The production step is
/// per-platform cart-transfer deep links — tracked, not silently missing.
String? platformShopUrl(String platformId) {
  switch (platformId) {
    case 'blinkit':
      return 'https://www.blinkit.com';
    case 'zepto':
      return 'https://www.zepto.com';
    case 'instamart':
      return 'https://www.swiggy.com';
    default:
      return null;
  }
}

Future<void> openPlatformShop(
  BuildContext context, {
  required String platformId,
  required String platformName,
}) async {
  final url = platformShopUrl(platformId);
  if (url == null) {
    _note(context, 'Handoff for $platformName isn\u2019t wired yet.');
    return;
  }
  final ok = await launchUrl(
    Uri.parse(url),
    mode: LaunchMode.externalApplication,
  );
  if (!ok && context.mounted) {
    _note(context, 'Couldn\u2019t open $platformName.');
  }
}

void _note(BuildContext context, String message) {
  ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
}
