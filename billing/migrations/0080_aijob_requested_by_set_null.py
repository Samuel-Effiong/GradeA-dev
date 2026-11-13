"""Epic B1a, NOTES §21v R2: AIJob.requested_by becomes SET_NULL.

An AIJob is a credit record (owner D1/D4), so it outlives the user who
requested it, as BillingTransaction.user does. Under CASCADE, erasing a student
deleted the jobs they started, including jobs billed to a teacher's wallet,
and their settlement records.

Class: ADDITIVE (docs/MIGRATIONS.md §2). The column only loses NOT NULL;
on_delete is Django-side, so the FK constraint itself is unchanged and no
existing row is read or rewritten.

Reversing it: refused while any AIJob has no requesting user
(refuse_reverse_with_requesterless_jobs), because restoring NOT NULL would
fail on those rows, or would need them deleted, which throws away credit
records.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def refuse_reverse_with_requesterless_jobs(apps, schema_editor):
    """NOTES §21v R2: the reverse restores NOT NULL, so it is allowed only
    when every AIJob still has its requesting user."""
    AIJob = apps.get_model("billing", "AIJob")
    orphaned = AIJob.objects.filter(requested_by__isnull=True).count()
    if orphaned:
        raise RuntimeError(
            f"Refusing to reverse billing 0080: {orphaned} AIJob rows have no "
            "requesting user."
        )


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0079_aijob_wallet_set_null"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="aijob",
            name="requested_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="ai_jobs",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # Reversed first on a rollback, before the AlterField restores NOT NULL.
        migrations.RunPython(
            migrations.RunPython.noop, refuse_reverse_with_requesterless_jobs
        ),
    ]
