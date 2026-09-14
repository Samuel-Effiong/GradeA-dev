"""
H-11: the asynchronous raw-text edit path.

`POST submissions/<pk>/update-async` queues the existing
assignments.tasks.extract_answer_background_task (rewired, previously
unrouted and broken) as a tracked task and answers 202 with a task id; no
AI work happens inside the request. The synchronous PATCH now calls the
same service, so the two cannot drift. Also covered here: the
duplicate-request guards on update-async and upload-async, the task's
idempotency claim under redelivery on a real Celery worker, refund on
failure after the charge, and (opt-in, billed) one real provider call.

Run with:
    python manage.py test students.tests_async_edit_path
    RUN_REAL_AI=1 python manage.py test students.tests_async_edit_path.RealProviderEditTest
"""

import os
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import Mock, patch

import redis
import requests
from celery.contrib.testing.worker import start_worker
from celery.signals import task_postrun, worker_shutdown
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import LiveServerTestCase, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from assignments.models import Assignment, AssignmentStatus
from assignments.tasks import (
    EXTRACTION_TASK_STALE_AFTER_SECONDS,
    extract_answer_background_task,
)
from AutoGrader.celery import app as celery_app
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from billing.refunds import record_billing_task_id
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    GradingState,
    StudentSubmission,
)
from students.services import update_submission_from_raw_text
from users.models import CustomUser, UserTypes

EXTRACTED = {
    "answers": [{"question_number": 1, "answer_html": "<p>edited answer</p>"}],
    "extraction_confidence": 77,
}
EDITED_TEXT = "Question 1: edited answer"


def _user(tag, user_type, **extra):
    return CustomUser.objects.create_user(
        email=f"{tag}@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=user_type,
        first_name=tag.replace("-", " ").title(),
        last_name="Edit",
        is_active=True,
        email_verified_at=timezone.now(),
        **extra,
    )


def _fund(teacher, credits=100_000):
    wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
    CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        total_credits=credits,
        used_credits=0,
        expires_at=timezone.now() + timedelta(days=30),
    )


def _classroom(tag):
    teacher = _user(f"{tag}-teacher", UserTypes.TEACHER)
    _fund(teacher)
    student = _user(f"{tag}-student", UserTypes.STUDENT)
    session = Session.objects.create(name="S", teacher=teacher)
    course = Course.objects.create(name="C", teacher=teacher, session=session)
    StudentCourse.objects.create(
        student=student, course=course, enrollment_status=EnrollmentStatusType.ENROLLED
    )
    assignment = Assignment.objects.create(
        title="A",
        course=course,
        status=AssignmentStatus.PUBLISHED,
        questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
    )
    submission = StudentSubmission.objects.create(
        assignment=assignment,
        student=student,
        answers=[{"question_number": 1, "answer_html": "original"}],
        attempt_count=1,
        is_published=False,
        formatted_grade="keep-me",
    )
    return teacher, student, course, assignment, submission


class UpdateAsyncRouteTest(APITestCase):
    def setUp(self):
        (
            self.teacher,
            self.student,
            self.course,
            self.assignment,
            self.submission,
        ) = _classroom("route")
        self.url = reverse(
            "student-submission-update-async", kwargs={"pk": self.submission.pk}
        )
        self.client.force_authenticate(user=self.student)

    def _post(self, text=EDITED_TEXT):
        return self.client.post(self.url, {"raw_input": text}, format="json")

    @patch("students.services.ai_processor")
    @patch("students.views.launch_processing_task")
    def test_queues_the_tracked_task_and_does_no_ai_work_in_the_request(
        self, mock_launch, mock_ai
    ):
        mock_launch.return_value.id = "celery-id"

        response = self._post()

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["task_id"], "celery-id")
        self.assertEqual(str(response.data["submission_id"]), str(self.submission.id))
        mock_ai.extract_answer_with_retry.assert_not_called()
        tracked = BackgroundProcessingTask.objects.get()
        self.assertEqual(tracked.task_type, BackgroundTaskType.ANSWER_EXTRACTION)
        self.assertEqual(tracked.submission_id, self.submission.id)
        self.assertEqual(tracked.requested_by, self.student)
        task_fn, task_row, sub_id, text, user_id = mock_launch.call_args.args
        self.assertIs(task_fn, extract_answer_background_task)
        self.assertEqual(task_row.id, tracked.id)
        self.assertEqual(
            (sub_id, text, user_id),
            (str(self.submission.id), EDITED_TEXT, str(self.student.id)),
        )
        # The row itself is untouched until the worker writes.
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.answers[0]["answer_html"], "original")

    @patch("students.views.launch_processing_task")
    def test_course_teacher_may_also_queue_an_edit(self, mock_launch):
        mock_launch.return_value.id = "celery-id"
        self.client.force_authenticate(user=self.teacher)
        self.assertEqual(self._post().status_code, status.HTTP_202_ACCEPTED)

    @patch("students.views.launch_processing_task")
    def test_other_student_other_teacher_and_anonymous_are_kept_out(self, mock_launch):
        other_student = _user("route-other-student", UserTypes.STUDENT)
        StudentCourse.objects.create(
            student=other_student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        other_teacher = _user("route-other-teacher", UserTypes.TEACHER)
        _fund(other_teacher)

        self.client.force_authenticate(user=other_student)
        self.assertEqual(self._post().status_code, status.HTTP_404_NOT_FOUND)
        self.client.force_authenticate(user=other_teacher)
        self.assertEqual(self._post().status_code, status.HTTP_404_NOT_FOUND)
        self.client.force_authenticate(user=None)
        self.assertEqual(self._post().status_code, status.HTTP_401_UNAUTHORIZED)
        mock_launch.assert_not_called()
        self.assertFalse(BackgroundProcessingTask.objects.exists())

    @patch("students.views.launch_processing_task")
    def test_missing_text_and_unpublished_assignment_are_400(self, mock_launch):
        self.assertEqual(self._post("   ").status_code, status.HTTP_400_BAD_REQUEST)
        Assignment.objects.filter(pk=self.assignment.pk).update(
            status=AssignmentStatus.DRAFT
        )
        # A student cannot even see a draft assignment's submission.
        self.assertIn(
            self._post().status_code,
            (status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND),
        )
        mock_launch.assert_not_called()

    @patch("students.views.launch_processing_task")
    def test_graded_and_being_graded_rows_are_409(self, mock_launch):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            grading_state=GradingState.RUNNING, grading_started_at=timezone.now()
        )
        response = self._post()
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("being graded", response.data["error"])

        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            grading_state=GradingState.DONE, graded_at=timezone.now(), score=8
        )
        response = self._post()
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("already been graded", response.data["error"])
        mock_launch.assert_not_called()
        self.assertFalse(BackgroundProcessingTask.objects.exists())

    @patch("students.views.launch_processing_task")
    def test_second_request_while_the_first_is_still_processing_is_409(
        self, mock_launch
    ):
        # The client-retry-after-timeout case: the first task is queued (or
        # already running); the retry must not queue a second billed run.
        mock_launch.return_value.id = "celery-id"
        self.assertEqual(self._post().status_code, status.HTTP_202_ACCEPTED)

        for state in (BackgroundTaskStatus.PENDING, BackgroundTaskStatus.STARTED):
            with self.subTest(state=state):
                BackgroundProcessingTask.objects.update(status=state)
                response = self._post("a different edit")
                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                self.assertIn("still being processed", response.data["error"])
        self.assertEqual(BackgroundProcessingTask.objects.count(), 1)
        self.assertEqual(mock_launch.call_count, 1)

        # Once the first task is terminal, a new edit is accepted again.
        BackgroundProcessingTask.objects.update(status=BackgroundTaskStatus.SUCCESS)
        self.assertEqual(self._post().status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(BackgroundProcessingTask.objects.count(), 2)

    @patch("students.views.launch_processing_task")
    def test_upload_async_has_the_same_duplicate_guard(self, mock_launch):
        mock_launch.return_value.id = "celery-id"
        url = reverse(
            "student-submission-upload-async",
            kwargs={"assignment_id": self.assignment.pk},
        )

        def upload():
            return self.client.post(
                url,
                {
                    "answer": SimpleUploadedFile(
                        "a.pdf", b"%PDF-1.4\n%%EOF\n", content_type="application/pdf"
                    )
                },
                format="multipart",
            )

        self.assertEqual(upload().status_code, status.HTTP_200_OK)
        response = upload()
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("still being processed", response.data["error"])
        self.assertEqual(BackgroundProcessingTask.objects.count(), 1)
        BackgroundProcessingTask.objects.update(status=BackgroundTaskStatus.FAILURE)
        self.assertEqual(upload().status_code, status.HTTP_200_OK)

    @patch("students.services.ai_processor")
    def test_sync_patch_uses_the_same_service_and_answers_4xx_for_user_errors(
        self, mock_ai
    ):
        # V-2: a user-caused refusal is a 4xx with its own text, not a 500.
        # V-4: the save is column-scoped (formatted_grade survives).
        mock_ai.extract_answer_with_retry.return_value = EXTRACTED
        detail = reverse("student-submission-detail", kwargs={"pk": self.submission.pk})

        response = self.client.patch(detail, {"raw_input": EDITED_TEXT}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.submission.refresh_from_db()
        self.assertEqual(
            self.submission.answers[0]["answer_html"], "<p>edited answer</p>"
        )
        self.assertEqual(self.submission.extraction_confidence, 77)
        self.assertEqual(self.submission.formatted_grade, "keep-me")

        from billing.errors import InsufficientCreditsError

        mock_ai.extract_answer_with_retry.side_effect = InsufficientCreditsError(
            "Refill your wallet to continue"
        )
        response = self.client.patch(detail, {"raw_input": EDITED_TEXT}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"], "Refill your wallet to continue")

        mock_ai.extract_answer_with_retry.side_effect = RuntimeError("model exploded")
        response = self.client.patch(detail, {"raw_input": EDITED_TEXT}, format="json")
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertNotIn("exploded", response.data["error"])


class ExtractionTaskTest(TestCase):
    def setUp(self):
        (
            self.teacher,
            self.student,
            self.course,
            self.assignment,
            self.submission,
        ) = _classroom("task")

    def _tracked(self, **extra):
        return BackgroundProcessingTask.objects.create(
            requested_by=self.student,
            task_type=BackgroundTaskType.ANSWER_EXTRACTION,
            assignment=self.assignment,
            submission=self.submission,
            **extra,
        )

    def _run(self, tracked, task_id=None):
        return extract_answer_background_task.apply(
            args=(str(self.submission.id), EDITED_TEXT, str(self.student.id)),
            kwargs={"processing_task_id": str(tracked.id)},
            task_id=task_id or str(uuid.uuid4()),
        ).get()

    @patch("students.services.ai_processor")
    def test_success_writes_only_the_extraction_columns(self, mock_ai):
        mock_ai.extract_answer_with_retry.return_value = EXTRACTED
        tracked = self._tracked()

        result = self._run(tracked)

        self.assertEqual(result["status"], "SUCCESS")
        self.submission.refresh_from_db()
        self.assertEqual(
            self.submission.answers[0]["answer_html"], "<p>edited answer</p>"
        )
        self.assertIn("edited answer", self.submission.raw_input)
        self.assertEqual(self.submission.extraction_confidence, 77)
        self.assertEqual(self.submission.formatted_grade, "keep-me")
        self.assertEqual(self.submission.attempt_count, 1)
        tracked.refresh_from_db()
        self.assertEqual(tracked.status, BackgroundTaskStatus.SUCCESS)
        self.assertIsNotNone(tracked.started_at)
        # The billed call was made with the requesting user, not the row's student.
        user_arg = mock_ai.extract_answer_with_retry.call_args.args[0]
        self.assertEqual(user_arg, self.student)

    @patch("students.services.ai_processor")
    def test_grade_landing_during_extraction_is_refused_once_and_not_retried(
        self, mock_ai
    ):
        def grade_lands(*args, **kwargs):
            StudentSubmission.objects.filter(pk=self.submission.pk).update(
                graded_at=timezone.now(), score=8, grading_state=GradingState.DONE
            )
            return EXTRACTED

        mock_ai.extract_answer_with_retry.side_effect = grade_lands
        tracked = self._tracked()

        result = self._run(tracked)

        self.assertEqual(result["status"], "FAILURE")
        self.assertIn("already been graded", result["message"])
        self.assertEqual(mock_ai.extract_answer_with_retry.call_count, 1)
        tracked.refresh_from_db()
        self.assertEqual(tracked.status, BackgroundTaskStatus.FAILURE)
        self.assertIn("already been graded", tracked.error)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.answers[0]["answer_html"], "original")

    @patch("students.services.ai_processor")
    def test_transient_failure_is_retried_and_the_success_recorded(self, mock_ai):
        mock_ai.extract_answer_with_retry.side_effect = [
            TimeoutError("model timed out"),
            EXTRACTED,
        ]
        tracked = self._tracked()

        result = self._run(tracked)

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(mock_ai.extract_answer_with_retry.call_count, 2)
        tracked.refresh_from_db()
        self.assertEqual(tracked.status, BackgroundTaskStatus.SUCCESS)
        self.assertIn("timed out", tracked.meta["last_error"])

    @patch("billing.services.SubscriptionService.refund_credits")
    @patch("students.services.ai_processor")
    def test_failure_after_the_charge_refunds_it(self, mock_ai, mock_refund):
        # The charge is committed inside the extraction; a malformed result
        # afterwards must reclaim it - the row never changed.
        def charge_then_return_junk(*args, **kwargs):
            record_billing_task_id("edit-billing-task-1")
            return {"answers": "not a list"}

        mock_ai.extract_answer_with_retry.side_effect = charge_then_return_junk

        with self.assertRaises(ValueError):
            update_submission_from_raw_text(self.student, self.submission, EDITED_TEXT)

        mock_refund.assert_called_once()
        self.assertEqual(mock_refund.call_args.args[0], "edit-billing-task-1")
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.answers[0]["answer_html"], "original")

    @patch("students.services.ai_processor")
    def test_redelivery_of_a_running_task_skips_without_a_second_billed_call(
        self, mock_ai
    ):
        # Simulate the original delivery holding the claim: STARTED, fresh.
        tracked = self._tracked(
            status=BackgroundTaskStatus.STARTED, started_at=timezone.now()
        )
        mock_ai.extract_answer_with_retry.return_value = EXTRACTED

        result = self._run(tracked)

        self.assertEqual(result["status"], "SUCCESS")
        self.assertIn("duplicate run skipped", result["message"])
        mock_ai.extract_answer_with_retry.assert_not_called()
        tracked.refresh_from_db()
        self.assertEqual(tracked.status, BackgroundTaskStatus.STARTED)

    @patch("students.services.ai_processor")
    def test_stale_claim_from_a_dead_worker_is_taken_over(self, mock_ai):
        tracked = self._tracked(
            status=BackgroundTaskStatus.STARTED,
            started_at=timezone.now()
            - timedelta(seconds=EXTRACTION_TASK_STALE_AFTER_SECONDS + 60),
        )
        mock_ai.extract_answer_with_retry.return_value = EXTRACTED

        result = self._run(tracked)

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(mock_ai.extract_answer_with_retry.call_count, 1)
        tracked.refresh_from_db()
        self.assertEqual(tracked.status, BackgroundTaskStatus.SUCCESS)


class UpdateAsyncConcurrencyLiveHTTPTest(LiveServerTestCase):
    """Twelve real HTTP clients hitting update-async at once for one
    submission: exactly one task is queued, the rest are refused, and no
    AI work happens in any request."""

    WORKERS = 12

    def setUp(self):
        (
            self.teacher,
            self.student,
            self.course,
            self.assignment,
            self.submission,
        ) = _classroom("live")
        self.url = self.live_server_url + reverse(
            "student-submission-update-async", kwargs={"pk": self.submission.pk}
        )
        self.token = str(RefreshToken.for_user(self.student).access_token)

    def _post(self, i):
        response = requests.post(
            self.url,
            json={"raw_input": f"{EDITED_TEXT} #{i}"},
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=30,
        )
        return response.status_code

    def test_concurrent_edit_requests_queue_exactly_one_task(self):
        launch = Mock()
        launch.return_value.id = "celery-id"
        with patch("students.views.launch_processing_task", launch), patch(
            "students.services.ai_processor"
        ) as mock_ai:
            with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
                codes = sorted(pool.map(self._post, range(self.WORKERS)))

        self.assertEqual(codes, [202] + [409] * (self.WORKERS - 1), codes)
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(BackgroundProcessingTask.objects.count(), 1)
        mock_ai.extract_answer_with_retry.assert_not_called()


class ExtractionTaskLiveWorkerTest(TransactionTestCase):
    """The rewired task on a real Celery worker against the real Redis
    broker: a normal run, a redelivery while the original is held inside
    the AI call, and a stale claim taken over."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.queue = f"s7-edit-{uuid.uuid4().hex[:10]}"

    def setUp(self):
        (
            self.teacher,
            self.student,
            self.course,
            self.assignment,
            self.submission,
        ) = _classroom(f"worker-{uuid.uuid4().hex[:6]}")
        self.outcomes = []
        self._lock = threading.Lock()
        task_postrun.connect(self._on_postrun, weak=False, dispatch_uid=self.id())
        worker_shutdown.connect(
            self._on_shutdown, weak=False, dispatch_uid=f"{self.id()}-shutdown"
        )

    def tearDown(self):
        task_postrun.disconnect(self._on_postrun, dispatch_uid=self.id())
        worker_shutdown.disconnect(
            self._on_shutdown, dispatch_uid=f"{self.id()}-shutdown"
        )
        redis.Redis.from_url(settings.CELERY_BROKER_URL).delete(self.queue)
        super().tearDown()

    def _on_postrun(self, sender=None, task_id=None, state=None, retval=None, **kw):
        try:
            if getattr(sender, "name", None) == extract_answer_background_task.name:
                with self._lock:
                    self.outcomes.append((task_id, state, retval))
        finally:
            connections.close_all()

    @staticmethod
    def _on_shutdown(**kw):
        connections.close_all()

    def _wait(self, predicate, what, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError(f"timed out waiting for {what}")

    def _publish(self, tracked, task_id):
        return extract_answer_background_task.apply_async(
            args=(str(self.submission.id), EDITED_TEXT, str(self.student.id)),
            kwargs={"processing_task_id": str(tracked.id)},
            queue=self.queue,
            task_id=task_id,
        )

    def _worker(self):
        return start_worker(
            celery_app,
            queues=[self.queue],
            concurrency=3,
            pool="threads",
            perform_ping_check=False,
            loglevel="WARNING",
            shutdown_timeout=30,
        )

    def test_redelivery_while_running_skips_and_the_original_lands_once(self):
        tracked = BackgroundProcessingTask.objects.create(
            requested_by=self.student,
            task_type=BackgroundTaskType.ANSWER_EXTRACTION,
            assignment=self.assignment,
            submission=self.submission,
        )
        task_id = str(uuid.uuid4())
        release = threading.Event()
        calls = []

        def held(*args, **kwargs):
            calls.append(1)
            release.wait(timeout=45)
            return EXTRACTED

        ai = patch("students.services.ai_processor").start()
        self.addCleanup(patch.stopall)
        ai.extract_answer_with_retry.side_effect = held

        with self._worker():
            self._publish(tracked, task_id)
            self._wait(lambda: len(calls) == 1, "original to enter the AI call")
            self._publish(tracked, task_id)  # the redelivery
            self._wait(
                lambda: len([o for o in self.outcomes if o[0] == task_id]) == 1,
                "the duplicate to skip",
            )
            _, state, retval = [o for o in self.outcomes if o[0] == task_id][0]
            self.assertEqual(state, "SUCCESS")
            self.assertIn("duplicate run skipped", retval["message"])
            tracked.refresh_from_db()
            self.assertEqual(tracked.status, BackgroundTaskStatus.STARTED)

            release.set()
            self._wait(
                lambda: len([o for o in self.outcomes if o[0] == task_id]) == 2,
                "the original to finish",
            )

        self.submission.refresh_from_db()
        self.assertEqual(
            self.submission.answers[0]["answer_html"], "<p>edited answer</p>"
        )
        tracked.refresh_from_db()
        self.assertEqual(tracked.status, BackgroundTaskStatus.SUCCESS)
        self.assertEqual(ai.extract_answer_with_retry.call_count, 1)


RUN_REAL_AI = os.environ.get("RUN_REAL_AI") == "1"


@unittest.skipUnless(
    RUN_REAL_AI, "Real AI call is opt-in and billed: set RUN_REAL_AI=1"
)
class RealProviderEditTest(TestCase):
    """One real, billed extraction through the edit service: the provider
    is called exactly once, the answers come back from the text, and the
    wallet is charged exactly once. Requires the teacher to have an active
    subscription context (the AI access gate), built as in
    assignments/tests_real_extraction.py."""

    def setUp(self):
        from billing.models import (
            BillingInterval,
            PlanCategory,
            PlanTier,
            PlanType,
            SubscriptionPlan,
            UserSubscription,
        )

        (
            self.teacher,
            self.student,
            self.course,
            self.assignment,
            self.submission,
        ) = _classroom("real")
        plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Real Edit",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            interval=BillingInterval.MONTHLY,
            monthly_credits=500_000,
            overage_block_size=500,
            overage_block_price=10,
            max_overage_blocks=10,
            is_active=True,
        )
        now = timezone.now()
        UserSubscription.objects.create(
            user=self.teacher,
            plan=plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - timedelta(days=1),
            billing_cycle_end=now + timedelta(days=29),
            next_credit_grant_at=now + timedelta(days=29),
        )
        wallet = CreditWallet.objects.get(user=self.teacher)
        CreditBucket.objects.filter(wallet=wallet).delete()
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=500_000,
            used_credits=0,
            expires_at=now + timedelta(days=25),
        )
        Assignment.objects.filter(pk=self.assignment.pk).update(
            questions=[
                {
                    "question_number": 1,
                    "question_text": "What is the capital city of Iceland?",
                    "question_type": "SHORT-ANSWER",
                    "points": 5,
                }
            ]
        )
        self.assignment.refresh_from_db()
        self.submission.assignment = self.assignment

    def test_a_real_edit_extracts_the_answer_and_charges_once(self):
        from billing.models import CreditUsageLog

        before = CreditUsageLog.objects.count()
        wallet = CreditWallet.objects.get(user=self.teacher)
        used_before = wallet.total_remaining_credits()

        result = update_submission_from_raw_text(
            self.teacher,
            self.submission,
            "Question 1: The capital city of Iceland is Reykjavik.",
        )

        self.assertTrue(result.answers, "no answers came back from the provider")
        joined = " ".join(str(a.get("answer_html", "")) for a in result.answers)
        self.assertIn("reykjav", joined.lower())
        self.assertGreater(result.extraction_confidence, 0)
        wallet = CreditWallet.objects.get(user=self.teacher)
        self.assertLess(
            wallet.total_remaining_credits(), used_before, "nothing was charged"
        )
        self.assertEqual(
            CreditUsageLog.objects.count() - before, 1, "charged more than once"
        )
