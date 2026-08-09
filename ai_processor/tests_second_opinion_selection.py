"""
Unit coverage for ai_processor/second_opinion.py — the trigger-selection,
model-picking, and comparison logic behind the selective blind second
opinion.

Locked here:
- Only LLM-graded, attempted questions are ever eligible; deterministic
  evaluations are never second-guessed.
- Trigger semantics: low run-confidence (strict <) selects everything
  eligible; a model-emitted flag_for_review selects exactly its own
  question; high-point questions select themselves; the QA sample is ONE
  rng draw per submission selecting the full eligible set. Reasons
  accumulate.
- pick_second_model never returns grader A's own model — same-model
  "independence" is a skip, not a fallback.
- compare_evaluations treats clamped score equality as agreement, and
  packages both sides of a disagreement for the teacher.

Run with:
    python manage.py test ai_processor.tests_second_opinion_selection
"""

from django.test import SimpleTestCase

from ai_processor.second_opinion import (
    REASON_HIGH_STAKES,
    REASON_LOW_CONFIDENCE,
    REASON_QA_SAMPLE,
    compare_evaluations,
    pick_second_model,
    select_second_opinion_targets,
)
from ai_processor.services import AIProcessor

KEY_FN = AIProcessor._question_number_key


class _FixedRng:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def random(self):
        self.calls += 1
        return self.value


def _evaluation(number, level="good", graded_by="some-model", flag=None, score=8):
    return {
        "question_number": number,
        "level_achieved": level,
        "graded_by": graded_by,
        "flag_for_review": flag,
        "score_awarded": score,
    }


def _question(number, points=10):
    return {"question_number": number, "points": points}


def _select(result, questions, **overrides):
    kwargs = {
        "key_fn": KEY_FN,
        "min_confidence": 80,
        "high_points_threshold": 15,
        "sample_rate": 0,
        "rng": _FixedRng(0.99),
    }
    kwargs.update(overrides)
    return select_second_opinion_targets(result, questions, **kwargs)


class SelectTargetsTest(SimpleTestCase):
    def test_nothing_triggers_on_a_confident_clean_run(self):
        result = {
            "grading_confidence": 90,
            "question_evaluations": [_evaluation(1), _evaluation(2)],
        }
        self.assertEqual(_select(result, [_question(1), _question(2)]), {})

    def test_low_confidence_selects_all_eligible(self):
        result = {
            "grading_confidence": 60,
            "question_evaluations": [_evaluation(1), _evaluation(2)],
        }
        selected = _select(result, [_question(1), _question(2)])
        self.assertEqual(set(selected), {KEY_FN(1), KEY_FN(2)})
        for reasons in selected.values():
            self.assertIn(REASON_LOW_CONFIDENCE, reasons)

    def test_confidence_exactly_at_threshold_does_not_trigger(self):
        # Ledger #9: strict <, not <=.
        result = {
            "grading_confidence": 80,
            "question_evaluations": [_evaluation(1)],
        }
        self.assertEqual(_select(result, [_question(1)]), {})

    def test_missing_confidence_is_treated_as_full_confidence(self):
        result = {"question_evaluations": [_evaluation(1)]}
        self.assertEqual(_select(result, [_question(1)]), {})

    def test_flag_selects_only_the_flagged_question(self):
        # Ledger #10: per-question granularity.
        result = {
            "grading_confidence": 95,
            "question_evaluations": [
                _evaluation(1, flag={"flag_type": "BORDERLINE_SCORE"}),
                _evaluation(2),
            ],
        }
        selected = _select(result, [_question(1), _question(2)])
        self.assertEqual(set(selected), {KEY_FN(1)})
        self.assertEqual(selected[KEY_FN(1)], ["flagged:BORDERLINE_SCORE"])

    def test_high_points_selects_only_the_expensive_question(self):
        result = {
            "grading_confidence": 95,
            "question_evaluations": [_evaluation(1), _evaluation(2)],
        }
        selected = _select(result, [_question(1, points=25), _question(2, points=5)])
        self.assertEqual(set(selected), {KEY_FN(1)})
        self.assertEqual(selected[KEY_FN(1)], [REASON_HIGH_STAKES])

    def test_qa_sample_is_one_draw_selecting_everything(self):
        # Ledger #11, both outcomes under a seeded rng.
        result = {
            "grading_confidence": 95,
            "question_evaluations": [_evaluation(1), _evaluation(2)],
        }
        questions = [_question(1), _question(2)]

        rng = _FixedRng(0.01)  # below the rate → sampled
        selected = _select(result, questions, sample_rate=0.05, rng=rng)
        self.assertEqual(set(selected), {KEY_FN(1), KEY_FN(2)})
        for reasons in selected.values():
            self.assertEqual(reasons, [REASON_QA_SAMPLE])
        self.assertEqual(rng.calls, 1)  # one draw per SUBMISSION

        rng = _FixedRng(0.99)  # above the rate → not sampled
        self.assertEqual(_select(result, questions, sample_rate=0.05, rng=rng), {})

    def test_zero_sample_rate_never_draws(self):
        result = {
            "grading_confidence": 95,
            "question_evaluations": [_evaluation(1)],
        }
        rng = _FixedRng(0.0)  # would always sample if drawn
        self.assertEqual(_select(result, [_question(1)], sample_rate=0, rng=rng), {})
        self.assertEqual(rng.calls, 0)

    def test_reasons_accumulate(self):
        result = {
            "grading_confidence": 50,
            "question_evaluations": [
                _evaluation(1, flag={"flag_type": "EXTRACTION_ERROR"})
            ],
        }
        selected = _select(result, [_question(1, points=20)])
        reasons = selected[KEY_FN(1)]
        self.assertIn(REASON_LOW_CONFIDENCE, reasons)
        self.assertIn("flagged:EXTRACTION_ERROR", reasons)
        self.assertIn(REASON_HIGH_STAKES, reasons)

    def test_deterministic_evaluations_are_never_eligible(self):
        # Ledger #1: even under the strongest trigger.
        result = {
            "grading_confidence": 10,
            "question_evaluations": [
                _evaluation(1, graded_by="deterministic"),
                _evaluation(2),
            ],
        }
        selected = _select(result, [_question(1), _question(2)])
        self.assertEqual(set(selected), {KEY_FN(2)})

    def test_not_attempted_is_never_eligible(self):
        # Ledger #8.
        result = {
            "grading_confidence": 10,
            "question_evaluations": [_evaluation(1, level="not_attempted")],
        }
        self.assertEqual(_select(result, [_question(1, points=25)]), {})

    def test_int_and_string_question_numbers_join(self):
        # Ledger #12: rubric says 1 (int), evaluation says "1" (str).
        result = {
            "grading_confidence": 95,
            "question_evaluations": [_evaluation("1")],
        }
        selected = _select(result, [_question(1, points=25)])
        self.assertEqual(set(selected), {KEY_FN(1)})


class PickSecondModelTest(SimpleTestCase):
    def test_picks_first_differing_candidate(self):
        self.assertEqual(
            pick_second_model("x-ai/grok-4.3", ["x-ai/grok-4.3", "deepseek/v4"]),
            "deepseek/v4",
        )

    def test_same_model_only_is_a_skip(self):
        # Ledger #2: grader A already ran (via fallback) on the only
        # candidate — no independent model exists.
        self.assertIsNone(pick_second_model("deepseek/v4", ["deepseek/v4"]))

    def test_empty_candidates(self):
        self.assertIsNone(pick_second_model("any", []))
        self.assertIsNone(pick_second_model("any", None))


class CompareEvaluationsTest(SimpleTestCase):
    def _a(self, number, score, rationale="A says so"):
        return {
            "question_number": number,
            "score_awarded": score,
            "level_achieved": "good",
            "evaluation_rationale": rationale,
            "evidence_quotes": ["span a"],
        }

    def _b(self, number, score, rationale="B says so"):
        return {
            "question_number": number,
            "score_awarded": score,
            "level_achieved": "fair",
            "evaluation_rationale": rationale,
            "evidence_quotes": ["span b"],
        }

    def test_equal_scores_agree(self):
        comparison = compare_evaluations(
            [self._a(1, 8)], [self._b(1, 8)], key_fn=KEY_FN
        )
        self.assertEqual(comparison["agreements"], [1])
        self.assertEqual(comparison["disagreements"], [])

    def test_different_scores_disagree_with_both_sides_packaged(self):
        comparison = compare_evaluations(
            [self._a(1, 8)], [self._b(1, 5)], key_fn=KEY_FN
        )
        self.assertEqual(comparison["agreements"], [])
        [disagreement] = comparison["disagreements"]
        self.assertEqual(disagreement["question_number"], 1)
        self.assertEqual(disagreement["a"]["score_awarded"], 8)
        self.assertEqual(disagreement["b"]["score_awarded"], 5)
        self.assertEqual(disagreement["a"]["evaluation_rationale"], "A says so")
        self.assertEqual(disagreement["b"]["evidence_quotes"], ["span b"])

    def test_b_awarding_points_where_a_gave_zero_is_a_disagreement(self):
        # Ledger #14.
        comparison = compare_evaluations(
            [self._a(1, 0)], [self._b(1, 5)], key_fn=KEY_FN
        )
        self.assertEqual(len(comparison["disagreements"]), 1)

    def test_keys_missing_from_b_are_skipped(self):
        comparison = compare_evaluations(
            [self._a(1, 8), self._a(2, 4)], [self._b(1, 8)], key_fn=KEY_FN
        )
        self.assertEqual(comparison["agreements"], [1])
        self.assertEqual(comparison["disagreements"], [])

    def test_int_str_question_numbers_join(self):
        comparison = compare_evaluations(
            [self._a(1, 8)], [self._b("1", 3)], key_fn=KEY_FN
        )
        self.assertEqual(len(comparison["disagreements"]), 1)

    def test_numeric_string_scores_are_coerced(self):
        comparison = compare_evaluations(
            [self._a(1, 8)], [self._b(1, "8")], key_fn=KEY_FN
        )
        self.assertEqual(comparison["agreements"], [1])
