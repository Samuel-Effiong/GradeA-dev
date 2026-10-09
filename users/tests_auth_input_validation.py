"""
Malformed and adversarial input at the unauthenticated auth boundary.

Two of these are regression guards for crashes the code-review pass found
and fixed, neither of which had a test:

  * AuthViewSet.verify read `request.data.get("email").strip()`. A body
    missing "email" (or "token") therefore raised AttributeError on None
    and came back as a 500 - an unauthenticated caller could produce a
    server error, and 500-noise buries real incidents.
  * AuthViewSet.register_student compared `user.activation_expires <
    timezone.now()` with no None check, while the column is nullable. An
    invite whose expiry was never set raised TypeError instead of telling
    the student their link needs renewing.

The rest are the ordinary adversarial shapes an unauthenticated endpoint
has to survive: wrong types, empty strings, whitespace-only values, and
oversized payloads.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import UserTypes

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM_CACHE)
class VerifyEndpointInputTests(APITestCase):
    """`POST /auth/verify` - unauthenticated, so it takes whatever arrives."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = User.objects.create_user(
            email="verify.input@gmail.com",
            password="password123",  # pragma: allowlist secret
            first_name="Verify",
            last_name="Input",
            user_type=UserTypes.TEACHER,
            activation_token="123456",
            activation_expires=timezone.now() + timezone.timedelta(minutes=15),
        )
        self.url = reverse("auth-verify")

    def test_missing_both_fields_is_a_400_not_a_500(self):
        """The regression: `.get("email").strip()` on an absent key."""
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("required", str(response.data).lower())

    def test_missing_email_only_is_a_400_not_a_500(self):
        response = self.client.post(self.url, {"token": "123456"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_token_only_is_a_400_not_a_500(self):
        response = self.client.post(self.url, {"email": self.user.email}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_null_values_are_a_400_not_a_500(self):
        response = self.client.post(
            self.url, {"email": None, "token": None}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_whitespace_only_values_are_rejected(self):
        """Stripped to empty, so this must take the "required" path."""
        response = self.client.post(
            self.url, {"email": "   ", "token": "   "}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_wrong_token_does_not_activate_the_account(self):
        response = self.client.post(
            self.url,
            {"email": self.user.email, "token": "999999"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertIsNone(self.user.email_verified_at)

    def test_another_users_token_cannot_verify_this_account(self):
        """email and token must match the SAME row, not merely both exist."""
        other = User.objects.create_user(
            email="verify.other@gmail.com",
            password="password123",  # pragma: allowlist secret
            first_name="Other",
            last_name="User",
            user_type=UserTypes.TEACHER,
            activation_token="654321",
            activation_expires=timezone.now() + timezone.timedelta(minutes=15),
        )

        response = self.client.post(
            self.url,
            {"email": self.user.email, "token": "654321"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        other.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertFalse(other.is_active)

    def test_expired_token_is_rejected_and_leaves_the_account_inactive(self):
        self.user.activation_expires = timezone.now() - timezone.timedelta(minutes=1)
        self.user.save(update_fields=["activation_expires"])

        response = self.client.post(
            self.url,
            {"email": self.user.email, "token": "123456"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("expired", str(response.data).lower())
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

    def test_oversized_field_does_not_crash_the_endpoint(self):
        response = self.client.post(
            self.url,
            {"email": "a" * 10000 + "@gmail.com", "token": "1" * 10000},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_valid_token_still_activates(self):
        """Regression guard: the input hardening must not break the real flow."""
        with patch("users.views.sync_user_to_mailerlite"):
            response = self.client.post(
                self.url,
                {"email": self.user.email, "token": "123456"},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertIsNotNone(self.user.email_verified_at)
        # Single use: the token is consumed.
        self.assertIsNone(self.user.activation_token)
