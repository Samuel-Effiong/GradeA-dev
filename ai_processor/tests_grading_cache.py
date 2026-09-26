"""
Coverage for cross-student consistency (Fix 5): ai_processor/grading_cache.py
and its wiring into AIProcessor._partition_cached / _store_cache_evaluations.

Locked here:
- Two submissions with the EXACT same question content and EXACT same
  answer text produce the exact same evaluation, and the second grading
  makes no AI call at all — consistency by construction, not by luck of
  temperature 0.
- A different answer, or an edited rubric/model_answer, is a fresh grade
  (a cache key that changes automatically, nothing to invalidate by
  hand).
- The kill switch (GRADING_ANSWER_CACHE_ENABLED=False) restores today's
  behavior exactly: every submission is graded fresh.
- A question whose evaluation drew a second-opinion disagreement is
  never written to the cache — reusing a disputed grade for a future
  student would silently spread an unresolved disagreement.
- Cache-served evaluations are excluded from second-opinion eligibility,
  the same way deterministic ones are (ai_processor/second_opinion.py).

Run with:
    python manage.py test ai_processor.tests_grading_cache
"""

import json
from unittest.mock import MagicMock, patch

from django.core.cache import cache as django_cache
from django.test import SimpleTestCase, override_settings

from ai_processor import grading_cache
from ai_processor.services import AIProcessor


def _ai_response(payload, model="test-model"):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(payload)
    response.usage.total_tokens = 100
    response.model = model
    return response


def _essay(number=1, points=10, model_answer="A model essay."):
    return {
        "question_number": number,
        "question_text": f"Essay question {number}?",
        "question_type": "ESSAY",
        "points": points,
        "options": [],
        "rubric": [
            {"level": "excellent", "description": "Great", "points": points},
            {"level": "good", "description": "Good", "points": 8},
            {"level": "poor", "description": "Poor", "points": 0},
        ],
        "model_answer": model_answer,
    }


def _answer(number, text):
    return {"question_number": number, "answer_html": text}


def _evaluation(number=1, score=8, quotes=None):
    return {
        "question_number": number,
        "score_awarded": score,
        "max_points": 10,
        "evidence_quotes": quotes if quotes is not None else [f"Essay {number}"],
    }


def _payload(evaluations):
    return {"question_evaluations": evaluations}


# ── Unit tests for the cache module itself ─────────────────────────────────


class GradingCacheUnitTest(SimpleTestCase):
    def setUp(self):
        django_cache.clear()
        self.addCleanup(django_cache.clear)

    def test_roundtrip_hit(self):
        question = _essay(1)
        grading_cache.store_evaluation(
            question, "<p>my answer</p>", _evaluation(1), model_name="m"
        )
        hit = grading_cache.get_cached_evaluation(
            question, "<p>my answer</p>", model_name="m"
        )
        self.assertIsNotNone(hit)
        self.assertTrue(hit["from_cache"])
        self.assertEqual(hit["score_awarded"], 8)

    def test_miss_on_different_answer(self):
        question = _essay(1)
        grading_cache.store_evaluation(
            question, "<p>answer A</p>", _evaluation(1), model_name="m"
        )
        hit = grading_cache.get_cached_evaluation(
            question, "<p>answer B</p>", model_name="m"
        )
        self.assertIsNone(hit)

    def test_miss_on_different_model_answer(self):
        # A rubric/model_answer edit must change the key automatically —
        # there is nothing to invalidate by hand.
        grading_cache.store_evaluation(
            _essay(1, model_answer="Old model answer."),
            "<p>my answer</p>",
            _evaluation(1),
            model_name="m",
        )
        hit = grading_cache.get_cached_evaluation(
            _essay(1, model_answer="New model answer."),
            "<p>my answer</p>",
            model_name="m",
        )
        self.assertIsNone(hit)

    def test_miss_on_different_model_name(self):
        grading_cache.store_evaluation(
            _essay(1), "<p>my answer</p>", _evaluation(1), model_name="model-a"
        )
        hit = grading_cache.get_cached_evaluation(
            _essay(1), "<p>my answer</p>", model_name="model-b"
        )
        self.assertIsNone(hit)

    def test_answer_whitespace_edges_are_ignored(self):
        grading_cache.store_evaluation(
            _essay(1), "  <p>my answer</p>  ", _evaluation(1), model_name="m"
        )
        hit = grading_cache.get_cached_evaluation(
            _essay(1), "<p>my answer</p>", model_name="m"
        )
        self.assertIsNotNone(hit)

    @override_settings(GRADING_ANSWER_CACHE_ENABLED=False)
    def test_disabled_never_stores_or_hits(self):
        grading_cache.store_evaluation(
            _essay(1), "<p>my answer</p>", _evaluation(1), model_name="m"
        )
        hit = grading_cache.get_cached_evaluation(
            _essay(1), "<p>my answer</p>", model_name="m"
        )
        self.assertIsNone(hit)

    @override_settings(GRADING_ANSWER_CACHE_TTL_SECONDS=999)
    def test_store_uses_the_configured_ttl(self):
        with patch("ai_processor.grading_cache.cache") as mock_cache:
            grading_cache.store_evaluation(
                _essay(1), "<p>my answer</p>", _evaluation(1), model_name="m"
            )
            self.assertEqual(mock_cache.set.call_args.kwargs["timeout"], 999)

    def test_backend_error_degrades_to_a_miss_not_a_crash(self):
        with patch(
            "ai_processor.grading_cache.cache.get", side_effect=Exception("boom")
        ):
            hit = grading_cache.get_cached_evaluation(
                _essay(1), "<p>my answer</p>", model_name="m"
            )
        self.assertIsNone(hit)

    def test_backend_error_on_write_does_not_raise(self):
        with patch(
            "ai_processor.grading_cache.cache.set", side_effect=Exception("boom")
        ):
            grading_cache.store_evaluation(
                _essay(1), "<p>my answer</p>", _evaluation(1), model_name="m"
            )  # must not raise


# ── Pipeline integration ─────────────────────────────────────────────────


@override_settings(GRADING_SECOND_OPINION_ENABLED=False)
class PipelineCacheTest(SimpleTestCase):
    def setUp(self):
        self.processor = AIProcessor()
        django_cache.clear()
        self.addCleanup(django_cache.clear)

    @patch.object(AIProcessor, "execute_graded_task")
    def test_second_identical_submission_makes_no_ai_call(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1, score=8)]))
        question = _essay(1)
        answer = [_answer(1, "<p>Essay 1 photosynthesis answer.</p>")]

        first = self.processor._grade_student_submission_impl(
            user=MagicMock(), rubric_json=[question], answer_json=answer
        )
        second = self.processor._grade_student_submission_impl(
            user=MagicMock(), rubric_json=[question], answer_json=answer
        )

        self.assertEqual(mock_execute.call_count, 1)
        self.assertEqual(
            first["grading_summary"]["total_score"],
            second["grading_summary"]["total_score"],
        )
        self.assertTrue(second["question_evaluations"][0].get("from_cache"))
        self.assertNotIn("from_cache", first["question_evaluations"][0])

    @patch.object(AIProcessor, "execute_graded_task")
    def test_different_answer_text_is_a_fresh_call(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1, score=8)]))
        question = _essay(1)

        self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=[question],
            answer_json=[_answer(1, "<p>Essay 1 answer A</p>")],
        )
        self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=[question],
            answer_json=[_answer(1, "<p>Essay 1 answer B</p>")],
        )
        self.assertEqual(mock_execute.call_count, 2)

    @patch.object(AIProcessor, "execute_graded_task")
    def test_edited_rubric_forces_a_fresh_grade(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1, score=8)]))
        answer = [_answer(1, "<p>Essay 1 same answer text.</p>")]

        self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=[_essay(1, model_answer="Version 1")],
            answer_json=answer,
        )
        self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=[_essay(1, model_answer="Version 2 — teacher edited")],
            answer_json=answer,
        )
        self.assertEqual(mock_execute.call_count, 2)

    @override_settings(GRADING_ANSWER_CACHE_ENABLED=False)
    @patch.object(AIProcessor, "execute_graded_task")
    def test_kill_switch_restores_a_fresh_call_every_time(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1, score=8)]))
        question = _essay(1)
        answer = [_answer(1, "<p>Essay 1 same answer.</p>")]

        self.processor._grade_student_submission_impl(
            user=MagicMock(), rubric_json=[question], answer_json=answer
        )
        self.processor._grade_student_submission_impl(
            user=MagicMock(), rubric_json=[question], answer_json=answer
        )
        self.assertEqual(mock_execute.call_count, 2)

    @patch.object(AIProcessor, "execute_graded_task")
    def test_all_questions_cached_makes_zero_ai_calls(self, mock_execute):
        # A submission whose every question is a cache hit must not
        # trigger even the single-pass call — mirrors the existing
        # zero-AI-call fast path for an all-deterministic submission.
        mock_execute.return_value = _ai_response(_payload([_evaluation(1, score=8)]))
        question = _essay(1)
        answer = [_answer(1, "<p>Essay 1 answer.</p>")]

        self.processor._grade_student_submission_impl(
            user=MagicMock(), rubric_json=[question], answer_json=answer
        )
        mock_execute.reset_mock()
        result = self.processor._grade_student_submission_impl(
            user=MagicMock(), rubric_json=[question], answer_json=answer
        )
        self.assertEqual(mock_execute.call_count, 0)
        self.assertEqual(result["grading_summary"]["total_score"], 8)


@override_settings(GRADING_ANSWER_CACHE_ENABLED=False)
class SecondOpinionCacheInteractionTest(SimpleTestCase):
    """
    Uses GRADING_ANSWER_CACHE_ENABLED=False at the class level and
    re-enables it per test where needed, matching the pattern used
    elsewhere in this suite for isolating one feature from another.
    """

    A_MODEL = "grader-a-model"
    B_MODEL = "grader-b-model"

    def setUp(self):
        self.processor = AIProcessor()
        django_cache.clear()
        self.addCleanup(django_cache.clear)

    def _second_opinion_settings(self, **overrides):
        settings = {
            "GRADING_SECOND_OPINION_ENABLED": True,
            "GRADING_SECOND_OPINION_MODELS": [self.B_MODEL],
            "GRADING_SECOND_OPINION_MIN_CONFIDENCE": 0,
            # 1, not some high bar: the fixture question is worth 20
            # points, and the trigger is points >= this threshold — so
            # this must be LOW to force the trigger, not high.
            "GRADING_SECOND_OPINION_HIGH_POINTS": 1,
            "GRADING_SECOND_OPINION_SAMPLE_RATE": 0,
            "GRADING_ANSWER_CACHE_ENABLED": True,
        }
        settings.update(overrides)
        return settings

    @patch.object(AIProcessor, "execute_graded_task")
    def test_cached_evaluation_is_not_selected_for_a_fresh_second_opinion(
        self, mock_execute
    ):
        question = _essay(1, points=20)
        answer = [_answer(1, "<p>Essay 1 answer worth a full second read.</p>")]

        def respond(**kwargs):
            model_name = kwargs.get("override_model") or self.A_MODEL
            return _ai_response(_payload([_evaluation(1, score=15)]), model=model_name)

        mock_execute.side_effect = respond
        with override_settings(**self._second_opinion_settings()):
            self.processor._grade_student_submission_impl(
                user=MagicMock(), rubric_json=[question], answer_json=answer
            )
            call_count_after_first = mock_execute.call_count
            self.assertGreaterEqual(call_count_after_first, 2)  # A + B

            mock_execute.reset_mock()
            second = self.processor._grade_student_submission_impl(
                user=MagicMock(), rubric_json=[question], answer_json=answer
            )
        # Fully served from cache: no A call, no B call.
        self.assertEqual(mock_execute.call_count, 0)
        self.assertTrue(second["question_evaluations"][0].get("from_cache"))

    @patch.object(AIProcessor, "execute_graded_task")
    def test_disagreed_evaluation_is_never_cached(self, mock_execute):
        question = _essay(1, points=20)
        answer = [_answer(1, "<p>Essay 1 genuinely borderline answer.</p>")]

        def respond(**kwargs):
            if kwargs.get("override_model"):
                return _ai_response(
                    _payload([_evaluation(1, score=0)]), model=self.B_MODEL
                )
            return _ai_response(
                _payload([_evaluation(1, score=20)]), model=self.A_MODEL
            )

        mock_execute.side_effect = respond
        with override_settings(**self._second_opinion_settings()):
            first = self.processor._grade_student_submission_impl(
                user=MagicMock(), rubric_json=[question], answer_json=answer
            )
            self.assertTrue((first.get("second_opinion") or {}).get("disagreements"))

            mock_execute.reset_mock()
            self.processor._grade_student_submission_impl(
                user=MagicMock(), rubric_json=[question], answer_json=answer
            )
        # A disputed grade must never be silently replayed onto another
        # student — the second submission must trigger a fresh A call
        # (and, since it's the same borderline case, a fresh B call too).
        self.assertGreaterEqual(mock_execute.call_count, 1)
