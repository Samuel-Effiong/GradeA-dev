"""
Run the nightly Stripe price reconciliation by hand.

Exists for rollout steps 2 and 3 — running the sweep in a non-production
environment, then against the real account, before the schedule is ever
enabled. Also the way to check a suspicion without waiting for 02:30.

Detection only. This command, like the task it wraps, writes nothing to
Stripe and no billing state. There is deliberately no `--fix`: correcting
a mismatch is a money decision, and the existing
`manage.py reconcile_overage_prices --fix` remains the one deliberate,
human-operated way to do it.
"""

from django.core.management.base import BaseCommand

from billing.models import PRICE_RECONCILIATION_ALERT_STATUSES
from billing.price_reconciliation import reconcile_prices


class Command(BaseCommand):
    help = "Check every active plan's Stripe prices against local expectations."

    def add_arguments(self, parser):
        parser.add_argument(
            "--plan",
            dest="plans",
            action="append",
            default=None,
            help="Limit to this plan name. Repeatable.",
        )

    def handle(self, *args, **options):
        from billing.models import SubscriptionPlan

        queryset = SubscriptionPlan.objects.filter(is_active=True).order_by("name")
        if options["plans"]:
            queryset = queryset.filter(name__in=options["plans"])

        run = reconcile_prices(list(queryset))

        self.stdout.write(f"run      {run.id}")
        self.stdout.write(f"account  {run.stripe_account_id or '(unidentified)'}")
        self.stdout.write(f"api      {run.stripe_api_version or '(library default)'}")
        self.stdout.write("")

        header = (
            f"{'plan':<22}{'kind':<9}{'expected':>9}{'stripe':>8}  "
            f"{'status':<20}price id"
        )
        self.stdout.write(header)
        self.stdout.write("-" * len(header))

        for result in run.results.all():
            expected = "-" if result.expected_amount is None else result.expected_amount
            actual = "-" if result.stripe_amount is None else result.stripe_amount
            line = (
                f"{result.plan_name:<22}{result.price_kind:<9}"
                f"{str(expected):>9}{str(actual):>8}  "
                f"{result.status:<20}{result.price_id or '-'}"
            )
            if result.status in PRICE_RECONCILIATION_ALERT_STATUSES:
                self.stdout.write(self.style.ERROR(line))
            elif result.status == "STRIPE_UNAVAILABLE":
                self.stdout.write(self.style.WARNING(line))
            else:
                self.stdout.write(line)
            if result.mismatched_fields:
                self.stdout.write(
                    f"    mismatched: {', '.join(result.mismatched_fields)}"
                )
            if result.error_message:
                self.stdout.write(f"    {result.error_message}")

        self.stdout.write("")
        style = self.style.ERROR if run.needs_attention else self.style.SUCCESS
        self.stdout.write(style(run.summary))
