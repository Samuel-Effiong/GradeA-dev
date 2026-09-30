"""
FR-A-07 S7b: per-item retry (08a §4.4, §5; F4).

  POST tasks/session/{session_id}/items/{item_id}/retry
  POST tasks/session/{session_id}/retry-failed  {"reason_codes": [...]}  (optional)

* Only a FAILED GRADE item whose code is retryable as it is
  (PROVIDER_FAILURE, INSUFFICIENT_CREDITS_MID_BATCH) is retried, IN PLACE:
  the same item_id, retry_count + 1, back to PENDING, relaunched with a new
  Celery id. 202 with the item in session-results' shape.
* Anything else is a 409 NOT_RETRYABLE and nothing is launched. An UPLOAD
  item is never retried in place (its file isn't kept, F4): it answers
  "upload the file again", params.resolution = "replace_file".
* Two retries of one item at once: exactly one wins.
* retry-failed retries every retryable grade item (optionally only those
  codes) and lists the rest as skipped, with their codes.
* `resolve` is deferred (F2/F3).
"""

import threading
import uuid
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from audit.enums import AuditAction
from audit.models import AuditEvent
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
    StudentSubmission,
)
from users.models import CustomUser, UserTypes

GRADE = BackgroundTaskType.BATCH_SUBMISSION_GRADING
UPLOAD = BackgroundTaskType.BATCH_ANSWER_UPLOAD


class RetryFixture:
    #: Supplied by the TestCase this mixin is combined with.
    addCleanup: Callable[..., Any]
    assertEqual: Callable[..., Any]

    def build(self):
        cache.clear()
        self.teacher = self.user("teacher", UserTypes.TEACHER)
        self.other = self.user("other", UserTypes.TEACHER)
        course = Course.objects.create(
            name="Maths",
            teacher=self.teacher,
            session=Session.objects.create(name="S7b", teacher=self.teacher),
        )
        self.assignment = Assignment.objects.create(
            title="Homework",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )
        self.session = BatchUploadSession.objects.create(
            teacher=self.teacher,
            course=course,
            task_type=BatchUploadType.GRADE,
            total_files=5,
        )
        self.api = APIClient()
        self.api.force_authenticate(user=self.teacher)
        self.launched = []

        def launch(task_callable, processing_task, *args, **kwargs):
            self.launched.append((task_callable.name, processing_task.id, args, kwargs))
            celery_id = str(uuid.uuid4())
            BackgroundProcessingTask.objects.filter(pk=processing_task.pk).update(
                celery_task_id=celery_id
            )
            return SimpleNamespace(id=celery_id)

        for target in (
            patch("students.item_retry.launch_processing_task", side_effect=launch),
            patch(
                "users.permissions.HasCreditBalance.has_permission", return_value=True
            ),
        ):
            target.start()
            self.addCleanup(target.stop)
        self.index = 0

    def user(self, key, user_type):
        return CustomUser.objects.create_user(
            email=f"s7b-{key}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=user_type,
            first_name=key.title(),
            last_name="S7b",
        )

    def item(self, task_type=GRADE, status=BackgroundTaskStatus.FAILURE, code=""):
        self.index += 1
        student = self.user(f"student{self.index}", UserTypes.STUDENT)
        StudentCourse.objects.create(
            student=student,
            course=self.assignment.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "<p>4</p>"}],
        )
        return BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=task_type,
            batch_session=self.session,
            assignment=self.assignment,
            submission=submission if task_type == GRADE else None,
            file_name=f"item {self.index}",
            item_index=self.index,
            status=status,
            reason_code=code,
            error="The grading service couldn't finish this item." if code else "x",
            celery_task_id=str(uuid.uuid4()),
        )

    def retry(self, item, session=None):
        return self.api.post(
            reverse(
                "task-retry-item",
                kwargs={
                    "session_id": str((session or self.session).id),
                    "item_id": str(item.id),
                },
            )
        )

    def retry_failed(self, body=None):
        return self.api.post(
            reverse("task-retry-failed", kwargs={"session_id": str(self.session.id)}),
            body or {},
            format="json",
        )

    def body(self, response):
        return response.json()["data"]

    def refused(self, response):
        self.assertEqual(response.status_code, 409, response.content[:400])
        envelope = response.json()["error"]["field_errors"]
        self.assertEqual(envelope["reason_code"], "NOT_RETRYABLE")
        return envelope


class RetryOneItem(RetryFixture, TestCase):
    def setUp(self):
        self.build()

    def test_a_provider_failure_is_retried_in_place(self):
        item = self.item(code="PROVIDER_FAILURE")
        old_celery_id = item.celery_task_id

        response = self.retry(item)

        self.assertEqual(response.status_code, 202, response.content[:400])
        entry = self.body(response)
        self.assertEqual(entry["item_id"], str(item.id))
        self.assertEqual(entry["retry_count"], 1)
        self.assertEqual(entry["status"], BackgroundTaskStatus.PENDING)
        item.refresh_from_db()
        self.assertEqual(item.retry_count, 1)
        self.assertEqual(item.status, BackgroundTaskStatus.PENDING)
        self.assertEqual(item.reason_code, "")
        self.assertEqual(item.error, "")
        self.assertNotEqual(item.celery_task_id, old_celery_id)
        [(name, item_id, args, kwargs)] = self.launched
        self.assertEqual(name, "assignments.tasks.grade_engine_async")
        self.assertEqual(item_id, item.id)
        self.assertEqual(args, (str(self.teacher.id), str(item.submission_id)))
        self.assertEqual(kwargs, {"batch_id": str(self.session.id)})
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditAction.GRADING_REQUESTED, target_id=str(item.submission_id)
            ).exists()
        )

    def test_a_mid_batch_credit_stop_is_retried_after_a_top_up(self):
        item = self.item(code="INSUFFICIENT_CREDITS_MID_BATCH")

        self.assertEqual(self.retry(item).status_code, 202)
        item.refresh_from_db()
        self.assertEqual(item.retry_count, 1)

    def test_a_second_retry_counts_again_on_the_same_item(self):
        item = self.item(code="PROVIDER_FAILURE")
        self.retry(item)
        BackgroundProcessingTask.objects.filter(pk=item.pk).update(
            status=BackgroundTaskStatus.FAILURE, reason_code="PROVIDER_FAILURE"
        )

        self.assertEqual(self.retry(item).status_code, 202)
        item.refresh_from_db()
        self.assertEqual(item.retry_count, 2)
        self.assertEqual(BackgroundProcessingTask.objects.count(), 1)

    def test_a_code_that_is_not_retryable_as_it_is_is_a_409(self):
        for code in ("", "RUBRIC_MISSING", "FILE_UNREADABLE"):
            with self.subTest(code=code or "none"):
                item = self.item(code=code)
                envelope = self.refused(self.retry(item))
                item.refresh_from_db()
                self.assertEqual(item.retry_count, 0)
                self.assertEqual(item.status, BackgroundTaskStatus.FAILURE)
                self.assertNotEqual(
                    envelope["params"].get("resolution"), "replace_file"
                )
        self.assertEqual(self.launched, [])

    def test_an_item_that_has_not_failed_is_a_409(self):
        for status in (BackgroundTaskStatus.SUCCESS, BackgroundTaskStatus.PENDING):
            with self.subTest(status=status):
                self.refused(self.retry(self.item(status=status, code="")))
        self.assertEqual(self.launched, [])

    def test_an_upload_item_answers_upload_the_file_again(self):
        # Even a retryable code: the file is not kept (F4).
        for code in ("FILE_UNREADABLE", "PROVIDER_FAILURE"):
            with self.subTest(code=code):
                item = self.item(task_type=UPLOAD, code=code)
                envelope = self.refused(self.retry(item))
                self.assertEqual(envelope["params"]["resolution"], "replace_file")
                self.assertIn("upload the file again", envelope["error"].lower())
                item.refresh_from_db()
                self.assertEqual(item.retry_count, 0)
        self.assertEqual(self.launched, [])

    def test_another_teachers_session_or_a_foreign_item_is_a_404(self):
        item = self.item(code="PROVIDER_FAILURE")
        self.api.force_authenticate(user=self.other)
        self.assertEqual(self.retry(item).status_code, 404)

        self.api.force_authenticate(user=self.teacher)
        elsewhere = BatchUploadSession.objects.create(
            teacher=self.teacher, task_type=BatchUploadType.GRADE, total_files=1
        )
        self.assertEqual(self.retry(item, session=elsewhere).status_code, 404)
        self.assertEqual(self.launched, [])


class RetryFailed(RetryFixture, TestCase):
    def setUp(self):
        self.build()
        self.p1 = self.item(code="PROVIDER_FAILURE")
        self.p2 = self.item(code="PROVIDER_FAILURE")
        self.credit = self.item(code="INSUFFICIENT_CREDITS_MID_BATCH")
        self.uncoded = self.item(code="")
        self.done = self.item(status=BackgroundTaskStatus.SUCCESS)

    def test_every_retryable_failure_is_retried_and_the_rest_listed(self):
        response = self.retry_failed()

        self.assertEqual(response.status_code, 202, response.content[:400])
        data = self.body(response)
        self.assertEqual(
            sorted(data["retried"]),
            sorted(str(i.id) for i in (self.p1, self.p2, self.credit)),
        )
        self.assertEqual(
            data["skipped"], [{"item_id": str(self.uncoded.id), "reason_code": None}]
        )
        self.assertEqual(len(self.launched), 3)

    def test_only_the_named_codes_are_retried(self):
        data = self.body(self.retry_failed({"reason_codes": ["PROVIDER_FAILURE"]}))

        self.assertEqual(
            sorted(data["retried"]), sorted(str(i.id) for i in (self.p1, self.p2))
        )
        self.assertEqual(
            sorted((s["item_id"], s["reason_code"]) for s in data["skipped"]),
            sorted(
                [
                    (str(self.credit.id), "INSUFFICIENT_CREDITS_MID_BATCH"),
                    (str(self.uncoded.id), None),
                ]
            ),
        )


class TwoRetriesOfOneItemAtOnce(RetryFixture, TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.build()

    def test_exactly_one_wins(self):
        item = self.item(code="PROVIDER_FAILURE")
        barrier = threading.Barrier(2)
        statuses = []

        def attempt():
            client = APIClient()
            client.force_authenticate(user=self.teacher)
            try:
                barrier.wait(timeout=10)
                statuses.append(
                    client.post(
                        reverse(
                            "task-retry-item",
                            kwargs={
                                "session_id": str(self.session.id),
                                "item_id": str(item.id),
                            },
                        )
                    ).status_code
                )
            finally:
                connection.close()

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(sorted(statuses), [202, 409])
        item.refresh_from_db()
        self.assertEqual(item.retry_count, 1)
        self.assertEqual(len(self.launched), 1)
