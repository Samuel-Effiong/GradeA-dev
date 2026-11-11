"""Epic B1b-6 (08b §1.4, SM ruling 2026-11-08; Q1 and Q11, NOTES §6.3): the
characterization of grade-all, pinned with the flag off.

Committed green on today's code, before grade-all's own loop is replaced by
the one dispatch helper (`assignments.tasks._dispatch_tracked_grading`). It
must stay green, unchanged, afterwards: flag off is byte-for-byte today's
path. It pins the items, their indexes, the messages, the audit rows (with
the request's data) and the response body.

The message is caught on `grade_engine_async.delay`, the one object both the
view and the helper reach, so the pin does not depend on which module sends.

    python manage.py test assignments.tests_grade_all_characterization
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from assignments.tasks import grade_engine_async
from assignments.tests_cache_matrix_g3 import question
from audit.enums import AuditAction
from audit.models import AuditEvent
from billing.tests.test_h38_part2_removed_teacher_routes import fund_wallet
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
    StudentSubmission,
)
from users.models import CustomUser, UserTypes

NAMES = (("Ada", "Lovelace"), ("Alan", "Turing"), ("Grace", "Hopper"))


@override_settings(BILLING_HOLDS_ENABLED=False)
class GradeAllCharacterizationTests(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="b6.char.teacher@example.com",
            password="testpass123",  # pragma: allowlist secret  # nosec B106
            user_type=UserTypes.TEACHER,
        )
        fund_wallet(self.teacher)
        session = Session.objects.create(name="S", teacher=self.teacher)
        self.course = Course.objects.create(
            name="C", teacher=self.teacher, session=session
        )
        self.assignment = Assignment.objects.create(
            title="A",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(1)],
        )
        self.submissions: dict = {}
        for first, last in NAMES:
            student = CustomUser.objects.create_user(
                email=f"b6.char.{first.lower()}@example.com",
                password="testpass123",  # pragma: allowlist secret  # nosec B106
                user_type=UserTypes.STUDENT,
                first_name=first,
                last_name=last,
            )
            StudentCourse.objects.create(
                student=student,
                course=self.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            submission = StudentSubmission.objects.create(
                assignment=self.assignment, student=student, answers={}
            )
            self.submissions[submission.pk] = f"{first} {last}"
        self.client = APIClient(raise_request_exception=False)
        self.client.force_authenticate(user=self.teacher)

    def grade_all(self):
        self.sent = []

        def delay(*args, **kwargs):
            celery_id = f"celery-{uuid.uuid4().hex[:12]}"
            self.sent.append((args, kwargs, celery_id))
            return SimpleNamespace(id=celery_id)

        with patch.object(grade_engine_async, "delay", side_effect=delay):
            return self.client.post(
                reverse("assignment-grade-all", args=[self.assignment.pk]),
                {},
                format="json",
                HTTP_USER_AGENT="b6-characterization",
                REMOTE_ADDR="10.6.6.6",
            )

    def test_grade_all_as_it_is_today(self):
        response = self.grade_all()
        self.assertEqual(response.status_code, 202, response.content[:400])

        session = BatchUploadSession.objects.get()
        items = list(
            BackgroundProcessingTask.objects.filter(batch_session=session).order_by(
                "item_index"
            )
        )

        # The items: one per ungraded paper, numbered from 1.
        self.assertEqual([item.item_index for item in items], [1, 2, 3])
        for item in items:
            with self.subTest(item=item.item_index):
                self.assertEqual(
                    item.task_type, BackgroundTaskType.BATCH_SUBMISSION_GRADING
                )
                self.assertEqual(item.requested_by_id, self.teacher.pk)
                self.assertEqual(item.assignment_id, self.assignment.pk)
                self.assertEqual(
                    item.file_name,
                    f"Submission for {self.submissions[item.submission_id]}",
                )
                self.assertEqual(item.meta, {"step": "Queued for batch grading"})
                self.assertEqual(item.status, BackgroundTaskStatus.PENDING)
                self.assertIsNone(item.job_id)
                self.assertIsNone(item.hold_state)
        self.assertEqual({item.submission_id for item in items}, set(self.submissions))

        # The messages: one per item, in item order, with today's arguments
        # (the view sends the session's id as a UUID, and no
        # only_if_ungraded).
        self.assertEqual(
            [(args, kwargs) for args, kwargs, _ in self.sent],
            [
                (
                    (str(self.teacher.id), str(item.submission_id)),
                    {"batch_id": session.id, "processing_task_id": str(item.id)},
                )
                for item in items
            ],
        )
        self.assertEqual(
            [item.celery_task_id for item in items],
            [celery_id for _, _, celery_id in self.sent],
        )

        # The audit rows: one GRADING_REQUESTED per item, with the request.
        events = list(
            AuditEvent.objects.filter(action=AuditAction.GRADING_REQUESTED).order_by(
                "occurred_at", "id"
            )
        )
        self.assertEqual(len(events), 3)
        by_task = {event.metadata["task_id"]: event for event in events}
        for item in items:
            event = by_task[str(item.id)]
            with self.subTest(event=item.item_index):
                self.assertEqual(event.actor_id, self.teacher.pk)
                self.assertEqual(event.target_type, "StudentSubmission")
                self.assertEqual(event.target_id, item.submission_id)
                self.assertEqual(event.outcome, "SUCCESS")
                self.assertEqual(
                    event.metadata,
                    {
                        "assignment_id": str(self.assignment.id),
                        "submission_id": str(item.submission_id),
                        "task_id": str(item.id),
                        "task_type": BackgroundTaskType.BATCH_SUBMISSION_GRADING,
                    },
                )
                self.assertEqual(event.user_agent, "b6-characterization")
                self.assertEqual(event.source_ip, "10.6.6.6")

        # The response body.
        body = response.json()["data"]
        self.assertEqual(body["session_id"], str(session.id))
        self.assertEqual(body["message"], "Batch processing started for 3 submissions")
        self.assertEqual(
            body["tasks"],
            [
                {"file_name": item.file_name, "task_id": item.celery_task_id}
                for item in items
            ],
        )
