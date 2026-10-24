"""
Backfill ai_grading_completed_at for rows written while it was a DateField.

After 0027 every historical value is a midnight timestamp (the date the
DateField kept, cast to TIMESTAMPTZ), which is not a completion time and
still makes `ai_grading_completed_at - ai_graded_at` negative for those
rows. The same save that wrote it (students.services._populate_and_save_grade)
sets `graded_at = timezone.now()` a few statements later, so graded_at IS
the completion instant to within milliseconds. Rows that have a completion
date but no graded_at were never successfully graded (the save that sets
both never ran to the end); their date-only value is set to NULL so the
dashboard average ignores them rather than counting a fake duration.

Data-only migration, separate from the 0027 schema change per
docs/MIGRATIONS.md. Idempotent: re-running it on already-backfilled rows
is a no-op, because a backfilled value equals graded_at already. Reverse
is a no-op: the original dates are recoverable from the backfilled value
(its date part) and 0027's reverse truncates to that anyway.
"""

from django.db import migrations
from django.db.models import F


def backfill_completed_at_from_graded_at(apps, schema_editor):
    StudentSubmission = apps.get_model("students", "StudentSubmission")

    StudentSubmission.objects.filter(
        ai_grading_completed_at__isnull=False, graded_at__isnull=False
    ).exclude(ai_grading_completed_at=F("graded_at")).update(
        ai_grading_completed_at=F("graded_at")
    )

    StudentSubmission.objects.filter(
        ai_grading_completed_at__isnull=False, graded_at__isnull=True
    ).update(ai_grading_completed_at=None)


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0027_alter_studentsubmission_ai_grading_completed_at"),
    ]

    operations = [
        migrations.RunPython(
            backfill_completed_at_from_graded_at, migrations.RunPython.noop
        ),
    ]
