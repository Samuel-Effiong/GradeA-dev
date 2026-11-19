from django.db import migrations

from billing.transaction_wording import rewrite_backfilled_description


def rewrite(apps, schema_editor):
    """S18 (row 138): old backfilled rows get the plain text the command now writes."""
    Transaction = apps.get_model("billing", "BillingTransaction")
    rows = Transaction.objects.filter(description__startswith="Backfilled from ")
    for txn in rows.select_related(
        "user_subscription__plan", "license_subscription__plan"
    ):
        sub = txn.license_subscription or txn.user_subscription
        plan = sub.plan if sub is not None else None
        new = rewrite_backfilled_description(
            txn.description,
            plan_name=(plan.display_name or plan.name) if plan else None,
            license=txn.license_subscription_id is not None,
        )
        if new:
            Transaction.objects.filter(pk=txn.pk).update(description=new)


def noop(apps, schema_editor):
    """The old text is not restored."""


class Migration(migrations.Migration):
    dependencies = [("billing", "0088_remove_creditrequest_ai_job")]

    operations = [migrations.RunPython(rewrite, noop)]
