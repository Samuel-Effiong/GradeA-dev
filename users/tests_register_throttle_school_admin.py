"""
Merge-down b14 (beta batches 13 to 14 into the Phase 2 line): the per-network
rate limit on the code-based sign-up doors that are still open.

users/tests_s7d_registration_paused.py (Epic A S7d, H-68) tested the old
student door's coded pause, and its fourth test checked that the per-network
RegisterThrottle's 429 is not mistaken for that pause. H-152 closed the
student door, so that module went. The per-network limit still guards the
school-admin invitation door, which is the one code-based sign-up left, so
this test keeps the part that is still true: the rate limit answers 429 and
carries no REGISTRATION_PAUSED code. (The dead pause code itself is H-207.)
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from users.throttling import RegisterThrottle

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM_CACHE)
class SchoolAdminDoorRateLimitTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def post(self, token):
        return self.client.post(
            reverse("auth-register-school-admin"),
            {
                "email": "nobody.admin@example.com",
                "token": token,
                "password": "Caller-Chosen-Pw-78",  # pragma: allowlist secret
            },
            format="json",
        )

    def test_the_per_network_limit_answers_429_without_a_pause_code(self):
        with patch.object(RegisterThrottle, "rate", "1/min", create=True):
            first = self.post("900001")
            second = self.post("900002")

        self.assertEqual(first.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(second.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertNotIn("REGISTRATION_PAUSED", second.content.decode())
