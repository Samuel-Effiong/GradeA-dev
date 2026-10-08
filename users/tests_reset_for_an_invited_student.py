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

  * A reset is refused, and nothing is stamped or changed, for an account that
    is INACTIVE and never verified, whatever code exists (a code issued while
    the account was active, then the account switched off). Same generic 400 as
    an unknown address. The code already issued is left as it is.

  * A reset step AND a request step refuse an account that never verified its
    email and holds admin power (is_staff, is_superuser or user type
    SUPER_ADMIN, any one of them): nobody gets a new first road into the
    highest accounts by a mailbox code. The request answers exactly as the
    inactive refusal does; the reset answers like an unknown address. A
    VERIFIED super admin is unchanged. A licence-invited teacher has the road
    like an invited student.

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

from billing.license_service import LicenseSubscriptionService
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

    # -- a switched-off, never-verified account (Senior Manager's ruling) --

    def switched_off_with_a_code(self):
        """An invited student asks for a code, then the account is switched
        off (still never verified); the code exists, as the request step made
        it. Adopted from Verifier 1's v6."""
        student = self.invite()
        code = self.code_for(student)
        User.objects.filter(pk=student.pk).update(is_active=False)
        student.refresh_from_db()
        return student, code

    def test_a_reset_is_refused_for_a_switched_off_never_verified_account(self):
        student, code = self.switched_off_with_a_code()
        before = student.password

        response = self.reset(student.email, code)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("access", response.data)
        student.refresh_from_db()
        self.assertIsNone(student.email_verified_at)
        self.assertEqual(student.password, before)

    def test_that_refusal_is_the_same_as_for_an_address_with_no_account(self):
        student, code = self.switched_off_with_a_code()

        refused = self.reset(student.email, code)
        unknown = self.reset("nobody.switched.off.h164@example.com", code)

        self.assertEqual(unknown.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(refused.status_code, unknown.status_code)
        self.assertEqual(refused.content, unknown.content)

    def test_a_refused_reset_leaves_the_code_and_its_budget_alone(self):
        """Decision: the code already issued is LEFT. A refusal writes nothing
        (no attempt counted, no row deleted); the guard looks at the account
        every time, and the code expires by itself."""
        student, code = self.switched_off_with_a_code()

        self.reset(student.email, code)

        otp = PasswordResetOTP.objects.get(user=student)
        self.assertEqual(otp.code, code)
        self.assertEqual(otp.attempts, 0)

    def test_a_switched_off_account_that_verified_its_email_still_resets(self):
        """Green on the old code too: the guard is for never-verified accounts
        only; what a verified switched-off account could do is unchanged."""
        student = User.objects.create_user(
            email="verified.off.h164@example.com",
            password="Old-pass-2",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            is_active=False,
            email_verified_at=timezone.now(),
        )

        response = self.reset(student.email, self.code_for(student))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        student.refresh_from_db()
        self.assertTrue(student.check_password(NEW_PASSWORD))

    # -- a licence-invited teacher (Senior Manager's ruling on N3) ---------

    def licence_invite(self, email="licence.teacher@h164-school.edu"):
        """The real path: a school admin adds a teacher to a licence. The
        mail is replaced; its arguments are read."""
        mail = patch("billing.license_service.send_email_task")
        self.licence_mail = mail.start()
        self.addCleanup(mail.stop)
        admin = User.objects.create_user(
            email="h164.school.admin@h164-school.edu",
            password="Admin-pass-1",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=True,
            email_verified_at=timezone.now(),
        )
        teacher = LicenseSubscriptionService._get_or_invite_teacher(
            email, self.school, admin
        )
        if teacher is None:
            self.fail("the invitation made no teacher")
        teacher.refresh_from_db()
        self.assertTrue(teacher.is_active)
        self.assertIsNone(teacher.email_verified_at)
        self.assertTrue(teacher.must_change_password)
        self.assertEqual(self.licence_mail.call_count, 1)
        return teacher, admin

    def test_a_licence_invited_teacher_is_sent_a_reset_code(self):
        teacher, _ = self.licence_invite()

        response = self.request_reset(teacher.email)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.sent_mock.assert_called_once()
        self.assertEqual(
            self.sent_mock.call_args.kwargs["recipient_list"], [teacher.email]
        )

    def test_a_licence_invited_teachers_reset_stamps_and_signs_them_in(self):
        teacher, _ = self.licence_invite()

        response = self.reset(teacher.email, self.code_for(teacher))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        teacher.refresh_from_db()
        self.assertIsNotNone(teacher.email_verified_at)
        self.assertFalse(teacher.must_change_password)
        self.assertIsNotNone(teacher.last_login)

    def test_a_licence_re_add_after_a_reset_keeps_the_password_they_chose(self):
        """What this row creates for teachers: an invited teacher who has
        never signed in can use Forgot password; after that a re-invite no
        longer replaces their password."""
        teacher, admin = self.licence_invite()
        self.reset(teacher.email, self.code_for(teacher))

        again = LicenseSubscriptionService._get_or_invite_teacher(
            teacher.email, self.school, admin
        )

        if again is None:
            self.fail("the re-add returned no teacher")
        self.assertEqual(again.pk, teacher.pk)
        self.assertEqual(self.licence_mail.call_count, 1, "a new temporary password")
        self.client.credentials()
        signed_in = self.client.post(
            reverse("login"),
            {"email": teacher.email, "password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(signed_in.status_code, status.HTTP_200_OK)

    # -- an account with admin power that never verified its email ---------

    def never_verified(self, email, **flags):
        flags.setdefault("user_type", UserTypes.TEACHER)
        return User.objects.create_user(
            email=email,
            password="Power-pass-1",  # pragma: allowlist secret
            is_active=True,
            email_verified_at=None,
            **flags,
        )

    def assert_request_refused(self, account):
        response = self.request_reset(account.email)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(PasswordResetOTP.objects.filter(user=account).exists())
        self.sent_mock.assert_not_called()

    def assert_reset_refused(self, account):
        code = self.code_for(account)
        before = account.password

        response = self.reset(account.email, code)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("access", response.data)
        account.refresh_from_db()
        self.assertIsNone(account.email_verified_at)
        self.assertEqual(account.password, before)

    def test_the_request_refuses_a_never_verified_staff_account(self):
        self.assert_request_refused(
            self.never_verified("staff.h164@x.example", is_staff=True)
        )

    def test_the_request_refuses_a_never_verified_superuser_flag(self):
        self.assert_request_refused(
            self.never_verified("flag.h164@x.example", is_superuser=True)
        )

    def test_the_request_refuses_a_never_verified_super_admin_type(self):
        self.assert_request_refused(
            self.never_verified("type.h164@x.example", user_type=UserTypes.SUPER_ADMIN)
        )

    def test_the_request_refuses_a_command_line_superuser(self):
        """create_superuser sets is_staff and is_superuser and leaves the
        user type at its default, TEACHER: the flags, not the type, name it."""
        account = User.objects.create_superuser(
            email="cmdline.h164@x.example",
            password="Power-pass-2",  # pragma: allowlist secret
        )
        self.assertEqual(account.user_type, UserTypes.TEACHER)
        self.assertIsNone(account.email_verified_at)

        self.assert_request_refused(account)

    def test_that_refusal_says_what_the_inactive_refusal_says(self):
        inactive = self.unverified_inactive()
        power = self.never_verified("words.h164@x.example", is_superuser=True)

        refused_inactive = self.request_reset(inactive.email)
        refused_power = self.request_reset(power.email)

        self.assertEqual(refused_power.status_code, refused_inactive.status_code)
        self.assertEqual(refused_power.content, refused_inactive.content)

    def test_the_reset_refuses_a_never_verified_staff_account(self):
        self.assert_reset_refused(
            self.never_verified("rstaff.h164@x.example", is_staff=True)
        )

    def test_the_reset_refuses_a_never_verified_superuser_flag(self):
        self.assert_reset_refused(
            self.never_verified("rflag.h164@x.example", is_superuser=True)
        )

    def test_the_reset_refuses_a_never_verified_super_admin_type(self):
        self.assert_reset_refused(
            self.never_verified("rtype.h164@x.example", user_type=UserTypes.SUPER_ADMIN)
        )

    def test_the_reset_refuses_a_command_line_superuser(self):
        account = User.objects.create_superuser(
            email="rcmdline.h164@x.example",
            password="Power-pass-3",  # pragma: allowlist secret
        )

        self.assert_reset_refused(account)

    def test_that_reset_refusal_is_the_same_as_for_an_address_with_no_account(self):
        power = self.never_verified("rwords.h164@x.example", is_superuser=True)
        code = self.code_for(power)

        refused = self.reset(power.email, code)
        unknown = self.reset("nobody.power.h164@example.com", code)

        self.assertEqual(refused.status_code, unknown.status_code)
        self.assertEqual(refused.content, unknown.content)

    def verified_super_admin(self, email):
        return User.objects.create_user(
            email=email,
            password="Power-pass-4",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
            is_staff=True,
            is_superuser=True,
            is_active=True,
            email_verified_at=timezone.now(),
        )

    def test_a_verified_super_admin_is_still_sent_a_reset_code(self):
        """Green on the old code too: the refusal is for accounts that never
        verified their email, nothing else."""
        account = self.verified_super_admin("vsuper.h164@x.example")

        response = self.request_reset(account.email)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.sent_mock.assert_called_once()

    def test_a_verified_super_admin_can_still_reset(self):
        """Green on the old code too."""
        account = self.verified_super_admin("vsuperreset.h164@x.example")

        response = self.reset(account.email, self.code_for(account))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        account.refresh_from_db()
        self.assertTrue(account.check_password(NEW_PASSWORD))
