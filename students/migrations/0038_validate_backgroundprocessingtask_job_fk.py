"""Epic B1a step 1: validate the BackgroundProcessingTask.job FK, step two.

Class: ADDITIVE (docs/MIGRATIONS.md §2). VALIDATE CONSTRAINT reads every row
under SHARE UPDATE EXCLUSIVE (no block on reads or writes) and takes ROW
SHARE on billing_aijob. Every existing row has job_id NULL, so it cannot
fail.

Reversing it does nothing (a validated constraint stays validated).
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0037_backgroundprocessingtask_credit_hold"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                'ALTER TABLE "students_backgroundprocessingtask" '
                'VALIDATE CONSTRAINT "students_bpt_job_id_fk_billing_aijob"'
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
