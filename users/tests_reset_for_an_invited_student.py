"""
H-164: "forgot password" works for an invited student who has never signed in.

WHAT WAS WRONG (found by reading, 2026-10-07)
---------------------------------------------
A teacher adds a student by email. `enroll_student_by_email` creates the
account ACTIVE with emailed first-time login details and `email_verified_at`
EMPTY (the invitation proves nothing about the mailbox). A student who lost
that email used the forgot-password link, which calls `POST /auth/otp` with
RESET_PASSWORD, and was refused with "Email not verified.". They were not locked out (the
verify-email road, then a reset, or Google sign-in, get them in) but the
obvious road did not work.

THE RULE (option A, approved 2026-10-08)
----------------------------------------
  * The request step refuses a never-verified account only if it is also
    INACTIVE (a self-registered row, or an old-scheme pending student). An
    active never-verified account (an invited student) is sent a reset code,
    with the same neutral 202 as an unknown address. The reset lock is
    unchanged.
  * A SUCCESSFUL reset stamps `email_verified_at` (the code proves control of
    the mailbox as the verify link does), in the same save as the new
    password; never on the request, never on a wrong code.
  * A successful reset clears `must_change_password`: the temporary password
    it stood for has just been replaced by the student's own.
  * A reset signs the student in (it returns tokens), so it stamps
    `last_login` like every other sign-in: otherwise a later add of the
    student to another course would see "never signed in" and overwrite the
    password they just chose with a fresh temporary one.

Run with:
    python manage.py test users.tests_reset_for_an_invited_student
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework.throttling import SimpleRateThrottle

from classrooms.models import Course, School, Session
from classrooms.services import enroll_student_by_email
from users.models import PasswordResetOTP, UserTypes

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
MANY_IPS = {"otp_request": "1000/hour", "login": "1000/min", "anon": "1000/min"}

NEW_PASSWORD = "a-new-strong-passphrase-for-h164"  # pragma: allowlist secret


@override_settings(CACHES=LOCMEM)
class InvitedStudentResetTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        throttles = patch.dict(SimpleRateThrottle.THROTTLE_RATES, MANY_IPS)
        throttles.start()
        self.addCleanup(throttles.stop)
        # Sends go through these; their arguments are read, nothing is sent.
        self.sent = patch("users.views.safe_delay")
        self.sent_mock = self.sent.start()
        self.addCleanup(self.sent.stop)
        for target in (
            "classrooms.services.notifications.safe_delay",
            "users.views.send_user_activation_email",
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)

        self.school = School.objects.create(name="Reset High")
        self.teacher = User.objects.create_user(
            email="h164.teacher@reset-high.example",
            password="Teacher-pass-1",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            school=self.school,
            is_active=True,
        )
        self.session = Session.objects.create(name="Term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Maths", teacher=self.teacher, session=self.session
        )
        self.other_course = Course.objects.create(
            name="Physics", teacher=self.teacher, session=self.session
        )
        self.ip = 0

    # -- fixtures --------------------------------------------------------

    def invite(self, email="invited.h164@example.com"):
        """The real path: a teacher adds the student by email."""
        student, invited = enroll_student_by_email(course=self.course, email=email)
        self.assertTrue(invited)
        student.refresh_from_db()
        self.assertTrue(student.is_active)
        self.assertIsNone(student.email_verified_at)
        self.assertTrue(student.must_change_password)
        self.assertIsNone(student.last_login)
        return student

    def unverified_inactive(self, email="pending.h164@example.com"):
        return User.objects.create_user(
            email=email,
            password="Whatever-1",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            is_active=False,
            email_verified_at=None,
        )

    def request_reset(self, email):
        self.ip += 1
        return self.client.post(
            reverse("auth-otp"),
            {"email": email, "otp_type": "RESET_PASSWORD"},
            format="json",
            REMOTE_ADDR=f"10.164.0.{self.ip}",
        )

    def code_for(self, student):
        otp, _ = PasswordResetOTP.objects.get_or_create(user=student)
        return otp.generate_code()

    def reset(self, email, code, password=NEW_PASSWORD):
        return self.client.post(
            reverse("auth-reset-password"),
            {"email": email, "otp": code, "new_password": password},
            format="json",
        )

    # -- the request step -------------------------------------------------

    def test_an_invited_student_who_never_signed_in_is_sent_a_reset_code(self):
        student = self.invite()

        response = self.request_reset(student.email)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertTrue(PasswordResetOTP.objects.filter(user=student).exists())
        self.sent_mock.assert_called_once()
        self.assertEqual(
            self.sent_mock.call_args.kwargs["recipient_list"], [student.email]
        )

    def test_the_reply_is_the_same_as_for_an_address_with_no_account(self):
        student = self.invite()

        unknown = self.request_reset("nobody.h164@example.com")
        invited = self.request_reset(student.email)

        self.assertEqual(unknown.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(invited.status_code, unknown.status_code)
        self.assertEqual(invited.content, unknown.content)

    def test_an_inactive_never_verified_row_is_still_refused(self):
        """Green on the old code too: a self-registered row or an old-scheme
        pending student does not get a reset code."""
        pending = self.unverified_inactive()

        response = self.request_reset(pending.email)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(PasswordResetOTP.objects.filter(user=pending).exists())
        self.sent_mock.assert_not_called()

    def test_a_locked_reset_sends_nothing_and_answers_the_same(self):
        student = self.invite()
        PasswordResetOTP.objects.create(
            user=student, locked_until=timezone.now() + timedelta(minutes=30)
        )
        unknown = self.request_reset("nobody.locked.h164@example.com")

        response = self.request_reset(student.email)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.content, unknown.content)
        self.sent_mock.assert_not_called()

    def test_the_request_alone_does_not_verify_the_email(self):
        student = self.invite()

        self.request_reset(student.email)

        student.refresh_from_db()
        self.assertIsNone(student.email_verified_at)

    # -- the reset step ---------------------------------------------------

    def test_a_successful_reset_sets_the_password_and_stamps_the_email(self):
        student = self.invite()
        temporary_hash = student.password
        code = self.code_for(student)

        response = self.reset(student.email, code)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        student.refresh_from_db()
        self.assertTrue(student.check_password(NEW_PASSWORD))
        self.assertNotEqual(student.password, temporary_hash)
        self.assertIsNotNone(student.email_verified_at)

    def test_a_wrong_code_stamps_nothing(self):
        student = self.invite()
        code = self.code_for(student)
        wrong = "000000" if code != "000000" else "111111"

        response = self.reset(student.email, wrong)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        student.refresh_from_db()
        self.assertIsNone(student.email_verified_at)
        self.assertFalse(student.check_password(NEW_PASSWORD))

    def test_a_successful_reset_clears_must_change_password(self):
        student = self.invite()

        self.reset(student.email, self.code_for(student))

        student.refresh_from_db()
        self.assertFalse(student.must_change_password)

    def test_a_reset_leaves_an_established_accounts_flag_alone(self):
        """Green on the old code too: a verified user whose flag is false
        stays false (nothing invents the flag)."""
        established = User.objects.create_user(
            email="established.h164@example.com",
            password="Old-pass-1",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            is_active=True,
            email_verified_at=timezone.now(),
        )
        verified_at = established.email_verified_at

        self.reset(established.email, self.code_for(established))

        established.refresh_from_db()
        self.assertFalse(established.must_change_password)
        self.assertEqual(established.email_verified_at, verified_at)

    def test_a_reset_signs_the_student_in(self):
        student = self.invite()

        self.reset(student.email, self.code_for(student))

        student.refresh_from_db()
        self.assertIsNotNone(student.last_login)

    def test_a_later_add_to_another_course_keeps_the_password_they_chose(self):
        """The reason the reset stamps last_login: without it the add sees
        "never signed in" and overwrites the new password with a fresh
        temporary one, emailing it."""
        student = self.invite()
        self.reset(student.email, self.code_for(student))

        same, invited = enroll_student_by_email(
            course=self.other_course, email=student.email
        )

        self.assertFalse(invited)
        same.refresh_from_db()
        self.assertTrue(same.check_password(NEW_PASSWORD))
