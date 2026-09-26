"""Extraction status survives grading and reaches the review queue.

Answer extraction cannot be validated only in isolation: the distinction
between BLANK and NOT_FOUND_IN_DOCUMENT is worth nothing unless it is still
there when the grade is written and the review queue is built. These tests
take benchmark scenarios through the whole chain:

    generated PDF
      -> production rasterizer
      -> answer extraction (deterministic model), against a REAL Assignment
      -> grading (single-pass path, deterministic grading model)
      -> _stamp_answer_provenance
      -> _populate_and_save_grade (the submission row and its review flags)

WHAT IS AND IS NOT FAKED

Two model calls are faked: extraction and grading. Nothing else is.

The fake grader scores an answered question full marks and anything else
zero. The assertions do NOT rely on that choice for the safety property:
`answer_status` on each evaluation and `answers_not_found` are stamped by
_stamp_answer_provenance from the EXTRACTION payload, independently of
anything the grader returns, and the review flags are computed by
_populate_and_save_grade from those. A regression anywhere in that chain -
a status collapsed in the merge, dropped in pairing, or ignored by the
review builder - fails here even though the grader is fake.
"""

import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from ai_processor import services
from ai_processor.benchmark.answers import SCENARIOS_BY_ID
from ai_processor.benchmark.answers.harness import (
    describe,
    full_check,
    run_scenario,
    strip_markup,
)
from ai_processor.benchmark.answers.provider import _Response
from ai_processor.extraction_schemas import ANSWERED, REVIEW_REQUIRED_STATUSES
from ai_processor.grading_schemas import GRADING_SINGLE_PASS_RESPONSE_SCHEMA
from assignments.models import Assignment
from classrooms.models import Course, Session
from students.models import StudentSubmission
from students.services import _populate_and_save_grade
from users.models import CustomUser, UserTypes

#: Every benchmark question is declared with 5 points (scenarios._questions_for).
POINTS = 5

#: A single-call mixed page, a blank on the chunk seam, the blank-after-
#: chunk-1 defect, a not-found tail, the last-chunk BLANK quirk, and an
#: answer split across chunks.
INTEGRATION_SCENARIOS = ("AE-300", "AE-202", "AE-601", "AE-305", "AE-800", "AE-205")


def fake_grader(questions, extracted_answers, prompts):
    """
    A single-pass grading response: full marks where the extraction has an
    answer, zero elsewhere. Records every prompt it is sent in `prompts`.
    """
    by_number = {str(a.get("question_number")): a for a in extracted_answers}

    def grade(**kwargs):
        if kwargs.get("response_schema") is not GRADING_SINGLE_PASS_RESPONSE_SCHEMA:
            raise AssertionError("expected a single-pass grading call")
        prompts.append(
            " ".join(
                block.get("text", "")
                for block in (kwargs.get("user_prompt") or [])
                if isinstance(block, dict)
            )
        )
        evaluations = []
        for question in questions:
            answer = by_number.get(str(question["question_number"]), {})
            text = strip_markup(answer.get("answer_html") or "")
            answered = answer.get("answer_status") == ANSWERED and bool(text)
            evaluations.append(
                {
                    "question_number": question["question_number"],
                    "question_text": question["question_text"],
                    "question_type": question["question_type"],
                    "max_points": POINTS,
                    "student_answer": text,
                    "model_answer": question.get("model_answer", ""),
                    "evidence_quotes": [text] if answered else [],
                    "evaluation_rationale": "benchmark grader",
                    "level_decision": "clear",
                    "level_achieved": "correct" if answered else "not_attempted",
                    "score_awarded": POINTS if answered else 0,
                    "strengths": [],
                    "weaknesses": [],
                    "improvement_suggestions": [],
                    "feedback_for_student": "",
                    "flag_for_review": None,
                }
            )
        return _Response(
            json.dumps(
                {
                    "question_evaluations": evaluations,
                    # Deliberately wrong: the pipeline must recompute totals
                    # rather than trust the model's arithmetic.
                    "grading_summary": {
                        "total_score": 999,
                        "max_total_points": 1,
                        "percentage": 1,
                    },
                    "score_calculation_verification": {},
                    "overall_performance_analysis": {},
                    "recommendations": {},
                    "grading_confidence": 90,
                }
            )
        )

    return grade


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False,
    GRADING_ANSWER_CACHE_ENABLED=False,
    GRADING_EVIDENCE_ENFORCEMENT="log",
    GRADING_CUSTOM_INSTRUCTIONS_ENABLED=False,
)
class ExtractionThroughGradingTest(TestCase):
    maxDiff = None

    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="answer-benchmark-grading-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.student = CustomUser.objects.create_user(
            email="answer-benchmark-grading-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        session = Session.objects.create(name="S", teacher=self.teacher)
        self.course = Course.objects.create(
            name="C", teacher=self.teacher, session=session
        )

    def extract_grade_and_persist(self, scenario):
        questions = list(scenario.questions)
        assignment = Assignment.objects.create(
            title=scenario.name, course=self.course, questions=questions
        )

        run = run_scenario(scenario, assignment_model=assignment)
        problems = full_check(run, scenario)
        self.assertEqual(problems, [], describe(run, scenario, problems))
        # full_check above has already failed the test if extraction raised.
        answers = (run.result or {})["answers"]

        prompts = []
        grader = services.AIProcessor.__new__(services.AIProcessor)
        with patch.object(
            grader, "execute_graded_task", fake_grader(questions, answers, prompts)
        ):
            grading = grader.extract_grade_with_retry(
                self.teacher,
                json.dumps(questions),
                answers,
                assignment_model=assignment,
                max_retries=1,
            )

        submission = StudentSubmission.objects.create(
            assignment=assignment, student=self.student, answers=answers
        )
        _populate_and_save_grade(submission, grading, None)
        submission.refresh_from_db()
        return run, grading, submission, prompts

    def test_status_score_and_review_agree_end_to_end(self):
        for scenario_id in INTEGRATION_SCENARIOS:
            scenario = SCENARIOS_BY_ID[scenario_id]
            with self.subTest(scenario=scenario_id):
                run, grading, submission, prompts = self.extract_grade_and_persist(
                    scenario
                )
                expected = {e.question: e.status for e in scenario.expectations}
                needs_review = {
                    q
                    for q, status in expected.items()
                    if status in REVIEW_REQUIRED_STATUSES
                }
                answered = [e for e in scenario.expectations if e.status == ANSWERED]

                # 1. Every status reaches the graded evaluation unchanged:
                #    a BLANK is not promoted to NOT_FOUND, and a NOT_FOUND
                #    is not quietly collapsed into a BLANK.
                graded = {
                    str(ev["question_number"]): ev["answer_status"]
                    for ev in grading["question_evaluations"]
                }
                self.assertEqual(graded, expected)

                # 2. Exactly the questions we do not have the work for are
                #    marked for review - and a genuine blank is not.
                self.assertEqual(
                    {str(m["question_number"]) for m in grading["answers_not_found"]},
                    needs_review,
                )

                # 3. The grade is consistent with the extracted state, and
                #    recomputed rather than taken from the model.
                summary = grading["grading_summary"]
                self.assertEqual(summary["total_score"], POINTS * len(answered))
                self.assertEqual(summary["max_total_points"], POINTS * len(expected))

                # 4. The persisted submission carries the same flags.
                self.assertEqual(submission.needs_review, bool(needs_review))
                self.assertEqual(
                    {
                        str(reason["question_number"])
                        for reason in (submission.review_reasons or [])
                        if reason.get("type") == "answer_not_found"
                    },
                    needs_review,
                )
                if needs_review:
                    self.assertEqual(submission.review_tier, "critical")

                # 5. Every answer the student wrote reached the grader -
                #    including both halves of an answer split across chunks.
                self.assertEqual(len(prompts), 1)
                for expectation in answered:
                    self.assertIn(expectation.answer, prompts[0])
                    for _page, fragment in expectation.fragments:
                        self.assertIn(fragment, prompts[0])
