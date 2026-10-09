"""
H-212: the closed student door (`POST /auth/register/student`, answered 410
since H-152) is still rate limited per address.

`register_student` carries `throttle_classes=[RegisterThrottle]` (scope
"register", keyed on the caller's address). The H-152 docstring promises
"the per-address rate limit above still applies", and no test held either
line: removing the throttle, or swapping it for a class on another scope, left
every test green (Verifier 2's mutant D4). These tests send more requests than
a lowered "register" rate from one address and require the first ones to get
the closed answer (410) and the next to get 429, while another address is
still answered 410.

Run with:
    python manage.py test users.tests_register_student_rate_limit
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework.throttling import SimpleRateThrottle

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
RATES = {"register": "3/hour", "anon": "1000/min", "login": "1000/min"}
ADDRESS = "203.0.113.10"
OTHER_ADDRESS = "203.0.113.77"


@override_settings(CACHES=LOCMEM)
class ClosedStudentDoorRateLimitTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        rates = patch.dict(SimpleRateThrottle.THROTTLE_RATES, RATES)
        rates.start()
        self.addCleanup(rates.stop)

    def knock(self, address):
        return self.client.post(
            reverse("auth-register-student"),
            {"email": "nobody@x.example", "token": "000000"},
            format="json",
            REMOTE_ADDR=address,
        )

    def test_the_fourth_request_from_one_address_in_the_hour_is_a_429(self):
        answers = [self.knock(ADDRESS).status_code for _ in range(4)]

        self.assertEqual(
            answers,
            [
                status.HTTP_410_GONE,
                status.HTTP_410_GONE,
                status.HTTP_410_GONE,
                status.HTTP_429_TOO_MANY_REQUESTS,
            ],
        )

    def test_another_address_is_still_answered_when_one_is_limited(self):
        for _ in range(4):
            self.knock(ADDRESS)

        response = self.knock(OTHER_ADDRESS)

        self.assertEqual(response.status_code, status.HTTP_410_GONE)

    def test_the_limited_address_stays_limited_on_the_next_request(self):
        for _ in range(4):
            self.knock(ADDRESS)

        response = self.knock(ADDRESS)

        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
