"""
Regression coverage for rubric-level snapping.

Grading rule #1 in GRADING_ASSIGNMENT_PROMPT_5.txt is "discrete scores
only" — a score must equal one of the rubric's level point values, not
anything in between. That rule was asserted in the prompt but never
mechanically enforced: AIProcessor._finalize_grading_result clamped a
score to [0, question.points] but happily persisted 7.5 on a four-level
ladder if the model returned it.

The fix snaps every LLM-graded score to the nearest rubric-level value
(ties resolve down), in the same arithmetic-authority pass that already
clamps and recomputes totals. Deterministic evaluations (graded_by ==
"deterministic") are exact by construction and are never snapped.

A side effect: ai_processor.second_opinion._severity's levels_apart used
to return None whenever a score wasn't exactly a rubric value (which was
common, since nothing enforced that). With snapping in place, LLM scores
are now always a rubric value (or 0), so levels_apart resolves.

Run with:
    python manage.py test ai_processor.tests_rubric_snapping
"""

from django.test import SimpleTestCase, override_settings

from ai_processor.second_opinion import compare_evaluations
from ai_processor.services import AIProcessor


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
)
class RubricSnappingTest(SimpleTestCase):
    """Unit-level coverage of the snap itself — pure function, no DB or AI
    calls needed."""

    def setUp(self):
        self.processor = AIProcessor()

    def _rubric(self, *points):
        return [{"level": str(p), "points": p, "description": ""} for p in points]

    def test_score_snaps_to_nearest_rubric_level(self):
        # Ladder (10, 7, 4, 0); 7.5 is closer to 7 than to 10.
        questions = [
            {"question_number": 1, "points": 10, "rubric": self._rubric(10, 7, 4, 0)}
        ]
        evaluations = [{"question_number": 1, "score_awarded": 7.5}]

        result = self.processor._finalize_grading_result(evaluations, questions)

        evaluation = result["question_evaluations"][0]
        self.assertEqual(evaluation["score_awarded"], 7)
        self.assertEqual(evaluation["snapped_from"], 7.5)
        self.assertEqual(result["total_score"], 7)
        self.assertEqual(
            result["score_calculation_verification"]["snapped_to_rubric_level_count"],
            1,
        )

    def test_exact_tie_resolves_downward(self):
        # Ladder (7, 4); 5.5 is equidistant from both — must resolve to 4.
        questions = [{"question_number": 1, "points": 7, "rubric": self._rubric(7, 4)}]
        evaluations = [{"question_number": 1, "score_awarded": 5.5}]

        result = self.processor._finalize_grading_result(evaluations, questions)

        self.assertEqual(result["question_evaluations"][0]["score_awarded"], 4)

    def test_skipped_answer_stays_zero_even_off_ladder(self):
        # Ladder floor is 2, not 0 — a skipped answer must still be
        # expressible as 0 rather than snapping up to the nearest level.
        questions = [
            {
                "question_number": 1,
                "points": 20,
                "rubric": self._rubric(20, 15, 8, 2),
            }
        ]
        evaluations = [{"question_number": 1, "score_awarded": 0}]

        result = self.processor._finalize_grading_result(evaluations, questions)

        self.assertEqual(result["question_evaluations"][0]["score_awarded"], 0)
        self.assertNotIn("snapped_from", result["question_evaluations"][0])

    def test_missing_or_malformed_rubric_is_not_snapped(self):
        questions_missing = [{"question_number": 1, "points": 10}]
        questions_malformed = [
            {"question_number": 1, "points": 10, "rubric": "not-a-list"}
        ]
        questions_single_level = [
            {"question_number": 1, "points": 10, "rubric": self._rubric(10)}
        ]
        evaluations = [{"question_number": 1, "score_awarded": 6.3}]

        for questions in (
            questions_missing,
            questions_malformed,
            questions_single_level,
        ):
            with self.subTest(questions=questions):
                result = self.processor._finalize_grading_result(evaluations, questions)
                self.assertEqual(
                    result["question_evaluations"][0]["score_awarded"], 6.3
                )
                self.assertNotIn("snapped_from", result["question_evaluations"][0])

    def test_deterministic_evaluation_is_never_snapped(self):
        # A deterministic evaluation is exact by construction — even if it
        # somehow carried a non-rubric score, it must pass through
        # unchanged rather than being second-guessed.
        questions = [
            {"question_number": 1, "points": 10, "rubric": self._rubric(10, 5, 0)}
        ]
        evaluations = [
            {
                "question_number": 1,
                "score_awarded": 7,
                "graded_by": "deterministic",
            }
        ]

        result = self.processor._finalize_grading_result(evaluations, questions)

        evaluation = result["question_evaluations"][0]
        self.assertEqual(evaluation["score_awarded"], 7)
        self.assertNotIn("snapped_from", evaluation)

    def test_levels_apart_resolves_once_scores_are_snapped(self):
        # Before snapping existed, two LLM-graded scores that weren't
        # exact rubric values made levels_apart unresolvable (None). Now
        # that _finalize_grading_result snaps both sides before comparison
        # (as second_opinion.compare_evaluations' docstring already
        # assumes), levels_apart resolves to a real distance.
        questions = [
            {
                "question_number": 1,
                "points": 20,
                "rubric": self._rubric(20, 19, 18, 0),
            }
        ]
        evals_a = self.processor._finalize_grading_result(
            [{"question_number": 1, "score_awarded": 20}], questions
        )["question_evaluations"]
        evals_b = self.processor._finalize_grading_result(
            [{"question_number": 1, "score_awarded": 18.4}], questions
        )["question_evaluations"]

        comparison = compare_evaluations(
            evals_a,
            evals_b,
            key_fn=self.processor._question_number_key,
            questions=questions,
        )

        self.assertEqual(len(comparison["disagreements"]), 1)
        severity = comparison["disagreements"][0]["severity"]
        self.assertEqual(severity["levels_apart"], 2)
        self.assertEqual(severity["tier"], "critical")
