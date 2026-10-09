"""
Tenant boundary on the submission upload endpoints (R-6 finding V-1,
standards §3): before this pass all three looked the assignment up by bare
id. A student holding an assignment id from another school could create a
submission inside that school's course (billing that teacher's credits for
the extraction), and any teacher could batch-upload files into any
assignment on the platform.

Written from the attacker's side: each test attempts the breach and asserts
it failed, then confirms the legitimate caller still succeeds.

Run with:
    python manage.py test students.tests_submission_tenancy
"""

from datetime import timedelta
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import (
    BackgroundProcessingTask,
    BatchUploadSession,
    StudentSubmission,
)
from users.models import CustomUser, UserTypes

PDF = b"%PDF-1.4\n%%EOF\n"


def _user(tag, user_type):
    return CustomUser.objects.create_user(
        email=f"{tag}@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=user_type,
        first_name=tag.replace("-", " ").title(),
        last_name="Tenant",
    )


def _funded_teacher(tag):
    teacher = _user(tag, UserTypes.TEACHER)
    wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
    CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        total_credits=100_000,
        used_credits=0,
        expires_at=timezone.now() + timedelta(days=30),
    )
    return teacher


def _course_with_assignment(teacher, tag):
    session = Session.objects.create(name=f"S-{tag}", teacher=teacher)
    course = Course.objects.create(name=f"C-{tag}", teacher=teacher, session=session)
    assignment = Assignment.objects.create(
        title=f"A-{tag}",
        course=course,
        status=AssignmentStatus.PUBLISHED,
        questions=[{"question_number": 1, "question_text": "Q?", "points": 10}],
    )
    return course, assignment


class UploadEndpointTenancyTest(APITestCase):
    def setUp(self):
        # Two tenants. The victim's assignment is fully set up and funded.
        self.victim_teacher = _funded_teacher("victim-teacher")
        self.victim_course, self.victim_assignment = _course_with_assignment(
            self.victim_teacher, "victim"
        )
        self.victim_student = _user("victim-student", UserTypes.STUDENT)
        StudentCourse.objects.create(
            student=self.victim_student,
            course=self.victim_course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        # The attacker is a legitimate user elsewhere.
        self.attacker_teacher = _funded_teacher("attacker-teacher")
        self.attacker_course, self.attacker_assignment = _course_with_assignment(
            self.attacker_teacher, "attacker"
        )
        self.attacker_student = _user("attacker-student", UserTypes.STUDENT)
        StudentCourse.objects.create(
            student=self.attacker_student,
            course=self.attacker_course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

    def _file(self):
        return SimpleUploadedFile("answers.pdf", PDF, content_type="application/pdf")

    def _sync_url(self, assignment):
        return reverse(
            "student-submission-upload-answers", kwargs={"assignment_id": assignment.pk}
        )

    def _async_url(self, assignment):
        return reverse(
            "student-submission-upload-async", kwargs={"assignment_id": assignment.pk}
        )

    def _batch_url(self, assignment):
        return reverse(
            "student-submission-batch-upload", kwargs={"assignment_id": assignment.pk}
        )

    # -- students ---------------------------------------------------------

    @patch("students.views.AssignmentProcessingService.prepare_ai_content")
    def test_student_cannot_submit_to_an_assignment_they_are_not_enrolled_in(
        self, mock_prepare
    ):
        self.client.force_authenticate(user=self.attacker_student)

        response = self.client.post(
            self._sync_url(self.victim_assignment),
            {"answer": self._file()},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_prepare.assert_not_called()
        self.assertFalse(
            StudentSubmission.objects.filter(student=self.attacker_student).exists()
        )

    @patch("students.views.launch_processing_task")
    def test_student_cannot_queue_an_upload_into_another_course(self, mock_launch):
        self.client.force_authenticate(user=self.attacker_student)

        response = self.client.post(
            self._async_url(self.victim_assignment),
            {"answer": self._file()},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_launch.assert_not_called()
        self.assertFalse(BackgroundProcessingTask.objects.exists())

    def test_withdrawn_or_pending_enrolment_does_not_open_the_assignment(self):
        for state in (EnrollmentStatusType.WITHDRAWN, EnrollmentStatusType.PENDING):
            with self.subTest(state=state):
                StudentCourse.objects.filter(
                    student=self.victim_student, course=self.victim_course
                ).update(enrollment_status=state)
                self.client.force_authenticate(user=self.victim_student)
                response = self.client.post(
                    self._sync_url(self.victim_assignment),
                    {"answer": self._file()},
                    format="multipart",
                )
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    @patch("students.services.ai_processor")
    @patch("students.views.AssignmentProcessingService.prepare_ai_content")
    def test_enrolled_student_still_submits_normally(self, mock_prepare, mock_ai):
        mock_prepare.return_value = "content"
        mock_ai.extract_answer_with_retry.return_value = {
            "answers": [{"question_number": 1, "answer_html": "x"}]
        }
        self.client.force_authenticate(user=self.victim_student)

        response = self.client.post(
            self._sync_url(self.victim_assignment),
            {"answer": self._file()},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            StudentSubmission.objects.filter(
                student=self.victim_student, assignment=self.victim_assignment
            ).exists()
        )

    # -- teachers ---------------------------------------------------------

    @patch("students.views.launch_processing_task")
    def test_teacher_cannot_batch_upload_into_another_teachers_assignment(
        self, mock_launch
    ):
        self.client.force_authenticate(user=self.attacker_teacher)

        response = self.client.post(
            self._batch_url(self.victim_assignment),
            {"answers": [self._file()]},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_launch.assert_not_called()
        self.assertFalse(BatchUploadSession.objects.exists())

    @patch("students.views.launch_processing_task")
    def test_teacher_batch_uploads_into_their_own_assignment(self, mock_launch):
        mock_launch.return_value.id = "celery-id"
        self.client.force_authenticate(user=self.victim_teacher)

        response = self.client.post(
            self._batch_url(self.victim_assignment),
            {"answers": [self._file()]},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(
            BatchUploadSession.objects.get().assignment_id, self.victim_assignment.id
        )
