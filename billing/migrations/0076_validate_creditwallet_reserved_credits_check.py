"""Epic B1a step 1: validate CHECK (reserved_credits >= 0), step two of two.

Class: ADDITIVE (docs/MIGRATIONS.md §2). VALIDATE CONSTRAINT reads every row
of billing_creditwallet under SHARE UPDATE EXCLUSIVE, which does not block
reads or writes. Every existing row holds 0, so it cannot fail.

Reversing it does nothing (a validated constraint stays validated); reverse
0075 to drop the constraint.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0075_creditwallet_reserved_credits_check"),
    ]

    operations = [
        migrations.RunSQL(
            sql=(
                'ALTER TABLE "billing_creditwallet" '
                'VALIDATE CONSTRAINT "creditwallet_reserved_credits_nonneg"'
            ),
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
