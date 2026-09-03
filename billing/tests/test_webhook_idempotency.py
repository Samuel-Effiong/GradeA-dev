"""
billing/webhooks.py — the Stripe webhook idempotency ledger.

THE REGRESSION THESE LOCK
--------------------------
The dispatcher used to treat "a row exists" as "already handled", and
DELETED the row when a handler raised so Stripe's retry would start
fresh. That combination lost billing events permanently:

  1. Delivery A inserts evt_123 and starts working (slowly).
  2. Stripe times out waiting on A and redelivers evt_123 as B.
  3. B sees the row, concludes "duplicate", and returns 200.
  4. Stripe records the event as DELIVERED and never sends it again.
  5. A's handler fails, deletes the row, returns 500 — but Stripe has
     already stopped tracking A's attempt.

The customer paid, got no credits, and no record survived to explain it.
`test_in_flight_duplicate_is_not_reported_as_delivered` is that exact
story, and it is the most important test in this file.

A second hole had the same shape: gunicorn kills any request over
--timeout, `except Exception` cannot catch that, and the surviving row
made every later redelivery a "duplicate" forever —
`test_stale_claim_is_reclaimed_and_handler_runs` locks the recovery.

The fix: the ledger records WHAT HAPPENED (PROCESSING/SUCCEEDED/FAILED),
rows are never deleted, and only SUCCEEDED suppresses a redelivery.

This file is also the first test coverage billing/webhooks.py has ever
had, so it deliberately locks the PRESERVED behaviours too — signature
verification, the duplicate skip, 405 on GET — not just the new ones.
"""

import threading
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

import stripe as real_stripe
from django.core.checks import Error
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.checks import check_atomic_requests_disabled
from billing.management.commands.backfill_billing_transactions import (
    Command as BackfillCommand,
)
from billing.models import StripeEvent, StripeEventStatus
from billing.tasks import sweep_stale_stripe_events
from billing.webhooks import (
    STRIPE_EVENT_CLAIM_STALE_AFTER,
    STRIPE_RETRY_WINDOW,
    ClaimOutcome,
    _claim_stripe_event,
    _finish_stripe_event,
    _record_and_dispatch,
)

EVENT_ID = "evt_idempotency_test"
EVENT_TYPE = "checkout.session.completed"


def make_event(event_id=EVENT_ID, event_type=EVENT_TYPE):
    """The shape webhooks.py consumes: event["data"]["object"]."""
    return {
        "id": event_id,
        "type": event_type,
        "data": {"object": {"id": "cs_test_1", "metadata": {}}},
    }


def seed_event(status, *, claimed_at=None, completed_at=None, attempts=1, error=""):
    return StripeEvent.objects.create(
        stripe_event_id=EVENT_ID,
        event_type=EVENT_TYPE,
        payload=make_event()["data"],
        status=status,
        claimed_at=claimed_at,
        completed_at=completed_at,
        attempts=attempts,
        last_error=error,
    )


class ClaimStripeEventTests(TestCase):
    """The claim state machine."""

    def test_brand_new_event_is_claimed(self):
        outcome, token = _claim_stripe_event(make_event())

        self.assertIs(outcome, ClaimOutcome.CLAIMED)
        row = StripeEvent.objects.get(stripe_event_id=EVENT_ID)
        self.assertEqual(row.status, StripeEventStatus.PROCESSING)
        self.assertEqual(row.attempts, 1)
        self.assertEqual(row.claimed_at, token)

    def test_succeeded_event_is_not_reclaimed(self):
        seed_event(StripeEventStatus.SUCCEEDED, completed_at=timezone.now())

        outcome, token = _claim_stripe_event(make_event())

        self.assertIs(outcome, ClaimOutcome.ALREADY_SUCCEEDED)
        self.assertIsNone(token)
        row = StripeEvent.objects.get(stripe_event_id=EVENT_ID)
        self.assertEqual(row.status, StripeEventStatus.SUCCEEDED)
        self.assertEqual(row.attempts, 1, "a losing claim must not burn an attempt")

    def test_failed_event_is_reclaimable(self):
        seed_event(
            StripeEventStatus.FAILED,
            completed_at=timezone.now(),
            error="RuntimeError('boom')",
        )

        outcome, _ = _claim_stripe_event(make_event())

        self.assertIs(outcome, ClaimOutcome.CLAIMED)
        row = StripeEvent.objects.get(stripe_event_id=EVENT_ID)
        self.assertEqual(row.status, StripeEventStatus.PROCESSING)
        self.assertEqual(row.attempts, 2)
        self.assertEqual(row.last_error, "", "stale error text must be cleared")

    def test_fresh_processing_claim_blocks_a_second_delivery(self):
        seed_event(StripeEventStatus.PROCESSING, claimed_at=timezone.now())

        outcome, token = _claim_stripe_event(make_event())

        self.assertIs(outcome, ClaimOutcome.IN_FLIGHT)
        self.assertIsNone(token)

    def test_stale_processing_claim_is_stealable(self):
        seed_event(
            StripeEventStatus.PROCESSING,
            claimed_at=timezone.now()
            - STRIPE_EVENT_CLAIM_STALE_AFTER
            - timedelta(seconds=1),
        )

        outcome, _ = _claim_stripe_event(make_event())

        self.assertIs(outcome, ClaimOutcome.CLAIMED)
        self.assertEqual(StripeEvent.objects.get(stripe_event_id=EVENT_ID).attempts, 2)

    def test_staleness_boundary_just_inside_is_still_in_flight(self):
        """A claim one minute short of the cutoff belongs to a live worker."""
        seed_event(
            StripeEventStatus.PROCESSING,
            claimed_at=timezone.now()
            - STRIPE_EVENT_CLAIM_STALE_AFTER
            + timedelta(seconds=60),
        )

        outcome, _ = _claim_stripe_event(make_event())

        self.assertIs(outcome, ClaimOutcome.IN_FLIGHT)

    def test_processing_row_with_null_claimed_at_is_claimable(self):
        """
        Locks the nullable-exclude SQL semantics. A PROCESSING row with no
        claim timestamp cannot be attributed to a live worker, so it must
        fall on the claimable side — an ORM upgrade silently flipping this
        would strand events forever.
        """
        seed_event(StripeEventStatus.PROCESSING, claimed_at=None)

        outcome, _ = _claim_stripe_event(make_event())

        self.assertIs(outcome, ClaimOutcome.CLAIMED)

    def test_payload_and_type_are_refreshed_on_reclaim(self):
        seed_event(StripeEventStatus.FAILED, completed_at=timezone.now())

        _claim_stripe_event(make_event(event_type="invoice.payment_succeeded"))

        row = StripeEvent.objects.get(stripe_event_id=EVENT_ID)
        self.assertEqual(row.event_type, "invoice.payment_succeeded")


class FinishStripeEventTests(TestCase):
    """The fencing token on the terminal write."""

    def test_owner_settles_the_row(self):
        token = timezone.now()
        seed_event(StripeEventStatus.PROCESSING, claimed_at=token)

        self.assertTrue(
            _finish_stripe_event(EVENT_ID, token, StripeEventStatus.SUCCEEDED)
        )
        row = StripeEvent.objects.get(stripe_event_id=EVENT_ID)
        self.assertEqual(row.status, StripeEventStatus.SUCCEEDED)
        self.assertIsNotNone(row.completed_at)

    def test_stale_owner_cannot_stomp_the_new_owners_result(self):
        """
        A slow worker whose claim was stolen must not flip the thief's
        fresh SUCCEEDED back to FAILED — that would invite a replay of
        non-idempotent Stripe side effects.
        """
        stolen_token = timezone.now() - timedelta(hours=1)
        new_token = timezone.now()
        seed_event(StripeEventStatus.PROCESSING, claimed_at=new_token)

        self.assertFalse(
            _finish_stripe_event(
                EVENT_ID, stolen_token, StripeEventStatus.FAILED, error="late failure"
            )
        )
        row = StripeEvent.objects.get(stripe_event_id=EVENT_ID)
        self.assertEqual(row.status, StripeEventStatus.PROCESSING)
        self.assertEqual(row.last_error, "")

    def test_error_text_is_truncated(self):
        token = timezone.now()
        seed_event(StripeEventStatus.PROCESSING, claimed_at=token)

        _finish_stripe_event(
            EVENT_ID, token, StripeEventStatus.FAILED, error="x" * 5000
        )

        row = StripeEvent.objects.get(stripe_event_id=EVENT_ID)
        self.assertEqual(len(row.last_error), 2000)


class DispatchTests(TestCase):
    """_record_and_dispatch's HTTP contract and side effects."""

    def _dispatch(self, handler, event=None):
        with patch.dict(
            "billing.webhooks._EVENT_HANDLERS", {EVENT_TYPE: handler}, clear=False
        ):
            return _record_and_dispatch(event or make_event(), log_prefix="test")

    def test_success_marks_succeeded(self):
        calls = []

        response = self._dispatch(calls.append)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            StripeEvent.objects.get(stripe_event_id=EVENT_ID).status,
            StripeEventStatus.SUCCEEDED,
        )

    def test_handler_failure_keeps_the_row_as_failed(self):
        """THE BEHAVIOUR CHANGE: the row used to be deleted here."""

        def boom(_obj):
            raise RuntimeError("handler exploded")

        response = self._dispatch(boom)

        self.assertEqual(response.status_code, 500)
        row = StripeEvent.objects.get(stripe_event_id=EVENT_ID)
        self.assertEqual(row.status, StripeEventStatus.FAILED)
        self.assertIn("handler exploded", row.last_error)
        self.assertIsNotNone(row.completed_at)

    def test_duplicate_of_succeeded_event_skips_the_handler(self):
        """Preserved behaviour: genuine duplicates must not re-run."""
        seed_event(StripeEventStatus.SUCCEEDED, completed_at=timezone.now())
        calls = []

        response = self._dispatch(calls.append)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, [])

    def test_in_flight_event_returns_409_without_running_the_handler(self):
        seed_event(StripeEventStatus.PROCESSING, claimed_at=timezone.now())
        calls = []

        response = self._dispatch(calls.append)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(calls, [])

    def test_failed_event_is_retried_on_redelivery(self):
        seed_event(StripeEventStatus.FAILED, completed_at=timezone.now())
        calls = []

        response = self._dispatch(calls.append)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 1)
        row = StripeEvent.objects.get(stripe_event_id=EVENT_ID)
        self.assertEqual(row.status, StripeEventStatus.SUCCEEDED)
        self.assertEqual(row.attempts, 2)

    def test_stale_claim_is_reclaimed_and_handler_runs(self):
        """Recovers events that were permanently stuck before this change."""
        seed_event(
            StripeEventStatus.PROCESSING,
            claimed_at=timezone.now()
            - STRIPE_EVENT_CLAIM_STALE_AFTER
            - timedelta(minutes=1),
        )
        calls = []

        response = self._dispatch(calls.append)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            StripeEvent.objects.get(stripe_event_id=EVENT_ID).status,
            StripeEventStatus.SUCCEEDED,
        )

    def test_unknown_event_type_is_recorded_and_settled(self):
        event = make_event(event_type="some.unhandled.event")

        response = _record_and_dispatch(event, log_prefix="test")

        self.assertEqual(response.status_code, 200)
        row = StripeEvent.objects.get(stripe_event_id=EVENT_ID)
        self.assertEqual(row.status, StripeEventStatus.SUCCEEDED)
        self.assertEqual(row.event_type, "some.unhandled.event")


class WebhookRaceRegressionTests(TransactionTestCase):
    """
    Real concurrency, real DB connections. A TestCase's shared per-test
    transaction hides the race entirely, so these must be
    TransactionTestCase (same reasoning as
    students/tests_grading_idempotency.py).

    The waits below are deliberately generous. These are rendezvous
    timeouts, not assertions about speed — a loaded CI box (or a developer
    running two suites back to back) can take seconds just to schedule a
    thread, and a tight timeout turns that into a phantom failure of the
    most important test in the file. A real regression fails on the
    assertion, not on the clock.
    """

    THREAD_RENDEZVOUS_TIMEOUT = 30
    THREAD_JOIN_TIMEOUT = 60

    def test_only_one_of_two_concurrent_claims_succeeds(self):
        outcomes = []
        start_barrier = threading.Barrier(2)

        def attempt_claim():
            start_barrier.wait(timeout=self.THREAD_RENDEZVOUS_TIMEOUT)
            try:
                outcomes.append(_claim_stripe_event(make_event())[0])
            finally:
                connection.close()

        threads = [threading.Thread(target=attempt_claim) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=self.THREAD_JOIN_TIMEOUT)

        self.assertEqual(len(outcomes), 2)
        self.assertEqual(
            sorted(o.value for o in outcomes),
            ["claimed", "in_flight"],
            "exactly one concurrent delivery should have won the claim",
        )

    def test_in_flight_duplicate_is_not_reported_as_delivered(self):
        """
        THE MONEY TEST — the whole reason this change exists.

        Delivery A is mid-handler when Stripe redelivers as B. B must NOT
        answer 200: telling Stripe "delivered" for work that is still
        running (and about to fail) is how the event was lost forever.
        """
        handler_started = threading.Event()
        release_handler = threading.Event()
        handler_calls = []
        responses = {}

        def slow_failing_handler(obj):
            handler_calls.append(obj)
            handler_started.set()
            release_handler.wait(timeout=self.THREAD_RENDEZVOUS_TIMEOUT)
            raise RuntimeError("handler failed after Stripe gave up waiting")

        def delivery_a():
            try:
                with patch.dict(
                    "billing.webhooks._EVENT_HANDLERS",
                    {EVENT_TYPE: slow_failing_handler},
                    clear=False,
                ):
                    responses["a"] = _record_and_dispatch(
                        make_event(), log_prefix="A"
                    ).status_code
            finally:
                connection.close()

        thread_a = threading.Thread(target=delivery_a)
        thread_a.start()
        self.assertTrue(
            handler_started.wait(timeout=self.THREAD_RENDEZVOUS_TIMEOUT),
            "handler never started — the thread could not be scheduled, so "
            "the race was never actually set up (this is a harness problem, "
            "not a regression)",
        )

        # Stripe's redelivery arrives while A is still inside the handler.
        responses["b"] = _record_and_dispatch(make_event(), log_prefix="B").status_code

        self.assertEqual(
            responses["b"],
            409,
            "a redelivery arriving mid-flight must NOT be told the event was "
            "delivered — 200 here is the permanent-loss bug",
        )
        self.assertEqual(len(handler_calls), 1, "the handler must not run twice")

        release_handler.set()
        thread_a.join(timeout=self.THREAD_JOIN_TIMEOUT)
        self.assertFalse(thread_a.is_alive(), "delivery A never finished")
        self.assertEqual(responses["a"], 500)

        # The row survives, recording the failure — it used to be deleted.
        row = StripeEvent.objects.get(stripe_event_id=EVENT_ID)
        self.assertEqual(row.status, StripeEventStatus.FAILED)

        # And Stripe's next retry actually does the work.
        recovered = []
        with patch.dict(
            "billing.webhooks._EVENT_HANDLERS",
            {EVENT_TYPE: recovered.append},
            clear=False,
        ):
            final = _record_and_dispatch(make_event(), log_prefix="C")

        self.assertEqual(final.status_code, 200)
        self.assertEqual(len(recovered), 1)
        row.refresh_from_db()
        self.assertEqual(row.status, StripeEventStatus.SUCCEEDED)
        self.assertEqual(row.attempts, 2)


class WebhookEndpointTests(TestCase):
    """
    HTTP-level coverage of both endpoints. billing/webhooks.py had none
    before this change, so signature handling is locked here too.
    """

    def _post(self, url_name="stripe-webhook"):
        return self.client.post(
            reverse(url_name),
            data="{}",
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=deadbeef",
        )

    def test_valid_event_is_processed(self):
        calls = []
        with patch.object(
            real_stripe.Webhook, "construct_event", return_value=make_event()
        ), patch.dict(
            "billing.webhooks._EVENT_HANDLERS", {EVENT_TYPE: calls.append}, clear=False
        ):
            response = self._post()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            StripeEvent.objects.get(stripe_event_id=EVENT_ID).status,
            StripeEventStatus.SUCCEEDED,
        )

    def test_invalid_payload_is_rejected_without_touching_the_ledger(self):
        with patch.object(
            real_stripe.Webhook, "construct_event", side_effect=ValueError("bad json")
        ):
            response = self._post()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            StripeEvent.objects.count(),
            0,
            "an unverified request must never create a ledger row",
        )

    def test_bad_signature_is_rejected_without_touching_the_ledger(self):
        with patch.object(
            real_stripe.Webhook,
            "construct_event",
            side_effect=real_stripe.error.SignatureVerificationError("nope", "sig"),
        ):
            response = self._post()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(StripeEvent.objects.count(), 0)

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get(reverse("stripe-webhook")).status_code, 405)

    def test_thin_webhook_shares_the_same_ledger_behaviour(self):
        calls = []
        with patch.object(
            real_stripe.Webhook, "construct_event", return_value={"id": EVENT_ID}
        ), patch.object(
            real_stripe.Event, "retrieve", return_value=make_event()
        ), patch.dict(
            "billing.webhooks._EVENT_HANDLERS", {EVENT_TYPE: calls.append}, clear=False
        ):
            response = self._post("stripe-webhook-thin")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 1)

    def test_thin_webhook_retrieve_failure_creates_no_row(self):
        with patch.object(
            real_stripe.Webhook, "construct_event", return_value={"id": EVENT_ID}
        ), patch.object(
            real_stripe.Event, "retrieve", side_effect=RuntimeError("stripe down")
        ):
            response = self._post("stripe-webhook-thin")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            StripeEvent.objects.count(),
            0,
            "no row should exist for work that was never attempted",
        )


class SweepStaleStripeEventsTests(TestCase):
    def test_task_runs_with_no_arguments(self):
        """
        Signature smoke test — this exact class of bug (a bound task
        missing bind=True) already shipped once here; see
        billing/tests/test_billing_tasks.py.
        """
        self.assertIn("Stripe event sweep", sweep_stale_stripe_events.apply().get())

    def test_stale_claim_is_settled_as_failed(self):
        seed_event(
            StripeEventStatus.PROCESSING,
            claimed_at=timezone.now()
            - STRIPE_EVENT_CLAIM_STALE_AFTER
            - timedelta(minutes=1),
        )

        summary = sweep_stale_stripe_events.apply().get()

        row = StripeEvent.objects.get(stripe_event_id=EVENT_ID)
        self.assertEqual(row.status, StripeEventStatus.FAILED)
        self.assertIn("abandoned", row.last_error)
        self.assertIn("1 abandoned claim(s)", summary)

    def test_fresh_claim_and_succeeded_rows_are_left_alone(self):
        fresh = seed_event(StripeEventStatus.PROCESSING, claimed_at=timezone.now())
        StripeEvent.objects.create(
            stripe_event_id="evt_done",
            event_type=EVENT_TYPE,
            status=StripeEventStatus.SUCCEEDED,
            completed_at=timezone.now(),
        )

        sweep_stale_stripe_events.apply().get()

        fresh.refresh_from_db()
        self.assertEqual(fresh.status, StripeEventStatus.PROCESSING)
        self.assertEqual(
            StripeEvent.objects.get(stripe_event_id="evt_done").status,
            StripeEventStatus.SUCCEEDED,
        )

    def test_failures_are_split_by_stripes_retry_window(self):
        seed_event(
            StripeEventStatus.FAILED,
            completed_at=timezone.now() - STRIPE_RETRY_WINDOW - timedelta(hours=1),
        )

        summary = sweep_stale_stripe_events.apply().get()

        self.assertIn("1 FAILED past it", summary)


class ReplayStripeEventsCommandTests(TestCase):
    """
    The human-gated repair path. It must never act without --apply, and
    must never touch an event that already succeeded.
    """

    def _run(self, *args):
        out = StringIO()
        call_command("replay_stripe_events", *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_dry_run_is_the_default_and_changes_nothing(self):
        seed_event(StripeEventStatus.FAILED, completed_at=timezone.now())
        calls = []

        with patch.dict(
            "billing.webhooks._EVENT_HANDLERS", {EVENT_TYPE: calls.append}, clear=False
        ):
            output = self._run()

        self.assertEqual(calls, [], "a dry run must not execute any handler")
        self.assertIn("DRY RUN", output)
        self.assertIn("WOULD REPLAY", output)
        self.assertEqual(
            StripeEvent.objects.get(stripe_event_id=EVENT_ID).status,
            StripeEventStatus.FAILED,
        )

    def test_apply_replays_the_stored_payload_and_settles_the_row(self):
        seed_event(StripeEventStatus.FAILED, completed_at=timezone.now())
        calls = []

        with patch.dict(
            "billing.webhooks._EVENT_HANDLERS", {EVENT_TYPE: calls.append}, clear=False
        ):
            output = self._run("--apply")

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["id"], "cs_test_1", "the stored payload is replayed")
        self.assertIn("REPLAYED", output)
        row = StripeEvent.objects.get(stripe_event_id=EVENT_ID)
        self.assertEqual(row.status, StripeEventStatus.SUCCEEDED)
        self.assertEqual(row.last_error, "")

    def test_succeeded_events_are_never_replayed(self):
        """Replaying one would double-grant credits."""
        seed_event(StripeEventStatus.SUCCEEDED, completed_at=timezone.now())
        calls = []

        with patch.dict(
            "billing.webhooks._EVENT_HANDLERS", {EVENT_TYPE: calls.append}, clear=False
        ):
            output = self._run("--apply")

        self.assertEqual(calls, [])
        self.assertIn("No FAILED Stripe events", output)

    def test_a_replay_that_fails_again_stays_failed(self):
        seed_event(StripeEventStatus.FAILED, completed_at=timezone.now())

        def boom(_obj):
            raise RuntimeError("still broken")

        with patch.dict(
            "billing.webhooks._EVENT_HANDLERS", {EVENT_TYPE: boom}, clear=False
        ):
            output = self._run("--apply")

        self.assertIn("FAILED AGAIN", output)
        row = StripeEvent.objects.get(stripe_event_id=EVENT_ID)
        self.assertEqual(row.status, StripeEventStatus.FAILED)
        self.assertIn("still broken", row.last_error)

    def test_event_id_filter_selects_a_single_event(self):
        seed_event(StripeEventStatus.FAILED, completed_at=timezone.now())
        StripeEvent.objects.create(
            stripe_event_id="evt_other",
            event_type=EVENT_TYPE,
            payload=make_event()["data"],
            status=StripeEventStatus.FAILED,
            completed_at=timezone.now(),
        )

        output = self._run("--event-id", EVENT_ID)

        self.assertIn(EVENT_ID, output)
        self.assertNotIn("evt_other", output)


class BackfillCommandExcludesUnsucceededEventsTests(TestCase):
    """
    Cross-file guard. backfill_billing_transactions derives invoice rows
    from stored webhook payloads. That was safe only while failures
    deleted their rows; now that FAILED/PROCESSING rows survive, deriving
    from one would invent an invoice for money that never moved.
    """

    def test_only_succeeded_events_are_backfilled(self):
        StripeEvent.objects.create(
            stripe_event_id="evt_ok",
            event_type="invoice.payment_succeeded",
            payload={"object": {"id": "in_1"}},
            status=StripeEventStatus.SUCCEEDED,
            completed_at=timezone.now(),
        )
        StripeEvent.objects.create(
            stripe_event_id="evt_failed",
            event_type="invoice.payment_succeeded",
            payload={"object": {"id": "in_2"}},
            status=StripeEventStatus.FAILED,
            completed_at=timezone.now(),
        )
        StripeEvent.objects.create(
            stripe_event_id="evt_in_flight",
            event_type="invoice.payment_succeeded",
            payload={"object": {"id": "in_3"}},
            status=StripeEventStatus.PROCESSING,
            claimed_at=timezone.now(),
        )

        command = BackfillCommand()
        with patch.object(
            BackfillCommand, "_process_stripe_event", return_value=True
        ) as mock_process:
            processed = command._backfill_from_stripe_events(dry_run=True)

        self.assertEqual(processed, 1)
        considered = [
            call.args[0].stripe_event_id for call in mock_process.call_args_list
        ]
        self.assertEqual(considered, ["evt_ok"])


class AtomicRequestsCheckTests(TestCase):
    """
    ATOMIC_REQUESTS would make the claim invisible until the response
    commits, silently restoring the event-loss bug — and every test would
    still pass, because a TestCase wraps each test in a transaction
    anyway. Hence a system check rather than a comment.
    """

    def test_no_error_by_default(self):
        self.assertEqual(check_atomic_requests_disabled(None), [])

    def test_error_when_atomic_requests_is_enabled(self):
        databases = {"default": {"ENGINE": "x", "ATOMIC_REQUESTS": True}}
        with override_settings(DATABASES=databases):
            errors = check_atomic_requests_disabled(None)

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], Error)
        self.assertEqual(errors[0].id, "billing.E001")
