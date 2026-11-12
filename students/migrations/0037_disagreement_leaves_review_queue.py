"""
R1 (owner, 2026-11-12): the second AI's disagreement no longer puts a
submission in the teacher's review queue. One-time cleanup of the rows it
put there.

- In the queue ONLY because of the second AI (every reason that is not a
  teacher's resolution is grader_disagreement or second_opinion_unavailable):
  taken out - needs_review False, review_severity and review_tier None.
- Still in the queue for another reason (an upload flag, an unreadable
  paper, an answer not found, a corrected AI reply): stays, and its tier and
  severity are worked out again from those reasons alone, by the code
  grading uses (students/review_state.py).

Grades and feedback are not touched, and review_reasons is kept as it is:
the second AI's entries stay on record. A queryset update: no audit rows,
no signals. Reverse: nothing to undo.
"""

from django.db import migrations

from students.review_state import DISAGREEMENT_REASON_TYPES, review_state


def leave_queue(apps, schema_editor):
    StudentSubmission = apps.get_model("students", "StudentSubmission")
    flagged = StudentSubmission.objects.filter(needs_review=True).only(
        "id", "review_reasons", "review_severity", "review_tier"
    )
    for row in flagged.iterator():
        reasons = row.review_reasons
        if not isinstance(reasons, list) or not reasons:
            # Flagged with no reason on record: nothing says it was the
            # second AI, so it is left alone.
            continue
        filed = [
            reason
            for reason in reasons
            if not (isinstance(reason, dict) and reason.get("resolved"))
        ]
        if filed and all(
            isinstance(reason, dict) and reason.get("type") in DISAGREEMENT_REASON_TYPES
            for reason in filed
        ):
            StudentSubmission.objects.filter(pk=row.pk).update(
                needs_review=False, review_severity=None, review_tier=None
            )
            continue
        severity, tier = review_state(reasons)
        if tier is None:
            # Nothing this code ranks (an entry of a shape it does not
            # know): left as it is.
            continue
        if (severity, tier) != (row.review_severity, row.review_tier):
            StudentSubmission.objects.filter(pk=row.pk).update(
                review_severity=severity, review_tier=tier
            )


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0036_studentsubmission_pending_result"),
    ]

    operations = [
        migrations.RunPython(leave_queue, migrations.RunPython.noop),
    ]
