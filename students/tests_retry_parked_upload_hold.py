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

    python manage.py test students.tests_retry_parked_upload_hold
"""

import contextvars
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from ai_processor.services import AIProcessor
from billing import holds as credit_holds
from billing import refunds
from billing.errors import InsufficientCreditsError
from billing.models import CreditUsageLog, CreditWallet
from billing.testing.holds import (
    ai_response,
    estimate_for,
    fund,
    holds,
    real_billed_call,
)
from students import task_tracking
from students.models import BackgroundProcessingTask, BackgroundTaskStatus, KeptUpload
from students.task_tracking import mark_processing_task_cancelled
from students.tests_parked_files_kept import PROVIDER, ParkedFixture

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
        launch = patch(
            "students.item_retry.launch_processing_task", side_effect=self._run_now
        )
        launch.start()
        self.addCleanup(launch.stop)

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

    def _run_now(self, task_callable, processing_task, *args, **kwargs):
        celery_id = str(uuid.uuid4())
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

    def test_it_is_refused_in_the_worker_before_any_charge(self):
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
    def test_the_refusal_is_the_calls_own_fit_check_before_the_provider(self):
        """The access check passes (it reads the total, which the siblings'
        holds still count); the call's fit check under its own new hold
        (Unit.before_call -> ensure_call_fits) refuses before the provider."""
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
        self.assertEqual(len(refusals), 1)
        self.assertEqual(self.provider_calls, 0, "refused before the provider")
        self.assertEqual(wallet_of(self.teacher).reserved_credits, 2 * self.door)
        self.assertEqual(holds().check_invariants(wallet_of(self.teacher)), [])


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

    def test_it_is_refused_in_the_worker_before_any_charge(self):
        self.retry_f2()  # refused earlier still: at the route
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
