"""
Tests for server-side must_change_password enforcement
(users/authentication.py MustChangePasswordJWTAuthentication).

A license-invited teacher logs in with a system-generated password and must
be blocked from doing anything else until they change it - not as a
frontend convention, but enforced on every authenticated request regardless
of which viewset handles it. These tests exercise real JWT authentication
(never `force_authenticate`, which bypasses the authentication_classes
chain entirely and so would never touch the code under test).
"""

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from users.models import CustomUser, UserTypes

GENERATED_PASSWORD = "Generated-Pw9-Xk2m"  # pragma: allowlist secret
NEW_PASSWORD = "TeacherChosenPw7-Zq"  # pragma: allowlist secret


class ForcedPasswordChangeEnforcementTests(APITestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="invited_teacher@school.edu",
            password=GENERATED_PASSWORD,
            first_name="Invited",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
            is_active=True,
            email_verified_at=timezone.now(),
            must_change_password=True,
        )
        self._authenticate_as(self.teacher)

    def _authenticate_as(self, user):
        access = RefreshToken.for_user(user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    def test_flagged_user_is_blocked_from_an_arbitrary_protected_endpoint(self):
        response = self.client.get(
            reverse("user-detail", kwargs={"pk": self.teacher.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data.get("detail").code, "password_change_required")

    def test_flagged_user_can_still_reach_change_password(self):
        response = self.client.post(
            reverse("auth-change-password"),
            {"current_password": GENERATED_PASSWORD, "new_password": NEW_PASSWORD},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_flagged_user_can_still_reach_logout(self):
        refresh = RefreshToken.for_user(self.teacher)
        response = self.client.post(
            reverse("auth-logout"), {"refresh": str(refresh)}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_205_RESET_CONTENT)

    def test_after_changing_password_the_flag_clears_and_access_is_normal(self):
        change_response = self.client.post(
            reverse("auth-change-password"),
            {"current_password": GENERATED_PASSWORD, "new_password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(change_response.status_code, status.HTTP_200_OK)

        self.teacher.refresh_from_db()
        self.assertFalse(self.teacher.must_change_password)

        # change-password blacklists all prior tokens and issues a fresh
        # pair - re-authenticate with the new access token, as the real
        # frontend would.
        self.client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {change_response.data['access']}"
        )
        response = self.client.get(
            reverse("user-detail", kwargs={"pk": self.teacher.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_a_normal_user_sees_zero_behavior_change(self):
        """The one this enforcement must not regress: an ordinary account
        (must_change_password False, the default) is completely unaffected
        anywhere in the API."""
        normal_user = CustomUser.objects.create_user(
            email="normal_teacher@school.edu",
            password="OrdinaryPassw0rd!",  # pragma: allowlist secret
            first_name="Normal",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
            is_active=True,
            email_verified_at=timezone.now(),
        )
        self.assertFalse(normal_user.must_change_password)
        self._authenticate_as(normal_user)

        response = self.client.get(
            reverse("user-detail", kwargs={"pk": normal_user.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_token_refresh_is_unaffected_by_the_flag(self):
        """auth/refresh authenticates via the refresh token in the request
        body (simplejwt's TokenViewBase sets authentication_classes = ()),
        never through our access-token authenticator, so it must keep
        working for a flagged user."""
        refresh = RefreshToken.for_user(self.teacher)
        # No Authorization header at all - refresh doesn't need one.
        self.client.credentials()

        response = self.client.post(
            reverse("refresh"), {"refresh": str(refresh)}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_login_itself_is_unaffected_by_the_flag(self):
        """A teacher has to be ABLE to log in with the generated password
        before anything can force them to change it - login stays AllowAny
        and unauthenticated, so the flag can't block it."""
        self.client.credentials()

        response = self.client.post(
            reverse("login"),
            {"email": self.teacher.email, "password": GENERATED_PASSWORD},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
