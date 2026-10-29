"""H-235: my-students' submission counts are over PUBLISHED work only.

The user's decision (2026-10-14): `total_assignments_in_course` counts the
published assignments of the counted courses (H-234's basis); DRAFT and
UNPUBLISHED ones are left out, and a published one that is not yet due is
IN. `total_assignments_submitted` counts the student's submissions to those
published assignments, and `percentage_of_submission` follows. `at_risk`
keeps its published-AND-due basis, unchanged (r1 pins that).

The fixture: one teacher, one session, one course C1 with one published,
due, graded assignment P1 that the student Wren submitted (80%).
"""

from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
MY_STUDENTS = reverse("student-course-my-students")


@override_settings(CACHES=LOCMEM)
class TheCountsAreOverPublishedWorkTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = self._user(UserTypes.TEACHER, "Tess")
        self.term = Session.objects.create(name="Term 1", teacher=self.teacher)
        self.c1 = Course.objects.create(
            name="C1", teacher=self.teacher, session=self.term
        )
        self.wren = self._user(UserTypes.STUDENT, "Wren")
        StudentCourse.objects.create(
            student=self.wren,
            course=self.c1,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self._submit(self._assignment(AssignmentStatus.PUBLISHED), graded=True)
        self.client.force_authenticate(self.teacher)

    def _user(self, kind, first):
        return CustomUser.objects.create_user(
            email=f"h235-{first.lower()}@audit.test",
            password="remediation-pass-123",  # pragma: allowlist secret
            user_type=kind,
            first_name=first,
            last_name="H235",
            is_active=True,
        )

    def _assignment(self, assignment_status, *, due_in_days=-1):
        return Assignment.objects.create(
            title=f"{assignment_status} work",
            course=self.c1,
            status=assignment_status,
            total_points=100,
            due_date=timezone.now() + timedelta(days=due_in_days),
        )

    def _submit(self, assignment, *, graded=False):
        return StudentSubmission.objects.create(
            assignment=assignment,
            student=self.wren,
            answers={},
            score=80 if graded else None,
            score_percentage=80 if graded else None,
            submission_date=timezone.now(),
            graded_at=timezone.now() if graded else None,
        )

    def _row(self):
        cache.clear()
        response = self.client.get(MY_STUDENTS)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        rows = [r for r in response.data["results"] if r["id"] == str(self.wren.id)]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def _counts(self):
        row = self._row()
        return (
            row["total_assignments_in_course"],
            row["total_assignments_submitted"],
            row["percentage_of_submission"],
        )

    # --- the total ----------------------------------------------------------

    def test_p1_a_draft_is_not_in_the_total(self):
        self._assignment(AssignmentStatus.DRAFT)

        self.assertEqual(self._counts(), (1, 1, 100.0))

    def test_p2_an_unpublished_one_is_not_in_the_total(self):
        self._assignment(AssignmentStatus.UNPUBLISHED)

        self.assertEqual(self._counts(), (1, 1, 100.0))

    def test_p3_control_a_published_one_not_yet_due_is_in_the_total(self):
        """Green before and after: today every assignment counts."""
        self._assignment(AssignmentStatus.PUBLISHED, due_in_days=1)

        self.assertEqual(self._counts(), (2, 1, 50.0))

    # --- the submitted count --------------------------------------------------

    def test_p4_a_submission_to_a_draft_is_not_counted(self):
        self._submit(self._assignment(AssignmentStatus.DRAFT))

        self.assertEqual(self._counts(), (1, 1, 100.0))

    def test_p5_a_submission_to_an_unpublished_one_is_not_counted(self):
        self._submit(self._assignment(AssignmentStatus.UNPUBLISHED))

        self.assertEqual(self._counts(), (1, 1, 100.0))

    def test_p6_the_percent_follows(self):
        # P2 published and due, not submitted; a draft, submitted.
        self._assignment(AssignmentStatus.PUBLISHED)
        self._submit(self._assignment(AssignmentStatus.DRAFT))

        self.assertEqual(self._counts(), (2, 1, 50.0))

    # --- at_risk keeps its own basis ------------------------------------------

    def test_r1_control_at_risk_still_counts_only_work_due_by_now(self):
        """Green before and after. Three published assignments not yet due:
        the counts include them (1 of 4), but at_risk judges 1 of 1 due, at
        80%. Over the counts' basis it would be 25% of 4, below the critical
        50%, and at risk."""
        for _ in range(3):
            self._assignment(AssignmentStatus.PUBLISHED, due_in_days=1)

        self.assertEqual(self._counts(), (4, 1, 25.0))
        self.assertFalse(self._row()["at_risk"])
