"""
Regression coverage for H1: the single-pass grading path (<=
GRADING_QUESTIONS_PER_CHUNK questions — the common case, most assignments
are short) used to return the model's JSON straight through with no
arithmetic recompute and no clamping. The batched path (> that many
questions) already recalculated total_score/percentage in Python; the
single-pass path didn't, so a model that returned an over-awarded score
(more than a question is worth), a non-numeric score, or an internally
inconsistent percentage propagated unchecked into the database and onto a
student's report card.

The fix is a single shared helper, AIProcessor._finalize_grading_result,
used by both paths: it coerces every score_awarded to a float, clamps it
to [0, question.points], and recomputes total_score/max_total_points/
percentage from the clamped values — never trusting what the model
returned. Both the per-question evaluations AND the summary totals it
returns are corrected, so a clamped question's score never disagrees with
the total that supposedly includes it.

Run with:
    python manage.py test ai_processor.tests_grading_arithmetic
"""

from django.test import SimpleTestCase, override_settings

from ai_processor.services import AIProcessor


@override_settings(GRADING_SECOND_OPINION_ENABLED=False)
class FinalizeGradingResultTest(SimpleTestCase):
    """Unit-level coverage of the arithmetic itself — pure function, no
    DB or AI calls needed."""

    def setUp(self):
        self.processor = AIProcessor()

    def test_over_awarded_score_is_clamped_to_question_max(self):
        questions = [{"question_number": 1, "points": 10}]
        evaluations = [{"question_number": 1, "score_awarded": 15}]

        result = self.processor._finalize_grading_result(evaluations, questions)

        self.assertEqual(result["total_score"], 10)
        self.assertEqual(result["max_total_points"], 10)
        self.assertEqual(result["percentage"], 100)
        self.assertEqual(result["question_evaluations"][0]["score_awarded"], 10)

    def test_negative_score_is_clamped_to_zero(self):
        questions = [{"question_number": 1, "points": 10}]
        evaluations = [{"question_number": 1, "score_awarded": -5}]

        result = self.processor._finalize_grading_result(evaluations, questions)

        self.assertEqual(result["total_score"], 0)
        self.assertEqual(result["question_evaluations"][0]["score_awarded"], 0)

    def test_non_numeric_score_is_coerced_to_zero_not_crashed(self):
        questions = [{"question_number": 1, "points": 10}]
        evaluations = [{"question_number": 1, "score_awarded": "eight"}]

        result = self.processor._finalize_grading_result(evaluations, questions)

        self.assertEqual(result["total_score"], 0)
        self.assertEqual(result["question_evaluations"][0]["score_awarded"], 0)

    def test_null_score_is_coerced_to_zero(self):
        questions = [{"question_number": 1, "points": 10}]
        evaluations = [{"question_number": 1, "score_awarded": None}]

        result = self.processor._finalize_grading_result(evaluations, questions)

        self.assertEqual(result["total_score"], 0)

    def test_valid_score_within_range_is_unchanged(self):
        questions = [{"question_number": 1, "points": 10}]
        evaluations = [{"question_number": 1, "score_awarded": 7}]

        result = self.processor._finalize_grading_result(evaluations, questions)

        self.assertEqual(result["total_score"], 7)
        self.assertEqual(result["percentage"], 70)

    def test_evaluation_with_no_matching_question_is_floored_but_uncapped(self):
        questions = [{"question_number": 1, "points": 10}]
        # question_number 99 doesn't exist in the rubric — there's no known
        # cap to clamp against, so only the floor (>= 0) applies.
        evaluations = [
            {"question_number": 1, "score_awarded": 5},
            {"question_number": 99, "score_awarded": 1000},
        ]

        result = self.processor._finalize_grading_result(evaluations, questions)

        # max_total_points comes only from the real rubric (10), so the
        # stray evaluation inflates total_score without a matching cap —
        # exactly why H2 (reconciling evaluations against the rubric by
        # question_number) is a separate, still-open issue. This test
        # locks today's documented behavior for that case, not a claim
        # that it's fully solved here.
        self.assertEqual(result["max_total_points"], 10)
        self.assertEqual(result["total_score"], 1005)

    def test_percentage_reflects_clamped_total_not_models_claim(self):
        # Two questions worth 10 each; model over-awards one to 20.
        # Correct clamped total is 10 + 10 = 20 / 20 = 100%, not whatever
        # inflated percentage the model might separately claim.
        questions = [
            {"question_number": 1, "points": 10},
            {"question_number": 2, "points": 10},
        ]
        evaluations = [
            {"question_number": 1, "score_awarded": 20},
            {"question_number": 2, "score_awarded": 10},
        ]

        result = self.processor._finalize_grading_result(evaluations, questions)

        self.assertEqual(result["total_score"], 20)
        self.assertEqual(result["max_total_points"], 20)
        self.assertEqual(result["percentage"], 100)

    def test_empty_evaluations_yields_zero_score_not_a_crash(self):
        questions = [{"question_number": 1, "points": 10}]

        result = self.processor._finalize_grading_result([], questions)

        self.assertEqual(result["total_score"], 0)
        self.assertEqual(result["max_total_points"], 10)
        self.assertEqual(result["percentage"], 0)

    def test_zero_max_total_points_does_not_divide_by_zero(self):
        result = self.processor._finalize_grading_result([], [])
        self.assertEqual(result["percentage"], 0)

    def test_points_awarded_legacy_key_is_still_read(self):
        # _grade_question_batch's prompt asks for score_awarded, but the
        # fallback to points_awarded predates this fix and is preserved.
        questions = [{"question_number": 1, "points": 10}]
        evaluations = [{"question_number": 1, "points_awarded": 6}]

        result = self.processor._finalize_grading_result(evaluations, questions)

        self.assertEqual(result["total_score"], 6)


@override_settings(GRADING_SECOND_OPINION_ENABLED=False)
class SinglePassPathAppliesArithmeticProtectionTest(SimpleTestCase):
    """
    Integration-level proof that the single-pass path (<=
    GRADING_QUESTIONS_PER_CHUNK questions) is now covered — this is the
    exact gap H1 describes. Exercises _grade_student_submission_impl
    directly with a stubbed execute_graded_task, so no DB/billing
    machinery is needed to prove the arithmetic protection is applied.
    """

    def setUp(self):
        self.processor = AIProcessor()

    def _stub_response(self, content):
        from unittest.mock import MagicMock

        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = content
        response.usage.total_tokens = 100
        return response

    def test_single_pass_result_is_recomputed_not_trusted(self):
        import json
        from unittest.mock import patch

        rubric = json.dumps([{"question_number": 1, "points": 10}])
        answers = json.dumps([{"question_number": 1, "answer_html": "x"}])

        # The model lies on both counts: over-awards the one question
        # (15 > the 10 it's worth) AND reports an unrelated percentage.
        model_content = json.dumps(
            {
                "question_evaluations": [
                    {
                        "question_number": 1,
                        "score_awarded": 15,
                        "evidence_quotes": ["x"],
                    }
                ],
                "grading_summary": {
                    "total_score": 15,
                    "max_total_points": 10,
                    "percentage": 999,
                },
            }
        )

        with patch.object(
            self.processor,
            "execute_graded_task",
            return_value=self._stub_response(model_content),
        ):
            result = self.processor._grade_student_submission_impl(
                user=object(), rubric_json=rubric, answer_json=answers
            )

        # The model's numbers are gone; only the clamped/recomputed ones
        # survive.
        self.assertEqual(result["grading_summary"]["total_score"], 10)
        self.assertEqual(result["grading_summary"]["max_total_points"], 10)
        self.assertEqual(result["grading_summary"]["percentage"], 100)
        self.assertEqual(result["question_evaluations"][0]["score_awarded"], 10)
        self.assertIn("score_calculation_verification", result)
