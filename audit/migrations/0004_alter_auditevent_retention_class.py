"""Epic B1a step 1 (plan 08 §4): RetentionClass gains FINANCIAL (7 years).

Class: ADDITIVE (docs/MIGRATIONS.md §2). Choices only: no schema change
(max_length 16 is unchanged; "FINANCIAL" fits), no retag of existing rows
(AuditEvent is append-only). Old code never writes FINANCIAL.

To undo: reverse this migration (state only). Rows already stored FINANCIAL
stay, and the old sweep never deletes them (kept, not lost).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0003_auditevent_target_time_ix"),
    ]

    operations = [
        migrations.AlterField(
            model_name="auditevent",
            name="retention_class",
            field=models.CharField(
                choices=[
                    ("GENERAL", "General (12 months)"),
                    ("STUDENT_RECORD", "Student record (3 years)"),
                    ("FINANCIAL", "Financial (7 years)"),
                ],
                max_length=16,
            ),
        ),
    ]
