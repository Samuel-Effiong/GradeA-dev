"""
The missing-answer review flag: _populate_and_save_grade's newest source.

WHAT IS BEING PROTECTED

A question graded without the student's answer is scored 0 exactly like a
question the student chose to skip. Before this flag those two were
indistinguishable, so a student whose answer was lost in extraction took a
zero that no human was ever asked to look at. These tests pin the
behaviour that ends that, and — just as importantly — pin that a GENUINE
blank stays quiet, because a flag that fires on every skipped question in
the class is a flag teachers learn to ignore.

They also pin that the accumulation refactor did not change any existing
review behaviour: the grader-disagreement and second-opinion-unavailable
paths must produce byte-identical reasons/severity/tier to before.

Run with:
    python manage.py test students.tests_missing_answer_review
"""

from django.test import TestCase
from django.utils import timezone

from assignments.models import Assignment
from classrooms.models import Course, Session
from students.models import StudentSubmission
from students.services import _populate_and_save_grade, _review_sort_key
from users.models import CustomUser, UserTypes

QUESTIONS = [
    {
        "question_number": 1,
        "question_text": "Discuss.",
        "question_type": "ESSAY",
        "points": 20,
        "options": [],
        "rubric": [
            {"level": "excellent", "description": "Great", "points": 20},
            {"level": "good", "description": "Good", "points": 15},
            {"level": "poor", "description": "Poor", "points": 0},
        ],
        "model_answer": "A model essay.",
    }
]


def grading(*, not_found=None, second_opinion=None, score=15):
    result = {
        "grading_summary": {
            "total_score": score,
            "max_total_points": 20,
            "percentage": score / 20 * 100,
        },
        "question_evaluations": [
            {"question_number": 1, "score_awarded": score, "max_points": 20}
        ],
        "grading_confidence": 90,
    }
    if not_found is not None:
        result["answers_not_found"] = not_found
    if second_opinion is not None:
        result["second_opinion"] = second_opinion
    return result


MISSING = [
    {
        "question_number": 1,
        "answer_status": "NOT_FOUND_IN_DOCUMENT",
        "score_awarded": 0,
        "max_points": 20,
    }
]

DISAGREEMENT = {
    "disagreements": [
        {
            "question_number": 1,
            "a": {"score_awarded": 15},
            "b": {"score_awarded": 0},
            "severity": {"tier": "critical", "gap_fraction": 0.75},
        }
    ]
}


class MissingAnswerReviewTest(TestCase):
    def setUp(self):
        stamp = timezone.now().timestamp()
        teacher = CustomUser.objects.create_user(
            email=f"mar-teacher-{stamp}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        student = CustomUser.objects.create_user(
            email=f"mar-student-{stamp}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        session = Session.objects.create(name="S", teacher=teacher)
        course = Course.objects.create(name="C", teacher=teacher, session=session)
        assignment = Assignment.objects.create(
            title="Essay assignment", course=course, questions=QUESTIONS
        )
        self.submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "<p>work</p>"}],
        )

    def grade(self, result):
        _populate_and_save_grade(self.submission, result, None)
        self.submission.refresh_from_db()
        return self.submission

    # ── the new source ────────────────────────────────────────────────
    def test_missing_answer_flags_the_submission(self):
        submission = self.grade(grading(not_found=MISSING))
        self.assertTrue(submission.needs_review)

    def test_missing_answer_is_always_critical(self):
        # A data-integrity failure, not a marking disagreement.
        submission = self.grade(grading(not_found=MISSING))
        self.assertEqual(submission.review_tier, "critical")

    def test_missing_answer_sorts_to_the_very_top_of_the_queue(self):
        submission = self.grade(grading(not_found=MISSING))
        self.assertEqual(submission.review_severity, _review_sort_key("critical", 1.0))

    def test_missing_answer_reason_is_self_describing(self):
        submission = self.grade(grading(not_found=MISSING))
        reason = submission.review_reasons[0]
        self.assertEqual(reason["type"], "answer_not_found")
        self.assertEqual(reason["question_number"], 1)
        self.assertEqual(reason["answer_status"], "NOT_FOUND_IN_DOCUMENT")
        self.assertEqual(reason["max_points"], 20)

    def test_every_missing_answer_gets_its_own_reason(self):
        many = [dict(MISSING[0], question_number=n) for n in (1, 2, 3)]
        submission = self.grade(grading(not_found=many))
        self.assertEqual(len(submission.review_reasons), 3)

    def test_empty_not_found_list_does_not_flag(self):
        # THE NOISE GUARD. A clean submission must stay clean, or teachers
        # stop reading the queue.
        submission = self.grade(grading(not_found=[]))
        self.assertFalse(submission.needs_review)
        self.assertIsNone(submission.review_reasons)

    def test_absent_key_does_not_flag(self):
        # Results that predate the provenance check.
        submission = self.grade(grading())
        self.assertFalse(submission.needs_review)

    def test_the_grade_itself_is_unaffected_by_the_flag(self):
        submission = self.grade(grading(not_found=MISSING, score=15))
        self.assertEqual(float(submission.score), 15.0)

    # ── accumulation: both sources at once ────────────────────────────
    def test_missing_answer_and_disagreement_are_both_reported(self):
        # The if/elif this replaced would have reported only one.
        submission = self.grade(grading(not_found=MISSING, second_opinion=DISAGREEMENT))
        types = {r["type"] for r in submission.review_reasons}
        self.assertEqual(types, {"answer_not_found", "grader_disagreement"})

    def test_missing_answer_outranks_a_moderate_disagreement(self):
        moderate = {
            "disagreements": [
                {
                    "question_number": 1,
                    "a": {"score_awarded": 15},
                    "b": {"score_awarded": 10},
                    "severity": {"tier": "moderate", "gap_fraction": 0.25},
                }
            ]
        }
        submission = self.grade(grading(not_found=MISSING, second_opinion=moderate))
        self.assertEqual(submission.review_tier, "critical")

    def test_missing_answer_combines_with_second_opinion_unavailable(self):
        unavailable = {"needs_review": True, "skipped": "out of credits"}
        submission = self.grade(grading(not_found=MISSING, second_opinion=unavailable))
        types = {r["type"] for r in submission.review_reasons}
        self.assertIn("answer_not_found", types)
        self.assertEqual(len(submission.review_reasons), 2)

    # ── regression: existing behaviour must be byte-identical ─────────
    def test_disagreement_only_behaves_exactly_as_before(self):
        submission = self.grade(grading(second_opinion=DISAGREEMENT))
        self.assertTrue(submission.needs_review)
        self.assertEqual(submission.review_tier, "critical")
        self.assertEqual(len(submission.review_reasons), 1)
        self.assertEqual(submission.review_reasons[0]["type"], "grader_disagreement")
        self.assertEqual(submission.review_severity, _review_sort_key("critical", 0.75))

    def test_second_opinion_unavailable_behaves_exactly_as_before(self):
        submission = self.grade(
            grading(second_opinion={"needs_review": True, "skipped": "out of credits"})
        )
        self.assertTrue(submission.needs_review)
        self.assertEqual(submission.review_tier, "moderate")
        self.assertEqual(submission.review_severity, _review_sort_key("moderate", None))
        self.assertEqual(
            submission.review_reasons[0]["type"], "second_opinion_unavailable"
        )

    def test_disagreement_suppresses_the_unavailable_reason_as_before(self):
        # The original elif semantics: a disagreement means the second
        # opinion DID run, so "unavailable" must not also appear.
        both = dict(DISAGREEMENT, needs_review=True, skipped="out of credits")
        submission = self.grade(grading(second_opinion=both))
        types = {r["type"] for r in submission.review_reasons}
        self.assertEqual(types, {"grader_disagreement"})

    def test_clean_grade_clears_a_stale_flag(self):
        self.grade(grading(not_found=MISSING))
        self.assertTrue(self.submission.needs_review)
        # Re-grade, this time with the answer found.
        submission = self.grade(grading(not_found=[]))
        self.assertFalse(submission.needs_review)
        self.assertIsNone(submission.review_reasons)
        self.assertIsNone(submission.review_severity)
        self.assertIsNone(submission.review_tier)

    def test_malformed_not_found_entries_do_not_crash(self):
        submission = self.grade(grading(not_found=[{}, {"question_number": None}]))
        self.assertTrue(submission.needs_review)
        self.assertEqual(len(submission.review_reasons), 2)


class ExpiredFixtureGuard(TestCase):
    """The fixtures above assume a teacher with no credit wallet is fine
    because _populate_and_save_grade makes no billed calls. If that ever
    changes, this test is where it shows up first."""

    def test_populate_makes_no_ai_calls(self):
        from unittest.mock import patch

        stamp = timezone.now().timestamp() + 1
        teacher = CustomUser.objects.create_user(
            email=f"guard-teacher-{stamp}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        student = CustomUser.objects.create_user(
            email=f"guard-student-{stamp}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        session = Session.objects.create(name="S", teacher=teacher)
        course = Course.objects.create(name="C", teacher=teacher, session=session)
        assignment = Assignment.objects.create(
            title="A", course=course, questions=QUESTIONS
        )
        submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "<p>x</p>"}],
        )
        with patch("ai_processor.services.AIProcessor.execute_graded_task") as mocked:
            _populate_and_save_grade(submission, grading(not_found=MISSING), None)
        mocked.assert_not_called()
