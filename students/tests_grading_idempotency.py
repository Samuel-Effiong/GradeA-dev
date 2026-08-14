"""
Regression coverage for C3: Celery is configured with acks_late=True and a
Redis broker visibility_timeout. A grading run (several sequential AI
calls, each with its own retries — see assignments.tasks.grade_engine_async)
could legitimately take longer than that timeout, so Redis would redeliver
the same task message to a second worker while the first was still
running. Nothing stopped the duplicate: two full, separately-billed
grading pipelines could run concurrently on one submission, racing to
write score/feedback last.

The fix is a claim (StudentSubmission.grading_state /
grading_started_at, students.services._claim_submission_for_grading) that
grade_engine acquires before doing any work and releases on
success/failure, plus a generous staleness window so a claim left behind
by a genuinely crashed/killed worker can still be reclaimed. These tests
lock: the claim is race-free under real concurrency, a live claim blocks a
duplicate run without ever calling the (billed) AI pipeline, a stale claim
is recoverable, legitimate re-grading of an already-graded submission is
never blocked, and both call sites (the sync view and the Celery task)
surface a blocked duplicate as a clean skip rather than an error.

Run with:
    python manage.py test students.tests_grading_idempotency
"""

import threading
from datetime import timedelta
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment
from assignments.tasks import grade_engine_async
from classrooms.models import Course, Session
from students.exceptions import SubmissionGradingInProgressError
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
    GradingState,
    StudentSubmission,
)
from students.services import (
    GRADING_CLAIM_STALE_AFTER,
    _claim_submission_for_grading,
    _mark_grading_claim_failed,
    grade_engine,
)
from users.models import CustomUser, UserTypes


def _valid_grading_result(score=8, max_points=10):
    return {
        "grading_summary": {
            "total_score": score,
            "max_total_points": max_points,
            "percentage": round(score / max_points * 100, 2),
        },
        "grading_confidence": 90,
        "question_evaluations": [],
    }


class GradingClaimFixtureMixin:
    def _make_submission(self):
        teacher = CustomUser.objects.create_user(
            email=f"teacher-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        student = CustomUser.objects.create_user(
            email=f"student-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        session = Session.objects.create(name="Test Session", teacher=teacher)
        course = Course.objects.create(
            name="Test Course", teacher=teacher, session=session
        )
        assignment = Assignment.objects.create(
            title="Test Assignment",
            course=course,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )
        submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "An answer."}],
        )
        return teacher, submission


class ClaimSubmissionForGradingTest(GradingClaimFixtureMixin, TestCase):
    """Unit-level coverage of the claim primitive itself."""

    def test_idle_submission_is_claimable(self):
        _, submission = self._make_submission()
        self.assertTrue(_claim_submission_for_grading(submission.id))
        submission.refresh_from_db()
        self.assertEqual(submission.grading_state, GradingState.RUNNING)
        self.assertIsNotNone(submission.grading_started_at)

    def test_live_running_claim_blocks_a_second_claim(self):
        _, submission = self._make_submission()
        self.assertTrue(_claim_submission_for_grading(submission.id))
        # A second claim attempt while the first is still fresh must fail.
        self.assertFalse(_claim_submission_for_grading(submission.id))

    def test_stale_running_claim_is_reclaimable(self):
        _, submission = self._make_submission()
        stale_start = timezone.now() - GRADING_CLAIM_STALE_AFTER - timedelta(seconds=1)
        StudentSubmission.objects.filter(pk=submission.pk).update(
            grading_state=GradingState.RUNNING, grading_started_at=stale_start
        )
        self.assertTrue(_claim_submission_for_grading(submission.id))
        submission.refresh_from_db()
        # The claim was refreshed to now, not left at the old stale timestamp.
        self.assertGreater(submission.grading_started_at, stale_start)

    def test_done_submission_is_reclaimable_for_regrade(self):
        _, submission = self._make_submission()
        StudentSubmission.objects.filter(pk=submission.pk).update(
            grading_state=GradingState.DONE, graded_at=timezone.now()
        )
        self.assertTrue(_claim_submission_for_grading(submission.id))

    def test_failed_submission_is_reclaimable(self):
        _, submission = self._make_submission()
        StudentSubmission.objects.filter(pk=submission.pk).update(
            grading_state=GradingState.FAILED
        )
        self.assertTrue(_claim_submission_for_grading(submission.id))

    def test_mark_grading_claim_failed_resets_to_failed(self):
        _, submission = self._make_submission()
        _claim_submission_for_grading(submission.id)
        _mark_grading_claim_failed(submission.id)
        submission.refresh_from_db()
        self.assertEqual(submission.grading_state, GradingState.FAILED)


class ClaimSubmissionForGradingConcurrencyTest(
    GradingClaimFixtureMixin, TransactionTestCase
):
    """
    The real regression lock: under actual concurrent execution (two
    threads, two DB connections — a TestCase's shared per-test transaction
    would hide any race), exactly one claimant must win. This is what a
    Celery redelivery racing the still-running original looks like.
    """

    def test_only_one_of_two_concurrent_claims_succeeds(self):
        _, submission = self._make_submission()
        results = []
        start_barrier = threading.Barrier(2)

        def attempt_claim():
            start_barrier.wait(timeout=5)
            try:
                results.append(_claim_submission_for_grading(submission.id))
            finally:
                connection.close()

        threads = [threading.Thread(target=attempt_claim) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(results), 2)
        self.assertEqual(
            sorted(results),
            [False, True],
            "exactly one concurrent claim attempt should have won",
        )


class GradeEngineIdempotencyTest(GradingClaimFixtureMixin, TestCase):
    """grade_engine-level coverage: the claim actually gates the (billed)
    AI pipeline, and legitimate re-grading is never blocked by it."""

    @patch("students.services.ai_processor")
    def test_blocked_claim_raises_without_calling_ai_pipeline(self, mock_ai):
        teacher, submission = self._make_submission()
        StudentSubmission.objects.filter(pk=submission.pk).update(
            grading_state=GradingState.RUNNING, grading_started_at=timezone.now()
        )

        with self.assertRaises(SubmissionGradingInProgressError):
            grade_engine(teacher, submission)

        # The load-bearing assertion: a blocked duplicate must never reach
        # the billed AI pipeline.
        mock_ai.extract_grade_with_retry.assert_not_called()

    @patch("students.services.ai_processor")
    def test_successful_grading_claims_and_marks_done(self, mock_ai):
        teacher, submission = self._make_submission()
        mock_ai.extract_grade_with_retry.return_value = _valid_grading_result()

        result = grade_engine(teacher, submission)

        self.assertEqual(result.grading_state, GradingState.DONE)
        submission.refresh_from_db()
        self.assertEqual(submission.grading_state, GradingState.DONE)
        self.assertIsNotNone(submission.graded_at)
        mock_ai.extract_grade_with_retry.assert_called_once()

    @patch("students.services.ai_processor")
    def test_failed_grading_releases_claim_and_reraises(self, mock_ai):
        teacher, submission = self._make_submission()
        mock_ai.extract_grade_with_retry.side_effect = RuntimeError("model exploded")

        with self.assertRaises(RuntimeError):
            grade_engine(teacher, submission)

        submission.refresh_from_db()
        self.assertEqual(submission.grading_state, GradingState.FAILED)

    @patch("students.services.ai_processor")
    def test_stale_claim_is_recovered_and_ai_pipeline_runs(self, mock_ai):
        teacher, submission = self._make_submission()
        mock_ai.extract_grade_with_retry.return_value = _valid_grading_result()
        stale_start = timezone.now() - GRADING_CLAIM_STALE_AFTER - timedelta(seconds=1)
        StudentSubmission.objects.filter(pk=submission.pk).update(
            grading_state=GradingState.RUNNING, grading_started_at=stale_start
        )

        result = grade_engine(teacher, submission)

        self.assertEqual(result.grading_state, GradingState.DONE)
        mock_ai.extract_grade_with_retry.assert_called_once()

    @patch("students.services.ai_processor")
    def test_regrading_an_already_graded_submission_is_not_blocked(self, mock_ai):
        # A DONE submission is a legitimate, intentional re-grade target —
        # not a duplicate — and must be claimable again.
        teacher, submission = self._make_submission()
        mock_ai.extract_grade_with_retry.return_value = _valid_grading_result(score=5)
        StudentSubmission.objects.filter(pk=submission.pk).update(
            grading_state=GradingState.DONE, graded_at=timezone.now()
        )

        mock_ai.extract_grade_with_retry.return_value = _valid_grading_result(score=9)
        result = grade_engine(teacher, submission)

        self.assertEqual(result.grading_state, GradingState.DONE)
        self.assertEqual(float(result.score), 9)
        mock_ai.extract_grade_with_retry.assert_called_once()


class GradeViewInProgressConflictTest(APITestCase):
    """The sync HTTP entry point surfaces a blocked duplicate as 409, not
    a 500 — a real (if less likely) scenario since two concurrent
    requests can both hit the synchronous `grade` action directly."""

    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="conflict-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        student = CustomUser.objects.create_user(
            email="conflict-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        session = Session.objects.create(name="S", teacher=self.teacher)
        course = Course.objects.create(name="C", teacher=self.teacher, session=session)
        assignment = Assignment.objects.create(
            title="A", course=course, questions=[{"question_number": 1, "points": 10}]
        )
        self.submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "x"}],
        )
        from billing.models import CreditBucket, CreditBucketType, CreditWallet

        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.url = reverse(
            "student-submission-grade", kwargs={"pk": self.submission.pk}
        )

    @patch("students.views.grade_engine")
    def test_in_progress_claim_returns_409_not_500(self, mock_grade_engine):
        mock_grade_engine.side_effect = SubmissionGradingInProgressError(
            "already being graded"
        )
        self.client.force_authenticate(user=self.teacher)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("already being graded", response.data["error"])


class GradeEngineAsyncSkipHandlingTest(GradingClaimFixtureMixin, TestCase):
    """
    The Celery task side of a blocked duplicate — the exact scenario C3
    describes: Redis redelivers grade_engine_async's message to a second
    worker while the first is still running. The second worker's task must
    finish cleanly (SUCCESS, not FAILURE) without re-running the AI
    pipeline, so it isn't retried or reported to the user as an error.
    """

    @patch("students.services.ai_processor")
    def test_redelivered_task_skips_cleanly_when_original_still_running(self, mock_ai):
        teacher, submission = self._make_submission()
        # Simulate the original (still-running) worker holding a live claim.
        StudentSubmission.objects.filter(pk=submission.pk).update(
            grading_state=GradingState.RUNNING, grading_started_at=timezone.now()
        )
        processing_task = BackgroundProcessingTask.objects.create(
            requested_by=teacher,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            submission=submission,
        )

        # Run the task exactly as Celery would (self-binding included),
        # with no real broker needed.
        result = grade_engine_async.apply(
            args=(str(teacher.id), str(submission.id)),
            kwargs={"processing_task_id": str(processing_task.id)},
        ).get()

        self.assertEqual(result["status"], "SUCCESS")
        self.assertIn("already being graded", result["message"])
        mock_ai.extract_grade_with_retry.assert_not_called()

        processing_task.refresh_from_db()
        self.assertEqual(processing_task.status, BackgroundTaskStatus.SUCCESS)
        self.assertTrue(processing_task.meta.get("skipped"))

    @patch("students.services.ai_processor")
    def test_first_delivery_grades_normally(self, mock_ai):
        teacher, submission = self._make_submission()
        mock_ai.extract_grade_with_retry.return_value = _valid_grading_result()
        processing_task = BackgroundProcessingTask.objects.create(
            requested_by=teacher,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            submission=submission,
        )

        result = grade_engine_async.apply(
            args=(str(teacher.id), str(submission.id)),
            kwargs={"processing_task_id": str(processing_task.id)},
        ).get()

        self.assertEqual(result["status"], "SUCCESS")
        mock_ai.extract_grade_with_retry.assert_called_once()
        submission.refresh_from_db()
        self.assertEqual(submission.grading_state, GradingState.DONE)


class GradeEngineAsyncBatchFailureReportingTest(GradingClaimFixtureMixin, TestCase):
    """
    A "Grade All" run fans out one grade_engine_async task per submission
    with a shared batch_id. Every other batch task type (upload, extraction,
    cancellation) records a FAILED entry with the error on
    BatchUploadSession.results when it fails; grading was the one exception
    -- its except block never called session.update_result(), so a failed
    submission in a "Grade All" run silently vanished from the batch
    results instead of showing up as a failure with a reason.
    """

    @patch("students.services.ai_processor")
    def test_grading_failure_records_a_failed_result_on_the_batch_session(
        self, mock_ai
    ):
        teacher, submission = self._make_submission()
        mock_ai.extract_grade_with_retry.side_effect = TimeoutError("timed out")
        processing_task = BackgroundProcessingTask.objects.create(
            requested_by=teacher,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            submission=submission,
        )
        session = BatchUploadSession.objects.create(
            teacher=teacher,
            task_type=BatchUploadType.GRADE,
            total_files=1,
        )

        with self.assertRaises(TimeoutError):
            grade_engine_async.apply(
                args=(str(teacher.id), str(submission.id)),
                kwargs={
                    "batch_id": str(session.id),
                    "processing_task_id": str(processing_task.id),
                },
            ).get()

        session.refresh_from_db()
        self.assertEqual(len(session.results), 1)
        entry = session.results[0]
        self.assertEqual(entry["status"], "FAILED")
        self.assertIn(submission.student.get_full_name(), entry["file_name"])
        # The specific, classified reason -- not the generic catch-all.
        self.assertIn("timed out", entry["error"])

    @patch("students.services.ai_processor")
    def test_grading_failure_without_batch_id_does_not_touch_any_session(self, mock_ai):
        teacher, submission = self._make_submission()
        mock_ai.extract_grade_with_retry.side_effect = RuntimeError("boom")
        processing_task = BackgroundProcessingTask.objects.create(
            requested_by=teacher,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            submission=submission,
        )

        with self.assertRaises(RuntimeError):
            grade_engine_async.apply(
                args=(str(teacher.id), str(submission.id)),
                kwargs={"processing_task_id": str(processing_task.id)},
            ).get()

        self.assertFalse(BatchUploadSession.objects.exists())
