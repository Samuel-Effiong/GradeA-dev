"""
CourseViewSet.student_summary dispatches a TRACKED background task.

It used to call `student_summary_async.delay(...)` directly and hand the
frontend the raw Celery id. That id had no owner, which is why
users.views.TaskViewSet.task_status needed an unguarded `AsyncResult`
fallback to report on it - and that fallback served any authenticated
caller any task's state. This endpoint was the only thing depending on it.

These tests pin down that the summary is now dispatched through
create_processing_task/launch_processing_task, owned by the requesting
teacher, so it polls and cancels like every other async endpoint and needs
no fallback.
"""

from datetime import timedelta
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import CreditBucket, CreditBucketType, CreditWallet
from classrooms.models import Course, StudentCourse
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
)
from students.task_context import get_task_context
from users.models import CustomUser, UserTypes


class StudentSummaryTaskTrackingTests(APITestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="summary-teacher@gmail.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Summary",
            last_name="Teacher",
            is_active=True,
        )
        self.student = CustomUser.objects.create_user(
            email="summary-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Sam",
            last_name="Student",
            is_active=True,
        )
        self.course = Course.objects.create(
            name="Summary 101",
            teacher=self.teacher,
            description="Course for student summary tracking tests.",
        )
        self.enrollment = StudentCourse.objects.create(
            course=self.course, student=self.student
        )

        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )

        self.url = reverse("course-student-summary", kwargs={"pk": self.course.pk})
        self.client.force_authenticate(user=self.teacher)

    def _request(self):
        return self.client.get(self.url, {"student_id": str(self.student.id)})

    @patch("classrooms.views.student_summary_async")
    def test_creates_a_tracked_task_owned_by_the_requesting_teacher(self, mock_task):
        mock_task.delay.return_value.id = "celery-id-123"

        response = self._request()

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        processing_task = BackgroundProcessingTask.objects.get(
            task_type=BackgroundTaskType.STUDENT_SUMMARY
        )
        self.assertEqual(processing_task.requested_by, self.teacher)
        self.assertEqual(processing_task.celery_task_id, "celery-id-123")
        self.assertEqual(processing_task.status, BackgroundTaskStatus.PENDING)

    @patch("classrooms.views.student_summary_async")
    def test_returned_task_id_is_the_celery_id_the_poller_looks_up(self, mock_task):
        mock_task.delay.return_value.id = "celery-id-123"

        response = self._request()

        # The endpoint must hand back the id that task_status resolves
        # against BackgroundProcessingTask.celery_task_id - not the tracking
        # row's own pk, and not an AsyncResult object.
        self.assertEqual(str(response.data["task_id"]), "celery-id-123")

    @patch("classrooms.views.student_summary_async")
    def test_the_task_receives_the_processing_task_id(self, mock_task):
        mock_task.delay.return_value.id = "celery-id-123"

        self._request()

        processing_task = BackgroundProcessingTask.objects.get(
            task_type=BackgroundTaskType.STUDENT_SUMMARY
        )
        _, kwargs = mock_task.delay.call_args
        self.assertEqual(kwargs["processing_task_id"], str(processing_task.id))

    @patch("classrooms.views.student_summary_async")
    def test_student_and_course_ids_are_recorded_for_context(self, mock_task):
        mock_task.delay.return_value.id = "celery-id-123"

        self._request()

        processing_task = BackgroundProcessingTask.objects.get(
            task_type=BackgroundTaskType.STUDENT_SUMMARY
        )
        # BackgroundProcessingTask has no student/course FK, so these have to
        # survive in meta or the frontend can't correlate the result.
        self.assertEqual(processing_task.meta["student_id"], str(self.student.id))
        self.assertEqual(processing_task.meta["course_id"], str(self.course.id))

        context = get_task_context(processing_task)
        self.assertEqual(context["resource_type"], "student_summary")
        self.assertEqual(context["resource_id"], str(self.student.id))
        self.assertEqual(context["action"], "summarised")
        self.assertEqual(context["additional_ids"]["course_id"], str(self.course.id))

    @patch("classrooms.views.student_summary_async")
    def test_the_new_task_is_pollable_through_task_status(self, mock_task):
        """End-to-end: the id this endpoint returns resolves for its owner."""
        mock_task.delay.return_value.id = "celery-id-123"
        task_id = self._request().data["task_id"]

        status_url = reverse("task-task-status", kwargs={"task_id": str(task_id)})
        status_response = self.client.get(status_url)

        self.assertEqual(status_response.status_code, status.HTTP_200_OK)
        self.assertEqual(status_response.data["status"], "processing")
        self.assertEqual(status_response.data["resource_type"], "student_summary")

    @patch("classrooms.views.student_summary_async")
    def test_another_teacher_cannot_poll_this_summary(self, mock_task):
        mock_task.delay.return_value.id = "celery-id-123"
        task_id = self._request().data["task_id"]

        intruder = CustomUser.objects.create_user(
            email="intruder@gmail.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Nosy",
            last_name="Teacher",
            is_active=True,
        )
        self.client.force_authenticate(user=intruder)

        status_url = reverse("task-task-status", kwargs={"task_id": str(task_id)})
        status_response = self.client.get(status_url)

        self.assertEqual(status_response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("classrooms.views.student_summary_async")
    def test_a_cached_summary_still_short_circuits_without_a_task(self, mock_task):
        self.enrollment.ai_summary = "Already generated."
        self.enrollment.ai_summary_generated_at = timezone.now()
        self.enrollment.save()

        response = self._request()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["cached"])
        mock_task.delay.assert_not_called()
        self.assertFalse(
            BackgroundProcessingTask.objects.filter(
                task_type=BackgroundTaskType.STUDENT_SUMMARY
            ).exists()
        )

    @patch("classrooms.views.student_summary_async")
    def test_broker_outage_returns_503_and_marks_the_task_failed(self, mock_task):
        from kombu.exceptions import OperationalError

        mock_task.delay.side_effect = OperationalError("broker down")

        response = self._request()

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        processing_task = BackgroundProcessingTask.objects.get(
            task_type=BackgroundTaskType.STUDENT_SUMMARY
        )
        self.assertEqual(processing_task.status, BackgroundTaskStatus.FAILURE)
