"""
Wiring tests for the answer-extraction hardening.

ai_processor/tests_answer_completeness.py covers the checking RULES in
isolation. This file covers the parts that only exist once the rules are
plugged into the pipeline, and that a pure-logic test cannot reach:

  * the gate runs INSIDE extract_answer_with_retry's loop, so an
    incomplete payload is re-asked rather than persisted;
  * the FINAL attempt degrades instead of destroying the submission;
  * both kill switches behave, and the schema one is loud when off;
  * _stamp_answer_provenance marks every grading path, and can never
    cost a student their grade.

Run with:
    python manage.py test ai_processor.tests_answer_extraction_gate
"""

from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from ai_processor.answer_completeness import MODE_LOG, MODE_OFF, MODE_STRICT
from ai_processor.extraction_schemas import (
    ANSWER_EXTRACTION_RESPONSE_SCHEMA,
    ANSWERED,
    BLANK,
    NOT_FOUND_IN_DOCUMENT,
)
from ai_processor.services import AIProcessor


class FakeAssignment:
    """Stands in for the Assignment model: only .questions is read."""

    __slots__ = ("questions",)

    def __init__(self, questions):
        self.questions = questions


QUESTIONS = [
    {"question_number": 1, "question_text": "Q1", "points": 10},
    {"question_number": 2, "question_text": "Q2", "points": 10},
]


def payload(*answers):
    return {"student_name": "Ada", "answers": list(answers)}


def answer(number, html="work", status=ANSWERED):
    return {
        "question_number": number,
        "answer_html": html,
        "answer_status": status,
    }


class ExtractionGateTest(SimpleTestCase):
    def setUp(self):
        self.processor = AIProcessor()
        self.assignment = FakeAssignment(QUESTIONS)

    def _run(self, side_effect, **kwargs):
        with patch.object(
            AIProcessor, "extract_answer_image", side_effect=side_effect
        ) as mocked:
            result = self.processor.extract_answer_with_retry(
                user=None,
                content=[],
                assignment="ctx",
                assignment_model=self.assignment,
                **kwargs,
            )
        return result, mocked

    def test_complete_payload_returns_on_the_first_attempt(self):
        result, mocked = self._run([payload(answer(1), answer(2))])
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(len(result["answers"]), 2)

    def test_incomplete_payload_is_retried(self):
        # THE POINT OF PUTTING THE GATE INSIDE THE LOOP: attempt 1 loses
        # question 2, attempt 2 has it, and the good payload is what the
        # caller sees.
        result, mocked = self._run([payload(answer(1)), payload(answer(1), answer(2))])
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(
            [a["answer_status"] for a in result["answers"]], [ANSWERED, ANSWERED]
        )

    def test_persistently_incomplete_payload_degrades_on_the_final_attempt(self):
        # Never destroy the submission: a flagged grade beats no grade.
        result, mocked = self._run([payload(answer(1))] * 3)
        self.assertEqual(mocked.call_count, 3)
        self.assertEqual(
            [a["answer_status"] for a in result["answers"]],
            [ANSWERED, NOT_FOUND_IN_DOCUMENT],
        )

    def test_degraded_result_never_invents_an_answer(self):
        result, _ = self._run([payload(answer(1))] * 3)
        self.assertEqual(result["answers"][1]["answer_html"], "")

    def test_genuine_blanks_never_trigger_a_retry(self):
        # A student who skipped question 2 must cost exactly one call.
        result, mocked = self._run(
            [payload(answer(1), answer(2, html="", status=BLANK))]
        )
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(result["answers"][1]["answer_status"], BLANK)

    def test_max_retries_is_honoured(self):
        _, mocked = self._run([payload(answer(1))] * 2, max_retries=2)
        self.assertEqual(mocked.call_count, 2)

    def test_single_attempt_degrades_immediately(self):
        # max_retries=1 means the first attempt IS the final attempt.
        result, mocked = self._run([payload(answer(1))], max_retries=1)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(result["answers"][1]["answer_status"], NOT_FOUND_IN_DOCUMENT)

    def test_transport_errors_still_retry_as_before(self):
        result, mocked = self._run(
            [RuntimeError("blip"), payload(answer(1), answer(2))]
        )
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(len(result["answers"]), 2)

    def test_exhausted_transport_errors_still_raise(self):
        with self.assertRaises(Exception) as ctx:
            self._run([RuntimeError("blip")] * 3)
        self.assertIn("All 3 attempts failed", str(ctx.exception))


class GateDisabledTest(SimpleTestCase):
    """The gate must not fire where it has nothing to check against."""

    def setUp(self):
        self.processor = AIProcessor()

    def _run(self, assignment_model, side_effect):
        with patch.object(
            AIProcessor, "extract_answer_image", side_effect=side_effect
        ) as mocked:
            result = self.processor.extract_answer_with_retry(
                user=None,
                content=[],
                assignment="ctx",
                assignment_model=assignment_model,
            )
        return result, mocked

    def test_no_assignment_model_skips_the_gate(self):
        result, mocked = self._run(None, [payload(answer(1))])
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(len(result["answers"]), 1)

    def test_assignment_without_questions_skips_the_gate(self):
        result, mocked = self._run(FakeAssignment([]), [payload(answer(1))])
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(len(result["answers"]), 1)

    def test_assignment_with_null_questions_skips_the_gate(self):
        # Assignment.questions is nullable and IS null before extraction.
        result, mocked = self._run(FakeAssignment(None), [payload(answer(1))])
        self.assertEqual(len(result["answers"]), 1)

    @override_settings(ANSWER_COMPLETENESS_ENFORCEMENT=MODE_OFF)
    def test_off_setting_skips_the_gate_entirely(self):
        result, mocked = self._run(FakeAssignment(QUESTIONS), [payload(answer(1))])
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(len(result["answers"]), 1)

    @override_settings(ANSWER_COMPLETENESS_ENFORCEMENT=MODE_LOG)
    def test_log_setting_repairs_without_retrying(self):
        result, mocked = self._run(FakeAssignment(QUESTIONS), [payload(answer(1))])
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(result["answers"][1]["answer_status"], NOT_FOUND_IN_DOCUMENT)

    def test_non_dict_result_passes_through_untouched(self):
        result, _ = self._run(FakeAssignment(QUESTIONS), [["not", "a", "dict"]])
        self.assertEqual(result, ["not", "a", "dict"])


class ModeResolutionTest(SimpleTestCase):
    @override_settings(ANSWER_COMPLETENESS_ENFORCEMENT=MODE_LOG)
    def test_valid_mode_is_returned(self):
        self.assertEqual(AIProcessor._answer_completeness_mode(), MODE_LOG)

    @override_settings(ANSWER_COMPLETENESS_ENFORCEMENT="nonsense")
    def test_unrecognised_mode_fails_closed_to_strict(self):
        # Fail CLOSED: a typo'd env var must not silently disable a safety
        # check. Matches _snap/_severity's "never downgrade what we can't
        # measure" rule.
        with self.assertLogs("ai_processor.services", "WARNING"):
            self.assertEqual(AIProcessor._answer_completeness_mode(), MODE_STRICT)


class SchemaSwitchTest(SimpleTestCase):
    def test_schema_is_on_by_default(self):
        self.assertIs(
            AIProcessor._answer_extraction_schema(), ANSWER_EXTRACTION_RESPONSE_SCHEMA
        )

    @override_settings(ANSWER_EXTRACTION_SCHEMA_ENABLED=False)
    def test_disabling_the_schema_returns_none(self):
        with self.assertLogs("ai_processor.services", "WARNING"):
            self.assertIsNone(AIProcessor._answer_extraction_schema())

    @override_settings(ANSWER_EXTRACTION_SCHEMA_ENABLED=False)
    def test_disabling_the_schema_is_loud(self):
        # The whole reason this differs from _grading_response_schema.
        with self.assertLogs("ai_processor.services", "WARNING") as logs:
            AIProcessor._answer_extraction_schema()
        self.assertIn("ANSWER_EXTRACTION_SCHEMA_ENABLED", logs.output[0])


class AnswerStatusReachesGraderTest(SimpleTestCase):
    """
    The extraction -> grading contract.

    A `NOT_FOUND_IN_DOCUMENT` question scores 0 exactly like a blank one,
    and that is correct. What must NOT be the same is what the student is
    TOLD: telling someone they skipped a question they actually answered
    is an accusation they cannot contest, because they have no way to know
    it was our extraction that failed rather than their memory.

    The grader can only word that differently if it can SEE the status, so
    these tests pin that the field survives into the prompt payload and
    that the prompt explains what to do with it.
    """

    def test_answer_status_survives_into_the_batched_prompt(self):
        processor = AIProcessor()
        pairs = processor._pair_question_with_answers(
            QUESTIONS,
            [answer(1), answer(2, html="", status=NOT_FOUND_IN_DOCUMENT)],
        )
        payload = [p["answer"] for p in pairs]
        self.assertEqual(
            [a.get("answer_status") for a in payload],
            [ANSWERED, NOT_FOUND_IN_DOCUMENT],
        )

    def test_fabricated_placeholder_carries_not_found_not_blank(self):
        # The placeholder is invented by US because extraction returned
        # nothing. Labelling it BLANK would assert the student skipped it,
        # which is exactly the claim we have no evidence for.
        processor = AIProcessor()
        pairs = processor._pair_question_with_answers(QUESTIONS, [answer(1)])
        self.assertEqual(pairs[1]["answer"]["answer_status"], NOT_FOUND_IN_DOCUMENT)
        self.assertEqual(pairs[1]["answer"]["answer_html"], "")

    def test_grading_prompt_documents_every_status(self):
        prompt = open("ai_processor/GRADING_ASSIGNMENT_PROMPT_5.txt").read()
        for status in (ANSWERED, BLANK, "ILLEGIBLE", NOT_FOUND_IN_DOCUMENT):
            with self.subTest(status=status):
                self.assertIn(status, prompt)

    def test_grading_prompt_forbids_blaming_the_student_for_a_lost_answer(self):
        prompt = open("ai_processor/GRADING_ASSIGNMENT_PROMPT_5.txt").read()
        section = prompt.split("NOT_FOUND_IN_DOCUMENT")[-1].lower()
        self.assertIn("not", section)
        self.assertIn("skipped", section)


class ProvenanceStampingTest(SimpleTestCase):
    """_stamp_answer_provenance — the silent-zero marker."""

    def setUp(self):
        self.processor = AIProcessor()

    def stamp(self, evaluations, answers, questions=QUESTIONS):
        return self.processor._stamp_answer_provenance(
            {"question_evaluations": evaluations}, questions, answers
        )

    def test_answered_questions_are_marked_answered(self):
        result = self.stamp(
            [{"question_number": 1}, {"question_number": 2}],
            [answer(1), answer(2)],
        )
        self.assertEqual(
            [e["answer_status"] for e in result["question_evaluations"]],
            [ANSWERED, ANSWERED],
        )
        self.assertEqual(result["answers_not_found"], [])

    def test_missing_answer_is_marked_and_collected(self):
        result = self.stamp(
            [{"question_number": 1}, {"question_number": 2, "score_awarded": 0}],
            [answer(1)],
        )
        self.assertEqual(
            result["question_evaluations"][1]["answer_status"], NOT_FOUND_IN_DOCUMENT
        )
        self.assertEqual(len(result["answers_not_found"]), 1)
        self.assertEqual(result["answers_not_found"][0]["question_number"], 2)

    def test_genuine_blank_is_not_collected_for_review(self):
        # THE DISTINCTION THAT MATTERS. Both score 0; only one is a fault.
        result = self.stamp(
            [{"question_number": 1}, {"question_number": 2}],
            [answer(1), answer(2, html="", status=BLANK)],
        )
        self.assertEqual(result["question_evaluations"][1]["answer_status"], BLANK)
        self.assertEqual(result["answers_not_found"], [])

    def test_status_is_inferred_when_the_payload_carries_none(self):
        # Schema-disabled path: infer, and never guess NOT_FOUND.
        result = self.stamp(
            [{"question_number": 1}, {"question_number": 2}],
            [
                {"question_number": 1, "answer_html": "work"},
                {"question_number": 2, "answer_html": ""},
            ],
        )
        self.assertEqual(
            [e["answer_status"] for e in result["question_evaluations"]],
            [ANSWERED, BLANK],
        )
        self.assertEqual(result["answers_not_found"], [])

    def test_string_question_numbers_match(self):
        result = self.stamp([{"question_number": "1"}], [answer(1)])
        self.assertEqual(result["question_evaluations"][0]["answer_status"], ANSWERED)

    def test_checked_marker_is_always_set(self):
        # Lets a consumer tell "nothing missing" from "predates the check".
        result = self.stamp([{"question_number": 1}], [answer(1)])
        self.assertTrue(result["answer_provenance_checked"])

    def test_json_string_inputs_are_accepted(self):
        import json

        result = self.processor._stamp_answer_provenance(
            {"question_evaluations": [{"question_number": 1}]},
            json.dumps(QUESTIONS),
            json.dumps([answer(1)]),
        )
        self.assertEqual(result["question_evaluations"][0]["answer_status"], ANSWERED)

    def test_non_dict_result_passes_through(self):
        self.assertIsNone(self.processor._stamp_answer_provenance(None, [], []))

    def test_malformed_inputs_never_raise(self):
        for questions, answers in (
            (None, None),
            ("not json", "not json"),
            ({}, {}),
            (QUESTIONS, ["junk", None, 42]),
        ):
            with self.subTest(questions=questions, answers=answers):
                result = self.processor._stamp_answer_provenance(
                    {"question_evaluations": [{"question_number": 1}]},
                    questions,
                    answers,
                )
                self.assertIsInstance(result, dict)

    def test_stamping_never_alters_a_score(self):
        # The safety property: provenance bookkeeping is additive only.
        evaluations = [{"question_number": 1, "score_awarded": 7, "max_points": 10}]
        result = self.stamp(evaluations, [])
        self.assertEqual(result["question_evaluations"][0]["score_awarded"], 7)

    def test_internal_failure_is_swallowed_and_the_grade_survives(self):
        graded = {"question_evaluations": [{"question_number": 1, "score_awarded": 9}]}
        with patch.object(
            AIProcessor, "_coerce_json_list", side_effect=RuntimeError("boom")
        ):
            with self.assertLogs("ai_processor.services", "ERROR"):
                result = self.processor._stamp_answer_provenance(graded, [], [])
        self.assertEqual(result["question_evaluations"][0]["score_awarded"], 9)
