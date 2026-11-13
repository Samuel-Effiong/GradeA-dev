"""Epic B1a step 2 (SM ruling 2026-11-06): the stale-hold sweep margin as an
editable setting, BillingSettings.hold_sweep_margin_minutes (default 15).

Class: ADDITIVE (docs/MIGRATIONS.md §2): a new column with a real default and
db_default=15 on a table created in 0074 (not yet released).

To undo: reverse this migration (drops the column; the previous code never
reads it).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0076_validate_creditwallet_reserved_credits_check"),
    ]

    operations = [
        migrations.AddField(
            model_name="billingsettings",
            name="hold_sweep_margin_minutes",
            field=models.PositiveSmallIntegerField(
                db_default=15,
                default=15,
                help_text="Minutes past the redelivery window (and past the time limit for jobs with no task row) before a stale credit hold is returned.",
            ),
        ),
    ]
