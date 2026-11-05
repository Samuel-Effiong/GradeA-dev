"""H-292: "Grade all" with nothing left to grade is a coded refusal.

It used to raise DRF's ParseError, so the activity log labelled an ordinary
"nothing to do" as PARSE_ERROR (it reads like a broken request), and the
message the teacher saw had a typo ("submissons"). Owner approved
2026-11-05: reason code NOTHING_TO_GRADE, message "No ungraded submissions
to process." (typo corrected), no remediation line, status unchanged (400).
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from assignments.tests_cache_matrix_g3 import make_active_user, question
from audit.enums import AuditAction, ErrorClass, ReasonCode
from audit.models import AuditEvent
from AutoGrader.reason_codes import REASON_CODES
from billing.tests.test_h38_part2_removed_teacher_routes import fund_wallet
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import BatchUploadSession, StudentSubmission
from users.models import UserTypes

DISPATCH = "celery.app.task.Task.apply_async"
MESSAGE = "No ungraded submissions to process."


class NothingToGradeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.teacher = make_active_user("h292-t@grade.test", UserTypes.TEACHER)
        fund_wallet(self.teacher)
        self.student = make_active_user(
            "h292-s@grade.test", UserTypes.STUDENT, first_name="Nothing"
        )
        session = Session.objects.create(name="H292", teacher=self.teacher)
        self.course = Course.objects.create(
            name="H292", teacher=self.teacher, session=session
        )
        StudentCourse.objects.create(
            student=self.student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.assignment = Assignment.objects.create(
            title="H292",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(1)],
        )
        self.url = reverse("assignment-grade-all", args=[self.assignment.pk])
        self.client = APIClient(raise_request_exception=False)
        self.client.force_authenticate(user=self.teacher)

    def _post(self):
        with patch(DISPATCH) as dispatch:
            response = self.client.post(self.url, {}, format="json")
        return response, dispatch

    def _assert_coded_refusal(self, response):
        self.assertEqual(response.status_code, 400, response.content)
        envelope = response.json()["error"]["field_errors"]
        self.assertEqual(envelope["reason_code"], "NOTHING_TO_GRADE")
        self.assertEqual(envelope["error"], MESSAGE)
        self.assertEqual(envelope["error_class"], ErrorClass.USER)
        self.assertIsNone(envelope["remediation"])

    def test_no_submissions_at_all_is_nothing_to_grade(self):
        response, dispatch = self._post()
        self._assert_coded_refusal(response)
        dispatch.assert_not_called()
        self.assertFalse(BatchUploadSession.objects.exists())

    def test_every_submission_already_graded_is_nothing_to_grade(self):
        StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers=[{"question_number": 1, "answer_html": "x"}],
            graded_at=timezone.now(),
        )
        response, dispatch = self._post()
        self._assert_coded_refusal(response)
        dispatch.assert_not_called()

    def test_the_activity_log_row_carries_the_reason_code(self):
        self._post()
        row = AuditEvent.objects.filter(
            action=AuditAction.STATE_CHANGE, metadata__route="assignment-grade-all"
        ).get()
        self.assertEqual(row.reason_code, "NOTHING_TO_GRADE")
        self.assertNotEqual(row.metadata.get("reason_label"), "PARSE_ERROR")

    def test_the_catalogue_entry_is_the_approved_text(self):
        spec = REASON_CODES[ReasonCode("NOTHING_TO_GRADE")]
        self.assertEqual(spec.error_class, ErrorClass.USER)
        self.assertEqual(spec.http_status, 400)
        self.assertEqual(spec.message, MESSAGE)
        self.assertEqual(spec.remediation, "")
        self.assertFalse(spec.retryable)

    def test_an_ungraded_submission_still_starts_grading(self):
        StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers=[{"question_number": 1, "answer_html": "x"}],
        )
        response, _ = self._post()
        self.assertEqual(response.status_code, 202, response.content)
