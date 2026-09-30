"""
billing/tests/test_h28_stripe_budget.py
=======================================
H-28 Change 1, commit 9: the per-REQUEST Stripe budget
(DESIGN_PROPOSAL.md §9i (2)).

gunicorn kills a request at 100 s, and a killed worker runs no code, so it
sends no alert. stripe-python's default is 80 s per call with two retries,
and a licence operation makes up to four calls. So the Stripe work shares
one deadline (REQUEST_BUDGET_SECONDS, 75 s), each call waits only for what
is left, and the operation reaches its own error branch, and its alert,
in time.

The NAMED GATE-5 ASSERTION (d4's wording): make Stripe slow on EVERY call
in the sequence and assert the handler reaches its own error branch and
completes its alert before 100 s. A single slow call cannot catch the
multiplication. `test_slow_stripe_on_every_call_ends_in_the_error_branch_
with_its_alert_in_time` is that assertion, with the budget scaled down so
the test runs in about a second. The production numbers are pinned
separately.
"""

import threading
import time
from unittest.mock import patch

from django.db import connections, transaction

from billing import license_stripe_mutation
from billing.imports import stripe
from billing.license_service import LicenseSubscriptionService
from billing.models import (
    LicenseStripeMutationIntent,
    LicenseStripeMutationOperation,
    LicenseStripeMutationStatus,
)
from billing.tests.test_h28_cancel_phases import MUTATION_LOGGER, LicencePhaseTestCase
from billing.webhooks import WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS

PRODUCTION_BUDGET = license_stripe_mutation.REQUEST_BUDGET_SECONDS
BUDGET = 1.0
DELAY = 0.6  # every call; four in sequence would take 2.4 s


def join_abandoned_calls():
    """A call the budget gave up on keeps running in the background; let it
    finish before the next test touches the fake."""
    for worker in threading.enumerate():
        if worker.name == "h28-stripe-call":
            worker.join(5)


class StripeBudgetTests(LicencePhaseTestCase):
    SUB_ID = "sub_h28_budget"

    def setUp(self):
        super().setUp()
        self.addCleanup(join_abandoned_calls)
        self.emails = []
        p = patch(
            "AutoGrader.dispatch.safe_delay",
            side_effect=lambda task, **kwargs: self.emails.append(kwargs),
        )
        p.start()
        self.addCleanup(p.stop)

    def slow(self, fn):
        def call(*args, **kwargs):
            time.sleep(DELAY)
            return fn(*args, **kwargs)

        return call

    def slow_stripe_everywhere(self):
        fake = self.stripe
        return [
            patch.object(stripe.Subscription, name, side_effect=self.slow(method))
            for name, method in (
                ("retrieve", fake.subscription_retrieve),
                ("modify", fake.subscription_modify),
                ("delete", fake.subscription_delete),
            )
        ] + [
            patch.object(
                stripe.Invoice, "retrieve", side_effect=self.slow(fake.invoice_retrieve)
            ),
            patch.object(
                stripe.Invoice, "void_invoice", side_effect=self.slow(fake.invoice_void)
            ),
            patch.object(
                stripe.Price, "create", side_effect=self.slow(fake.price_create)
            ),
        ]

    # -- the named Gate-5 assertion ------------------------------------------

    def test_slow_stripe_on_every_call_ends_in_the_error_branch_with_its_alert_in_time(
        self,
    ):
        patches = self.slow_stripe_everywhere()
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        started = time.monotonic()
        with patch.object(
            license_stripe_mutation, "REQUEST_BUDGET_SECONDS", BUDGET
        ), self.assertLogs(MUTATION_LOGGER, level="ERROR") as logs:
            with self.assertRaisesRegex(
                ValueError, "Stripe error while updating seats"
            ):
                LicenseSubscriptionService.update_seats(
                    self.licence, self.SEATS + 5, performed_by=self.superadmin
                )
        elapsed = time.monotonic() - started

        # The error branch was reached within the budget, not after the slow
        # sequence: four calls would have taken 4 x DELAY.
        self.assertLess(elapsed, BUDGET + 0.5, f"took {elapsed:.2f} s")
        # ...and the alert completed before the operation returned.
        self.assertIn("MANUAL RECONCILIATION NEEDED", "\n".join(logs.output))
        self.assertEqual(
            [e["recipient_list"] for e in self.emails], [[self.superadmin.email]]
        )
        # The abandoned modify may still land, so it is never classified
        # "not applied": the intent stays PENDING for a human.
        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.PENDING)
        join_abandoned_calls()
        # And it did land, after the caller gave up. The intent still says
        # PENDING (outcome unknown), never FAILED ("not applied").
        self.assertEqual(self.stripe.quantity, self.SEATS + 5)
        intent.refresh_from_db()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.PENDING)
        self.assertEqual(
            [c[0] for c in self.stripe.calls].count("Subscription.retrieve"),
            1,
            "an abandoned call must not be read back",
        )
        self.assertEqual(self.fresh_licence().max_seats, self.SEATS)

    def test_the_production_budget_ends_well_inside_the_request_timeout(self):
        """Scaled down above; these are the real numbers. The Stripe work
        must leave room for the local write, a compensation and the alert
        before gunicorn's kill."""
        self.assertLessEqual(PRODUCTION_BUDGET, 80)
        self.assertLessEqual(
            PRODUCTION_BUDGET + 15, WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS
        )

    # -- the budget's own rules ----------------------------------------------

    def test_a_call_the_budget_never_started_is_failed_not_pending(self):
        intent = LicenseStripeMutationIntent.objects.create(
            license_subscription=self.licence,
            operation=LicenseStripeMutationOperation.CANCEL,
            stripe_subscription_id=self.SUB_ID,
            requested_change={"auto_renew": [True, False]},
        )
        called = []

        with license_stripe_mutation.stripe_budget(0):
            with self.assertRaises(
                license_stripe_mutation.StripeBudgetExhausted
            ) as caught:
                license_stripe_mutation.apply_at_stripe(
                    intent,
                    call=lambda **key: called.append(key),
                    reached=lambda: True,
                )

        self.assertFalse(caught.exception.started)
        self.assertEqual(called, [])
        intent.refresh_from_db()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.FAILED)
        self.assertIn("Not attempted", str(intent.failure_reason))

    def test_nested_budgets_keep_the_earlier_deadline(self):
        with license_stripe_mutation.stripe_budget(0):
            with license_stripe_mutation.stripe_budget(60):
                with self.assertRaises(license_stripe_mutation.StripeBudgetExhausted):
                    license_stripe_mutation.call_stripe(lambda: "never")

    def test_without_a_budget_the_call_runs_inline(self):
        self.assertIs(
            license_stripe_mutation.call_stripe(threading.current_thread),
            threading.current_thread(),
        )

    def test_under_a_budget_the_call_reports_its_callers_transaction(self):
        """The runtime proof that no Stripe call runs inside a transaction
        must survive the worker thread."""
        with license_stripe_mutation.stripe_budget():
            outside = license_stripe_mutation.call_stripe(
                license_stripe_mutation.caller_in_atomic_block
            )
            with transaction.atomic():
                inside = license_stripe_mutation.call_stripe(
                    license_stripe_mutation.caller_in_atomic_block
                )
            worker = license_stripe_mutation.call_stripe(threading.current_thread)

        self.assertFalse(outside)
        self.assertTrue(inside)
        self.assertNotEqual(worker, threading.current_thread())

    def test_the_worker_thread_leaves_no_database_connection_open(self):
        """The worker only calls Stripe; even if something on it touched the
        database, its connection is closed, so none leaks."""
        seen = {}

        def touches_the_database():
            # connections["default"] is this thread's own wrapper (the
            # module-level `connection` is a proxy shared by every thread).
            worker_wrapper = connections["default"]
            with worker_wrapper.cursor() as cursor:
                cursor.execute("SELECT 1")
            seen["wrapper"] = worker_wrapper
            return "done"

        with license_stripe_mutation.stripe_budget():
            self.assertEqual(
                license_stripe_mutation.call_stripe(touches_the_database), "done"
            )

        self.assertIsNot(seen["wrapper"], connections["default"])
        self.assertIsNone(seen["wrapper"].connection, "left open")

    def test_an_error_from_stripe_reaches_the_caller_unchanged(self):
        def refuse():
            raise stripe.error.InvalidRequestError("No such subscription", "id")

        with license_stripe_mutation.stripe_budget():
            with self.assertRaisesRegex(
                stripe.error.InvalidRequestError, "No such subscription"
            ):
                license_stripe_mutation.call_stripe(refuse)
