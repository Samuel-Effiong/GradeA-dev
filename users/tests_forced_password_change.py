"""
Tests for users/authentication.py's MustChangePasswordJWTAuthentication
after the hard block was removed (product decision: a license teacher,
student or school admin with a system-generated password may use the API
immediately; must_change_password is now a frontend nudge, not a server-
side gate).

These tests exercise real JWT authentication (never `force_authenticate`,
which bypasses the authentication_classes chain entirely and so would
never touch the code under test) to prove the flag no longer blocks
anything, while the flag itself and its lifecycle (set on creation,
cleared on change-password) are unchanged.
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

    def test_flagged_user_is_not_blocked_from_an_arbitrary_protected_endpoint(self):
        response = self.client.get(
            reverse("user-detail", kwargs={"pk": self.teacher.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)

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


class MustChangePasswordIsExposedOnLoginTests(APITestCase):
    """
    must_change_password is no longer enforced server-side, so the
    frontend needs to read it itself to show a nudge. CustomUserSerializer
    backs the login response (CustomTokenObtainPairSerializer.validate),
    so this one field addition surfaces it there.
    """

    def test_login_response_reports_true_for_a_flagged_user(self):
        teacher = CustomUser.objects.create_user(
            email="flagged-login@school.edu",
            password=GENERATED_PASSWORD,
            first_name="Flagged",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
            is_active=True,
            email_verified_at=timezone.now(),
            must_change_password=True,
        )

        response = self.client.post(
            reverse("login"),
            {"email": teacher.email, "password": GENERATED_PASSWORD},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["user"]["must_change_password"])

    def test_login_response_reports_false_for_a_normal_user(self):
        password = "OrdinaryPassw0rd!"  # pragma: allowlist secret
        teacher = CustomUser.objects.create_user(
            email="unflagged-login@school.edu",
            password=password,
            first_name="Unflagged",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
            is_active=True,
            email_verified_at=timezone.now(),
        )

        response = self.client.post(
            reverse("login"),
            {"email": teacher.email, "password": password},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["user"]["must_change_password"])

    def test_field_is_read_only_and_cannot_be_client_set(self):
        """Mirrors the is_active read-only guard - a client must never be
        able to clear their own must_change_password flag by PATCHing it."""
        teacher = CustomUser.objects.create_user(
            email="readonly-check@school.edu",
            password=GENERATED_PASSWORD,
            first_name="Readonly",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
            is_active=True,
            email_verified_at=timezone.now(),
            must_change_password=True,
        )
        access = RefreshToken.for_user(teacher).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

        response = self.client.patch(
            reverse("user-detail", kwargs={"pk": teacher.pk}),
            {"must_change_password": False},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        teacher.refresh_from_db()
        self.assertTrue(teacher.must_change_password)
