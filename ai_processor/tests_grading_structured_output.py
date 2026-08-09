"""
Coverage for structured grading output: the json_schema contracts
(ai_processor/grading_schemas.py) and their wiring plus evidence
enforcement inside _grade_student_submission_impl.

Locked here:
- Every grading call site passes its schema (single-pass, batch, summary)
  and falls back to None (free-form json_object) when the
  GRADING_RESPONSE_SCHEMA_ENABLED kill switch is off.
- The schemas are strict-mode well-formed: every object node has
  additionalProperties: False and requires every property, so a provider
  cannot silently drop fields.
- Fabricated evidence, missing evidence, and points-on-blank-answers are
  rejected and retried at both pipeline call sites; verified quotes
  survive into the stored result with fabrications filtered out.
- A batch response containing evaluations for questions OUTSIDE its
  batch has them dropped — never merged as double-counted duplicates.

Run with:
    python manage.py test ai_processor.tests_grading_structured_output
"""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from ai_processor.grading_schemas import (
    GRADING_BATCH_RESPONSE_SCHEMA,
    GRADING_SINGLE_PASS_RESPONSE_SCHEMA,
    GRADING_SUMMARY_RESPONSE_SCHEMA,
    QUESTION_EVALUATION_SCHEMA,
)
from ai_processor.services import AIProcessor


def _ai_response(payload, model="test-model"):
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


def _answer(number, text):
    return {"question_number": number, "answer_html": text}


def _evaluation(number, score=8, quotes=None):
    return {
        "question_number": number,
        "score_awarded": score,
        "max_points": 10,
        "evidence_quotes": quotes if quotes is not None else [f"Essay {number}"],
    }


def _payload(evaluations):
    return {"question_evaluations": evaluations}


SUMMARY_MARKER = "All questions have been graded individually"


def _summary_payload():
    return {
        "grading_summary": {},
        "overall_performance_analysis": "ok",
        "score_calculation_verification": {},
        "grader_meta_analysis": "consistent",
        "grading_confidence": 90,
        "recommendations": [],
    }


@override_settings(GRADING_SECOND_OPINION_ENABLED=False)
class SchemaWellFormednessTest(SimpleTestCase):
    ALL_SCHEMAS = [
        GRADING_BATCH_RESPONSE_SCHEMA,
        GRADING_SINGLE_PASS_RESPONSE_SCHEMA,
        GRADING_SUMMARY_RESPONSE_SCHEMA,
    ]

    def test_wrapper_shape_matches_provider_contract(self):
        # __ai_model wraps these as {"type": "json_schema", "json_schema":
        # <this>} — same contract ASSIGNMENT_GENERATION_RESPONSE_SCHEMA
        # already uses in production.
        for schema in self.ALL_SCHEMAS:
            self.assertIn("name", schema)
            self.assertIs(schema["strict"], True)
            self.assertIn("schema", schema)
            json.dumps(schema)  # must be serializable as-is

    def _walk_objects(self, node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                yield node
            for value in node.values():
                yield from self._walk_objects(value)
        elif isinstance(node, list):
            for item in node:
                yield from self._walk_objects(item)

    def test_every_object_node_is_strict(self):
        # Strict mode demands: no extra keys, and every declared property
        # required — otherwise a provider may silently drop fields.
        for schema in self.ALL_SCHEMAS:
            for node in self._walk_objects(schema["schema"]):
                self.assertIs(node.get("additionalProperties"), False, node)
                self.assertEqual(
                    set(node.get("required", [])),
                    set(node["properties"].keys()),
                    node,
                )

    def test_evaluation_contract_fields(self):
        properties = QUESTION_EVALUATION_SCHEMA["properties"]
        self.assertIn("evidence_quotes", properties)
        self.assertIn("flag_for_review", properties)
        self.assertIn("evidence_quotes", QUESTION_EVALUATION_SCHEMA["required"])
        levels = properties["level_achieved"]["enum"]
        self.assertEqual(
            set(levels),
            {
                "excellent",
                "good",
                "fair",
                "poor",
                "correct",
                "incorrect",
                "not_attempted",
            },
        )

    def test_batch_and_single_pass_share_the_evaluation_schema(self):
        self.assertIs(
            GRADING_BATCH_RESPONSE_SCHEMA["schema"]["properties"][
                "question_evaluations"
            ]["items"],
            QUESTION_EVALUATION_SCHEMA,
        )
        self.assertIs(
            GRADING_SINGLE_PASS_RESPONSE_SCHEMA["schema"]["properties"][
                "question_evaluations"
            ]["items"],
            QUESTION_EVALUATION_SCHEMA,
        )


@override_settings(GRADING_SECOND_OPINION_ENABLED=False)
class SchemaCallSiteTest(SimpleTestCase):
    def setUp(self):
        self.processor = AIProcessor()

    @patch.object(AIProcessor, "execute_graded_task")
    def test_single_pass_call_carries_single_pass_schema(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=[_essay(1)],
            answer_json=[_answer(1, "<p>Essay 1</p>")],
        )
        self.assertIs(
            mock_execute.call_args.kwargs["response_schema"],
            GRADING_SINGLE_PASS_RESPONSE_SCHEMA,
        )

    @patch.object(AIProcessor, "execute_graded_task")
    def test_batched_calls_carry_batch_then_summary_schema(self, mock_execute):
        questions = [_essay(n) for n in range(1, 8)]  # 7 → batched path
        answers = [_answer(n, f"<p>Essay {n}</p>") for n in range(1, 8)]

        def respond(**kwargs):
            prompt = kwargs["user_prompt"][0]["text"]
            if SUMMARY_MARKER in prompt:
                return _ai_response(_summary_payload())
            asked = [n for n in range(1, 8) if f"Essay question {n}?" in prompt]
            return _ai_response(_payload([_evaluation(n) for n in asked]))

        mock_execute.side_effect = respond
        self.processor._grade_student_submission_impl(
            user=MagicMock(), rubric_json=questions, answer_json=answers
        )

        schemas = [
            call.kwargs["response_schema"] for call in mock_execute.call_args_list
        ]
        self.assertEqual(len(schemas), 3)  # 2 batches + summary
        self.assertIs(schemas[0], GRADING_BATCH_RESPONSE_SCHEMA)
        self.assertIs(schemas[1], GRADING_BATCH_RESPONSE_SCHEMA)
        self.assertIs(schemas[2], GRADING_SUMMARY_RESPONSE_SCHEMA)

    @override_settings(GRADING_RESPONSE_SCHEMA_ENABLED=False)
    @patch.object(AIProcessor, "execute_graded_task")
    def test_kill_switch_falls_back_to_free_form(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=[_essay(1)],
            answer_json=[_answer(1, "<p>Essay 1</p>")],
        )
        self.assertIsNone(mock_execute.call_args.kwargs["response_schema"])


@override_settings(GRADING_SECOND_OPINION_ENABLED=False)
class SinglePassEvidenceEnforcementTest(SimpleTestCase):
    def setUp(self):
        self.processor = AIProcessor()
        self.questions = [_essay(1)]
        self.answers = [_answer(1, "<p>Essay 1 about photosynthesis.</p>")]

    def _run(self):
        return self.processor._grade_student_submission_impl(
            user=MagicMock(), rubric_json=self.questions, answer_json=self.answers
        )

    @patch.object(AIProcessor, "execute_graded_task")
    def test_fabricated_evidence_is_rejected(self, mock_execute):
        mock_execute.return_value = _ai_response(
            _payload([_evaluation(1, quotes=["totally invented span"])])
        )
        with self.assertRaisesMessage(ValueError, "evidence check failed"):
            self._run()

    @patch.object(AIProcessor, "execute_graded_task")
    def test_missing_evidence_with_points_is_rejected(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1, quotes=[])]))
        with self.assertRaisesMessage(ValueError, "no evidence"):
            self._run()

    @patch.object(AIProcessor, "execute_graded_task")
    def test_points_awarded_to_unanswered_question_is_rejected(self, mock_execute):
        # The hallmark hallucination: the student never answered, the
        # model awards points anyway.
        self.answers = []
        mock_execute.return_value = _ai_response(
            _payload([_evaluation(1, quotes=["anything at all"])])
        )
        with self.assertRaisesMessage(ValueError, "empty/blank"):
            self._run()

    @patch.object(AIProcessor, "execute_graded_task")
    def test_verified_evidence_survives_and_fabrications_are_filtered(
        self, mock_execute
    ):
        mock_execute.return_value = _ai_response(
            _payload(
                [
                    _evaluation(
                        1,
                        quotes=["about photosynthesis", "invented nonsense"],
                    )
                ]
            )
        )
        result = self._run()
        evaluation = result["question_evaluations"][0]
        self.assertEqual(evaluation["evidence_quotes"], ["about photosynthesis"])
        self.assertEqual(evaluation["unverified_evidence_count"], 1)
        self.assertTrue(evaluation["evidence_verified"])

    @patch.object(AIProcessor, "execute_graded_task")
    def test_zero_score_needs_no_evidence(self, mock_execute):
        mock_execute.return_value = _ai_response(
            _payload([_evaluation(1, score=0, quotes=[])])
        )
        result = self._run()
        self.assertEqual(result["question_evaluations"][0]["score_awarded"], 0)

    @override_settings(GRADING_EVIDENCE_ENFORCEMENT="log")
    @patch.object(AIProcessor, "execute_graded_task")
    def test_log_mode_annotates_without_rejecting(self, mock_execute):
        mock_execute.return_value = _ai_response(
            _payload([_evaluation(1, quotes=["totally invented span"])])
        )
        result = self._run()
        evaluation = result["question_evaluations"][0]
        self.assertFalse(evaluation["evidence_verified"])
        self.assertEqual(evaluation["evidence_quotes"], [])

    @override_settings(GRADING_EVIDENCE_ENFORCEMENT="off")
    @patch.object(AIProcessor, "execute_graded_task")
    def test_off_mode_leaves_evaluations_untouched(self, mock_execute):
        mock_execute.return_value = _ai_response(
            _payload([_evaluation(1, quotes=["totally invented span"])])
        )
        result = self._run()
        evaluation = result["question_evaluations"][0]
        self.assertEqual(evaluation["evidence_quotes"], ["totally invented span"])
        self.assertNotIn("evidence_verified", evaluation)


@override_settings(GRADING_SECOND_OPINION_ENABLED=False)
class BatchEvidenceEnforcementTest(SimpleTestCase):
    def setUp(self):
        self.processor = AIProcessor()
        # 7 essays → batched path (2 batches of 5 + 2).
        self.questions = [_essay(n) for n in range(1, 8)]
        self.answers = [_answer(n, f"<p>Essay {n} content.</p>") for n in range(1, 8)]

    @patch.object(AIProcessor, "execute_graded_task")
    def test_fabricated_batch_evidence_retries_three_times_then_fails(
        self, mock_execute
    ):
        def respond(**kwargs):
            prompt = kwargs["user_prompt"][0]["text"]
            if SUMMARY_MARKER in prompt:
                return _ai_response(_summary_payload())
            asked = [n for n in range(1, 8) if f"Essay question {n}?" in prompt]
            return _ai_response(
                _payload([_evaluation(n, quotes=["fabricated span"]) for n in asked])
            )

        mock_execute.side_effect = respond
        with self.assertRaisesMessage(Exception, "failed after 3 attempts"):
            self.processor._grade_student_submission_impl(
                user=MagicMock(),
                rubric_json=self.questions,
                answer_json=self.answers,
            )
        # Batch 1 was attempted exactly 3 times, then the run aborted —
        # the summary call must never have happened.
        self.assertEqual(mock_execute.call_count, 3)

    @patch.object(AIProcessor, "execute_graded_task")
    def test_out_of_batch_evaluations_are_dropped_not_double_counted(
        self, mock_execute
    ):
        def respond(**kwargs):
            prompt = kwargs["user_prompt"][0]["text"]
            if SUMMARY_MARKER in prompt:
                return _ai_response(_summary_payload())
            asked = [n for n in range(1, 8) if f"Essay question {n}?" in prompt]
            # Model hallucinates evaluations for EVERY question in every
            # batch response; only this batch's must survive.
            return _ai_response(
                _payload([_evaluation(n) for n in range(1, 8)] if asked else [])
            )

        mock_execute.side_effect = respond
        result = self.processor._grade_student_submission_impl(
            user=MagicMock(), rubric_json=self.questions, answer_json=self.answers
        )

        evaluations = result["question_evaluations"]
        self.assertEqual(len(evaluations), 7)
        numbers = sorted(ev["question_number"] for ev in evaluations)
        self.assertEqual(numbers, list(range(1, 8)))
        # 7 × 8 points each — would be double that if duplicates merged.
        self.assertEqual(result["grading_summary"]["total_score"], 56)

    @patch.object(AIProcessor, "execute_graded_task")
    def test_clean_batches_pass_with_verified_evidence(self, mock_execute):
        def respond(**kwargs):
            prompt = kwargs["user_prompt"][0]["text"]
            if SUMMARY_MARKER in prompt:
                return _ai_response(_summary_payload())
            asked = [n for n in range(1, 8) if f"Essay question {n}?" in prompt]
            return _ai_response(
                _payload([_evaluation(n, quotes=[f"Essay {n} content"]) for n in asked])
            )

        mock_execute.side_effect = respond
        result = self.processor._grade_student_submission_impl(
            user=MagicMock(), rubric_json=self.questions, answer_json=self.answers
        )
        self.assertEqual(mock_execute.call_count, 3)  # 2 batches + summary
        for evaluation in result["question_evaluations"]:
            self.assertTrue(evaluation["evidence_verified"])
