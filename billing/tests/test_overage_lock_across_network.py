"""
P1 — the overage grant must not hold its transaction (and the CreditWallet
row lock) open across an outbound Stripe receipt lookup.

`_handle_overage_checkout_completed` runs inside
`handle_checkout_completed`'s @transaction.atomic. It locks the wallet and
writes the grant. On b744c9f it then called the since-deleted
`resolve_stripe_receipt_url`, a live `stripe.PaymentIntent.retrieve`
(stripe-python default: 80s timeout, max_network_retries=2). While Python
waited on that call, the Postgres session was "idle in transaction" with
the wallet row locked.

Production's role guard rails (docs/ops/postgres-guard-rails.md) are
`idle_in_transaction_session_timeout = 60s` (confirmed live on production
and beta, 2026-09-17) and `lock_timeout = 10s`. So a slow Stripe response:

  1. kills the handler's session at 60s, rolling back a PAID grant; the
     event lands FAILED with handler_started_at set, and because the async
     endpoint already answered Stripe 200, Stripe never redelivers;
  2. meanwhile makes every credit consumption on that wallet fail after
     10s of waiting on the lock.

Both guard rails are scaled down here (2s) so the reproduction runs in
seconds; the Stripe stub sleeps past them. Everything else is the
production path: the StripeEvent claim, `_run_handler_inline` (what the
Celery task calls), the real handler, real Postgres, real threads.

These tests assert the CORRECT behaviour. They fail on b744c9f (evidence:
docs/evidence/p1_overage_lock/) and pass once the link is resolved after
commit (billing/receipts.py).
"""

import threading
import time

from django.db import connection, connections
from django.test import TransactionTestCase

from billing.models import CreditWallet, StripeEvent, StripeEventStatus
from billing.stripe_service import StripeWebhookHandler
from billing.tests.test_overage_purchase_integrity import (
    BLOCK,
    OverageFixture,
    make_plan,
)
from billing.tests.test_receipt_lookup_outside_transaction import (
    run_receipt_tasks_inline,
)
from billing.tests.testing_fake_stripe import fake_stripe
from billing.webhooks import _claim_stripe_event, _run_handler_inline

GUARD_RAIL = "2s"
SLOW_STRIPE_SECONDS = 4
THREAD_JOIN_SECONDS = 60


def mock_tasks():
    # Receipt fills run inline on the committing thread, outside the
    # transaction, exactly where a worker would run them.
    return run_receipt_tasks_inline()


class OverageGrantAcrossSlowStripeTests(TransactionTestCase, OverageFixture):
    reset_sequences = True

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build(email="overage.lock@billing.test")

    # -- helpers ---------------------------------------------------------

    def _event(self, session, event_id):
        return {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {"object": session},
        }

    def _deliver(self, event, *, set_guard_rails=False):
        """Claim + run exactly as billing.tasks.process_stripe_event does."""
        if set_guard_rails:
            with connection.cursor() as cur:
                cur.execute(
                    "SET idle_in_transaction_session_timeout = %s", [GUARD_RAIL]
                )
        _, token = _claim_stripe_event(event)
        return _run_handler_inline(
            event,
            StripeWebhookHandler.handle_checkout_completed,
            token,
            log_prefix="P1 repro",
        )

    def _in_thread(self, fn):
        box = {}

        def body():
            try:
                box["result"] = fn()
            except Exception as exc:  # noqa: BLE001 - asserted on by caller
                box["error"] = exc
            finally:
                connection.close()

        t = threading.Thread(target=body)
        t.start()
        return t, box

    def _join(self, t):
        t.join(timeout=THREAD_JOIN_SECONDS)
        self.assertFalse(t.is_alive(), "worker thread did not finish")

    # -- 1. the paid grant survives a slow receipt lookup -----------------

    def test_slow_receipt_lookup_does_not_roll_back_paid_grant(self):
        event = self._event(
            self.checkout_session(self.wallet, self.plan), "evt_p1_slow"
        )

        # Every receipt lookup is slow, wherever it runs. Only one that runs
        # inside the webhook transaction can kill that transaction.
        with fake_stripe(receipt_delay_seconds=SLOW_STRIPE_SECONDS), mock_tasks():
            t, box = self._in_thread(lambda: self._deliver(event, set_guard_rails=True))
            self._join(t)

        connections.close_all()
        row = StripeEvent.objects.get(stripe_event_id="evt_p1_slow")
        self.assertEqual(
            self.granted_credits(self.wallet),
            BLOCK,
            f"PAID overage grant was lost (event status={row.status}, "
            f"last_error={row.last_error[:300]!r}, thread_error={box.get('error')!r})",
        )
        self.assertEqual(self.purchase_ledger(self.wallet).count(), 1)
        self.assertEqual(row.status, StripeEventStatus.SUCCEEDED)

    # -- 2. the wallet is not locked for the duration of the Stripe call --

    def test_credit_consumption_is_not_blocked_by_slow_receipt_lookup(self):
        # Give the wallet spendable credits through the production path.
        with fake_stripe(), mock_tasks():
            self._deliver(
                self._event(
                    self.checkout_session(
                        self.wallet,
                        self.plan,
                        session_id="cs_seed",
                        payment_intent="pi_seed",
                    ),
                    "evt_p1_seed",
                )
            )
        self.assertEqual(self.granted_credits(self.wallet), BLOCK)

        slow_event = self._event(
            self.checkout_session(
                self.wallet, self.plan, session_id="cs_slow2", payment_intent="pi_slow2"
            ),
            "evt_p1_slow2",
        )

        def consume():
            with connection.cursor() as cur:
                cur.execute("SET lock_timeout = %s", [GUARD_RAIL])
            CreditWallet.objects.get(pk=self.wallet.pk).consume_credits(
                10, feature="p1_repro"
            )
            return "consumed"

        with fake_stripe(receipt_delay_seconds=SLOW_STRIPE_SECONDS), mock_tasks():
            purchase_t, _ = self._in_thread(lambda: self._deliver(slow_event))
            time.sleep(1)  # let the purchase take the wallet lock
            consume_t, consume_box = self._in_thread(consume)
            self._join(consume_t)
            self._join(purchase_t)

        self.assertNotIn(
            "error",
            consume_box,
            f"credit consumption failed while a purchase waited on Stripe: "
            f"{consume_box.get('error')!r}",
        )
        self.assertEqual(consume_box.get("result"), "consumed")
