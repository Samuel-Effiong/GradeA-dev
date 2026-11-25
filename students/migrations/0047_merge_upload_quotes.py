"""Join beta's upload quote tables (0046, which depends on 0044_item_dismissed
on beta and staging alike) with staging's excused-work merge (0045). No
operations: it only gives the graph one leaf."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0045_merge_excused_and_dismissed"),
        ("students", "0046_upload_quotes"),
    ]

    operations = []
