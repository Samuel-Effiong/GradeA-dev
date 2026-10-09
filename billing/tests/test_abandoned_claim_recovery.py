"""
billing/tests/test_abandoned_claim_recovery.py
==============================================
A dead worker must not permanently lose a Stripe event.

WHY THIS BECAME A GAP
---------------------
The claim ledger always had stale-claim stealing, and the hourly sweeper
always marked abandoned claims FAILED — which is a CLAIMABLE state. That
was sufficient while handlers ran inside the HTTP request: a dead worker
meant a non-2xx (or no) response, so Stripe redelivered and the redelivery
re-claimed the FAILED row.

Making dispatch asynchronous removed that safety net without anyone
noticing. The endpoint now claims the event and answers Stripe 200 in
milliseconds, so as far as STRIPE is concerned the delivery succeeded and
will never be sent again. If the Celery worker then dies, the row goes
FAILED and sits there: claimable in principle, but with no claimant left
in the world.

    before: worker dies -> Stripe redelivers -> recovered
    after:  worker dies -> Stripe already got its 200 -> stranded

THE DISCRIMINATOR
-----------------
Re-running a handler is not unconditionally safe — they call
stripe.Refund.create and Subscription.modify, which no database rollback
undoes. So recovery turns on ONE fact: did the handler actually start?

    handler_started_at IS NULL  the worker died between claiming and
                                doing anything at all. No Stripe call was
                                made. Re-dispatch is safe.
    handler_started_at SET      the worker may have got part-way through
                                irreversible calls. FAILED, for a human.

That is the whole design, and every test below is about protecting it.
"""

import threading
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from AutoGrader.testing.concurrency import run_concurrently
from billing.models import StripeEvent, StripeEventStatus
from billing.tasks import STRIPE_EVENT_MAX_RECOVERY_ATTEMPTS, sweep_stale_stripe_events
from billing.webhooks import STRIPE_EVENT_CLAIM_STALE_AFTER

HANDLED_TYPE = "invoice.payment_succeeded"


def make_event(
    *,
    event_id="evt_abandoned",
    status=StripeEventStatus.PROCESSING,
    claimed_age=None,
    handler_started_at=None,
    recovery_attempts=0,
    event_type=HANDLED_TYPE,
):
    """A claimed event, aged so the sweeper considers it abandoned."""
    if claimed_age is None:
        claimed_age = STRIPE_EVENT_CLAIM_STALE_AFTER + timedelta(minutes=5)
    return StripeEvent.objects.create(
        stripe_event_id=event_id,
        event_type=event_type,
        payload={"object": {"id": "in_1"}},
        status=status,
        claimed_at=timezone.now() - claimed_age,
        handler_started_at=handler_started_at,
        recovery_attempts=recovery_attempts,
        attempts=1,
    )


class DeadWorkerRecoveryTests(TestCase):
    """The headline: an event whose worker died is not lost."""

    def test_THE_REGRESSION_a_worker_dying_before_processing_does_not_lose_the_event(
        self,
    ):
        """
        The exact scenario async dispatch created: claimed, queued, worker
        killed, Stripe already told 200. Nothing but the sweeper can save
        this event.
        """
        event = make_event()

        with patch("billing.tasks.process_stripe_event.delay") as delay:
            sweep_stale_stripe_events()

        delay.assert_called_once()
        queued_id, _token = delay.call_args[0]
        self.assertEqual(queued_id, event.stripe_event_id)

        event.refresh_from_db()
        self.assertEqual(
            event.status,
            StripeEventStatus.PROCESSING,
            "the event was abandoned rather than handed to a live worker",
        )

    def test_the_redispatch_carries_the_NEW_claim_token(self):
        """
        Not the dead worker's. If the recovered task ran under the old
        token its terminal write would be rejected by the fence and the
        event would look abandoned all over again.
        """
        event = make_event()
        old_token = event.claimed_at

        with patch("billing.tasks.process_stripe_event.delay") as delay:
            sweep_stale_stripe_events()

        _id, token_iso = delay.call_args[0]
        event.refresh_from_db()
        self.assertNotEqual(token_iso, old_token.isoformat())
        self.assertEqual(token_iso, event.claimed_at.isoformat())

    def test_recovery_is_counted_so_it_can_be_bounded(self):
        event = make_event()

        with patch("billing.tasks.process_stripe_event.delay"):
            sweep_stale_stripe_events()

        event.refresh_from_db()
        self.assertEqual(event.recovery_attempts, 1)

    def test_the_claim_is_refreshed_so_a_redelivery_sees_a_LIVE_claim(self):
        """
        Mid-recovery, a Stripe redelivery must get 409 (busy), not start a
        second concurrent run of the same handler.
        """
        event = make_event()

        with patch("billing.tasks.process_stripe_event.delay"):
            sweep_stale_stripe_events()

        event.refresh_from_db()
        age = timezone.now() - event.claimed_at
        self.assertLess(
            age,
            STRIPE_EVENT_CLAIM_STALE_AFTER,
            "the recovered claim still looks stale, so a redelivery would "
            "steal it and run the handler twice at once",
        )


class MidHandlerDeathIsNotReplayedTests(TestCase):
    """The safety half: what must NOT be auto-recovered."""

    def test_a_worker_dying_DURING_the_handler_is_not_replayed(self):
        """
        It may already have called stripe.Refund.create. Replaying that
        automatically refunds a customer twice with nobody watching.
        """
        make_event(handler_started_at=timezone.now() - timedelta(hours=2))

        with patch("billing.tasks.process_stripe_event.delay") as delay:
            sweep_stale_stripe_events()

        self.assertFalse(
            delay.called,
            "an event that may have made irreversible Stripe calls was "
            "automatically replayed",
        )

    def test_it_is_marked_FAILED_for_a_human_instead(self):
        event = make_event(handler_started_at=timezone.now() - timedelta(hours=2))

        with patch("billing.tasks.process_stripe_event.delay"):
            sweep_stale_stripe_events()

        event.refresh_from_db()
        self.assertEqual(event.status, StripeEventStatus.FAILED)

    def test_the_reason_says_why_it_was_not_replayed(self):
        event = make_event(handler_started_at=timezone.now() - timedelta(hours=2))

        with patch("billing.tasks.process_stripe_event.delay"):
            sweep_stale_stripe_events()

        event.refresh_from_db()
        self.assertIn("NOT auto-replayed", event.last_error)


class SlowWorkerIsNotMistakenForDeadTests(TestCase):
    def test_a_claim_inside_the_window_is_left_completely_alone(self):
        event = make_event(claimed_age=timedelta(seconds=5))

        with patch("billing.tasks.process_stripe_event.delay") as delay:
            sweep_stale_stripe_events()

        self.assertFalse(delay.called)
        event.refresh_from_db()
        self.assertEqual(event.status, StripeEventStatus.PROCESSING)
        self.assertEqual(event.recovery_attempts, 0)

    def test_a_slow_worker_that_HAS_started_is_never_re_dispatched(self):
        """
        Even once it crosses the staleness line. A legitimately slow
        handler is still running; a second concurrent run is the one
        outcome worse than waiting.
        """
        make_event(handler_started_at=timezone.now())

        with patch("billing.tasks.process_stripe_event.delay") as delay:
            sweep_stale_stripe_events()

        self.assertFalse(delay.called)

    def test_the_original_worker_cannot_settle_a_row_it_no_longer_owns(self):
        """
        The fencing token, end to end: the dead worker comes back, finishes,
        and tries to write SUCCEEDED under its old claim.
        """
        from billing.webhooks import _finish_stripe_event

        event = make_event()
        stale_token = event.claimed_at

        with patch("billing.tasks.process_stripe_event.delay"):
            sweep_stale_stripe_events()

        _finish_stripe_event(
            event.stripe_event_id, stale_token, StripeEventStatus.SUCCEEDED
        )

        event.refresh_from_db()
        self.assertEqual(
            event.status,
            StripeEventStatus.PROCESSING,
            "a zombie worker settled a row that had been recovered from it",
        )


class RecoveryIsBoundedTests(TestCase):
    def test_repeated_reaping_stops_at_the_cap(self):
        make_event(recovery_attempts=STRIPE_EVENT_MAX_RECOVERY_ATTEMPTS)

        with patch("billing.tasks.process_stripe_event.delay") as delay:
            sweep_stale_stripe_events()

        self.assertFalse(
            delay.called,
            "an event that has died the same way every time is still being "
            "retried forever instead of being reported",
        )

    def test_past_the_cap_it_is_marked_FAILED_not_left_PROCESSING(self):
        event = make_event(recovery_attempts=STRIPE_EVENT_MAX_RECOVERY_ATTEMPTS)

        with patch("billing.tasks.process_stripe_event.delay"):
            sweep_stale_stripe_events()

        event.refresh_from_db()
        self.assertEqual(event.status, StripeEventStatus.FAILED)

    def test_each_sweep_advances_the_counter_by_exactly_one(self):
        event = make_event()

        with patch("billing.tasks.process_stripe_event.delay"):
            sweep_stale_stripe_events()
            event.refresh_from_db()
            StripeEvent.objects.filter(pk=event.pk).update(
                claimed_at=timezone.now()
                - STRIPE_EVENT_CLAIM_STALE_AFTER
                - timedelta(minutes=5)
            )
            sweep_stale_stripe_events()

        event.refresh_from_db()
        self.assertEqual(event.recovery_attempts, 2)

    def test_an_unhandled_event_type_is_not_re_dispatched(self):
        """Nothing to run; queueing it would just fail again in an hour."""
        event = make_event(event_type="invoice.upcoming")

        with patch("billing.tasks.process_stripe_event.delay") as delay:
            sweep_stale_stripe_events()

        self.assertFalse(delay.called)
        event.refresh_from_db()
        self.assertEqual(event.status, StripeEventStatus.FAILED)


class BrokerOutageDuringRecoveryTests(TestCase):
    def test_a_broker_failure_releases_the_claim_for_the_next_sweep(self):
        """
        The subtle one. If the queue is down and we keep the fresh claim,
        the row looks healthy for a full staleness window while nobody is
        working on it — so the claim goes back to being stale.
        """
        event = make_event()
        original_token = event.claimed_at

        with patch(
            "billing.tasks.process_stripe_event.delay",
            side_effect=OSError("broker unreachable"),
        ):
            sweep_stale_stripe_events()

        event.refresh_from_db()
        self.assertEqual(
            event.claimed_at,
            original_token,
            "the claim was left fresh after the queue rejected it, hiding "
            "the event from the next sweep",
        )

    def test_the_broker_failure_is_recorded_on_the_row(self):
        event = make_event()

        with patch(
            "billing.tasks.process_stripe_event.delay",
            side_effect=OSError("broker unreachable"),
        ):
            sweep_stale_stripe_events()

        event.refresh_from_db()
        self.assertIn("broker", event.last_error.lower())

    def test_the_next_sweep_recovers_it_once_the_broker_is_back(self):
        make_event()

        with patch(
            "billing.tasks.process_stripe_event.delay",
            side_effect=OSError("broker unreachable"),
        ):
            sweep_stale_stripe_events()

        with patch("billing.tasks.process_stripe_event.delay") as delay:
            sweep_stale_stripe_events()

        self.assertTrue(delay.called)


class SucceededEventsAreNeverTouchedTests(TestCase):
    def test_a_succeeded_event_is_not_recovered_however_old(self):
        event = make_event(status=StripeEventStatus.SUCCEEDED)

        with patch("billing.tasks.process_stripe_event.delay") as delay:
            sweep_stale_stripe_events()

        self.assertFalse(delay.called)
        event.refresh_from_db()
        self.assertEqual(event.status, StripeEventStatus.SUCCEEDED)


class ConcurrentRecoveryTests(TransactionTestCase):
    """Two sweepers, real threads, one event."""

    reset_sequences = True

    # The dispatch patch is entered ONCE, on the main thread, around the
    # race. It used to be entered inside each worker: patch() rebinds a
    # module attribute and is not thread-safe, so the first worker out could
    # restore the real .delay while the other was still sweeping.

    def test_two_sweepers_racing_recover_the_event_exactly_once(self):
        make_event()

        queued = []
        lock = threading.Lock()

        def fake_delay(event_id, token):
            with lock:
                queued.append((event_id, token))

        with patch("billing.tasks.process_stripe_event.delay", side_effect=fake_delay):
            _, errors = run_concurrently(
                lambda i: sweep_stale_stripe_events(), 2, test=self, name="sweeper"
            )

        self.assertEqual(errors, [], f"a sweeper raised: {errors!r}")
        self.assertEqual(
            len(queued),
            1,
            f"the event was re-dispatched {len(queued)} times — two workers "
            f"would run the same handler concurrently",
        )

    def test_the_loser_does_not_mark_the_row_FAILED(self):
        """
        The losing sweeper must not fall through and settle a row the
        winner just handed to a live worker.
        """
        event = make_event()

        with patch("billing.tasks.process_stripe_event.delay"):
            _, errors = run_concurrently(
                lambda i: sweep_stale_stripe_events(), 2, test=self, name="sweeper"
            )

        self.assertEqual(errors, [], f"a sweeper raised: {errors!r}")

        event.refresh_from_db()
        self.assertEqual(
            event.status,
            StripeEventStatus.PROCESSING,
            "the losing sweeper marked FAILED an event the winner had just "
            "re-dispatched to a live worker",
        )
