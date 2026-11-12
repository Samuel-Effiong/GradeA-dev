"""R2: every BetaProfile's tallies set to its wallet charges, once.

total_credits_used and the per-feature counters become the sum of the
teacher's CreditUsageLog rows with is_refunded false (raw unit, as before);
has_hit_80_percent / has_hit_cap are set or cleared from the corrected
total (owner, 2026-11-12: they always match real usage);
usage_velocity and conversion_probability are rescored. Profiles that
already match are not written. See billing/beta_tallies.py.

Reverse does nothing: the old tallies were the drift. No audit rows: these
are analytics counters, not credits or grades (docs/MIGRATIONS.md asks for
none).
"""

from django.db import migrations


def reconcile_all(apps, schema_editor):
    from billing.beta_tallies import reconcile

    BetaProfile = apps.get_model("billing", "BetaProfile")
    reconcile(
        BetaProfile.objects.all(),
        usage_log_model=apps.get_model("billing", "CreditUsageLog"),
        wallet_model=apps.get_model("billing", "CreditWallet"),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0074_h80_backfill_has_hit_80_percent"),
    ]

    operations = [
        migrations.RunPython(reconcile_all, migrations.RunPython.noop),
    ]
