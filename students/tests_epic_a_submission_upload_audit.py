"""Epic A §6: submission-upload audit instrumentation (the sync, async and
batch upload endpoints - three separate paths per the plan's own survey).

Each test drives the real HTTP endpoint once and asserts FR-A-01's literal
wording - exactly one AuditEvent, not >= 1. Only the billed AI call/Celery
dispatch is stubbed; everything else is the real view, permission and
credit-gate chain (HasCreditBalance checks the TEACHER's wallet even for a
student's own submission - see students.tests_submission_tenancy's
_funded_teacher, the same pattern reused here).

Run with:
    python manage.py test students.tests_epic_a_submission_upload_audit --settings=settings_worktree
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from audit import history
from audit.enums import AuditAction, AuditOutcome
from audit.models import AuditEvent
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

PDF = b"%PDF-1.4\n%%EOF\n"


class SubmissionUploadAuditTest(APITestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="upload-audit-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Upload",
            last_name="Teacher",
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.session = Session.objects.create(name="Upload Term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Upload Course", teacher=self.teacher, session=self.session
        )
        self.assignment = Assignment.objects.create(
            title="Upload Assignment",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[{"question_number": 1, "question_text": "Q?", "points": 10}],
        )
        self.student = CustomUser.objects.create_user(
            email="upload-audit-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Upload",
            last_name="Student",
        )
        # Epic A S4: the fixture enrolment would itself be a ROSTER_CHANGE;
        # these tests are about the upload's one event.
        with history.suppressed():
            StudentCourse.objects.create(
                student=self.student,
                course=self.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )

    def _file(self):
        return SimpleUploadedFile("answers.pdf", PDF, content_type="application/pdf")

    @patch("students.services.ai_processor")
    @patch("students.views.AssignmentProcessingService.prepare_ai_content")
    def test_sync_upload_emits_exactly_one_event(self, mock_prepare, mock_ai):
        mock_prepare.return_value = "content"
        mock_ai.extract_answer_with_retry.return_value = {
            "answers": [{"question_number": 1, "answer_html": "4"}]
        }
        self.client.force_authenticate(user=self.student)

        response = self.client.post(
            reverse(
                "student-submission-upload-answers",
                kwargs={"assignment_id": self.assignment.id},
            ),
            {"answer": self._file()},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        submission = StudentSubmission.objects.get(
            student=self.student, assignment=self.assignment
        )
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.SUBMISSION_UPLOAD)
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertEqual(str(event.target_id), str(submission.id))
        self.assertEqual(event.metadata["assignment_id"], str(self.assignment.id))
        # STUDENT actor: no email/IP/UA on the row (audit_student_no_pii_ck).
        self.assertIsNone(event.actor_email)

    @patch("students.views.launch_processing_task")
    def test_async_upload_emits_exactly_one_event(self, mock_launch):
        mock_launch.return_value = MagicMock(id="celery-task-id")
        self.client.force_authenticate(user=self.student)

        response = self.client.post(
            reverse(
                "student-submission-upload-async",
                kwargs={"assignment_id": self.assignment.id},
            ),
            {"answer": self._file()},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.SUBMISSION_UPLOAD)
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertEqual(str(event.target_id), str(self.assignment.id))

    @patch("students.views.launch_processing_task")
    def test_batch_upload_emits_exactly_one_event_regardless_of_file_count(
        self, mock_launch
    ):
        mock_launch.return_value = MagicMock(id="celery-task-id")
        self.client.force_authenticate(user=self.teacher)

        response = self.client.post(
            reverse(
                "student-submission-batch-upload",
                kwargs={"assignment_id": self.assignment.id},
            ),
            {"answers": [self._file(), self._file(), self._file()]},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.SUBMISSION_UPLOAD)
        self.assertEqual(str(event.target_id), str(self.assignment.id))
        self.assertEqual(event.metadata["file_count"], 3)
