"""
Unit coverage for ai_processor/evidence.py — mechanical verification of
grading evidence quotes.

The properties locked here:
- Verification is normalization-tolerant (HTML, entities, unicode, case,
  whitespace, smart punctuation) but NEVER fuzzy: a paraphrase or a
  changed word does not verify.
- Both tag-stripping strategies are tried (tags→space and tags→empty), so
  quotes verify whether markup sits between words or inside them.
- Policy: points awarded on a non-empty answer require ≥1 VERIFIED quote;
  fabricated-only evidence, absent evidence, and points-on-a-blank-answer
  are violations in strict mode; zero-score evaluations are exempt;
  deterministic evaluations are never inspected.
- MODE_LOG annotates without rejecting; MODE_OFF touches nothing.

Run with:
    python manage.py test ai_processor.tests_grading_evidence
"""

from django.test import SimpleTestCase

from ai_processor.evidence import (
    MODE_LOG,
    MODE_OFF,
    MODE_STRICT,
    enforce_evidence,
    normalize_for_evidence,
    quote_appears_in_answer,
    verify_evaluation_evidence,
)
from ai_processor.services import AIProcessor

KEY_FN = AIProcessor._question_number_key


class NormalizeForEvidenceTest(SimpleTestCase):
    def test_none_and_empty(self):
        self.assertEqual(normalize_for_evidence(None), ("", ""))
        self.assertEqual(normalize_for_evidence(""), ("", ""))

    def test_two_tag_strategies(self):
        spaced, joined = normalize_for_evidence("<p>end.</p><p>Start</p>")
        self.assertEqual(spaced, "end. start")
        self.assertEqual(joined, "end.start")

    def test_entities_unicode_case_whitespace(self):
        spaced, _ = normalize_for_evidence("Tom &amp;  JERRY\nrun")
        self.assertEqual(spaced, "tom & jerry run")
        _, joined = normalize_for_evidence("H<sub>2</sub>O")
        self.assertEqual(joined, "h2o")

    def test_smart_punctuation_straightened(self):
        spaced, _ = normalize_for_evidence("the student’s “best” work — truly")
        self.assertEqual(spaced, 'the student\'s "best" work - truly')


class QuoteAppearsInAnswerTest(SimpleTestCase):
    ANSWER = (
        "<p>Photosynthesis converts light energy into chemical energy.</p>"
        "<p>It occurs in the chloroplasts of plant cells.</p>"
    )

    def test_exact_span_verifies(self):
        self.assertTrue(
            quote_appears_in_answer(
                "converts light energy into chemical energy", self.ANSWER
            )
        )

    def test_case_and_whitespace_tolerated(self):
        self.assertTrue(
            quote_appears_in_answer("  PHOTOSYNTHESIS   converts light ", self.ANSWER)
        )

    def test_quote_with_html_verifies_against_plain_and_vice_versa(self):
        self.assertTrue(
            quote_appears_in_answer("<p>It occurs in the chloroplasts</p>", self.ANSWER)
        )
        self.assertTrue(
            quote_appears_in_answer("chloroplasts of plant cells", self.ANSWER)
        )

    def test_inline_markup_inside_a_word(self):
        # tags→empty protects words split by inline markup.
        self.assertTrue(
            quote_appears_in_answer("photosynthesis", "<b>photo</b>synthesis rules")
        )

    def test_span_across_block_boundary(self):
        # tags→space protects word boundaries between block elements.
        self.assertTrue(
            quote_appears_in_answer("chemical energy. It occurs", self.ANSWER)
        )

    def test_smart_quotes_and_entities(self):
        answer = "<p>The student’s answer was &quot;correct&quot;.</p>"
        self.assertTrue(quote_appears_in_answer("the student's answer", answer))
        self.assertTrue(quote_appears_in_answer('was "correct"', answer))

    def test_unicode_folding(self):
        self.assertTrue(quote_appears_in_answer("H₂O", "Water is H<sub>2</sub>O."))

    def test_paraphrase_does_not_verify(self):
        self.assertFalse(
            quote_appears_in_answer("turns sunlight into usable energy", self.ANSWER)
        )

    def test_changed_word_does_not_verify(self):
        self.assertFalse(
            quote_appears_in_answer(
                "converts light energy into kinetic energy", self.ANSWER
            )
        )

    def test_empty_quote_or_answer_never_verifies(self):
        self.assertFalse(quote_appears_in_answer("", self.ANSWER))
        self.assertFalse(quote_appears_in_answer("   ", self.ANSWER))
        self.assertFalse(quote_appears_in_answer("photosynthesis", ""))
        self.assertFalse(quote_appears_in_answer("photosynthesis", "<p>  </p>"))


class VerifyEvaluationEvidenceTest(SimpleTestCase):
    def test_mixed_verified_and_fabricated(self):
        evaluation = {"evidence_quotes": ["light energy", "made-up nonsense", 42, "  "]}
        verified, unverified = verify_evaluation_evidence(
            evaluation, "<p>Converts light energy.</p>"
        )
        self.assertEqual(verified, ["light energy"])
        # 42 and "  " are coerced away entirely; only the fabricated
        # string counts as unverified.
        self.assertEqual(unverified, 1)

    def test_non_list_quotes(self):
        verified, unverified = verify_evaluation_evidence(
            {"evidence_quotes": "not a list"}, "answer"
        )
        self.assertEqual((verified, unverified), ([], 0))


def _evaluation(number=1, score=5, quotes=None, **overrides):
    evaluation = {
        "question_number": number,
        "score_awarded": score,
        "evidence_quotes": quotes if quotes is not None else [],
    }
    evaluation.update(overrides)
    return evaluation


class EnforceEvidenceStrictTest(SimpleTestCase):
    ANSWERS = {KEY_FN(1): "<p>The mitochondria is the powerhouse of the cell.</p>"}

    def test_verified_quote_passes_and_annotates(self):
        evaluation = _evaluation(quotes=["powerhouse of the cell"])
        violations = enforce_evidence(
            [evaluation], self.ANSWERS, mode=MODE_STRICT, key_fn=KEY_FN
        )
        self.assertEqual(violations, [])
        self.assertTrue(evaluation["evidence_verified"])
        self.assertEqual(evaluation["evidence_quotes"], ["powerhouse of the cell"])

    def test_fabricated_only_evidence_is_a_violation(self):
        evaluation = _evaluation(quotes=["the chloroplast produces ATP"])
        violations = enforce_evidence(
            [evaluation], self.ANSWERS, mode=MODE_STRICT, key_fn=KEY_FN
        )
        self.assertEqual(len(violations), 1)
        self.assertIn("fabricated", violations[0])
        self.assertFalse(evaluation["evidence_verified"])
        self.assertEqual(evaluation["evidence_quotes"], [])

    def test_no_evidence_with_points_is_a_violation(self):
        evaluation = _evaluation(quotes=[])
        violations = enforce_evidence(
            [evaluation], self.ANSWERS, mode=MODE_STRICT, key_fn=KEY_FN
        )
        self.assertEqual(len(violations), 1)
        self.assertIn("no evidence", violations[0])

    def test_points_awarded_to_blank_answer_is_a_violation(self):
        # The purest hallucinated-grading case: awarding points to a
        # question with no answer at all.
        evaluation = _evaluation(number=2, quotes=["anything"])
        violations = enforce_evidence(
            [evaluation],
            {KEY_FN(2): ""},
            mode=MODE_STRICT,
            key_fn=KEY_FN,
        )
        self.assertEqual(len(violations), 1)
        self.assertIn("empty/blank", violations[0])

    def test_answer_missing_from_map_treated_as_blank(self):
        evaluation = _evaluation(number=99, quotes=["anything"])
        violations = enforce_evidence(
            [evaluation], self.ANSWERS, mode=MODE_STRICT, key_fn=KEY_FN
        )
        self.assertEqual(len(violations), 1)
        self.assertIn("empty/blank", violations[0])

    def test_zero_score_is_exempt(self):
        for evaluation in [
            _evaluation(score=0, quotes=[]),
            _evaluation(score=0, quotes=["fabricated stuff"]),
            _evaluation(score=0, level_achieved="not_attempted"),
        ]:
            violations = enforce_evidence(
                [evaluation], self.ANSWERS, mode=MODE_STRICT, key_fn=KEY_FN
            )
            self.assertEqual(violations, [], evaluation)
            self.assertTrue(evaluation["evidence_verified"])

    def test_zero_score_fabricated_quotes_still_filtered(self):
        evaluation = _evaluation(score=0, quotes=["fabricated stuff"])
        enforce_evidence([evaluation], self.ANSWERS, mode=MODE_STRICT, key_fn=KEY_FN)
        self.assertEqual(evaluation["evidence_quotes"], [])
        self.assertEqual(evaluation["unverified_evidence_count"], 1)

    def test_mixed_quotes_keep_verified_and_count_dropped(self):
        evaluation = _evaluation(
            quotes=["powerhouse of the cell", "completely invented"]
        )
        violations = enforce_evidence(
            [evaluation], self.ANSWERS, mode=MODE_STRICT, key_fn=KEY_FN
        )
        self.assertEqual(violations, [])
        self.assertEqual(evaluation["evidence_quotes"], ["powerhouse of the cell"])
        self.assertEqual(evaluation["unverified_evidence_count"], 1)

    def test_deterministic_evaluations_are_never_inspected(self):
        evaluation = _evaluation(quotes=[], graded_by="deterministic")
        violations = enforce_evidence(
            [evaluation], self.ANSWERS, mode=MODE_STRICT, key_fn=KEY_FN
        )
        self.assertEqual(violations, [])
        self.assertNotIn("evidence_verified", evaluation)

    def test_int_and_string_question_numbers_join(self):
        evaluation = _evaluation(number="1", quotes=["powerhouse of the cell"])
        violations = enforce_evidence(
            [evaluation], self.ANSWERS, mode=MODE_STRICT, key_fn=KEY_FN
        )
        self.assertEqual(violations, [])
        self.assertTrue(evaluation["evidence_verified"])

    def test_non_dict_and_bool_score_are_safe(self):
        evaluations = ["garbage", _evaluation(score=True, quotes=[])]
        violations = enforce_evidence(
            evaluations, self.ANSWERS, mode=MODE_STRICT, key_fn=KEY_FN
        )
        # bool is not a real score (coerces to 0) — exempt, not a crash.
        self.assertEqual(violations, [])


class EnforceEvidenceModesTest(SimpleTestCase):
    ANSWERS = {KEY_FN(1): "<p>A real answer.</p>"}

    def test_log_mode_annotates_but_never_rejects(self):
        evaluation = _evaluation(quotes=["fabricated"])
        violations = enforce_evidence(
            [evaluation], self.ANSWERS, mode=MODE_LOG, key_fn=KEY_FN
        )
        self.assertEqual(violations, [])
        self.assertFalse(evaluation["evidence_verified"])
        self.assertEqual(evaluation["evidence_quotes"], [])

    def test_off_mode_touches_nothing(self):
        evaluation = _evaluation(quotes=["fabricated"])
        violations = enforce_evidence(
            [evaluation], self.ANSWERS, mode=MODE_OFF, key_fn=KEY_FN
        )
        self.assertEqual(violations, [])
        self.assertEqual(evaluation["evidence_quotes"], ["fabricated"])
        self.assertNotIn("evidence_verified", evaluation)
