from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, School, Session
from students.models import StudentSubmission
from students.services import (
    _maybe_notify_admins_grading_complete,
    upload_answers_engine,
)
from users.models import CustomUser, UserTypes


class StudentSubmissionGradeUpdateTest(APITestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Teacher",
            last_name="One",
        )
        self.student = CustomUser.objects.create_user(
            email="student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Student",
            last_name="One",
        )
        self.session = Session.objects.create(name="Test Session", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Test Course", teacher=self.teacher, session=self.session
        )
        self.assignment = Assignment.objects.create(
            title="Test Assignment",
            course=self.course,
            questions={"q1": "What is 1+1?"},
        )
        self.submission = StudentSubmission.objects.create(
            assignment=self.assignment, student=self.student, answers={"q1": "2"}
        )
        self.url = reverse(
            "student-submission-update-grade", kwargs={"pk": self.submission.pk}
        )

    def test_teacher_can_update_grade(self):
        self.client.force_authenticate(user=self.teacher)
        data = {"score": 95.00, "feedback": {"overall": "Great job!"}}
        response = self.client.patch(self.url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.submission.refresh_from_db()
        self.assertEqual(float(self.submission.score), 95.00)
        self.assertEqual(self.submission.feedback, {"overall": "Great job!"})
        self.assertTrue(self.submission.was_regraded)
        self.assertIsNotNone(self.submission.regraded_at)

    def test_student_cannot_update_grade(self):
        self.client.force_authenticate(user=self.student)
        data = {"score": 100.00}
        response = self.client.patch(self.url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class StudentSubmissionNotificationTest(APITestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="teacher-notify@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Teacher",
            last_name="Notify",
        )
        self.student = CustomUser.objects.create_user(
            email="student-notify@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Student",
            last_name="Notify",
        )
        self.session = Session.objects.create(
            name="Notification Session", teacher=self.teacher
        )
        self.course = Course.objects.create(
            name="Notification Course", teacher=self.teacher, session=self.session
        )
        self.assignment = Assignment.objects.create(
            title="Notification Assignment",
            course=self.course,
            questions={"q1": "What is 2+2?"},
        )
        self.content = [{"type": "text", "text": "mock submission"}]
        self.mock_submission_payload = {
            "answers": [{"question_number": 1, "answer_html": "<p>4</p>"}]
        }

    @patch("students.services.send_email_task.delay")
    @patch("students.services.ai_processor.extract_answer_with_retry")
    def test_email_sent_when_teacher_enables_student_submission_notifications(
        self, mock_extract_answer_with_retry, mock_send_email
    ):
        self.teacher.settings.notify_student_submission = True
        self.teacher.settings.save(update_fields=["notify_student_submission"])
        mock_extract_answer_with_retry.return_value = self.mock_submission_payload

        upload_answers_engine(self.assignment, self.content, self.student)

        mock_send_email.assert_called_once()
        self.assertEqual(StudentSubmission.objects.count(), 1)

    @patch("students.services.send_email_task.delay")
    @patch("students.services.ai_processor.extract_answer_with_retry")
    def test_email_not_sent_when_teacher_disables_student_submission_notifications(
        self, mock_extract_answer_with_retry, mock_send_email
    ):
        self.teacher.settings.notify_student_submission = False
        self.teacher.settings.save(update_fields=["notify_student_submission"])
        mock_extract_answer_with_retry.return_value = self.mock_submission_payload

        upload_answers_engine(self.assignment, self.content, self.student)

        mock_send_email.assert_not_called()
        self.assertEqual(StudentSubmission.objects.count(), 1)

    @patch("students.services.send_email_task.delay")
    @patch("students.services.ai_processor.extract_answer_with_retry")
    def test_email_not_sent_again_when_existing_submission_is_updated(
        self, mock_extract_answer_with_retry, mock_send_email
    ):
        self.teacher.settings.notify_student_submission = True
        self.teacher.settings.save(update_fields=["notify_student_submission"])
        mock_extract_answer_with_retry.return_value = self.mock_submission_payload

        upload_answers_engine(self.assignment, self.content, self.student)
        upload_answers_engine(self.assignment, self.content, self.student)

        mock_send_email.assert_called_once()
        self.assertEqual(StudentSubmission.objects.count(), 1)

    @patch("students.services.send_email_task.delay")
    @patch("students.services.ai_processor.extract_answer_with_retry")
    def test_notification_failure_does_not_abort_submission_creation(
        self, mock_extract_answer_with_retry, mock_send_email
    ):
        self.teacher.settings.notify_student_submission = True
        self.teacher.settings.save(update_fields=["notify_student_submission"])
        mock_extract_answer_with_retry.return_value = self.mock_submission_payload
        mock_send_email.side_effect = RuntimeError("queue failed")

        upload_answers_engine(self.assignment, self.content, self.student)

        self.assertEqual(StudentSubmission.objects.count(), 1)
        mock_send_email.assert_called_once()


class SchoolAdminGradingCompleteNotificationTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Grading Complete School")

        self.admin = CustomUser.objects.create_user(
            email="grading-admin@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            first_name="Grading",
            last_name="Admin",
            school=self.school,
            is_active=True,
        )
        self.admin.settings.notify_grading_complete = True
        self.admin.settings.save(update_fields=["notify_grading_complete"])

        self.teacher = CustomUser.objects.create_user(
            email="grading-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Grading",
            last_name="Teacher",
            school=self.school,
            is_active=True,
        )
        self.session = Session.objects.create(name="Grading Term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Grading Course",
            teacher=self.teacher,
            session=self.session,
        )
        self.assignment = Assignment.objects.create(
            title="Grading Assignment",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions={"q1": "What is 1+1?"},
        )

        self.student_one = CustomUser.objects.create_user(
            email="grading-student-one@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Student",
            last_name="One",
        )
        self.student_two = CustomUser.objects.create_user(
            email="grading-student-two@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Student",
            last_name="Two",
        )

    def _make_submission(self, student, *, graded=True):
        return StudentSubmission.objects.create(
            assignment=self.assignment,
            student=student,
            answers={"q1": "2"},
            score=100,
            score_percentage=100,
            graded_at=timezone.now() if graded else None,
        )

    @patch("students.services.send_email_task.delay")
    def test_notifies_once_when_all_submissions_graded(self, mock_send_email):
        self._make_submission(self.student_one, graded=True)
        self._make_submission(self.student_two, graded=True)

        _maybe_notify_admins_grading_complete(self.assignment)

        mock_send_email.assert_called_once()
        self.assertEqual(
            mock_send_email.mock_calls[0].kwargs["recipient_list"], [self.admin.email]
        )
        self.assertIn(
            "Grading complete", mock_send_email.mock_calls[0].kwargs["subject"]
        )
        self.assignment.refresh_from_db()
        self.assertIsNotNone(self.assignment.admin_grading_notified_at)

    @patch("students.services.send_email_task.delay")
    def test_does_not_notify_when_some_submissions_ungraded(self, mock_send_email):
        self._make_submission(self.student_one, graded=True)
        self._make_submission(self.student_two, graded=False)

        _maybe_notify_admins_grading_complete(self.assignment)

        mock_send_email.assert_not_called()
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.admin_grading_notified_at)

    @patch("students.services.send_email_task.delay")
    def test_does_not_notify_when_no_submissions_exist(self, mock_send_email):
        _maybe_notify_admins_grading_complete(self.assignment)

        mock_send_email.assert_not_called()
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.admin_grading_notified_at)

    @patch("students.services.send_email_task.delay")
    def test_does_not_notify_for_unpublished_assignment(self, mock_send_email):
        self.assignment.status = AssignmentStatus.DRAFT
        self.assignment.save(update_fields=["status"])
        self._make_submission(self.student_one, graded=True)

        _maybe_notify_admins_grading_complete(self.assignment)

        mock_send_email.assert_not_called()
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.admin_grading_notified_at)

    @patch("students.services.send_email_task.delay")
    def test_does_not_renotify_on_regrade(self, mock_send_email):
        self._make_submission(self.student_one, graded=True)
        _maybe_notify_admins_grading_complete(self.assignment)
        mock_send_email.assert_called_once()

        mock_send_email.reset_mock()
        _maybe_notify_admins_grading_complete(self.assignment)

        mock_send_email.assert_not_called()

    def test_concurrent_completion_only_claims_once(self):
        """Simulates two grading workers racing to observe "all graded"
        simultaneously: only one atomic UPDATE should succeed."""
        self._make_submission(self.student_one, graded=True)

        first_claim = Assignment.objects.filter(
            pk=self.assignment.pk, admin_grading_notified_at__isnull=True
        ).update(admin_grading_notified_at=timezone.now())
        second_claim = Assignment.objects.filter(
            pk=self.assignment.pk, admin_grading_notified_at__isnull=True
        ).update(admin_grading_notified_at=timezone.now())

        self.assertEqual(first_claim, 1)
        self.assertEqual(second_claim, 0)

    @patch("students.services.send_email_task.delay")
    def test_no_notification_when_teacher_has_no_school(self, mock_send_email):
        self.teacher.school = None
        self.teacher.save(update_fields=["school"])
        self._make_submission(self.student_one, graded=True)

        _maybe_notify_admins_grading_complete(self.assignment)

        mock_send_email.assert_not_called()

    @patch("students.services.send_email_task.delay")
    def test_no_notification_when_admin_not_opted_in(self, mock_send_email):
        self.admin.settings.notify_grading_complete = False
        self.admin.settings.save(update_fields=["notify_grading_complete"])
        self._make_submission(self.student_one, graded=True)

        _maybe_notify_admins_grading_complete(self.assignment)

        mock_send_email.assert_not_called()

    @patch("students.services.send_email_task.delay")
    def test_admin_in_different_school_not_notified(self, mock_send_email):
        other_school = School.objects.create(name="Other School")
        other_admin = CustomUser.objects.create_user(
            email="other-grading-admin@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            first_name="Other",
            last_name="Admin",
            school=other_school,
            is_active=True,
        )
        other_admin.settings.notify_grading_complete = True
        other_admin.settings.save(update_fields=["notify_grading_complete"])

        self._make_submission(self.student_one, graded=True)
        _maybe_notify_admins_grading_complete(self.assignment)

        mock_send_email.assert_called_once()
        self.assertEqual(
            mock_send_email.mock_calls[0].kwargs["recipient_list"], [self.admin.email]
        )
