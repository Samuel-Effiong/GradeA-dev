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
from billing.access_control import AIFeatureNotAvailableError
from billing.errors import InsufficientCreditsError
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    Session,
    StudentCourse,
    Topic,
)
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

    @patch("assignments.views.ai_processor.generate_assignment_from_prompt_with_retry")
    def test_insufficient_credits_returns_402(self, mock_generate_assignment):
        mock_generate_assignment.side_effect = InsufficientCreditsError(
            "Refill your wallet to continue"
        )

        response = self.client.post(
            reverse("assignment-generate", kwargs={"course_id": self.course.id}),
            {"prompt": "Create a one-question biology quiz."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertIn("error", response.data)
        # The failed attempt's user message is still recorded even though
        # no assistant reply was produced - the DB write for it happens
        # before the AI call, outside the (now much smaller) transaction.
        self.assertEqual(
            AssignmentGenerationMessage.objects.filter(
                role=AssignmentGenerationRole.USER
            ).count(),
            1,
        )
        self.assertEqual(
            AssignmentGenerationMessage.objects.filter(
                role=AssignmentGenerationRole.ASSISTANT
            ).count(),
            0,
        )

        # Regression test: a session whose latest message is a dangling
        # USER message (metadata=None, since USER messages never set it)
        # used to 500 the whole sessions list - get_latest_message_preview
        # did `getattr(latest_message, "metadata", {})`, whose {} default
        # only applies when the attribute is missing, not when it's None.
        list_response = self.client.get(reverse("assignment-generation-session-list"))
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertEqual(list_response.data["results"][0]["latest_message_preview"], "")

    @patch("assignments.views.ai_processor.generate_assignment_from_prompt_with_retry")
    def test_ai_feature_not_available_returns_403(self, mock_generate_assignment):
        mock_generate_assignment.side_effect = AIFeatureNotAvailableError(
            "AI access denied: plan does not include this feature"
        )

        response = self.client.post(
            reverse("assignment-generate", kwargs={"course_id": self.course.id}),
            {"prompt": "Create a one-question biology quiz."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("error", response.data)

    @patch("assignments.views.ai_processor.generate_assignment_from_prompt_with_retry")
    def test_insufficient_prompt_returns_clarification_not_a_draft(
        self, mock_generate_assignment
    ):
        mock_generate_assignment.return_value = {
            "needs_clarification": True,
            "title": "",
            "instructions": "",
            "total_points": 0,
            "question_count": 0,
            "assignment_type": "HYBRID",
            "questions": [],
            "self_assessment": (
                "What subject or topic would you like this assignment to " "cover?"
            ),
        }

        response = self.client.post(
            reverse("assignment-generate", kwargs={"course_id": self.course.id}),
            {"prompt": "Summarize something interesting for me please"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(response.data["is_draft"])
        self.assertTrue(response.data["needs_clarification"])
        self.assertEqual(
            response.data["reply"],
            "What subject or topic would you like this assignment to cover?",
        )
        self.assertIsNone(response.data["assignment_id"])
        self.assertEqual(Assignment.objects.count(), 0)

        assistant_message = AssignmentGenerationMessage.objects.get(
            id=response.data["message_id"]
        )
        self.assertIsNone(assistant_message.assignment_snapshot)
        self.assertEqual(
            assistant_message.metadata["draft_status"], "NEEDS_CLARIFICATION"
        )

        # A clarification turn can't be saved as a real Assignment - the
        # save endpoint already rejects anything that isn't an AI_DRAFT.
        save_response = self.client.post(
            reverse(
                "assignment-save-generated-draft",
                kwargs={"message_id": assistant_message.id},
            ),
            {},
            format="json",
        )
        self.assertEqual(save_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Assignment.objects.count(), 0)

    def test_short_prompt_is_rejected_before_any_ai_call(self):
        with patch(
            "assignments.views.ai_processor.generate_assignment_from_prompt_with_retry"
        ) as mock_generate_assignment:
            response = self.client.post(
                reverse("assignment-generate", kwargs={"course_id": self.course.id}),
                {"prompt": "hi"},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_generate_assignment.assert_not_called()
        self.assertEqual(AssignmentGenerationMessage.objects.count(), 0)

    @patch("assignments.views.ai_processor.generate_assignment_from_prompt_with_retry")
    def test_course_context_is_built_and_passed_to_ai_processor(
        self, mock_generate_assignment
    ):
        self.course.description = "A course on the Old and New Testaments."
        self.course.save(update_fields=["description"])
        Topic.objects.create(course=self.course, name="Genesis")
        Topic.objects.create(course=self.course, name="Parables")

        mock_generate_assignment.return_value = generated_assignment_payload()

        self.client.post(
            reverse("assignment-generate", kwargs={"course_id": self.course.id}),
            {"prompt": "Create a one-question biology quiz."},
            format="json",
        )

        course_context = mock_generate_assignment.call_args.kwargs["course_context"]
        self.assertIn("Course name: Draft Course", course_context)
        self.assertIn(
            "Course description: A course on the Old and New Testaments.",
            course_context,
        )
        self.assertIn("Genesis", course_context)
        self.assertIn("Parables", course_context)

    @patch("assignments.views.ai_processor.generate_assignment_from_prompt_with_retry")
    def test_course_context_caps_topic_list_at_fifteen(self, mock_generate_assignment):
        for i in range(20):
            Topic.objects.create(course=self.course, name=f"Topic {i:02d}")

        mock_generate_assignment.return_value = generated_assignment_payload()

        self.client.post(
            reverse("assignment-generate", kwargs={"course_id": self.course.id}),
            {"prompt": "Create a one-question biology quiz."},
            format="json",
        )

        course_context = mock_generate_assignment.call_args.kwargs["course_context"]
        topic_count = course_context.count("Topic ")
        self.assertEqual(topic_count, 15)

    @patch("assignments.views.ai_processor.generate_assignment_from_prompt_with_retry")
    def test_self_assessment_is_sanitized_in_clarification_response(
        self, mock_generate_assignment
    ):
        mock_generate_assignment.return_value = {
            "needs_clarification": True,
            "title": "",
            "instructions": "",
            "total_points": 0,
            "question_count": 0,
            "assignment_type": "HYBRID",
            "questions": [],
            "self_assessment": (
                "<p>Hi</p><script>alert(1)</script>"
                '<p onclick="alert(1)">click me</p>'
            ),
        }

        response = self.client.post(
            reverse("assignment-generate", kwargs={"course_id": self.course.id}),
            {"prompt": "Summarize something interesting for me please"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("<script", response.data["reply"])
        self.assertNotIn("onclick", response.data["reply"])
        self.assertIn("<p>Hi</p>", response.data["reply"])

        assistant_message = AssignmentGenerationMessage.objects.get(
            id=response.data["message_id"]
        )
        self.assertNotIn("<script", assistant_message.content)
        self.assertNotIn("<script", assistant_message.metadata["reply"])

    @patch("assignments.views.ai_processor.generate_assignment_from_prompt_with_retry")
    def test_self_assessment_is_sanitized_in_draft_response(
        self, mock_generate_assignment
    ):
        payload = generated_assignment_payload()
        payload["self_assessment"] = "<p>Nice quiz</p><script>alert(1)</script>"
        mock_generate_assignment.return_value = payload

        response = self.client.post(
            reverse("assignment-generate", kwargs={"course_id": self.course.id}),
            {"prompt": "Create a one-question biology quiz."},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertNotIn("<script", response.data["reply"])
        self.assertIn("<p>Nice quiz</p>", response.data["reply"])

    @patch("assignments.views.ai_processor.generate_assignment_from_prompt_with_retry")
    def test_empty_questions_without_clarification_flag_is_treated_as_clarification(
        self, mock_generate_assignment
    ):
        mock_generate_assignment.return_value = {
            "title": "",
            "instructions": "",
            "total_points": 0,
            "question_count": 0,
            "assignment_type": "HYBRID",
            "questions": [],
            "self_assessment": "<p>What subject should this cover?</p>",
        }

        response = self.client.post(
            reverse("assignment-generate", kwargs={"course_id": self.course.id}),
            {"prompt": "Summarize something interesting for me please"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertFalse(response.data["is_draft"])
        self.assertTrue(response.data["needs_clarification"])
        self.assertEqual(
            response.data["reply"], "<p>What subject should this cover?</p>"
        )
        self.assertEqual(Assignment.objects.count(), 0)

        assistant_message = AssignmentGenerationMessage.objects.get(
            id=response.data["message_id"]
        )
        self.assertIsNone(assistant_message.assignment_snapshot)
        self.assertEqual(
            assistant_message.metadata["draft_status"], "NEEDS_CLARIFICATION"
        )

    @patch("assignments.views.ai_processor.generate_assignment_from_prompt_with_retry")
    def test_empty_questions_with_blank_self_assessment_gets_fallback_reply(
        self, mock_generate_assignment
    ):
        mock_generate_assignment.return_value = {
            "title": "",
            "instructions": "",
            "total_points": 0,
            "question_count": 0,
            "assignment_type": "HYBRID",
            "questions": [],
            "self_assessment": "",
        }

        response = self.client.post(
            reverse("assignment-generate", kwargs={"course_id": self.course.id}),
            {"prompt": "Summarize something interesting for me please"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["needs_clarification"])
        self.assertTrue(response.data["reply"])
        self.assertIn("cover", response.data["reply"])


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
