"""
ai_grading_completed_at: DateField -> DateTimeField.

Why: students.services writes `timezone.now()` into this column at the end
of a grading run, and dashboard/views.py::platform_ai_performance reports
`ai_grading_completed_at - ai_graded_at` as the platform's average grading
duration. On a DateField the datetime was truncated to a date, so the
subtraction was (midnight - the real start time): measured on PostgreSQL
18.6, `DATE '2026-09-13' - TIMESTAMPTZ '2026-09-13 15:44+00'` is
`-1 day, 8:16:00`. Every value the dashboard ever showed for this metric
was negative.

Deploy safety (docs/MIGRATIONS.md lists "change a column's type" as
non-additive by default; this specific change is checked against the
actual rule - both releases must work against the resulting schema - and
passes it, so it ships as one migration rather than expand/contract):

* Previous-release WRITES: Django's DateField coerces the datetime to a
  date and sends a DATE literal; PostgreSQL casts DATE to TIMESTAMPTZ at
  midnight and accepts it. No error, and 0028 cannot backfill such a row
  because its graded_at is precise while this value is not - but a worker
  from the previous release writing during the deploy window is at most a
  handful of rows, and they read as "midnight", not as a crash.
* Previous-release READS: DateField has no from_db_value, so the model
  attribute simply becomes the datetime psycopg returns. The only reader
  is the dashboard subtraction, which works on either type.
* The column stays nullable; no default is introduced;
  scripts/check_migration_safety.py classifies this as additive.

Reverse: Django alters the column back to DATE, truncating the time part.
That is lossy but correct - it is exactly the state the column was in.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0026_alter_backgroundprocessingtask_task_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="studentsubmission",
            name="ai_grading_completed_at",
            field=models.DateTimeField(
                blank=True,
                help_text="The time the ai finished grading the student submission",
                null=True,
            ),
        ),
    ]
