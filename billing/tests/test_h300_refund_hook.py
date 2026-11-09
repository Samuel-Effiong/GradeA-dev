"""H-300 road A (staging, B1a): a failure to queue the departed-share refund
or its email, after the purchase has committed, must not mark the event
FAILED.

The hook runs after the webhook's transaction commits. If it raised, the
handler raised, `_run_handler_inline` marked the event FAILED with the grants
already made, and the event was left for `replay_stripe_events`. The hook is
now registered with robust=True and queues through safe_delay, like the
receipt hook (billing/receipts.py); a refund that could not be queued is
logged for manual reconciliation.

Every Stripe call is mocked.
"""

import logging
from datetime import timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from kombu.exceptions import OperationalError

from billing.models import (
    LicenseOveragePurchaseIntent,
    LicenseOveragePurchaseStatus,
    StripeEvent,
    StripeEventStatus,
)
from billing.stripe_service import StripeWebhookHandler
from billing.tests.test_departed_teacher_refund import (
    CREATE,
    LIST,
    PI,
    PRICE,
    SEND,
    SchoolFixture,
    refund_list,
    switch_on,
    task,
)
from billing.webhooks import STRIPE_EVENT_CLAIM_STALE_AFTER, _claim_stripe_event

EVENT_ID = "evt_h300_hook"
BLOCK = 5000


class Base(SchoolFixture):
    def make_purchase(self):
        self.make_school()
        self.intent = LicenseOveragePurchaseIntent.objects.create(
            license_subscription=self.license_sub,
            initiated_by=self.admin,
            total_blocks=3,
            allocations=self.allocations,
            block_size_snapshot=self.plan.overage_block_size,
            unit_price_cents_snapshot=PRICE,
            amount_cents=3 * PRICE,
            status=LicenseOveragePurchaseStatus.PENDING,
            stripe_checkout_session_id="cs_h300_hook",
        )
        self.session = {
            "id": "cs_h300_hook",
            "object": "checkout.session",
            "payment_status": "paid",
            "payment_intent": PI,
            "invoice": None,
            "amount_total": 3 * PRICE,
            "currency": "usd",
            "metadata": {
                "flow": "license_overage_purchase_checkout",
                "license_id": str(self.license_sub.id),
                "intent_id": str(self.intent.id),
            },
        }
        self.event = {
            "id": EVENT_ID,
            "type": "checkout.session.completed",
            "data": {"object": self.session},
        }
        self.depart(self.c)

    def ours(self):
        return {
            "id": "re_ours",
            "amount": PRICE,
            "status": "succeeded",
            "metadata": {
                "system_refund_reason": "departed_teacher_share",
                "intent_id": str(self.intent.id),
                "retain_credits": "true",
            },
        }

    def row(self):
        return StripeEvent.objects.get(stripe_event_id=EVENT_ID)

    def granted(self):
        return tuple(self.overage_credits(t) for t in (self.a, self.b, self.c))


class RoadAHookFailureAfterCommit(TransactionTestCase, Base):
    """Real commits, so the on-commit hook runs as the handler returns."""

    def process(self):
        from billing.tasks import process_stripe_event

        _, token = _claim_stripe_event(self.event)
        process_stripe_event.run(EVENT_ID, token.isoformat())

    def test_h300_a1_broker_down_for_the_refund_leaves_the_event_succeeded(self):
        self.make_purchase()
        with switch_on(), patch.object(
            task(), "delay", side_effect=OperationalError("broker down")
        ) as delay, patch(CREATE) as create, patch(
            LIST, return_value=refund_list()
        ), patch(
            SEND
        ), self.assertLogs(
            "billing", logging.ERROR
        ) as logs:
            self.process()
        self.assertEqual(self.row().status, StripeEventStatus.SUCCEEDED)
        self.assertEqual(self.granted(), (BLOCK, BLOCK, 0))
        delay.assert_called_once_with(str(self.intent.id), PRICE)
        create.assert_not_called()
        self.assertTrue(
            any(
                "MANUAL RECONCILIATION" in line and str(self.intent.id) in line
                for line in logs.output
            ),
            logs.output,
        )

    def test_h300_a2_broker_down_for_the_email_still_queues_the_refund(self):
        self.make_purchase()
        with switch_on(), patch.object(task(), "delay") as delay, patch(CREATE), patch(
            LIST, return_value=refund_list()
        ), patch(SEND, side_effect=OperationalError("broker down")) as send:
            self.process()
        self.assertEqual(self.row().status, StripeEventStatus.SUCCEEDED)
        self.assertEqual(self.granted(), (BLOCK, BLOCK, 0))
        delay.assert_called_once_with(str(self.intent.id), PRICE)
        send.assert_called_once()

    def test_h300_a3_any_other_queueing_error_is_contained_too(self):
        self.make_purchase()
        with switch_on(), patch.object(
            task(), "delay", side_effect=RuntimeError("not a broker error")
        ), patch(CREATE), patch(LIST, return_value=refund_list()), patch(SEND):
            self.process()
        self.assertEqual(self.row().status, StripeEventStatus.SUCCEEDED)
        self.assertEqual(self.granted(), (BLOCK, BLOCK, 0))


class RoadBSwitchOnReplay(TestCase, Base):
    """Road B with the refund switch on: the worker dies after commit, the
    sweeper marks the claim FAILED, an operator replays it."""

    def run_then_die(self):
        _, token = _claim_stripe_event(self.event)
        StripeEvent.objects.filter(stripe_event_id=EVENT_ID).update(
            handler_started_at=timezone.now()
        )
        with self.captureOnCommitCallbacks(execute=True):
            StripeWebhookHandler.handle_checkout_completed(self.session)
        StripeEvent.objects.filter(stripe_event_id=EVENT_ID).update(
            claimed_at=token - STRIPE_EVENT_CLAIM_STALE_AFTER - timedelta(minutes=1)
        )
        from billing.tasks import sweep_stale_stripe_events

        sweep_stale_stripe_events.run()
        self.assertEqual(self.row().status, StripeEventStatus.FAILED)

    def replay_with_refund(self):
        with switch_on(), patch.object(
            task(), "delay", side_effect=lambda *a: task().apply(args=a)
        ), patch(CREATE) as create, patch(
            LIST, side_effect=[refund_list(), refund_list(self.ours())]
        ), patch(
            SEND
        ):
            self.run_then_die()
            with self.captureOnCommitCallbacks(execute=True):
                call_command("replay_stripe_events", "--event-id", EVENT_ID, "--apply")
        return create

    def test_h300_b2_switch_on_replay_does_not_grant_twice(self):
        self.make_purchase()
        self.replay_with_refund()
        self.assertEqual(self.granted(), (BLOCK, BLOCK, 0))

    def test_h300_b3_switch_on_replay_does_not_refund_twice(self):
        self.make_purchase()
        create = self.replay_with_refund()
        self.assertEqual(create.call_count, 1)
