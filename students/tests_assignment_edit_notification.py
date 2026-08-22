"""Tests for notify_students_of_assignment_edit - the blanket "your teacher
edited this assignment after you submitted" email, and the hook that fires
it from AssignmentProcessingService.update_assignment_from_extraction.
"""

from unittest.mock import patch

from django.test import TestCase

from assignments.models import Assignment
from classrooms.models import Course, Session
from students.models import StudentSubmission
from students.services import notify_students_of_assignment_edit
from users.models import CustomUser, UserTypes


class NotifyStudentsOfAssignmentEditTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Teacher",
            last_name="One",
        )
        self.session = Session.objects.create(name="Term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Course", teacher=self.teacher, session=self.session
        )
        self.assignment = Assignment.objects.create(
            title="MCQ",
            course=self.course,
            questions=[{"question_number": 1, "options": ["one", "two"]}],
        )

    def _make_student(self, email, *, opted_in, synthetic=False):
        student_email = (
            email if not synthetic else email.split("@")[0] + "@student.local"
        )
        student = CustomUser.objects.create_user(
            email=student_email,
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Student",
            last_name=email.split("@")[0],
        )
        student.settings.notify_assignment_edited = opted_in
        student.settings.save()
        return student

    @patch("students.services.send_email_task.delay")
    def test_opted_in_student_with_a_submission_is_notified(self, mock_delay):
        student = self._make_student("opted-in@example.com", opted_in=True)
        StudentSubmission.objects.create(
            assignment=self.assignment, student=student, answers=[]
        )

        notify_students_of_assignment_edit(self.assignment)

        mock_delay.assert_called_once()
        _, kwargs = mock_delay.call_args
        self.assertEqual(kwargs["recipient_list"], [student.email])
        self.assertIn("MCQ", kwargs["subject"])

    @patch("students.services.send_email_task.delay")
    def test_opted_out_student_is_not_notified(self, mock_delay):
        student = self._make_student("opted-out@example.com", opted_in=False)
        StudentSubmission.objects.create(
            assignment=self.assignment, student=student, answers=[]
        )

        notify_students_of_assignment_edit(self.assignment)

        mock_delay.assert_not_called()

    @patch("students.services.send_email_task.delay")
    def test_synthetic_student_local_account_is_never_notified(self, mock_delay):
        student = self._make_student(
            "synthetic@example.com", opted_in=True, synthetic=True
        )
        StudentSubmission.objects.create(
            assignment=self.assignment, student=student, answers=[]
        )

        notify_students_of_assignment_edit(self.assignment)

        mock_delay.assert_not_called()

    @patch("students.services.send_email_task.delay")
    def test_student_without_a_submission_is_not_notified(self, mock_delay):
        self._make_student("no-submission@example.com", opted_in=True)
        # No StudentSubmission created for this student.

        notify_students_of_assignment_edit(self.assignment)

        mock_delay.assert_not_called()

    @patch("students.services.send_email_task.delay")
    def test_multiple_submitters_each_get_their_own_notification(self, mock_delay):
        student_a = self._make_student("a@example.com", opted_in=True)
        student_b = self._make_student("b@example.com", opted_in=True)
        StudentSubmission.objects.create(
            assignment=self.assignment, student=student_a, answers=[]
        )
        StudentSubmission.objects.create(
            assignment=self.assignment, student=student_b, answers=[]
        )

        notify_students_of_assignment_edit(self.assignment)

        self.assertEqual(mock_delay.call_count, 2)
        recipients = {
            call.kwargs["recipient_list"][0] for call in mock_delay.call_args_list
        }
        self.assertEqual(recipients, {student_a.email, student_b.email})


class UpdateAssignmentFromExtractionNotificationHookTest(TestCase):
    """
    Integration point: update_assignment_from_extraction must fire the
    notification exactly when the edited assignment already has
    submissions, and never when it doesn't (e.g. a brand-new assignment
    being extracted for the first time).
    """

    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="teacher2@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Teacher",
            last_name="Two",
        )
        self.session = Session.objects.create(name="Term2", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Course2", teacher=self.teacher, session=self.session
        )
        self.assignment = Assignment.objects.create(
            title="MCQ2",
            course=self.course,
            questions=[{"question_number": 1, "options": ["one", "two"]}],
        )

    def _extraction_payload(self):
        return {
            "title": "MCQ2 (edited)",
            "instructions": "",
            "total_points": 10,
            "question_count": 1,
            "assignment_type": "OBJECTIVE",
            "questions": [
                {
                    "question_number": 1,
                    "question_text": "Q1",
                    "question_type": "OBJECTIVE",
                    "points": 10,
                    "options": ["one", "two"],
                    "rubric": [],
                    "model_answer": "one",
                }
            ],
        }

    @patch("students.services.notify_students_of_assignment_edit")
    @patch("ai_processor.services.ai_processor.extract_assignment_with_retry")
    def test_notification_fires_when_assignment_has_submissions(
        self, mock_extract_ai, mock_notify
    ):
        from assignments.services import AssignmentProcessingService

        student = CustomUser.objects.create_user(
            email="submitter@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Sub",
            last_name="Mitter",
        )
        StudentSubmission.objects.create(
            assignment=self.assignment, student=student, answers=[]
        )
        mock_extract_ai.return_value = self._extraction_payload()

        AssignmentProcessingService.update_assignment_from_extraction(
            self.teacher, self.assignment, content=[]
        )

        mock_notify.assert_called_once_with(self.assignment)

    @patch("students.services.notify_students_of_assignment_edit")
    @patch("ai_processor.services.ai_processor.extract_assignment_with_retry")
    def test_notification_does_not_fire_without_submissions(
        self, mock_extract_ai, mock_notify
    ):
        from assignments.services import AssignmentProcessingService

        mock_extract_ai.return_value = self._extraction_payload()

        AssignmentProcessingService.update_assignment_from_extraction(
            self.teacher, self.assignment, content=[]
        )

        mock_notify.assert_not_called()
