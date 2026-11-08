"""Epic B1a step 9, finding A (NOTES §21n): AIJob.wallet becomes SET_NULL.

Class: ADDITIVE (docs/MIGRATIONS.md §2). The column only loses NOT NULL;
on_delete is Django-side, so the FK constraint itself is unchanged and no
existing row is read or rewritten.

Reversing it: refused while any AIJob has no wallet
(refuse_reverse_with_walletless_jobs), because restoring NOT NULL would fail
on those rows, or would need them deleted, which throws away job history.
"""

import django.db.models.deletion
from django.db import migrations, models


def refuse_reverse_with_walletless_jobs(apps, schema_editor):
    """NOTES §21n: the reverse restores NOT NULL, so it is allowed only when
    every AIJob still has its wallet."""
    AIJob = apps.get_model("billing", "AIJob")
    orphaned = AIJob.objects.filter(wallet__isnull=True).count()
    if orphaned:
        raise RuntimeError(
            f"Refusing to reverse billing 0079: {orphaned} AIJob rows have no "
            "wallet."
        )


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0078_alter_licensebillingrecord_record_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="aijob",
            name="wallet",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ai_jobs",
                to="billing.creditwallet",
            ),
        ),
        # Reversed first on a rollback, before the AlterField restores NOT NULL.
        migrations.RunPython(
            migrations.RunPython.noop, refuse_reverse_with_walletless_jobs
        ),
    ]
