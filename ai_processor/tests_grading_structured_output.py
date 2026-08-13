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
    # Three levels, not two: _evaluation()'s default score (8) must be an
    # exact rubric-level value, or AIProcessor's rubric-level snapping
    # (score corrected to the NEAREST level in _finalize_grading_result)
    # would round every fixture score up to `points`, breaking the
    # per-question total assertions below.
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


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
)
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


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
)
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


def _fake_assignment(id=1, title="", instructions="", custom_ai_prompt=""):
    """
    Just enough of an Assignment to exercise the prompt-building helpers
    (_assignment_context_block, _custom_instructions_block) — those only
    ever read .id/.title/.instructions/.custom_ai_prompt via getattr, so
    a real Django model instance isn't needed.
    """
    from types import SimpleNamespace

    return SimpleNamespace(
        id=id, title=title, instructions=instructions, custom_ai_prompt=custom_ai_prompt
    )


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
)
class AssignmentContextTest(SimpleTestCase):
    """Fix 2: a batch previously saw only its own 5-question slice, with
    no title/instructions/sense of the whole paper."""

    def setUp(self):
        self.processor = AIProcessor()

    @patch.object(AIProcessor, "execute_graded_task")
    def test_single_pass_prompt_carries_title_and_instructions(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=[_essay(1)],
            answer_json=[_answer(1, "<p>Essay 1</p>")],
            assignment_model=_fake_assignment(
                title="Midterm Essay Paper", instructions="Show your working."
            ),
        )
        prompt = mock_execute.call_args.kwargs["user_prompt"][0]["text"]
        self.assertIn("Midterm Essay Paper", prompt)
        self.assertIn("Show your working.", prompt)

    @patch.object(AIProcessor, "execute_graded_task")
    def test_batch_prompt_carries_title_and_batch_position(self, mock_execute):
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
            user=MagicMock(),
            rubric_json=questions,
            answer_json=answers,
            assignment_model=_fake_assignment(title="Final Exam"),
        )

        batch_prompts = [
            call.kwargs["user_prompt"][0]["text"]
            for call in mock_execute.call_args_list
            if SUMMARY_MARKER not in call.kwargs["user_prompt"][0]["text"]
        ]
        self.assertEqual(len(batch_prompts), 2)
        for prompt in batch_prompts:
            self.assertIn("Final Exam", prompt)
        self.assertIn("This batch is 1 of 2", batch_prompts[0])
        self.assertIn("This batch is 2 of 2", batch_prompts[1])

    @patch.object(AIProcessor, "execute_graded_task")
    def test_blank_title_and_instructions_add_no_block(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=[_essay(1)],
            answer_json=[_answer(1, "<p>Essay 1</p>")],
            assignment_model=_fake_assignment(),
        )
        prompt = mock_execute.call_args.kwargs["user_prompt"][0]["text"]
        self.assertNotIn("### Assignment Context", prompt)

    @patch.object(AIProcessor, "execute_graded_task")
    def test_no_assignment_model_adds_no_block_and_does_not_crash(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=[_essay(1)],
            answer_json=[_answer(1, "<p>Essay 1</p>")],
        )
        prompt = mock_execute.call_args.kwargs["user_prompt"][0]["text"]
        self.assertNotIn("### Assignment Context", prompt)


def _essay_with_image(number, image_url, points=10):
    question = _essay(number, points=points)
    question["question_image"] = image_url
    return question


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
)
class QuestionImageTest(SimpleTestCase):
    """Fix 3: question_image existed on the schema but was never sent to
    the grading model — a question whose content IS a diagram was graded
    blind."""

    def setUp(self):
        self.processor = AIProcessor()

    @patch.object(AIProcessor, "execute_graded_task")
    def test_question_image_becomes_an_image_content_block(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=[_essay_with_image(1, "https://example.com/diagram.png")],
            answer_json=[_answer(1, "<p>Essay 1</p>")],
        )
        content = mock_execute.call_args.kwargs["user_prompt"]
        image_blocks = [c for c in content if c.get("type") == "image_url"]
        self.assertEqual(len(image_blocks), 1)
        self.assertEqual(
            image_blocks[0]["image_url"]["url"], "https://example.com/diagram.png"
        )

    @patch.object(AIProcessor, "execute_graded_task")
    def test_blank_question_image_stays_text_only(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=[_essay(1)],
            answer_json=[_answer(1, "<p>Essay 1</p>")],
        )
        content = mock_execute.call_args.kwargs["user_prompt"]
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]["type"], "text")

    @patch.object(AIProcessor, "execute_graded_task")
    def test_non_http_url_is_rejected_not_sent(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=[_essay_with_image(1, "javascript:alert(1)")],
            answer_json=[_answer(1, "<p>Essay 1</p>")],
        )
        content = mock_execute.call_args.kwargs["user_prompt"]
        image_blocks = [c for c in content if c.get("type") == "image_url"]
        self.assertEqual(image_blocks, [])

    @override_settings(GRADING_MAX_IMAGES_PER_CALL=2)
    @patch.object(AIProcessor, "execute_graded_task")
    def test_image_count_is_capped_per_call(self, mock_execute):
        questions = [
            _essay_with_image(n, f"https://example.com/{n}.png") for n in range(1, 4)
        ]
        answers = [_answer(n, f"<p>Essay {n}</p>") for n in range(1, 4)]
        mock_execute.return_value = _ai_response(
            _payload([_evaluation(n) for n in range(1, 4)])
        )
        self.processor._grade_student_submission_impl(
            user=MagicMock(), rubric_json=questions, answer_json=answers
        )
        content = mock_execute.call_args.kwargs["user_prompt"]
        image_blocks = [c for c in content if c.get("type") == "image_url"]
        self.assertEqual(len(image_blocks), 2)


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
)
class CustomInstructionsTest(SimpleTestCase):
    """Fix 4: Assignment.custom_ai_prompt existed but was read by nothing
    in the grading pipeline (its one reference was commented-out dead
    code in dashboard/views.py)."""

    def setUp(self):
        self.processor = AIProcessor()

    @patch.object(AIProcessor, "execute_graded_task")
    def test_custom_prompt_appears_framed_as_non_overriding(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=[_essay(1)],
            answer_json=[_answer(1, "<p>Essay 1</p>")],
            assignment_model=_fake_assignment(
                custom_ai_prompt="Always require units on numeric answers."
            ),
        )
        system_prompt = mock_execute.call_args.kwargs["system_prompt"][0]["text"]
        self.assertIn("Always require units on numeric answers.", system_prompt)
        self.assertIn("never overrides the rules above", system_prompt)

    @patch.object(AIProcessor, "execute_graded_task")
    def test_blank_custom_prompt_adds_nothing(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=[_essay(1)],
            answer_json=[_answer(1, "<p>Essay 1</p>")],
            assignment_model=_fake_assignment(custom_ai_prompt=""),
        )
        system_prompt = mock_execute.call_args.kwargs["system_prompt"][0]["text"]
        self.assertNotIn("Teacher's Additional Grading Instructions", system_prompt)

    @override_settings(GRADING_CUSTOM_INSTRUCTIONS_ENABLED=False)
    @patch.object(AIProcessor, "execute_graded_task")
    def test_flag_off_suppresses_custom_prompt(self, mock_execute):
        mock_execute.return_value = _ai_response(_payload([_evaluation(1)]))
        self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=[_essay(1)],
            answer_json=[_answer(1, "<p>Essay 1</p>")],
            assignment_model=_fake_assignment(custom_ai_prompt="Always require units."),
        )
        system_prompt = mock_execute.call_args.kwargs["system_prompt"][0]["text"]
        self.assertNotIn("Always require units.", system_prompt)

    @patch.object(AIProcessor, "execute_graded_task")
    def test_batch_path_also_carries_custom_prompt(self, mock_execute):
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
            user=MagicMock(),
            rubric_json=questions,
            answer_json=answers,
            assignment_model=_fake_assignment(
                custom_ai_prompt="Accept British and American spelling."
            ),
        )
        for call in mock_execute.call_args_list:
            system_prompt = call.kwargs["system_prompt"][0]["text"]
            self.assertIn("Accept British and American spelling.", system_prompt)


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
)
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


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
)
class BatchEvidenceEnforcementTest(SimpleTestCase):
    def setUp(self):
        self.processor = AIProcessor()
        # 7 essays → batched path (2 batches of 5 + 2).
        self.questions = [_essay(n) for n in range(1, 8)]
        self.answers = [_answer(n, f"<p>Essay {n} content.</p>") for n in range(1, 8)]

    @patch.object(AIProcessor, "execute_graded_task")
    def test_fabricated_batch_evidence_retries_then_degrades_on_the_last_try(
        self, mock_execute
    ):
        # CONTRACT CHANGED (see ai_processor/benchmark/FINDINGS.md #1).
        # This used to assert the run ABORTED after 3 failed attempts.
        # The first live benchmark run showed what that costs in
        # practice: on long multi-step algebra the model quotes by
        # eliding intermediate steps — textually not verbatim, so it
        # reads as fabrication — and one maths submission in 21 ended up
        # with no grade at all. The student most likely to trigger it is
        # the one showing the most working.
        #
        # So strict enforcement still rejects attempts 1 and 2 (a re-ask
        # usually fixes it), but the FINAL attempt degrades to "log":
        # the grade is returned with the quote marked unverified rather
        # than thrown away. Retry behaviour is unchanged; only the
        # terminal case is.
        def respond(**kwargs):
            prompt = kwargs["user_prompt"][0]["text"]
            if SUMMARY_MARKER in prompt:
                return _ai_response(_summary_payload())
            asked = [n for n in range(1, 8) if f"Essay question {n}?" in prompt]
            return _ai_response(
                _payload([_evaluation(n, quotes=["fabricated span"]) for n in asked])
            )

        mock_execute.side_effect = respond
        result = self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=self.questions,
            answer_json=self.answers,
        )

        # Both batches fabricate, so both burn 3 attempts (2 rejected,
        # the 3rd degraded): 3 + 3, plus the summary call — which now
        # DOES happen, because the run survived instead of aborting.
        self.assertEqual(mock_execute.call_count, 7)

        evaluations = result["question_evaluations"]
        self.assertEqual(len(evaluations), 7)
        # Every quote was fabricated, so every evaluation must carry the
        # unverified marker — the teacher can still tell.
        for evaluation in evaluations:
            self.assertFalse(evaluation.get("evidence_verified", True))

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
