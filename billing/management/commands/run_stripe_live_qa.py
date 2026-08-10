"""
Run the billing QA suite against REAL Stripe test mode.

Every other billing test mocks Stripe, so they verify our BELIEFS about
its API rather than its behaviour. This command checks the beliefs.

WHAT IT COSTS
-------------
Nothing in money — it runs only against `sk_test_` keys and refuses
outright otherwise. It costs TIME: each scenario advances a Stripe test
clock, which Stripe processes asynchronously, so a full run takes
minutes, not seconds. That is why it belongs in a nightly job and not in
the commit path.

WHAT IT CREATES
---------------
Real Stripe test-mode customers/subscriptions/invoices, and real local
users on a non-routable `.invalid` domain. Everything is torn down at the
end, including the StripeEvent ledger rows the run produced — those are
deleted because a leftover FAILED QA event would make
`sweep_stale_stripe_events` page about a customer who never existed.

Usage:
    python manage.py run_stripe_live_qa --list
    python manage.py run_stripe_live_qa
    python manage.py run_stripe_live_qa --scenario renewals
    python manage.py run_stripe_live_qa --scenario renewals --scenario failed_renewal
    python manage.py run_stripe_live_qa --tier fast      # nightly set
    python manage.py run_stripe_live_qa --tier deep --workers 8
    python manage.py run_stripe_live_qa --keep-objects   # debugging only

Requires `ENABLE_STRIPE_LIVE_QA=True` and test-mode Stripe keys.
Exits non-zero if any scenario fails, so cron/CI can detect it.
"""

import logging

from django.core.management.base import BaseCommand, CommandError

# Importing the package is what REGISTERS the deep (long-horizon)
# scenarios into the shared registry. Without it --list and --tier would
# silently show only the fast ones, which is worse than an error: it
# looks like complete information.
import billing.live_qa  # noqa: F401
from billing.stripe_live_qa import LiveQAConfigurationError, LiveQARefused
from billing.stripe_live_qa_scenarios import (
    SCENARIO_TIERS,
    SCENARIOS,
    run_suite,
    scenarios_for_tier,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Run the billing QA scenarios against real Stripe test mode. "
        "Requires ENABLE_STRIPE_LIVE_QA and sk_test_ keys."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--scenario",
            action="append",
            dest="scenarios",
            help=(
                "Run only this scenario. Repeatable. Defaults to all. "
                f"Choices: {', '.join(sorted(SCENARIOS))}."
            ),
        )
        parser.add_argument(
            "--keep-objects",
            action="store_true",
            help=(
                "Skip teardown so the Stripe objects can be inspected in the "
                "dashboard. Debugging only — leaks test clocks, local users "
                "and ledger rows that you must then remove by hand."
            ),
        )
        parser.add_argument(
            "--list",
            action="store_true",
            dest="list_only",
            help="List the available scenarios and exit without running anything.",
        )
        parser.add_argument(
            "--tier",
            choices=["fast", "deep"],
            default=None,
            help=(
                "fast = the ~20-30 minute nightly set. deep = everything, "
                "including multi-year horizon runs that take hours. Ignored "
                "when --scenario is given."
            ),
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=1,
            help=(
                "Run this many scenarios concurrently. 1 (the default) uses "
                "the original single-threaded path. Higher values route "
                "events through a shared poller, which is what makes "
                "long-horizon runs finish in reasonable wall-clock time — "
                "each actor spends most of its life waiting on Stripe."
            ),
        )
        parser.add_argument(
            "--budget-seconds",
            type=float,
            default=None,
            help=(
                "Wall-clock budget. Scenarios not started before it expires "
                "are reported as budget-exhausted rather than silently "
                "dropped. Concurrent runs only."
            ),
        )

    def handle(self, *args, **options):
        if options["list_only"]:
            self.stdout.write("Available scenarios:")
            for name, fn in sorted(SCENARIOS.items()):
                summary = (fn.__doc__ or "").strip().splitlines()[0]
                tier = SCENARIO_TIERS.get(name, "fast")
                self.stdout.write(f"  [{tier}] {name}: {summary}")
            return

        scenarios = options["scenarios"]
        if not scenarios and options["tier"]:
            scenarios = scenarios_for_tier(options["tier"])
            self.stdout.write(f"tier={options['tier']}: {len(scenarios)} scenario(s)")

        workers = max(1, options["workers"])
        try:
            if workers > 1:
                # Imported lazily so the single-threaded path never pays
                # for the concurrency package.
                from billing.live_qa.runner import run_suite_concurrently

                result = run_suite_concurrently(
                    scenarios,
                    max_workers=workers,
                    keep_objects=options["keep_objects"],
                    budget_seconds=options["budget_seconds"],
                )
            else:
                result = run_suite(scenarios, keep_objects=options["keep_objects"])
        except LiveQARefused as exc:
            # A guardrail, not a bug. Say so plainly rather than dumping a
            # traceback that looks like a crash.
            raise CommandError(str(exc)) from exc
        except LiveQAConfigurationError as exc:
            raise CommandError(f"Live QA cannot run here: {exc}") from exc

        self._report(result)

        if not result.passed:
            raise CommandError(
                f"Stripe live QA FAILED ({len(result.failed_scenarios)} scenario(s), "
                f"{len(result.cleanup_errors)} cleanup error(s)). See the output above."
            )

    def _report(self, result):
        for scenario in result.scenarios:
            header = f"{scenario.name} ({scenario.duration_seconds:.1f}s)"
            if scenario.passed:
                self.stdout.write(self.style.SUCCESS(f"PASS {header}"))
            else:
                self.stdout.write(self.style.ERROR(f"FAIL {header}"))

            if scenario.error:
                self.stdout.write(f"      raised: {scenario.error}")

            # Only failed checks by default — a passing scenario's checks
            # are noise in a nightly log, and the ones that matter are the
            # ones that broke.
            for check in scenario.failed_checks:
                self.stdout.write(f"      {check}")

        for error in result.cleanup_errors:
            self.stdout.write(self.style.WARNING(f"CLEANUP {error}"))

        # Notes are facts about how far the run got — a Stripe test-clock
        # ceiling, an exhausted budget, a shared test account. They are
        # never failures, so they print plainly and do not affect the exit
        # code.
        for note in getattr(result, "notes", []):
            self.stdout.write(f"NOTE {note}")

        style = self.style.SUCCESS if result.passed else self.style.ERROR
        self.stdout.write(style(result.summary()))
