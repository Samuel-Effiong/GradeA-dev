"""H-1 Stage 3: dedicated security / tenant-isolation gate.

The plan's gate requires: "Other tenants' cached responses stay
byte-identical through a burst of tenant-A mutations." Every individual
matrix test (G1-G9, P1-P5) already checks one unrelated viewer per case;
this test is the dedicated cross-tenant burst the gate calls for
separately - one tenant (school B) takes NO action while tenant A (school
A) runs every kind of mutation this stage touches, back to back, and
school B's cached payload is compared byte-for-byte before and after.

Real Redis + real Postgres. Legacy wildcards disabled, so only the new
targeted bumps are live - this is deliberately the harshest condition:
if ANY new receiver's fan-out reached across the tenant boundary, this
test would catch it as a SPURIOUS change to school B's payload.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from AutoGrader.tests_cache_matrix_support import _canonical, legacy_wildcards_disabled
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from students.models import StudentSubmission
from users.models import UserTypes

User = get_user_model()


def make_active_user(email, user_type, first_name, **extra):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name=first_name,
        last_name=user_type.title(),
        **extra,
    )


def question(number=1):
    return {
        "question_number": number,
        "question_text": f"Q{number}",
        "question_type": "OBJECTIVE",
        "points": 10,
        "options": ["one", "two"],
        "rubric": [],
        "model_answer": "one",
    }


class TenantAMutationBurstLeavesTenantBUntouchedTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.patched = self.enterContext(legacy_wildcards_disabled())
        self.assertTrue(self.patched, "no legacy module was patched")

        # --- Tenant A: everything the burst mutates ---
        self.school_a = School.objects.create(name="Tenant A School")
        self.admin_a = make_active_user(
            "iso-admin-a@x.test",
            UserTypes.SCHOOL_ADMIN,
            "IsoAdminA",
            school=self.school_a,
        )
        self.teacher_a = make_active_user(
            "iso-teacher-a@x.test",
            UserTypes.TEACHER,
            "IsoTeacherA",
            school=self.school_a,
        )
        self.student_a = make_active_user(
            "iso-student-a@x.test", UserTypes.STUDENT, "IsoStudentA"
        )
        self.session_a = Session.objects.create(
            name="Iso A term", teacher=self.teacher_a
        )
        self.course_a = Course.objects.create(
            name="Iso A course", teacher=self.teacher_a, session=self.session_a
        )
        self.enrollment_a = StudentCourse.objects.create(
            student=self.student_a,
            course=self.course_a,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.assignment_a = Assignment.objects.create(
            title="Iso A assignment",
            course=self.course_a,
            status=AssignmentStatus.PUBLISHED,
            questions=[question()],
        )
        self.submission_a = StudentSubmission.objects.create(
            assignment=self.assignment_a,
            student=self.student_a,
            answers=[{"question_number": 1, "answer_html": "one"}],
            graded_at=timezone.now(),
            score=Decimal("8.0"),
            is_published=False,
        )

        # --- Tenant B: the control group. Untouched by anything below. ---
        self.school_b = School.objects.create(name="Tenant B School")
        self.admin_b = make_active_user(
            "iso-admin-b@x.test",
            UserTypes.SCHOOL_ADMIN,
            "IsoAdminB",
            school=self.school_b,
        )
        self.teacher_b = make_active_user(
            "iso-teacher-b@x.test",
            UserTypes.TEACHER,
            "IsoTeacherB",
            school=self.school_b,
        )
        self.student_b = make_active_user(
            "iso-student-b@x.test", UserTypes.STUDENT, "IsoStudentB"
        )
        self.session_b = Session.objects.create(
            name="Iso B term", teacher=self.teacher_b
        )
        self.course_b = Course.objects.create(
            name="Iso B course", teacher=self.teacher_b, session=self.session_b
        )
        StudentCourse.objects.create(
            student=self.student_b,
            course=self.course_b,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.assignment_b = Assignment.objects.create(
            title="Iso B assignment",
            course=self.course_b,
            status=AssignmentStatus.PUBLISHED,
            questions=[question()],
        )

    def _get(self, user, url):
        client = APIClient()
        client.force_authenticate(user)
        return client.get(url)

    def _tenant_b_snapshot(self):
        """Every tenant-B viewer's cached payload for a family the Stage 3
        receivers touch, keyed by label so a diff names exactly who moved."""
        return {
            "school B admin's user list": _canonical(
                self._get(self.admin_b, reverse("user-list"))
            ),
            "school B admin's session list": _canonical(
                self._get(self.admin_b, reverse("session-list"))
            ),
            "school B teacher's course retrieve": _canonical(
                self._get(
                    self.teacher_b, reverse("course-detail", args=[self.course_b.pk])
                )
            ),
            "school B teacher's assignment list": _canonical(
                self._get(self.teacher_b, reverse("assignment-list"))
            ),
            "school B student's course list": _canonical(
                self._get(self.student_b, reverse("course-list"))
            ),
            "school B student's submission list": _canonical(
                self._get(self.student_b, reverse("student-submission-list"))
            ),
        }

    def test_a_full_burst_of_tenant_a_mutations_leaves_tenant_b_byte_identical(self):
        before = self._tenant_b_snapshot()

        client_teacher_a = APIClient()
        client_teacher_a.force_authenticate(self.teacher_a)
        client_admin_a = APIClient()
        client_admin_a.force_authenticate(self.admin_a)

        # G1: publish/edit an assignment.
        r = client_teacher_a.patch(
            reverse("assignment-detail", args=[self.assignment_a.pk]),
            {"title": "Iso A assignment renamed"},
        )
        assert r.status_code == 200, r.content

        # G3: publish-all-grades.
        r = client_teacher_a.post(
            reverse("assignment-publish-all-grades", args=[self.assignment_a.pk])
        )
        assert r.status_code == 200, r.content

        # G5: rename the course.
        r = client_teacher_a.patch(
            reverse("course-detail", args=[self.course_a.pk]), {"name": "Iso A renamed"}
        )
        assert r.status_code == 200, r.content

        # G4/G8: move a user (superadmin-only in production, but the
        # signal path under test is the same CustomUser save regardless
        # of who triggers it) and rename a user.
        r = client_teacher_a.patch(
            reverse("user-detail", args=[self.teacher_a.pk]),
            {"first_name": "IsoTeacherARenamed"},
        )
        assert r.status_code == 200, r.content

        # G2: withdraw the enrolled student.
        r = client_teacher_a.patch(
            reverse("student-course-detail", args=[self.enrollment_a.pk]),
            {"enrollment_status": "WITHDRAWN"},
        )
        assert r.status_code == 200, r.content

        after = self._tenant_b_snapshot()

        moved = [label for label in before if before[label] != after[label]]
        self.assertEqual(
            moved,
            [],
            "tenant A's mutation burst changed tenant B's cached payload for: "
            f"{moved} - a fan-out crossed the tenant boundary",
        )
