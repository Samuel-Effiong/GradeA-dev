"""
P1c — automatic replay of FAILED Stripe webhook events, for one flow only.

The feature exists because asynchronous dispatch means Stripe is answered
200 before the handler runs, so a failed handler is never redelivered and a
paid overage purchase can leave a customer uncredited indefinitely. The
danger is the mirror image: most handlers move money irreversibly, so an
automatic replay that reaches one can refund or charge a customer twice.

These tests are therefore weighted towards what must NOT happen:

  * the allow-list's membership is pinned, so nobody widens it silently;
  * a widened allow-list STILL cannot reach a refund or a
    Subscription.modify handler (the second, independent gate);
  * every other event type and flow is denied, and the reason is recorded
    on the row, not only in the log;
  * a replay of an already-granted purchase grants nothing a second time;
  * concurrent sweeps, and a sweep racing a live redelivery, cannot both
    run the handler;
  * Stripe is never re-fetched to decide anything.
"""

import threading
from unittest import mock

from django.db import connection
from django.test import TestCase, TransactionTestCase

from billing import event_replay
from billing.event_replay import (
    AUTO_REPLAYABLE,
    MAX_AUTO_REPLAY_ATTEMPTS,
    VETTED_HANDLERS,
    ReplayOutcome,
    replay_safe_failed_events,
)
from billing.models import (
    BillingTransaction,
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    StripeEvent,
    StripeEventStatus,
)
from billing.stripe_service import StripeWebhookHandler
from billing.tests.test_overage_purchase_integrity import (
    BLOCK,
    OverageFixture,
    make_plan,
)
from billing.tests.test_receipt_lookup_outside_transaction import (
    run_receipt_tasks_inline,
)
from billing.tests.testing_fake_stripe import assert_stripe_untouched, fake_stripe

OVERAGE_KEY = ("checkout.session.completed", "overage_block_purchase_checkout")


class AllowListPinningTests(TestCase):
    """
    The allow-list is the whole safety argument, so its membership is
    pinned. If this test fails, someone widened the set that decides which
    money-moving handlers run unattended: that needs the Senior Manager's
    review and a written idempotency proof, not a green suite.
    """

    def test_exact_membership(self):
        self.assertEqual(set(AUTO_REPLAYABLE), {OVERAGE_KEY})

    def test_the_only_entry_maps_to_the_overage_flow_handler(self):
        self.assertIs(
            AUTO_REPLAYABLE[OVERAGE_KEY],
            StripeWebhookHandler._handle_overage_checkout_completed,
        )

    def test_vetted_handlers_is_exactly_that_handler(self):
        self.assertEqual(
            VETTED_HANDLERS,
            {"StripeWebhookHandler._handle_overage_checkout_completed"},
        )

    def test_no_refund_or_subscription_mutating_handler_is_listed(self):
        forbidden = {
            StripeWebhookHandler.handle_charge_refunded,
            StripeWebhookHandler.handle_dispute_event,
            StripeWebhookHandler.handle_subscription_updated,
            StripeWebhookHandler.handle_subscription_deleted,
            StripeWebhookHandler.handle_invoice_payment_succeeded,
            StripeWebhookHandler._handle_individual_upgrade_checkout_completed,
            StripeWebhookHandler._handle_license_create,
            StripeWebhookHandler._handle_license_overage_checkout_completed,
        }
        self.assertEqual(set(AUTO_REPLAYABLE.values()) & forbidden, set())


class ReplayFixture(OverageFixture):
    #: Set by each TestCase's setUp; declared so the type checker can see it.
    wallet: CreditWallet

    def failed_event(
        self,
        event_id,
        *,
        event_type="checkout.session.completed",
        session=None,
        flow="overage_block_purchase_checkout",
        payment_intent="pi_replay",
        attempts=0,
    ):
        session = session or self.checkout_session(
            self.wallet,
            self.plan,
            session_id=f"cs_{event_id}",
            payment_intent=payment_intent,
        )
        if flow is None:
            session = {
                **session,
                "metadata": {
                    k: v
                    for k, v in (session.get("metadata") or {}).items()
                    if k != "flow"
                },
            }
        elif flow != "overage_block_purchase_checkout":
            session = {**session, "metadata": {**session["metadata"], "flow": flow}}
        return StripeEvent.objects.create(
            stripe_event_id=event_id,
            event_type=event_type,
            payload={"object": session},
            status=StripeEventStatus.FAILED,
            auto_replay_attempts=attempts,
        )


class DenyByDefaultTests(TestCase, ReplayFixture):
    """Everything not allow-listed is skipped, and says why on the row."""

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build(email="deny@replay.test")

    def assert_skipped(self, row, outcome):
        row.refresh_from_db()
        self.assertEqual(row.status, StripeEventStatus.FAILED)
        self.assertIn(outcome.value, row.auto_replay_note)
        self.assertEqual(row.auto_replay_attempts, 0)

    def test_unknown_flow_on_an_allowed_event_type(self):
        row = self.failed_event("evt_upgrade", flow="individual_upgrade_checkout")
        counts = replay_safe_failed_events()
        self.assertEqual(counts[ReplayOutcome.NOT_ALLOW_LISTED.value], 1)
        self.assertEqual(counts[ReplayOutcome.REPLAYED.value], 0)
        self.assert_skipped(row, ReplayOutcome.NOT_ALLOW_LISTED)

    def test_missing_flow(self):
        row = self.failed_event("evt_noflow", flow=None)
        replay_safe_failed_events()
        self.assert_skipped(row, ReplayOutcome.NO_FLOW_IN_PAYLOAD)

    def test_disallowed_event_type_is_never_even_selected(self):
        row = self.failed_event("evt_refund", event_type="charge.refunded")
        counts = replay_safe_failed_events()
        self.assertEqual(sum(counts.values()), 0)
        row.refresh_from_db()
        self.assertEqual(row.status, StripeEventStatus.FAILED)
        self.assertEqual(row.auto_replay_note, "")

    def test_succeeded_events_are_never_replayed(self):
        row = self.failed_event("evt_done")
        StripeEvent.objects.filter(pk=row.pk).update(status=StripeEventStatus.SUCCEEDED)
        counts = replay_safe_failed_events()
        self.assertEqual(sum(counts.values()), 0)

    def test_attempts_are_capped(self):
        row = self.failed_event("evt_capped", attempts=MAX_AUTO_REPLAY_ATTEMPTS)
        counts = replay_safe_failed_events()
        self.assertEqual(sum(counts.values()), 0, "a capped event was selected again")
        row.refresh_from_db()
        self.assertEqual(row.auto_replay_attempts, MAX_AUTO_REPLAY_ATTEMPTS)


class ReplayOneGuardTests(TestCase, ReplayFixture):
    """
    Guards that only matter when a row changes between the sweep selecting
    it and replay_one acting on it - a concurrent sweep or a live
    redelivery. Driven through replay_one directly, with a row whose DB
    state has moved on from the copy in hand.
    """

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build(email="guards@replay.test")

    def spy(self):
        spy = mock.Mock(
            __qualname__="StripeWebhookHandler._handle_overage_checkout_completed"
        )
        return mock.patch.dict(event_replay.AUTO_REPLAYABLE, {OVERAGE_KEY: spy}), spy

    def test_exhausted_row_is_not_run_even_when_handed_over(self):
        row = self.failed_event("evt_exhausted", attempts=MAX_AUTO_REPLAY_ATTEMPTS)
        patcher, spy = self.spy()
        with patcher:
            outcome = event_replay.replay_one(row)
        self.assertEqual(outcome, ReplayOutcome.ATTEMPTS_EXHAUSTED)
        spy.assert_not_called()

    def test_row_already_claimed_by_a_live_worker_is_not_run(self):
        row = self.failed_event("evt_inflight")
        StripeEvent.objects.filter(pk=row.pk).update(
            status=StripeEventStatus.PROCESSING
        )
        patcher, spy = self.spy()
        with patcher:
            outcome = event_replay.replay_one(row)
        self.assertEqual(outcome, ReplayOutcome.CLAIM_LOST)
        spy.assert_not_called()

    def test_stale_copy_from_a_sweep_that_lost_the_race_is_not_run(self):
        """Another sweep replayed it, and it failed again, since we read it."""
        row = self.failed_event("evt_stale")
        StripeEvent.objects.filter(pk=row.pk).update(auto_replay_attempts=1)
        patcher, spy = self.spy()
        with patcher:
            outcome = event_replay.replay_one(row)  # row still says 0
        self.assertEqual(outcome, ReplayOutcome.CLAIM_LOST)
        spy.assert_not_called()

    def test_runs_exactly_the_mapped_handler_not_the_dispatcher(self):
        row = self.failed_event("evt_direct")
        patcher, spy = self.spy()
        with patcher, mock.patch.object(
            StripeWebhookHandler, "handle_checkout_completed"
        ) as dispatcher:
            outcome = event_replay.replay_one(row)
        self.assertEqual(outcome, ReplayOutcome.REPLAYED)
        spy.assert_called_once()
        dispatcher.assert_not_called()


class WidenedAllowListTests(TestCase, ReplayFixture):
    """
    The defence against a future careless edit: even if someone adds an
    unsafe entry to AUTO_REPLAYABLE, the handler must still not run,
    because it is not in VETTED_HANDLERS. Asserted on BEHAVIOUR - the
    handler is patched and proven never to be called - not on the list.
    """

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build(email="widened@replay.test")

    def test_widened_to_a_refund_handler_the_handler_is_never_called(self):
        row = self.failed_event("evt_widened_refund", event_type="charge.refunded")
        refund_handler = mock.Mock(
            __qualname__="StripeWebhookHandler.handle_charge_refunded"
        )

        with mock.patch.dict(
            event_replay.AUTO_REPLAYABLE,
            {("charge.refunded", "overage_block_purchase_checkout"): refund_handler},
        ), mock.patch.object(
            StripeWebhookHandler, "handle_charge_refunded"
        ) as real_refund, self.assertLogs(
            "billing.event_replay", "ERROR"
        ):
            counts = replay_safe_failed_events()

        refund_handler.assert_not_called()
        real_refund.assert_not_called()
        self.assertEqual(counts[ReplayOutcome.HANDLER_NOT_VETTED.value], 1)
        row.refresh_from_db()
        self.assertEqual(row.status, StripeEventStatus.FAILED)
        self.assertIn("handler_not_vetted", row.auto_replay_note)

    def test_widened_to_the_upgrade_flow_no_subscription_is_modified(self):
        row = self.failed_event(
            "evt_widened_upgrade", flow="individual_upgrade_checkout"
        )
        upgrade = mock.Mock(
            __qualname__="StripeWebhookHandler._handle_individual_upgrade_checkout_completed"
        )

        with fake_stripe() as fake, mock.patch.dict(
            event_replay.AUTO_REPLAYABLE,
            {("checkout.session.completed", "individual_upgrade_checkout"): upgrade},
        ), self.assertLogs("billing.event_replay", "ERROR"):
            replay_safe_failed_events()

        upgrade.assert_not_called()
        assert_stripe_untouched(self, fake)
        row.refresh_from_db()
        self.assertEqual(row.status, StripeEventStatus.FAILED)


class ReplayTheAllowedFlowTests(TransactionTestCase, ReplayFixture):
    reset_sequences = True

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build(email="allowed@replay.test")

    def test_stuck_paid_purchase_is_credited(self):
        row = self.failed_event("evt_stuck", payment_intent="pi_stuck")

        with fake_stripe() as fake, run_receipt_tasks_inline(), self.assertLogs(
            "billing.event_replay", "WARNING"
        ):
            counts = replay_safe_failed_events()

        self.assertEqual(counts[ReplayOutcome.REPLAYED.value], 1)
        self.assertEqual(self.granted_credits(self.wallet), BLOCK)
        self.assertEqual(self.purchase_ledger(self.wallet).count(), 1)
        row.refresh_from_db()
        self.assertEqual(row.status, StripeEventStatus.SUCCEEDED)
        self.assertEqual(row.auto_replay_attempts, 1)
        self.assertIn("replayed", row.auto_replay_note)
        # The receipt link is resolved after commit, as everywhere else.
        assert_stripe_untouched(self, fake)
        self.assertEqual(
            BillingTransaction.objects.filter(
                stripe_payment_intent_id="pi_stuck"
            ).count(),
            1,
        )

    def test_replaying_an_already_granted_purchase_grants_nothing_more(self):
        """The idempotency proof the allow-list rests on, exercised."""
        session = self.checkout_session(
            self.wallet, self.plan, session_id="cs_already", payment_intent="pi_already"
        )
        with fake_stripe(), run_receipt_tasks_inline():
            StripeWebhookHandler.handle_checkout_completed(session)
        self.assertEqual(self.granted_credits(self.wallet), BLOCK)

        row = self.failed_event("evt_already", session=session)
        with fake_stripe(), run_receipt_tasks_inline():
            counts = replay_safe_failed_events()

        self.assertEqual(counts[ReplayOutcome.REPLAYED.value], 1)
        self.assertEqual(
            self.granted_credits(self.wallet), BLOCK, "the replay double-granted"
        )
        self.assertEqual(
            CreditBucket.objects.filter(
                wallet=self.wallet, bucket_type=CreditBucketType.OVERAGE
            ).count(),
            1,
        )
        row.refresh_from_db()
        self.assertEqual(row.status, StripeEventStatus.SUCCEEDED)

    def test_a_replay_that_fails_again_stays_failed(self):
        row = self.failed_event("evt_failagain", payment_intent="pi_failagain")

        with mock.patch.object(
            StripeWebhookHandler,
            "_handle_overage_checkout_completed",
            side_effect=RuntimeError("still broken"),
        ), mock.patch.dict(
            event_replay.AUTO_REPLAYABLE,
            {OVERAGE_KEY: _vetted_raiser()},
        ), self.assertLogs(
            "billing.event_replay", "ERROR"
        ):
            counts = replay_safe_failed_events()

        self.assertEqual(counts[ReplayOutcome.FAILED_AGAIN.value], 1)
        row.refresh_from_db()
        self.assertEqual(row.status, StripeEventStatus.FAILED)
        self.assertEqual(row.auto_replay_attempts, 1)
        self.assertIn("still broken", row.last_error)
        self.assertEqual(self.granted_credits(self.wallet), 0)

    def test_stripe_is_never_re_fetched_to_decide(self):
        self.failed_event("evt_nofetch", payment_intent="pi_nofetch")
        with fake_stripe() as fake, mock.patch(
            "celery.app.task.Task.delay", return_value=None
        ):
            replay_safe_failed_events()
        self.assertEqual(fake.calls, [], "the replay called Stripe to decide or to act")


def _vetted_raiser():
    """A handler that fails, wearing the vetted handler's name."""

    def handler(session, metadata):
        raise RuntimeError("still broken")

    handler.__qualname__ = "StripeWebhookHandler._handle_overage_checkout_completed"
    return handler


class ReplayConcurrencyTests(TransactionTestCase, ReplayFixture):
    """Two sweeps, and a sweep racing a redelivery, must grant exactly once."""

    reset_sequences = True
    THREADS = 20
    ROUNDS = 10

    def setUp(self):
        self.plan = make_plan(max_blocks=100)
        self.user, self.wallet = self.build(email="conc@replay.test")

    def _run(self, fn, count):
        barrier = threading.Barrier(count)
        errors = []
        lock = threading.Lock()

        def body(i):
            try:
                barrier.wait(timeout=30)
                fn(i)
            except Exception as exc:  # noqa: BLE001 - asserted below
                with lock:
                    errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=body, args=(i,)) for i in range(count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertEqual([t.name for t in threads if t.is_alive()], [])
        return errors

    def test_concurrent_sweeps_replay_once(self):
        for round_no in range(self.ROUNDS):
            pi = f"pi_conc_{round_no}"
            self.failed_event(f"evt_conc_{round_no}", payment_intent=pi)
            before = self.granted_credits(self.wallet)

            with fake_stripe(), run_receipt_tasks_inline():
                errors = self._run(lambda i: replay_safe_failed_events(), self.THREADS)

            self.assertEqual(errors, [], f"round {round_no}: {errors!r}")
            self.assertEqual(
                self.granted_credits(self.wallet) - before,
                BLOCK,
                f"round {round_no}: concurrent sweeps did not grant exactly once",
            )
            self.assertEqual(
                StripeEvent.objects.get(stripe_event_id=f"evt_conc_{round_no}").status,
                StripeEventStatus.SUCCEEDED,
            )

    def test_sweep_racing_a_live_redelivery_grants_once(self):
        from billing.webhooks import _claim_stripe_event, _run_handler_inline

        for round_no in range(self.ROUNDS):
            event_id = f"evt_race_{round_no}"
            pi = f"pi_race_{round_no}"
            row = self.failed_event(event_id, payment_intent=pi)
            session = (row.payload or {})["object"]
            before = self.granted_credits(self.wallet)

            def redeliver(i, event_id=event_id, session=session):
                event = {
                    "id": event_id,
                    "type": "checkout.session.completed",
                    "data": {"object": session},
                }
                outcome, token = _claim_stripe_event(event)
                if token is None:
                    return
                _run_handler_inline(
                    event,
                    StripeWebhookHandler.handle_checkout_completed,
                    token,
                    log_prefix="race test",
                )

            def worker(i):
                if i % 2:
                    replay_safe_failed_events()
                else:
                    redeliver(i)

            with fake_stripe(), run_receipt_tasks_inline():
                errors = self._run(worker, self.THREADS)

            self.assertEqual(errors, [], f"round {round_no}: {errors!r}")
            self.assertEqual(
                self.granted_credits(self.wallet) - before,
                BLOCK,
                f"round {round_no}: a sweep and a redelivery both granted",
            )


class ReplayTaskWiringTests(TestCase, ReplayFixture):
    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build(email="wiring@replay.test")

    def test_beat_schedule_and_health_expectation(self):
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE["replay-safe-failed-stripe-events"]
        self.assertEqual(
            entry["task"], "billing.tasks.replay_safe_failed_stripe_events"
        )
        self.assertIn(
            "replay-safe-failed-stripe-events", settings.BEAT_HEALTH_EXPECTATIONS
        )

    def test_task_reports_nothing_eligible(self):
        from billing.tasks import replay_safe_failed_stripe_events

        self.assertIn("nothing eligible", replay_safe_failed_stripe_events.run())
