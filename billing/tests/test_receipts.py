"""
billing/receipts.py: resolving Stripe receipt links AFTER commit.

The flow-level proof that no lookup runs inside a webhook transaction is in
test_receipt_lookup_outside_transaction.py. This file covers the pieces:

  * the bounded lookup (timeout, no retries, never raises);
  * fill_receipt_url (fill-if-null, never overwrites);
  * schedule_receipt_url_fill (on_commit only; rollback queues nothing;
    broker down after commit never fails the committed purchase);
  * the hourly sweep (window, minimum age, batch and time bounds);
  * concurrency: 20 threads x 10 rounds, one logical fill; duplicate
    webhook deliveries with a slow Stripe grant exactly once.

BillingTransaction rows are recorded through
BillingTransactionService.record, the only path production uses.
"""

import threading
from datetime import timedelta
from unittest import mock

from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from kombu.exceptions import OperationalError as KombuOperationalError

from billing import receipts
from billing.billing_transaction_service import BillingTransactionService
from billing.models import (
    BillingTransaction,
    BillingTransactionMethod,
    BillingTransactionSource,
    BillingTransactionStatus,
    BillingTransactionType,
    StripeEvent,
    StripeEventStatus,
)
from billing.receipts import FillOutcome
from billing.tests.test_overage_purchase_integrity import (
    BLOCK,
    OverageFixture,
    make_plan,
)
from billing.tests.test_receipt_lookup_outside_transaction import (
    RECEIPT_TASK_NAME,
    run_receipt_tasks_inline,
)
from billing.tests.testing_fake_stripe import (
    assert_stripe_untouched,
    charge_receipt_url,
    fake_stripe,
    invoice_url,
)
from billing.webhooks import _claim_stripe_event, _run_handler_inline

THREADS = 20
ROUNDS = 10
JOIN_SECONDS = 60


def record(**refs):
    occurred_at = refs.pop("occurred_at", None)
    return BillingTransactionService.record(
        source=BillingTransactionSource.INDIVIDUAL,
        transaction_type=BillingTransactionType.INDIVIDUAL_OVERAGE_PURCHASE,
        status=BillingTransactionStatus.PAID,
        billing_method=BillingTransactionMethod.STRIPE,
        amount_cents=1000,
        description="receipt test",
        occurred_at=occurred_at,
        **refs,
    )


def pi_receipt(pi_id):
    return charge_receipt_url(f"ch_for_{pi_id}")


def _fail_on_second_call(original):
    """Let the pre-read through, then kill the process before the UPDATE."""
    state = {"calls": 0}

    def wrapper(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] > 1:
            raise RuntimeError("worker killed after Stripe answered")
        return original(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


class LookupReceiptUrlTests(TestCase):
    def test_priority_invoice_then_charge_then_payment_intent(self):
        with fake_stripe() as fake:
            self.assertEqual(
                receipts.lookup_receipt_url(
                    invoice_id="in_1", charge_id="ch_1", payment_intent_id="pi_1"
                ),
                invoice_url("in_1"),
            )
            self.assertEqual(
                receipts.lookup_receipt_url(charge_id="ch_1", payment_intent_id="pi_1"),
                charge_receipt_url("ch_1"),
            )
            self.assertEqual(
                receipts.lookup_receipt_url(payment_intent_id="pi_1"),
                pi_receipt("pi_1"),
            )
        self.assertEqual(
            [c.path for c in fake.calls],
            ["/v1/invoices/in_1", "/v1/charges/ch_1", "/v1/payment_intents/pi_1"],
        )
        self.assertEqual(fake.calls[2].query.get("expand[0]"), ["latest_charge"])

    def test_bounded_client_short_timeout_and_no_retries(self):
        with fake_stripe() as fake:
            receipts.lookup_receipt_url(payment_intent_id="pi_bounded")
        (call,) = fake.calls
        self.assertEqual(call.timeout, receipts.RECEIPT_LOOKUP_TIMEOUT_SECONDS)
        self.assertLessEqual(receipts.RECEIPT_LOOKUP_TIMEOUT_SECONDS, 10)
        self.assertEqual(call.max_network_retries, 0)

    def test_no_reference_makes_no_call(self):
        with fake_stripe() as fake:
            self.assertIsNone(receipts.lookup_receipt_url())
        self.assertEqual(fake.calls, [])

    def test_stripe_error_returns_none(self):
        """
        A Stripe outage is expected and routine: WARNING, never ERROR. An
        ERROR here would page someone for a condition the sweep heals.
        """
        error = (500, {"error": {"type": "api_error", "message": "boom"}})
        with fake_stripe(receipt_error=error), self.assertLogs(
            "billing.receipts", "WARNING"
        ) as logs:
            self.assertIsNone(receipts.lookup_receipt_url(invoice_id="in_err"))
        self.assertEqual({r.levelname for r in logs.records}, {"WARNING"})

    def test_network_timeout_returns_none(self):
        import stripe

        with mock.patch.object(
            receipts,
            "_receipt_stripe_client",
            side_effect=stripe.APIConnectionError("timed out"),
        ), self.assertLogs("billing.receipts", "WARNING"):
            self.assertIsNone(receipts.lookup_receipt_url(invoice_id="in_timeout"))

    def test_unexpected_error_returns_none(self):
        with mock.patch.object(
            receipts, "_receipt_stripe_client", side_effect=RuntimeError("bug")
        ), self.assertLogs("billing.receipts", "ERROR"):
            self.assertIsNone(receipts.lookup_receipt_url(invoice_id="in_bug"))

    def test_unexpanded_latest_charge_returns_none(self):
        with fake_stripe() as fake:
            original = fake._route

            def route(call, post_data):
                if call.path.startswith("/v1/payment_intents/"):
                    return 200, {
                        "id": "pi_raw",
                        "object": "payment_intent",
                        "latest_charge": "ch_not_expanded",
                    }
                return original(call, post_data)

            fake._route = route
            with self.assertLogs("billing.receipts", "ERROR") as logs:
                self.assertIsNone(
                    receipts.lookup_receipt_url(payment_intent_id="pi_raw")
                )
        self.assertIn("unexpanded latest_charge", "\n".join(logs.output))

    def test_payment_intent_without_charge_returns_none(self):
        with fake_stripe() as fake:
            fake._route = lambda call, post_data: (
                200,
                {"id": "pi_nc", "object": "payment_intent", "latest_charge": None},
            )
            self.assertIsNone(receipts.lookup_receipt_url(payment_intent_id="pi_nc"))


# ---------------------------------------------------------------------------
# Fill
# ---------------------------------------------------------------------------


class FillReceiptUrlTests(TestCase):
    def test_fills_missing_link(self):
        txn = record(stripe_payment_intent_id="pi_fill")
        with fake_stripe():
            self.assertEqual(receipts.fill_receipt_url(txn.pk), FillOutcome.FILLED)
        txn.refresh_from_db()
        self.assertEqual(txn.receipt_url, pi_receipt("pi_fill"))

    def test_fills_empty_string_link(self):
        txn = record(stripe_payment_intent_id="pi_blank")
        BillingTransaction.objects.filter(pk=txn.pk).update(receipt_url="")
        with fake_stripe():
            self.assertEqual(receipts.fill_receipt_url(txn.pk), FillOutcome.FILLED)

    def test_never_overwrites_and_makes_no_call_when_set(self):
        txn = record(
            stripe_payment_intent_id="pi_set", receipt_url="https://kept.test/r"
        )
        with fake_stripe() as fake:
            self.assertEqual(receipts.fill_receipt_url(txn.pk), FillOutcome.ALREADY_SET)
        self.assertEqual(fake.calls, [])
        txn.refresh_from_db()
        self.assertEqual(txn.receipt_url, "https://kept.test/r")

    def test_link_set_during_lookup_is_not_overwritten(self):
        """The conditional UPDATE, not the pre-read, is the guard."""
        txn = record(stripe_payment_intent_id="pi_race")
        with fake_stripe() as fake:
            original = fake._route

            def route(call, post_data):
                BillingTransaction.objects.filter(pk=txn.pk).update(
                    receipt_url="https://winner.test/r"
                )
                return original(call, post_data)

            fake._route = route
            self.assertEqual(receipts.fill_receipt_url(txn.pk), FillOutcome.ALREADY_SET)
        txn.refresh_from_db()
        self.assertEqual(txn.receipt_url, "https://winner.test/r")

    def test_unresolved_leaves_row_for_the_sweep(self):
        txn = record(stripe_payment_intent_id="pi_down")
        error = (503, {"error": {"type": "api_error", "message": "unavailable"}})
        with fake_stripe(receipt_error=error), self.assertLogs(
            "billing.receipts", "WARNING"
        ):
            self.assertEqual(receipts.fill_receipt_url(txn.pk), FillOutcome.UNRESOLVED)
        txn.refresh_from_db()
        self.assertIsNone(txn.receipt_url)

    def test_row_without_stripe_reference(self):
        txn = record(stripe_checkout_session_id="cs_only")
        with fake_stripe() as fake:
            self.assertEqual(
                receipts.fill_receipt_url(txn.pk), FillOutcome.NO_STRIPE_REFERENCE
            )
        self.assertEqual(fake.calls, [])

    def test_missing_row(self):
        self.assertEqual(
            receipts.fill_receipt_url("00000000-0000-0000-0000-000000000000"),
            FillOutcome.MISSING,
        )

    def test_task_wrapper(self):
        from billing.tasks import fill_billing_transaction_receipt_url

        self.assertEqual(fill_billing_transaction_receipt_url.name, RECEIPT_TASK_NAME)
        txn = record(stripe_invoice_id="in_task")
        with fake_stripe():
            result = fill_billing_transaction_receipt_url.run(str(txn.pk))
        self.assertEqual(result, f"{txn.pk}: filled")


# ---------------------------------------------------------------------------
# Scheduling (real commits)
# ---------------------------------------------------------------------------


class ScheduleReceiptUrlFillTests(TransactionTestCase):
    def test_queued_only_after_commit(self):
        seen_in_transaction = []
        with fake_stripe(), mock.patch("AutoGrader.dispatch.safe_delay") as safe_delay:
            with transaction.atomic():
                txn = record(stripe_payment_intent_id="pi_commit")
                receipts.schedule_receipt_url_fill(txn)
                seen_in_transaction.append(safe_delay.call_count)
            self.assertEqual(seen_in_transaction, [0])
            safe_delay.assert_called_once()
            self.assertEqual(safe_delay.call_args.args[1], str(txn.pk))

    def test_rollback_queues_nothing(self):
        with mock.patch("AutoGrader.dispatch.safe_delay") as safe_delay:
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    txn = record(stripe_payment_intent_id="pi_rollback")
                    receipts.schedule_receipt_url_fill(txn)
                    raise RuntimeError("handler failed after recording")
        safe_delay.assert_not_called()
        self.assertFalse(
            BillingTransaction.objects.filter(
                stripe_payment_intent_id="pi_rollback"
            ).exists()
        )

    def test_nothing_queued_when_link_already_known_or_no_reference(self):
        with mock.patch("AutoGrader.dispatch.safe_delay") as safe_delay:
            with transaction.atomic():
                receipts.schedule_receipt_url_fill(
                    record(
                        stripe_invoice_id="in_known", receipt_url="https://known.test/r"
                    )
                )
                receipts.schedule_receipt_url_fill(
                    record(stripe_checkout_session_id="cs_x")
                )
        safe_delay.assert_not_called()

    def test_broker_down_after_commit_does_not_fail_the_caller(self):
        from billing.tasks import fill_billing_transaction_receipt_url

        with mock.patch.object(
            fill_billing_transaction_receipt_url,
            "delay",
            side_effect=KombuOperationalError("broker unreachable"),
        ), self.assertLogs("AutoGrader.dispatch", "ERROR"):
            with transaction.atomic():
                txn = record(stripe_payment_intent_id="pi_broker_down")
                receipts.schedule_receipt_url_fill(txn)
        self.assertTrue(BillingTransaction.objects.filter(pk=txn.pk).exists())

    def test_unexpected_enqueue_error_after_commit_does_not_fail_the_caller(self):
        from billing.tasks import fill_billing_transaction_receipt_url

        with mock.patch.object(
            fill_billing_transaction_receipt_url,
            "delay",
            side_effect=RuntimeError("bug"),
        ), self.assertLogs("django.db.backends.base", "ERROR"):
            with transaction.atomic():
                txn = record(stripe_payment_intent_id="pi_enqueue_bug")
                receipts.schedule_receipt_url_fill(txn)
        self.assertTrue(BillingTransaction.objects.filter(pk=txn.pk).exists())


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


class SweepMissingReceiptUrlsTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def aged(self, minutes, **refs):
        return record(occurred_at=self.now - timedelta(minutes=minutes), **refs)

    def test_fills_rows_in_window_only(self):
        fresh = self.aged(1, stripe_payment_intent_id="pi_fresh")
        due = self.aged(30, stripe_payment_intent_id="pi_due")
        old = self.aged(60 * 24 * 4, stripe_payment_intent_id="pi_old")
        no_ref = self.aged(30, stripe_checkout_session_id="cs_noref")
        done = self.aged(
            30, stripe_invoice_id="in_done", receipt_url="https://done.test/r"
        )

        with fake_stripe() as fake:
            counts = receipts.sweep_missing_receipt_urls(now=self.now)

        self.assertEqual(counts["filled"], 1)
        self.assertEqual([c.path for c in fake.calls], ["/v1/payment_intents/pi_due"])
        for row, expected in (
            (fresh, None),
            (due, pi_receipt("pi_due")),
            (old, None),
            (no_ref, None),
            (done, "https://done.test/r"),
        ):
            row.refresh_from_db()
            self.assertEqual(row.receipt_url, expected)

    def test_batch_bound_newest_first(self):
        rows = [
            self.aged(10 + i, stripe_payment_intent_id=f"pi_b{i}") for i in range(5)
        ]
        with fake_stripe(), mock.patch.object(receipts, "RECEIPT_SWEEP_BATCH_SIZE", 3):
            counts = receipts.sweep_missing_receipt_urls(now=self.now)
        self.assertEqual(counts["filled"], 3)
        filled = [
            r.receipt_url is not None
            for r in (BillingTransaction.objects.get(pk=r.pk) for r in rows)
        ]
        self.assertEqual(filled, [True, True, True, False, False])

    def test_rows_it_cannot_fill_do_not_consume_the_batch(self):
        """Already-filled and reference-less rows are excluded in the query,
        so they can never starve a fillable row out of a bounded batch."""
        for i in range(3):
            self.aged(
                10 + i,
                stripe_invoice_id=f"in_done{i}",
                receipt_url="https://done.test/r",
            )
            self.aged(10 + i, stripe_checkout_session_id=f"cs_noref{i}")
        target = self.aged(30, stripe_payment_intent_id="pi_starved")
        with fake_stripe(), mock.patch.object(receipts, "RECEIPT_SWEEP_BATCH_SIZE", 3):
            counts = receipts.sweep_missing_receipt_urls(now=self.now)
        self.assertEqual(counts["filled"], 1)
        target.refresh_from_db()
        self.assertEqual(target.receipt_url, pi_receipt("pi_starved"))

    def test_time_budget_stops_the_run(self):
        for i in range(4):
            self.aged(10 + i, stripe_payment_intent_id=f"pi_t{i}")
        ticks = iter([0, 0, 1, 10_000])
        with fake_stripe(), mock.patch.object(
            receipts, "monotonic", lambda: next(ticks)
        ):
            counts = receipts.sweep_missing_receipt_urls(now=self.now)
        self.assertEqual(counts["filled"], 2)
        self.assertEqual(counts["out_of_time"], 2)

    def test_unresolved_rows_are_retried_by_a_later_sweep(self):
        row = self.aged(30, stripe_invoice_id="in_retry")
        error = (503, {"error": {"type": "api_error", "message": "down"}})
        with fake_stripe(receipt_error=error), self.assertLogs(
            "billing.receipts", "WARNING"
        ):
            self.assertEqual(
                receipts.sweep_missing_receipt_urls(now=self.now)["unresolved"], 1
            )
        with fake_stripe():
            self.assertEqual(
                receipts.sweep_missing_receipt_urls(now=self.now)["filled"], 1
            )
        row.refresh_from_db()
        self.assertEqual(row.receipt_url, invoice_url("in_retry"))

    def test_beat_schedule_and_health_expectation(self):
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE["sweep-missing-receipt-urls"]
        self.assertEqual(entry["task"], "billing.tasks.sweep_missing_receipt_urls")
        self.assertIn("sweep-missing-receipt-urls", settings.BEAT_HEALTH_EXPECTATIONS)

    def test_sweep_task_wrapper(self):
        from billing.tasks import sweep_missing_receipt_urls

        self.aged(30, stripe_invoice_id="in_wrapper")
        with fake_stripe():
            summary = sweep_missing_receipt_urls.run()
        self.assertIn("1 filled", summary)


class BackfillReceiptUrlsCommandTests(TestCase):
    def test_uses_bounded_lookup_and_never_overwrites(self):
        from io import StringIO

        from django.core.management import call_command

        missing = record(
            stripe_invoice_id="in_backfill",
            occurred_at=timezone.now() - timedelta(days=400),
        )
        kept = record(stripe_invoice_id="in_backfill_kept")

        with fake_stripe() as fake:
            original = fake._route

            def route(call, post_data):
                # A concurrent sweep fills `kept` while the command is looking
                # it up - once, and only for kept's own lookup, so the order
                # in which the command visits rows cannot mask an overwrite.
                if call.path.endswith("/in_backfill_kept"):
                    BillingTransaction.objects.filter(pk=kept.pk).update(
                        receipt_url="https://sweep-won.test/r"
                    )
                return original(call, post_data)

            fake._route = route
            # `kept` is still NULL when the command selects its rows.
            call_command("backfill_receipt_urls", stdout=StringIO())

        self.assertTrue(all(c.max_network_retries == 0 for c in fake.calls))
        missing.refresh_from_db()
        kept.refresh_from_db()
        self.assertEqual(missing.receipt_url, invoice_url("in_backfill"))
        self.assertEqual(kept.receipt_url, "https://sweep-won.test/r")

    def test_dry_run_writes_nothing(self):
        from io import StringIO

        from django.core.management import call_command

        row = record(stripe_invoice_id="in_dry")
        with fake_stripe():
            call_command("backfill_receipt_urls", "--dry-run", stdout=StringIO())
        row.refresh_from_db()
        self.assertIsNone(row.receipt_url)


# ---------------------------------------------------------------------------
# Failure / recovery through the real webhook path
# ---------------------------------------------------------------------------


class WebhookReceiptRecoveryTests(TransactionTestCase, OverageFixture):
    reset_sequences = True

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build(email="receipt.recovery@billing.test")

    def deliver(self, event_id, session):
        event = {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {"object": session},
        }
        _, token = _claim_stripe_event(event)
        return _run_handler_inline(
            event, self.handler(), token, log_prefix="receipt recovery test"
        )

    def handler(self):
        from billing.stripe_service import StripeWebhookHandler

        return StripeWebhookHandler.handle_checkout_completed

    def test_worker_lost_before_receipt_task_is_healed_by_sweep(self):
        session = self.checkout_session(
            self.wallet, self.plan, payment_intent="pi_lost"
        )
        # Every task is dropped: the receipt task never runs.
        with fake_stripe() as fake, mock.patch(
            "celery.app.task.Task.delay", return_value=None
        ):
            response = self.deliver("evt_lost", session)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.granted_credits(self.wallet), BLOCK)
        self.assertEqual(
            StripeEvent.objects.get(stripe_event_id="evt_lost").status,
            StripeEventStatus.SUCCEEDED,
        )
        txn = BillingTransaction.objects.get(stripe_payment_intent_id="pi_lost")
        self.assertIsNone(txn.receipt_url)

        with fake_stripe():
            counts = receipts.sweep_missing_receipt_urls(
                now=timezone.now()
                + receipts.RECEIPT_SWEEP_MIN_AGE
                + timedelta(minutes=1)
            )
        self.assertEqual(counts["filled"], 1)
        txn.refresh_from_db()
        self.assertEqual(txn.receipt_url, pi_receipt("pi_lost"))

    def test_broker_down_at_commit_grant_succeeds(self):
        from billing.tasks import fill_billing_transaction_receipt_url

        session = self.checkout_session(
            self.wallet, self.plan, payment_intent="pi_nobroker"
        )
        with fake_stripe() as fake, mock.patch.object(
            fill_billing_transaction_receipt_url,
            "delay",
            side_effect=KombuOperationalError("broker unreachable"),
        ):
            response = self.deliver("evt_nobroker", session)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.granted_credits(self.wallet), BLOCK)
        self.assertEqual(
            StripeEvent.objects.get(stripe_event_id="evt_nobroker").status,
            StripeEventStatus.SUCCEEDED,
        )

    def test_stripe_down_during_receipt_task_grant_succeeds(self):
        session = self.checkout_session(
            self.wallet, self.plan, payment_intent="pi_sdown"
        )
        error = (503, {"error": {"type": "api_error", "message": "unavailable"}})
        with fake_stripe(receipt_error=error), run_receipt_tasks_inline():
            response = self.deliver("evt_sdown", session)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.granted_credits(self.wallet), BLOCK)
        self.assertIsNone(
            BillingTransaction.objects.get(
                stripe_payment_intent_id="pi_sdown"
            ).receipt_url
        )

    def test_malformed_stripe_reply_leaves_grant_intact_and_is_backfilled(self):
        """
        The behaviour fix-flaky-test's dropped P2 was to have guaranteed: a
        reply this code did not expect (here an unexpanded latest_charge,
        which raises AttributeError on the old inline path) must fail closed.
        The grant stands, the link is left null, and the sweep fills it.
        """
        session = self.checkout_session(
            self.wallet, self.plan, payment_intent="pi_malformed"
        )

        with fake_stripe() as fake, run_receipt_tasks_inline():
            original = fake._route
            fake._route = lambda call, post_data: (
                (
                    200,
                    {
                        "id": "pi_malformed",
                        "object": "payment_intent",
                        "latest_charge": "ch_not_expanded",
                    },
                )
                if call.is_receipt_lookup
                else original(call, post_data)
            )
            with self.assertLogs("billing.receipts", "ERROR"):
                response = self.deliver("evt_malformed", session)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.granted_credits(self.wallet), BLOCK)
        self.assertEqual(
            StripeEvent.objects.get(stripe_event_id="evt_malformed").status,
            StripeEventStatus.SUCCEEDED,
        )
        txn = BillingTransaction.objects.get(stripe_payment_intent_id="pi_malformed")
        self.assertIsNone(txn.receipt_url)

        # Stripe answers properly on the next sweep.
        with fake_stripe():
            counts = receipts.sweep_missing_receipt_urls(
                now=timezone.now()
                + receipts.RECEIPT_SWEEP_MIN_AGE
                + timedelta(minutes=1)
            )
        self.assertEqual(counts["filled"], 1)
        txn.refresh_from_db()
        self.assertEqual(txn.receipt_url, pi_receipt("pi_malformed"))

    def test_crash_after_stripe_answers_but_before_the_local_write(self):
        """
        The ordering that used to lose money, now inverted: the Stripe call
        happens AFTER the grant is durable. A process killed between Stripe
        answering and the local UPDATE can therefore only lose the link.

        App DB after: grant present, event SUCCEEDED, receipt_url NULL.
        Stripe after: unchanged - the only call made was a GET.
        """
        session = self.checkout_session(
            self.wallet, self.plan, payment_intent="pi_crash"
        )

        with fake_stripe() as fake, run_receipt_tasks_inline():
            with mock.patch.object(
                receipts.BillingTransaction.objects,
                "filter",
                side_effect=_fail_on_second_call(
                    receipts.BillingTransaction.objects.filter
                ),
            ):
                response = self.deliver("evt_crash", session)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.granted_credits(self.wallet), BLOCK)
        self.assertEqual(
            StripeEvent.objects.get(stripe_event_id="evt_crash").status,
            StripeEventStatus.SUCCEEDED,
        )
        txn = BillingTransaction.objects.get(stripe_payment_intent_id="pi_crash")
        self.assertIsNone(txn.receipt_url)
        assert_stripe_untouched(self, fake)

        with fake_stripe():
            receipts.sweep_missing_receipt_urls(
                now=timezone.now()
                + receipts.RECEIPT_SWEEP_MIN_AGE
                + timedelta(minutes=1)
            )
        txn.refresh_from_db()
        self.assertEqual(txn.receipt_url, pi_receipt("pi_crash"))

    def test_handler_failure_after_record_queues_no_receipt_task(self):
        session = self.checkout_session(
            self.wallet, self.plan, payment_intent="pi_boom"
        )
        queued = []
        with fake_stripe() as fake, mock.patch(
            "celery.app.task.Task.delay",
            autospec=True,
            side_effect=lambda task, *a, **k: queued.append(task.name),
        ), mock.patch(
            "billing.stripe_service.logger.info",
            side_effect=RuntimeError("crash after the record, before commit"),
        ):
            response = self.deliver("evt_boom", session)

        self.assertEqual(response.status_code, 500)
        self.assertNotIn(RECEIPT_TASK_NAME, queued)
        self.assertEqual(fake.calls, [])
        self.assertEqual(self.granted_credits(self.wallet), 0)
        self.assertFalse(
            BillingTransaction.objects.filter(
                stripe_payment_intent_id="pi_boom"
            ).exists()
        )


class ReceiptIsolationTests(TransactionTestCase, OverageFixture):
    """
    Gate 9: a receipt link belongs to exactly one purchase. Positive and
    negative direction, with a second teacher's identical-shaped purchase
    present throughout.
    """

    reset_sequences = True

    def setUp(self):
        self.plan = make_plan()
        self.mine, self.my_wallet = self.build(email="mine@isolation.test")
        self.theirs, self.their_wallet = self.build(email="theirs@isolation.test")

    def test_link_lands_only_on_its_own_row(self):
        from billing.stripe_service import StripeWebhookHandler

        for wallet, pi, event_id in (
            (self.my_wallet, "pi_mine", "evt_iso_mine"),
            (self.their_wallet, "pi_theirs", "evt_iso_theirs"),
        ):
            event = {
                "id": event_id,
                "type": "checkout.session.completed",
                "data": {
                    "object": self.checkout_session(
                        wallet, self.plan, session_id=f"cs_{pi}", payment_intent=pi
                    )
                },
            }
            with fake_stripe(), run_receipt_tasks_inline():
                _, token = _claim_stripe_event(event)
                _run_handler_inline(
                    event,
                    StripeWebhookHandler.handle_checkout_completed,
                    token,
                    log_prefix="isolation test",
                )

        mine = BillingTransaction.objects.get(stripe_payment_intent_id="pi_mine")
        theirs = BillingTransaction.objects.get(stripe_payment_intent_id="pi_theirs")
        self.assertEqual(mine.receipt_url, pi_receipt("pi_mine"))
        self.assertEqual(theirs.receipt_url, pi_receipt("pi_theirs"))
        self.assertEqual(mine.user_id, self.mine.id)
        self.assertEqual(theirs.user_id, self.theirs.id)
        # Negative direction: nothing of the other purchase appears anywhere
        # in this row, and each wallet got exactly its own block.
        self.assertNotIn("pi_theirs", str(mine.__dict__))
        self.assertNotIn("pi_mine", str(theirs.__dict__))
        self.assertEqual(self.granted_credits(self.my_wallet), BLOCK)
        self.assertEqual(self.granted_credits(self.their_wallet), BLOCK)

    def test_fill_writes_one_row_only(self):
        mine = record(stripe_payment_intent_id="pi_only_mine", user=self.mine)
        theirs = record(stripe_payment_intent_id="pi_only_theirs", user=self.theirs)
        with fake_stripe():
            receipts.fill_receipt_url(mine.pk)
        mine.refresh_from_db()
        theirs.refresh_from_db()
        self.assertEqual(mine.receipt_url, pi_receipt("pi_only_mine"))
        self.assertIsNone(theirs.receipt_url)


# ---------------------------------------------------------------------------
# Concurrency (real threads, real Postgres)
# ---------------------------------------------------------------------------


#: Postgres refuses a new backend past max_connections, and these tests open
#: one connection per thread. With several test runs sharing one server that
#: shows up as OperationalError("too many clients"), which is the machine
#: being full, not the code being wrong. Named explicitly so nobody reads it
#: as a product failure or retries it away.
DB_CAPACITY_MARKERS = ("too many clients", "remaining connection slots")


def assert_not_db_capacity(test, errors):
    for exc in errors:
        if any(marker in str(exc) for marker in DB_CAPACITY_MARKERS):
            test.fail(
                f"Postgres ran out of connection slots ({exc}). This test needs "
                f"{THREADS} simultaneous connections; other test runs on this "
                f"machine are using the server's max_connections. Re-run it in "
                f"a booked slot - do not treat this as a pass or a flake."
            )


def run_threads(count, fn):
    barrier = threading.Barrier(count)
    results, errors = [], []
    lock = threading.Lock()

    def body(i):
        try:
            barrier.wait(timeout=30)
            value = fn(i)
            with lock:
                results.append(value)
        except Exception as exc:  # noqa: BLE001 - asserted on by caller
            with lock:
                errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=body, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN_SECONDS)
    alive = [t.name for t in threads if t.is_alive()]
    return results, errors, alive


class ReceiptConcurrencyTests(TransactionTestCase, OverageFixture):
    reset_sequences = True

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build(email="receipt.concurrency@billing.test")

    def test_concurrent_fills_write_the_link_exactly_once(self):
        for round_no in range(ROUNDS):
            txn = record(stripe_payment_intent_id=f"pi_conc_{round_no}")
            with fake_stripe(receipt_delay_seconds=0.05):
                results, errors, alive = run_threads(
                    THREADS, lambda i, pk=txn.pk: receipts.fill_receipt_url(pk)
                )
            self.assertEqual(alive, [], f"round {round_no}: threads still running")
            assert_not_db_capacity(self, errors)
            self.assertEqual(errors, [], f"round {round_no}: {errors!r}")
            self.assertEqual(len(results), THREADS)
            self.assertEqual(
                results.count(FillOutcome.FILLED),
                1,
                f"round {round_no}: {results.count(FillOutcome.FILLED)} fills",
            )
            self.assertEqual(
                set(results) - {FillOutcome.FILLED}, {FillOutcome.ALREADY_SET}
            )
            txn.refresh_from_db()
            self.assertEqual(txn.receipt_url, pi_receipt(f"pi_conc_{round_no}"))

    def test_duplicate_webhook_deliveries_with_slow_stripe_grant_once(self):
        from billing.stripe_service import StripeWebhookHandler

        for round_no in range(ROUNDS):
            pi = f"pi_dup_{round_no}"
            session = self.checkout_session(
                self.wallet,
                self.plan,
                session_id=f"cs_dup_{round_no}",
                payment_intent=pi,
            )
            before = self.granted_credits(self.wallet)

            def deliver(i, round_no=round_no, session=session):
                # Distinct event ids carrying the same session: Stripe's own
                # documented duplicate shape, which the ledger cannot dedupe.
                event = {
                    "id": f"evt_dup_{round_no}_{i}",
                    "type": "checkout.session.completed",
                    "data": {"object": session},
                }
                _, token = _claim_stripe_event(event)
                return _run_handler_inline(
                    event,
                    StripeWebhookHandler.handle_checkout_completed,
                    token,
                    log_prefix="dup test",
                ).status_code

            with fake_stripe(
                receipt_delay_seconds=0.2
            ) as fake, run_receipt_tasks_inline():
                results, errors, alive = run_threads(THREADS, deliver)

            self.assertEqual(alive, [], f"round {round_no}: threads still running")
            assert_not_db_capacity(self, errors)
            self.assertEqual(errors, [], f"round {round_no}: {errors!r}")
            self.assertEqual(results, [200] * THREADS, f"round {round_no}")
            self.assertEqual(
                self.granted_credits(self.wallet) - before,
                BLOCK,
                f"round {round_no}: duplicate deliveries did not grant exactly once",
            )
            self.assertEqual(
                BillingTransaction.objects.filter(stripe_payment_intent_id=pi).count(),
                1,
            )
            self.assertEqual(
                BillingTransaction.objects.get(stripe_payment_intent_id=pi).receipt_url,
                pi_receipt(pi),
            )
            inside = [c.path for c in fake.receipt_lookups() if c.in_transaction]
            self.assertEqual(inside, [], f"round {round_no}: lookup inside transaction")
