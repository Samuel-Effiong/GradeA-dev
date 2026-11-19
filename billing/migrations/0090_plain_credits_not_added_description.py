from django.db import migrations

from billing.transaction_wording import rewrite_credits_not_added_description


def rewrite(apps, schema_editor):
    """Brief 4: rows written with the old staff wording get the plain text."""
    Transaction = apps.get_model("billing", "BillingTransaction")
    rows = Transaction.objects.filter(description__startswith="Overage purchase ")
    for txn in rows.only("pk", "description"):
        new = rewrite_credits_not_added_description(txn.description)
        if new:
            Transaction.objects.filter(pk=txn.pk).update(description=new)


def noop(apps, schema_editor):
    """The old text is not restored."""


class Migration(migrations.Migration):
    dependencies = [("billing", "0089_plain_backfill_descriptions")]

    operations = [migrations.RunPython(rewrite, noop)]
