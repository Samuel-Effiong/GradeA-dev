# Joins the B1b chain (0084) with the H-80 backfill (0074_h80) from beta.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0084_b1b6_deprecate_plan_queue_fields"),
        ("billing", "0074_h80_backfill_has_hit_80_percent"),
    ]

    operations = []
