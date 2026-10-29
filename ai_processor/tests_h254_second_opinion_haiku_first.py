"""H-254: the second grader tries claude-haiku-5.5 first, then deepseek.

A second-opinion request carries the question images of the questions it
re-grades, and deepseek/deepseek-v4-pro cannot read images: with deepseek
first, grader B's call failed on every such paper, and the broad handler let
grader A's grade stand with no second opinion and no review flag. Haiku reads
images and is cheaper in list price, so it goes first (the owner's decision,
2026-10-24). On the normal path (grader A = grok) grader B is now haiku, with
images or without. The stated limit: when grader A itself fell back to haiku
(an image call while grok is down), grader B is deepseek, which still fails
on images.

The candidate list is the `default=` written in settings.py, read from its
syntax tree, so the tests do not depend on the environment the run has.
The model names are written out here, not read from the code.

Run with:
    python manage.py test ai_processor.tests_h254_second_opinion_haiku_first
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from ai_processor.second_opinion import pick_second_model
from ai_processor.services import AIProcessor
from ai_processor.tests_h205_image_fallback import _settings_default
from users.models import CustomUser, UserTypes

HAIKU = "anthropic/claude-haiku-5.5"
DEEPSEEK = "deepseek/deepseek-v4-pro"
GROK = "x-ai/grok-4.3"
DEFAULT_CANDIDATES = _settings_default("GRADING_SECOND_OPINION_MODELS")
ANSWER_TEXT = "The diagram shows a cell dividing into two."


class TheDefaultOrderTest(SimpleTestCase):
    def test_the_default_candidates_are_haiku_then_deepseek(self):
        self.assertEqual(DEFAULT_CANDIDATES, [HAIKU, DEEPSEEK])


class TheChoiceFromTheDefaultTest(SimpleTestCase):
    def test_grader_a_on_grok_gives_haiku(self):
        self.assertEqual(pick_second_model(GROK, DEFAULT_CANDIDATES), HAIKU)

    def test_grader_a_on_haiku_gives_deepseek(self):
        self.assertEqual(pick_second_model(HAIKU, DEFAULT_CANDIDATES), DEEPSEEK)

    def test_grader_a_on_deepseek_gives_haiku(self):
        self.assertEqual(pick_second_model(DEEPSEEK, DEFAULT_CANDIDATES), HAIKU)


def _question(with_image):
    question = {
        "question_number": 1,
        "question_text": "What does the diagram show?",
        "question_type": "ESSAY",
        "points": 10,
        "options": [],
        "rubric": [
            {"level": "excellent", "description": "Full", "points": 10},
            {"level": "good", "description": "Most", "points": 8},
            {"level": "poor", "description": "None", "points": 0},
        ],
        "model_answer": "A cell dividing.",
    }
    if with_image:
        question["question_image"] = "https://example.com/q1.png"
    return question


def _evaluation():
    return {
        "question_number": 1,
        "score_awarded": 8,
        "max_points": 10,
        "level_achieved": "good",
        "evaluation_rationale": "Names the division.",
        "evidence_quotes": [ANSWER_TEXT],
    }


def _reply(payload, model):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(payload)),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(total_tokens=10),
        model=model,
    )


def _has_image(prompt):
    return isinstance(prompt, list) and any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in prompt
    )


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=True,
    GRADING_SECOND_OPINION_MODELS=DEFAULT_CANDIDATES,
    GRADING_SECOND_OPINION_MIN_CONFIDENCE=80,
    GRADING_SECOND_OPINION_SAMPLE_RATE=0,
    GRADING_SECOND_OPINION_SUBJECTIVE_TYPES=[],
    GRADING_ANSWER_CACHE_ENABLED=False,
)
class TheRealSecondOpinionPathTest(TestCase):
    """Through _grade_student_submission_impl and the real second-opinion
    path. Only __ai_model is replaced: the first call is grader A, whose
    reply names the model that answered and a low confidence, so the
    question is re-graded; every later call is grader B."""

    def setUp(self):
        self.processor = AIProcessor()
        self.super_admin = CustomUser.objects.create_user(
            email="h254-admin@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=True,
        )

    def grade(self, with_image, grader_a_model=GROK):
        calls = []

        def fake_model(*args, **kwargs):
            calls.append(
                {
                    "user_prompt": args[1],
                    "override_model": kwargs.get("override_model"),
                    "sub_models": kwargs.get("sub_models"),
                }
            )
            if len(calls) == 1:
                payload = {
                    "question_evaluations": [_evaluation()],
                    "grading_summary": {
                        "total_score": 0,
                        "max_total_points": 0,
                        "percentage": 0,
                    },
                    "grading_confidence": 50,
                    "overall_performance_analysis": "ok",
                    "recommendations": [],
                }
                return _reply(payload, grader_a_model)
            payload = {"question_evaluations": [_evaluation()]}
            return _reply(payload, kwargs.get("override_model") or "unnamed/b")

        with patch.object(
            AIProcessor, "_AIProcessor__ai_model", side_effect=fake_model
        ):
            result = self.processor._grade_student_submission_impl(
                user=self.super_admin,
                rubric_json=[_question(with_image)],
                answer_json=[
                    {"question_number": 1, "answer_html": f"<p>{ANSWER_TEXT}</p>"}
                ],
            )
        return calls, result

    def test_a_question_with_an_image_is_re_graded_by_haiku(self):
        calls, result = self.grade(with_image=True)
        self.assertEqual(len(calls), 2)
        grader_b = calls[1]
        self.assertTrue(_has_image(grader_b["user_prompt"]))
        self.assertEqual(grader_b["override_model"], HAIKU)
        self.assertEqual(grader_b["sub_models"], [HAIKU])
        self.assertEqual(result["second_opinion"]["model"], HAIKU)

    def test_a_question_without_an_image_is_re_graded_by_haiku_too(self):
        calls, result = self.grade(with_image=False)
        self.assertEqual(len(calls), 2)
        grader_b = calls[1]
        self.assertFalse(_has_image(grader_b["user_prompt"]))
        self.assertEqual(grader_b["override_model"], HAIKU)
        self.assertEqual(grader_b["sub_models"], [HAIKU])
        self.assertEqual(result["second_opinion"]["model"], HAIKU)

    def test_when_grader_a_fell_back_to_haiku_grader_b_is_deepseek(self):
        """The stated limit: an image call while grok is down. Grader B is
        then deepseek, which still cannot read the images."""
        calls, result = self.grade(with_image=True, grader_a_model=HAIKU)
        self.assertEqual(len(calls), 2)
        grader_b = calls[1]
        self.assertTrue(_has_image(grader_b["user_prompt"]))
        self.assertEqual(grader_b["override_model"], DEEPSEEK)
        self.assertEqual(grader_b["sub_models"], [DEEPSEEK])
        self.assertEqual(result["second_opinion"]["model"], DEEPSEEK)
