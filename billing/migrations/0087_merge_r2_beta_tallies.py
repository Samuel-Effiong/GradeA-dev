from django.db import migrations


class Migration(migrations.Migration):
    """Joins beta's R2 tally correction (0075) with staging's 0086."""

    dependencies = [
        ("billing", "0075_r2_beta_tallies_from_wallet"),
        ("billing", "0086_b1c_credit_requests"),
    ]

    operations = []
