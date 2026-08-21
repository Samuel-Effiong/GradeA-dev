"""
End-to-end coverage for Redis/Celery-broker-outage resilience.

Two distinct contracts are being locked down here, matching the two kinds
of dispatch identified in AutoGrader/dispatch.py:

- User-initiated processing dispatch (grading, uploads), which all funnel
  through students.task_tracking.launch_processing_task, must surface a
  clean 503 ("processing is temporarily unavailable") instead of a raw
  broker connection traceback when Redis is unreachable.
- Non-critical, view-triggered side-effect dispatch (email/notification
  sends via AutoGrader.dispatch.safe_delay) must never break the request
  that triggered it, even when Redis is unreachable.
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from classrooms.models import Course, Session
from students.models import BackgroundProcessingTask, StudentSubmission
from users.models import CustomUser, UserTypes


class GradeAsyncBrokerOutageTest(APITestCase):
    """The grade-async endpoint is the most direct example of a
    user-initiated dispatch: a teacher clicks "grade this submission" and
    the request itself queues the work. Losing the broker here must not
    look like a generic server error."""

    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="broker-outage-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        session = Session.objects.create(name="S", teacher=self.teacher)
        course = Course.objects.create(name="C", teacher=self.teacher, session=session)
        self.assignment = Assignment.objects.create(
            title="A",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )
        student = CustomUser.objects.create_user(
            email="broker-outage-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        self.submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "An answer."}],
        )

        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )

        self.url = reverse(
            "student-submission-grade-async", kwargs={"pk": self.submission.pk}
        )
        self.client.force_authenticate(user=self.teacher)

    @patch("students.views.grade_engine_async")
    def test_broker_outage_returns_clean_503_not_a_raw_traceback(self, mock_task):
        mock_task.delay.side_effect = ConnectionError("broker unreachable")

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        # response.data is DRF's pre-render data and bypasses the project's
        # APIJSONRenderer wrapping (success/message/error) - assert against
        # the actual rendered body a real client receives instead.
        body = response.json()
        message = body.get("message", "")
        self.assertIn("temporarily unavailable", message.lower())
        # The raw connection error text is an implementation detail and
        # must never reach the client.
        self.assertNotIn("broker unreachable", str(body))

        # The tracked task reflects the real outcome instead of being left
        # orphaned in PENDING forever.
        task = BackgroundProcessingTask.objects.filter(
            submission=self.submission
        ).first()
        self.assertIsNotNone(task)
        self.assertEqual(task.status, "FAILURE")

    @patch("students.views.grade_engine_async")
    def test_healthy_broker_still_dispatches_normally(self, mock_task):
        # Regression guard: the happy path must be completely unaffected.
        mock_task.delay.return_value.id = "fake-celery-task-id"

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_task.delay.assert_called_once()


class SafeDelaySideEffectOutageTest(TestCase):
    """A side-effect dispatch (post-publish notification email) must never
    break the action that triggered it, even when the broker is down."""

    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="safe-delay-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        session = Session.objects.create(name="S", teacher=self.teacher)
        self.course = Course.objects.create(
            name="C", teacher=self.teacher, session=session
        )

    @patch("assignments.tasks.send_new_assignment_posted_notification")
    def test_publishing_an_assignment_succeeds_even_if_broker_is_down(
        self, mock_notification_task
    ):
        mock_notification_task.delay.side_effect = ConnectionError("broker unreachable")

        # This is the actual user-facing action (publishing an assignment)
        # that the post_save signal piggybacks a best-effort notification
        # onto. It must succeed regardless of the notification dispatch.
        # The signal dispatches via transaction.on_commit, which TestCase's
        # wrapping transaction never actually commits — captureOnCommitCallbacks
        # runs those callbacks explicitly so this test exercises the real
        # failure path instead of silently skipping it.
        with self.captureOnCommitCallbacks(execute=True):
            assignment = Assignment.objects.create(
                title="A",
                course=self.course,
                status=AssignmentStatus.PUBLISHED,
                questions=[
                    {"question_number": 1, "question_text": "Q1?", "points": 10}
                ],
            )

        assignment.refresh_from_db()
        self.assertEqual(assignment.status, AssignmentStatus.PUBLISHED)
        mock_notification_task.delay.assert_called_once()
