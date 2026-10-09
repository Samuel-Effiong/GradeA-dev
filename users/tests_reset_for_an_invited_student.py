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
from users.throttling import verify_lock_until

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

    def test_a_switched_off_account_that_verified_its_email_no_longer_resets(self):
        """REVERSED by H-202 (Senior Manager, 2026-10-08). H-164 pinned the old
        behaviour here: a verified, switched-off account could still reset
        (200, new password set). A switched-off person must not be able to set
        a password on the account, nor be told it worked."""
        student = User.objects.create_user(
            email="verified.off.h164@example.com",
            password="Old-pass-2",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            is_active=False,
            email_verified_at=timezone.now(),
        )

        response = self.reset(student.email, self.code_for(student))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        student.refresh_from_db()
        self.assertFalse(student.check_password(NEW_PASSWORD))

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
        with self.captureOnCommitCallbacks(execute=True):
            teacher = LicenseSubscriptionService._get_or_invite_teacher(
                email, self.school, admin
            )
        if teacher is None:
            self.fail("the invitation made no teacher")
        teacher.refresh_from_db()
        self.assertTrue(teacher.is_active)
        self.assertIsNone(teacher.email_verified_at)
        self.assertTrue(teacher.must_change_password)
        self.assertEqual(self.licence_mail.delay.call_count, 1)
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

        with self.captureOnCommitCallbacks(execute=True):
            again = LicenseSubscriptionService._get_or_invite_teacher(
                teacher.email, self.school, admin
            )

        if again is None:
            self.fail("the re-add returned no teacher")
        self.assertEqual(again.pk, teacher.pk)
        self.assertEqual(
            self.licence_mail.delay.call_count, 1, "a new temporary password"
        )
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


VERIFY_RATES = {**MANY_IPS, "verify_email": "1000/hour"}


@override_settings(CACHES=LOCMEM)
class VerifyEmailAdminPowerTests(APITestCase):
    """Senior Manager's ruling (2026-10-08 19:14): the same three-marker
    condition as the reset, on the VERIFY_EMAIL code request and on
    POST /auth/verify. The code request answers like an unknown address and
    sends nothing; /auth/verify answers with its wrong-code refusal, spends
    the attempt like a wrong guess and writes nothing. The real activation
    mail road runs (only the task's delay is replaced)."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        throttles = patch.dict(SimpleRateThrottle.THROTTLE_RATES, VERIFY_RATES)
        throttles.start()
        self.addCleanup(throttles.stop)
        mail = patch("users.services.send_email_task")
        self.mail = mail.start()
        self.addCleanup(mail.stop)
        for target in (
            "users.views.sync_user_to_mailerlite",
            "users.views.safe_delay",
            "users.views.AnalyticsService",
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.ip = 0

    # -- fixtures --------------------------------------------------------

    def account(self, email, verified=False, active=True, **flags):
        flags.setdefault("user_type", UserTypes.TEACHER)
        return User.objects.create_user(
            email=email,
            password="Verify-pass-1",  # pragma: allowlist secret
            is_active=active,
            email_verified_at=timezone.now() if verified else None,
            **flags,
        )

    def with_token(self, user):
        User.objects.filter(pk=user.pk).update(
            activation_token="424242",
            activation_expires=timezone.now() + timedelta(minutes=15),
        )
        user.refresh_from_db()
        return user

    def ask_for_code(self, email):
        self.ip += 1
        return self.client.post(
            reverse("auth-otp"),
            {"email": email, "otp_type": "VERIFY_EMAIL"},
            format="json",
            REMOTE_ADDR=f"10.202.0.{self.ip}",
        )

    def verify(self, email, token):
        return self.client.post(
            reverse("auth-verify"),
            {"email": email, "token": token},
            format="json",
        )

    def assert_code_request_sends_nothing(self, account):
        response = self.ask_for_code(account.email)

        account.refresh_from_db()
        self.assertIsNone(account.activation_token)
        self.mail.delay.assert_not_called()
        return response

    def assert_code_request_answers_like_an_unknown_address(self, account):
        unknown = self.ask_for_code("nobody.verify.h164@example.com")
        response = self.ask_for_code(account.email)

        self.assertEqual(unknown.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.status_code, unknown.status_code)
        self.assertEqual(response.content, unknown.content)

    def assert_verify_refused_and_nothing_written(self, account):
        self.with_token(account)

        response = self.verify(account.email, "424242")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("access", response.data)
        account.refresh_from_db()
        self.assertIsNone(account.email_verified_at)
        self.assertEqual(account.activation_token, "424242")
        self.assertIsNotNone(account.activation_expires)

    def assert_verify_refusal_is_the_wrong_code_refusal(self, account):
        self.with_token(account)

        refused = self.verify(account.email, "424242")
        wrong = self.verify(account.email, "000000")

        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(refused.status_code, wrong.status_code)
        self.assertEqual(refused.content, wrong.content)

    def staff_only(self, email):
        return self.account(email, is_staff=True)

    def flag_only(self, email):
        return self.account(email, is_superuser=True)

    def type_only(self, email):
        return self.account(email, user_type=UserTypes.SUPER_ADMIN)

    def command_line(self, email):
        account = User.objects.create_superuser(
            email=email,
            password="Verify-pass-2",  # pragma: allowlist secret
        )
        self.assertEqual(account.user_type, UserTypes.TEACHER)
        self.assertIsNone(account.email_verified_at)
        return account

    # -- the code request ------------------------------------------------

    def test_the_code_request_sends_nothing_to_a_never_verified_staff_account(self):
        self.assert_code_request_sends_nothing(self.staff_only("vqs.h164@x.example"))

    def test_the_code_request_sends_nothing_to_a_never_verified_superuser_flag(self):
        self.assert_code_request_sends_nothing(self.flag_only("vqf.h164@x.example"))

    def test_the_code_request_sends_nothing_to_a_never_verified_super_admin_type(self):
        self.assert_code_request_sends_nothing(self.type_only("vqt.h164@x.example"))

    def test_the_code_request_sends_nothing_to_a_command_line_superuser(self):
        self.assert_code_request_sends_nothing(self.command_line("vqc.h164@x.example"))

    def test_that_code_request_answer_for_a_staff_account_is_the_unknown_ones(self):
        self.assert_code_request_answers_like_an_unknown_address(
            self.staff_only("vqws.h164@x.example")
        )

    def test_that_code_request_answer_for_a_superuser_flag_is_the_unknown_ones(self):
        self.assert_code_request_answers_like_an_unknown_address(
            self.flag_only("vqwf.h164@x.example")
        )

    def test_that_code_request_answer_for_a_super_admin_type_is_the_unknown_ones(self):
        self.assert_code_request_answers_like_an_unknown_address(
            self.type_only("vqwt.h164@x.example")
        )

    # -- POST /auth/verify -----------------------------------------------

    def test_verify_refuses_a_never_verified_staff_account(self):
        self.assert_verify_refused_and_nothing_written(
            self.staff_only("vvs.h164@x.example")
        )

    def test_verify_refuses_a_never_verified_superuser_flag(self):
        self.assert_verify_refused_and_nothing_written(
            self.flag_only("vvf.h164@x.example")
        )

    def test_verify_refuses_a_never_verified_super_admin_type(self):
        self.assert_verify_refused_and_nothing_written(
            self.type_only("vvt.h164@x.example")
        )

    def test_verify_refuses_a_command_line_superuser(self):
        self.assert_verify_refused_and_nothing_written(
            self.command_line("vvc.h164@x.example")
        )

    def test_that_verify_refusal_for_a_staff_account_is_the_wrong_code_one(self):
        self.assert_verify_refusal_is_the_wrong_code_refusal(
            self.staff_only("vvws.h164@x.example")
        )

    def test_that_verify_refusal_for_a_superuser_flag_is_the_wrong_code_one(self):
        self.assert_verify_refusal_is_the_wrong_code_refusal(
            self.flag_only("vvwf.h164@x.example")
        )

    def test_that_verify_refusal_for_a_super_admin_type_is_the_wrong_code_one(self):
        self.assert_verify_refusal_is_the_wrong_code_refusal(
            self.type_only("vvwt.h164@x.example")
        )

    @override_settings(VERIFY_EMAIL_MAX_FAILURES=2)
    def test_a_refused_verify_spends_the_budget_like_a_wrong_guess(self):
        account = self.with_token(self.flag_only("vvb.h164@x.example"))

        self.verify(account.email, "424242")
        self.verify(account.email, "424242")
        third = self.verify(account.email, "424242")

        self.assertEqual(third.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    @override_settings(VERIFY_EMAIL_MAX_FAILURES=2)
    def test_a_refused_verify_on_an_admin_power_account_locks_the_address_like_a_wrong_guess(
        self,
    ):
        """The budget is spent as for a wrong guess only if the address is
        locked when it runs out (the lock is also what stops a new code being
        mailed to it). Its own test, so the budget test keeps one reason to
        fail."""
        account = self.with_token(self.command_line("vvl.h164@x.example"))

        self.verify(account.email, "424242")
        self.assertIsNone(verify_lock_until(account.email))
        self.verify(account.email, "424242")

        self.assertIsNotNone(verify_lock_until(account.email))

    # -- controls: nothing else changes ------------------------------------

    def test_a_verified_admin_still_gets_the_already_verified_answer(self):
        """Green on the old code too."""
        account = self.account(
            "vcv.h164@x.example",
            verified=True,
            is_staff=True,
            is_superuser=True,
            user_type=UserTypes.SUPER_ADMIN,
        )

        response = self.ask_for_code(account.email)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_verified_admin_can_still_verify(self):
        """Green on the old code too."""
        account = self.account(
            "vcw.h164@x.example",
            verified=True,
            is_superuser=True,
            user_type=UserTypes.SUPER_ADMIN,
        )
        self.with_token(account)

        response = self.verify(account.email, "424242")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertIn("access", response.data)

    def test_an_ordinary_unverified_user_still_activates_end_to_end(self):
        """Green on the old code too: a self-registered account (inactive,
        never verified) asks for a code, gets one, and verifying makes it
        active and verified."""
        user = self.account("vco.h164@x.example", active=False)

        self.ask_for_code(user.email)
        user.refresh_from_db()
        self.assertIsNotNone(user.activation_token)
        self.mail.delay.assert_called_once()
        response = self.verify(user.email, user.activation_token)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertIsNotNone(user.email_verified_at)

    def test_an_invited_school_admin_still_verifies(self):
        """Green on the old code too: an invited school admin (inactive,
        never verified, a 7-day token) is not an admin-power account in this
        sense."""
        school = School.objects.create(name="Verify High")
        admin = self.account(
            "vcs.h164@x.example",
            active=False,
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
        )
        self.with_token(admin)

        response = self.verify(admin.email, "424242")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        admin.refresh_from_db()
        self.assertTrue(admin.is_active)
        self.assertIsNotNone(admin.email_verified_at)

    def test_a_licence_invited_teacher_still_verifies_end_to_end(self):
        """Green on the old code too: an active, never-verified licence
        teacher asks for a code and verifies with it."""
        teacher = self.account("vcl.h164@x.example")

        self.ask_for_code(teacher.email)
        teacher.refresh_from_db()
        self.assertIsNotNone(teacher.activation_token)
        response = self.verify(teacher.email, teacher.activation_token)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        teacher.refresh_from_db()
        self.assertIsNotNone(teacher.email_verified_at)
