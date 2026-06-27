from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django_celery_beat.models import PeriodicTask
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import (
    Assignment,
    AssignmentGenerationMessage,
    AssignmentGenerationRole,
    AssignmentStatus,
)
from assignments.signals import assignment_due_reminder_task_name
from assignments.tasks import send_assignment_due_reminder
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes


def generated_assignment_payload():
    return {
        "title": "<h1>Cell Biology Quiz</h1>",
        "instructions": "<p>Answer every question.</p>",
        "total_points": 10,
        "question_count": 1,
        "assignment_type": "OBJECTIVE",
        "questions": [
            {
                "question_number": 1,
                "question_text": "<p>What is the powerhouse of the cell?</p>",
                "question_type": "OBJECTIVE",
                "question_image": "",
                "points": 10,
                "blooms_level": "Remember",
                "options": [
                    "<p>Mitochondria</p>",
                    "<p>Nucleus</p>",
                    "<p>Ribosome</p>",
                ],
                "rubric": [],
                "model_answer": "<p>Mitochondria</p>",
            }
        ],
        "potential_issues": [],
        "self_assessment": "<p>I created a focused objective check.</p>",
        "extraction_confidence": 95,
    }


class AssignmentGenerationDraftAPITest(APITestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="teacher-draft@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Draft",
            last_name="Teacher",
        )
        self.session = Session.objects.create(
            name="Draft Session", teacher=self.teacher
        )
        self.course = Course.objects.create(
            name="Draft Course",
            teacher=self.teacher,
            session=self.session,
        )
        self.client.force_authenticate(user=self.teacher)

    @patch("assignments.views.ai_processor.generate_assignment_from_prompt_with_retry")
    def test_generated_assignment_is_saved_as_draft_not_assignment(
        self, mock_generate_assignment
    ):
        mock_generate_assignment.return_value = generated_assignment_payload()

        response = self.client.post(
            reverse("assignment-generate", kwargs={"course_id": self.course.id}),
            {"prompt": "Create a one-question biology quiz."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(response.data["assignment_id"])
        self.assertTrue(response.data["is_draft"])
        self.assertEqual(Assignment.objects.count(), 0)

        assistant_message = AssignmentGenerationMessage.objects.get(
            id=response.data["message_id"]
        )
        self.assertEqual(assistant_message.role, AssignmentGenerationRole.ASSISTANT)
        self.assertIsNone(assistant_message.assignment)
        self.assertEqual(assistant_message.metadata["draft_status"], "AI_DRAFT")
        self.assertEqual(
            assistant_message.assignment_snapshot["title"],
            generated_assignment_payload()["title"],
        )

    @patch("assignments.views.ai_processor.generate_assignment_from_prompt_with_retry")
    def test_generated_draft_is_only_persisted_when_saved(
        self, mock_generate_assignment
    ):
        mock_generate_assignment.return_value = generated_assignment_payload()

        generate_response = self.client.post(
            reverse("assignment-generate", kwargs={"course_id": self.course.id}),
            {"prompt": "Create a one-question biology quiz."},
            format="json",
        )
        message_id = generate_response.data["message_id"]

        save_response = self.client.post(
            reverse(
                "assignment-save-generated-draft",
                kwargs={"message_id": message_id},
            ),
            {"status": AssignmentStatus.DRAFT},
            format="json",
        )

        self.assertEqual(save_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Assignment.objects.count(), 1)
        assignment = Assignment.objects.get()
        self.assertEqual(str(assignment.id), save_response.data["id"])
        self.assertEqual(assignment.course, self.course)
        self.assertEqual(assignment.status, AssignmentStatus.DRAFT)

        assistant_message = AssignmentGenerationMessage.objects.get(id=message_id)
        self.assertEqual(assistant_message.assignment, assignment)
        self.assertEqual(assistant_message.metadata["draft_status"], "SAVED")

        second_save_response = self.client.post(
            reverse(
                "assignment-save-generated-draft",
                kwargs={"message_id": message_id},
            ),
            {},
            format="json",
        )

        self.assertEqual(second_save_response.status_code, status.HTTP_200_OK)
        self.assertEqual(Assignment.objects.count(), 1)


class AssignmentDueReminderSchedulingTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="teacher-due@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Due",
            last_name="Teacher",
        )
        self.session = Session.objects.create(name="Due Session", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Due Course",
            teacher=self.teacher,
            session=self.session,
        )

    def test_creating_assignment_schedules_both_due_reminders(self):
        assignment = Assignment.objects.create(
            title="Essay One",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=timezone.now() + timedelta(days=2),
        )

        self.assertTrue(
            PeriodicTask.objects.filter(
                name=assignment_due_reminder_task_name(assignment.id, 24)
            ).exists()
        )
        self.assertTrue(
            PeriodicTask.objects.filter(
                name=assignment_due_reminder_task_name(assignment.id, 1)
            ).exists()
        )

    def test_assignment_close_to_due_date_only_schedules_one_hour_reminder(self):
        assignment = Assignment.objects.create(
            title="Essay Two",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=timezone.now() + timedelta(hours=2),
        )

        self.assertFalse(
            PeriodicTask.objects.filter(
                name=assignment_due_reminder_task_name(assignment.id, 24)
            ).exists()
        )
        self.assertTrue(
            PeriodicTask.objects.filter(
                name=assignment_due_reminder_task_name(assignment.id, 1)
            ).exists()
        )

    def test_draft_assignment_does_not_schedule_due_reminders(self):
        assignment = Assignment.objects.create(
            title="Draft Essay",
            course=self.course,
            status=AssignmentStatus.DRAFT,
            due_date=timezone.now() + timedelta(days=2),
        )

        self.assertFalse(
            PeriodicTask.objects.filter(
                name=assignment_due_reminder_task_name(assignment.id, 24)
            ).exists()
        )
        self.assertFalse(
            PeriodicTask.objects.filter(
                name=assignment_due_reminder_task_name(assignment.id, 1)
            ).exists()
        )

    def test_unpublishing_assignment_removes_due_reminders(self):
        assignment = Assignment.objects.create(
            title="Essay Three",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=timezone.now() + timedelta(days=2),
        )

        assignment.status = AssignmentStatus.UNPUBLISHED
        assignment.save(update_fields=["status"])

        self.assertFalse(
            PeriodicTask.objects.filter(
                name=assignment_due_reminder_task_name(assignment.id, 24)
            ).exists()
        )
        self.assertFalse(
            PeriodicTask.objects.filter(
                name=assignment_due_reminder_task_name(assignment.id, 1)
            ).exists()
        )


class AssignmentDueReminderDeliveryTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="teacher-reminder@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Reminder",
            last_name="Teacher",
        )
        self.student = CustomUser.objects.create_user(
            email="student-reminder@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Reminder",
            last_name="Student",
        )
        self.system_student = CustomUser.objects.create_user(
            email="auto.student@student.local",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Auto",
            last_name="Student",
        )
        self.opted_out_student = CustomUser.objects.create_user(
            email="optedout@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Opted",
            last_name="Out",
        )

        self.session = Session.objects.create(
            name="Reminder Session", teacher=self.teacher
        )
        self.course = Course.objects.create(
            name="Reminder Course",
            teacher=self.teacher,
            session=self.session,
        )
        for student in [self.student, self.system_student, self.opted_out_student]:
            StudentCourse.objects.create(
                student=student,
                course=self.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )

        self.assignment = Assignment.objects.create(
            title="Reminder Assignment",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=timezone.now() + timedelta(days=2),
        )

    @patch("assignments.tasks.send_email_task.delay")
    def test_due_reminder_only_targets_opted_in_teacher_and_real_email_students(
        self, mock_send_email
    ):
        self.teacher.settings.notify_assignment_due_reminder = True
        self.teacher.settings.save(update_fields=["notify_assignment_due_reminder"])
        self.student.settings.notify_assignment_due_reminder = True
        self.student.settings.save(update_fields=["notify_assignment_due_reminder"])
        self.system_student.settings.notify_assignment_due_reminder = True
        self.system_student.settings.save(
            update_fields=["notify_assignment_due_reminder"]
        )
        self.opted_out_student.settings.notify_assignment_due_reminder = False
        self.opted_out_student.settings.save(
            update_fields=["notify_assignment_due_reminder"]
        )

        result = send_assignment_due_reminder(str(self.assignment.id), 24)

        self.assertIn("Queued 2 assignment due reminder emails.", result)
        self.assertEqual(mock_send_email.call_count, 2)
        recipient_lists = [
            call.kwargs["recipient_list"] for call in mock_send_email.mock_calls
        ]
        self.assertIn([self.teacher.email], recipient_lists)
        self.assertIn([self.student.email], recipient_lists)

    @patch("assignments.tasks.send_email_task.delay")
    def test_due_reminder_skips_unpublished_assignments(self, mock_send_email):
        self.assignment.status = AssignmentStatus.DRAFT
        self.assignment.save(update_fields=["status"])

        result = send_assignment_due_reminder(str(self.assignment.id), 1)

        self.assertEqual(result, "Assignment is not eligible for due date reminders.")
        mock_send_email.assert_not_called()

    @patch("assignments.tasks.send_email_task.delay")
    def test_due_reminder_skips_students_who_already_submitted(self, mock_send_email):
        self.teacher.settings.notify_assignment_due_reminder = True
        self.teacher.settings.save(update_fields=["notify_assignment_due_reminder"])
        self.student.settings.notify_assignment_due_reminder = True
        self.student.settings.save(update_fields=["notify_assignment_due_reminder"])

        StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers={"q1": "done"},
        )

        result = send_assignment_due_reminder(str(self.assignment.id), 24)

        self.assertIn("Queued 1 assignment due reminder emails.", result)
        self.assertEqual(mock_send_email.call_count, 1)
        self.assertEqual(
            mock_send_email.mock_calls[0].kwargs["recipient_list"], [self.teacher.email]
        )

    @patch("assignments.tasks.send_email_task.delay")
    def test_due_reminder_rejects_invalid_offset(self, mock_send_email):
        result = send_assignment_due_reminder(str(self.assignment.id), 6)

        self.assertEqual(result, "Invalid reminder offset: 6")
        mock_send_email.assert_not_called()

    @patch("assignments.tasks.send_email_task.delay")
    def test_due_reminder_continues_when_one_email_queue_fails(self, mock_send_email):
        self.teacher.settings.notify_assignment_due_reminder = True
        self.teacher.settings.save(update_fields=["notify_assignment_due_reminder"])
        self.student.settings.notify_assignment_due_reminder = True
        self.student.settings.save(update_fields=["notify_assignment_due_reminder"])
        mock_send_email.side_effect = [RuntimeError("queue failed"), None]

        result = send_assignment_due_reminder(str(self.assignment.id), 24)

        self.assertIn("Queued 1 assignment due reminder emails.", result)
        self.assertEqual(mock_send_email.call_count, 2)
