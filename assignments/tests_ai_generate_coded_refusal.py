"""The AI assignment-generation chat refuses with the coded envelope.

QA (beta, 2026-11-26): a teacher at 0 credits using "Create with AI" got a
402 holding only the message: no reason_code, error_class, remediation,
retryable, params, reference or legacy `code`, which every other credit
refusal carries (AutoGrader.reason_codes.coded_response through
billing.refusals.refusal_response). The view caught both refusals itself
and built the body by hand. The upload door's two 402s
(assignments.upload_door) were hand-built the same way.

The sentence each user reads does not change: these tests pin it beside the
new keys. Read after the renderer (users.renderers.APIJSONRenderer), as the
client reads it.
"""

from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from AutoGrader.reason_codes import (
    INSUFFICIENT_CREDITS_INDIVIDUAL_MESSAGE,
    INSUFFICIENT_CREDITS_MESSAGE,
)
from billing.access_control import AIFeatureNotAvailableError
from billing.errors import InsufficientCreditsError
from classrooms.models import Course, Session
from users.models import CustomUser, UserTypes

GENERATE = "assignments.views.ai_processor.generate_assignment_from_prompt_with_retry"
PLAN_TEXT = "AI access denied: plan does not include this feature"
SCHOOL_FIX = "Top up credits, or ask your school administrator, then try again."
ENVELOPE_KEYS = {
    "error",
    "reason_code",
    "error_class",
    "remediation",
    "retryable",
    "params",
    "reference",
}


class TheGenerationChatRefusesWithTheCodedEnvelope(APITestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="gen-refusal@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.course = Course.objects.create(
            name="Biology",
            teacher=self.teacher,
            session=Session.objects.create(name="Term", teacher=self.teacher),
        )
        self.client = APIClient(raise_request_exception=False)
        self.client.force_authenticate(user=self.teacher)

    def refused(self, error):
        with patch(GENERATE, side_effect=error):
            response = self.client.post(
                reverse("assignment-generate", kwargs={"course_id": self.course.id}),
                {"prompt": "Create a one-question biology quiz."},
                format="json",
            )
        body = response.json()
        return response, body, body["error"]["field_errors"]

    def assertCoded(self, response, envelope, reason_code, legacy, message, fix):
        """Every key the other coded endpoints send (owner's target shape,
        2026-11-26), after the renderer."""
        body = response.json()
        self.assertEqual(set(envelope), ENVELOPE_KEYS | {"code"}, envelope)
        self.assertEqual(envelope["reason_code"], reason_code)
        self.assertEqual(envelope["code"], legacy)
        self.assertEqual(envelope["error_class"], "USER")
        self.assertIs(envelope["retryable"], False)
        self.assertEqual(envelope["params"], {})
        self.assertEqual(envelope["remediation"], fix)
        self.assertEqual(envelope["reference"], response["X-Request-ID"])
        # The sentence shown, before and after the fix.
        self.assertEqual(envelope["error"], message)
        self.assertEqual(body["message"], message)
        self.assertIs(body["success"], False)

    def test_a_credit_refusal_is_coded_402(self):
        response, body, envelope = self.refused(
            InsufficientCreditsError("Refill your wallet to continue")
        )

        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertCoded(
            response,
            envelope,
            "INSUFFICIENT_CREDITS",
            "insufficient_credits",
            INSUFFICIENT_CREDITS_MESSAGE,
            SCHOOL_FIX,
        )

    def test_an_individual_teachers_credit_refusal_keeps_its_sentence(self):
        response, body, envelope = self.refused(
            InsufficientCreditsError("Refill your wallet to continue", individual=True)
        )

        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertCoded(
            response,
            envelope,
            "INSUFFICIENT_CREDITS",
            "insufficient_credits",
            INSUFFICIENT_CREDITS_INDIVIDUAL_MESSAGE,
            "Top up credits, then try again.",
        )

    def test_the_credit_refusals_own_text_is_never_shown(self):
        """Control: the exception's text can carry balance detail."""
        _, body, envelope = self.refused(
            InsufficientCreditsError("Upload needs ~60000 credits, 12 left")
        )

        self.assertNotIn("60000", str(body))
        self.assertEqual(envelope["error"], INSUFFICIENT_CREDITS_MESSAGE)

    def test_a_plan_refusal_is_coded_403(self):
        response, body, envelope = self.refused(AIFeatureNotAvailableError(PLAN_TEXT))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertCoded(
            response,
            envelope,
            "AI_FEATURE_NOT_AVAILABLE",
            "ai_feature_not_available",
            PLAN_TEXT,
            "Upgrade your plan, or ask your school administrator to enable it.",
        )


class TheUploadDoorRefusesWithTheCodedEnvelope(APITestCase):
    """assignments.upload_door's two 402s: same refusal class, same
    envelope. A teacher keeps the credit sentence and its remediation; a
    student keeps the one student sentence with no remediation, as
    students.views._failure_response does (H-216)."""

    def user(self, user_type, email):
        return CustomUser.objects.create_user(email=email, user_type=user_type)

    def assertDoorCoded(self, response, message):
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
        self.assertTrue(ENVELOPE_KEYS <= set(response.data), response.data)
        self.assertEqual(response.data["reason_code"], "INSUFFICIENT_CREDITS")
        self.assertEqual(response.data["code"], "insufficient_credits")
        self.assertIs(response.data["retryable"], False)
        self.assertEqual(response.data["error"], message)

    def test_the_batch_refusal(self):
        from assignments.upload_door import batch_refusal_response

        teacher = self.user(UserTypes.TEACHER, "door-batch@example.com")
        with patch("assignments.upload_door.is_individual_teacher", return_value=False):
            response = batch_refusal_response(teacher)

        self.assertDoorCoded(response, INSUFFICIENT_CREDITS_MESSAGE)
        self.assertTrue(response.data["remediation"])

    def test_the_batch_refusal_for_an_individual_teacher(self):
        from assignments.upload_door import batch_refusal_response

        teacher = self.user(UserTypes.TEACHER, "door-batch-i@example.com")
        with patch("assignments.upload_door.is_individual_teacher", return_value=True):
            response = batch_refusal_response(teacher)

        self.assertDoorCoded(response, INSUFFICIENT_CREDITS_INDIVIDUAL_MESSAGE)

    def test_a_teachers_single_upload_refusal(self):
        from assignments.upload_door import _refusal_response

        teacher = self.user(UserTypes.TEACHER, "door-one@example.com")
        response = _refusal_response(
            teacher, InsufficientCreditsError("needs ~60000, 12 left")
        )

        self.assertDoorCoded(response, INSUFFICIENT_CREDITS_MESSAGE)
        self.assertNotIn("60000", str(response.data))

    def test_a_students_upload_refusal_keeps_the_student_sentence(self):
        from assignments.upload_door import _refusal_response
        from students.exceptions import STUDENT_UPLOAD_NOT_PROCESSED

        student = self.user(UserTypes.STUDENT, "door-s@example.com")
        response = _refusal_response(
            student, InsufficientCreditsError("needs ~60000, 12 left")
        )

        self.assertDoorCoded(response, STUDENT_UPLOAD_NOT_PROCESSED)
        self.assertIsNone(response.data["remediation"])
        self.assertNotIn("60000", str(response.data))
