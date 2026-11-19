"""
QA rows 11, 12 and 25: plain owner-approved sentences in place of the
server's own. Only the human text changes: status, `code` and shape stay.
"""

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from users.models import PasswordResetOTP
from users.tests_auth_endpoints import LOCMEM_CACHE, make_user

EXPIRED = "Your session has expired. Please sign in again."
SIGN_IN = "Please sign in to continue."
RESET_DONE = "Your password has been reset. You're now signed in."


@override_settings(CACHES=LOCMEM_CACHE)
class PlainAuthMessageTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user("plain.msgs@gmail.com")

    def test_no_credentials_row_12(self):
        response = self.client.post(reverse("auth-change-password"), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["detail"], SIGN_IN)

    def test_bad_access_token_row_11(self):
        self.client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
        response = self.client.post(reverse("auth-change-password"), {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["detail"], EXPIRED)
        self.assertEqual(response.data["code"], "token_not_valid")
        self.assertIn("messages", response.data)

    def test_bad_refresh_token_row_11(self):
        response = self.client.post(
            reverse("refresh"), {"refresh": "not-a-real-token"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(response.data["detail"], EXPIRED)
        self.assertEqual(response.data["code"], "token_not_valid")

    def test_password_reset_done_row_25(self):
        RefreshToken.for_user(self.user)
        otp = PasswordResetOTP.objects.create(user=self.user)
        code = otp.generate_code()
        response = self.client.post(
            reverse("auth-reset-password"),
            {
                "email": self.user.email,
                "otp": code,
                "new_password": "a-replacement-password-77",  # pragma: allowlist secret
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["detail"], RESET_DONE)
        self.assertIn("access", response.data)
        self.assertIn("refresh", response.data)
