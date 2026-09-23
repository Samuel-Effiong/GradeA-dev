"""H-42: a school admin invited via SchoolWithAdminSerializer has no usable
password (create_user() is called with no password kwarg - see
SchoolWithAdminSerializer.create()'s own comment). Their only way to ever
set one is completing the invite at POST /auth/register/school-admin with
the 7-day activation_token from _send_school_admin_invitation_email.

POST /auth/otp (otp_type=VERIFY_EMAIL) is AllowAny, takes just an email, and
looks up ANY inactive/unverified user regardless of how their account was
created - including this school admin. Before this fix, it unconditionally
called send_user_activation_email(), which:

  1. Overwrites activation_token/activation_expires with a fresh 15-minute
     OTP token, permanently invalidating the original 7-day invite token
     (there is no way back to it - it's gone, not just expired).
  2. Emails a `/verify-email?...` link on the wrong frontend domain
     (FRONTEND_DOMAIN, the teacher app - see H-42's other half, already
     fixed for the invitation email itself).
  3. POST /auth/verify (the generic completion for that link) sets
     is_active=True and clears the token - but never asks for or sets a
     password. Net result: an active, verified account with an unusable
     password and no remaining path to /register/school-admin (is_active
     is already True, so it will never match that endpoint's
     is_active=False filter, and the token that mattered is gone).

This file proves the dead end existed, then (once fixed) proves it can't
happen anymore: a school admin's own pending invite is never disturbed by
the generic self-registration OTP/verify flow.
"""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import School
from users.models import CustomUser, UserTypes
from users.services import send_user_activation_email


def make_pending_school_admin(email="admin@example.com"):
    school = School.objects.create(name="Riverside High")
    admin = CustomUser.objects.create_user(
        email=email,
        first_name="Ada",
        last_name="Min",
        user_type=UserTypes.SCHOOL_ADMIN,
        school=school,
        is_active=False,
        activation_token="original-7-day-token",
        activation_expires=timezone.now() + timezone.timedelta(days=7),
    )
    return school, admin


class SendUserActivationEmailNeverHijacksSchoolAdminInviteTests(TestCase):
    """Unit-level: send_user_activation_email() must resend the actual
    school-admin invitation (a fresh 7-day token) rather than routing a
    school admin through the generic, password-less 15-minute OTP flow."""

    def test_resends_a_fresh_invitation_instead_of_the_generic_otp_flow(self):
        school, admin = make_pending_school_admin()

        send_user_activation_email(admin)

        admin.refresh_from_db()
        # A resend, not a no-op: the token changed...
        self.assertNotEqual(admin.activation_token, "original-7-day-token")
        self.assertIsNotNone(admin.activation_token)
        # ...to one good for ~7 days, not the generic flow's 15 minutes.
        self.assertGreater(
            admin.activation_expires, timezone.now() + timezone.timedelta(days=6)
        )
        self.assertFalse(admin.is_active)
        self.assertIsNone(admin.email_verified_at)

        # And the new token still completes at the real school-admin
        # endpoint - it wasn't just changed, it still works.
        self.assertTrue(
            CustomUser.objects.filter(
                email=admin.email,
                activation_token=admin.activation_token,
                user_type=UserTypes.SCHOOL_ADMIN,
                is_active=False,
            ).exists()
        )


class OtpEndpointNeverDeadEndsAPendingSchoolAdminTests(APITestCase):
    """End-to-end: POST /auth/otp must not be able to strand an invited
    school admin the way it could before this fix."""

    def test_verify_email_otp_does_not_invalidate_the_pending_invite(self):
        school, admin = make_pending_school_admin("admin2@example.com")

        response = self.client.post(
            reverse("auth-otp"),
            {"email": admin.email, "otp_type": "VERIFY_EMAIL"},
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

        admin.refresh_from_db()
        # A resend, not a dead end: the token changed to a new one still
        # good for the real school-admin completion flow - it did not get
        # overwritten with one that leads nowhere (H-42).
        self.assertNotEqual(admin.activation_token, "original-7-day-token")
        self.assertIsNotNone(admin.activation_token)
        self.assertFalse(admin.is_active)
        self.assertIsNone(admin.email_verified_at)
        self.assertFalse(admin.has_usable_password())

        # And /register/school-admin must work with the new token - proving
        # the invite is still completable, just refreshed.
        complete_response = self.client.post(
            reverse("auth-register-school-admin"),
            {
                "email": admin.email,
                "token": admin.activation_token,
                "password": "a-strong-new-password-123",  # pragma: allowlist secret
            },
        )
        self.assertEqual(complete_response.status_code, status.HTTP_200_OK)
        admin.refresh_from_db()
        self.assertTrue(admin.is_active)
        self.assertTrue(admin.has_usable_password())
