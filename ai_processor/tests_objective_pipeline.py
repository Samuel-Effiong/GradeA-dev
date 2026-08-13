"""
Integration coverage for Tier 0 deterministic objective grading inside
_grade_student_submission_impl (ai_processor/services.py).

What these tests lock:
- Hybrid assignments partition per-question: claimed objectives never
  re-enter any AI prompt; essays/short-answers and AMBIGUOUS objectives
  always do.
- An all-deterministic submission makes ZERO AI calls (and therefore
  bills zero credits — execute_graded_task is the only billing entry).
- The single-pass/batched threshold counts only LLM-bound questions.
- Deterministic evaluations win over any model re-emission (dedupe), and
  the completeness check is scoped to the LLM-bound questions.
- With GRADING_DETERMINISTIC_OBJECTIVE = False the pipeline behaves
  exactly as before (rollback lever).
- End-to-end through students.services.grade_engine: the grade persists,
  the claim state machine completes, and no credits are consumed for an
  all-objective submission.

AI calls are stubbed at AIProcessor.execute_graded_task — the single
chokepoint through which every model call and every credit charge flows —
so `assert_not_called()` doubles as a zero-billing assertion.

Run with:
    python manage.py test ai_processor.tests_objective_pipeline
"""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from ai_processor.services import AIProcessor


def _ai_response(payload, model="test-model"):
    """OpenAI-SDK-shaped stub: .choices[0].message.content + .usage +
    .model (read by _response_model_name)."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(payload)
    response.usage.total_tokens = 100
    response.model = model
    return response


def _objective(number, correct="Paris", answer_options=None, **overrides):
    question = {
        "question_number": number,
        "question_text": f"Objective question {number}?",
        "question_type": "OBJECTIVE",
        "points": 5,
        "options": answer_options or ["London", "Berlin", "Paris", "Madrid"],
        "rubric": [],
        "model_answer": correct,
    }
    question.update(overrides)
    return question


def _essay(number, points=10):
    # Three levels, not two: _essay_evaluation()'s default score (8) must
    # be an exact rubric-level value, or AIProcessor's rubric-level
    # snapping (score corrected to the NEAREST level in
    # _finalize_grading_result) would round every fixture essay score up
    # to `points`, breaking these merged-total assertions.
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


def _essay_evaluation(number, score=8, max_points=10, evidence=None):
    return {
        "question_number": number,
        "question_type": "ESSAY",
        "max_points": max_points,
        "score_awarded": score,
        "level_achieved": "good",
        # Evidence contract: awarded points must cite a verbatim span of
        # the student's answer. Every fixture answer contains "Essay {n}".
        "evidence_quotes": evidence if evidence is not None else [f"Essay {number}"],
        "evaluation_rationale": "Solid work.",
        "feedback_for_student": "Good.",
    }


def _single_pass_payload(evaluations):
    return {
        "question_evaluations": evaluations,
        "grading_summary": {"total_score": 0, "max_total_points": 0, "percentage": 0},
        "grading_confidence": 90,
        "overall_performance_analysis": "ok",
        "recommendations": [],
    }


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
)
class HybridPartitionTest(SimpleTestCase):
    def setUp(self):
        self.processor = AIProcessor()
        self.questions = [
            _objective(1),  # answered correctly
            _objective(2),  # answered incorrectly (but unambiguously)
            _essay(3),
            _essay(4),
        ]
        self.answers = [
            _answer(1, "<p>Paris</p>"),
            _answer(2, "London"),
            _answer(3, "<p>Essay 3 is my answer.</p>"),
            _answer(4, "<p>Essay 4 is my answer.</p>"),
        ]

    @patch.object(AIProcessor, "execute_graded_task")
    def test_claimed_objectives_never_reach_the_prompt(self, mock_execute):
        mock_execute.return_value = _ai_response(
            _single_pass_payload([_essay_evaluation(3), _essay_evaluation(4)])
        )

        result = self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=self.questions,
            answer_json=self.answers,
        )

        self.assertEqual(mock_execute.call_count, 1)
        prompt = mock_execute.call_args.kwargs["user_prompt"][0]["text"]
        # The claimed objectives' options/answers must not re-enter the
        # grading task...
        for leaked in ["London", "Berlin", "Madrid"]:
            self.assertNotIn(leaked, prompt)
        # ...while the essays and the read-only context block must.
        self.assertIn("Essay question 3", prompt)
        self.assertIn("Essay 4 is my answer", prompt)
        self.assertIn("Already Graded Deterministically", prompt)

        evaluations = result["question_evaluations"]
        self.assertEqual(len(evaluations), 4)
        by_number = {ev["question_number"]: ev for ev in evaluations}
        self.assertEqual(by_number[1]["score_awarded"], 5)
        self.assertEqual(by_number[1]["graded_by"], "deterministic")
        self.assertEqual(by_number[2]["score_awarded"], 0)
        self.assertEqual(by_number[3]["graded_by"], "test-model")
        # Totals cover the merged set: 5 + 0 + 8 + 8 of 30.
        self.assertEqual(result["grading_summary"]["total_score"], 21)
        self.assertEqual(result["grading_summary"]["max_total_points"], 30)
        self.assertEqual(result["grading_summary"]["percentage"], 70.0)

    @patch.object(AIProcessor, "execute_graded_task")
    def test_model_reemission_of_claimed_question_is_dropped(self, mock_execute):
        # Ledger #18: the model ignores the DO-NOT-re-grade instruction
        # and re-emits question 1 with a different score — the
        # deterministic evaluation must win.
        mock_execute.return_value = _ai_response(
            _single_pass_payload(
                [
                    {"question_number": 1, "score_awarded": 2, "max_points": 5},
                    _essay_evaluation(3),
                    _essay_evaluation(4),
                ]
            )
        )

        result = self.processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=self.questions,
            answer_json=self.answers,
        )

        evaluations = result["question_evaluations"]
        self.assertEqual(len(evaluations), 4)
        by_number = {ev["question_number"]: ev for ev in evaluations}
        self.assertEqual(by_number[1]["score_awarded"], 5)
        self.assertEqual(by_number[1]["graded_by"], "deterministic")

    @patch.object(AIProcessor, "execute_graded_task")
    def test_completeness_check_scoped_to_llm_questions(self, mock_execute):
        # A response covering only the LLM-bound questions passes; the
        # deterministically-graded ones must not count as "missing"...
        mock_execute.return_value = _ai_response(
            _single_pass_payload([_essay_evaluation(3), _essay_evaluation(4)])
        )
        result = self.processor._grade_student_submission_impl(
            user=MagicMock(), rubric_json=self.questions, answer_json=self.answers
        )
        self.assertEqual(len(result["question_evaluations"]), 4)

        # ...but a response missing an LLM-bound essay still fails loudly
        # (the retry path upstream depends on this raise).
        mock_execute.return_value = _ai_response(
            _single_pass_payload([_essay_evaluation(3)])
        )
        with self.assertRaises(ValueError):
            self.processor._grade_student_submission_impl(
                user=MagicMock(), rubric_json=self.questions, answer_json=self.answers
            )


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
)
class AllDeterministicTest(SimpleTestCase):
    @patch.object(AIProcessor, "execute_graded_task")
    def test_zero_ai_calls_and_complete_result_shape(self, mock_execute):
        processor = AIProcessor()
        questions = [
            _objective(1),
            _objective(2),
            _objective(3),
        ]
        answers = [
            _answer(1, "Paris"),  # correct
            _answer(2, "Berlin"),  # incorrect
            # question 3 unanswered → not_attempted
        ]

        result = processor._grade_student_submission_impl(
            user=MagicMock(), rubric_json=questions, answer_json=answers
        )

        # The single billing/AI chokepoint was never reached: zero model
        # calls, zero credits.
        mock_execute.assert_not_called()

        self.assertEqual(result["grading_summary"]["total_score"], 5)
        self.assertEqual(result["grading_summary"]["max_total_points"], 15)
        self.assertEqual(result["grading_summary"]["percentage"], 33.33)
        self.assertEqual(result["grading_confidence"], 100)
        self.assertEqual(result["grading_model"], "deterministic")
        self.assertIn("score_calculation_verification", result)
        self.assertIn("overall_performance_analysis", result)
        levels = sorted(ev["level_achieved"] for ev in result["question_evaluations"])
        self.assertEqual(levels, ["correct", "incorrect", "not_attempted"])

    @patch.object(AIProcessor, "execute_graded_task")
    def test_string_rubric_and_answers_also_short_circuit(self, mock_execute):
        # grade_engine passes Python lists, but the impl accepts JSON
        # strings too — the partition must work on both.
        processor = AIProcessor()
        result = processor._grade_student_submission_impl(
            user=MagicMock(),
            rubric_json=json.dumps([_objective(1)]),
            answer_json=json.dumps([_answer(1, "Paris")]),
        )
        mock_execute.assert_not_called()
        self.assertEqual(result["grading_summary"]["total_score"], 5)


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
)
class AmbiguousFallbackTest(SimpleTestCase):
    @patch.object(AIProcessor, "execute_graded_task")
    def test_typoed_key_defers_to_llm_while_clean_sibling_does_not(self, mock_execute):
        processor = AIProcessor()
        questions = [
            _objective(1),  # clean → deterministic
            _objective(2, correct="Pariss"),  # typo'd key → AMBIGUOUS → LLM
        ]
        answers = [_answer(1, "Paris"), _answer(2, "Paris")]
        mock_execute.return_value = _ai_response(
            _single_pass_payload(
                [
                    {
                        "question_number": 2,
                        "score_awarded": 5,
                        "max_points": 5,
                        "evidence_quotes": ["Paris"],
                    }
                ]
            )
        )

        result = processor._grade_student_submission_impl(
            user=MagicMock(), rubric_json=questions, answer_json=answers
        )

        prompt = mock_execute.call_args.kwargs["user_prompt"][0]["text"]
        self.assertIn("Objective question 2", prompt)
        self.assertIn("Pariss", prompt)  # the ambiguous one is in the prompt
        # The clean sibling must be absent from the GRADING sections; it
        # legitimately appears (scores only) in the read-only context
        # block, so scope the assertion to everything before that block.
        grading_sections = prompt.split("Already Graded Deterministically")[0]
        self.assertNotIn("Objective question 1", grading_sections)
        self.assertEqual(len(result["question_evaluations"]), 2)


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
)
class ThresholdOnRemainderTest(SimpleTestCase):
    def _hybrid_12(self):
        # 8 clean objectives + 4 essays: 12 total, 4 LLM-bound.
        questions = [_objective(n) for n in range(1, 9)] + [
            _essay(n) for n in range(9, 13)
        ]
        answers = [_answer(n, "Paris") for n in range(1, 9)] + [
            _answer(n, f"<p>Essay {n}</p>") for n in range(9, 13)
        ]
        return questions, answers

    @patch.object(AIProcessor, "execute_graded_task")
    def test_flag_on_uses_single_pass_over_remainder(self, mock_execute):
        questions, answers = self._hybrid_12()
        mock_execute.return_value = _ai_response(
            _single_pass_payload([_essay_evaluation(n) for n in range(9, 13)])
        )

        result = AIProcessor()._grade_student_submission_impl(
            user=MagicMock(), rubric_json=questions, answer_json=answers
        )

        # 4 LLM-bound questions ≤ chunk size (5) → exactly ONE call, even
        # though the assignment has 12 questions.
        self.assertEqual(mock_execute.call_count, 1)
        self.assertEqual(len(result["question_evaluations"]), 12)
        self.assertEqual(result["grading_summary"]["max_total_points"], 80)

    @override_settings(GRADING_DETERMINISTIC_OBJECTIVE=False)
    @patch.object(AIProcessor, "execute_graded_task")
    def test_flag_off_restores_batched_behavior(self, mock_execute):
        # Ledger #19: rollback lever. 12 questions with the flag off →
        # the pre-Tier-0 batched pipeline: 3 grading batches + 1 summary.
        questions, answers = self._hybrid_12()
        all_evaluations = [
            {
                "question_number": n,
                "score_awarded": 5,
                "max_points": 5,
                "evidence_quotes": ["Paris"],
            }
            for n in range(1, 9)
        ] + [_essay_evaluation(n) for n in range(9, 13)]

        def respond(**kwargs):
            prompt = kwargs["user_prompt"][0]["text"]
            if "All questions have been graded individually" in prompt:
                return _ai_response(
                    {
                        "grading_summary": {},
                        "overall_performance_analysis": "ok",
                        "score_calculation_verification": {},
                        "grader_meta_analysis": {},
                        "grading_confidence": 90,
                        "recommendations": [],
                    }
                )
            return _ai_response({"question_evaluations": all_evaluations})

        mock_execute.side_effect = respond

        AIProcessor()._grade_student_submission_impl(
            user=MagicMock(), rubric_json=questions, answer_json=answers
        )

        # 3 batches (5+5+2) + 1 summary = 4 calls, exactly as before.
        self.assertEqual(mock_execute.call_count, 4)
        first_prompt = mock_execute.call_args_list[0].kwargs["user_prompt"][0]["text"]
        # Objective content re-enters the AI prompts when the flag is off.
        self.assertIn("Objective question 1", first_prompt)
        self.assertNotIn("Already Graded Deterministically", first_prompt)

    @override_settings(GRADING_DETERMINISTIC_OBJECTIVE=False)
    @patch.object(AIProcessor, "execute_graded_task")
    def test_flag_off_single_pass_prompt_has_no_context_block(self, mock_execute):
        questions = [_objective(1), _essay(2)]
        answers = [_answer(1, "Paris"), _answer(2, "<p>Essay 2</p>")]
        mock_execute.return_value = _ai_response(
            _single_pass_payload(
                [
                    {
                        "question_number": 1,
                        "score_awarded": 5,
                        "max_points": 5,
                        "evidence_quotes": ["Paris"],
                    },
                    _essay_evaluation(2),
                ]
            )
        )

        AIProcessor()._grade_student_submission_impl(
            user=MagicMock(), rubric_json=questions, answer_json=answers
        )

        prompt = mock_execute.call_args.kwargs["user_prompt"][0]["text"]
        self.assertIn("London", prompt)  # objective options present again
        self.assertNotIn("Already Graded Deterministically", prompt)


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
)
class BatchedHybridTest(SimpleTestCase):
    @patch.object(AIProcessor, "execute_graded_task")
    def test_merged_evaluations_reach_summary_and_totals(self, mock_execute):
        # 4 clean objectives + 9 essays → 9 LLM-bound → batched path:
        # 2 grading batches + 1 summary call.
        questions = [_objective(n) for n in range(1, 5)] + [
            _essay(n) for n in range(5, 14)
        ]
        answers = [_answer(n, "Paris") for n in range(1, 5)] + [
            _answer(n, f"<p>Essay {n}</p>") for n in range(5, 14)
        ]

        def respond(**kwargs):
            prompt = kwargs["user_prompt"][0]["text"]
            if "All questions have been graded individually" in prompt:
                return _ai_response(
                    {
                        "grading_summary": {},
                        "overall_performance_analysis": "ok",
                        "score_calculation_verification": {},
                        "grader_meta_analysis": {},
                        "grading_confidence": 88,
                        "recommendations": [],
                    }
                )
            # Answer exactly the questions this batch asked about.
            asked = [n for n in range(5, 14) if f"Essay question {n}?" in prompt]
            return _ai_response(
                {"question_evaluations": [_essay_evaluation(n) for n in asked]}
            )

        mock_execute.side_effect = respond

        result = AIProcessor()._grade_student_submission_impl(
            user=MagicMock(), rubric_json=questions, answer_json=answers
        )

        self.assertEqual(mock_execute.call_count, 3)  # 2 batches + summary

        # No batch prompt contains claimed-objective content.
        batch_prompts = [
            call.kwargs["user_prompt"][0]["text"]
            for call in mock_execute.call_args_list[:-1]
        ]
        for prompt in batch_prompts:
            self.assertNotIn("Objective question", prompt)

        # The summary call sees the MERGED evaluations, deterministic
        # provenance included.
        summary_prompt = mock_execute.call_args_list[-1].kwargs["user_prompt"][0][
            "text"
        ]
        self.assertIn('"graded_by": "deterministic"', summary_prompt)

        self.assertEqual(len(result["question_evaluations"]), 13)
        # 4×5 objective + 9×8 essay = 92 of 4×5 + 9×10 = 110.
        self.assertEqual(result["grading_summary"]["total_score"], 92)
        self.assertEqual(result["grading_summary"]["max_total_points"], 110)


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
)
class GradeEngineEndToEndTest(TestCase):
    """Through students.services.grade_engine with a real submission row:
    the deterministic result persists and completes the claim state
    machine, and an all-objective submission consumes zero credits."""

    def setUp(self):
        from django.utils import timezone

        from assignments.models import Assignment
        from classrooms.models import Course, Session
        from students.models import StudentSubmission
        from users.models import CustomUser, UserTypes

        self.teacher = CustomUser.objects.create_user(
            email=f"tier0-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        student = CustomUser.objects.create_user(
            email=f"tier0-student-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        session = Session.objects.create(name="S", teacher=self.teacher)
        course = Course.objects.create(name="C", teacher=self.teacher, session=session)
        self.assignment = Assignment.objects.create(
            title="All objective",
            course=course,
            questions=[_objective(1), _objective(2)],
        )
        self.submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=student,
            answers=[_answer(1, "Paris"), _answer(2, "Berlin")],
        )

    @patch.object(AIProcessor, "execute_graded_task")
    def test_all_objective_submission_grades_without_ai_or_credits(self, mock_execute):
        from billing.models import CreditUsageLog
        from students.models import GradingState
        from students.services import grade_engine

        result = grade_engine(self.teacher, self.submission)

        mock_execute.assert_not_called()
        self.assertEqual(CreditUsageLog.objects.count(), 0)

        self.submission.refresh_from_db()
        self.assertEqual(self.submission.grading_state, GradingState.DONE)
        self.assertIsNotNone(self.submission.graded_at)
        self.assertEqual(float(self.submission.score), 5.0)  # 1 right, 1 wrong
        self.assertEqual(self.submission.max_points, 10)
        self.assertEqual(float(self.submission.score_percentage), 50.0)
        self.assertEqual(self.submission.grading_confidence, 100)
        self.assertEqual(result.feedback["grading_model"], "deterministic")
