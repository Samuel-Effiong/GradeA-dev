"""A newly created school admin (SchoolWithAdminSerializer.create(), backing
POST /schools/create_with_admin) is active immediately with a temporary
password, the same pattern as a license-invited teacher
(billing/license_service.py). Before this change they were created
is_active=False with an unusable password and had to click a 7-day
activation-token link to set one.

_send_school_admin_invitation_email() and resend_school_admin_invitation()
still serve the old is_active=False/activation_token lifecycle for admin
rows that predate this change - see classrooms/test_school_admin_invitation_email.py
and classrooms/test_school_admin_otp_deadend.py, both left untouched and
still exercising that path directly against hand-built fixtures.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.serializers import SchoolWithAdminSerializer
from users.models import CustomUser, UserTypes


def _make_superadmin(email="superadmin@example.com"):
    superadmin = CustomUser.objects.create_superuser(
        email=email,
        password="password123",  # pragma: allowlist secret
        first_name="Super",
        last_name="Admin",
    )
    superadmin.user_type = UserTypes.SUPER_ADMIN
    superadmin.is_active = True
    superadmin.save()
    return superadmin


def _admin_payload(email="admin@riverside.edu", school_name="Riverside High"):
    return {
        "school_name": school_name,
        "admin_email": email,
        "admin_first_name": "Ada",
        "admin_last_name": "Min",
    }


class NewSchoolAdminIsActiveImmediatelyTests(TestCase):
    """Unit-level: SchoolWithAdminSerializer.create() itself."""

    def _create(self, **overrides):
        payload = _admin_payload(**overrides)
        serializer = SchoolWithAdminSerializer(data=payload)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        with patch("classrooms.serializers.send_email_task"):
            with self.captureOnCommitCallbacks(execute=True):
                result = serializer.save()
        return result["school"], result["admin"]

    def test_admin_is_created_active_with_no_activation_token(self):
        _, admin = self._create()

        self.assertTrue(admin.is_active)
        self.assertIsNone(admin.activation_token)
        self.assertIsNone(admin.activation_expires)

    def test_admin_is_created_with_a_working_temporary_password(self):
        _, admin = self._create()

        self.assertTrue(admin.has_usable_password())
        self.assertTrue(admin.must_change_password)

    def test_admin_email_is_marked_verified_at_creation(self):
        """Unlike the license-teacher path, this must be set here - see the
        comment on SchoolWithAdminSerializer.create()'s admin_data: without
        it, POST /auth/otp (VERIFY_EMAIL) would still resend the old,
        now-dead activation-token link to an already-active admin."""
        _, admin = self._create()

        self.assertIsNotNone(admin.email_verified_at)

    def test_generated_password_is_never_logged(self):
        import logging
        import re

        records = []

        class _CapturingHandler(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _CapturingHandler()
        root_logger = logging.getLogger()
        root_logger.addHandler(handler)
        try:
            payload = _admin_payload(email="quiet@riverside.edu")
            serializer = SchoolWithAdminSerializer(data=payload)
            self.assertTrue(serializer.is_valid())
            with patch("classrooms.serializers.send_email_task") as mock_send_email:
                with self.captureOnCommitCallbacks(execute=True):
                    serializer.save()
            merge_data = mock_send_email.delay.call_args.kwargs["merge_data"]
        finally:
            root_logger.removeHandler(handler)

        m = re.search(r"temporary password: (\S+)", merge_data["top_content"])
        assert m is not None
        plaintext_password = m.group(1)
        for record in records:
            self.assertNotIn(plaintext_password, record.getMessage())


class SchoolAdminInvitationEmailIsALoginLinkTests(TestCase):
    """The invitation email for a newly created admin must point at login,
    not the old registration-completion page - and must carry the
    temporary password in the copy, since there's no link to click through
    with it pre-filled."""

    @override_settings(SCHOOL_ADMIN_FRONTEND_DOMAIN="admin.example.test")
    def test_invitation_email_links_to_login_on_the_school_admin_domain(self):
        payload = _admin_payload(email="linked@riverside.edu")
        serializer = SchoolWithAdminSerializer(data=payload)
        self.assertTrue(serializer.is_valid())

        with patch("classrooms.serializers.send_email_task") as mock_send_email:
            with self.captureOnCommitCallbacks(execute=True):
                serializer.save()

        merge_data = mock_send_email.delay.call_args.kwargs["merge_data"]
        activation_url = merge_data["activation_url"]
        self.assertEqual(activation_url, "https://admin.example.test/login")
        self.assertNotIn("token=", activation_url)
        self.assertNotIn("register/school-admin", activation_url)

    def test_invitation_email_body_carries_the_temporary_password(self):
        payload = _admin_payload(email="carries-password@riverside.edu")
        serializer = SchoolWithAdminSerializer(data=payload)
        self.assertTrue(serializer.is_valid())

        with patch("classrooms.serializers.send_email_task") as mock_send_email:
            with self.captureOnCommitCallbacks(execute=True):
                result = serializer.save()

        admin = result["admin"]
        merge_data = mock_send_email.delay.call_args.kwargs["merge_data"]
        admin.refresh_from_db()
        # The password isn't recoverable from the stored hash, so the only
        # way to check the email carries the *real* one is to check it
        # against the account it actually set.
        import re

        m = re.search(r"temporary password: (\S+)", merge_data["top_content"])
        assert m is not None
        self.assertTrue(admin.check_password(m.group(1)))
        self.assertNotIn("expires in 7 days", merge_data["bottom_content"])
        self.assertNotIn("Complete your registration", merge_data["top_content"])


class CreateWithAdminEndpointTests(APITestCase):
    """End-to-end: POST /schools/create_with_admin."""

    def setUp(self):
        self.superadmin = _make_superadmin()
        self.client.force_authenticate(self.superadmin)

    @patch("classrooms.serializers.send_email_task")
    def test_response_contains_no_password_and_admin_is_active(self, mock_send_email):
        response = self.client.post(
            reverse("school-create-with-admin"),
            _admin_payload(email="e2e@riverside.edu"),
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        admin_data = response.data["admin"]
        self.assertNotIn("password", admin_data)
        self.assertTrue(admin_data["is_active"])

        admin = CustomUser.objects.get(email="e2e@riverside.edu")
        self.assertTrue(admin.is_active)
        self.assertTrue(admin.has_usable_password())
        self.assertTrue(admin.must_change_password)

    @patch("classrooms.serializers.send_email_task")
    def test_new_admin_can_log_in_with_the_emailed_temporary_password(
        self, mock_send_email
    ):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("school-create-with-admin"),
                _admin_payload(email="loginflow@riverside.edu"),
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        merge_data = mock_send_email.delay.call_args.kwargs["merge_data"]
        import re

        m = re.search(r"temporary password: (\S+)", merge_data["top_content"])
        assert m is not None
        password = m.group(1)

        login_response = self.client.post(
            reverse("login"),
            {"email": "loginflow@riverside.edu", "password": password},
        )
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)


class OldRegistrationCompletionEndpointIsUnreachableForNewAdminsTests(APITestCase):
    """POST /auth/register/school-admin filters on is_active=False, which a
    newly created admin no longer satisfies - confirming it's genuinely
    unreachable for any *new* invite, not merely unused."""

    @patch("classrooms.serializers.send_email_task")
    def test_completion_endpoint_rejects_a_newly_created_admin(self, mock_send_email):
        superadmin = _make_superadmin()
        self.client.force_authenticate(superadmin)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("school-create-with-admin"),
                _admin_payload(email="deadend@riverside.edu"),
            )
        admin = CustomUser.objects.get(email="deadend@riverside.edu")
        self.client.force_authenticate(None)

        response = self.client.post(
            reverse("auth-register-school-admin"),
            {
                "email": admin.email,
                "token": "anything",
                "password": "whatever-new-password-123",  # pragma: allowlist secret
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class VerifyEmailOtpDoesNotDeadEndANewlyCreatedAdminTests(APITestCase):
    """POST /auth/otp (VERIFY_EMAIL) must recognise a newly created admin
    as already verified, rather than falling through to
    resend_school_admin_invitation() and emailing a dead activation-token
    link (that link's own completion endpoint now always rejects them -
    see OldRegistrationCompletionEndpointIsUnreachableForNewAdminsTests)."""

    @patch("classrooms.serializers.send_email_task")
    def test_otp_verify_email_short_circuits_instead_of_resending(
        self, mock_send_email
    ):
        superadmin = _make_superadmin()
        self.client.force_authenticate(superadmin)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(
                reverse("school-create-with-admin"),
                _admin_payload(email="otpcheck@riverside.edu"),
            )
        admin = CustomUser.objects.get(email="otpcheck@riverside.edu")
        original_token = admin.activation_token
        self.client.force_authenticate(None)

        with patch(
            "classrooms.serializers.resend_school_admin_invitation"
        ) as mock_resend:
            response = self.client.post(
                reverse("auth-otp"),
                {"email": admin.email, "otp_type": "VERIFY_EMAIL"},
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_resend.assert_not_called()
        admin.refresh_from_db()
        self.assertEqual(admin.activation_token, original_token)
