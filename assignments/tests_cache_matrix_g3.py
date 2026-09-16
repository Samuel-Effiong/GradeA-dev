"""H-1 Stage 3, gap G3: publish-all-grades invalidates only the first student.

`publish_all_grades` (assignments/views.py) snapshots every submission being
published for the first time, bulk-`.update()`s them all to
`is_published=True`, then USED TO invalidate caches for exactly one of them
-- `newly_published[0]` -- because `.update()` bypasses `post_save` and the
view fired the receiver by hand for a single instance instead of the whole
batch. Every other student in the batch kept reading a pre-publish
submission list past the point their grade actually went out.

FIXED (H-1 Stage 3): `invalidate_submission_caches_bulk` (students/signals.py)
bumps every submission's student in one pipelined `bump_many`, plus the
shared course/teacher/school/global scopes once. This suite proves both
students in the batch are FRESH after publish-all, and that an unrelated
teacher's submission list for their OWN course stays UNAFFECTED (tenant
isolation).

Fixtures follow the Stage 3 rule (plan §0): the graded, unpublished
submissions are created directly with exactly the fields grading sets
(`graded_at`, `score`, `is_published=False`); the mutation under test runs
through the real `POST assignments/<pk>/publish-all-grades` endpoint.

Real Redis + real Postgres.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from AutoGrader.tests_cache_matrix_support import (
    UNAFFECTED,
    FreshnessMatrixMixin,
    Read,
    legacy_wildcards_disabled,
)
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import StudentSubmission
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


def make_active_user(email, user_type, first_name="G3"):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name=first_name,
        last_name=user_type.title(),
    )


class PublishAllGradesFreshnessTests(FreshnessMatrixMixin, TransactionTestCase):
    """G3: every newly-published student's submission list must refresh."""

    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.patched_modules = self.enterContext(legacy_wildcards_disabled())
        self.assertTrue(self.patched_modules, "no legacy module was patched")

        self.teacher = make_active_user("g3-teacher@x.test", UserTypes.TEACHER)
        self.other_teacher = make_active_user(
            "g3-other-teacher@x.test", UserTypes.TEACHER, first_name="G3Other"
        )
        self.first_student = make_active_user(
            "g3-first@x.test", UserTypes.STUDENT, first_name="G3First"
        )
        self.second_student = make_active_user(
            "g3-second@x.test", UserTypes.STUDENT, first_name="G3Second"
        )

        self.session = Session.objects.create(name="G3 term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="G3 course", teacher=self.teacher, session=self.session
        )
        for student in (self.first_student, self.second_student):
            StudentCourse.objects.create(
                student=student,
                course=self.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )

        self.assignment = Assignment.objects.create(
            title="G3 assignment",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question()],
        )

        # Graded (production sets graded_at + score via the grading claim),
        # not yet published -- exactly the state publish-all-grades acts on.
        self.first_submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.first_student,
            answers=[{"question_number": 1, "answer_html": "one"}],
            graded_at=timezone.now(),
            score=Decimal("8.0"),
            is_published=False,
        )
        self.second_submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.second_student,
            answers=[{"question_number": 1, "answer_html": "one"}],
            graded_at=timezone.now(),
            score=Decimal("6.0"),
            is_published=False,
        )

        self.list_url = reverse("student-submission-list")

    def reads(self):
        return [
            Read("first student's submission list", self.first_student, self.list_url),
            Read(
                "second student's submission list", self.second_student, self.list_url
            ),
            Read(
                "unrelated teacher's own submission list",
                self.other_teacher,
                self.list_url,
            ),
        ]

    def publish_all(self):
        client = APIClient()
        client.force_authenticate(self.teacher)
        response = client.post(
            reverse("assignment-publish-all-grades", args=[self.assignment.pk])
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_publish_all_now_refreshes_every_student_in_the_batch(self):
        result = self.run_matrix(
            "publish-all-grades (G3 fixed)",
            self.reads(),
            self.publish_all,
        )
        self.assert_no_stale(
            result,
            expect_changed=[
                "first student's submission list",
                "second student's submission list",
            ],
        )
        verdicts = {o.label: o.verdict for o in result.outcomes}
        self.assertEqual(
            verdicts["unrelated teacher's own submission list"],
            UNAFFECTED,
            result.table(),
        )
