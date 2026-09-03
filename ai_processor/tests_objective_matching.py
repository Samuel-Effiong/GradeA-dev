"""
Unit coverage for ai_processor/objective_grading.py — the Tier 0
deterministic matcher for OBJECTIVE questions.

Every row of the edge-case ledger in the implementation plan maps to at
least one test here. The load-bearing property throughout: the matcher
only CLAIMS (CORRECT / INCORRECT / NOT_ATTEMPTED) what it can match
unambiguously; every doubtful case must come back AMBIGUOUS (deferred to
the LLM) — never a forced wrong grade.

Pure-logic module, so SimpleTestCase (no DB) throughout.

Run with:
    python manage.py test ai_processor.tests_objective_matching
"""

from django.test import SimpleTestCase

from ai_processor.objective_grading import (
    AMBIGUOUS,
    CORRECT,
    INCORRECT,
    NOT_APPLICABLE,
    NOT_ATTEMPTED,
    build_objective_evaluation,
    build_option_index,
    match_objective_answer,
    normalize_text,
    split_letter_prefix,
)


def _mcq(**overrides):
    question = {
        "question_number": 1,
        "question_text": "What is the capital of France?",
        "question_type": "OBJECTIVE",
        "points": 5,
        "options": ["London", "Berlin", "Paris", "Madrid"],
        "rubric": [],
        "model_answer": "Paris",
    }
    question.update(overrides)
    return question


class NormalizeTextTest(SimpleTestCase):
    def test_none_and_empty(self):
        self.assertEqual(normalize_text(None), "")
        self.assertEqual(normalize_text(""), "")
        self.assertEqual(normalize_text("   "), "")

    def test_html_tags_strip_to_empty_not_space(self):
        # "H<sub>2</sub>O" must equal a plain-text "H2O" answer — tags
        # stripped to a space would break every chemistry/math option.
        self.assertEqual(normalize_text("H<sub>2</sub>O"), "h2o")
        self.assertEqual(normalize_text("<p>Paris</p>"), "paris")

    def test_entities_unescaped(self):
        self.assertEqual(normalize_text("Tom &amp; Jerry"), "tom & jerry")

    def test_nfkc_folds_unicode_variants(self):
        self.assertEqual(normalize_text("H₂O"), "h2o")  # subscript two
        self.assertEqual(normalize_text("Ｐａｒｉｓ"), "paris")  # full-width

    def test_casefold_and_whitespace_collapse(self):
        self.assertEqual(normalize_text("  PARIS   is\n nice "), "paris is nice")

    def test_trailing_punctuation_stripped(self):
        self.assertEqual(normalize_text("Paris."), "paris")
        self.assertEqual(normalize_text("Paris,"), "paris")

    def test_non_string_input(self):
        self.assertEqual(normalize_text(42), "42")


class SplitLetterPrefixTest(SimpleTestCase):
    def test_prefix_forms(self):
        for raw in ["b) berlin", "(b) berlin", "b. berlin", "b: berlin", "b- berlin"]:
            self.assertEqual(split_letter_prefix(raw), ("b", "berlin"), raw)

    def test_bare_letter(self):
        self.assertEqual(split_letter_prefix("b"), ("b", ""))
        # normalize_text strips the trailing dot before this is called
        self.assertEqual(split_letter_prefix(normalize_text("B.")), ("b", ""))
        self.assertEqual(split_letter_prefix(normalize_text("(C)")), ("c", ""))

    def test_word_starting_with_letter_is_not_a_prefix(self):
        # "berlin" must NOT parse as prefix "b" + "erlin" — the regex
        # requires an explicit delimiter after the letter.
        self.assertEqual(split_letter_prefix("berlin"), (None, "berlin"))

    def test_empty(self):
        self.assertEqual(split_letter_prefix(""), (None, ""))


class BuildOptionIndexTest(SimpleTestCase):
    def test_clean_options(self):
        index = build_option_index(["London", "Berlin", "Paris"])
        self.assertIsNotNone(index)
        self.assertEqual(index.by_text["paris"], 2)
        self.assertEqual(index.by_letter["a"], 0)
        self.assertEqual(index.by_letter["c"], 2)

    def test_embedded_prefixes(self):
        index = build_option_index(["A) H<sub>2</sub>O", "B) CO<sub>2</sub>"])
        self.assertIsNotNone(index)
        self.assertEqual(index.by_text["h2o"], 0)
        self.assertEqual(index.by_letter["b"], 1)

    def test_embedded_letters_conflicting_with_position_are_unresolvable(self):
        # Options listed out of letter order: both schemes exist and
        # disagree, so bare-letter answers must be unresolvable, while
        # text matching still works.
        index = build_option_index(["B) Berlin", "A) Athens"])
        self.assertIsNotNone(index)
        self.assertIsNone(index.by_letter["a"])
        self.assertIsNone(index.by_letter["b"])
        self.assertEqual(index.by_text["berlin"], 0)

    def test_rejects_non_list_and_too_few(self):
        self.assertIsNone(build_option_index(None))
        self.assertIsNone(build_option_index("Paris"))
        self.assertIsNone(build_option_index([]))
        self.assertIsNone(build_option_index(["only one"]))

    def test_rejects_empty_option(self):
        self.assertIsNone(build_option_index(["Paris", "  "]))

    def test_rejects_duplicates_after_normalization(self):
        self.assertIsNone(build_option_index(["true", "True", "x", "y"]))
        self.assertIsNone(build_option_index(["<p>Paris</p>", "PARIS", "Rome"]))


class MatchObjectiveAnswerTest(SimpleTestCase):
    # ── Claimed: happy paths ──────────────────────────────────────────

    def test_full_text_answer_correct_and_incorrect(self):
        self.assertEqual(match_objective_answer(_mcq(), "<p>Paris</p>"), CORRECT)
        self.assertEqual(match_objective_answer(_mcq(), "Berlin"), INCORRECT)

    def test_bare_letter_answer(self):
        self.assertEqual(match_objective_answer(_mcq(), "C"), CORRECT)  # Paris
        self.assertEqual(match_objective_answer(_mcq(), "b."), INCORRECT)
        self.assertEqual(match_objective_answer(_mcq(), "(c)"), CORRECT)

    def test_prefixed_text_answer_letter_and_body_agree(self):
        self.assertEqual(match_objective_answer(_mcq(), "C) Paris"), CORRECT)
        self.assertEqual(match_objective_answer(_mcq(), "B) Berlin"), INCORRECT)

    def test_unicode_and_html_equivalence(self):
        question = _mcq(
            options=["A) H<sub>2</sub>O", "B) CO<sub>2</sub>", "C) O<sub>2</sub>"],
            model_answer="A) H2O",
        )
        self.assertEqual(match_objective_answer(question, "H₂O"), CORRECT)
        self.assertEqual(match_objective_answer(question, "a"), CORRECT)
        self.assertEqual(match_objective_answer(question, "CO2"), INCORRECT)

    def test_not_attempted_for_empty_answers(self):
        for empty in ["", None, "   ", "<p></p>", "<p>  </p>"]:
            self.assertEqual(
                match_objective_answer(_mcq(), empty), NOT_ATTEMPTED, repr(empty)
            )

    def test_model_answer_letter_only_resolves_positionally(self):
        # Bad-but-recoverable data: model_answer "C" instead of "Paris".
        question = _mcq(model_answer="C")
        self.assertEqual(match_objective_answer(question, "Paris"), CORRECT)
        self.assertEqual(match_objective_answer(question, "London"), INCORRECT)

    def test_true_false_synonyms(self):
        question = _mcq(options=["True", "False"], model_answer="True")
        for yes in ["T", "true", "Correct", "YES"]:
            self.assertEqual(match_objective_answer(question, yes), CORRECT, yes)
        for no in ["F", "false", "Incorrect", "no"]:
            self.assertEqual(match_objective_answer(question, no), INCORRECT, no)

    def test_question_number_type_never_matters_to_matching(self):
        # Matching is on content; question_number int/str is the
        # pipeline's join key, not the matcher's concern.
        self.assertEqual(
            match_objective_answer(_mcq(question_number="1"), "Paris"), CORRECT
        )

    # ── Deferred: every doubt goes to the LLM ─────────────────────────

    def test_right_letter_wrong_text_is_ambiguous(self):
        # Ledger #1 — the classic unsafe case: letter says one option,
        # text beside it says another.
        self.assertEqual(match_objective_answer(_mcq(), "C) London"), AMBIGUOUS)

    def test_letter_with_unmatchable_text_is_ambiguous(self):
        # Letter resolves but its body matches nothing (typo/free text).
        self.assertEqual(match_objective_answer(_mcq(), "C) Pariss"), AMBIGUOUS)

    def test_typoed_model_answer_is_ambiguous(self):
        # Ledger #2 — the answer key itself doesn't match any option.
        self.assertEqual(
            match_objective_answer(_mcq(model_answer="Pariss"), "Paris"), AMBIGUOUS
        )

    def test_missing_model_answer_is_ambiguous(self):
        self.assertEqual(
            match_objective_answer(_mcq(model_answer=""), "Paris"), AMBIGUOUS
        )
        self.assertEqual(
            match_objective_answer(_mcq(model_answer=None), "Paris"), AMBIGUOUS
        )

    def test_paraphrase_is_ambiguous(self):
        # Ledger #9 — could be right, but it isn't verbatim an option.
        self.assertEqual(
            match_objective_answer(_mcq(), "It's the French capital"), AMBIGUOUS
        )

    def test_multi_select_answers_are_ambiguous(self):
        # Ledger #10 — multi-select has special all-or-nothing prompt rules.
        for multi in ["A and C", "b, d", "A & B", "a + c"]:
            self.assertEqual(match_objective_answer(_mcq(), multi), AMBIGUOUS, multi)

    def test_math_equivalence_is_ambiguous(self):
        # Ledger #12 — "5=x" vs "x=5" is the LLM's judgment, by design.
        question = _mcq(options=["x=5", "x=7"], model_answer="x=5")
        self.assertEqual(match_objective_answer(question, "5=x"), AMBIGUOUS)

    def test_bad_points_are_ambiguous(self):
        # Ledger #13.
        for bad in [None, 0, -3, "many", float("nan")]:
            self.assertEqual(
                match_objective_answer(_mcq(points=bad), "Paris"), AMBIGUOUS, bad
            )

    def test_bad_options_are_ambiguous(self):
        # Ledger #14 + #5.
        for bad in [[], ["only"], None, "not-a-list", ["true", "True", "x", "y"]]:
            self.assertEqual(
                match_objective_answer(_mcq(options=bad), "x"), AMBIGUOUS, bad
            )

    def test_conflicting_embedded_letters_make_bare_letter_ambiguous(self):
        question = _mcq(options=["B) Berlin", "A) Athens"], model_answer="Berlin")
        self.assertEqual(match_objective_answer(question, "a"), AMBIGUOUS)
        # Full-text still resolves fine.
        self.assertEqual(match_objective_answer(question, "Athens"), INCORRECT)

    # ── Not applicable: never touches subjective questions ────────────

    def test_essay_and_short_answer_are_not_applicable(self):
        # Ledger #20.
        for question_type in ["ESSAY", "SHORT-ANSWER", "", None]:
            question = _mcq(question_type=question_type)
            self.assertEqual(
                match_objective_answer(question, "Paris"),
                NOT_APPLICABLE,
                question_type,
            )

    def test_non_dict_question_is_not_applicable(self):
        self.assertEqual(match_objective_answer(None, "x"), NOT_APPLICABLE)
        self.assertEqual(match_objective_answer("question", "x"), NOT_APPLICABLE)

    def test_question_type_case_insensitive(self):
        self.assertEqual(
            match_objective_answer(_mcq(question_type="objective"), "Paris"), CORRECT
        )


class BuildObjectiveEvaluationTest(SimpleTestCase):
    # Field set the LLM contract guarantees (GRADING_ASSIGNMENT_PROMPT_4)
    CONTRACT_FIELDS = {
        "question_number",
        "question_text",
        "question_type",
        "max_points",
        "student_answer",
        "model_answer",
        "score_awarded",
        "level_achieved",
        "evaluation_rationale",
        "strengths",
        "weaknesses",
        "improvement_suggestions",
        "feedback_for_student",
    }

    def test_correct_evaluation_shape_and_values(self):
        evaluation = build_objective_evaluation(_mcq(), "<p>Paris</p>", CORRECT)
        self.assertTrue(self.CONTRACT_FIELDS.issubset(evaluation.keys()))
        self.assertEqual(evaluation["score_awarded"], 5)
        self.assertEqual(evaluation["max_points"], 5)
        self.assertEqual(evaluation["level_achieved"], "correct")
        self.assertEqual(evaluation["graded_by"], "deterministic")
        self.assertEqual(evaluation["student_answer"], "<p>Paris</p>")

    def test_incorrect_evaluation_names_the_correct_answer(self):
        evaluation = build_objective_evaluation(_mcq(), "Berlin", INCORRECT)
        self.assertEqual(evaluation["score_awarded"], 0)
        self.assertEqual(evaluation["level_achieved"], "incorrect")
        self.assertIn("Paris", evaluation["feedback_for_student"])

    def test_not_attempted_evaluation(self):
        evaluation = build_objective_evaluation(_mcq(), "", NOT_ATTEMPTED)
        self.assertEqual(evaluation["score_awarded"], 0)
        self.assertEqual(evaluation["level_achieved"], "not_attempted")

    def test_fractional_points_preserved(self):
        evaluation = build_objective_evaluation(_mcq(points=2.5), "Paris", CORRECT)
        self.assertEqual(evaluation["score_awarded"], 2.5)

    def test_unclaimed_match_raises(self):
        with self.assertRaises(ValueError):
            build_objective_evaluation(_mcq(), "Paris", AMBIGUOUS)
        with self.assertRaises(ValueError):
            build_objective_evaluation(_mcq(), "Paris", NOT_APPLICABLE)
