"""
Regression coverage for the grading-pipeline hardening that sits alongside
the H1/H2/C2/C3 fixes:

- The answer<->question join in _pair_question_with_answers matches by
  NORMALIZED question_number. Before this fix the join was an exact-type
  dict lookup, so an answer whose question_number came back as the string
  "3" never matched the rubric's int 3 — the student silently scored 0 on
  a question they answered. A mixed int/str question_number set also
  crashed the sort with TypeError (swallowed and retried 3x, burning
  credits, before failing the run).

- safe_sort_key orders mixed int/str collections deterministically
  instead of raising.

- execute_graded_task restricts OpenRouter fallback models for grading
  (task_type="grade_assignment") to GRADING_FALLBACK_MODELS — never a
  nano-tier model — so two students in one class can't be graded by
  models of visibly different capability depending on transient routing.
  All other task types keep the default fallback chain.

Run with:
    python manage.py test ai_processor.tests_grading_hardening
"""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from ai_processor.services import (
    DEFAULT_FALLBACK_MODELS,
    GRADING_FALLBACK_MODELS,
    AIProcessor,
)
from ai_processor.tools import safe_sort_key
from users.models import CustomUser, UserTypes


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
)
class QuestionNumberJoinTest(SimpleTestCase):
    def setUp(self):
        self.processor = AIProcessor()

    def test_string_answer_number_matches_int_rubric_number(self):
        questions = [
            {"question_number": 1, "points": 5},
            {"question_number": 2, "points": 5},
        ]
        answers = [
            {"question_number": "1", "answer_html": "answer one"},
            {"question_number": 2, "answer_html": "answer two"},
        ]

        pairs = self.processor._pair_question_with_answers(
            json.dumps(questions), json.dumps(answers)
        )

        # The load-bearing assertion: "1" (string) matched question 1 (int)
        # instead of being replaced by the not-found placeholder.
        self.assertEqual(pairs[0]["answer"]["answer_html"], "answer one")
        self.assertEqual(pairs[1]["answer"]["answer_html"], "answer two")

    def test_int_answer_number_matches_string_rubric_number(self):
        questions = [{"question_number": "1", "points": 5}]
        answers = [{"question_number": 1, "answer_html": "answer one"}]

        pairs = self.processor._pair_question_with_answers(
            json.dumps(questions), json.dumps(answers)
        )

        self.assertEqual(pairs[0]["answer"]["answer_html"], "answer one")

    def test_genuinely_missing_answer_still_gets_placeholder(self):
        questions = [
            {"question_number": 1, "points": 5},
            {"question_number": 2, "points": 5},
        ]
        answers = [{"question_number": 1, "answer_html": "only one"}]

        pairs = self.processor._pair_question_with_answers(
            json.dumps(questions), json.dumps(answers)
        )

        self.assertEqual(pairs[1]["answer"]["answer_html"], "")
        self.assertIn("No answer found", pairs[1]["answer"]["notes"])

    def test_mixed_int_and_string_question_numbers_do_not_crash_the_sort(self):
        questions = [
            {"question_number": 2, "points": 5},
            {"question_number": "1a", "points": 5},
            {"question_number": 1, "points": 5},
        ]

        # Before the fix this raised TypeError ('<' not supported between
        # int and str) inside the sort.
        pairs = self.processor._pair_question_with_answers(
            json.dumps(questions), json.dumps([])
        )

        ordered = [p["question"]["question_number"] for p in pairs]
        # Numeric first in numeric order, then non-numeric.
        self.assertEqual(ordered, [1, 2, "1a"])

    def test_non_dict_answer_entries_are_ignored_not_crashed(self):
        questions = [{"question_number": 1, "points": 5}]
        answers = [None, "junk", {"question_number": 1, "answer_html": "real"}]

        pairs = self.processor._pair_question_with_answers(
            json.dumps(questions), json.dumps(answers)
        )

        self.assertEqual(pairs[0]["answer"]["answer_html"], "real")


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
)
class SafeSortKeyTest(SimpleTestCase):
    def test_mixed_collection_sorts_without_typeerror(self):
        values = [3, "2a", "10", 1, "b"]
        ordered = sorted(values, key=safe_sort_key)
        self.assertEqual(ordered, [1, 3, "10", "2a", "b"])

    def test_numeric_strings_sort_numerically_not_lexically(self):
        self.assertEqual(sorted(["10", "9", "2"], key=safe_sort_key), ["2", "9", "10"])


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
)
class GradingFallbackModelRestrictionTest(TestCase):
    """
    Proves execute_graded_task hands __ai_model the restricted fallback
    list for grading calls and the default list otherwise. Uses a
    SUPER_ADMIN caller so no billing fixtures are needed — the model
    routing decision is made before, and independently of, the billing
    branch.
    """

    def setUp(self):
        self.processor = AIProcessor()
        self.super_admin = CustomUser.objects.create_user(
            email="fallback-admin@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
        )

    def _captured_sub_models(self, task_type):
        captured = {}

        def fake_model(*args, **kwargs):
            captured["sub_models"] = kwargs.get("sub_models")
            response = MagicMock()
            response.choices = [MagicMock()]
            response.choices[0].message.content = "{}"
            response.usage.total_tokens = 10
            return response

        with patch.object(
            AIProcessor, "_AIProcessor__ai_model", side_effect=fake_model
        ):
            self.processor.execute_graded_task(
                user=self.super_admin,
                feature="Grading Assignment",
                task_type=task_type,
                system_prompt="system",
                user_prompt="user",
            )
        return captured["sub_models"]

    def test_grading_calls_use_restricted_fallbacks(self):
        self.assertEqual(
            self._captured_sub_models("grade_assignment"), GRADING_FALLBACK_MODELS
        )

    def test_grading_fallbacks_never_include_a_nano_model(self):
        for model in GRADING_FALLBACK_MODELS:
            self.assertNotIn("nano", model)
        # And the restriction is real — it's a strict subset of the default.
        self.assertTrue(set(GRADING_FALLBACK_MODELS) < set(DEFAULT_FALLBACK_MODELS))

    def test_non_grading_calls_keep_default_fallbacks(self):
        # None means __ai_model falls through to DEFAULT_FALLBACK_MODELS.
        self.assertIsNone(self._captured_sub_models("generate_assignment"))

    def test_extraction_calls_use_restricted_fallbacks(self):
        # Reading handwriting - student answers or scanned assignment
        # questions - is just as sensitive to a silent nano-tier downgrade
        # as grading is, so both extraction task types get the same
        # restricted fallback list as grading.
        for task_type in ("extract_answer", "extract_assignment"):
            self.assertEqual(
                self._captured_sub_models(task_type), GRADING_FALLBACK_MODELS
            )
