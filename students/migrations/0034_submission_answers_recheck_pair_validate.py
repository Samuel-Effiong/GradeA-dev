"""AI-call record, slice 2: the check constraint on the re-check's pair, step two of two.

Validates the constraint 0033 added NOT VALID: PostgreSQL reads every row of
students_studentsubmission once and checks it. VALIDATE CONSTRAINT takes a
SHARE UPDATE EXCLUSIVE lock, which does not block reads or writes of the table
(it blocks other schema changes on it and a concurrent VACUUM FULL-style
rewrite). It is a migration of its own so that it runs in a transaction of its
own, after the ACCESS EXCLUSIVE lock of 0033 has been released. Every row that
exists holds "unlabelled" in both columns after 0032, so it cannot fail. How
long it takes grows with the number of rows and is not measured.

No state operation: the model already holds the constraint (0033).

To undo: nothing to undo. Reversing this migration does nothing (a validated
constraint stays validated and still protects the table); reversing 0033
drops the constraint.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0033_submission_answers_recheck_pair_check"),
    ]

    operations = [
        migrations.RunSQL(
            sql='ALTER TABLE "students_studentsubmission" VALIDATE CONSTRAINT "answers_recheck_pair_allowed"',
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
