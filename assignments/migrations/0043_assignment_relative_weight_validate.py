"""Epic E2: step two of two for `assignment_relative_weight_positive` (0042
added it NOT VALID). VALIDATE CONSTRAINT reads every existing row under a
SHARE UPDATE EXCLUSIVE lock, which blocks neither reads nor writes. Every
existing row holds the default 1.00, so it cannot fail. Reversing this
migration does nothing (a validated constraint stays validated).
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("assignments", "0042_assignment_category_relative_weight"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                'ALTER TABLE "assignments_assignment" '
                'VALIDATE CONSTRAINT "assignment_relative_weight_positive"'
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
