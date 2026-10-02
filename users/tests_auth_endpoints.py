"""
The auth endpoints that had no tests at all.

`logout`, `request_change_password` and `register/school-admin` were three
complete, reachable endpoints with zero coverage - two of them mutate
credentials and one blacklists tokens. This file also covers the remaining
uncovered branches of the OTP, reset-password and student-registration
flows, and the legacy `session_results` fallback.
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import RefreshToken

from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from students.models import BatchUploadSession
from users.models import PasswordChangeOTP, PasswordResetOTP, UserTypes

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD = "a-strong-password-42"  # pragma: allowlist secret


def make_user(email, **overrides):
    defaults = {
        "email": email,
        "password": PASSWORD,
        "first_name": "Auth",
        "last_name": "Endpoint",
        "user_type": UserTypes.TEACHER,
        "is_active": True,
        "email_verified_at": timezone.now(),
    }
    defaults.update(overrides)
    return User.objects.create_user(**defaults)


@override_settings(CACHES=LOCMEM_CACHE)
class LogoutTests(APITestCase):
    """Blacklists the refresh token so it can no longer mint access tokens."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user("logout.user@gmail.com")
        self.client.force_authenticate(user=self.user)
        self.url = reverse("auth-logout")

    def test_a_valid_refresh_token_is_blacklisted(self):
        refresh = RefreshToken.for_user(self.user)

        response = self.client.post(self.url, {"refresh": str(refresh)}, format="json")

        self.assertEqual(response.status_code, status.HTTP_205_RESET_CONTENT)
        self.assertTrue(BlacklistedToken.objects.exists())

    def test_a_blacklisted_token_can_no_longer_be_refreshed(self):
        """Blacklisting has to actually stop the token working."""
        refresh = RefreshToken.for_user(self.user)
        self.client.post(self.url, {"refresh": str(refresh)}, format="json")

        refreshed = self.client.post(
            reverse("refresh"), {"refresh": str(refresh)}, format="json"
        )

        self.assertEqual(refreshed.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_a_missing_refresh_token_is_a_400(self):
        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("required", str(response.data).lower())

    def test_a_malformed_refresh_token_is_a_400(self):
        response = self.client.post(self.url, {"refresh": "not-a-jwt"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_already_blacklisted_token_is_rejected(self):
        refresh = RefreshToken.for_user(self.user)
        self.client.post(self.url, {"refresh": str(refresh)}, format="json")

        response = self.client.post(self.url, {"refresh": str(refresh)}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_anonymous_callers_are_rejected(self):
        self.client.force_authenticate(user=None)

        response = self.client.post(self.url, {"refresh": "x"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


@override_settings(CACHES=LOCMEM_CACHE)
class RequestChangePasswordTests(APITestCase):
    """Emails the OTP that `change-password` optionally accepts."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user("request.change@gmail.com")
        self.client.force_authenticate(user=self.user)
        self.url = reverse("auth-request-change-password")

    def test_an_otp_is_created_and_emailed(self):
        with patch("users.views.send_mail") as mock_send:
            response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        otp = PasswordChangeOTP.objects.get(user=self.user)
        self.assertIsNotNone(otp.code)
        mock_send.assert_called_once()
        self.assertIn(otp.code, mock_send.call_args.kwargs["message"])
        self.assertEqual(
            mock_send.call_args.kwargs["recipient_list"], [self.user.email]
        )

    def test_the_emailed_code_is_the_one_change_password_accepts(self):
        """End to end: request a code, then spend it."""
        with patch("users.views.send_mail"):
            self.client.post(self.url, {}, format="json")

        code = PasswordChangeOTP.objects.get(user=self.user).code
        new_password = "an-even-stronger-password-99"  # pragma: allowlist secret

        response = self.client.post(
            reverse("auth-change-password"),
            {
                "current_password": PASSWORD,
                "new_password": new_password,
                "otp": code,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(new_password))

    def test_requesting_twice_replaces_the_code_rather_than_duplicating(self):
        with patch("users.views.send_mail"):
            self.client.post(self.url, {}, format="json")
            first = PasswordChangeOTP.objects.get(user=self.user).code
            self.client.post(self.url, {}, format="json")

        self.assertEqual(PasswordChangeOTP.objects.filter(user=self.user).count(), 1)
        second = PasswordChangeOTP.objects.get(user=self.user).code
        self.assertNotEqual(first, second)

    def test_an_unverified_inactive_account_is_refused(self):
        unverified = make_user(
            "unverified.change@gmail.com", is_active=False, email_verified_at=None
        )
        self.client.force_authenticate(user=unverified)

        with patch("users.views.send_mail") as mock_send:
            response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_send.assert_not_called()

    def test_anonymous_callers_are_rejected(self):
        self.client.force_authenticate(user=None)

        response = self.client.post(self.url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


@override_settings(CACHES=LOCMEM_CACHE)
class RegisterSchoolAdminTests(APITestCase):
    """Completing a school-admin invitation."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.school = School.objects.create(name="Invite School")
        self.admin = User.objects.create_user(
            email="invited.admin@acme-school.org",
            password="placeholder-password-1",  # pragma: allowlist secret
            first_name="Invited",
            last_name="Admin",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=False,
            activation_token="admin-invite-token",
            activation_expires=timezone.now() + timezone.timedelta(days=1),
        )
        self.url = reverse("auth-register-school-admin")

    def payload(self, **overrides):
        body = {
            "email": self.admin.email,
            "token": "admin-invite-token",
            "password": PASSWORD,
        }
        body.update(overrides)
        return body

    def test_a_valid_invite_activates_the_account_and_logs_in(self):
        with patch("users.views.sync_user_to_mailerlite"):
            response = self.client.post(self.url, self.payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertIn("access", response.data)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertIsNotNone(self.admin.email_verified_at)
        self.assertIsNone(self.admin.activation_token)
        self.assertTrue(self.admin.check_password(PASSWORD))

    def test_the_token_is_single_use(self):
        with patch("users.views.sync_user_to_mailerlite"):
            self.client.post(self.url, self.payload(), format="json")
            second = self.client.post(self.url, self.payload(), format="json")

        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_wrong_token_is_refused(self):
        response = self.client.post(
            self.url, self.payload(token="wrong-token"), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_active)

    def test_an_expired_invite_is_refused_with_guidance(self):
        User.objects.filter(pk=self.admin.pk).update(
            activation_expires=timezone.now() - timezone.timedelta(days=1)
        )

        response = self.client.post(self.url, self.payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("expired", str(response.data).lower())
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_active)

    def test_another_users_email_cannot_redeem_this_token(self):
        other = User.objects.create_user(
            email="other.admin@acme-school.org",
            password="placeholder-password-1",  # pragma: allowlist secret
            first_name="Other",
            last_name="Admin",
            user_type=UserTypes.SCHOOL_ADMIN,
            is_active=False,
        )

        response = self.client.post(
            self.url, self.payload(email=other.email), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        other.refresh_from_db()
        self.assertFalse(other.is_active)

    def test_a_non_school_admin_cannot_use_this_endpoint(self):
        """The lookup pins user_type - a teacher invite is not redeemable here."""
        teacher = User.objects.create_user(
            email="teacher.invite@gmail.com",
            password="placeholder-password-1",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="Invite",
            user_type=UserTypes.TEACHER,
            is_active=False,
            activation_token="admin-invite-token",
            activation_expires=timezone.now() + timezone.timedelta(days=1),
        )

        response = self.client.post(
            self.url, self.payload(email=teacher.email), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        teacher.refresh_from_db()
        self.assertFalse(teacher.is_active)

    def test_an_already_active_admin_cannot_re_register(self):
        User.objects.filter(pk=self.admin.pk).update(is_active=True)

        response = self.client.post(self.url, self.payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_fields_are_a_400(self):
        response = self.client.post(
            self.url, {"email": self.admin.email}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_unexpected_failure_is_reported_without_leaking_internals(self):
        with patch(
            "users.views.RefreshToken.for_user",
            side_effect=RuntimeError("token backend exploded"),
        ), patch("users.views.sync_user_to_mailerlite"):
            response = self.client.post(self.url, self.payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertNotIn("exploded", str(response.data))


@override_settings(CACHES=LOCMEM_CACHE)
class OTPEndpointBranchTests(APITestCase):
    """`/auth/otp` - the branches tests_throttling.py does not reach."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user("otp.branches@gmail.com")
        self.url = reverse("auth-otp")

    def test_an_unknown_email_does_not_reveal_that_it_is_unknown(self):
        """Account enumeration guard: same answer either way."""
        response = self.client.post(
            self.url,
            {"email": "nobody@gmail.com", "otp_type": "RESET_PASSWORD"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertIn("if an account", str(response.data).lower())

    def test_an_invalid_otp_type_is_rejected(self):
        response = self.client.post(
            self.url,
            {"email": self.user.email, "otp_type": "NOT_A_TYPE"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_verify_email_on_an_already_verified_account_is_refused(self):
        response = self.client.post(
            self.url,
            {"email": self.user.email, "otp_type": "VERIFY_EMAIL"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already verified", str(response.data).lower())

    def test_verify_email_resends_for_an_unverified_account(self):
        unverified = make_user(
            "otp.unverified@gmail.com", is_active=False, email_verified_at=None
        )

        with patch("users.views.send_user_activation_email") as mock_send:
            response = self.client.post(
                self.url,
                {"email": unverified.email, "otp_type": "VERIFY_EMAIL"},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        mock_send.assert_called_once()

    def test_reset_password_on_an_unverified_account_is_refused(self):
        unverified = make_user(
            "otp.noreset@gmail.com", is_active=False, email_verified_at=None
        )

        response = self.client.post(
            self.url,
            {"email": unverified.email, "otp_type": "RESET_PASSWORD"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not verified", str(response.data).lower())

    def test_reset_password_issues_a_code(self):
        with patch("users.views.safe_delay") as mock_delay:
            response = self.client.post(
                self.url,
                {"email": self.user.email, "otp_type": "RESET_PASSWORD"},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertIsNotNone(PasswordResetOTP.objects.get(user=self.user).code)
        mock_delay.assert_called_once()


@override_settings(CACHES=LOCMEM_CACHE)
class ResetPasswordBranchTests(APITestCase):
    """`/auth/reset-password` branches not covered by the lockout suite."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user("reset.branches@gmail.com")
        self.url = reverse("auth-reset-password")

    def submit(self, **overrides):
        body = {
            "email": self.user.email,
            "otp": "123456",
            "new_password": "a-replacement-password-77",  # pragma: allowlist secret
        }
        body.update(overrides)
        return self.client.post(self.url, body, format="json")

    def test_an_unknown_email_is_refused_with_the_generic_message(self):
        response = self.submit(email="nobody@gmail.com")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("invalid email, otp code", str(response.data).lower())

    def test_no_outstanding_otp_is_refused_with_the_same_message(self):
        """Identical wording, so nothing distinguishes the two failures."""
        response = self.submit()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("invalid email, otp code", str(response.data).lower())

    def test_an_expired_otp_is_deleted_and_refused(self):
        otp = PasswordResetOTP.objects.create(user=self.user)
        code = otp.generate_code()
        PasswordResetOTP.objects.filter(pk=otp.pk).update(
            created_at=timezone.now() - timezone.timedelta(minutes=16)
        )

        response = self.submit(otp=code)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(PasswordResetOTP.objects.filter(pk=otp.pk).exists())

    def test_a_successful_reset_blacklists_the_pre_existing_tokens(self):
        """
        A password reset has to end sessions that existed before it - that
        is the point of resetting after a compromise.
        """
        old_refresh = RefreshToken.for_user(self.user)
        old_jti = old_refresh["jti"]
        otp = PasswordResetOTP.objects.create(user=self.user)
        code = otp.generate_code()

        response = self.submit(otp=code)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        old_token = OutstandingToken.objects.get(jti=old_jti)
        self.assertTrue(BlacklistedToken.objects.filter(token=old_token).exists())

    def test_the_reset_hands_back_a_working_token(self):
        """
        The freshly issued pair is minted AFTER the blacklisting, so the
        user stays logged in rather than being locked out by their own
        reset.
        """
        RefreshToken.for_user(self.user)
        otp = PasswordResetOTP.objects.create(user=self.user)
        code = otp.generate_code()

        response = self.submit(otp=code)

        new_refresh = response.data["refresh"]
        refreshed = self.client.post(
            reverse("refresh"), {"refresh": new_refresh}, format="json"
        )
        self.assertEqual(refreshed.status_code, status.HTTP_200_OK)


@override_settings(CACHES=LOCMEM_CACHE)
class StudentRegistrationBranchTests(APITestCase):
    """Branches of `register/student` beyond the expiry cases."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.teacher = make_user("student.reg.teacher@gmail.com")
        self.session = Session.objects.create(name="Term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Biology", teacher=self.teacher, session=self.session
        )
        self.student = User.objects.create_user(
            email="pending.student@student.local",
            password="placeholder-password-1",  # pragma: allowlist secret
            first_name="",
            last_name="",
            user_type=UserTypes.STUDENT,
            is_active=False,
            activation_token="student-token",
            activation_expires=timezone.now() + timezone.timedelta(days=1),
        )
        self.url = reverse("auth-register-student")

    def payload(self, **overrides):
        body = {
            "first_name": "Sam",
            "last_name": "Student",
            "password": PASSWORD,
            "token": "student-token",
        }
        body.update(overrides)
        return body

    def test_pending_enrollments_are_promoted_to_enrolled(self):
        enrollment = StudentCourse.objects.create(
            student=self.student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.PENDING,
        )

        with patch("users.views.sync_user_to_mailerlite"):
            response = self.client.post(self.url, self.payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.enrollment_status, EnrollmentStatusType.ENROLLED)

    def test_an_unexpected_failure_is_reported_without_leaking_internals(self):
        with patch(
            "users.views.StudentCourse.objects.filter",
            side_effect=RuntimeError("enrollment lookup exploded"),
        ):
            response = self.client.post(self.url, self.payload(), format="json")

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertNotIn("exploded", str(response.data))
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_active)


@override_settings(CACHES=LOCMEM_CACHE)
class LegacySessionResultsTests(APITestCase):
    """
    `session_results` falls back to `session.results` for sessions created
    before per-task tracking existed. That branch had no coverage.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.teacher = make_user("legacy.session@gmail.com")
        self.client.force_authenticate(user=self.teacher)

    def _session(self, results, total_files):
        session = BatchUploadSession.objects.create(
            teacher=self.teacher, task_type="assignment", total_files=total_files
        )
        BatchUploadSession.objects.filter(pk=session.pk).update(results=results)
        session.refresh_from_db()
        return session

    def test_legacy_results_are_counted_by_status(self):
        session = self._session(
            [
                {"file_name": "a.pdf", "status": "SUCCESS", "error": None},
                {"file_name": "b.pdf", "status": "SUCCESS", "error": None},
                {"file_name": "c.pdf", "status": "FAILED", "error": "unreadable"},
            ],
            total_files=4,
        )

        response = self.client.get(
            reverse("task-session-results", kwargs={"session_id": session.id})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["success_count"], 2)
        self.assertEqual(response.data["failure_count"], 1)
        self.assertEqual(response.data["pending_count"], 1)
        self.assertEqual(response.data["progress"], "3 / 4")
        self.assertFalse(response.data["is_complete"])

    def test_a_completed_legacy_session_reports_complete(self):
        session = self._session(
            [{"file_name": "a.pdf", "status": "SUCCESS", "error": None}],
            total_files=1,
        )

        response = self.client.get(
            reverse("task-session-results", kwargs={"session_id": session.id})
        )

        self.assertTrue(response.data["is_complete"])
        self.assertEqual(response.data["percent"], 100)

    def test_a_legacy_failure_entry_keeps_its_error_text(self):
        session = self._session(
            [{"file_name": "c.pdf", "status": "FAILED", "error": "unreadable"}],
            total_files=1,
        )

        response = self.client.get(
            reverse("task-session-results", kwargs={"session_id": session.id})
        )

        self.assertEqual(response.data["failure_list"][0]["error"], "unreadable")
        self.assertIsNone(response.data["failure_list"][0]["task_id"])

    def test_an_empty_legacy_session_does_not_divide_by_zero(self):
        session = self._session([], total_files=0)

        response = self.client.get(
            reverse("task-session-results", kwargs={"session_id": session.id})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["percent"], 0)


@override_settings(CACHES=LOCMEM_CACHE)
class SuperAdminUserCreationTests(APITestCase):
    """`POST /users` is superadmin-only (the guard inside create())."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.teacher = make_user("create.teacher@gmail.com")
        self.super_admin = make_user(
            "create.super@example.com",
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=True,
        )

    def payload(self, email):
        return {
            "email": email,
            "password": PASSWORD,
            "first_name": "Made",
            "last_name": "ByAdmin",
        }

    def test_a_teacher_cannot_create_users(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.post(
            reverse("user-list"), self.payload("nope@gmail.com"), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(User.objects.filter(email="nope@gmail.com").exists())

    def test_a_superadmin_can_create_a_user(self):
        self.client.force_authenticate(user=self.super_admin)

        with patch("users.serializers.send_user_activation_email"):
            response = self.client.post(
                reverse("user-list"), self.payload("made@gmail.com"), format="json"
            )

        self.assertIn(
            response.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED)
        )
        self.assertTrue(User.objects.filter(email="made@gmail.com").exists())
