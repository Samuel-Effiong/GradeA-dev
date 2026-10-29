"""H-227: an ordinary refusal is not logged as an error.

`users.exceptions.custom_exception_handler` logged EVERY exception it saw at
ERROR with its full traceback, before DRF decided whether it could answer:
every 400, 401, 403, 404, 405 and 429 on every route, and the real crashes.
The traceback's last line is the exception's own text (a ValidationError's
whole field dict, which can quote what the user typed), and Sentry's logging
integration turns every ERROR record into an event.

The rule now (the Senior Manager's ruling, 2026-10-25): a refusal DRF
answers is logged at WARNING with its class and status only, no traceback and
no text (`billing.refusals.log_refusal`'s level and no-traceback rule). A
crash DRF does not answer keeps its ERROR "API Exception" record with the
traceback, unchanged: it is the only record of a real 500. Answers, status
codes and audit rows do not change.
"""

from unittest.mock import Mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.exceptions import (
    NotFound,
    PermissionDenied,
    Throttled,
    ValidationError,
)
from rest_framework.test import APITestCase

from audit.enums import AuditAction, AuditOutcome
from audit.models import AuditEvent
from audit.request_audit import INVALID_REQUEST
from users.exceptions import custom_exception_handler

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


class RefusalLoggingTest(TestCase):
    def context(self, path="/api/v1/thing", method="POST", user_pk=None):
        request = Mock()
        request.path = path
        request.method = method
        request.user = Mock(pk=user_pk)
        return {"view": Mock(), "request": request}

    def test_a_refusal_is_logged_at_warning_without_a_traceback(self):
        """R1."""
        with self.assertLogs("users.exceptions", level="WARNING") as logs:
            response = custom_exception_handler(
                NotFound("No such thing."), self.context()
            )

        self.assertEqual(response.status_code, 404)
        [record] = logs.records
        self.assertEqual(record.levelname, "WARNING")
        self.assertIsNone(record.exc_info)
        self.assertEqual(record.getMessage(), "API request refused: NotFound (404)")

    def test_a_refusal_log_carries_none_of_the_refusals_text(self):
        """R2: a serializer message can quote what the user typed."""
        exc = ValidationError({"email": ["probe-h227@example.com is already taken."]})

        with self.assertLogs("users.exceptions", level="WARNING") as logs:
            response = custom_exception_handler(exc, self.context())

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.data["email"], ["probe-h227@example.com is already taken."]
        )
        self.assertNotIn("probe-h227", "\n".join(logs.output))

    def test_a_refusal_writes_no_error_record(self):
        """R3: so it is no longer a Sentry error event."""
        with self.assertNoLogs("users.exceptions", level="ERROR"):
            response = custom_exception_handler(
                PermissionDenied("Not yours."), self.context()
            )

        self.assertEqual(response.status_code, 403)

    def test_a_crash_keeps_its_error_record_with_the_traceback(self):
        """K1 (control): the only record of a real 500 is unchanged."""
        with self.assertLogs("users.exceptions", level="ERROR") as logs:
            response = custom_exception_handler(
                KeyError("internal_detail_k1"),
                self.context(path="/api/v1/grade", method="POST", user_pk=42),
            )

        self.assertEqual(response.status_code, 500)
        [record] = logs.records
        self.assertEqual(record.levelname, "ERROR")
        self.assertEqual(record.getMessage(), "API Exception")
        self.assertIsNotNone(record.exc_info)
        # The request context, set through `extra`.
        self.assertEqual(record.__dict__["path"], "/api/v1/grade")
        self.assertEqual(record.__dict__["method"], "POST")
        self.assertEqual(record.__dict__["user"], 42)

    def test_a_throttle_keeps_its_answer(self):
        """K2 (control): the 429, its Retry-After and its coded envelope."""
        with self.assertLogs("users.exceptions", level="WARNING"):
            response = custom_exception_handler(Throttled(wait=30), self.context())

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "30")
        self.assertEqual(response.data["reason_code"], "RATE_LIMITED")


@override_settings(CACHES=LOCMEM)
class RefusalLoggingOnRealRoutesTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def test_a_refused_code_request_logs_no_error_and_answers_as_before(self):
        """R4: /auth/otp raises ParseError straight to the handler
        (users/views.py otp); the route is not audited (EXCLUDED_ROUTES)."""
        with self.assertNoLogs("users.exceptions", level="ERROR"):
            response = self.client.post(
                reverse("auth-otp"),
                {"email": "probe.h227@example.com", "otp_type": "NOT_A_TYPE"},
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Invalid OTP type", response.content.decode())
        self.assertFalse(AuditEvent.objects.exists())

    def test_a_refused_door_logs_no_error_and_keeps_its_audit_row(self):
        """R5: register/school-admin re-raises a ValidationError to the
        handler, and the audit middleware records the anonymous refusal."""
        with self.assertNoLogs("users.exceptions", level="ERROR"):
            response = self.client.post(
                reverse("auth-register-school-admin"), {}, format="json"
            )

        self.assertEqual(response.status_code, 400)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.AUTH_LOGIN)
        self.assertEqual(event.outcome, AuditOutcome.FAILURE)
        self.assertEqual(event.reason_code, INVALID_REQUEST)
