from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from django_celery_beat.models import PeriodicTask

from assignments.models import Assignment, AssignmentStatus
from assignments.signals import assignment_due_reminder_task_name
from assignments.tasks import send_assignment_due_reminder
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from users.models import CustomUser, UserTypes


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
