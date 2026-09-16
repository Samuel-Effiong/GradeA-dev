"""H-1 Stage 3, gap G2: the student-admin dashboard keys are unversioned.

`StudentAdminDashboardView.summary`/`assignments`/`overview` build their own
cache key (`studentadmins:user_id__<id>:...`) with plain `cache.get`/
`cache.set` -- no `versioned_key`, no generation-counter scope, and no
invalidation call anywhere clears the `studentadmins:` prefix. This is stale
under BOTH mechanisms today, not only once the wildcards are removed: a
withdrawn student's cached summary keeps serving 200 with course data past
the point production access-checks would return 404. The matrix records
this as a security-relevant staleness case (access outlives revocation),
not just a freshness nit.

Fixtures follow the Stage 3 rule (plan §0): rows are created directly, with
exactly the fields production sets; the mutation under test (withdrawal)
runs through the real `PATCH student-course/<pk>` endpoint.

Real Redis + real Postgres.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from AutoGrader.tests_cache_matrix_support import (
    STALE,
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


def make_active_user(email, user_type):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name="G2",
        last_name=user_type.title(),
    )


class StudentAdminSummaryFreshnessTests(FreshnessMatrixMixin, TransactionTestCase):
    """G2: a withdrawn student's cached course summary must go with them."""

    reset_sequences = True

    def setUp(self):
        cache.clear()
        # Run this with BOTH mechanisms live and both disabled: nothing
        # invalidates `studentadmins:` today either way, so the gap exists
        # under the legacy mechanism too. Default here: legacy disabled,
        # matching the rest of the Stage 3 "gaps disabled" sweep.
        self.patched_modules = self.enterContext(legacy_wildcards_disabled())
        self.assertTrue(self.patched_modules, "no legacy module was patched")

        self.teacher = make_active_user("g2-teacher@x.test", UserTypes.TEACHER)
        self.student = make_active_user("g2-student@x.test", UserTypes.STUDENT)

        self.session = Session.objects.create(name="G2 term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="G2 course", teacher=self.teacher, session=self.session
        )
        self.enrollment = StudentCourse.objects.create(
            student=self.student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        Assignment.objects.create(
            title="G2 assignment",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question()],
        )

        self.summary_url = reverse("student-summary", args=[self.course.pk])

    def reads(self):
        return [Read("student's course summary", self.student, self.summary_url)]

    def withdraw(self):
        client = APIClient()
        client.force_authenticate(self.teacher)
        response = client.patch(
            reverse("student-course-detail", args=[self.enrollment.pk]),
            {"enrollment_status": "WITHDRAWN"},
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_withdrawal_currently_leaves_the_cached_summary_stale(self):
        result = self.run_matrix(
            "withdraw enrolled student (G2 gap, unversioned key)",
            self.reads(),
            self.withdraw,
        )
        verdicts = {o.label: o.verdict for o in result.outcomes}
        self.assertEqual(verdicts, {"student's course summary": STALE}, result.table())
        # Spell out the security angle explicitly: the cached response
        # keeps returning 200 with course data after access was revoked.
        stale = result.outcomes[0]
        self.assertEqual(stale.cached_after[0], 200, result.table())
        self.assertEqual(stale.truth_after[0], 404, result.table())
