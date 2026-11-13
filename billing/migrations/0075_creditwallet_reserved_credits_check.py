"""Epic B1a step 1 (plan 08 §3.2, §4): CHECK (reserved_credits >= 0) on
billing_creditwallet, step one of two.

Class: ADDITIVE (docs/MIGRATIONS.md §2: a constraint in two steps, RunSQL
... NOT VALID, then VALIDATE CONSTRAINT in the next migration; precedent
students/0033, 0034).

The constraint is added NOT VALID after SET LOCAL lock_timeout = '5s'. ADD
CONSTRAINT takes an ACCESS EXCLUSIVE lock only to change the catalog; with
the timeout it gives up after five seconds instead of stalling the table, the
deploy step fails cleanly and is run again. NOT VALID checks only rows
written from now on, so no existing row is read. 0076 validates the existing
rows under a lock that does not block reads or writes. Every existing row
holds reserved_credits = 0 (0074's db_default), so validation cannot fail.

The model's CheckConstraint is the same condition (state operation).

To undo: reverse this migration (it drops only the constraint).
"""

from django.db import migrations, models

SET_LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"
ADD_NOT_VALID = (
    'ALTER TABLE "billing_creditwallet" '
    'ADD CONSTRAINT "creditwallet_reserved_credits_nonneg" '
    'CHECK ("reserved_credits" >= 0) NOT VALID'
)
DROP = (
    'ALTER TABLE "billing_creditwallet" '
    'DROP CONSTRAINT IF EXISTS "creditwallet_reserved_credits_nonneg"'
)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0074_b1a_credit_hold_schema"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[SET_LOCK_TIMEOUT, ADD_NOT_VALID],
            reverse_sql=[SET_LOCK_TIMEOUT, DROP],
            state_operations=[
                migrations.AddConstraint(
                    model_name="creditwallet",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("reserved_credits__gte", 0)),
                        name="creditwallet_reserved_credits_nonneg",
                    ),
                ),
            ],
        ),
    ]
