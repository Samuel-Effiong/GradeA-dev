"""
Integration coverage for AIProcessor._maybe_run_second_opinion — the
blind second grading pass wired into _grade_student_submission_impl.

Locked here:
- Grader B's calls carry the override_model and are BLIND: no score,
  level, or rationale from grader A appears in any B prompt (ledger #3).
- Triggered-only: a confident, unflagged, low-stakes run makes zero
  second-opinion calls; the kill switch and sample_rate=0 make the
  result byte-identical to no-second-opinion (ledger #13).
- Agreement is recorded without flagging; disagreement lands in
  result["second_opinion"]["disagreements"] with both sides (ledger #4/5).
- Grader A's scores are NEVER altered by B, whatever B says.
- Same-model candidates → skip annotation, no B calls (ledger #2).
- A failing second pass is non-fatal: A's grade survives, the error is
  annotated (ledger #6).
- B is subject to the same evidence enforcement as A.

Second opinion is explicitly enabled per-class here (legacy suites
disable it); the QA sample is pinned to 0 so triggers are deterministic.

Run with:
    python manage.py test ai_processor.tests_second_opinion_pipeline
"""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from ai_processor.services import AIProcessor

A_MODEL = "primary-model"
B_MODEL = "second-model"
A_RATIONALE = "Grader A distinctive rationale marker"
SUMMARY_MARKER = "All questions have been graded individually"

SECOND_OPINION_SETTINGS = {
    "GRADING_SECOND_OPINION_ENABLED": True,
    "GRADING_SECOND_OPINION_MODELS": [B_MODEL],
    "GRADING_SECOND_OPINION_MIN_CONFIDENCE": 80,
    "GRADING_SECOND_OPINION_HIGH_POINTS": 15,
    "GRADING_SECOND_OPINION_SAMPLE_RATE": 0,
}


def _ai_response(payload, model=A_MODEL):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(payload)
    response.usage.total_tokens = 100
    response.model = model
    return response


def _essay(number, points=10):
    return {
        "question_number": number,
        "question_text": f"Essay question {number}?",
        "question_type": "ESSAY",
        "points": points,
        "options": [],
        "rubric": [
            {"level": "excellent", "description": "Great", "points": points},
            {"level": "poor", "description": "Poor", "points": 0},
        ],
        "model_answer": "A model essay.",
    }


def _answer(number):
    return {"question_number": number, "answer_html": f"<p>Essay {number} text.</p>"}


def _evaluation(number, score=8, rationale=A_RATIONALE):
    return {
        "question_number": number,
        "score_awarded": score,
        "max_points": 10,
        "level_achieved": "good",
        "evaluation_rationale": rationale,
        "evidence_quotes": [f"Essay {number} text"],
    }


def _a_payload(evaluations, confidence=90):
    return {
        "question_evaluations": evaluations,
        "grading_summary": {"total_score": 0, "max_total_points": 0, "percentage": 0},
        "grading_confidence": confidence,
        "overall_performance_analysis": "ok",
        "recommendations": [],
    }


def _is_b_call(kwargs):
    return kwargs.get("override_model") == B_MODEL


@override_settings(**SECOND_OPINION_SETTINGS)
class SecondOpinionPipelineTest(SimpleTestCase):
    def setUp(self):
        self.processor = AIProcessor()
        self.questions = [_essay(1)]
        self.answers = [_answer(1)]

    def _run(self, respond):
        with patch.object(
            AIProcessor, "execute_graded_task", side_effect=respond
        ) as mock_execute:
            result = self.processor._grade_student_submission_impl(
                user=MagicMock(),
                rubric_json=self.questions,
                answer_json=self.answers,
            )
        return result, mock_execute

    def test_confident_clean_run_makes_no_second_opinion_calls(self):
        def respond(**kwargs):
            self.assertFalse(_is_b_call(kwargs))
            return _ai_response(_a_payload([_evaluation(1)], confidence=95))

        result, mock_execute = self._run(respond)
        self.assertEqual(mock_execute.call_count, 1)
        self.assertNotIn("second_opinion", result)

    def test_low_confidence_triggers_blind_b_pass_and_agreement(self):
        b_prompts = []

        def respond(**kwargs):
            if _is_b_call(kwargs):
                b_prompts.append(kwargs["user_prompt"][0]["text"])
                return _ai_response(
                    {"question_evaluations": [_evaluation(1, rationale="B view")]},
                    model=B_MODEL,
                )
            return _ai_response(_a_payload([_evaluation(1)], confidence=50))

        result, mock_execute = self._run(respond)

        self.assertEqual(mock_execute.call_count, 2)  # A + one B batch
        # Ledger #3 — blindness: nothing of grader A's output may reach B.
        self.assertEqual(len(b_prompts), 1)
        self.assertNotIn(A_RATIONALE, b_prompts[0])
        self.assertNotIn("score_awarded", b_prompts[0])
        self.assertNotIn("Already Graded", b_prompts[0])

        block = result["second_opinion"]
        self.assertEqual(block["model"], B_MODEL)
        self.assertEqual(block["agreements"], [1])
        self.assertEqual(block["disagreements"], [])
        self.assertIn("low_confidence", block["selected"][str(1)])

    def test_disagreement_carries_severity_from_pipeline(self):
        # The pipeline passes the selected questions + settings thresholds
        # into the comparator: an 8-vs-0 split on a 10-point question is
        # gap_fraction 0.8 → critical.
        def respond(**kwargs):
            if _is_b_call(kwargs):
                return _ai_response(
                    {"question_evaluations": [_evaluation(1, score=0)]},
                    model=B_MODEL,
                )
            return _ai_response(_a_payload([_evaluation(1)], confidence=50))

        result, _ = self._run(respond)

        [disagreement] = result["second_opinion"]["disagreements"]
        self.assertEqual(disagreement["severity"]["tier"], "critical")
        self.assertEqual(disagreement["severity"]["gap_fraction"], 0.8)

    def test_disagreement_is_recorded_and_a_score_stands(self):
        def respond(**kwargs):
            if _is_b_call(kwargs):
                return _ai_response(
                    {
                        "question_evaluations": [
                            _evaluation(1, score=0, rationale="B: does not earn it")
                        ]
                    },
                    model=B_MODEL,
                )
            return _ai_response(_a_payload([_evaluation(1)], confidence=50))

        result, _ = self._run(respond)

        [disagreement] = result["second_opinion"]["disagreements"]
        self.assertEqual(disagreement["a"]["score_awarded"], 8)
        self.assertEqual(disagreement["b"]["score_awarded"], 0)
        # Grader A's stored evaluation and totals are untouched by B.
        self.assertEqual(result["question_evaluations"][0]["score_awarded"], 8)
        self.assertEqual(result["grading_summary"]["total_score"], 8)

    def test_flag_trigger_selects_only_flagged_question(self):
        self.questions = [_essay(1), _essay(2)]
        self.answers = [_answer(1), _answer(2)]
        b_prompts = []

        def respond(**kwargs):
            if _is_b_call(kwargs):
                b_prompts.append(kwargs["user_prompt"][0]["text"])
                return _ai_response(
                    {"question_evaluations": [_evaluation(2)]}, model=B_MODEL
                )
            evaluations = [
                _evaluation(1),
                {
                    **_evaluation(2),
                    "flag_for_review": {
                        "flag_type": "BORDERLINE_SCORE",
                        "description": "close call",
                        "recommendation": "look",
                    },
                },
            ]
            return _ai_response(_a_payload(evaluations, confidence=95))

        result, mock_execute = self._run(respond)

        self.assertEqual(mock_execute.call_count, 2)
        self.assertEqual(list(result["second_opinion"]["selected"]), ["2"])
        # B was asked about question 2 only.
        self.assertIn("Essay question 2", b_prompts[0])
        self.assertNotIn("Essay question 1?", b_prompts[0])

    def test_high_points_trigger(self):
        self.questions = [_essay(1, points=25)]

        def respond(**kwargs):
            if _is_b_call(kwargs):
                return _ai_response(
                    {"question_evaluations": [_evaluation(1)]}, model=B_MODEL
                )
            return _ai_response(_a_payload([_evaluation(1)], confidence=95))

        result, mock_execute = self._run(respond)
        self.assertEqual(mock_execute.call_count, 2)
        self.assertIn("high_stakes", result["second_opinion"]["selected"][str(1)])

    def test_same_model_candidates_skip_without_b_calls(self):
        def respond(**kwargs):
            self.assertFalse(_is_b_call(kwargs))
            # Grader A's response reports it was routed to B_MODEL — the
            # only candidate — so no independent model exists.
            return _ai_response(
                _a_payload([_evaluation(1)], confidence=50), model=B_MODEL
            )

        result, mock_execute = self._run(respond)
        self.assertEqual(mock_execute.call_count, 1)
        self.assertEqual(
            result["second_opinion"]["skipped"], "no independent model available"
        )

    def test_second_pass_failure_is_non_fatal(self):
        def respond(**kwargs):
            if _is_b_call(kwargs):
                raise RuntimeError("second model exploded")
            return _ai_response(_a_payload([_evaluation(1)], confidence=50))

        result, _ = self._run(respond)

        # Grader A's grade survives, the failure is annotated.
        self.assertEqual(result["question_evaluations"][0]["score_awarded"], 8)
        self.assertEqual(result["grading_summary"]["total_score"], 8)
        self.assertIn("error", result["second_opinion"])

    def test_b_is_subject_to_evidence_enforcement(self):
        # B fabricates evidence on every attempt → its batch fails after
        # retries → non-fatal error annotation, A's grade stands.
        def respond(**kwargs):
            if _is_b_call(kwargs):
                return _ai_response(
                    {
                        "question_evaluations": [
                            {**_evaluation(1), "evidence_quotes": ["invented"]}
                        ]
                    },
                    model=B_MODEL,
                )
            return _ai_response(_a_payload([_evaluation(1)], confidence=50))

        result, mock_execute = self._run(respond)

        self.assertIn("error", result["second_opinion"])
        self.assertIn("evidence", result["second_opinion"]["error"])
        # A call + 3 rejected B attempts.
        self.assertEqual(mock_execute.call_count, 4)
        self.assertEqual(result["grading_summary"]["total_score"], 8)

    @override_settings(GRADING_SECOND_OPINION_ENABLED=False)
    def test_kill_switch_produces_identical_result(self):
        # Ledger #13.
        def respond(**kwargs):
            self.assertFalse(_is_b_call(kwargs))
            return _ai_response(_a_payload([_evaluation(1)], confidence=10))

        result, mock_execute = self._run(respond)
        self.assertEqual(mock_execute.call_count, 1)
        self.assertNotIn("second_opinion", result)


@override_settings(**SECOND_OPINION_SETTINGS)
class SecondOpinionBatchedPathTest(SimpleTestCase):
    """The hook also fires on the batched (large-assignment) exit."""

    def test_batched_run_with_low_confidence_gets_second_opinion(self):
        processor = AIProcessor()
        questions = [_essay(n) for n in range(1, 8)]  # 7 → batched path
        answers = [_answer(n) for n in range(1, 8)]

        def respond(**kwargs):
            prompt = kwargs["user_prompt"][0]["text"]
            if _is_b_call(kwargs):
                asked = [n for n in range(1, 8) if f"Essay question {n}?" in prompt]
                return _ai_response(
                    {"question_evaluations": [_evaluation(n) for n in asked]},
                    model=B_MODEL,
                )
            if SUMMARY_MARKER in prompt:
                return _ai_response(
                    {
                        "grading_summary": {},
                        "overall_performance_analysis": "ok",
                        "score_calculation_verification": {},
                        "grader_meta_analysis": "meta",
                        "grading_confidence": 40,  # low → full second read
                        "recommendations": [],
                    }
                )
            asked = [n for n in range(1, 8) if f"Essay question {n}?" in prompt]
            return _ai_response(
                {"question_evaluations": [_evaluation(n) for n in asked]}
            )

        with patch.object(
            AIProcessor, "execute_graded_task", side_effect=respond
        ) as mock_execute:
            result = processor._grade_student_submission_impl(
                user=MagicMock(), rubric_json=questions, answer_json=answers
            )

        # 2 A batches + 1 summary + 2 B batches (7 questions re-read).
        self.assertEqual(mock_execute.call_count, 5)
        self.assertEqual(len(result["second_opinion"]["agreements"]), 7)
        self.assertEqual(result["second_opinion"]["disagreements"], [])
