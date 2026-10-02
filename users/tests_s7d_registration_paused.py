"""
Epic A S7d, catalogue section B (H-68): the student-registration pause is
coded REGISTRATION_PAUSED.

When register_student's global failure budget (H-47) is spent, the 429 keeps
its text and Retry-After and gains `code` and the coded envelope, so a client
can tell the pause from the per-network RegisterThrottle, whose 429 carries
neither. The renew-student-token door, which spends the same budget, keeps
its own answer (SM ruling Q6: it needs approved wording first).
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from audit.enums import ReasonCode
from AutoGrader.reason_codes import REASON_CODES
from users.models import UserTypes
from users.throttling import RegisterThrottle

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CALLER_PASSWORD = "Caller-Chosen-Pw-78"  # pragma: allowlist secret
SPEC = REASON_CODES[ReasonCode.REGISTRATION_PAUSED]


@override_settings(CACHES=LOCMEM_CACHE, REGISTER_STUDENT_GLOBAL_FAILURE_LIMIT=3)
class RegistrationPausedTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def without_per_network_throttles(self):
        from classrooms.views import CourseViewSet
        from users.views import AuthViewSet

        for viewset in (AuthViewSet, CourseViewSet):
            patcher = patch.object(viewset, "get_throttles", return_value=[])
            patcher.start()
            self.addCleanup(patcher.stop)

    def post(self, token):
        return self.client.post(
            reverse("auth-register-student"),
            {
                "token": token,
                "first_name": "Caller",
                "last_name": "Chosen",
                "password": CALLER_PASSWORD,
            },
            format="json",
        )

    def spend_the_budget(self):
        for guess in ("900001", "900002", "900003"):
            self.assertEqual(self.post(guess).status_code, 400)

    def test_the_pause_is_coded_and_keeps_its_text_and_retry_after(self):
        self.without_per_network_throttles()
        self.spend_the_budget()

        response = self.post("111111")

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertTrue(1 <= int(response["Retry-After"]) <= 3600)
        body = response.json()
        envelope = body["error"]["field_errors"]
        self.assertEqual(envelope["reason_code"], "REGISTRATION_PAUSED")
        self.assertEqual(envelope["code"], "REGISTRATION_PAUSED")
        self.assertEqual(envelope["error_class"], "USER")
        self.assertIs(envelope["retryable"], True)
        self.assertEqual(envelope["remediation"], SPEC.remediation)
        self.assertEqual(envelope["reference"], response["X-Request-ID"])
        # Today's text, unchanged; DRF still appends when to come back.
        self.assertTrue(body["message"].startswith(SPEC.message), body["message"])
        self.assertIn("Expected available in", body["message"])

    def test_the_per_network_throttle_is_not_mistaken_for_the_pause(self):
        """The two 429s must be told apart: the rate limit carries no code."""
        with patch.object(RegisterThrottle, "rate", "1/min", create=True):
            self.post("900001")
            response = self.post("900002")

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertNotIn("REGISTRATION_PAUSED", response.content.decode())

    def test_the_renew_door_keeps_its_own_answer(self):
        """SM ruling Q6: renew-student-token spends the same budget but is
        not coded until its wording is approved."""
        self.without_per_network_throttles()
        self.spend_the_budget()

        response = self.client.post(
            reverse("course-renew-activation-token"),
            {"token": "111111"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertNotIn("REGISTRATION_PAUSED", response.content.decode())
        self.assertIn("renewal is paused", response.json()["message"])

    def test_below_the_budget_a_valid_code_still_completes(self):
        self.without_per_network_throttles()
        student = User.objects.create_user(
            email="s.111111@student.local",
            password=None,
            first_name="Pending",
            last_name="Student",
            user_type=UserTypes.STUDENT,
            is_active=False,
            activation_token="111111",
            activation_expires=timezone.now() + timedelta(minutes=15),
        )

        response = self.post("111111")

        self.assertNotEqual(response.status_code, 429, response.content)
        self.assertNotIn("REGISTRATION_PAUSED", response.content.decode())
        student.refresh_from_db()
        self.assertTrue(student.is_active)
