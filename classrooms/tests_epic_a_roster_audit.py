"""Epic A §6: roster-change audit instrumentation (bulk_add_students,
remove_student - the only two roster-mutating endpoints; there is no
single-student-add endpoint).

Each test drives the real HTTP endpoint once and asserts FR-A-01's literal
wording - exactly one AuditEvent, not >= 1 (a bulk add of several students
is still ONE roster-change action, matching CREDIT_TRANSACTION's own
one-event-per-bulk-write precedent, not one event per student).

Run with:
    python manage.py test classrooms.tests_epic_a_roster_audit --settings=settings_worktree
"""

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from audit.enums import AuditAction, AuditOutcome
from audit.models import AuditEvent

from .models import Course, EnrollmentStatusType, Session, StudentCourse

User = get_user_model()


class RosterChangeAuditTest(APITestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            email="roster-audit-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Roster",
            last_name="Teacher",
            user_type="TEACHER",
        )
        self.session = Session.objects.create(name="Term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Roster Course", teacher=self.teacher, session=self.session
        )
        self.client.force_authenticate(user=self.teacher)

    def test_bulk_add_students_emits_exactly_one_event(self):
        raw_data = (
            "First Name\tLast Name\tEmail\n"
            "John\tDoe\tjohn.doe@example.com\n"
            "Jane\tSmith\tjane.smith@example.com\n"
        )

        response = self.client.post(
            reverse("course-bulk-add-students", kwargs={"pk": self.course.pk}),
            {"raw_data": raw_data},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.ROSTER_CHANGE)
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertEqual(str(event.target_id), str(self.course.id))
        self.assertEqual(event.metadata["item_count"], 2)
        self.assertEqual(event.metadata["succeeded_count"], 2)
        self.assertEqual(event.metadata["failed_count"], 0)

    def test_remove_student_emits_exactly_one_event(self):
        student = User.objects.create_user(
            email="roster-audit-student@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Roster",
            last_name="Student",
            user_type="STUDENT",
        )
        StudentCourse.objects.create(
            student=student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

        response = self.client.delete(
            reverse(
                "course-remove-student",
                kwargs={"pk": self.course.pk, "student_id": str(student.id)},
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            StudentCourse.objects.filter(student=student, course=self.course).exists()
        )
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.ROSTER_CHANGE)
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertEqual(str(event.target_id), str(self.course.id))
        self.assertEqual(event.metadata["student_id"], str(student.id))
