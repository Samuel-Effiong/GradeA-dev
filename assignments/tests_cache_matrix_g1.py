"""H-1 Stage 3, gap G1: assignment changes bump no viewer.

`_bump_assignment_scopes` (assignments/signals.py) used to bump
`usr(teacher_id)`, but no production write path ever sets
`Assignment.teacher` (see plan §0), so that bump was always a no-op, and
no receiver bumped enrolled students either - the legacy wildcards were
the only thing that ever refreshed the teacher's or an enrolled student's
assignment list.

FIXED (H-1 Stage 3): `_bump_assignment_scopes` now bumps `usr(course
.teacher_id)` - the real owner - plus `usr` of every student enrolled in
the course, in one pipelined `bump_many`. This suite proves that with the
legacy wildcards disabled (so only the new targeted bumps are in play), a
publish now reaches the teacher and the enrolled student and reaches
nobody else - an unrelated teacher and a student outside the course stay
UNAFFECTED, which is the tenant/role-isolation half of this fix.

Fixtures follow the Stage 3 rule (plan §0): the assignment row is created
directly, with exactly the fields production sets (no `teacher=`); the
mutation under test runs through the real `PATCH assignments/<pk>`
endpoint, which needs no AI call and no credits for a status-only edit.

Real Redis + real Postgres.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from AutoGrader.tests_cache_matrix_support import (
    UNAFFECTED,
    FreshnessMatrixMixin,
    Read,
    legacy_wildcards_disabled,
)
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from users.models import UserTypes

User = get_user_model()


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


def make_active_user(email, user_type, **extra):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name="G1",
        last_name=user_type.title(),
        **extra,
    )


class AssignmentPublishFreshnessTests(FreshnessMatrixMixin, TransactionTestCase):
    """G1: publishing an assignment must refresh every viewer who can see it."""

    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.patched_modules = self.enterContext(legacy_wildcards_disabled())
        self.assertTrue(self.patched_modules, "no legacy module was patched")

        self.teacher = make_active_user("g1-teacher@x.test", UserTypes.TEACHER)
        self.other_teacher = make_active_user(
            "g1-other-teacher@x.test", UserTypes.TEACHER
        )
        self.enrolled_student = make_active_user(
            "g1-enrolled@x.test", UserTypes.STUDENT
        )
        self.outside_student = make_active_user("g1-outside@x.test", UserTypes.STUDENT)

        # Session and Course carry `teacher`/`created_by` the way production
        # sets them (Session.objects.create(teacher=...) for an INDIVIDUAL
        # session, Course.objects.create(teacher=...) via CurrentUserDefault)
        # -- only Assignment.teacher is the field no production path sets.
        self.session = Session.objects.create(name="G1 term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="G1 course", teacher=self.teacher, session=self.session
        )
        StudentCourse.objects.create(
            student=self.enrolled_student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

        self.assignment = Assignment.objects.create(
            title="G1 assignment",
            course=self.course,
            status=AssignmentStatus.DRAFT,
            questions=[question()],
        )

        self.list_url = reverse("assignment-list")

    def reads(self):
        return [
            Read("teacher's assignment list", self.teacher, self.list_url),
            Read(
                "enrolled student's assignment list",
                self.enrolled_student,
                self.list_url,
            ),
            Read("other teacher's assignment list", self.other_teacher, self.list_url),
            Read(
                "outside student's assignment list", self.outside_student, self.list_url
            ),
        ]

    def publish(self):
        client = APIClient()
        client.force_authenticate(self.teacher)
        response = client.patch(
            reverse("assignment-detail", args=[self.assignment.pk]),
            {"status": "PUBLISHED"},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_publish_now_refreshes_teacher_and_enrolled_student_only(self):
        result = self.run_matrix(
            "publish assignment (G1 fixed)",
            self.reads(),
            self.publish,
        )
        self.assert_no_stale(
            result,
            expect_changed=[
                "teacher's assignment list",
                "enrolled student's assignment list",
            ],
        )
        verdicts = {o.label: o.verdict for o in result.outcomes}
        self.assertEqual(
            verdicts["other teacher's assignment list"], UNAFFECTED, result.table()
        )
        self.assertEqual(
            verdicts["outside student's assignment list"], UNAFFECTED, result.table()
        )
