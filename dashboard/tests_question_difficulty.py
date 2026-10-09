"""
Tests for DashboardService.analyze_question_difficulty.

The method was previously unreachable AND wrong: it called `.items()` on
`submission.answers` (a LIST, not a dict) and looked for a "score" key on
entries that only ever carry the student's answer. Both callers are
commented out in dashboard/views.py, which is the only reason it never
raised in production.

These tests pin the corrected behaviour so that uncommenting a caller
does something right, and so the shape assumption cannot silently rot
again.

Run with:
    python manage.py test dashboard.tests_question_difficulty
"""

from django.test import SimpleTestCase

from dashboard.services import DashboardService


class FakeSubmission:
    """Only `.feedback` and `.answers` are read."""

    __slots__ = ("feedback", "answers")

    def __init__(self, feedback=None, answers=None):
        self.feedback = feedback
        self.answers = answers


def graded(*pairs):
    """(question_number, awarded, max_points) -> a grading feedback blob."""
    return FakeSubmission(
        feedback={
            "question_evaluations": [
                {
                    "question_number": number,
                    "score_awarded": awarded,
                    "max_points": available,
                }
                for number, awarded, available in pairs
            ]
        },
        # The real extractor's shape, and the shape the old code crashed
        # on. Present in every fixture so a regression to `.items()` fails
        # here immediately.
        answers=[{"question_number": p[0], "answer_html": "<p>x</p>"} for p in pairs],
    )


class AnalyzeQuestionDifficultyTest(SimpleTestCase):
    def setUp(self):
        self.service = DashboardService()

    def test_no_submissions_returns_empty(self):
        self.assertEqual(self.service.analyze_question_difficulty([]), ([], []))

    def test_submissions_without_feedback_are_skipped(self):
        subs = [FakeSubmission(feedback=None), FakeSubmission(feedback={})]
        self.assertEqual(self.service.analyze_question_difficulty(subs), ([], []))

    def test_answers_list_shape_does_not_raise(self):
        # THE REGRESSION. The old implementation called .items() on this.
        subs = [graded((1, 5, 10))]
        self.service.analyze_question_difficulty(subs)

    def test_hardest_and_easiest_are_identified(self):
        subs = [
            graded((1, 10, 10), (2, 2, 10), (3, 6, 10)),
            graded((1, 9, 10), (2, 1, 10), (3, 5, 10)),
        ]
        hardest, easiest = self.service.analyze_question_difficulty(subs)
        self.assertEqual([q for q, _ in hardest][0], "2")
        self.assertEqual([q for q, _ in easiest][0], "1")

    def test_scores_are_normalised_to_a_fraction_of_available_marks(self):
        # A 20-point essay averaging 12 (60%) is HARDER than a 2-point MCQ
        # averaging 2 (100%), even though 12 > 2. Raw marks would invert
        # this, which is why the fraction matters.
        subs = [graded((1, 12, 20), (2, 2, 2))]
        hardest, easiest = self.service.analyze_question_difficulty(subs)
        self.assertEqual(hardest[0][0], "1")
        self.assertAlmostEqual(hardest[0][1], 0.6)
        self.assertEqual(easiest[0][0], "2")
        self.assertAlmostEqual(easiest[0][1], 1.0)

    def test_average_is_across_submissions(self):
        subs = [graded((1, 10, 10)), graded((1, 0, 10))]
        hardest, _ = self.service.analyze_question_difficulty(subs)
        self.assertAlmostEqual(hardest[0][1], 0.5)

    def test_string_and_integer_question_numbers_are_merged(self):
        # Extracted assignments carry either; treating them as different
        # questions would halve every average.
        subs = [graded((1, 10, 10)), graded(("1", 0, 10))]
        hardest, _ = self.service.analyze_question_difficulty(subs)
        self.assertEqual(len(hardest), 1)
        self.assertAlmostEqual(hardest[0][1], 0.5)

    def test_zero_mark_questions_are_skipped(self):
        # Would divide by zero, and carries no difficulty signal anyway.
        subs = [graded((1, 0, 0), (2, 5, 10))]
        hardest, _ = self.service.analyze_question_difficulty(subs)
        self.assertEqual([q for q, _ in hardest], ["2"])

    def test_scores_above_the_maximum_are_clamped(self):
        subs = [graded((1, 15, 10))]
        _, easiest = self.service.analyze_question_difficulty(subs)
        self.assertAlmostEqual(easiest[0][1], 1.0)

    def test_negative_scores_are_clamped(self):
        subs = [graded((1, -5, 10))]
        hardest, _ = self.service.analyze_question_difficulty(subs)
        self.assertAlmostEqual(hardest[0][1], 0.0)

    def test_malformed_evaluations_are_skipped_not_fatal(self):
        sub = FakeSubmission(
            feedback={
                "question_evaluations": [
                    "junk",
                    None,
                    {},
                    {"question_number": None, "score_awarded": 1, "max_points": 2},
                    {"question_number": 1, "score_awarded": "n/a", "max_points": 10},
                    {"question_number": 2, "score_awarded": 4, "max_points": 10},
                ]
            }
        )
        hardest, _ = self.service.analyze_question_difficulty([sub])
        self.assertEqual([q for q, _ in hardest], ["2"])

    def test_missing_question_evaluations_key_is_handled(self):
        sub = FakeSubmission(feedback={"grading_summary": {"total_score": 5}})
        self.assertEqual(self.service.analyze_question_difficulty([sub]), ([], []))

    def test_at_most_two_of_each_are_returned(self):
        subs = [graded(*[(n, n, 10) for n in range(1, 8)])]
        hardest, easiest = self.service.analyze_question_difficulty(subs)
        self.assertEqual(len(hardest), 2)
        self.assertEqual(len(easiest), 2)

    def test_easiest_is_ordered_best_first(self):
        subs = [graded((1, 1, 10), (2, 5, 10), (3, 9, 10))]
        _, easiest = self.service.analyze_question_difficulty(subs)
        self.assertEqual([q for q, _ in easiest], ["3", "2"])

    def test_ties_resolve_in_a_stable_order(self):
        # Dict iteration order must not decide what a teacher is shown.
        subs = [graded((1, 5, 10), (2, 5, 10), (3, 5, 10))]
        first = self.service.analyze_question_difficulty(subs)
        second = self.service.analyze_question_difficulty(subs)
        self.assertEqual(first, second)

    def test_single_question_appears_in_both_lists(self):
        # With one question it is trivially both hardest and easiest;
        # neither list should come back empty.
        subs = [graded((1, 5, 10))]
        hardest, easiest = self.service.analyze_question_difficulty(subs)
        self.assertEqual(hardest, [("1", 0.5)])
        self.assertEqual(easiest, [("1", 0.5)])
