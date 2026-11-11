"""Epic B1b-6: batch grading jobs and the queue (plan 08 §3.7; 08b §1.4, §2;
NOTES §6.2-§6.3). Flag on unless a test says otherwise.

TransactionTestCase throughout: a batch's messages go after commit (P-3), so
the on-commit callbacks must really run, and the barrier races (P-1, P-2,
P-4) need their own connections.

The estimate is patched to a fixed per-paper p90 (PER), so how many papers
fit is plain arithmetic on the wallet's credits. Messages never leave:
grade_engine_async.delay is replaced and each send is recorded.
"""

import importlib
import threading
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.db import connection, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from redis.exceptions import ConnectionError as RedisConnectionError

from assignments.models import Assignment, AssignmentStatus
from assignments.tests_cache_matrix_g3 import question
from billing.models import (
    AIJob,
    AIJobStatus,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditWallet,
    HoldState,
)
from billing.testing.holds import ai_ready_teacher, fresh, funded_wallet, holds
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BatchUploadSession,
    BatchUploadType,
    StudentSubmission,
)
from users.models import CustomUser, UserTypes

PER = 100
ESTIMATE = {
    "p50": 80,
    "p90": 100,
    "source": "assignment",
    "shape_basis": "assignment_questions",
    "sample_size": 12,
}
REPO = Path(settings.BASE_DIR)


class _BatchQueue:
    """billing.batch_queue, imported when first used (the B1a precedent: the
    module does not exist before the product commit, and mypy must not
    see the name), so each test fails on its own at the red."""

    def __getattr__(self, name):
        return getattr(importlib.import_module("billing.batch_queue"), name)


batch_queue = _BatchQueue()


def race(n, work):
    barrier = threading.Barrier(n)
    results: list = [None] * n

    def run(i):
        try:
            barrier.wait()
            results[i] = work(i)
        except Exception as exc:  # recorded, asserted by the test
            results[i] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


def add_credits(wallet, credits):
    CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        total_credits=credits,
        used_credits=0,
        expires_at=timezone.now() + timedelta(days=30),
    )


@override_settings(BILLING_HOLDS_ENABLED=True)
class BatchBase(TransactionTestCase):
    def setUp(self):
        module = importlib.import_module("billing.batch_queue")
        estimate = patch(
            "billing.estimates.paper_hold_estimate", return_value=(ESTIMATE, PER)
        )
        estimate.start()
        self.addCleanup(estimate.stop)
        self.sent = []
        self.fail_at = set()
        self.messages = []
        delay = patch(
            "assignments.tasks.grade_engine_async.delay", side_effect=self._delay
        )
        delay.start()
        self.addCleanup(delay.stop)
        mail = patch.object(module, "notify")
        self.notify = mail.start()
        self.addCleanup(mail.stop)

    def _delay(self, *args, **kwargs):
        if len(self.sent) + 1 in self.fail_at:
            self.sent.append(None)
            raise RedisConnectionError("broker down")
        self.sent.append(kwargs["processing_task_id"])
        self.messages.append((args, kwargs))
        return SimpleNamespace(id=f"celery-{uuid.uuid4().hex[:8]}")

    def classroom(self, credits, papers, tag=None, ai_ready=False):
        tag = tag or self._testMethodName[-30:]
        if ai_ready:
            # A teacher whose papers really run (can_user_access_ai).
            teacher = ai_ready_teacher(f"b6.{tag}@example.com", credits)
            self.wallet = CreditWallet.objects.get(user=teacher)
        else:
            self.wallet = funded_wallet(f"b6.{tag}@example.com", credits)
        self.teacher = self.wallet.user
        session = Session.objects.create(name="S", teacher=self.teacher)
        self.course = Course.objects.create(
            name="C", teacher=self.teacher, session=session
        )
        self.assignment = Assignment.objects.create(
            title="A",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[],
        )
        for index in range(papers):
            student = CustomUser.objects.create_user(
                email=f"s{index}.{tag}@example.com",
                password="testpass123",  # pragma: allowlist secret  # nosec B106
                user_type=UserTypes.STUDENT,
                first_name="S",
                last_name=str(index),
            )
            StudentCourse.objects.create(
                student=student,
                course=self.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            StudentSubmission.objects.create(
                assignment=self.assignment, student=student, answers={}
            )

    def session(self):
        return BatchUploadSession.objects.create(
            teacher=self.teacher,
            course=self.course,
            task_type=BatchUploadType.GRADE,
            total_files=self.assignment.submissions.count(),
        )

    def dispatch(self, session=None, **kwargs):
        from assignments.tasks import _dispatch_tracked_grading

        return _dispatch_tracked_grading(
            self.teacher,
            self.assignment,
            self.assignment.submissions.order_by("student__last_name"),
            session or self.session(),
            **kwargs,
        )

    def job(self):
        return AIJob.objects.filter(job_type="BATCH_GRADING").latest("created_at")

    def rows(self, job=None):
        return list(
            BackgroundProcessingTask.objects.filter(job=job or self.job()).order_by(
                "item_index"
            )
        )

    def held(self, job=None):
        return [r for r in self.rows(job) if r.hold_state == HoldState.HELD]

    def waiting(self, job=None):
        return [
            r
            for r in self.rows(job)
            if r.hold_state is None and r.status == BackgroundTaskStatus.PENDING
        ]

    def settle_all(self, rows, actual=80):
        for row in rows:
            holds().settle(fresh(row).job, actual, item=fresh(row))

    def reserved(self):
        return fresh(self.wallet).reserved_credits

    def assert_books_agree(self):
        self.assertEqual(holds().check_invariants(self.wallet), [])


# --------------------------------------------------------------------------
# Start (plan §3.7 Create, Reserve)
# --------------------------------------------------------------------------


class BatchStartTests(BatchBase):
    def test_a_batch_holds_the_papers_that_fit_and_the_rest_wait(self):
        self.classroom(credits=350, papers=5)
        self.dispatch()
        job = self.job()
        self.assertEqual(job.status, AIJobStatus.RESERVED)
        self.assertEqual((job.total_items, job.held_per_item), (5, PER))
        self.assertEqual(len(self.held()), 3, "3 whole papers fit 350")
        self.assertEqual([r.item_index for r in self.waiting()], [4, 5])
        self.assertEqual(self.reserved(), 3 * PER)
        self.assertEqual(self.sent, [str(r.pk) for r in self.held()])
        self.assertTrue(all(r.hold_credits == PER for r in self.held()))
        self.assertTrue(all(not r.celery_task_id for r in self.waiting()))
        self.assert_books_agree()

    def test_the_estimate_is_written_on_the_job(self):
        self.classroom(credits=1000, papers=2)
        self.dispatch()
        job = self.job()
        self.assertEqual(
            (job.estimate_p50, job.estimate_p90, job.estimate_source),
            (80, 100, "assignment"),
            "obligation 5: the estimate rides on the job",
        )

    def test_no_message_is_sent_before_the_batch_commits(self):
        self.classroom(credits=1000, papers=3)
        with transaction.atomic():
            self.dispatch()
            self.assertEqual(self.sent, [], "P-3: a message before commit")
        self.assertEqual(len(self.sent), 3)

    def test_nothing_fits_queues_the_job_at_once(self):
        self.classroom(credits=50, papers=3)
        self.dispatch()
        job = self.job()
        self.assertEqual(job.status, AIJobStatus.QUEUED_PENDING_CREDITS)
        self.assertEqual(job.stopped_at_item, 1)
        self.assertEqual(
            job.expires_at - job.queued_at,
            timedelta(days=settings.QUEUE_LIFETIME_DAYS),
        )
        self.assertEqual(self.sent, [])
        self.assertEqual(self.reserved(), 0)
        self.notify.assert_called_once_with(job.pk, "queued")

    @override_settings(BILLING_HOLDS_ENABLED=False)
    def test_with_the_flag_off_every_paper_is_sent_and_no_job_is_made(self):
        self.classroom(credits=50, papers=3)
        self.dispatch()
        self.assertEqual(len(self.sent), 3)
        self.assertFalse(AIJob.objects.exists())

    def test_a_teacher_without_a_wallet_is_sent_as_with_the_flag_off(self):
        self.classroom(credits=0, papers=2)
        self.wallet.delete()
        self.dispatch()
        self.assertEqual(len(self.sent), 2)
        self.assertFalse(AIJob.objects.exists())


# --------------------------------------------------------------------------
# The last item (D6-a, obligation 1)
# --------------------------------------------------------------------------


class LastItemTests(BatchBase):
    def test_the_last_paper_queues_a_job_whose_other_papers_wait(self):
        self.classroom(credits=300, papers=5)
        self.dispatch()
        self.settle_all(self.held())
        job = self.job()
        self.assertEqual(job.status, AIJobStatus.QUEUED_PENDING_CREDITS)
        self.assertEqual(job.stopped_at_item, 4)
        self.assertEqual(job.completed_items, 3)
        self.assertEqual(
            job.reserved_credits - job.hold_drawn - job.released_credits, 0
        )
        self.assertEqual(self.reserved(), 0)
        self.assert_books_agree()

    def test_the_last_paper_settles_a_job_with_nothing_waiting(self):
        self.classroom(credits=1000, papers=2)
        self.dispatch()
        first, second = self.held()
        self.settle_all([first])
        self.assertEqual(self.job().status, AIJobStatus.RESERVED, "one still held")
        self.settle_all([second])
        self.assertEqual(self.job().status, AIJobStatus.SETTLED)

    def test_a_one_paper_batch_settles_when_its_paper_settles(self):
        # SM ruling §6.6.9 (2): a single-row job, finished by the helper.
        self.classroom(credits=1000, papers=1)
        self.dispatch()
        job = self.job()
        self.assertEqual((job.total_items, len(self.held())), (1, 1))
        self.settle_all(self.held())
        job = self.job()
        self.assertEqual(
            job.status, AIJobStatus.SETTLED, "a one-paper job did not finish"
        )
        self.assertEqual((job.completed_items, self.reserved()), (1, 0))
        self.assert_books_agree()

    def test_a_job_whose_papers_all_failed_ends_failed(self):
        self.classroom(credits=1000, papers=2)
        self.dispatch()
        for row in self.held():
            holds().fail_item(row.job, item=row)
        self.assertEqual(
            self.job().status,
            AIJobStatus.FAILED,
            "D6-a: failures count toward the last item",
        )
        self.assertEqual(self.reserved(), 0)

    def test_two_papers_finishing_at_once_move_the_job_once(self):
        # P-4.
        self.classroom(credits=200, papers=3)
        self.dispatch()
        rows = self.held()
        race(2, lambda i: holds().settle(rows[i].job, 80, item=rows[i]))
        job = self.job()
        self.assertEqual(job.status, AIJobStatus.QUEUED_PENDING_CREDITS)
        self.assertEqual(
            [c.args for c in self.notify.call_args_list], [(job.pk, "queued")]
        )
        self.assert_books_agree()


# --------------------------------------------------------------------------
# D6-c: a held paper that ends before its metered unit
# --------------------------------------------------------------------------


class EndedBeforeItsUnitTests(BatchBase):
    def test_a_skipped_paper_returns_its_hold_and_the_sweep_leaves_it(self):
        from students.task_tracking import mark_processing_task_success

        self.classroom(credits=1000, papers=2)
        self.dispatch()
        skipped, other = self.held()
        BackgroundProcessingTask.objects.filter(pk=skipped.pk).update(
            started_at=timezone.now() - timedelta(days=1)
        )
        mark_processing_task_success(skipped.pk)
        holds().sweep_stale_holds()
        self.assertEqual(
            fresh(skipped).status,
            BackgroundTaskStatus.SUCCESS,
            "the sweep overwrote a skipped paper",
        )
        skipped = fresh(skipped)
        self.assertEqual(skipped.hold_state, HoldState.RETURNED, "D6-c: hook")
        self.assertEqual(skipped.released_credits, PER)
        self.settle_all([other])
        self.assertEqual(self.job().status, AIJobStatus.SETTLED, "the job finished")
        self.assert_books_agree()

    def test_a_paper_the_hook_released_is_not_counted_twice(self):
        from students.task_tracking import mark_processing_task_failure

        self.classroom(credits=1000, papers=3)
        self.dispatch()
        row = self.held()[0]
        mark_processing_task_failure(row.pk, RuntimeError("refused"))
        holds().fail_item(row.job, item=fresh(row))
        holds().settle(row.job, 0, item=fresh(row))
        job = self.job()
        self.assertEqual(
            job.completed_items + job.failed_items, 1, "counted once, not twice"
        )
        self.assertEqual(fresh(row).released_credits, PER)
        self.assert_books_agree()

    def test_a_paper_the_hook_released_then_deleted_returns_its_hold_once(self):
        # B1b-6 obligation 4: the hook is a second writer of the rows C3
        # returns from (NOTES §1.12): a hold is never returned twice.
        from students.task_tracking import mark_processing_task_success

        self.classroom(credits=1000, papers=2)
        self.dispatch()
        row = self.held()[0]
        mark_processing_task_success(row.pk)
        self.assertEqual(self.reserved(), PER, "a hold returned twice")
        BackgroundProcessingTask.objects.filter(pk=row.pk).delete()
        self.assertEqual(self.reserved(), PER, "a hold returned twice")
        self.assert_books_agree()

    def test_a_cancelled_held_paper_returns_its_hold(self):
        from students.task_tracking import cancel_processing_task

        self.classroom(credits=1000, papers=2)
        self.dispatch()
        row = self.held()[0]
        with patch("students.task_tracking.celery_app.control.revoke"):
            cancel_processing_task(row)
        self.assertEqual(fresh(row).hold_state, HoldState.RETURNED)
        self.assertEqual(self.reserved(), PER)


# --------------------------------------------------------------------------
# Promotion (plan §3.7)
# --------------------------------------------------------------------------


class PromotionTests(BatchBase):
    def queued_job(self, credits=50, papers=4):
        self.classroom(credits=credits, papers=papers)
        self.dispatch()
        job = self.job()
        self.assertEqual(job.status, AIJobStatus.QUEUED_PENDING_CREDITS)
        return job

    def test_promotion_holds_only_the_papers_that_now_fit(self):
        # M-B9.
        job = self.queued_job()
        add_credits(self.wallet, 200)  # 250 spendable: 2 papers
        launched = batch_queue.promote_wallet(self.wallet.pk)
        self.assertEqual(launched, 2, "the promoter launched beyond k")
        self.assertEqual(fresh(job).status, AIJobStatus.RESERVED)
        self.assertEqual(len(self.waiting(job)), 2)
        self.assertEqual(len(self.sent), 2)
        self.assertLessEqual(self.reserved(), 250)
        self.assert_books_agree()

    def test_nothing_fitting_leaves_the_job_queued(self):
        job = self.queued_job()
        self.assertIsNone(batch_queue.promote_job(job))
        job = fresh(job)
        self.assertEqual(job.status, AIJobStatus.QUEUED_PENDING_CREDITS)
        self.assertEqual((job.reserved_credits, self.reserved()), (0, 0))
        self.assertEqual(self.sent, [])

    def test_a_promoted_paper_gets_past_the_batch_credit_stop(self):
        # Q14.
        from students.task_tracking import ensure_batch_has_credits

        job = self.queued_job()
        session_id = self.rows(job)[0].batch_session_id
        BatchUploadSession.objects.filter(pk=session_id).update(
            credits_exhausted_at=timezone.now()
        )
        add_credits(self.wallet, 200)
        batch_queue.promote_wallet(self.wallet.pk)
        self.assertIsNone(
            BatchUploadSession.objects.get(pk=session_id).credits_exhausted_at,
            "promotion did not clear the credit stop",
        )
        row = self.held(job)[0]
        ensure_batch_has_credits(row.pk)  # raises if the stop is still set

    def test_two_promoters_at_once_launch_each_paper_once(self):
        # P-1: the sweep and the grant hook on a barrier.
        job = self.queued_job(credits=50, papers=6)
        add_credits(self.wallet, 300)  # 350: 3 papers
        race(2, lambda i: batch_queue.promote_wallet(self.wallet.pk))
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(len(set(self.sent)), 3, "a paper launched twice")
        self.assertEqual(self.reserved(), 3 * PER)
        self.assertEqual(len(self.held(job)), 3)
        self.assert_books_agree()

    def test_a_settled_papers_leftover_promotes_a_queued_job_at_once(self):
        # P-5 (M-B28): the leftover is spendable before the first job ends.
        self.classroom(credits=200, papers=3)
        self.dispatch()
        first = self.job()
        self.assertEqual(len(self.held(first)), 2)
        self.dispatch()  # nothing left: the second batch queues
        second = self.job()
        self.assertEqual(second.status, AIJobStatus.QUEUED_PENDING_CREDITS)
        self.settle_all(self.held(first)[:1], actual=40)  # 60 back
        self.assertEqual(fresh(first).status, AIJobStatus.RESERVED, "not ended")
        add_credits(self.wallet, 40)  # 60 + 40 = one paper
        self.assertEqual(batch_queue.promote_wallet(self.wallet.pk), 1)
        self.assertEqual(fresh(second).status, AIJobStatus.RESERVED)
        self.assert_books_agree()

    def test_a_credit_entry_enqueues_the_wallets_promotion_after_commit(self):
        self.classroom(credits=0, papers=1)
        with patch("billing.tasks.promote_queued_jobs_for_wallet.delay") as promote:
            with transaction.atomic():
                CreditLedger.record(user=self.teacher, ledger_type="GRANT", amount=500)
                promote.assert_not_called()
            promote.assert_called_once_with(str(self.wallet.pk))

    def test_a_debit_entry_enqueues_nothing(self):
        self.classroom(credits=0, papers=1)
        with patch("billing.tasks.promote_queued_jobs_for_wallet.delay") as promote:
            CreditLedger.record(user=self.teacher, ledger_type="CONSUME", amount=-5)
        promote.assert_not_called()

    @override_settings(BILLING_HOLDS_ENABLED=False)
    def test_with_the_flag_off_a_credit_entry_enqueues_nothing(self):
        self.classroom(credits=0, papers=1)
        with patch("billing.tasks.promote_queued_jobs_for_wallet.delay") as promote:
            CreditLedger.record(user=self.teacher, ledger_type="GRANT", amount=500)
        promote.assert_not_called()


# --------------------------------------------------------------------------
# Expiry and the warning (plan §3.7 Lifetime; 08b §2.1)
# --------------------------------------------------------------------------


class ExpiryTests(BatchBase):
    def queued_job(self):
        self.classroom(credits=50, papers=3)
        self.dispatch()
        return self.job()

    def test_expiry_fails_the_waiting_papers_as_retryable(self):
        # M-B10.
        from students.item_retry import refusal_for

        job = self.queued_job()
        AIJob.objects.filter(pk=job.pk).update(expires_at=timezone.now())
        self.assertEqual(batch_queue.expire_due()["expired"], 1)
        self.assertEqual(fresh(job).status, AIJobStatus.EXPIRED)
        rows = self.rows(job)
        self.assertEqual(len(rows), 3, "expiry deleted papers")
        for row in rows:
            self.assertEqual(row.status, BackgroundTaskStatus.FAILURE)
            self.assertEqual(row.reason_code, "INSUFFICIENT_CREDITS_MID_BATCH")
            self.assertIsNone(refusal_for(row), "an expired paper is retryable")

    def test_the_warning_goes_once_48_hours_before_expiry(self):
        job = self.queued_job()
        AIJob.objects.filter(pk=job.pk).update(
            expires_at=timezone.now() + timedelta(hours=47)
        )
        self.notify.reset_mock()
        self.assertEqual(batch_queue.expire_due()["warned"], 1)
        self.assertEqual(batch_queue.expire_due()["warned"], 0)
        self.assertIsNotNone(fresh(job).warned_expiry_at)
        self.notify.assert_called_once_with(job.pk, "expiry_warning")

    def test_expiry_never_ends_a_promoted_job(self):
        job = self.queued_job()
        AIJob.objects.filter(pk=job.pk).update(
            status=AIJobStatus.RESERVED, expires_at=timezone.now()
        )
        self.assertEqual(batch_queue.expire_due()["expired"], 0)
        self.assertEqual(fresh(job).status, AIJobStatus.RESERVED)

    def test_promotion_and_expiry_at_once_have_one_winner(self):
        # P-2.
        job = self.queued_job()
        AIJob.objects.filter(pk=job.pk).update(expires_at=timezone.now())
        add_credits(self.wallet, 500)
        work = [
            lambda: batch_queue.promote_job(fresh(job)),
            lambda: batch_queue.expire_job(fresh(job)),
        ]
        race(2, lambda i: work[i]())
        status = fresh(job).status
        self.assertIn(status, (AIJobStatus.RESERVED, AIJobStatus.EXPIRED))
        if status == AIJobStatus.EXPIRED:
            self.assertEqual(self.reserved(), 0, "a hold left without a running job")
            self.assertEqual(self.sent, [])
        self.assert_books_agree()

    def test_expires_at_is_fixed_when_the_job_queues(self):
        job = self.queued_job()
        before = fresh(job).expires_at
        with override_settings(QUEUE_LIFETIME_DAYS=3):
            batch_queue.expire_due()
            self.assertEqual(fresh(job).expires_at, before)
            self.dispatch()  # a new batch queues under the new value
            newer = self.job()
            self.assertEqual(newer.expires_at - newer.queued_at, timedelta(days=3))


# --------------------------------------------------------------------------
# The backstop (S-5)
# --------------------------------------------------------------------------


class BackstopTests(BatchBase):
    def stalled_job(self, hours):
        self.classroom(credits=1000, papers=2)
        self.dispatch()
        job = self.job()
        AIJob.objects.filter(pk=job.pk).update(
            reservation_claimed_at=timezone.now() - timedelta(hours=hours)
        )
        return job

    def test_papers_none_of_which_started_in_36_hours_fail_and_return_holds(self):
        job = self.stalled_job(hours=37)
        result = holds().sweep_stale_holds()
        self.assertEqual(result["items_backstopped"], 2)
        for row in self.rows(job):
            self.assertEqual(row.status, BackgroundTaskStatus.FAILURE)
            self.assertEqual(row.reason_code, "PROVIDER_FAILURE")
            self.assertEqual(row.hold_state, HoldState.RETURNED)
        self.assertEqual(self.reserved(), 0)
        self.assertEqual(fresh(job).status, AIJobStatus.FAILED)
        self.assert_books_agree()

    def test_a_broker_backlog_younger_than_36_hours_is_left(self):
        job = self.stalled_job(hours=35)
        self.assertEqual(holds().sweep_stale_holds()["items_backstopped"], 0)
        self.assertEqual(len(self.held(job)), 2)


# --------------------------------------------------------------------------
# The automatic paths and H-215a (B-1)
# --------------------------------------------------------------------------


class AutomaticPathTests(BatchBase):
    def test_an_automatic_run_that_runs_out_queues_and_resumes(self):
        # B-1.
        from assignments.tasks import auto_grade_due_assignment

        self.classroom(credits=1000, papers=30)
        Assignment.objects.filter(pk=self.assignment.pk).update(
            auto_grade_on_due_date=True, questions=[question(1)]
        )
        auto_grade_due_assignment(str(self.assignment.id))
        job = self.job()
        self.assertEqual(len(self.held(job)), 10)
        self.settle_all(self.held(job))
        job = fresh(job)
        self.assertEqual(job.status, AIJobStatus.QUEUED_PENDING_CREDITS)
        self.assertIsNotNone(job.expires_at)
        self.notify.assert_any_call(job.pk, "queued")
        add_credits(self.wallet, 2000)
        self.assertEqual(batch_queue.promote_wallet(self.wallet.pk), 20)
        self.assertEqual(len(self.sent), 30)

    def test_a_scheduled_batch_is_one_job(self):
        from assignments.tasks import grade_batch_async

        self.classroom(credits=100, papers=2)
        Assignment.objects.filter(pk=self.assignment.pk).update(questions=[question(1)])
        grade_batch_async.run(str(self.teacher.id), str(self.assignment.id))
        self.assertEqual(
            AIJob.objects.filter(job_type="BATCH_GRADING").count(),
            1,
            "the scheduled batch did not go through the helper",
        )
        self.assertEqual(len(self.waiting()), 1)

    def test_a_broker_outage_at_the_start_is_retried_without_duplicates(self):
        from assignments.tasks import auto_grade_due_assignment

        self.classroom(credits=1000, papers=3)
        Assignment.objects.filter(pk=self.assignment.pk).update(
            auto_grade_on_due_date=True, questions=[question(1)]
        )
        self.fail_at = {2}
        with patch.object(auto_grade_due_assignment, "retry") as retry:
            retry.side_effect = RuntimeError("retried")
            with self.assertRaisesMessage(RuntimeError, "retried"):
                auto_grade_due_assignment(str(self.assignment.id))
        session_id = retry.call_args.kwargs["kwargs"]["session_id"]
        first = self.job()
        self.assertEqual(self.reserved(), PER, "only the sent paper keeps a hold")
        self.fail_at = set()
        auto_grade_due_assignment(str(self.assignment.id), session_id=session_id)
        rows = BackgroundProcessingTask.objects.filter(batch_session_id=session_id)
        self.assertEqual(rows.count(), 3, "one item per paper")
        self.assertEqual(
            sorted(s for s in self.sent if s), sorted(str(r.pk) for r in rows)
        )
        self.assertNotEqual(self.job().pk, first.pk)
        # SM ruling §6.6.9 (3a): the first job is not stranded. It keeps only
        # the paper it sent; when that paper settles, it ends.
        self.assertEqual(len(self.held(first)), 1, "the first job's sent paper")
        self.settle_all(self.held(first))
        first = fresh(first)
        self.assertEqual(
            first.status, AIJobStatus.SETTLED, "the first job was stranded"
        )
        self.assertEqual(self.held(first), [], "a held row left on the first job")
        self.assert_books_agree()

    @override_settings(
        GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
    )
    def test_after_an_outage_and_retry_the_teacher_sees_one_line_per_paper(self):
        # SM ruling §6.6.9 (3b): H-215a's TheTeacherSeesOneLinePerPaperTest,
        # flag on: the papers really run, and session-results reads 3 / 3.
        from django.urls import reverse
        from rest_framework.test import APIClient

        from ai_processor.services import AIProcessor
        from assignments.tasks import auto_grade_due_assignment, grade_engine_async
        from students.tests_h215a_auto_grade_resilience import ANSWER, QUESTION, _reply

        self.classroom(credits=100_000, papers=3, ai_ready=True)
        Assignment.objects.filter(pk=self.assignment.pk).update(
            auto_grade_on_due_date=True, questions=[QUESTION]
        )
        StudentSubmission.objects.filter(assignment=self.assignment).update(
            answers=[{"question_number": 1, "answer_html": f"<p>{ANSWER}</p>"}]
        )
        self.fail_at = {2}
        with patch.object(auto_grade_due_assignment, "retry") as retry:
            retry.side_effect = RuntimeError("retried")
            with self.assertRaisesMessage(RuntimeError, "retried"):
                auto_grade_due_assignment(str(self.assignment.id))
        session_id = retry.call_args.kwargs["kwargs"]["session_id"]
        self.fail_at = set()
        auto_grade_due_assignment(str(self.assignment.id), session_id=session_id)
        with patch.object(AIProcessor, "_AIProcessor__ai_model", side_effect=_reply):
            for args, kwargs in self.messages:
                ran = grade_engine_async.apply(args=args, kwargs=kwargs)
                self.assertTrue(ran.successful(), ran.result)
        api = APIClient()
        api.force_authenticate(user=self.teacher)
        response = api.get(
            reverse("task-session-results", kwargs={"session_id": str(session_id)})
        )
        self.assertEqual(response.status_code, 200, response.content[:400])
        self.assertEqual(response.data["progress"], "3 / 3")
        self.assertIs(response.data["is_complete"], True)
        self.assertEqual(response.data["failure_count"], 0)
        self.assertEqual(response.data["success_count"], 3)
        self.assertEqual(
            AIJob.objects.filter(job_type="BATCH_GRADING")
            .exclude(status=AIJobStatus.SETTLED)
            .count(),
            0,
            "a batch job left open",
        )
        self.assertEqual(self.reserved(), 0)
        self.assert_books_agree()

    def test_waiting_papers_are_not_sent_again_by_a_retry(self):
        from assignments.tasks import _papers_not_yet_sent

        self.classroom(credits=100, papers=3)
        session = self.session()
        self.dispatch(session=session)
        ungraded = self.assignment.submissions.filter(graded_at__isnull=True)
        self.assertEqual(_papers_not_yet_sent(ungraded, session).count(), 0)


# --------------------------------------------------------------------------
# grade-all (D6-d) and deletes (obligation 2)
# --------------------------------------------------------------------------


class GradeAllTests(BatchBase):
    def test_a_waiting_paper_is_listed_with_no_task_id(self):
        from django.urls import reverse
        from rest_framework.test import APIClient

        self.classroom(credits=150, papers=2)
        Assignment.objects.filter(pk=self.assignment.pk).update(questions=[question(1)])
        client = APIClient()
        client.force_authenticate(user=self.teacher)
        response = client.post(
            reverse("assignment-grade-all", args=[self.assignment.pk]),
            {},
            format="json",
        )
        self.assertEqual(response.status_code, 202, response.content[:300])
        tasks = response.json()["data"]["tasks"]
        self.assertEqual(len(tasks), 2)
        self.assertIsNotNone(tasks[0]["task_id"])
        self.assertIsNone(tasks[1]["task_id"], "D6-d: a waiting paper has no task")


class DeleteTests(BatchBase):
    def test_deleting_a_batch_with_waiting_papers_ends_the_job(self):
        # Obligation 2: two never-held PENDING rows, any visit order.
        self.classroom(credits=100, papers=3)
        self.dispatch()
        job = self.job()
        self.assertEqual(len(self.waiting(job)), 2)
        self.assignment.delete()
        self.assertEqual(fresh(job).status, AIJobStatus.FAILED)
        self.assertEqual(self.reserved(), 0)
        self.assert_books_agree()

    def test_deleting_the_held_paper_queues_a_job_whose_papers_wait(self):
        self.classroom(credits=100, papers=3)
        self.dispatch()
        job = self.job()
        self.held(job)[0].delete()
        self.assertEqual(fresh(job).status, AIJobStatus.QUEUED_PENDING_CREDITS)
        self.assertEqual(self.reserved(), 0)
        self.assert_books_agree()


# --------------------------------------------------------------------------
# Emails, the beat and the deprecated plan fields
# --------------------------------------------------------------------------


class QueueEmailTests(TransactionTestCase):
    def test_no_queue_email_is_sent_before_the_texts_are_approved(self):
        # B1b-8: the texts are approved and on; the off path still holds.
        self.assertIs(batch_queue.QUEUE_EMAIL_APPROVED, True)
        # By path: `batch_queue` here is a lazy proxy, not the module (§8.10).
        with patch("AutoGrader.tasks.send_email_task.delay") as send, patch(
            "billing.batch_queue.QUEUE_EMAIL_APPROVED", False
        ), self.assertLogs("billing.batch_queue", "WARNING"):
            batch_queue.notify(uuid.uuid4(), "queued")
        send.assert_not_called()

    def test_the_texts_are_the_owner_approved_ones_verbatim(self):
        texts = batch_queue.QUEUE_EMAIL_TEXTS
        self.assertEqual(
            texts["queued"][1],
            "Your credits ran out while grading {assignment}. {done} of {total} "
            "papers were graded and saved. The other {left} papers will start "
            "again on their own when you add credits. They will wait for {days} "
            "days, until {expires_on}.",
        )
        self.assertEqual(
            texts["expiry_warning"][0], "Your waiting papers expire in 2 days"
        )
        self.assertEqual(texts["restarted"][0], "Your grading batch has started again")
        self.assertEqual(
            texts["expired"][0], "Your waiting papers have stopped waiting"
        )
        self.assertEqual(
            batch_queue.QUEUE_EMAIL_CLOSING_LICENSE,
            "Ask your school administrator for more credits to continue.",
        )


class BeatTests(TransactionTestCase):
    def test_promotion_and_expiry_run_every_five_minutes(self):
        schedule = {
            entry["task"]: entry["schedule"]
            for entry in settings.CELERY_BEAT_SCHEDULE.values()
        }
        self.assertEqual(schedule["billing.tasks.promote_queued_jobs"], 300.0)
        self.assertEqual(schedule["billing.tasks.expire_queued_jobs"], 300.0)


class DeprecatedPlanFieldTests(TransactionTestCase):
    """08b §2.2: the two plan fields stay in the schema, unused."""

    FIELDS = ("queue_expiry_days", "queue_expiry_warning_hours")

    def test_no_code_names_the_deprecated_fields(self):
        this = Path(__file__).resolve()
        offenders = []
        for path in REPO.rglob("*.py"):
            parts = path.relative_to(REPO).parts
            if "migrations" in parts or parts[0] in ("docs", "node_modules"):
                continue
            if path.resolve() == this or ".venv" in parts:
                continue
            text = path.read_text(errors="ignore")
            if any(field in text for field in self.FIELDS):
                offenders.append(str(path.relative_to(REPO)))
        # The fields themselves, and B1a's schema test that pins their
        # defaults (08b §2.2: billing/tests/test_holds_schema.py:353-360).
        self.assertEqual(
            sorted(offenders),
            ["billing/models.py", "billing/tests/test_holds_schema.py"],
        )

    def test_their_help_text_says_they_are_deprecated(self):
        from billing.models import SubscriptionPlan

        for name in self.FIELDS:
            field = SubscriptionPlan._meta.get_field(name)
            help_text = getattr(field, "help_text", "")
            self.assertTrue(help_text.startswith("Deprecated (owner 2026-11-08)"))
