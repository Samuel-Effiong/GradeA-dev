"""Epic B1b step 1, FB-1: the check on AIJob's deleted_items_* columns,
step two of two.

Validates the constraint 0081 added NOT VALID: PostgreSQL reads every row of
billing_aijob once and checks it. VALIDATE CONSTRAINT takes a SHARE UPDATE
EXCLUSIVE lock, which does not block reads or writes of the table. It is a
migration of its own so that it runs in a transaction of its own, after the
ACCESS EXCLUSIVE lock of 0081 has been released (the house way, students
0033/0034). Every existing row holds 0 in all three columns (0081's
db_default), so it cannot fail.

No state operation: the model already holds the constraint (0081).

To undo: nothing to undo. Reversing this migration does nothing (a
validated constraint stays validated); reversing 0081 drops the constraint
and the columns.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0081_aijob_deleted_items"),
    ]

    operations = [
        migrations.RunSQL(
            sql='ALTER TABLE "billing_aijob" VALIDATE CONSTRAINT "aijob_deleted_items_nonneg"',
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
