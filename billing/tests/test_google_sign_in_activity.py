"""S17 (owner go 2026-11-18, beta): a Google sign-in records activity like an
email sign-in - the teacher gets a BetaProfile and the distinct sign-in days
count. A tracking failure never blocks the sign-in. No backfill."""

from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from freezegun import freeze_time
from rest_framework.test import APIClient

from billing.models import BetaProfile
from users.models import CustomUser, UserTypes

from .test_intent_signals import LeadsBase
from .test_intent_signals_pins import NOW

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class GoogleSignInActivityTests(LeadsBase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.addCleanup(cache.clear)
        patcher = patch("users.views.sync_user_to_mailerlite")
        patcher.start()
        self.addCleanup(patcher.stop)

    def person_of(self, user_type, tag):
        return CustomUser.objects.create_user(
            email=f"google.{tag}@gmail.com",
            password=None,
            first_name="Goo",
            last_name="Gle",
            user_type=user_type,
            is_active=True,
        )

    def google(self, email):
        with patch("requests.post") as post, patch(
            "users.views.id_token.verify_oauth2_token"
        ) as verify:
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {
                "id_token": "fake-id-token",
                "access_token": "fake-access-token",
                "expires_in": 3600,
            }
            verify.return_value = {
                "email": email,
                "email_verified": True,
                "given_name": "Goo",
                "family_name": "Gle",
            }
            return APIClient().post(
                reverse("auth-google-auth"), {"code": "oauth-code"}, format="json"
            )

    def days(self, user):
        return BetaProfile.objects.get(user=user).distinct_login_days

    def test_a_teachers_first_google_sign_in_creates_their_profile(self):
        teacher = self.person_of(UserTypes.TEACHER, "teacher")
        self.assertFalse(BetaProfile.objects.filter(user=teacher).exists())
        with freeze_time(NOW):
            response = self.google(teacher.email)
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(self.days(teacher), 1)
        self.assertIn(teacher.email, self.emails(page_size=100))

    def test_a_new_day_counts_and_the_same_day_twice_does_not(self):
        teacher = self.person_of(UserTypes.TEACHER, "days")
        with freeze_time(NOW):
            self.google(teacher.email)
            self.google(teacher.email)
        self.assertEqual(self.days(teacher), 1)
        with freeze_time(NOW + timedelta(days=1)):
            self.google(teacher.email)
        self.assertEqual(self.days(teacher), 2)

    def test_a_student_gets_no_profile(self):
        student = self.person_of(UserTypes.STUDENT, "student")
        response = self.google(student.email)
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertFalse(BetaProfile.objects.filter(user=student).exists())

    def test_a_tracking_failure_never_blocks_the_sign_in(self):
        teacher = self.person_of(UserTypes.TEACHER, "broken")
        with patch(
            "users.views.AnalyticsService.track_activity",
            side_effect=RuntimeError("tracking is down"),
        ):
            response = self.google(teacher.email)
        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()["data"] if "data" in response.json() else response.json()
        self.assertIn("access", body)
        self.assertIn("refresh", body)
