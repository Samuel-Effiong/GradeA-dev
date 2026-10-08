"""
Tests for AuthViewSet.change_password's dual-mode OTP handling.

`auth/request-change-password` emails a PasswordChangeOTP, but the frontend
does not send that code back yet, so `auth/change-password` has to accept
BOTH shapes: with an `otp` (fully verified) and without one (verified on
`current_password` alone, the behaviour that shipped). These tests pin both
halves down so the no-OTP path can't regress while the frontend catches up,
and so the OTP path can't silently degrade into "accepted whatever was
sent" once it is in use.
"""

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import CustomUser, PasswordChangeOTP, UserTypes

# Literal throwaway passwords for a test fixture - never a real credential.
CURRENT_PASSWORD = "OldPassw0rd!23"  # pragma: allowlist secret
NEW_PASSWORD = "BrandNewPassw0rd!45"  # pragma: allowlist secret


class ChangePasswordOTPOptionalTests(APITestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="teacher@gmail.com",
            password=CURRENT_PASSWORD,
            first_name="Test",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
            is_active=True,
            email_verified_at=timezone.now(),
        )
        self.url = reverse("auth-change-password")
        self.client.force_authenticate(user=self.user)

    def _post(self, **payload):
        body = {
            "current_password": CURRENT_PASSWORD,
            "new_password": NEW_PASSWORD,
        }
        body.update(payload)
        return self.client.post(self.url, body, format="json")

    def _password_changed(self):
        self.user.refresh_from_db()
        return self.user.check_password(NEW_PASSWORD)

    # ---------- no OTP: the behaviour that already shipped ----------

    def test_succeeds_without_an_otp(self):
        """The frontend does not send `otp` yet - this must keep working."""
        response = self._post()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(self._password_changed())

    def test_succeeds_with_an_empty_otp_string(self):
        # A form-encoded client that always sends the field will send "".
        # That is "no OTP supplied", not "an OTP that fails to match".
        response = self._post(otp="")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(self._password_changed())

    def test_no_otp_still_requires_the_current_password(self):
        response = self._post(current_password="not-the-right-password")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(self._password_changed())

    def test_an_unused_outstanding_otp_does_not_block_a_no_otp_change(self):
        # Requesting a code and then not using it must not lock the user out
        # of the current (code-less) flow.
        otp_obj = PasswordChangeOTP.objects.create(user=self.user)
        otp_obj.generate_code()

        response = self._post()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(self._password_changed())

    # ---------- with an OTP: verified for real ----------

    def test_succeeds_with_a_correct_otp(self):
        otp_obj = PasswordChangeOTP.objects.create(user=self.user)
        code = otp_obj.generate_code()

        response = self._post(otp=code)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(self._password_changed())

    def test_a_wrong_otp_is_rejected_and_the_password_is_unchanged(self):
        otp_obj = PasswordChangeOTP.objects.create(user=self.user)
        code = otp_obj.generate_code()
        wrong_code = "000000" if code != "000000" else "111111"

        response = self._post(otp=wrong_code)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(self._password_changed())
        # The code survives a wrong guess, so the user can retype it.
        self.assertTrue(PasswordChangeOTP.objects.filter(user=self.user).exists())

    def test_an_otp_sent_when_none_was_requested_is_rejected(self):
        self.assertFalse(PasswordChangeOTP.objects.filter(user=self.user).exists())

        response = self._post(otp="123456")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(self._password_changed())

    def test_an_expired_otp_is_rejected(self):
        otp_obj = PasswordChangeOTP.objects.create(user=self.user)
        code = otp_obj.generate_code()
        # PasswordChangeOTP.is_valid() is a 5-minute window.
        PasswordChangeOTP.objects.filter(pk=otp_obj.pk).update(
            created_at=timezone.now() - timezone.timedelta(minutes=6)
        )

        response = self._post(otp=code)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(self._password_changed())

    def test_another_users_otp_cannot_be_used(self):
        """A valid code belonging to someone else must not authorise this change."""
        other_user = CustomUser.objects.create_user(
            email="other@gmail.com",
            password="SomeOtherPassw0rd!1",  # pragma: allowlist secret
            first_name="Other",
            last_name="Person",
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        other_otp = PasswordChangeOTP.objects.create(user=other_user)
        other_code = other_otp.generate_code()

        response = self._post(otp=other_code)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(self._password_changed())

    def test_a_used_otp_cannot_be_replayed(self):
        otp_obj = PasswordChangeOTP.objects.create(user=self.user)
        code = otp_obj.generate_code()

        first = self._post(otp=code)
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertFalse(PasswordChangeOTP.objects.filter(user=self.user).exists())

        # Same code, changing back to the original password: must be refused
        # because the code was consumed, not because anything else failed.
        second = self.client.post(
            self.url,
            {
                "current_password": NEW_PASSWORD,
                "new_password": CURRENT_PASSWORD,
                "otp": code,
            },
            format="json",
        )

        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))

    def test_a_correct_otp_does_not_bypass_the_current_password_check(self):
        otp_obj = PasswordChangeOTP.objects.create(user=self.user)
        code = otp_obj.generate_code()

        response = self._post(otp=code, current_password="not-the-right-password")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(self._password_changed())
