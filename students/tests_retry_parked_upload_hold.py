"""Probe (SM 2026-11-19, red tests only, no product change): a parked upload
retried by hand. `retry_item` relaunches it from its kept file with no door
check and no door hold (`_relaunch_upload`). The questions asked:

(a) A retry with no spendable credit: is it refused, and where?
(b) Two retries, or a retry plus a new batch, against a wallet that fits only
    one call: can both pass the gate and overspend?

The worker runs for real (one real billed call inside its metered unit, as
students.tests_parked_files_kept does); only the provider is patched. The
interleaving is deterministic: the second retry, or the new batch, runs
inside the first retry's provider call, after its gate and hold and before
its charge, in a context with no active unit or refund scope (a nested unit
would join the first retry's unit, which a separate worker never does).

What the probe found on beta 157c3016b (red = a test that fails today):

(a) Holds on (production): the retry route accepts it (the siblings' holds
    keep the total above 0), and the worker's call is refused by its own fit
    check (Unit.before_call -> ensure_call_fits) before the provider; nothing
    is charged and the file is kept. RED: batch_credit_refusal reads it as a
    mid-batch stop because a sibling has started, so the retried file ends
    INSUFFICIENT_CREDITS_MID_BATCH and the session is stamped
    credits_exhausted_at, which stops a sibling that holds its own credits
    before its first call. N1's class: a file that could not get credits for
    itself stops the batch.
    Holds off at a total of 0: the retry route itself refuses (402).
(b) Holds on: no overspend. The first retry's call holds before the
    provider, so the second retry, or a new batch, is refused (or parked)
    and only one call is charged.
    Holds off (the emergency state): no overspend either (the charge itself
    refuses), but RED: both retries pass the balance check and both reach
    the provider; the second is refused its charge, so we pay for AI work
    that is never billed.

The fix (owner Q7, 2026-11-20; bug (a)): an upload retried from its kept
file takes a door hold first, with holds on, for the door's own figure on
the kept bytes (_estimate_for). One that does not fit is the door's usual
402 with nothing changed: the item stays parked, its file kept, the session
unstamped, and no sibling stops. retry-failed takes the holds in item order
and skips (INSUFFICIENT_CREDITS) what does not fit. Bug (b), flag off, is
not fixed here (SM: report only): its test is an expected failure.

    python manage.py test students.tests_retry_parked_upload_hold
"""

import contextvars
import uuid
from types import SimpleNamespace
from typing import Any, Callable, Optional
from unittest import expectedFailure
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from ai_processor.services import AIProcessor
from billing import holds as credit_holds
from billing import refunds
from billing.errors import InsufficientCreditsError
from billing.models import AIJobType, CreditUsageLog, CreditWallet, HoldState
from billing.testing.holds import (
    ai_response,
    estimate_for,
    fund,
    holds,
    real_billed_call,
)
from students import item_retry, kept_files, task_tracking
from students.models import BackgroundProcessingTask, BackgroundTaskStatus, KeptUpload
from students.task_tracking import mark_processing_task_cancelled
from students.tests_parked_files_kept import PROVIDER, ParkedFixture

#: The real figure function, before any test patches it (read by name, so
#: the tests-first commit type-checks before the function exists).
KEPT_ESTIMATE: Callable[[Any], Optional[int]] = vars(item_retry)["_kept_estimate"]

PARKED = "INSUFFICIENT_CREDITS"
MID = "INSUFFICIENT_CREDITS_MID_BATCH"


def wallet_of(user):
    return CreditWallet.objects.get(user=user)


def in_a_fresh_worker(fn):
    """Run `fn` as a separate worker would: same DB connection (the test's
    transaction), but no active metered unit and no refund scope."""

    def run():
        credit_holds._active_unit.set(None)
        refunds._active_task_ids.set(None)
        return fn()

    return contextvars.copy_context().run(run)


class _RetryFixture(ParkedFixture):
    """Retries launch the real worker at once; its call is real_billed_call
    (estimate `self.call`, actual usage `self.actual`)."""

    def start_worker_on_retry(self):
        self.call = estimate_for()
        self.actual = self.call
        self.provider_hook = None
        self.provider_calls = 0
        self.held_at_launch = []
        launch = patch(
            "students.item_retry.launch_processing_task", side_effect=self._run_now
        )
        launch.start()
        self.addCleanup(launch.stop)

        # The door's figure for a retried file is the worker's one call, as
        # the door's count is the task's calls (E2E F1); the kept bytes are
        # still read and converted (prepare_ai_content). Only the retry's
        # figure: a batch upload keeps the figures its test gives.
        def kept_estimate(item):
            with patch(
                "assignments.upload_door.upload_item_estimate",
                side_effect=lambda *a, **k: self.call,
            ):
                return KEPT_ESTIMATE(item)

        figure = patch.object(item_retry, "_kept_estimate", side_effect=kept_estimate)
        figure.start()
        self.addCleanup(figure.stop)

    def retry(self, item):
        """The route, with its launch sent at commit as in a real request
        (launch_at_door: no worker before the hold commits)."""
        with self.captureOnCommitCallbacks(execute=True):
            return super().retry(item)

    def retry_failed(self, session_id):
        with self.captureOnCommitCallbacks(execute=True):
            return self.api.post(
                reverse("task-retry-failed", kwargs={"session_id": str(session_id)}),
                {},
                format="json",
            )

    def _engine(self, *, request_user, processing_task_id, **kwargs):
        with holds().metered_unit(processing_task_id=processing_task_id, reason="t"):
            real_billed_call(request_user)
        return SimpleNamespace(id=uuid.uuid4())

    def _provider(self, *args, **kwargs):
        self.provider_calls += 1
        hook, self.provider_hook = self.provider_hook, None
        if hook is not None:
            in_a_fresh_worker(hook)
        return ai_response(self.actual)

    def _run_now(
        self, task_callable, processing_task, *args, _celery_task_id=None, **kwargs
    ):
        row = BackgroundProcessingTask.objects.get(pk=processing_task.pk)
        self.held_at_launch.append((row.pk, row.hold_state, row.hold_credits))
        celery_id = _celery_task_id or str(uuid.uuid4())
        BackgroundProcessingTask.objects.filter(pk=processing_task.pk).update(
            celery_task_id=celery_id
        )
        with patch(
            "assignments.tasks.upload_answers_engine", side_effect=self._engine
        ), patch.object(
            AIProcessor, PROVIDER, side_effect=self._provider
        ), self.captureOnCommitCallbacks(
            execute=True
        ):
            task_callable.apply(
                args=args, kwargs={**kwargs, "processing_task_id": processing_task.id}
            )
        return SimpleNamespace(id=celery_id)


# (a) ---------------------------------------------------------------------


class _RetryWithNoSpendableCredit(_RetryFixture):
    """Three files: f0 and f1 are queued and hold everything there is, f2 is
    parked. f0 has STARTED; f1 is dispatched and waiting for a worker. The
    teacher retries f2 with nothing spendable."""

    door = 4_000

    def setUp(self):
        self.build(2 * self.door, tag="retry-zero")
        data = self.upload(self.door, self.door, 3 * self.door)
        self.f0, self.f1, self.f2 = self.items(data)
        self.assertKeptParked(self.f2)
        BackgroundProcessingTask.objects.filter(pk=self.f0.pk).update(
            status=BackgroundTaskStatus.STARTED
        )
        self.start_worker_on_retry()

    def retry_f2(self):
        response = self.retry(self.f2)
        self.assertEqual(response.status_code, 202, response.content[:400])
        self.f2.refresh_from_db()

    def test_it_is_refused_before_any_charge(self):
        self.retry_f2()
        self.assertEqual(self.f2.status, BackgroundTaskStatus.FAILURE)
        self.assertIn(self.f2.reason_code, (PARKED, MID))
        self.assertFalse(CreditUsageLog.objects.exists(), "nothing is charged")
        row = KeptUpload.objects.get(processing_task=self.f2)
        self.assertIsNone(row.purged_at, "the file survives the refusal")

    def test_the_refused_retry_stays_a_parked_file(self):
        """N1's rule: a file that could not get credits for itself is parked
        (INSUFFICIENT_CREDITS, kept, retryable), not a mid-batch stop."""
        self.retry_f2()
        self.assertEqual(self.f2.reason_code, PARKED)

    def test_the_refused_retry_does_not_stop_siblings_holding_their_credits(self):
        """f1 holds its own credits; the retry of another file must not mark
        the session exhausted and stop it before its first call (N1 class)."""
        self.retry_f2()
        self.f2.batch_session.refresh_from_db()
        self.assertIsNone(self.f2.batch_session.credits_exhausted_at)
        task_tracking.ensure_batch_has_credits(self.f1.id)  # must not raise


@override_settings(BILLING_HOLDS_ENABLED=True)
class RetryWithNoSpendableCreditHoldsOn(_RetryWithNoSpendableCredit, TestCase):
    """Q7: the access check passes (it reads the total, which the siblings'
    holds still count); the retry's door hold does not fit, so the route
    answers the door's usual 402 and changes nothing."""

    def retry_f2(self):
        before = KeptUpload.objects.get(processing_task=self.f2)
        self.refusal = self.retry(self.f2)
        self.assertEqual(self.refusal.status_code, 402, self.refusal.content[:400])
        self.f2.refresh_from_db()
        self.assertEqual(self.f2.retry_count, 0, "the claim rolled back")
        self.assertEqual(self.f2.status, BackgroundTaskStatus.FAILURE)
        self.assertIsNone(self.f2.job_id)
        self.assertIsNone(self.f2.hold_state)
        self.assertEqual(KeptUpload.objects.get(processing_task=self.f2), before)

    def test_the_refusal_is_the_door_hold_before_any_launch(self):
        refusals = []
        real = credit_holds.ensure_call_fits

        def fits(*args, **kwargs):
            try:
                return real(*args, **kwargs)
            except InsufficientCreditsError as exc:
                refusals.append(exc)
                raise

        with patch.object(credit_holds, "ensure_call_fits", fits):
            self.retry_f2()
        self.assertEqual(refusals, [], "no worker call is ever tried")
        self.assertEqual(self.held_at_launch, [], "nothing is launched")
        self.assertEqual(self.provider_calls, 0)
        self.assertEqual(wallet_of(self.teacher).reserved_credits, 2 * self.door)
        self.assertEqual(holds().check_invariants(wallet_of(self.teacher)), [])

    def test_the_refusal_reads_the_doors_402(self):
        """The body the batch door answers when a hold does not fit
        (existing code and text, no new wording)."""
        from assignments.upload_door import DoorHold

        self.retry_f2()
        door = DoorHold(self.teacher, self.assignment, 1).refusal_response(1).data
        body = self.refusal.json()
        self.assertEqual(body["message"], door["error"])
        # `reference` is each request's own id (the coded envelope,
        # 2026-11-26); every other key is the door's.
        shown = dict(body["error"]["field_errors"])
        self.assertTrue(shown.pop("reference"))
        self.assertEqual(shown, {k: v for k, v in door.items() if k != "reference"})

    def test_retry_failed_skips_it_and_stamps_nothing(self):
        response = self.retry_failed(self.f2.batch_session_id)
        self.assertEqual(response.status_code, 202, response.content[:400])
        data = response.json()["data"]
        self.assertEqual(data["retried"], [])
        self.assertEqual(
            data["skipped"],
            [{"item_id": str(self.f2.id), "reason_code": PARKED}],
        )
        self.f2.refresh_from_db()
        self.assertKeptParked(self.f2)
        self.f2.batch_session.refresh_from_db()
        self.assertIsNone(self.f2.batch_session.credits_exhausted_at)
        task_tracking.ensure_batch_has_credits(self.f1.id)  # must not raise


@override_settings(BILLING_HOLDS_ENABLED=False)
class RetryWithNoSpendableCreditHoldsOff(_RetryWithNoSpendableCredit, TestCase):
    """Flag off there are no holds: f0 and f1 hold nothing, so 'nothing
    spendable' is set by spending the wallet down to zero instead."""

    def setUp(self):
        super().setUp()
        wallet = wallet_of(self.teacher)
        wallet.consume_credits(
            amount=wallet.total_remaining_credits(),
            feature="Grading Assignment",
            task_type="grade_assignment",
            task_id=str(uuid.uuid4()),
        )
        self.assertEqual(wallet_of(self.teacher).total_remaining_credits(), 0)

    def retry_f2(self):
        """At a total of 0 the retry route itself refuses (402), before any
        claim: the item, its file and the session are untouched."""
        before = CreditUsageLog.objects.count()
        response = self.retry(self.f2)
        self.assertEqual(response.status_code, 402, response.content[:400])
        self.f2.refresh_from_db()
        self.assertEqual(self.f2.retry_count, 0, "not claimed")
        self.assertEqual(CreditUsageLog.objects.count(), before, "no new charge")

    def test_it_is_refused_before_any_charge(self):
        self.retry_f2()  # refused at the route
        self.assertEqual(self.f2.reason_code, PARKED)


# (b) ---------------------------------------------------------------------


class _TwoClaimsOnOneCall(_RetryFixture):
    """Two parked files, nothing else running; the wallet then fits exactly
    one worker call (`self.call`), and each call really costs `self.call`."""

    def setUp(self):
        self.build(10, tag="retry-two")
        data = self.upload(1, 5_000, 5_000)
        queued, self.p1, self.p2 = self.items(data)
        with self.captureOnCommitCallbacks(execute=True):
            mark_processing_task_cancelled(queued.id)
        self.start_worker_on_retry()
        wallet = wallet_of(self.teacher)
        fund(
            self.teacher, self.call + self.call // 2 - wallet.total_remaining_credits()
        )
        self.assertLess(
            wallet_of(self.teacher).total_remaining_credits(), 2 * self.call
        )

    def assertNoOverspend(self):
        wallet = wallet_of(self.teacher)
        self.assertGreaterEqual(wallet.total_remaining_credits(), 0)
        # One call can be charged across two buckets: count calls, not rows.
        calls = (
            CreditUsageLog.objects.filter(wallet__user=self.teacher, is_refunded=False)
            .values("task_id")
            .distinct()
        )
        self.assertLessEqual(calls.count(), 1, "only one call fits")

    def test_two_retries_never_overspend(self):
        self.provider_hook = lambda: self.retry(self.p2)
        self.retry(self.p1)
        self.assertNoOverspend()

    def test_two_retries_cannot_both_pass_the_gate(self):
        """Only one call fits, so only one may reach the provider: a second
        call that runs and is then refused its charge is AI work we pay for
        and never bill."""
        self.provider_hook = lambda: self.retry(self.p2)
        self.retry(self.p1)
        self.assertEqual(self.provider_calls, 1)


@override_settings(BILLING_HOLDS_ENABLED=True)
class TwoClaimsOnOneCallHoldsOn(_TwoClaimsOnOneCall, TestCase):
    def test_a_new_batch_during_the_retry_parks_instead_of_overspending(self):
        def new_batch():
            self.new = self.upload(1, self.call)

        self.provider_hook = new_batch
        self.retry(self.p1)
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.status, BackgroundTaskStatus.SUCCESS)
        statuses = [t["status"] for t in self.new["tasks"]]
        self.assertEqual(
            statuses, ["ACCEPTED", "PARKED"], "the retry's call hold is respected"
        )
        self.assertNoOverspend()
        self.assertEqual(holds().check_invariants(wallet_of(self.teacher)), [])


@override_settings(BILLING_HOLDS_ENABLED=False)
class TwoClaimsOnOneCallHoldsOff(_TwoClaimsOnOneCall, TestCase):
    """The flag-off (emergency) path: a balance check, then the charge after
    the call returns. No reservation in between."""

    @expectedFailure
    def test_two_retries_cannot_both_pass_the_gate(self):
        """Bug (b), not fixed here (SM 2026-11-20: report and propose only):
        flag off, both calls reach the provider and the second is refused
        its charge."""
        super().test_two_retries_cannot_both_pass_the_gate()


# Q7: the hold a retry takes, and retry-failed's running balance ------------


@override_settings(BILLING_HOLDS_ENABLED=True)
class RetryTakesTheDoorHold(_RetryFixture, TestCase):
    """Four files, nothing else running: f0 is queued and then cancelled
    (its hold returned), f1-f3 are parked. Then the wallet is funded."""

    def setUp(self):
        self.build(10, tag="retry-hold")
        data = self.upload(1, 5_000, 5_000, 5_000)
        queued, self.p1, self.p2, self.p3 = self.items(data)
        with self.captureOnCommitCallbacks(execute=True):
            mark_processing_task_cancelled(queued.id)
        self.session = self.p1.batch_session
        self.start_worker_on_retry()

    def top_up_to(self, credits):
        fund(self.teacher, credits - wallet_of(self.teacher).total_remaining_credits())

    def test_a_retry_that_fits_holds_the_doors_figure_before_its_worker(self):
        self.top_up_to(3 * self.call)
        response = self.retry(self.p1)
        self.assertEqual(response.status_code, 202, response.content[:400])
        self.assertEqual(self.held_at_launch, [(self.p1.pk, HoldState.HELD, self.call)])
        self.p1.refresh_from_db()
        self.assertEqual(self.p1.status, BackgroundTaskStatus.SUCCESS)
        self.assertEqual(self.p1.job.job_type, AIJobType.UPLOAD)
        self.assertEqual(self.provider_calls, 1)
        self.assertEqual(wallet_of(self.teacher).reserved_credits, 0)
        self.assertEqual(holds().check_invariants(wallet_of(self.teacher)), [])

    def test_retry_failed_walks_the_items_with_a_running_balance(self):
        """Room for two calls: p1 and p2 are held and run, p3 is skipped
        with INSUFFICIENT_CREDITS and stays parked with its file."""
        self.top_up_to(2 * self.call + self.call // 2)
        response = self.retry_failed(self.session.id)
        self.assertEqual(response.status_code, 202, response.content[:400])
        data = response.json()["data"]
        self.assertEqual(data["retried"], [str(self.p1.id), str(self.p2.id)])
        self.assertIn(
            {"item_id": str(self.p3.id), "reason_code": PARKED}, data["skipped"]
        )
        for item in (self.p1, self.p2):
            item.refresh_from_db()
            self.assertEqual(item.status, BackgroundTaskStatus.SUCCESS)
        self.p3.refresh_from_db()
        self.assertKeptParked(self.p3)
        self.session.refresh_from_db()
        self.assertIsNone(self.session.credits_exhausted_at)
        self.assertEqual(self.provider_calls, 2)
        self.assertEqual(holds().check_invariants(wallet_of(self.teacher)), [])

    def test_a_skipped_item_does_not_stop_a_later_one_that_fits(self):
        """The walk goes on past a file that does not fit, as the batch
        door's does (parked_files)."""
        figures = iter([self.call, 3 * self.call, self.call])

        def per_file(item):
            figure = next(figures)
            with patch(
                "assignments.upload_door.upload_item_estimate",
                side_effect=lambda *a, **k: figure,
            ):
                return KEPT_ESTIMATE(item)

        self.top_up_to(2 * self.call + self.call // 2)
        with patch.object(item_retry, "_kept_estimate", side_effect=per_file):
            data = self.retry_failed(self.session.id).json()["data"]
        self.assertEqual(data["retried"], [str(self.p1.id), str(self.p3.id)])
        self.assertIn(
            {"item_id": str(self.p2.id), "reason_code": PARKED}, data["skipped"]
        )
        self.p2.refresh_from_db()
        self.assertKeptParked(self.p2)

    def test_the_retry_figure_is_the_doors_on_the_kept_bytes(self):
        """Not a second measuring path: _estimate_for on the same bytes, with
        the batch route's prompt and the item's assignment."""
        from assignments.upload_door import _estimate_for
        from assignments.upload_prompts import BATCH_UPLOAD_PROMPT

        row = KeptUpload.objects.get(processing_task=self.p1)
        uploaded = SimpleUploadedFile(
            row.original_name, kept_files.load(row.storage_key), row.content_type
        )
        expected = _estimate_for(uploaded, BATCH_UPLOAD_PROMPT, self.assignment)
        self.assertIsNotNone(expected)
        self.assertEqual(KEPT_ESTIMATE(self.p1), expected)
