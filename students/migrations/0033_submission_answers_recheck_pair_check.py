"""AI-call record, slice 2: the one check constraint on the re-check's pair.

Refuses any row where "unlabelled" or "not_run" stands in one of the two
re-check columns and not the same word in the other. Every row that exists
holds "unlabelled" in both columns after 0032, so the constraint is true
for all of them; adding it validates the table once (a read of every row,
under a brief lock). Its own migration so that it can be rolled forward or
back alone.

To undo: reverse this migration (it drops only the constraint), or go back
to the previous version of the code and leave it in place; the previous
code never writes either column.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0032_submission_answers_label"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="studentsubmission",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("answers_recheck_result", "unlabelled"),
                        ("answers_recheck_model", "unlabelled"),
                    ),
                    models.Q(
                        ("answers_recheck_result", "not_run"),
                        ("answers_recheck_model", "not_run"),
                    ),
                    models.Q(
                        models.Q(
                            ("answers_recheck_result__in", ["unlabelled", "not_run"]),
                            _negated=True,
                        ),
                        models.Q(
                            ("answers_recheck_model__in", ["unlabelled", "not_run"]),
                            _negated=True,
                        ),
                    ),
                    _connector="OR",
                ),
                name="answers_recheck_pair_allowed",
            ),
        ),
    ]
