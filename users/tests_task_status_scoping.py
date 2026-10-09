"""
TaskViewSet.task_status must only ever report on tasks the caller started.

This endpoint used to fall back to a bare `AsyncResult(task_id)` whenever no
tracked BackgroundProcessingTask matched. Because `get_processing_task`
filters on `requested_by`, "no tracked task matched" is precisely what
happens when the task belongs to SOMEBODY ELSE - so the fallback served
another user's task state and return value to anyone holding the id. Celery
task return values are not innocuous: `AutoGrader.tasks.send_email_task`
returns a string containing the recipient's email address.

These tests pin the closed behaviour: a task id the caller does not own is
indistinguishable from one that does not exist, and neither reaches Celery.
"""

import uuid
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
)
from users.models import CustomUser, UserTypes


class TaskStatusOwnershipScopingTests(APITestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="owner-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Owner",
            last_name="Teacher",
        )
        self.other_teacher = CustomUser.objects.create_user(
            email="intruder-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Intruder",
            last_name="Teacher",
        )

    def _url(self, task_id):
        return reverse("task-task-status", kwargs={"task_id": str(task_id)})

    def _tracked_task(self, owner, **overrides):
        defaults = {
            "requested_by": owner,
            "celery_task_id": str(uuid.uuid4()),
            "task_type": BackgroundTaskType.SUBMISSION_GRADING,
            "status": BackgroundTaskStatus.SUCCESS,
            "meta": {"step": "Graded", "owner_note": "owner-only payload"},
        }
        defaults.update(overrides)
        return BackgroundProcessingTask.objects.create(**defaults)

    def test_owner_can_read_their_own_task_status(self):
        task = self._tracked_task(self.teacher)
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(self._url(task.celery_task_id))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "completed")
        self.assertIn("owner-only payload", response.data["meta"])

    def test_another_users_task_is_not_readable(self):
        """The core regression: this used to return 200 with the task's state."""
        task = self._tracked_task(self.teacher)
        self.client.force_authenticate(user=self.other_teacher)

        response = self.client.get(self._url(task.celery_task_id))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertNotIn("owner-only payload", response.content.decode())

    def test_unknown_task_id_is_not_found(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(self._url(uuid.uuid4()))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("users.views.get_processing_task", return_value=None)
    def test_untracked_task_id_never_reaches_the_celery_backend(self, _mock_get):
        """
        No tracked row must mean no Celery lookup at all. If any AsyncResult
        fallback is ever reintroduced, this fails: a Celery id carries no
        owner, so there is nothing to authorise the read against.
        """
        self.client.force_authenticate(user=self.teacher)

        with patch("celery.result.AsyncResult") as mock_async_result:
            response = self.client.get(self._url(uuid.uuid4()))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_async_result.assert_not_called()

    def test_a_foreign_task_is_indistinguishable_from_a_missing_one(self):
        """
        Same status and same body for "someone else's" and "does not exist",
        so the endpoint can't be used to probe which task ids are real.
        """
        foreign = self._tracked_task(self.teacher)
        self.client.force_authenticate(user=self.other_teacher)

        foreign_response = self.client.get(self._url(foreign.celery_task_id))
        missing_response = self.client.get(self._url(uuid.uuid4()))

        self.assertEqual(foreign_response.status_code, missing_response.status_code)
        self.assertEqual(
            foreign_response.data["detail"], missing_response.data["detail"]
        )

    def test_status_requires_authentication(self):
        task = self._tracked_task(self.teacher)

        response = self.client.get(self._url(task.celery_task_id))

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
