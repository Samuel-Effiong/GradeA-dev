"""H-1 Stage 3, pre-existing staleness P1: a successful grading claim
invalidates nothing.

`_claim_submission_for_grading` (students/services.py) is a bare
`QuerySet.update(grading_state=RUNNING, ...)`, which bypasses `post_save`.
Only the FAILED release path (`_mark_grading_claim_failed`) called
`invalidate_submission_caches` afterwards; the successful claim did not.
A teacher's cached submission list therefore kept showing the
pre-grading state for the whole time a submission was RUNNING.

FIXED (H-1 Stage 3): a successful claim now also calls
`invalidate_submission_caches`, matching the FAILED path. This suite
proves the teacher's list refreshes and an unrelated teacher's own
(empty) list stays UNAFFECTED.

This is pre-existing staleness the owner decided Stage 3 fixes too (plan
§ "Old staleness"): it was live under BOTH mechanisms, not only once the
wildcards are removed, because the wildcard receiver is likewise only
reached through `post_save`/`post_delete`, which `.update()` never fires.

Fixtures follow the Stage 3 rule (plan §0): the submission is created
directly with exactly the fields production sets; the mutation under test
calls the real service function the grading endpoint itself calls.

Real Redis + real Postgres.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase
from django.urls import reverse

from assignments.models import Assignment, AssignmentStatus
from AutoGrader.tests_cache_matrix_support import (
    UNAFFECTED,
    FreshnessMatrixMixin,
    Read,
    legacy_wildcards_disabled,
)
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import GradingState, StudentSubmission
from students.services import _claim_submission_for_grading
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


def make_active_user(email, user_type, first_name):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name=first_name,
        last_name=user_type.title(),
    )


class GradingClaimFreshnessTests(FreshnessMatrixMixin, TransactionTestCase):
    """P1: a successful grading claim must refresh the teacher's own list."""

    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.patched_modules = self.enterContext(legacy_wildcards_disabled())
        self.assertTrue(self.patched_modules, "no legacy module was patched")

        self.teacher = make_active_user("p1-teacher@x.test", UserTypes.TEACHER, "P1T")
        self.other_teacher = make_active_user(
            "p1-other-teacher@x.test", UserTypes.TEACHER, "P1Other"
        )
        self.student = make_active_user("p1-student@x.test", UserTypes.STUDENT, "P1S")

        self.session = Session.objects.create(name="P1 term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="P1 course", teacher=self.teacher, session=self.session
        )
        StudentCourse.objects.create(
            student=self.student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.assignment = Assignment.objects.create(
            title="P1 assignment",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question()],
        )
        self.submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers=[{"question_number": 1, "answer_html": "one"}],
        )
        self.assertEqual(self.submission.grading_state, GradingState.IDLE)

        self.list_url = reverse("student-submission-list")

    def reads(self):
        return [
            Read("teacher's submission list", self.teacher, self.list_url),
            Read(
                "unrelated teacher's own submission list",
                self.other_teacher,
                self.list_url,
            ),
        ]

    def claim_for_grading(self):
        claimed = _claim_submission_for_grading(self.submission.id)
        self.assertTrue(claimed)

    def test_claim_now_refreshes_only_the_owning_teacher(self):
        result = self.run_matrix(
            "claim submission for grading (P1 fixed)",
            self.reads(),
            self.claim_for_grading,
        )
        self.assert_no_stale(result, expect_changed=["teacher's submission list"])
        verdicts = {o.label: o.verdict for o in result.outcomes}
        self.assertEqual(
            verdicts["unrelated teacher's own submission list"],
            UNAFFECTED,
            result.table(),
        )
