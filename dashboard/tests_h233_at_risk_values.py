"""H-233: `at_risk` on my-students and all-students reads like a boolean.

Both routes took `at_risk.lower() in ("1", "true")`, so `?at_risk=yes`
silently meant false and `?at_risk=maybe` filtered to the students NOT at
risk (Verifier's L4 on 913f248f). Now (SM-approved 2026-10-14) the value is
read with DRF's own BooleanField parsing (true/false, 1/0, yes/no, on/off,
t/f, y/n), empty means not given, and anything else is a 400 naming the
parameter. On all-students the value is checked before the cache key is
built, so a refused request leaves nothing in the cache.
"""

from typing import Any, cast

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from dashboard.tests_teacher_all_students import Builder
from users.models import UserTypes

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
MY_STUDENTS = reverse("student-course-my-students")
REFUSED = {"at_risk": ["'maybe' is not true or false."]}


@override_settings(CACHES=LOCMEM)
class AtRiskReadsLikeABooleanTest(Builder, APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = self.user(UserTypes.TEACHER)
        self.term = self.session(self.teacher)
        # Separate courses, so neither student's expected-work count takes
        # in the other's assignments.
        self.risky = self.at_risk_student(
            self.course(self.teacher, self.term), first="Risky"
        )
        self.safe = self.safe_student(
            self.course(self.teacher, self.term), first="Safe"
        )
        self.all_students = reverse(
            "teacher-admin-all-students", kwargs={"session_id": self.term.id}
        )
        self.client.force_authenticate(self.teacher)

    def _get(self, url, **params):
        cache.clear()
        return self.client.get(url, params)

    def _my_ids(self, **params):
        response = self._get(MY_STUDENTS, **params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return {r["id"] for r in response.data["results"]}

    def _all_ids(self, **params):
        response = self._get(self.all_students, **params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return {r["student_id"] for r in response.data["results"]}

    def test_v1_yes_is_true_on_my_students(self):
        self.assertEqual(self._my_ids(at_risk="yes"), {str(self.risky.id)})

    def test_v2_yes_is_true_on_all_students(self):
        self.assertEqual(self._all_ids(at_risk="yes"), {str(self.risky.id)})

    def test_v3_an_unknown_value_is_a_400_on_my_students(self):
        response = self._get(MY_STUDENTS, at_risk="maybe")

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.data
        )
        self.assertEqual(response.data, REFUSED)

    def test_v4_an_unknown_value_is_a_400_on_all_students(self):
        response = self._get(self.all_students, at_risk="maybe")

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.data
        )
        self.assertEqual(response.data, REFUSED)

    def test_v5_empty_is_not_given_on_all_students(self):
        self.assertEqual(
            self._all_ids(at_risk=""), {str(self.risky.id), str(self.safe.id)}
        )

    def test_v6_a_refused_value_leaves_nothing_in_the_cache(self):
        cache.clear()
        self.client.get(self.all_students, {"at_risk": "maybe"})

        stored = [key for key in cast(Any, cache)._cache if "view__all_students" in key]
        self.assertEqual(stored, [])

    def test_v7_control_the_plain_values_still_work(self):
        """Green before and after, on both routes."""
        self.assertEqual(self._my_ids(at_risk="true"), {str(self.risky.id)})
        self.assertEqual(self._my_ids(at_risk="false"), {str(self.safe.id)})
        self.assertEqual(self._all_ids(at_risk="1"), {str(self.risky.id)})
        self.assertEqual(self._all_ids(at_risk="false"), {str(self.safe.id)})
        self.assertEqual(
            self._my_ids(at_risk=""), {str(self.risky.id), str(self.safe.id)}
        )

    def test_v8_control_a_cached_answer_is_still_stored(self):
        """Green before and after: V6's empty list is not because nothing
        is ever stored under this key."""
        cache.clear()
        self.client.get(self.all_students, {"at_risk": "true"})

        stored = [key for key in cast(Any, cache)._cache if "view__all_students" in key]
        self.assertEqual(len(stored), 1)
