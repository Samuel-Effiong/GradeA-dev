from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment
from classrooms.models import Course, Session
from students.models import StudentSubmission
from students.services import upload_answers_engine
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
