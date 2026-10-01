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
import time
from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import RequestFactory, TestCase, TransactionTestCase
from django.utils import timezone

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

    def test_replays_racing_live_duplicate_events_for_one_session_grant_once(self):
        """
        Stripe's documented duplicate: DIFFERENT event ids carrying the SAME
        checkout session. Each id is its own ledger row, so the claim cannot
        help; only the handler's wallet lock and its already-granted check
        stand between this and a double grant (1a's probe P1, H-62 N3).
        """
        from billing.webhooks import _claim_stripe_event, _run_handler_inline

        for round_no in range(self.ROUNDS):
            session = self.checkout_session(
                self.wallet,
                self.plan,
                session_id=f"cs_dup_{round_no}",
                payment_intent=f"pi_dup_{round_no}",
            )
            for copy in ("a", "b"):
                self.failed_event(f"evt_dup_{round_no}_{copy}", session=session)
            before = self.granted_credits(self.wallet)
            buckets_before = self.overage_buckets(self.wallet).count()

            def deliver_live(i, round_no=round_no, session=session):
                event = {
                    "id": f"evt_dup_{round_no}_live_{i}",
                    "type": "checkout.session.completed",
                    "data": {"object": session},
                }
                _, token = _claim_stripe_event(event)
                if token is None:
                    return
                _run_handler_inline(
                    event,
                    StripeWebhookHandler.handle_checkout_completed,
                    token,
                    log_prefix="duplicate-event test",
                )

            def worker(i):
                if i % 2:
                    replay_safe_failed_events()
                else:
                    deliver_live(i)

            with fake_stripe(), run_receipt_tasks_inline():
                errors = self._run(worker, self.THREADS)

            self.assertEqual(errors, [], f"round {round_no}: {errors!r}")
            self.assertEqual(
                self.granted_credits(self.wallet) - before,
                BLOCK,
                f"round {round_no}: replays and live duplicates granted more than once",
            )
            self.assertEqual(
                self.overage_buckets(self.wallet).count() - buckets_before, 1
            )


class StaleReplayClaimTests(TransactionTestCase, ReplayFixture):
    """
    A replay whose claim goes stale while its handler is still running:
    sweep_stale_stripe_events settles the row FAILED, the next replay
    re-claims it and runs the handler again, concurrently with the first.
    The claim no longer separates them; the wallet lock must (1a's probe
    P2, H-62 N3).
    """

    reset_sequences = True
    WAIT_SECONDS = 20

    def setUp(self):
        self.plan = make_plan(max_blocks=100)
        self.user, self.wallet = self.build(email="stale@replay.test")

    def _wait_for_a_lock_waiter_or(self, thread):
        """Until a session in this test database waits on a lock (the second
        replay at the wallet lock) or `thread` has finished (which it does at
        once if nothing makes it wait). Never a fixed sleep."""
        deadline = time.monotonic() + self.WAIT_SECONDS
        while time.monotonic() < deadline and thread.is_alive():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
                if cursor.fetchone()[0]:
                    return
            time.sleep(0.05)

    def test_a_replay_whose_claim_went_stale_mid_handler_grants_once(self):
        from billing.tasks import sweep_stale_stripe_events

        row = self.failed_event("evt_stale", payment_intent="pi_stale")
        entered, release = threading.Event(), threading.Event()
        results = {}

        def schedule(billing_transaction):
            # The first replay stops here: its grant is written, uncommitted.
            if threading.current_thread().name == "stale-first":
                entered.set()
                release.wait(timeout=30)

        def run(name, fn):
            try:
                results[name] = fn()
            except Exception as exc:  # noqa: BLE001 - asserted below
                results[name] = exc
            finally:
                connection.close()

        first = threading.Thread(
            target=run,
            args=(
                "first",
                lambda: event_replay.replay_one(StripeEvent.objects.get(pk=row.pk)),
            ),
            name="stale-first",
        )
        second = threading.Thread(
            target=run, args=("second", replay_safe_failed_events), name="stale-second"
        )

        with mock.patch(
            "billing.stripe_service.schedule_receipt_url_fill", side_effect=schedule
        ), fake_stripe():
            first.start()
            try:
                self.assertTrue(
                    entered.wait(self.WAIT_SECONDS),
                    "the first replay never reached its grant",
                )
                StripeEvent.objects.filter(pk=row.pk).update(
                    claimed_at=timezone.now() - timedelta(days=1)
                )
                sweep_stale_stripe_events.run()
                self.assertEqual(
                    StripeEvent.objects.get(pk=row.pk).status,
                    StripeEventStatus.FAILED,
                    "precondition: the sweep settled the stale claim",
                )
                second.start()
                self._wait_for_a_lock_waiter_or(second)
            finally:
                release.set()
            first.join(30)
            second.join(30)

        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertIsInstance(results["second"], dict, results)
        self.assertEqual(results["second"]["replayed"], 1, results)
        self.assertEqual(
            self.granted_credits(self.wallet),
            BLOCK,
            f"a stale replay and its re-claim both granted: {results!r}",
        )
        self.assertEqual(
            StripeEvent.objects.get(pk=row.pk).status, StripeEventStatus.SUCCEEDED
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


class StripeEventAdminLockdownTests(TestCase, ReplayFixture):
    """
    P1c's load-bearing control, pinned permanently.

    The auto-replay grants credits to whatever wallet a FAILED
    checkout.session.completed row's STORED payload names, and its
    idempotency guard keys on that payload's payment intent - so a fresh
    fake intent is not blocked by it. The only thing between "someone can
    write a StripeEvent row" and "credits are minted automatically" is that
    nobody can write one except an authenticated Stripe delivery. The red
    team confirmed the Django admin is that control (red-team-tenancy,
    1f11dcd: add, change and delete all refused over HTTP for a real
    superuser). If a future change makes the ledger writable in the admin,
    these tests fail before the hole ships.

    Driven over HTTP as a real superuser (created through the manager's
    create_superuser, both flags set), not by calling the permission
    methods, so a replaced ModelAdmin or a second registration is caught.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.plan = make_plan()
        self.user, self.wallet = self.build(email="ledger.owner@replay.test")
        self.admin_user = get_user_model().objects.create_superuser(
            email="root@replay.test",
            password="adminpass123",  # pragma: allowlist secret
            user_type="SUPER_ADMIN",
        )
        self.client.force_login(self.admin_user)
        self.row = self.failed_event("evt_admin_target", payment_intent="pi_admin")

    def url(self, action, *args):
        from django.urls import reverse

        return reverse(f"admin:billing_stripeevent_{action}", args=args)

    def test_superuser_cannot_add_a_ledger_row(self):
        before = StripeEvent.objects.count()
        self.assertEqual(self.client.get(self.url("add")).status_code, 403)
        response = self.client.post(
            self.url("add"),
            {
                "stripe_event_id": "evt_forged",
                "event_type": "checkout.session.completed",
                "status": StripeEventStatus.FAILED,
                "payload": '{"object": {"metadata": {"flow": '
                '"overage_block_purchase_checkout"}}}',
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(StripeEvent.objects.count(), before)

    def test_superuser_cannot_change_status_or_payload(self):
        original = StripeEvent.objects.values().get(pk=self.row.pk)
        response = self.client.post(
            self.url("change", self.row.pk),
            {
                "status": StripeEventStatus.FAILED,
                "payload": '{"object": {"metadata": {"flow": '
                '"overage_block_purchase_checkout", "wallet_id": "attacker"}}}',
            },
        )
        # A view-only admin answers the change URL with the read-only page
        # (200) or a 403 depending on the view permission; either way the
        # write must not happen.
        self.assertIn(response.status_code, (200, 302, 403))
        self.assertEqual(StripeEvent.objects.values().get(pk=self.row.pk), original)

    def test_superuser_cannot_delete_a_ledger_row(self):
        response = self.client.post(self.url("delete", self.row.pk), {"post": "yes"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(StripeEvent.objects.filter(pk=self.row.pk).exists())

    def test_every_writable_field_is_read_only(self):
        from django.contrib import admin

        model_admin = admin.site._registry[StripeEvent]
        editable = {
            f.name
            for f in StripeEvent._meta.get_fields()
            if getattr(f, "editable", False) and not f.auto_created
        }
        self.assertEqual(
            editable - set(model_admin.readonly_fields),
            {
                "auto_replay_attempts",
                "auto_replay_note",
                "recovery_attempts",
                "handler_started_at",
            }
            & editable,
            "a StripeEvent field other than the replay/recovery bookkeeping "
            "is editable in the admin",
        )
        self.assertFalse(
            model_admin.has_change_permission(RequestFactory().get("/admin/"))
        )
