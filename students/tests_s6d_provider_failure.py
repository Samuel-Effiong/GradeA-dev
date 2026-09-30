"""
Epic A S6d, FR-A-06 #9 PROVIDER_FAILURE (08a §1, §5; F1).

When the grading or answer-extraction service cannot finish after its
retries, the failure is coded:

  - raised `from` the last attempt's error, so the cause stays on
    __cause__ (the grading retry used to raise a bare Exception, losing it);
  - the synchronous routes answer 503 with Retry-After and the coded
    envelope, and no provider text reaches the body (QA-ERR-03);
  - the message's credit clause says "The credits were refunded." when the
    run's refund scope held a charge, otherwise "No credits were charged."
    (F1; a failed call itself is never charged);
  - the audit event is PROVIDER when the cause is an infra failure, MODEL
    when it is not (unusable output), with reason_code PROVIDER_FAILURE.
"""

from unittest.mock import patch

import requests
from django.urls import reverse

from ai_processor.exceptions import (
    NOT_CHARGED,
    REFUNDED,
    ProviderFailureError,
    provider_failure,
)
from ai_processor.services import AIProcessor
from assignments.tests_cache_matrix_g3 import question
from audit.enums import AuditAction, ErrorClass, ReasonCode
from audit.models import AuditEvent
from billing.refunds import billing_refund_scope, record_billing_task_id
from students.tests_s6d_rubric_gate import RubricGateTestCase

GRADE = "ai_processor.services.AIProcessor.grade_student_submission"
EXECUTE = "ai_processor.services.AIProcessor.execute_graded_task"
PROVIDER_TEXT = "upstream said: 502 from openrouter"


def timeout(*args, **kwargs):
    raise requests.exceptions.Timeout(PROVIDER_TEXT)


def unusable(*args, **kwargs):
    raise ValueError(f"model output was not JSON ({PROVIDER_TEXT})")


class ProviderFailureTests(RubricGateTestCase):
    def setUp(self):
        super().setUp()
        self.assignment, self.submission = self.world([question()])

    def assert_coded_503(self, response):
        self.assertEqual(response.status_code, 503, response.content)
        self.assertEqual(response["Retry-After"], "30")
        envelope = response.json()["error"]["field_errors"]
        self.assertEqual(envelope["reason_code"], "PROVIDER_FAILURE")
        self.assertEqual(envelope["error_class"], ErrorClass.PROVIDER)
        self.assertTrue(envelope["retryable"])
        body = response.content.decode()
        for leak in (PROVIDER_TEXT, "Traceback", "Error during AI model", "Timeout"):
            self.assertNotIn(leak, body)
        return response.json()["message"]

    def test_sync_grade_answers_503_with_retry_after_and_no_provider_text(self):
        with patch(GRADE, side_effect=timeout) as grade:
            response = self.client.post(
                reverse("student-submission-grade", args=[self.submission.pk]),
                {},
                format="json",
            )

        self.assertEqual(grade.call_count, 3, "the retries still run")
        message = self.assert_coded_503(response)
        self.assertEqual(
            message, f"The grading service couldn't finish this item. {NOT_CHARGED}"
        )
        self.submission.refresh_from_db()
        self.assertIsNone(self.submission.graded_at)

    def test_the_grading_failure_keeps_its_cause(self):
        with patch(GRADE, side_effect=timeout):
            with self.assertRaises(ProviderFailureError) as caught:
                AIProcessor().extract_grade_with_retry(
                    self.teacher, [question()], self.submission.answers
                )
        self.assertIsInstance(caught.exception.__cause__, requests.exceptions.Timeout)
        self.assertIn("All 3 attempts failed", caught.exception.detail)
        self.assertNotIn(PROVIDER_TEXT, str(caught.exception))

    def test_a_sync_raw_text_edit_answers_503_too(self):
        """Answer extraction's retry raises the same coded failure."""
        with patch(EXECUTE, side_effect=timeout):
            response = self.client.patch(
                reverse("student-submission-detail", args=[self.submission.pk]),
                {"raw_input": "1. Photosynthesis"},
                format="json",
            )
        self.assert_coded_503(response)

    def test_async_grading_audits_provider_or_model_with_the_code(self):
        from assignments.tasks import grade_engine_async

        for failure, expected in (
            (timeout, ErrorClass.PROVIDER),
            (unusable, ErrorClass.MODEL),
        ):
            with self.subTest(failure=failure.__name__):
                # AuditEvent is append-only: assert on this run's event only.
                earlier = set(
                    AuditEvent.objects.filter(
                        action=AuditAction.GRADING_FAILED
                    ).values_list("id", flat=True)
                )
                with patch(GRADE, side_effect=failure):
                    outcome = grade_engine_async.apply(
                        args=(str(self.teacher.id), str(self.submission.id))
                    )
                self.assertTrue(outcome.failed())
                self.assertIsInstance(outcome.result, ProviderFailureError)
                event = AuditEvent.objects.exclude(id__in=earlier).get(
                    action=AuditAction.GRADING_FAILED
                )
                self.assertEqual(event.reason_code, ReasonCode.PROVIDER_FAILURE)
                self.assertEqual(event.error_class, expected)


class CreditClauseTests(RubricGateTestCase):
    """F1: the message tells the truth about the credits."""

    def test_no_charge_in_the_run_says_none_were_charged(self):
        with billing_refund_scope():
            error = provider_failure(RuntimeError("x"), 3)
        self.assertEqual(error.params, {"credit_clause": NOT_CHARGED})

    def test_a_charge_in_the_run_says_it_was_refunded(self):
        with patch("billing.refunds._refund_all"):
            with self.assertRaises(ProviderFailureError) as caught:
                with billing_refund_scope():
                    record_billing_task_id("charge-before-the-failure")
                    raise provider_failure(RuntimeError("x"), 3)
        self.assertEqual(caught.exception.params, {"credit_clause": REFUNDED})
        self.assertIn(REFUNDED, str(caught.exception))
