import uuid
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
)
from users.models import CustomUser, UserTypes


class TaskViewSetTest(APITestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="tasks-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Task",
            last_name="Teacher",
        )
        self.other_teacher = CustomUser.objects.create_user(
            email="other-tasks-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Other",
            last_name="Teacher",
        )
        self.client.force_authenticate(user=self.teacher)

    @patch("students.task_tracking.celery_app.control.revoke")
    @patch("students.task_tracking.AsyncResult.revoke")
    def test_cancel_endpoint_marks_tracked_task_cancelled(
        self, mock_async_revoke, mock_control_revoke
    ):
        task_id = str(uuid.uuid4())
        processing_task = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            celery_task_id=task_id,
            task_type=BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
            status=BackgroundTaskStatus.PENDING,
            file_name="assignment-1.pdf",
        )

        url = reverse("task-cancel", kwargs={"task_id": task_id})
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        processing_task.refresh_from_db()
        self.assertEqual(processing_task.status, BackgroundTaskStatus.CANCELLED)
        mock_async_revoke.assert_called_once_with(terminate=True, signal="SIGTERM")
        mock_control_revoke.assert_called_once_with(
            task_id, terminate=True, signal="SIGTERM"
        )

    def test_task_status_returns_cancelled_for_tracked_cancelled_task(self):
        task_id = str(uuid.uuid4())
        BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            celery_task_id=task_id,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            status=BackgroundTaskStatus.CANCELLED,
            file_name="submission.pdf",
        )

        url = reverse("task-task-status", kwargs={"task_id": task_id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "cancelled")

    def test_session_results_uses_tracked_processing_tasks(self):
        session = BatchUploadSession.objects.create(
            teacher=self.teacher,
            task_type="assignment",
            total_files=3,
        )
        BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            batch_session=session,
            celery_task_id=str(uuid.uuid4()),
            task_type=BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
            status=BackgroundTaskStatus.SUCCESS,
            file_name="assignment-a.pdf",
        )
        BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            batch_session=session,
            celery_task_id=str(uuid.uuid4()),
            task_type=BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
            status=BackgroundTaskStatus.CANCELLED,
            file_name="assignment-b.pdf",
        )
        BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            batch_session=session,
            celery_task_id=str(uuid.uuid4()),
            task_type=BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
            status=BackgroundTaskStatus.STARTED,
            file_name="assignment-c.pdf",
        )

        url = reverse("task-session-results", kwargs={"session_id": session.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["success_count"], 1)
        self.assertEqual(response.data["failure_count"], 0)
        self.assertEqual(response.data["cancelled_count"], 1)
        self.assertEqual(response.data["pending_count"], 1)
        self.assertFalse(response.data["is_complete"])

    def test_cancel_endpoint_rejects_task_owned_by_another_user(self):
        task_id = str(uuid.uuid4())
        BackgroundProcessingTask.objects.create(
            requested_by=self.other_teacher,
            celery_task_id=task_id,
            task_type=BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
            status=BackgroundTaskStatus.PENDING,
        )

        url = reverse("task-cancel", kwargs={"task_id": task_id})
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("students.task_tracking.celery_app.control.revoke")
    @patch("students.task_tracking.AsyncResult.revoke")
    def test_cancel_session_cancels_only_remaining_tasks(
        self, mock_async_revoke, mock_control_revoke
    ):
        session = BatchUploadSession.objects.create(
            teacher=self.teacher,
            task_type="assignment",
            total_files=4,
        )
        pending_task_id = str(uuid.uuid4())
        started_task_id = str(uuid.uuid4())
        cancelled_task_id = str(uuid.uuid4())
        success_task_id = str(uuid.uuid4())

        BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            batch_session=session,
            celery_task_id=pending_task_id,
            task_type=BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
            status=BackgroundTaskStatus.PENDING,
            file_name="pending.pdf",
        )
        BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            batch_session=session,
            celery_task_id=started_task_id,
            task_type=BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
            status=BackgroundTaskStatus.STARTED,
            file_name="started.pdf",
        )
        BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            batch_session=session,
            celery_task_id=cancelled_task_id,
            task_type=BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
            status=BackgroundTaskStatus.CANCELLED,
            file_name="cancelled.pdf",
        )
        BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            batch_session=session,
            celery_task_id=success_task_id,
            task_type=BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
            status=BackgroundTaskStatus.SUCCESS,
            file_name="success.pdf",
        )

        url = reverse("task-cancel-session", kwargs={"session_id": session.id})
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["cancelled_count"], 2)

        remaining_statuses = {
            task.file_name: task.status
            for task in BackgroundProcessingTask.objects.filter(batch_session=session)
        }
        self.assertEqual(
            remaining_statuses["pending.pdf"], BackgroundTaskStatus.CANCELLED
        )
        self.assertEqual(
            remaining_statuses["started.pdf"], BackgroundTaskStatus.CANCELLED
        )
        self.assertEqual(
            remaining_statuses["cancelled.pdf"], BackgroundTaskStatus.CANCELLED
        )
        self.assertEqual(
            remaining_statuses["success.pdf"], BackgroundTaskStatus.SUCCESS
        )

        self.assertEqual(mock_async_revoke.call_count, 2)
        self.assertEqual(mock_control_revoke.call_count, 2)

    def test_cancel_session_rejects_other_users_session(self):
        session = BatchUploadSession.objects.create(
            teacher=self.other_teacher,
            task_type="assignment",
            total_files=1,
        )

        url = reverse("task-cancel-session", kwargs={"session_id": session.id})
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
