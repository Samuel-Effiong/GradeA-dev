"""Epic B1a step 1 (plan 08 §3.2, §4): BackgroundProcessingTask's credit
hold - the job FK and the per-item hold columns.

Class: ADDITIVE (docs/MIGRATIONS.md §2).
- job: a new nullable FK to billing.AIJob. On Django 5.2 AddField of an FK
  would build its index inline and validate the constraint over every row
  while holding the lock. So the column and the constraint are RunSQL: ADD
  COLUMN (nullable, no default: a catalog change only), then ADD CONSTRAINT
  ... FOREIGN KEY ... NOT VALID (no existing row is read). 0038 validates it;
  0039 builds the index CONCURRENTLY. The state side is the model field with
  db_index=False.
- hold_state (nullable), hold_credits, hold_drawn, released_credits (int,
  default and db_default 0): plain additive columns.
SET LOCAL lock_timeout = '5s' bounds the wait for the ACCESS EXCLUSIVE catalog
lock; on timeout the deploy step fails cleanly and is run again.

To undo: reverse this migration (drops the constraint and the columns). The
previous code never reads them.
"""

import django.db.models.deletion
from django.db import migrations, models

TABLE = "students_backgroundprocessingtask"
FK = "students_bpt_job_id_fk_billing_aijob"
SET_LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"
ADD_COLUMN = f'ALTER TABLE "{TABLE}" ADD COLUMN "job_id" uuid NULL'
ADD_FK_NOT_VALID = (
    f'ALTER TABLE "{TABLE}" ADD CONSTRAINT "{FK}" '
    'FOREIGN KEY ("job_id") REFERENCES "billing_aijob" ("id") '
    "DEFERRABLE INITIALLY DEFERRED NOT VALID"
)
DROP_FK = f'ALTER TABLE "{TABLE}" DROP CONSTRAINT IF EXISTS "{FK}"'
DROP_COLUMN = f'ALTER TABLE "{TABLE}" DROP COLUMN IF EXISTS "job_id"'


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0076_validate_creditwallet_reserved_credits_check"),
        ("students", "0036_studentsubmission_pending_result"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=[SET_LOCK_TIMEOUT, ADD_COLUMN, ADD_FK_NOT_VALID],
                    reverse_sql=[SET_LOCK_TIMEOUT, DROP_FK, DROP_COLUMN],
                ),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="backgroundprocessingtask",
                    name="job",
                    field=models.ForeignKey(
                        blank=True,
                        db_index=False,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="items",
                        to="billing.aijob",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="backgroundprocessingtask",
            name="hold_state",
            field=models.CharField(
                blank=True,
                choices=[("HELD", "Credit held"), ("RETURNED", "Credit returned")],
                max_length=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="backgroundprocessingtask",
            name="hold_credits",
            field=models.IntegerField(
                db_default=0,
                default=0,
                help_text="Credits held for this item (held_per_item plus extensions).",
            ),
        ),
        migrations.AddField(
            model_name="backgroundprocessingtask",
            name="hold_drawn",
            field=models.IntegerField(
                db_default=0,
                default=0,
                help_text="Credits taken off wallet.reserved_credits by this item's settlement.",
            ),
        ),
        migrations.AddField(
            model_name="backgroundprocessingtask",
            name="released_credits",
            field=models.IntegerField(
                db_default=0,
                default=0,
                help_text="Credits returned to the wallet counter for this item.",
            ),
        ),
    ]
