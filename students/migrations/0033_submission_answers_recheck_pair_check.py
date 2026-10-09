"""AI-call record, slice 2: the one check constraint on the re-check's pair, step one of two.

The constraint says: the result column holds one of the five result words
("unlabelled", "not_run", "failed", "confirmed_blank", "found_writing"), and
"unlabelled" or "not_run" stands in the model column only beside the same
word in the result column.

This is step one: the constraint is added NOT VALID. PostgreSQL then checks
only rows that are written from now on and does NOT read the existing rows,
so there is no scan of students_studentsubmission. The statement takes an
ACCESS EXCLUSIVE lock on the table, but only for the moment it needs to
change the catalog (it first waits for the transactions that are using the
table to finish, as any ALTER TABLE does); how long that wait is on the live
system depends on what is running then, and is not measured. Step two,
0034, validates the existing rows under a lock that does not block reads or
writes. Every row that exists holds "unlabelled" in both columns after 0032,
so the validation cannot fail.

On deploy this migration runs first (a catalog change, no row is read), then
0034 (a read of every row of students_studentsubmission without blocking
reads or writes; its time grows with the row count and is not measured).
The model's CheckConstraint is the same condition (state operation); the SQL
below is what the database gets, and a test compares the two on every
combination of words.

To undo: reverse this migration (it drops only the constraint), or go back to
the previous version of the code and leave the constraint in place; the
previous code never writes either column. Reversing 0034 alone does nothing
(a validated constraint stays validated).
"""

from django.db import migrations, models

CONSTRAINT = "answers_recheck_pair_allowed"
TABLE = "students_studentsubmission"

ADD_NOT_VALID = (
    'ALTER TABLE "students_studentsubmission" '
    'ADD CONSTRAINT "answers_recheck_pair_allowed" CHECK ('
    '"answers_recheck_result" IN '
    "('unlabelled', 'not_run', 'failed', 'confirmed_blank', 'found_writing') "
    "AND ("
    "(\"answers_recheck_result\" = 'unlabelled' AND \"answers_recheck_model\" = 'unlabelled') "
    "OR (\"answers_recheck_result\" = 'not_run' AND \"answers_recheck_model\" = 'not_run') "
    "OR (NOT (\"answers_recheck_result\" IN ('unlabelled', 'not_run')) "
    "AND NOT (\"answers_recheck_model\" IN ('unlabelled', 'not_run')))"
    ")) NOT VALID"
)
DROP = 'ALTER TABLE "students_studentsubmission" DROP CONSTRAINT IF EXISTS "answers_recheck_pair_allowed"'


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0032_submission_answers_label"),
    ]

    operations = [
        migrations.RunSQL(
            sql=ADD_NOT_VALID,
            reverse_sql=DROP,
            state_operations=[
                migrations.AddConstraint(
                    model_name="studentsubmission",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            (
                                "answers_recheck_result__in",
                                [
                                    "unlabelled",
                                    "not_run",
                                    "failed",
                                    "confirmed_blank",
                                    "found_writing",
                                ],
                            ),
                            models.Q(
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
                                        (
                                            "answers_recheck_result__in",
                                            ["unlabelled", "not_run"],
                                        ),
                                        _negated=True,
                                    ),
                                    models.Q(
                                        (
                                            "answers_recheck_model__in",
                                            ["unlabelled", "not_run"],
                                        ),
                                        _negated=True,
                                    ),
                                ),
                                _connector="OR",
                            ),
                        ),
                        name=CONSTRAINT,
                    ),
                )
            ],
        ),
    ]
