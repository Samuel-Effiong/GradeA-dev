"""
billing/tests/test_overage_requires_payment_intent.py
=====================================================
H-66: the paid overage handler (_handle_overage_checkout_completed) grants
only when the session carries a payment_intent.

The PaymentIntent is this flow's idempotency key: the duplicate-delivery
check (_overage_already_granted) runs only when there is one, so a "paid"
session without one would take the wallet lock with no key, and two
different event ids for it would both grant. It is also what refunds and
disputes are matched on. H-62's Gate 4 (1a) found no real signed event can
reach this today (a paid, payment-mode session with a positive amount
always carries one), so the refusal is the second layer, made
unconditional so a future flow change (coupons, a $0 session, a wider
AUTO_REPLAYABLE) can't silently remove the key: refused with an ERROR, as
an unpaid session already is.

Tested through the handler and through the failed-event replay path.
"""

from django.test import TestCase

from billing.event_replay import replay_safe_failed_events
from billing.models import BillingTransaction, StripeEvent, StripeEventStatus
from billing.stripe_service import StripeWebhookHandler
from billing.tests.test_event_replay import ReplayFixture
from billing.tests.test_overage_purchase_integrity import BLOCK, make_plan

HANDLER_LOGGER = "billing.stripe_service"


class OverageRequiresPaymentIntentTests(TestCase, ReplayFixture):
    def setUp(self):
        self.stub_receipt_scheduling()
        self.plan = make_plan()
        self.user, self.wallet = self.build(email="h66-overage@example.com")

    def deliver(self, session):
        StripeWebhookHandler.handle_checkout_completed(session)

    def assert_refused(self, logs):
        self.assertEqual(self.overage_buckets(self.wallet).count(), 0)
        self.assertEqual(self.granted_credits(self.wallet), 0)
        self.assertFalse(
            BillingTransaction.objects.filter(user=self.user).exists(),
            "a refused session must not be recorded as a paid purchase",
        )
        [line] = [line for line in logs.output if "no payment_intent" in line]
        self.assertIn("ERROR", line)
        self.assertIn("credits NOT granted", line)
        self.assertNotIn("@", line)

    def test_a_paid_session_without_a_payment_intent_is_refused(self):
        session = self.checkout_session(self.wallet, self.plan, payment_intent=None)
        with self.assertLogs(HANDLER_LOGGER, "ERROR") as logs:
            self.deliver(session)
        self.assert_refused(logs)

    def test_an_empty_payment_intent_is_refused_too(self):
        session = self.checkout_session(self.wallet, self.plan, payment_intent="")
        with self.assertLogs(HANDLER_LOGGER, "ERROR") as logs:
            self.deliver(session)
        self.assert_refused(logs)

    def test_a_session_without_payment_status_or_payment_intent_is_refused(self):
        """No payment_status skips the unpaid refusal; the missing key still
        refuses."""
        session = self.checkout_session(self.wallet, self.plan, payment_intent=None)
        del session["payment_status"]
        with self.assertLogs(HANDLER_LOGGER, "ERROR") as logs:
            self.deliver(session)
        self.assert_refused(logs)

    def test_two_deliveries_of_a_keyless_session_grant_nothing(self):
        """What the key protects: two event ids, one session."""
        session = self.checkout_session(self.wallet, self.plan, payment_intent=None)
        with self.assertLogs(HANDLER_LOGGER, "ERROR"):
            self.deliver(session)
            self.deliver(session)
        self.assertEqual(self.granted_credits(self.wallet), 0)

    def test_the_replay_path_refuses_it_too(self):
        row = self.failed_event("evt_h66_keyless", payment_intent=None)

        with self.assertLogs(HANDLER_LOGGER, "ERROR") as logs:
            replay_safe_failed_events()

        self.assert_refused(logs)
        row.refresh_from_db()
        self.assertEqual(row.auto_replay_attempts, 1)

    def test_a_paid_session_with_a_payment_intent_still_grants(self):
        """The control."""
        self.deliver(self.checkout_session(self.wallet, self.plan))
        self.assertEqual(self.granted_credits(self.wallet), BLOCK)

    def test_a_replayed_session_with_a_payment_intent_still_grants(self):
        row = self.failed_event("evt_h66_keyed", payment_intent="pi_h66_keyed")
        replay_safe_failed_events()
        self.assertEqual(self.granted_credits(self.wallet), BLOCK)
        row.refresh_from_db()
        self.assertEqual(row.status, StripeEventStatus.SUCCEEDED)
        self.assertTrue(StripeEvent.objects.filter(pk=row.pk).exists())
