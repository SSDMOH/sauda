/// INR formatting helpers with Indian digit grouping (lakh / crore).

/// Formats [amount] as INR, e.g. 124500 -> "₹1,24,500".
String inr(num amount, {bool showPaise = false}) {
  final bool negative = amount < 0;
  final double abs = amount.abs().toDouble();
  final int rupees = abs.truncate();
  final String digits = rupees.toString();

  String grouped;
  if (digits.length <= 3) {
    grouped = digits;
  } else {
    final String lastThree = digits.substring(digits.length - 3);
    String rest = digits.substring(0, digits.length - 3);
    final List<String> parts = [];
    while (rest.length > 2) {
      parts.insert(0, rest.substring(rest.length - 2));
      rest = rest.substring(0, rest.length - 2);
    }
    if (rest.isNotEmpty) parts.insert(0, rest);
    grouped = '${parts.join(',')},$lastThree';
  }

  String out = '₹$grouped';
  if (showPaise) {
    final int paise = ((abs - rupees) * 100).round();
    out += '.${paise.toString().padLeft(2, '0')}';
  }
  return negative ? '-$out' : out;
}

/// Compact form for hero numbers, e.g. 4280 -> "₹4.3K".
String inrCompact(num amount) {
  final double a = amount.abs().toDouble();
  if (a >= 10000000) {
    return '₹${(amount / 10000000).toStringAsFixed(1)} Cr';
  }
  if (a >= 100000) {
    return '₹${(amount / 100000).toStringAsFixed(1)} L';
  }
  if (a >= 1000) {
    return '₹${(amount / 1000).toStringAsFixed(1)}K';
  }
  return inr(amount);
}

/// Signed savings delta, e.g. -186.5 -> "-₹187".
String inrSigned(num amount) {
  if (amount >= 0) return '+${inr(amount)}';
  return '-${inr(amount.abs())}';
}

String etaLabel(int minutes) => '$minutes min';

const List<String> _months = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

String dateLabel(DateTime d) => '${d.day} ${_months[d.month - 1]}';

String timeAgo(DateTime d) {
  final diff = DateTime.now().difference(d);
  if (diff.inMinutes < 60) return '${diff.inMinutes}m ago';
  if (diff.inHours < 24) return '${diff.inHours}h ago';
  return '${diff.inDays}d ago';
}
