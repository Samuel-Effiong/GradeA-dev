"""H-80: a BetaProfile that reached 100% of its allocation also reached 80%.

record_consumption set has_hit_80_percent only for 80-99%, so a teacher whose
single use jumped past 100% has has_hit_cap=True but has_hit_80_percent=False.
This sets has_hit_80_percent=True on exactly those rows. It is additive and
idempotent; reversing it does nothing (the flag is an "ever reached" signal,
and the fixed code would set it again).
"""

from django.db import migrations


def backfill_hit_80_percent(apps, schema_editor):
    BetaProfile = apps.get_model("billing", "BetaProfile")
    BetaProfile.objects.filter(has_hit_cap=True, has_hit_80_percent=False).update(
        has_hit_80_percent=True
    )


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0073_schoolcreditallocation_grant_anchor_at"),
    ]

    operations = [
        migrations.RunPython(backfill_hit_80_percent, migrations.RunPython.noop),
    ]
