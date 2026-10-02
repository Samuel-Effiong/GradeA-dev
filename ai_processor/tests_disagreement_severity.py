"""
Unit coverage for disagreement severity in
ai_processor/second_opinion.py::compare_evaluations.

The design position locked here: per-question equality REMAINS the
agreement test (discrete rubric levels — a tolerance would swallow
one-level disagreements, the most informative kind). Severity classifies
each disagreement AFTER detection, for teacher triage; it never
suppresses one.

Run with:
    python manage.py test ai_processor.tests_disagreement_severity
"""

from django.test import SimpleTestCase

from ai_processor.second_opinion import (
    TIER_BORDERLINE,
    TIER_CRITICAL,
    TIER_MODERATE,
    compare_evaluations,
)
from ai_processor.services import AIProcessor

KEY_FN = AIProcessor._question_number_key


def _essay_question(number=1, points=20, level_points=(20, 15, 8, 0)):
    return {
        "question_number": number,
        "question_type": "ESSAY",
        "points": points,
        "rubric": [
            {"level": f"level-{p}", "description": "d", "points": p}
            for p in level_points
        ],
    }


def _eval(number, score):
    return {
        "question_number": number,
        "score_awarded": score,
        "level_achieved": "good",
        "evaluation_rationale": "r",
        "evidence_quotes": [],
    }


def _compare(a_score, b_score, question, **kwargs):
    result = compare_evaluations(
        [_eval(question["question_number"], a_score)],
        [_eval(question["question_number"], b_score)],
        key_fn=KEY_FN,
        questions=[question],
        **kwargs,
    )
    return result["disagreements"][0] if result["disagreements"] else None


class SeverityTierTest(SimpleTestCase):
    def test_one_level_apart_moderate_by_fraction(self):
        # Ledger #1: 15 vs 8 on a 20-point rubric — adjacent rungs,
        # gap 7/20 = 0.35 → moderate.
        disagreement = _compare(15, 8, _essay_question())
        severity = disagreement["severity"]
        self.assertEqual(severity["gap_points"], 7)
        self.assertEqual(severity["gap_fraction"], 0.35)
        self.assertEqual(severity["levels_apart"], 1)
        self.assertEqual(severity["tier"], TIER_MODERATE)

    def test_extreme_gap_is_critical(self):
        # Ledger #2: excellent(20) vs poor(0) — 3 rungs, fraction 1.0.
        severity = _compare(20, 0, _essay_question())["severity"]
        self.assertEqual(severity["gap_fraction"], 1.0)
        self.assertEqual(severity["levels_apart"], 3)
        self.assertEqual(severity["tier"], TIER_CRITICAL)

    def test_two_levels_apart_is_critical_even_with_small_fraction(self):
        # Tightly-packed rubric (20, 19, 18, 0): 20 vs 18 is only a 0.1
        # point-fraction gap, but TWO rungs apart — someone is wrong, not
        # borderline. This isolates the levels_apart >= 2 rule from the
        # fraction rule.
        question = _essay_question(level_points=(20, 19, 18, 0))
        severity = _compare(20, 18, question)["severity"]
        self.assertEqual(severity["gap_fraction"], 0.1)
        self.assertEqual(severity["levels_apart"], 2)
        self.assertEqual(severity["tier"], TIER_CRITICAL)

    def test_adjacent_top_levels_small_fraction_is_borderline(self):
        # 20 vs 15 on 20: adjacent rungs, fraction 0.25... exactly at the
        # moderate threshold → moderate. Use a wider rubric for a true
        # borderline: 20 vs 17 with levels (20, 17, 8, 0) → 0.15.
        question = _essay_question(level_points=(20, 17, 8, 0))
        severity = _compare(20, 17, question)["severity"]
        self.assertEqual(severity["levels_apart"], 1)
        self.assertEqual(severity["gap_fraction"], 0.15)
        self.assertEqual(severity["tier"], TIER_BORDERLINE)

    def test_fraction_exactly_at_moderate_threshold_is_moderate(self):
        # Boundary: 0.25 uses >=.
        severity = _compare(20, 15, _essay_question())["severity"]
        self.assertEqual(severity["gap_fraction"], 0.25)
        self.assertEqual(severity["tier"], TIER_MODERATE)

    def test_objective_full_vs_zero_is_critical(self):
        # Ledger #3: an OBJECTIVE ambiguous-fallback question has an
        # empty rubric — no rungs — but a full-vs-zero split is a
        # correct/incorrect disagreement: fraction 1.0 → critical.
        question = {
            "question_number": 1,
            "question_type": "OBJECTIVE",
            "points": 5,
            "rubric": [],
        }
        severity = _compare(5, 0, question)["severity"]
        self.assertEqual(severity["gap_fraction"], 1.0)
        self.assertIsNone(severity["levels_apart"])
        self.assertEqual(severity["tier"], TIER_CRITICAL)

    def test_unknown_points_is_moderate_never_downgraded(self):
        # Ledger #4: what we can't measure must not sort last.
        question = {"question_number": 1, "points": 0, "rubric": []}
        severity = _compare(3, 1, question)["severity"]
        self.assertIsNone(severity["gap_fraction"])
        self.assertEqual(severity["tier"], TIER_MODERATE)

    def test_score_not_a_rubric_value_falls_back_to_fraction(self):
        # Ledger #5: shouldn't happen post-clamping, but must not crash.
        severity = _compare(9, 8, _essay_question())["severity"]
        self.assertIsNone(severity["levels_apart"])
        self.assertEqual(severity["tier"], TIER_BORDERLINE)  # 1/20 = 0.05

    def test_without_questions_no_severity_key(self):
        # Ledger #6: backward compatible.
        result = compare_evaluations([_eval(1, 8)], [_eval(1, 5)], key_fn=KEY_FN)
        self.assertNotIn("severity", result["disagreements"][0])

    def test_threshold_overrides_are_respected(self):
        # With a stricter critical threshold, 0.35 becomes critical.
        disagreement = _compare(
            15, 8, _essay_question(), critical_fraction=0.3, moderate_fraction=0.1
        )
        self.assertEqual(disagreement["severity"]["tier"], TIER_CRITICAL)

    def test_agreement_never_carries_severity(self):
        result = compare_evaluations(
            [_eval(1, 15)],
            [_eval(1, 15)],
            key_fn=KEY_FN,
            questions=[_essay_question()],
        )
        self.assertEqual(result["agreements"], [1])
        self.assertEqual(result["disagreements"], [])
