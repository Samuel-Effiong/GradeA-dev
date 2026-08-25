"""
Tests for the assignment-extraction benchmark's dataset and scorer.

An accuracy benchmark is an instrument, and an instrument nobody has
calibrated is just a number generator. Two failure modes matter more than
the rest, and most of this file is aimed at them:

  * a scorer that is too LENIENT reports a healthy extraction while
    teacher content is being silently dropped — the exact thing the
    benchmark was built to detect;
  * a scorer that is too STRICT fails on prose rewording that nobody
    would ever act on, and a benchmark people learn to ignore is worse
    than no benchmark.

The dataset validates itself (a fixture with a broken rubric produces
failures that belong to the fixture, and the natural response to those is
to relax the assertion — which is how an instrument quietly stops
measuring).

Run with:
    python manage.py test ai_processor.tests_extraction_benchmark
"""

from django.test import SimpleTestCase

from ai_processor.benchmark.extraction_dataset import (
    CUSTOM_LEVEL_NAMES,
    EXTRACTION_CASES,
    EXTRACTION_CASES_BY_KEY,
    MIXED_HYBRID,
    NO_RUBRIC,
    SIX_LEVEL_RUBRIC,
    SIX_OPTION_MCQ,
    iter_extraction_dataset_errors,
)
from ai_processor.benchmark.extraction_scoring import (
    METRICS,
    score_case,
    score_question,
    score_run,
)


def generated_rubric(points):
    """What the prompt should invent when a question arrives with none."""
    return [
        {"level": level, "points": value, "description": "<p>x</p>"}
        for level, value in (
            ("excellent", points),
            ("good", points * 0.6),
            ("fair", points * 0.3),
            ("poor", 0),
        )
    ]


def ideal_payload(case):
    """A flawless extraction: everything preserved, and a rubric generated
    exactly where the contract says one should be."""
    questions = []
    for question in case.questions:
        question = dict(question)
        if not question["rubric"] and question["question_type"] != "OBJECTIVE":
            question["rubric"] = generated_rubric(question["points"])
        questions.append(question)
    return {"questions": questions}


class DatasetIntegrityTest(SimpleTestCase):
    def test_dataset_validates_itself(self):
        errors = list(iter_extraction_dataset_errors())
        self.assertEqual(errors, [], "\n".join(errors))

    def test_every_case_is_reachable_by_key(self):
        for case in EXTRACTION_CASES:
            self.assertIs(EXTRACTION_CASES_BY_KEY[case.key], case)

    def test_every_case_explains_what_it_guards(self):
        for case in EXTRACTION_CASES:
            with self.subTest(case=case.key):
                self.assertGreater(len(case.guards.strip()), 40)

    def test_strict_fields_are_real_metrics(self):
        # A typo here would silently stop gating on that field.
        for case in EXTRACTION_CASES:
            for field in case.strict_fields:
                with self.subTest(case=case.key, field=field):
                    self.assertIn(field, METRICS)

    def test_total_points_matches_the_questions(self):
        self.assertEqual(MIXED_HYBRID.total_points, 12)

    def test_question_lookup_raises_for_an_unknown_number(self):
        with self.assertRaises(KeyError):
            SIX_LEVEL_RUBRIC.question(99)

    def test_the_six_level_case_really_has_six_levels(self):
        # The premise of the case. If a future edit trims it, the benchmark
        # would keep passing while measuring nothing.
        self.assertEqual(len(SIX_LEVEL_RUBRIC.question(1)["rubric"]), 6)

    def test_the_custom_names_case_uses_non_canonical_names(self):
        names = {level["level"] for level in CUSTOM_LEVEL_NAMES.question(1)["rubric"]}
        self.assertFalse(names & {"excellent", "good", "fair", "poor"})

    def test_the_six_option_case_really_has_six_options(self):
        self.assertEqual(len(SIX_OPTION_MCQ.question(1)["options"]), 6)


class DatasetValidatorTest(SimpleTestCase):
    """The validator must actually catch a broken fixture."""

    def _errors(self, case):
        from unittest.mock import patch

        with patch(
            "ai_processor.benchmark.extraction_dataset.EXTRACTION_CASES", [case]
        ):
            return list(iter_extraction_dataset_errors())

    def _mutate(self, case, **question_changes):
        from dataclasses import replace

        question = dict(case.questions[0])
        question.update(question_changes)
        return replace(case, questions=[question])

    def test_ascending_rubric_points_are_caught(self):
        rubric = [
            {"level": "a", "points": 0, "description": "<p>x</p>"},
            {"level": "b", "points": 10, "description": "<p>x</p>"},
        ]
        errors = self._errors(self._mutate(SIX_LEVEL_RUBRIC, rubric=rubric))
        self.assertTrue(any("must descend" in e for e in errors))

    def test_top_level_not_matching_question_points_is_caught(self):
        rubric = [
            {"level": "a", "points": 5, "description": "<p>x</p>"},
            {"level": "b", "points": 0, "description": "<p>x</p>"},
        ]
        errors = self._errors(self._mutate(SIX_LEVEL_RUBRIC, rubric=rubric))
        self.assertTrue(any("but the question is worth" in e for e in errors))

    def test_option_with_a_letter_label_is_caught(self):
        # The doubled-letter bug ("A. A) ...") this project already fixed.
        errors = self._errors(
            self._mutate(SIX_OPTION_MCQ, options=["A) Ribosome", "B) Lysosome"])
        )
        self.assertTrue(any("leading letter label" in e for e in errors))

    def test_objective_with_a_rubric_is_caught(self):
        rubric = [{"level": "a", "points": 5, "description": "<p>x</p>"}]
        errors = self._errors(self._mutate(SIX_OPTION_MCQ, rubric=rubric))
        self.assertTrue(any("must have an empty rubric" in e for e in errors))

    def test_open_ended_with_options_is_caught(self):
        errors = self._errors(self._mutate(SIX_LEVEL_RUBRIC, options=["a", "b"]))
        self.assertTrue(any("must have no options" in e for e in errors))

    def test_invalid_blooms_level_is_caught(self):
        errors = self._errors(self._mutate(SIX_LEVEL_RUBRIC, blooms_level="Guessing"))
        self.assertTrue(any("invalid blooms_level" in e for e in errors))

    def test_non_positive_points_are_caught(self):
        errors = self._errors(self._mutate(SIX_LEVEL_RUBRIC, points=0))
        self.assertTrue(any("positive number" in e for e in errors))


class PerfectExtractionTest(SimpleTestCase):
    def test_ideal_extraction_scores_one(self):
        run = score_run([score_case(c, ideal_payload(c)) for c in EXTRACTION_CASES])
        self.assertEqual(run["overall"], 1.0, run["rates"])
        self.assertTrue(run["passed"])
        self.assertEqual(run["cases_passed"], run["cases"])

    def test_level_name_casing_is_not_a_failure(self):
        # Extraction legitimately title-cases level names; holding it to
        # byte-equality would fail on nothing anyone would act on.
        case = CUSTOM_LEVEL_NAMES
        rubric = [
            dict(level, level=level["level"].title())
            for level in case.question(1)["rubric"]
        ]
        result = score_case(
            case, {"questions": [dict(case.question(1), rubric=rubric)]}
        )
        self.assertTrue(result["passed"])

    def test_html_reformatting_of_options_is_not_a_failure(self):
        case = SIX_OPTION_MCQ
        options = [f"<p>{o}</p>" for o in case.question(1)["options"]]
        result = score_case(
            case, {"questions": [dict(case.question(1), options=options)]}
        )
        self.assertTrue(result["passed"])


class RegressionDetectionTest(SimpleTestCase):
    """Every failure this benchmark exists to catch must actually fail."""

    def test_truncating_six_levels_to_four_fails(self):
        case = SIX_LEVEL_RUBRIC
        question = dict(case.question(1), rubric=case.question(1)["rubric"][:4])
        result = score_case(case, {"questions": [question]})
        self.assertFalse(result["passed"])
        self.assertTrue(any("rubric_levels" in f for f in result["strict_failures"]))

    def test_renaming_custom_levels_fails(self):
        case = CUSTOM_LEVEL_NAMES
        rubric = [
            dict(level, level=name)
            for level, name in zip(
                case.question(1)["rubric"],
                ["excellent", "good", "fair", "poor", "worse"],
                strict=True,
            )
        ]
        result = score_case(
            case, {"questions": [dict(case.question(1), rubric=rubric)]}
        )
        self.assertFalse(result["passed"])
        self.assertTrue(
            any("rubric_level_names" in f for f in result["strict_failures"])
        )

    def test_dropping_options_fails(self):
        case = SIX_OPTION_MCQ
        question = dict(case.question(1), options=case.question(1)["options"][:4])
        result = score_case(case, {"questions": [question]})
        self.assertFalse(result["passed"])

    def test_substituting_option_text_fails(self):
        case = SIX_OPTION_MCQ
        options = list(case.question(1)["options"])
        options[0] = "Nucleus"
        result = score_case(
            case, {"questions": [dict(case.question(1), options=options)]}
        )
        self.assertFalse(result["per_question"][0]["metrics"]["option_text"])

    def test_changing_points_fails(self):
        case = SIX_LEVEL_RUBRIC
        result = score_case(case, {"questions": [dict(case.question(1), points=99)]})
        self.assertFalse(result["passed"])

    def test_changing_question_type_fails(self):
        case = SIX_LEVEL_RUBRIC
        result = score_case(
            case, {"questions": [dict(case.question(1), question_type="ESSAY")]}
        )
        self.assertFalse(result["passed"])

    def test_dropping_a_question_fails_every_metric_for_it(self):
        # A dropped question must NOT be quietly excluded — that would let
        # an extraction returning one question of three score perfectly.
        case = MIXED_HYBRID
        result = score_case(case, {"questions": [dict(case.question(1))]})
        self.assertFalse(result["passed"])
        missing = [q for q in result["per_question"] if not q["found"]]
        self.assertEqual(len(missing), 2)
        for entry in missing:
            self.assertTrue(all(v is False for v in entry["metrics"].values()))

    def test_inventing_options_on_an_essay_fails(self):
        case = SIX_LEVEL_RUBRIC
        result = score_case(
            case, {"questions": [dict(case.question(1), options=["a", "b"])]}
        )
        self.assertFalse(result["per_question"][0]["metrics"]["option_count"])

    def test_inventing_a_rubric_on_an_objective_question_fails(self):
        case = SIX_OPTION_MCQ
        rubric = [{"level": "excellent", "points": 5, "description": "<p>x</p>"}]
        result = score_case(
            case, {"questions": [dict(case.question(1), rubric=rubric)]}
        )
        self.assertFalse(result["per_question"][0]["metrics"]["rubric_levels"])

    def test_empty_extraction_fails_everything(self):
        result = score_case(MIXED_HYBRID, {"questions": []})
        self.assertFalse(result["passed"])
        self.assertEqual(result["actual_questions"], 0)

    def test_none_payload_is_handled(self):
        result = score_case(MIXED_HYBRID, None)
        self.assertFalse(result["passed"])


class NoRubricCaseTest(SimpleTestCase):
    """The one case where inventing four levels is CORRECT."""

    def test_generating_four_levels_passes(self):
        result = score_case(NO_RUBRIC, ideal_payload(NO_RUBRIC))
        self.assertTrue(result["passed"])
        self.assertTrue(result["per_question"][0]["metrics"]["rubric_levels"])

    def test_returning_no_rubric_at_all_is_scored_as_a_miss(self):
        # Echoing the empty rubric back means the question cannot be
        # graded, so it must not score as a pass.
        result = score_case(NO_RUBRIC, {"questions": [dict(NO_RUBRIC.question(1))]})
        self.assertFalse(result["per_question"][0]["metrics"]["rubric_levels"])

    def test_it_does_not_gate_the_run(self):
        # rubric_levels is deliberately outside this case's strict_fields:
        # how many levels to invent is a judgement call, not teacher
        # content being lost.
        result = score_case(NO_RUBRIC, {"questions": [dict(NO_RUBRIC.question(1))]})
        self.assertTrue(result["passed"])


class NotApplicableMetricTest(SimpleTestCase):
    """None means 'not asked', and must never be counted as a pass."""

    def test_rubric_metrics_are_not_applicable_to_objective_questions(self):
        metrics = score_question(SIX_OPTION_MCQ.question(1), SIX_OPTION_MCQ.question(1))
        self.assertIsNone(metrics["rubric_level_names"])
        self.assertIsNone(metrics["rubric_points"])

    def test_option_text_is_not_applicable_to_essays(self):
        metrics = score_question(
            SIX_LEVEL_RUBRIC.question(1), SIX_LEVEL_RUBRIC.question(1)
        )
        self.assertIsNone(metrics["option_text"])

    def test_not_applicable_metrics_are_excluded_from_rates(self):
        # An all-MCQ run must not post a perfect rubric_level_names score
        # having never been asked to preserve a level name.
        result = score_case(SIX_OPTION_MCQ, ideal_payload(SIX_OPTION_MCQ))
        run = score_run([result])
        self.assertEqual(run["counts"]["rubric_level_names"]["total"], 0)
        self.assertIsNone(run["rates"]["rubric_level_names"])


class RunAggregationTest(SimpleTestCase):
    def test_weakest_metric_is_identified(self):
        case = SIX_LEVEL_RUBRIC
        broken = dict(case.question(1), rubric=case.question(1)["rubric"][:4])
        run = score_run([score_case(case, {"questions": [broken]})])
        self.assertEqual(run["weakest_metric"], "rubric_levels")

    def test_strict_failures_are_collected_across_cases(self):
        runs = [
            score_case(
                SIX_LEVEL_RUBRIC,
                {"questions": [dict(SIX_LEVEL_RUBRIC.question(1), points=1)]},
            ),
            score_case(SIX_OPTION_MCQ, {"questions": []}),
        ]
        run = score_run(runs)
        self.assertFalse(run["passed"])
        self.assertGreaterEqual(len(run["strict_failures"]), 2)

    def test_empty_run_does_not_crash(self):
        run = score_run([])
        self.assertEqual(run["cases"], 0)
        self.assertTrue(run["passed"])
