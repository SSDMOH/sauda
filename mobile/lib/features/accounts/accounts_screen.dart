import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../core/app_state.dart';
import '../../core/models.dart';
import '../../core/theme.dart';
import '../../core/widgets.dart';

/// Linked accounts: per-platform link cards with a mock OTP-link flow.
/// Production: tokens go to flutter_secure_storage (Keystore/Keychain),
/// never raw passwords, one-tap unlink deletes them everywhere.
class AccountsScreen extends StatelessWidget {
  const AccountsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Linked accounts',
          style: TextStyle(fontWeight: FontWeight.w900),
        ),
      ),
      body: Consumer<AppState>(
        builder: (context, state, _) {
          if (state.platforms.isEmpty) {
            return const Center(child: CircularProgressIndicator());
          }
          return ListView(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 24),
            children: [
              _privacyNote(context),
              const SizedBox(height: 12),
              for (final p in state.platforms)
                _linkCard(context, state, p),
            ],
          );
        },
      ),
    );
  }

  Widget _privacyNote(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: SaudaColors.emerald.withValues(alpha: 0.1),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Row(
        children: [
          const Icon(Icons.lock_outline_rounded,
              color: SaudaColors.emerald, size: 20),
          const SizedBox(width: 10),
          Expanded(
            child: Text(
              'Linking lets Sauda fetch your real prices, coupons and wallet balance. Tokens stay encrypted on your phone — unlink anytime.',
              style: TextStyle(
                fontSize: 12.5,
                color: scheme.onSurface.withValues(alpha: 0.75),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _linkCard(BuildContext context, AppState state, PlatformInfo p) {
    final account = state.accounts.firstWhere(
      (a) => a.platformId == p.id,
      orElse: () => LinkedAccount(platformId: p.id, linked: false),
    );
    return Container(
      margin: const EdgeInsets.only(bottom: 12),
      decoration: saudaCard(context, radius: 18),
      padding: const EdgeInsets.all(16),
      child: Row(
        children: [
          PlatformAvatar(platform: p, radius: 24),
          const SizedBox(width: 14),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  p.name,
                  style: const TextStyle(
                      fontWeight: FontWeight.w800, fontSize: 16),
                ),
                Text(
                  p.tagline,
                  style: TextStyle(
                    fontSize: 12,
                    color: Theme.of(context)
                        .colorScheme
                        .onSurface
                        .withValues(alpha: 0.6),
                  ),
                ),
                if (account.linked && account.phoneMasked != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 4),
                    child: Row(
                      children: [
                        const Icon(Icons.check_circle_rounded,
                            size: 14, color: SaudaColors.emerald),
                        const SizedBox(width: 4),
                        Text(
                          'Linked · ${account.phoneMasked}',
                          style: const TextStyle(
                            fontSize: 12,
                            color: SaudaColors.emerald,
                            fontWeight: FontWeight.w700,
                          ),
                        ),
                      ],
                    ),
                  ),
              ],
            ),
          ),
          account.linked
              ? TextButton(
                  onPressed: () async {
                    await state.api.unlink(platformId: p.id);
                    await state.refreshAccounts();
                    if (context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(
                            content: Text(
                                '${p.name} unlinked — tokens deleted.')),
                      );
                    }
                  },
                  child: const Text('Unlink'),
                )
              : FilledButton(
                  onPressed: () => _showOtpSheet(context, state, p),
                  style: FilledButton.styleFrom(
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(12),
                    ),
                  ),
                  child: const Text('Link'),
                ),
        ],
      ),
    );
  }

  void _showOtpSheet(
      BuildContext context, AppState state, PlatformInfo platform) {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(24)),
      ),
      builder: (_) => _OtpSheet(state: state, platform: platform),
    );
  }
}

class _OtpSheet extends StatefulWidget {
  final AppState state;
  final PlatformInfo platform;

  const _OtpSheet({required this.state, required this.platform});

  @override
  State<_OtpSheet> createState() => _OtpSheetState();
}

class _OtpSheetState extends State<_OtpSheet> {
  final _phoneCtrl = TextEditingController();
  final _otpCtrl = TextEditingController();
  String? _linkRef;
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _phoneCtrl.dispose();
    _otpCtrl.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
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
              PlatformAvatar(platform: widget.platform, radius: 20),
              const SizedBox(width: 12),
              Expanded(
                child: Text(
                  _linkRef == null
                      ? 'Link ${widget.platform.name}'
                      : 'Enter OTP',
                  style: const TextStyle(
                      fontSize: 18, fontWeight: FontWeight.w800),
                ),
              ),
              IconButton(
                icon: const Icon(Icons.close_rounded),
                onPressed: () => Navigator.pop(context),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            _linkRef == null
                ? 'We\u2019ll send a one-time password to verify it\u2019s you.'
                : widget.state.liveBackend
                    ? 'Sent to +91 ${_phoneCtrl.text}. Demo backend: any 6 digits work.'
                    : 'Sent to +91 ${_phoneCtrl.text}. Demo build: any 4 digits work.',
            style: TextStyle(
              fontSize: 13,
              color: Theme.of(context)
                  .colorScheme
                  .onSurface
                  .withValues(alpha: 0.65),
            ),
          ),
          const SizedBox(height: 16),
          if (_linkRef == null)
            TextField(
              controller: _phoneCtrl,
              keyboardType: TextInputType.phone,
              maxLength: 10,
              inputFormatters: [FilteringTextInputFormatter.digitsOnly],
              decoration: const InputDecoration(
                labelText: 'Mobile number',
                prefixText: '+91 ',
                counterText: '',
              ),
            )
          else
            TextField(
              controller: _otpCtrl,
              keyboardType: TextInputType.number,
              maxLength: widget.state.liveBackend ? 6 : 4,
              inputFormatters: [FilteringTextInputFormatter.digitsOnly],
              autofocus: true,
              style: tabularStyle.copyWith(
                fontSize: 28,
                fontWeight: FontWeight.w900,
                letterSpacing: 12,
              ),
              textAlign: TextAlign.center,
              decoration: const InputDecoration(counterText: ''),
            ),
          if (_error != null) ...[
            const SizedBox(height: 8),
            Text(_error!,
                style: const TextStyle(color: Colors.red, fontSize: 12.5)),
          ],
          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: FilledButton(
              onPressed: _busy ? null : _submit,
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
                  : Text(_linkRef == null ? 'Send OTP' : 'Verify & link'),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      if (_linkRef == null) {
        if (_phoneCtrl.text.length != 10) {
          throw Exception('Enter a valid 10-digit mobile number');
        }
        final ref = await widget.state.api.startLink(
          platformId: widget.platform.id,
          phone: _phoneCtrl.text,
        );
        setState(() => _linkRef = ref);
      } else {
        await widget.state.api.verifyOtp(
          linkRef: _linkRef!,
          otp: _otpCtrl.text,
        );
        await widget.state.refreshAccounts();
        if (mounted) {
          Navigator.pop(context);
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
                content: Text(
                    '${widget.platform.name} linked — live prices unlocked.')),
          );
        }
      }
    } catch (e) {
      setState(() => _error = e.toString().replaceFirst('Exception: ', ''));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }
}
