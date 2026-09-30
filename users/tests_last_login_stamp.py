"""A sign-in stamps last_login without firing the user-cache fan-out.

enroll_student_by_email reads "has ever signed in" (last_login or a
UserActivity row) to decide whether an existing student may be sent a fresh
password, so every sign-in must leave last_login behind. simplejwt's
UPDATE_LAST_LOGIN would do that with save(), and save() fires post_save ->
clear_user_cache: one bump of the user, any-user and GLOBAL generations plus
a nine-pattern wildcard sweep, on every login (the Verification Engineer's
measurement, retire (A) re-verification 2026-09-29). last_login is in no
cached payload, so the stamp is a queryset update that sends no signal.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.settings import api_settings

from users import signals
from users.models import UserTypes

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD = "Stamp-Login-Passw0rd!"  # pragma: allowlist secret


@override_settings(CACHES=LOCMEM_CACHE)
class PasswordLoginStampTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def make(self, email, user_type):
        return User.objects.create_user(
            email=email,
            password=PASSWORD,
            first_name="Stamp",
            last_name="Login",
            user_type=user_type,
            is_active=True,
        )

    def login_recording_invalidation(self, email):
        with patch.object(
            signals, "bump_many", wraps=signals.bump_many
        ) as bumps, patch.object(
            signals, "delete_cache_patterns", wraps=signals.delete_cache_patterns
        ) as sweeps:
            response = self.client.post(
                reverse("login"), {"email": email, "password": PASSWORD}, format="json"
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        return bumps, sweeps

    def test_a_student_login_stamps_last_login_with_no_cache_invalidation(self):
        student = self.make("stamp.student@example.com", UserTypes.STUDENT)

        bumps, sweeps = self.login_recording_invalidation(student.email)

        student.refresh_from_db()
        self.assertIsNotNone(student.last_login)
        self.assertEqual(bumps.call_count, 0, bumps.call_args_list)
        self.assertEqual(sweeps.call_count, 0, sweeps.call_args_list)

    def test_a_teacher_login_stamps_last_login_with_no_cache_invalidation(self):
        teacher = self.make("stamp.teacher@example.com", UserTypes.TEACHER)

        bumps, sweeps = self.login_recording_invalidation(teacher.email)

        teacher.refresh_from_db()
        self.assertIsNotNone(teacher.last_login)
        self.assertEqual(bumps.call_count, 0, bumps.call_args_list)
        self.assertEqual(sweeps.call_count, 0, sweeps.call_args_list)

    def test_a_failed_login_stamps_nothing(self):
        student = self.make("stamp.wrong@example.com", UserTypes.STUDENT)

        response = self.client.post(
            reverse("login"),
            {
                "email": student.email,
                "password": "not-the-password",  # pragma: allowlist secret
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        student.refresh_from_db()
        self.assertIsNone(student.last_login)

    def test_simplejwt_update_last_login_stays_off(self):
        """Turning it back on reintroduces the save() and its fan-out."""
        self.assertFalse(api_settings.UPDATE_LAST_LOGIN)


@override_settings(CACHES=LOCMEM_CACHE)
class GoogleSignInStampTests(APITestCase):
    """Google sign-in mints tokens itself, not through the login serializer,
    so it stamps last_login on its own (the Verification Engineer's
    non-blocking note)."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        mailerlite = patch("users.views.sync_user_to_mailerlite")
        mailerlite.start()
        self.addCleanup(mailerlite.stop)

    def google_sign_in(self, email):
        with patch("requests.post") as mocked_post, patch(
            "users.views.id_token.verify_oauth2_token"
        ) as mocked_verify:
            mocked_post.return_value.raise_for_status.return_value = None
            mocked_post.return_value.json.return_value = {
                "id_token": "fake-id-token",
                "access_token": "fake-access-token",
                "expires_in": 3600,
            }
            mocked_verify.return_value = {
                "email": email,
                "email_verified": True,
                "given_name": "Goo",
                "family_name": "Gle",
            }
            return self.client.post(
                reverse("auth-google-auth"), {"code": "oauth-code"}, format="json"
            )

    def test_a_google_sign_in_stamps_last_login(self):
        student = User.objects.create_user(
            email="google.student@gmail.com",
            password=None,
            first_name="Goo",
            last_name="Gle",
            user_type=UserTypes.STUDENT,
            is_active=True,
        )

        response = self.google_sign_in(student.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        student.refresh_from_db()
        self.assertIsNotNone(student.last_login)
