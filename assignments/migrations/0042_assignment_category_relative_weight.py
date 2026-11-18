"""Epic E2: an assignment's grade category and relative weight (FR-E-03/05),
and step one of two for the relative weight's check (it must be above 0).

Both columns are additive: `category` is nullable (SET_NULL), and
`relative_weight` has a database default of 1.00, so code older than this
migration can still insert an assignment (H-56) and every existing row
counts exactly as before.

The check is added NOT VALID, after SET LOCAL lock_timeout = '5s' (this
migration's transaction only): PostgreSQL then checks only rows written from
now on and does not read the existing rows, so there is no scan of
assignments_assignment; the ACCESS EXCLUSIVE lock lasts only for the catalog
change, and gives up after five seconds instead of stalling the table (the
deploy step then fails cleanly and is run again). Step two, 0043, validates
the existing rows without blocking reads or writes. Every existing row holds
the default 1.00, so the validation cannot fail. The pattern is students
0033/0034.

To undo: reverse this migration (it drops the check and both columns), or
roll the code back and leave them in place; older code never writes them.
"""

from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models

SET_LOCK_TIMEOUT = "SET LOCAL lock_timeout = '5s'"
ADD_NOT_VALID = (
    'ALTER TABLE "assignments_assignment" '
    'ADD CONSTRAINT "assignment_relative_weight_positive" '
    'CHECK ("relative_weight" > 0) NOT VALID'
)
DROP = (
    'ALTER TABLE "assignments_assignment" '
    'DROP CONSTRAINT IF EXISTS "assignment_relative_weight_positive"'
)


class Migration(migrations.Migration):

    dependencies = [
        ("assignments", "0041_topic_set_null"),
        ("classrooms", "0019_grade_categories"),
    ]

    operations = [
        migrations.AddField(
            model_name="assignment",
            name="category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="assignments",
                to="classrooms.coursecategory",
            ),
        ),
        migrations.AddField(
            model_name="assignment",
            name="relative_weight",
            field=models.DecimalField(
                db_default=Decimal("1.00"),
                decimal_places=2,
                default=Decimal("1.00"),
                max_digits=6,
            ),
        ),
        migrations.RunSQL(
            sql=[SET_LOCK_TIMEOUT, ADD_NOT_VALID],
            reverse_sql=[SET_LOCK_TIMEOUT, DROP],
            state_operations=[
                migrations.AddConstraint(
                    model_name="assignment",
                    constraint=models.CheckConstraint(
                        condition=models.Q(("relative_weight__gt", 0)),
                        name="assignment_relative_weight_positive",
                    ),
                ),
            ],
        ),
    ]
