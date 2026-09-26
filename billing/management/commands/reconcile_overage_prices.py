"""
Report — and on request correct — plans whose quoted overage price
disagrees with the Stripe price that actually charges.

Read-only by default. `--fix` aligns the local column TO STRIPE, which
changes nobody's actual charge: Stripe was already charging its own price.
It only makes the number we quote, display and record truthful.

Deliberately a command rather than a data migration. A migration would run
blind against every database it lands on, including ones this code has
never inspected, and this is a money-facing column. Someone should look at
the table below before changing it.
"""

from django.core.management.base import BaseCommand

from billing.overage_pricing import overage_price_drift


class Command(BaseCommand):
    help = "Report (and optionally fix) overage price drift against Stripe."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fix",
            action="store_true",
            help=(
                "Set each drifted plan's overage_block_price to the Stripe "
                "price it already charges. Does not change what any "
                "customer pays."
            ),
        )
        parser.add_argument(
            "--plan",
            dest="plans",
            action="append",
            default=None,
            help="Limit to this plan name. Repeatable.",
        )

    def handle(self, *args, **options):
        from billing.models import SubscriptionPlan

        queryset = (
            SubscriptionPlan.objects.filter(is_active=True)
            .exclude(stripe_overage_price_id="")
            .exclude(stripe_overage_price_id__isnull=True)
            .order_by("name")
        )
        if options["plans"]:
            queryset = queryset.filter(name__in=options["plans"])

        report = overage_price_drift(list(queryset))
        if not report:
            self.stdout.write("No active plans with a Stripe overage price.")
            return

        self.stdout.write(
            f"{'plan':<22}{'quoted':>8}{'charged':>9}  {'':<3}stripe price"
        )
        self.stdout.write("-" * 78)
        for row in report:
            if row["error"]:
                mark = "ERR"
                charged = "?"
            elif row["in_sync"]:
                mark = "ok "
                charged = str(row["stripe_cents"])
            else:
                mark = "!! "
                charged = str(row["stripe_cents"])
            self.stdout.write(
                f"{row['name']:<22}{row['local_cents']:>8}{charged:>9}  "
                f"{mark}{row['stripe_price_id']}"
            )
            if row["error"]:
                self.stdout.write(f"    {row['error']}")

        drifted = [r for r in report if not r["in_sync"] and not r["error"]]
        unreadable = [r for r in report if r["error"]]

        # An unreadable price is NOT a passing price. Saying "every plan is
        # in sync" while Stripe was unreachable for some of them turns an
        # outage into a clean bill of health — which is exactly the kind of
        # false reassurance this command exists to prevent.
        if unreadable:
            self.stdout.write(
                self.style.ERROR(
                    f"\n{len(unreadable)} plan(s) could not be checked "
                    f"against Stripe — their status is UNKNOWN, not "
                    f"verified. Re-run once Stripe is reachable."
                )
            )

        if not drifted:
            if unreadable:
                self.stdout.write(
                    f"Of the {len(report) - len(unreadable)} plan(s) that "
                    f"could be checked, all are in sync."
                )
            else:
                self.stdout.write(self.style.SUCCESS("\nEvery plan is in sync."))
            return

        self.stdout.write(
            self.style.ERROR(
                f"\n{len(drifted)} plan(s) quote a price Stripe does not "
                f"charge. Overage purchases on these plans are REFUSED "
                f"until they agree."
            )
        )

        if not options["fix"]:
            self.stdout.write("\nRe-run with --fix to align them to Stripe.")
            return

        for row in drifted:
            plan = row["plan"]
            before = plan.overage_block_price
            plan.overage_block_price = row["stripe_cents"]
            plan.save(update_fields=["overage_block_price"])
            self.stdout.write(
                self.style.WARNING(
                    f"  {plan.name}: overage_block_price {before} -> "
                    f"{row['stripe_cents']}"
                )
            )
        self.stdout.write(
            self.style.SUCCESS(f"\nAligned {len(drifted)} plan(s) to Stripe.")
        )
