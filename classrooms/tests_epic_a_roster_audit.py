"""Epic A §6: roster-change audit instrumentation (bulk_add_students,
remove_student - the only two roster-mutating endpoints; there is no
single-student-add endpoint).

Each test drives the real HTTP endpoint once and asserts exactly the events
it leaves. Updated on purpose for Epic A S4 (plan 08 §5, D3: one event per
changed record; SM rulings on S4):
- a bulk add leaves one ROSTER_CHANGE per enrolment it created (the S4
  history event, target the StudentCourse) PLUS the one aggregate
  ROSTER_CHANGE on the course, the only record of rows that failed (N + 1);
- a removal leaves exactly one ROSTER_CHANGE: the enrolment's history delete
  event. The explicit course-level emit it replaced is gone.

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
        self.assertEqual(AuditEvent.objects.count(), 3)
        self.assertEqual(
            set(AuditEvent.objects.values_list("action", flat=True)),
            {AuditAction.ROSTER_CHANGE},
        )
        event = AuditEvent.objects.get(target_type="Course")
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertEqual(str(event.target_id), str(self.course.id))
        self.assertEqual(event.metadata["item_count"], 2)
        self.assertEqual(event.metadata["succeeded_count"], 2)
        self.assertEqual(event.metadata["failed_count"], 0)

        enrolments = StudentCourse.all_objects.filter(course=self.course)
        created = AuditEvent.objects.filter(target_type="StudentCourse")
        self.assertEqual(
            sorted(str(e.target_id) for e in created),
            sorted(str(pk) for pk in enrolments.values_list("pk", flat=True)),
        )
        for history_event in created:
            self.assertEqual(history_event.actor_id, self.teacher.id)
            self.assertIsNone(history_event.before)
            self.assertEqual(history_event.after["course_id"], str(self.course.id))
            self.assertEqual(history_event.metadata["source"], "create")

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
        # The enrolment was created before the request (one create event);
        # the removal itself leaves exactly one more.
        removal = AuditEvent.objects.filter(metadata__source="delete")
        self.assertEqual(removal.count(), 1)
        self.assertEqual(AuditEvent.objects.count(), 2)
        event = removal.get()
        self.assertEqual(event.action, AuditAction.ROSTER_CHANGE)
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertEqual(event.target_type, "StudentCourse")
        self.assertEqual(event.actor_id, self.teacher.id)
        self.assertEqual(event.metadata["course_id"], str(self.course.id))
        self.assertEqual(event.metadata["student_id"], str(student.id))
        self.assertEqual(
            event.before,
            {"enrollment_status": "ENROLLED", "course_id": str(self.course.id)},
        )
        self.assertIsNone(event.after)
