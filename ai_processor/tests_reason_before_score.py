"""
Coverage for the "reason before score" change set.

Three related fixes, all aimed at the same finding: the grading pipeline
was structurally encouraged to pick a number first and justify it after.

1. FIELD ORDER IS BEHAVIOUR. Under strict structured output the model
   emits fields in the order the schema declares them, and every token it
   writes conditions what follows. QUESTION_EVALUATION_SCHEMA used to put
   score_awarded and level_achieved BEFORE evidence_quotes and
   evaluation_rationale, so the model committed to a score and then wrote
   a rationale that agreed with it. The order now runs evidence ->
   rationale -> level_decision -> level_achieved -> score_awarded, and
   the single-pass schema likewise grades every question before it is
   asked for a total. These tests pin that order, because it is the kind
   of thing an unrelated tidy-up would silently undo.

2. level_decision, a per-question uncertainty signal, drives second
   opinions. The existing submission-level grading_confidence proved
   useless for routing (a live run returned >= 80 on 120 of 124
   questions), so the second-opinion budget was effectively being spent
   on whichever questions were worth the most points rather than on
   whichever grades were most doubtful.

3. LaTeX whitespace no longer defeats deterministic objective matching.
   `$x^2 \\ln(x)$` and `$x^2\\ln(x)$` are the same expression; only
   whitespace INSIDE math spans is relaxed, so prose stays exact.

Run with:
    python manage.py test ai_processor.tests_reason_before_score
"""

from django.test import SimpleTestCase

from ai_processor.grading_schemas import (
    GRADING_SINGLE_PASS_RESPONSE_SCHEMA,
    QUESTION_EVALUATION_SCHEMA,
)
from ai_processor.objective_grading import (
    AMBIGUOUS,
    CORRECT,
    INCORRECT,
    build_objective_evaluation,
    build_option_index,
    collapse_math_whitespace,
    match_objective_answer,
    normalize_text,
)
from ai_processor.second_opinion import (
    REASON_BORDERLINE_LEVEL,
    REASON_HIGH_STAKES,
    select_second_opinion_targets,
)
from ai_processor.services import AIProcessor


def _key(value):
    return AIProcessor._question_number_key(value)


# ── 1. Schema ordering ────────────────────────────────────────────────────


class QuestionEvaluationFieldOrderTest(SimpleTestCase):
    """
    The per-question contract must make the model reason before it
    scores. These assert positions, not mere presence.
    """

    def setUp(self):
        self.order = list(QUESTION_EVALUATION_SCHEMA["properties"])

    def _before(self, earlier, later):
        self.assertLess(
            self.order.index(earlier),
            self.order.index(later),
            f"{earlier!r} must be emitted before {later!r} — the model "
            f"generates fields in schema order, so putting {later!r} first "
            f"makes it a guess that {earlier!r} is then written to justify.",
        )

    def test_evidence_and_rationale_precede_the_score(self):
        self._before("evidence_quotes", "score_awarded")
        self._before("evaluation_rationale", "score_awarded")
        self._before("evidence_quotes", "level_achieved")
        self._before("evaluation_rationale", "level_achieved")

    def test_evidence_precedes_the_rationale_that_uses_it(self):
        self._before("evidence_quotes", "evaluation_rationale")

    def test_level_is_chosen_before_its_points_are_named(self):
        # Policy rule 1: score_awarded must be exactly the selected
        # level's points. The level therefore has to come first for the
        # number to be a consequence of it.
        self._before("level_achieved", "score_awarded")

    def test_level_decision_is_declared_before_the_level(self):
        self._before("evaluation_rationale", "level_decision")
        self._before("level_decision", "level_achieved")

    def test_answer_context_precedes_all_judgment(self):
        for field in ("question_text", "student_answer", "model_answer"):
            self._before(field, "evidence_quotes")

    def test_required_list_matches_properties_order(self):
        # Some providers key generation order off `required` instead of
        # `properties`; if the two disagree the ordering guarantee holds
        # on one provider and silently not on another.
        self.assertEqual(self.order, list(QUESTION_EVALUATION_SCHEMA["required"]))

    def test_level_decision_is_a_closed_enum(self):
        field = QUESTION_EVALUATION_SCHEMA["properties"]["level_decision"]
        self.assertEqual(sorted(field["enum"]), ["borderline", "clear"])


class SinglePassFieldOrderTest(SimpleTestCase):
    """The same argument one level up: grade the paper, then total it."""

    def setUp(self):
        self.order = list(GRADING_SINGLE_PASS_RESPONSE_SCHEMA["schema"]["properties"])

    def test_questions_are_graded_before_the_total_is_stated(self):
        self.assertLess(
            self.order.index("question_evaluations"),
            self.order.index("grading_summary"),
            "grading_summary carries total_score; emitting it first lets "
            "the model announce a total and then grade toward it.",
        )

    def test_confidence_is_judged_last(self):
        self.assertEqual(
            self.order[-1],
            "grading_confidence",
            "how sure the grader is can only be assessed once the grading "
            "it refers to exists",
        )

    def test_required_list_matches_properties_order(self):
        self.assertEqual(
            self.order,
            list(GRADING_SINGLE_PASS_RESPONSE_SCHEMA["schema"]["required"]),
        )


# ── 2. level_decision normalization + the borderline trigger ──────────────


class LevelDecisionNormalizationTest(SimpleTestCase):
    """
    _finalize_grading_result is the one chokepoint every evaluation
    passes through, so it is where level_decision is made trustworthy.
    """

    QUESTIONS = [
        {
            "question_number": 1,
            "points": 10,
            "rubric": [
                {"level": "excellent", "points": 10},
                {"level": "good", "points": 7},
                {"level": "fair", "points": 4},
                {"level": "poor", "points": 1},
            ],
        }
    ]

    def _finalize(self, evaluation):
        result = AIProcessor()._finalize_grading_result([evaluation], self.QUESTIONS)
        return result["question_evaluations"][0]

    def test_borderline_is_preserved(self):
        out = self._finalize(
            {"question_number": 1, "score_awarded": 7, "level_decision": "borderline"}
        )
        self.assertEqual(out["level_decision"], "borderline")

    def test_case_and_padding_are_tolerated(self):
        out = self._finalize(
            {"question_number": 1, "score_awarded": 7, "level_decision": " BorderLine "}
        )
        self.assertEqual(out["level_decision"], "borderline")

    def test_missing_key_defaults_to_clear(self):
        # The safe direction: a model that omits the key must not route
        # every question to a billed second grader.
        out = self._finalize({"question_number": 1, "score_awarded": 7})
        self.assertEqual(out["level_decision"], "clear")

    def test_unrecognized_values_default_to_clear(self):
        for junk in (None, "", "maybe", "BORDERLINE-ISH", 42, [], {"a": 1}):
            with self.subTest(junk=junk):
                out = self._finalize(
                    {
                        "question_number": 1,
                        "score_awarded": 7,
                        "level_decision": junk,
                    }
                )
                self.assertEqual(out["level_decision"], "clear")

    def test_deterministic_evaluations_are_always_clear(self):
        evaluation = build_objective_evaluation(
            {
                "question_number": 1,
                "question_type": "OBJECTIVE",
                "points": 3,
                "model_answer": "A) Paris",
            },
            "A",
            CORRECT,
        )
        self.assertEqual(evaluation["level_decision"], "clear")


class BorderlineSecondOpinionTriggerTest(SimpleTestCase):
    """
    A borderline call is where an independent reader earns its cost: on a
    discrete ladder one rung is the gap between two adjacent grades.
    """

    QUESTIONS = [
        {"question_number": 1, "points": 5},
        {"question_number": 2, "points": 5},
    ]

    def _select(self, evaluations, **overrides):
        kwargs = {
            "key_fn": _key,
            # Deliberately inert: confidence high, no question rich
            # enough to trip high_stakes, no QA sampling. Anything that
            # fires is the borderline trigger and nothing else.
            "min_confidence": 80,
            "high_points_threshold": 15,
            "sample_rate": 0,
        }
        kwargs.update(overrides)
        return select_second_opinion_targets(
            {"grading_confidence": 95, "question_evaluations": evaluations},
            self.QUESTIONS,
            **kwargs,
        )

    def test_borderline_question_is_selected(self):
        selected = self._select(
            [
                {"question_number": 1, "level_decision": "borderline"},
                {"question_number": 2, "level_decision": "clear"},
            ]
        )
        self.assertEqual(list(selected), [_key(1)])
        self.assertIn(REASON_BORDERLINE_LEVEL, selected[_key(1)])

    def test_clear_questions_alone_select_nothing(self):
        self.assertEqual(
            self._select(
                [
                    {"question_number": 1, "level_decision": "clear"},
                    {"question_number": 2, "level_decision": "clear"},
                ]
            ),
            {},
        )

    def test_missing_level_decision_does_not_trigger(self):
        # Guards the spend: an older or degraded response that omits the
        # field must not escalate every question.
        self.assertEqual(
            self._select([{"question_number": 1}, {"question_number": 2}]), {}
        )

    def test_kill_switch_disables_the_trigger(self):
        self.assertEqual(
            self._select(
                [{"question_number": 1, "level_decision": "borderline"}],
                borderline_enabled=False,
            ),
            {},
        )

    def test_other_triggers_still_fire_when_borderline_is_off(self):
        selected = self._select(
            [{"question_number": 1, "level_decision": "borderline"}],
            borderline_enabled=False,
            high_points_threshold=5,
        )
        self.assertEqual(selected[_key(1)], [REASON_HIGH_STAKES])

    def test_reasons_accumulate_rather_than_replace(self):
        selected = self._select(
            [{"question_number": 1, "level_decision": "borderline"}],
            high_points_threshold=5,
        )
        self.assertEqual(
            sorted(selected[_key(1)]),
            sorted([REASON_BORDERLINE_LEVEL, REASON_HIGH_STAKES]),
        )

    def test_not_attempted_borderline_is_still_ineligible(self):
        # Nothing was awarded, so there is no grade to dispute — the
        # existing eligibility rules must win over the new trigger.
        self.assertEqual(
            self._select(
                [
                    {
                        "question_number": 1,
                        "level_decision": "borderline",
                        "level_achieved": "not_attempted",
                    }
                ]
            ),
            {},
        )

    def test_deterministic_borderline_is_still_ineligible(self):
        self.assertEqual(
            self._select(
                [
                    {
                        "question_number": 1,
                        "level_decision": "borderline",
                        "graded_by": "deterministic",
                    }
                ]
            ),
            {},
        )


# ── 3. LaTeX whitespace in deterministic objective matching ───────────────


class CollapseMathWhitespaceTest(SimpleTestCase):
    def test_whitespace_inside_math_is_removed(self):
        self.assertEqual(
            collapse_math_whitespace(r"$x^2 \ln(x)$"),
            r"$x^2\ln(x)$",
        )

    def test_prose_whitespace_is_untouched(self):
        # The safety argument for scoping this to math spans: "not able"
        # and "notable" are genuinely different answers.
        self.assertEqual(collapse_math_whitespace("not able"), "not able")

    def test_only_the_math_span_is_affected(self):
        self.assertEqual(
            collapse_math_whitespace(r"the value of $x^2 \ln(x)$ is big"),
            r"the value of $x^2\ln(x)$ is big",
        )

    def test_display_and_paren_delimiters_are_handled(self):
        self.assertEqual(collapse_math_whitespace(r"$$a + b$$"), "$$a+b$$")
        self.assertEqual(collapse_math_whitespace(r"\(a + b\)"), r"\(a+b\)")
        self.assertEqual(collapse_math_whitespace(r"\[a + b\]"), r"\[a+b\]")

    def test_empty_and_delimiterless_input(self):
        self.assertEqual(collapse_math_whitespace(""), "")
        self.assertEqual(collapse_math_whitespace("plain answer"), "plain answer")

    def test_unbalanced_delimiter_is_left_alone(self):
        self.assertEqual(collapse_math_whitespace("$a + b"), "$a + b")


class MathAwareObjectiveMatchingTest(SimpleTestCase):
    QUESTION = {
        "question_type": "OBJECTIVE",
        "points": 3,
        "options": [
            r"A) $3x^2 \ln(x) + x^2$",
            r"B) $3x^2 \ln(x)$",
            r"C) $3x^2 + \frac{1}{x}$",
            r"D) $x^2 \ln(x) + 3x^2$",
        ],
        "model_answer": r"A) $3x^2 \ln(x) + x^2$",
    }

    def test_answer_retyped_without_the_latex_space_is_claimed(self):
        # The case that used to cost a billed LLM call to reach an answer
        # tier 0 already had.
        self.assertEqual(
            match_objective_answer(self.QUESTION, r"<p>A) $3x^2\ln(x) + x^2$</p>"),
            CORRECT,
        )

    def test_wrong_option_retyped_without_the_space_is_still_wrong(self):
        self.assertEqual(
            match_objective_answer(self.QUESTION, r"<p>D) $x^2\ln(x) + 3x^2$</p>"),
            INCORRECT,
        )

    def test_text_only_answer_with_differing_spacing(self):
        self.assertEqual(
            match_objective_answer(self.QUESTION, r"<p>$3x^2\ln(x)+x^2$</p>"),
            CORRECT,
        )

    def test_letter_text_conflict_still_defers(self):
        # The core safety invariant is unchanged: relaxing whitespace must
        # not turn a contradictory answer into a confident claim.
        self.assertEqual(
            match_objective_answer(self.QUESTION, r"<p>A) $3x^2 \ln(x)$</p>"),
            AMBIGUOUS,
        )

    def test_mathematically_equivalent_rewriting_still_defers(self):
        # Reordered terms are equal in maths but not as strings; judging
        # that is the LLM's job, and tier 0 must not guess.
        self.assertEqual(
            match_objective_answer(self.QUESTION, r"<p>$x^2 + 3x^2\ln x$</p>"),
            AMBIGUOUS,
        )

    def test_options_colliding_under_collapse_are_not_matchable_by_it(self):
        question = {
            "question_type": "OBJECTIVE",
            "points": 1,
            "options": [r"$a b$", r"$ab$", r"$c$"],
            "model_answer": r"$c$",
        }
        index = build_option_index(question["options"])
        self.assertNotIn(
            "$ab$",
            index.by_math_text,
            "two options that collapse to the same string are "
            "indistinguishable under this relaxation, so the relaxation "
            "must not be what decides between them",
        )
        # The exact index still resolves each of them correctly.
        self.assertEqual(match_objective_answer(question, r"$a b$"), INCORRECT)
        self.assertEqual(match_objective_answer(question, r"$c$"), CORRECT)

    def test_prose_options_remain_whitespace_sensitive(self):
        question = {
            "question_type": "OBJECTIVE",
            "points": 1,
            "options": ["notable", "not able", "neither"],
            "model_answer": "notable",
        }
        self.assertEqual(match_objective_answer(question, "not able"), INCORRECT)
        self.assertEqual(match_objective_answer(question, "notable"), CORRECT)

    def test_normalization_still_runs_before_collapsing(self):
        # HTML tags and entities are stripped first, so a subscripted or
        # escaped option still reaches the math comparison.
        self.assertEqual(
            collapse_math_whitespace(normalize_text(r"<p>$x^2 &gt; y$</p>")),
            "$x^2>y$",
        )
