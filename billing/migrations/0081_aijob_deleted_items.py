"""Epic B1b step 1, FB-1 (NOTES §1.1): what deleted task rows held, drew
and released, kept on their job. Step one of two.

Class: ADDITIVE (docs/MIGRATIONS.md §2).
- Three integer columns with db_default=0: PostgreSQL 11+ adds a column
  with a constant default without rewriting the table, and the old code
  never names them, so a rollback of the code alone is safe.
- The check that the three are never negative is added NOT VALID, after
  SET LOCAL lock_timeout = '5s' (this migration's transaction only), the
  house way (students 0033/0034, docs/MIGRATIONS.md:95). ADD CONSTRAINT
  takes an ACCESS EXCLUSIVE lock only for the catalog change; NOT VALID
  reads no existing row. With the timeout, a busy table makes the deploy
  step fail cleanly instead of stalling the table. 0082 validates the
  existing rows under a lock that blocks neither reads nor writes. Every
  existing row holds 0 in all three (db_default), so it cannot fail.

To undo: reverse this migration. It drops the constraint, then the
columns. Nothing is lost that the live task rows and the ledger don't keep
(NOTES §1.1); jobs that lost rows go back to today's item_sum report.
"""

from django.db import migrations, models

SET_LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"
ADD_NOT_VALID = (
    'ALTER TABLE "billing_aijob" '
    'ADD CONSTRAINT "aijob_deleted_items_nonneg" CHECK ('
    '"deleted_items_held" >= 0 AND "deleted_items_drawn" >= 0 '
    'AND "deleted_items_released" >= 0) NOT VALID'
)
DROP = (
    'ALTER TABLE "billing_aijob" '
    'DROP CONSTRAINT IF EXISTS "aijob_deleted_items_nonneg"'
)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0080_aijob_requested_by_set_null"),
    ]

    operations = [
        migrations.AddField(
            model_name="aijob",
            name="deleted_items_held",
            field=models.IntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name="aijob",
            name="deleted_items_drawn",
            field=models.IntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name="aijob",
            name="deleted_items_released",
            field=models.IntegerField(db_default=0, default=0),
        ),
        migrations.RunSQL(
            sql=[SET_LOCK_TIMEOUT, ADD_NOT_VALID],
            reverse_sql=[SET_LOCK_TIMEOUT, DROP],
            state_operations=[
                migrations.AddConstraint(
                    model_name="aijob",
                    constraint=models.CheckConstraint(
                        condition=models.Q(
                            ("deleted_items_held__gte", 0),
                            ("deleted_items_drawn__gte", 0),
                            ("deleted_items_released__gte", 0),
                        ),
                        name="aijob_deleted_items_nonneg",
                    ),
                ),
            ],
        ),
    ]
