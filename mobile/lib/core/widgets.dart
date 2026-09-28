import 'package:flutter/material.dart';

import 'format.dart';
import 'models.dart';
import 'theme.dart';

/// Small coloured dot + LIVE / EST label. Honesty is the product.
class FreshnessBadge extends StatelessWidget {
  final Freshness freshness;

  const FreshnessBadge({super.key, required this.freshness});

  @override
  Widget build(BuildContext context) {
    final bool live = freshness == Freshness.live;
    final Color dot = live ? SaudaColors.emerald : SaudaColors.amber;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: dot.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(999),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
            width: 6,
            height: 6,
            decoration: BoxDecoration(color: dot, shape: BoxShape.circle),
          ),
          const SizedBox(width: 5),
          Text(
            live ? 'LIVE' : '~ EST.',
            style: TextStyle(
              fontSize: 10,
              fontWeight: FontWeight.w800,
              letterSpacing: 0.8,
              color: dot,
            ),
          ),
        ],
      ),
    );
  }
}

/// Amber "BEST" pill for the winning platform.
class BestBadge extends StatelessWidget {
  const BestBadge({super.key});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          colors: [SaudaColors.amber, SaudaColors.amberDeep],
        ),
        borderRadius: BorderRadius.circular(999),
      ),
      child: const Text(
        'BEST',
        style: TextStyle(
          fontSize: 10,
          fontWeight: FontWeight.w900,
          letterSpacing: 1,
          color: SaudaColors.ink,
        ),
      ),
    );
  }
}

/// Circular platform avatar in brand colour.
class PlatformAvatar extends StatelessWidget {
  final PlatformInfo platform;
  final double radius;

  const PlatformAvatar({super.key, required this.platform, this.radius = 20});

  @override
  Widget build(BuildContext context) {
    return Container(
      width: radius * 2,
      height: radius * 2,
      decoration: BoxDecoration(
        color: Color(platform.brandColor),
        shape: BoxShape.circle,
      ),
      alignment: Alignment.center,
      child: Text(
        platform.name.isEmpty ? '?' : platform.name[0],
        style: TextStyle(
          fontSize: radius * 0.95,
          fontWeight: FontWeight.w900,
          color: SaudaColors.ink,
        ),
      ),
    );
  }
}

/// Section title row with an optional action.
class SectionHeader extends StatelessWidget {
  final String title;
  final Widget? action;

  const SectionHeader({super.key, required this.title, this.action});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 20, 4, 10),
      child: Row(
        children: [
          Text(
            title,
            style: const TextStyle(fontSize: 17, fontWeight: FontWeight.w800),
          ),
          const Spacer(),
          if (action != null) action!,
        ],
      ),
    );
  }
}

/// Renders an itemised true-total bill:
/// items → delivery → platform fee → packaging → GST → − coupon = you pay.
class BillLinesView extends StatelessWidget {
  final TrueTotalBill bill;

  const BillLinesView({super.key, required this.bill});

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Column(
      children: [
        for (final line in bill.lines) _row(context, line),
        if (bill.appliedCoupon != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Row(
              children: [
                const Icon(Icons.local_offer_outlined, size: 14),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    'Coupon ${bill.appliedCoupon} applied automatically',
                    style: TextStyle(
                      fontSize: 12,
                      color: scheme.onSurface.withValues(alpha: 0.65),
                    ),
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }

  Widget _row(BuildContext context, BillLine line) {
    final scheme = Theme.of(context).colorScheme;
    final bool discount = line.amount < 0 && !line.isTotal;
    final Color amountColor = line.isTotal
        ? scheme.primary
        : discount
            ? SaudaColors.emerald
            : scheme.onSurface;
    final Widget row = Padding(
      padding: const EdgeInsets.symmetric(vertical: 5),
      child: Row(
        children: [
          Expanded(
            child: Text(
              line.label,
              style: TextStyle(
                fontSize: line.isTotal ? 15 : 13.5,
                fontWeight: line.isTotal ? FontWeight.w800 : FontWeight.w400,
                color: scheme.onSurface.withValues(
                    alpha: line.isTotal ? 1 : 0.75),
              ),
            ),
          ),
          Text(
            line.isTotal
                ? inr(line.amount)
                : discount
                    ? '−${inr(line.amount.abs())}'
                    : inr(line.amount),
            style: tabularStyle.copyWith(
              fontSize: line.isTotal ? 16 : 13.5,
              fontWeight: line.isTotal ? FontWeight.w900 : FontWeight.w600,
              color: amountColor,
            ),
          ),
        ],
      ),
    );
    if (line.isTotal) {
      return Column(
        children: [
          const Divider(height: 16),
          row,
        ],
      );
    }
    return row;
  }
}

/// Friendly empty state with an action.
class EmptyState extends StatelessWidget {
  final IconData icon;
  final String title;
  final String subtitle;
  final String actionLabel;
  final VoidCallback onAction;

  const EmptyState({
    super.key,
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.actionLabel,
    required this.onAction,
  });

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 84,
              height: 84,
              decoration: BoxDecoration(
                color: scheme.primary.withValues(alpha: 0.12),
                shape: BoxShape.circle,
              ),
              child: Icon(icon, size: 38, color: scheme.primary),
            ),
            const SizedBox(height: 18),
            Text(
              title,
              style: const TextStyle(
                  fontSize: 18, fontWeight: FontWeight.w800),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 8),
            Text(
              subtitle,
              style: TextStyle(
                fontSize: 13.5,
                color: scheme.onSurface.withValues(alpha: 0.6),
              ),
              textAlign: TextAlign.center,
            ),
            const SizedBox(height: 20),
            FilledButton(onPressed: onAction, child: Text(actionLabel)),
          ],
        ),
      ),
    );
  }
}
