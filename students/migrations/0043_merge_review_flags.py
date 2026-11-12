from django.db import migrations


class Migration(migrations.Migration):
    """Joins beta's review-flags cleanup (0037) with staging's 0042."""

    dependencies = [
        ("students", "0037_disagreement_leaves_review_queue"),
        ("students", "0042_keptupload_purge_reason"),
    ]

    operations = []
