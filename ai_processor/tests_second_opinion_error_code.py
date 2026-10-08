"""
H-128: a failed second opinion saves a fixed code, never the error's text.

`result["second_opinion"]["error"]` used to be `str(e)`, and the result is
saved whole as the submission's feedback. The text could be:

  * the teacher's credit balance ("Task requires ~N credits, but you only
    have M credits");
  * the reason the teacher's plan was refused;
  * the AI provider's whole error body: the provider library's message is
    "Error code: <status> - <the response body>".

Now the saved value is one of a short list of codes, and the text goes to
the log, which is scrubbed like any log line. Rows saved before this keep
whatever text they hold; H-127's student projection is what keeps that from
students.

Driven through the real grading path with the harness of
ai_processor/tests_second_opinion_pipeline.py.

Run with:
    python manage.py test ai_processor.tests_second_opinion_error_code
"""

import json
from unittest.mock import MagicMock, patch

import httpx
import openai
from django.test import SimpleTestCase, override_settings

from ai_processor.services import AIProcessor
from ai_processor.tests_second_opinion_pipeline import (
    B_MODEL,
    SECOND_OPINION_SETTINGS,
    _a_payload,
    _ai_response,
    _answer,
    _essay,
    _evaluation,
    _is_b_call,
)
from billing.access_control import AIFeatureNotAvailableError
from billing.errors import InsufficientCreditsError

#: The whole list. A new code is a decision, so the test names them.
CODES = {
    "out_of_credits",
    "access_refused",
    "provider_error",
    "evidence_rejected",
    "incomplete_response",
    "other",
}

RAW = "RAW-TEXT-THAT-MUST-NOT-BE-SAVED"


def provider_error():
    """What the provider library raises for a 429, built as it builds it:
    the message carries the whole response body."""
    body = {"error": {"message": f"upstream said {RAW}", "code": 429}}
    request = httpx.Request("POST", "https://provider.test/v1/chat/completions")
    return openai.RateLimitError(
        f"Error code: 429 - {body}",
        response=httpx.Response(429, request=request),
        body=body,
    )


@override_settings(**SECOND_OPINION_SETTINGS)
class SecondOpinionErrorCodeTest(SimpleTestCase):
    def setUp(self):
        self.processor = AIProcessor()
        self.questions = [_essay(1)]
        self.answers = [_answer(1)]

    def run_with_second_grader(self, second_grader):
        """Grade once; the first grader answers, with low confidence so a
        second opinion is asked for, and `second_grader` answers that."""

        def respond(**kwargs):
            if _is_b_call(kwargs):
                return second_grader()
            return _ai_response(_a_payload([_evaluation(1)], confidence=50))

        with patch.object(AIProcessor, "execute_graded_task", side_effect=respond):
            with self.assertLogs("ai_processor.services", level="INFO") as logs:
                result = self.processor._grade_student_submission_impl(
                    user=MagicMock(),
                    rubric_json=self.questions,
                    answer_json=self.answers,
                )
        logged = "\n".join(
            record.getMessage()
            + "\n"
            + (record.exc_text or "")
            + ("\n" + str(record.exc_info[1]) if record.exc_info else "")
            for record in logs.records
        )
        return result, logged

    def assert_code(self, result, code):
        block = result["second_opinion"]
        self.assertIn(code, CODES)
        self.assertEqual(block["error"], code)
        # Nothing of the error's own text anywhere in what is saved.
        self.assertNotIn(RAW, json.dumps(result, default=str))
        # The first grader's grade stands, as before.
        self.assertEqual(result["grading_summary"]["total_score"], 8)

    def raising(self, exc):
        def second_grader():
            raise exc

        return second_grader

    def test_out_of_credits(self):
        result, logged = self.run_with_second_grader(
            self.raising(
                InsufficientCreditsError(
                    f"Task requires ~21000 credits, but you only have {RAW} credits."
                )
            )
        )
        self.assert_code(result, "out_of_credits")
        # Still flagged for the teacher, exactly as before.
        self.assertTrue(result["second_opinion"]["needs_review"])
        self.assertEqual(
            result["second_opinion"]["skipped_reason"], "insufficient_credits"
        )
        # The text is in the log, not lost.
        self.assertIn(RAW, logged)

    def test_access_refused(self):
        result, logged = self.run_with_second_grader(
            self.raising(AIFeatureNotAvailableError(f"AI access denied: {RAW}"))
        )
        self.assert_code(result, "access_refused")
        self.assertIn(RAW, logged)

    def test_provider_error(self):
        result, logged = self.run_with_second_grader(self.raising(provider_error()))
        self.assert_code(result, "provider_error")
        self.assertIn(RAW, logged)

    def test_evidence_rejected(self):
        def second_grader():
            return _ai_response(
                {
                    "question_evaluations": [
                        {**_evaluation(1), "evidence_quotes": ["invented"]}
                    ]
                },
                model=B_MODEL,
            )

        result, _ = self.run_with_second_grader(second_grader)
        self.assert_code(result, "evidence_rejected")

    def test_incomplete_response(self):
        def second_grader():
            # An evaluation for a question that was not asked: dropped, so
            # the one that was asked is missing.
            return _ai_response(
                {"question_evaluations": [_evaluation(99)]}, model=B_MODEL
            )

        result, _ = self.run_with_second_grader(second_grader)
        self.assert_code(result, "incomplete_response")

    def test_anything_else(self):
        result, logged = self.run_with_second_grader(
            self.raising(RuntimeError(f"second model exploded: {RAW}"))
        )
        self.assert_code(result, "other")
        self.assertNotIn("needs_review", result["second_opinion"])
        self.assertIn(RAW, logged)
