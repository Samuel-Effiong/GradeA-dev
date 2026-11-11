"""
H-34: a super admin can lock and unlock a user's sign-in.

THE RULES (owner and SM rulings, 2026-11-11; docs/evidence/h34/NOTES.md)
-----------------------------------------------------------------------
* A new state, `CustomUser.admin_locked_at` (nullable), not `is_active`.
  Locking stamps it and raises `token_epoch` once (every live session ends).
  The lock lasts until a super admin unlocks.
* Every door refuses a locked account. Password sign-in and Google sign-in
  answer 401 ACCOUNT_LOCKED_BY_ADMIN with one of the owner's two approved
  sentences, BEFORE the password is checked, and count nothing. Verify,
  invited-admin activation and reset give the generic answer each already
  gives. A reset or verify code request is neutral and mails nothing. The
  per-request auth class refuses a locked user's token (the backstop).
* Unlock clears `admin_locked_at`, `failed_login_attempts` and
  `locked_until`. It never touches `is_active`.
* Super admins only (scope (a)): POST users/{id}/lock/ and /unlock/, and the
  back-office actions "Lock sign-in" / "Unlock sign-in". Nobody locks
  themselves or a super admin: the existing generic PermissionDenied.
* R1: message 1 (school administrator) for a user with a school, or for a
  student with a non-WITHDRAWN enrollment in a course whose teacher has a
  school; message 2 (the Grade A Plus team) otherwise.
* R2: remediation "" (null in the body); message 2 is the spec's
  "no_school" alternative.
* R3: a successful lock or unlock leaves exactly ONE event naming the
  requester (ACCOUNT_LOCK / ACCOUNT_UNLOCK); the ADMIN_ACTION tag is dropped
  by the helper that writes the named event, and only there.

The two sentences are copied byte for byte from the main checkout's
team/approved_wording_h34_admin_lock_2026-11-11.md, sha256
d3d1a12e7b02be9dda85771b3cdd3a505ca2712b5e616c5b983bf9081a64e229.

Run with:
    python manage.py test users.tests_admin_sign_in_lock
"""

import ast
import pathlib
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework.throttling import SimpleRateThrottle

from audit.models import AuditEvent
from classrooms.models import Course, EnrollmentStatusType, School, StudentCourse
from users.models import PasswordResetOTP, UserTypes
from users.tokens import EpochRefreshToken

User = get_user_model()

MESSAGE_SCHOOL = (
    "An administrator has locked your account. "
    "Please contact your school administrator to unlock it."
)
MESSAGE_NO_SCHOOL = (
    "An administrator has locked your account. "
    "Please contact the Grade A Plus team to unlock it."
)
CODE = "ACCOUNT_LOCKED_BY_ADMIN"
GENERIC_DENIED = "You do not have permission to perform this action."
PW = "Lock-h34-pass-1"  # pragma: allowlist secret
NEW_PW = "a-new-strong-passphrase-for-h34"  # pragma: allowlist secret
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
RATES = {
    "otp_request": "1000/hour",
    "login": "1000/min",
    "anon": "1000/min",
    "verify_email": "1000/hour",
    "password_reset": "1000/hour",  # pragma: allowlist secret
    "google_auth": "1000/hour",
}


class Base(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        throttles = patch.dict(SimpleRateThrottle.THROTTLE_RATES, RATES)
        throttles.start()
        self.addCleanup(throttles.stop)
        self.mock_send_email_task = self.patched("users.services.send_email_task")
        self.mock_safe_delay = self.patched("users.views.safe_delay")
        self.patched("users.views.sync_user_to_mailerlite")
        self.patched("users.views.AnalyticsService")
        self.school = School.objects.create(name="H34 Lock High")
        self.admin = self.super_admin("operator.h34@x.example")

    def patched(self, target):
        patcher = patch(target)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    # -- fixtures --------------------------------------------------------

    def person(self, email, **extra):
        fields = {
            "is_active": True,
            "email_verified_at": timezone.now(),
            "user_type": UserTypes.TEACHER,
            **extra,
        }
        return User.objects.create_user(email=email, password=PW, **fields)

    def super_admin(self, email):
        user = User.objects.create_superuser(email=email, password=PW)
        User.objects.filter(pk=user.pk).update(
            user_type=UserTypes.SUPER_ADMIN, email_verified_at=timezone.now()
        )
        user.refresh_from_db()
        return user

    def locked(self, user):
        User.objects.filter(pk=user.pk).update(admin_locked_at=timezone.now())
        user.refresh_from_db()
        return user

    def as_user(self, user):
        client = self.client_class()
        client.force_authenticate(user=user)
        return client

    def lock(self, target, by=None):
        return self.as_user(by or self.admin).post(
            reverse("user-lock", kwargs={"pk": target.pk}), {}, format="json"
        )

    def unlock(self, target, by=None):
        return self.as_user(by or self.admin).post(
            reverse("user-unlock", kwargs={"pk": target.pk}), {}, format="json"
        )

    def login(self, email, password=PW):
        return self.client_class().post(
            reverse("login"), {"email": email, "password": password}, format="json"
        )

    def fields(self, response):
        return response.json()["error"]["field_errors"]

    def events_naming(self, actor):
        return list(AuditEvent.objects.filter(actor_id=actor.pk))

    def epoch(self, user):
        return User.objects.values_list("token_epoch", flat=True).get(pk=user.pk)


# ---------------------------------------------------------------------------
# L1: the lock ends every session
# ---------------------------------------------------------------------------


@override_settings(CACHES=LOCMEM)
class L1LockEndsSessionsTests(Base):
    def test_lock_stamps_the_account_and_raises_the_epoch_once(self):
        target = self.person("l1.h34@x.example")
        before = self.epoch(target)

        response = self.lock(target)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        target.refresh_from_db()
        self.assertIsNotNone(target.admin_locked_at)
        self.assertEqual(target.token_epoch, before + 1)

    def test_an_access_token_from_before_the_lock_is_refused(self):
        target = self.person("l1b.h34@x.example")
        access = str(EpochRefreshToken.for_user(target).access_token)

        self.lock(target)

        client = self.client_class()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(
            client.get(reverse("user-me")).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_its_refresh_token_is_refused(self):
        target = self.person("l1c.h34@x.example")
        refresh = str(EpochRefreshToken.for_user(target))

        self.lock(target)

        response = self.client_class().post(
            reverse("refresh"), {"refresh": refresh}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


# ---------------------------------------------------------------------------
# L2: password sign-in is refused before the password is checked
# ---------------------------------------------------------------------------


@override_settings(CACHES=LOCMEM)
class L2PasswordSignInTests(Base):
    def assert_lock_answer(self, response, message):
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("access", response.json())
        fields = self.fields(response)
        self.assertEqual(fields["reason_code"], CODE)
        self.assertEqual(fields["code"], CODE)
        self.assertEqual(fields["detail"], message)
        self.assertEqual(response.json()["message"], message)
        self.assertEqual(fields["error_class"], "USER")
        self.assertIsNone(fields["remediation"])
        self.assertIs(fields["retryable"], False)
        self.assertEqual(fields["params"], {})

    def test_the_correct_password_gets_the_lock_answer(self):
        target = self.locked(self.person("l2.h34@x.example", school=self.school))

        self.assert_lock_answer(self.login(target.email), MESSAGE_SCHOOL)

    def test_a_wrong_password_gets_the_same_answer_and_is_not_counted(self):
        target = self.locked(self.person("l2b.h34@x.example", school=self.school))

        response = self.login(target.email, "not-the-password-1")

        self.assert_lock_answer(response, MESSAGE_SCHOOL)
        target.refresh_from_db()
        self.assertEqual(target.failed_login_attempts, 0)
        self.assertIsNone(target.locked_until)

    def test_the_password_is_never_checked(self):
        target = self.locked(self.person("l2c.h34@x.example"))

        with patch.object(User, "check_password") as checked:
            self.login(target.email)

        checked.assert_not_called()

    def test_one_denied_sign_in_event_names_the_account_as_target(self):
        target = self.locked(self.person("l2d.h34@x.example"))

        self.login(target.email)

        events = AuditEvent.objects.filter(
            action="AUTH_LOGIN", target_id=str(target.pk)
        )
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.outcome, "DENIED")
        self.assertEqual(event.reason_code, CODE)
        self.assertNotEqual(event.actor_id, str(target.pk))

    def test_the_admin_lock_answers_even_during_a_five_try_lock(self):
        target = self.locked(self.person("l2e.h34@x.example"))
        User.objects.filter(pk=target.pk).update(
            failed_login_attempts=5,
            locked_until=timezone.now() + timedelta(minutes=15),
        )

        self.assert_lock_answer(self.login(target.email), MESSAGE_NO_SCHOOL)


# ---------------------------------------------------------------------------
# R1 + R2: which approved sentence, and the spec itself
# ---------------------------------------------------------------------------


@override_settings(CACHES=LOCMEM)
class R1WhichSentenceTests(Base):
    def course_of(self, teacher, name):
        return Course.objects.create(name=name, teacher=teacher)

    def enrol(self, student, course, status_=EnrollmentStatusType.ENROLLED):
        return StudentCourse.objects.create(
            student=student, course=course, enrollment_status=status_
        )

    def detail_for(self, user):
        self.locked(user)
        return self.login(user.email).json()["error"]["field_errors"]["detail"]

    def school_teacher(self):
        return self.person("school.teacher.h34@x.example", school=self.school)

    def independent_teacher(self):
        return self.person("indie.teacher.h34@x.example")

    def student(self, email="student.h34@x.example"):
        return self.person(email, user_type=UserTypes.STUDENT)

    def test_a_teacher_with_a_school_gets_the_school_sentence(self):
        self.assertEqual(self.detail_for(self.school_teacher()), MESSAGE_SCHOOL)

    def test_a_school_admin_gets_the_school_sentence(self):
        admin = self.person(
            "sa.h34@x.example", user_type=UserTypes.SCHOOL_ADMIN, school=self.school
        )
        self.assertEqual(self.detail_for(admin), MESSAGE_SCHOOL)

    def test_an_independent_teacher_gets_the_team_sentence(self):
        self.assertEqual(self.detail_for(self.independent_teacher()), MESSAGE_NO_SCHOOL)

    def test_a_student_with_no_enrollment_gets_the_team_sentence(self):
        self.assertEqual(self.detail_for(self.student()), MESSAGE_NO_SCHOOL)

    def test_a_student_of_a_school_teacher_gets_the_school_sentence(self):
        student = self.student()
        self.enrol(student, self.course_of(self.school_teacher(), "H34 A"))
        self.assertEqual(self.detail_for(student), MESSAGE_SCHOOL)

    def test_a_pending_student_of_a_school_teacher_gets_the_school_sentence(self):
        student = self.student()
        self.enrol(
            student,
            self.course_of(self.school_teacher(), "H34 P"),
            EnrollmentStatusType.PENDING,
        )
        self.assertEqual(self.detail_for(student), MESSAGE_SCHOOL)

    def test_a_student_with_only_an_independent_teacher_gets_the_team_sentence(self):
        student = self.student()
        self.enrol(student, self.course_of(self.independent_teacher(), "H34 B"))
        self.assertEqual(self.detail_for(student), MESSAGE_NO_SCHOOL)

    def test_a_student_only_ever_withdrawn_gets_the_team_sentence(self):
        student = self.student()
        self.enrol(
            student,
            self.course_of(self.school_teacher(), "H34 C"),
            EnrollmentStatusType.WITHDRAWN,
        )
        self.assertEqual(self.detail_for(student), MESSAGE_NO_SCHOOL)

    def test_a_student_with_both_kinds_of_enrollment_gets_the_school_sentence(self):
        student = self.student()
        self.enrol(student, self.course_of(self.independent_teacher(), "H34 D"))
        self.enrol(student, self.course_of(self.school_teacher(), "H34 E"))
        self.assertEqual(self.detail_for(student), MESSAGE_SCHOOL)


class R2SpecTests(SimpleTestCase):
    def spec(self):
        from audit.enums import ReasonCode
        from AutoGrader.reason_codes import REASON_CODES

        return REASON_CODES[ReasonCode(CODE)]

    def test_both_sentences_are_the_approved_texts_byte_for_byte(self):
        spec = self.spec()
        self.assertEqual(spec.message.encode(), MESSAGE_SCHOOL.encode())
        self.assertEqual(
            {k: v.encode() for k, v in spec.alternative_messages.items()},
            {"no_school": MESSAGE_NO_SCHOOL.encode()},
        )

    def test_no_third_text_the_remediation_is_nothing_to_do(self):
        self.assertEqual(self.spec().remediation, "")
        self.assertEqual(self.spec().alternative_remediations, ())

    def test_a_401_user_error_that_is_not_retryable_and_takes_no_params(self):
        spec = self.spec()
        self.assertEqual(spec.http_status, 401)
        self.assertEqual(spec.error_class.value, "USER")
        self.assertIs(spec.retryable, False)
        self.assertEqual(spec.params, frozenset())


# ---------------------------------------------------------------------------
# L3: Google sign-in
# ---------------------------------------------------------------------------


@override_settings(CACHES=LOCMEM)
class L3GoogleTests(Base):
    def google(self, email):
        with patch("requests.post") as mocked_post, patch(
            "users.views.id_token.verify_oauth2_token"
        ) as mocked_verify:
            mocked_post.return_value.raise_for_status.return_value = None
            mocked_post.return_value.json.return_value = {
                "id_token": "fake-id-token",
                "access_token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "expires_in": 3600,
            }
            mocked_verify.return_value = {
                "email": email,
                "email_verified": True,
                "given_name": "Google",
                "family_name": "Person",
            }
            return self.client_class().post(
                reverse("auth-google-auth"), {"code": "oauth-code"}, format="json"
            )

    def test_a_locked_verified_account_gets_the_lock_answer(self):
        target = self.locked(self.person("g.h34@x.example", school=self.school))

        response = self.google(target.email)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertNotIn("access", response.json())
        fields = self.fields(response)
        self.assertEqual(fields["reason_code"], CODE)
        self.assertEqual(fields["detail"], MESSAGE_SCHOOL)
        event = AuditEvent.objects.get(action="AUTH_LOGIN", target_id=str(target.pk))
        self.assertEqual(event.outcome, "DENIED")
        self.assertEqual(event.reason_code, CODE)

    def test_a_locked_never_verified_account_is_not_switched_on(self):
        target = self.locked(
            self.person("g2.h34@x.example", is_active=False, email_verified_at=None)
        )
        before = target.password

        response = self.google(target.email)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self.fields(response)["detail"], MESSAGE_NO_SCHOOL)
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertIsNone(target.email_verified_at)
        self.assertEqual(target.password, before)


# ---------------------------------------------------------------------------
# L4: the reset road
# ---------------------------------------------------------------------------


@override_settings(CACHES=LOCMEM)
class L4ResetTests(Base):
    ip = 0

    def ask(self, email, otp_type):
        L4ResetTests.ip += 1
        return self.client_class().post(
            reverse("auth-otp"),
            {"email": email, "otp_type": otp_type},
            format="json",
            REMOTE_ADDR=f"10.34.0.{L4ResetTests.ip % 250 + 1}",
        )

    def reset(self, email, code):
        return self.client_class().post(
            reverse("auth-reset-password"),
            {"email": email, "otp": code, "new_password": NEW_PW},
            format="json",
        )

    def test_the_reset_code_request_is_neutral_and_makes_no_code(self):
        target = self.locked(self.person("r.h34@x.example"))

        unknown = self.ask("nobody.r.h34@example.com", "RESET_PASSWORD")
        answer = self.ask(target.email, "RESET_PASSWORD")

        self.assertEqual(answer.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(answer.content, unknown.content)
        self.assertFalse(PasswordResetOTP.objects.filter(user=target).exists())
        self.mock_safe_delay.assert_not_called()

    def test_the_verify_code_request_is_neutral_and_mails_nothing(self):
        target = self.locked(
            self.person("rv.h34@x.example", is_active=False, email_verified_at=None)
        )

        answer = self.ask(target.email, "VERIFY_EMAIL")

        self.assertEqual(answer.status_code, status.HTTP_202_ACCEPTED)
        target.refresh_from_db()
        self.assertIsNone(target.activation_token)
        self.mock_send_email_task.delay.assert_not_called()

    def test_a_valid_code_from_before_the_lock_sets_no_password(self):
        target = self.person("r2.h34@x.example")
        otp, _ = PasswordResetOTP.objects.get_or_create(user=target)
        code = otp.generate_code()
        self.locked(target)
        before = target.password

        response = self.reset(target.email, code)
        unknown = self.reset("nobody.r2.h34@example.com", code)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.content, unknown.content)
        self.assertNotIn("access", response.json())
        target.refresh_from_db()
        self.assertEqual(target.password, before)
        self.assertIsNotNone(target.admin_locked_at)


# ---------------------------------------------------------------------------
# L5: verify and invited-admin activation
# ---------------------------------------------------------------------------


@override_settings(CACHES=LOCMEM)
class L5ActivationTests(Base):
    def test_verify_refuses_a_locked_pending_account_as_a_wrong_code(self):
        target = self.locked(
            self.person(
                "v.h34@x.example",
                is_active=False,
                email_verified_at=None,
                activation_token="343434",
                activation_expires=timezone.now() + timedelta(minutes=15),
            )
        )

        refused = self.client_class().post(
            reverse("auth-verify"),
            {"email": target.email, "token": "343434"},
            format="json",
        )
        wrong = self.client_class().post(
            reverse("auth-verify"),
            {"email": target.email, "token": "000000"},
            format="json",
        )

        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(refused.content, wrong.content)
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertIsNone(target.email_verified_at)

    def test_invited_admin_activation_refuses_a_locked_invitation(self):
        target = self.locked(
            self.person(
                "inv.h34@x.example",
                user_type=UserTypes.SCHOOL_ADMIN,
                school=self.school,
                is_active=False,
                email_verified_at=None,
                activation_token="invite-h34-token",
                activation_expires=timezone.now() + timedelta(days=1),
            )
        )
        before = target.password

        response = self.client_class().post(
            reverse("auth-register-school-admin"),
            {"email": target.email, "token": "invite-h34-token", "password": NEW_PW},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("access", response.json())
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertEqual(target.password, before)


# ---------------------------------------------------------------------------
# L6: the backstop in the per-request auth class
# ---------------------------------------------------------------------------


@override_settings(CACHES=LOCMEM)
class L6BackstopTests(Base):
    def test_a_token_minted_after_the_lock_is_refused(self):
        target = self.locked(self.person("b.h34@x.example"))
        access = str(EpochRefreshToken.for_user(target).access_token)

        client = self.client_class()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

        self.assertEqual(
            client.get(reverse("user-me")).status_code, status.HTTP_401_UNAUTHORIZED
        )

    def test_control_the_same_token_works_for_an_unlocked_account(self):
        target = self.person("b2.h34@x.example")
        access = str(EpochRefreshToken.for_user(target).access_token)

        client = self.client_class()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

        self.assertEqual(client.get(reverse("user-me")).status_code, 200)


# ---------------------------------------------------------------------------
# L7 / L8: unlock
# ---------------------------------------------------------------------------


@override_settings(CACHES=LOCMEM)
class L7UnlockTests(Base):
    def test_unlock_clears_both_locks_and_the_count(self):
        target = self.locked(self.person("u.h34@x.example"))
        User.objects.filter(pk=target.pk).update(
            failed_login_attempts=7,
            locked_until=timezone.now() + timedelta(minutes=15),
        )

        response = self.unlock(target)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        target.refresh_from_db()
        self.assertIsNone(target.admin_locked_at)
        self.assertEqual(target.failed_login_attempts, 0)
        self.assertIsNone(target.locked_until)

    def test_sign_in_works_at_once_after_unlock(self):
        target = self.locked(self.person("u2.h34@x.example"))

        self.unlock(target)

        self.assertEqual(self.login(target.email).status_code, status.HTTP_200_OK)

    def test_unlock_never_switches_on_a_pending_account(self):
        target = self.locked(
            self.person("u3.h34@x.example", is_active=False, email_verified_at=None)
        )

        self.unlock(target)

        target.refresh_from_db()
        self.assertFalse(target.is_active)
        self.assertIsNone(target.admin_locked_at)

    def test_unlock_does_not_revive_sessions_from_before_the_lock(self):
        target = self.person("u4.h34@x.example")
        access = str(EpochRefreshToken.for_user(target).access_token)
        self.lock(target)

        self.unlock(target)

        client = self.client_class()
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(
            client.get(reverse("user-me")).status_code, status.HTTP_401_UNAUTHORIZED
        )


@override_settings(CACHES=LOCMEM)
class L8UnlockFiveTryLockTests(Base):
    def test_unlock_of_a_five_try_lock_alone_clears_it_and_says_so(self):
        target = self.person("u5.h34@x.example", school=self.school)
        User.objects.filter(pk=target.pk).update(
            failed_login_attempts=5,
            locked_until=timezone.now() + timedelta(minutes=15),
        )

        self.unlock(target)

        target.refresh_from_db()
        self.assertEqual(target.failed_login_attempts, 0)
        self.assertIsNone(target.locked_until)
        event = AuditEvent.objects.get(action="ACCOUNT_UNLOCK")
        self.assertEqual(
            event.metadata,
            {
                "had_admin_lock": False,
                "had_login_lock": True,
                "cleared_failed_attempts": 5,
            },
        )
        self.assertEqual(event.target_id, str(target.pk))
        self.assertEqual(event.school_id, str(self.school.pk))


# ---------------------------------------------------------------------------
# L9: who may lock whom
# ---------------------------------------------------------------------------


@override_settings(CACHES=LOCMEM)
class L9RulesTests(Base):
    def assert_refused_unchanged(self, response, target, code=403):
        self.assertEqual(response.status_code, code)
        target.refresh_from_db()
        self.assertIsNone(target.admin_locked_at)

    def test_a_super_admin_cannot_lock_themselves(self):
        response = self.lock(self.admin)

        self.assert_refused_unchanged(response, self.admin)
        self.assertEqual(response.json()["message"], GENERIC_DENIED)

    def test_a_super_admin_cannot_lock_another_super_admin(self):
        other = self.super_admin("other.operator.h34@x.example")

        response = self.lock(other)

        self.assert_refused_unchanged(response, other)
        self.assertEqual(response.json()["message"], GENERIC_DENIED)

    def test_a_superuser_flag_alone_makes_a_target_unlockable(self):
        other = self.person("flag.h34@x.example", is_superuser=True)

        self.assert_refused_unchanged(self.lock(other), other)

    def test_a_super_admin_type_alone_makes_a_target_unlockable(self):
        other = self.person("type.h34@x.example", user_type=UserTypes.SUPER_ADMIN)

        self.assert_refused_unchanged(self.lock(other), other)

    def test_no_other_role_may_lock_or_unlock(self):
        target = self.person("t.h34@x.example", school=self.school)
        for caller in (
            self.person(
                "c.sa.h34@x.example",
                user_type=UserTypes.SCHOOL_ADMIN,
                school=self.school,
            ),
            self.person("c.t.h34@x.example", school=self.school),
            self.person("c.s.h34@x.example", user_type=UserTypes.STUDENT),
        ):
            with self.subTest(caller=caller.user_type):
                self.assert_refused_unchanged(self.lock(target, by=caller), target)
                self.assertEqual(
                    self.unlock(target, by=caller).status_code,
                    status.HTTP_403_FORBIDDEN,
                )

    def test_an_unknown_id_is_not_found(self):
        response = self.as_user(self.admin).post(
            reverse("user-lock", kwargs={"pk": "00000000-0000-0000-0000-000000000034"}),
            {},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_anonymous_is_refused(self):
        target = self.person("anon.h34@x.example")
        response = self.client_class().post(
            reverse("user-lock", kwargs={"pk": target.pk}), {}, format="json"
        )
        self.assert_refused_unchanged(response, target, code=401)


# ---------------------------------------------------------------------------
# L10 + R3: exactly one event naming the requester
# ---------------------------------------------------------------------------


@override_settings(CACHES=LOCMEM)
class L10AuditTests(Base):
    def test_a_lock_leaves_one_account_lock_event_and_no_admin_action(self):
        target = self.person("a.h34@x.example", school=self.school)

        self.lock(target)

        events = self.events_naming(self.admin)
        self.assertEqual([e.action for e in events], ["ACCOUNT_LOCK"])
        event = events[0]
        self.assertEqual(event.target_type, "CustomUser")
        self.assertEqual(event.target_id, str(target.pk))
        self.assertEqual(event.school_id, str(self.school.pk))
        self.assertEqual(event.outcome, "SUCCESS")

    def test_an_unlock_leaves_one_account_unlock_event_and_no_admin_action(self):
        target = self.locked(self.person("a2.h34@x.example"))

        self.unlock(target)

        events = self.events_naming(self.admin)
        self.assertEqual([e.action for e in events], ["ACCOUNT_UNLOCK"])
        self.assertEqual(
            events[0].metadata,
            {
                "had_admin_lock": True,
                "had_login_lock": False,
                "cleared_failed_attempts": 0,
            },
        )

    def test_locking_a_locked_account_changes_nothing_and_leaves_admin_action(self):
        target = self.locked(self.person("a3.h34@x.example"))
        stamp, epoch = target.admin_locked_at, self.epoch(target)

        response = self.lock(target)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        target.refresh_from_db()
        self.assertEqual(target.admin_locked_at, stamp)
        self.assertEqual(self.epoch(target), epoch)
        self.assertEqual(
            [e.action for e in self.events_naming(self.admin)], ["ADMIN_ACTION"]
        )

    def test_unlocking_an_unlocked_account_leaves_admin_action(self):
        target = self.person("a4.h34@x.example")

        self.unlock(target)

        self.assertEqual(
            [e.action for e in self.events_naming(self.admin)], ["ADMIN_ACTION"]
        )


class R3HelperCallersGuardTests(SimpleTestCase):
    """The helper that writes the named event and drops the ADMIN_ACTION tag
    is called only by the two lock/unlock views (a static scan of production
    code, like audit.tests_history_guard)."""

    HELPER = "emit_named_admin_event"
    ROOT = pathlib.Path(__file__).resolve().parents[1]
    SKIP = {"migrations", "docs", "testing", ".git", "node_modules", "venv", ".venv"}

    def callers(self):
        found = set()
        for path in self.ROOT.rglob("*.py"):
            rel = path.relative_to(self.ROOT)
            if set(rel.parts) & self.SKIP or rel.name.startswith("tests"):
                continue
            if "tests" in rel.parts[:-1]:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(func):
                    if isinstance(node, ast.Call):
                        callee = node.func
                        name = getattr(callee, "attr", None) or getattr(
                            callee, "id", None
                        )
                        if name == self.HELPER:
                            found.add((rel.as_posix(), func.name))
        return found

    def test_the_only_callers_are_the_lock_and_unlock_views(self):
        self.assertEqual(
            self.callers(), {("users/views.py", "lock"), ("users/views.py", "unlock")}
        )

    def test_the_helper_exists_where_the_admin_action_hook_lives(self):
        from audit import admin_action

        self.assertTrue(callable(getattr(admin_action, self.HELPER, None)))


# ---------------------------------------------------------------------------
# L11: the back-office actions
# ---------------------------------------------------------------------------


@override_settings(CACHES=LOCMEM)
class L11BackOfficeTests(Base):
    def setUp(self):
        super().setUp()
        self.changelist = reverse("admin:users_customuser_changelist")

    def run_action(self, action, *users):
        self.client.force_login(self.admin)
        response = self.client.post(
            self.changelist,
            {"action": action, "_selected_action": [str(u.pk) for u in users]},
        )
        self.client.logout()
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)

    def test_the_labels_are_the_approved_texts(self):
        from django.contrib.admin.sites import site

        actions = site._registry[User].get_actions(self._request())
        labels = {name: entry[2] for name, entry in actions.items() if entry}
        self.assertEqual(labels.get("lock_sign_in"), "Lock sign-in")
        self.assertEqual(labels.get("unlock_sign_in"), "Unlock sign-in")

    def _request(self):
        from django.test import RequestFactory

        request = RequestFactory().get(self.changelist)
        request.user = self.admin
        return request

    def test_lock_changes_only_lockable_unlocked_rows_one_event_each(self):
        fresh = self.person("bo1.h34@x.example", school=self.school)
        already = self.locked(self.person("bo2.h34@x.example"))
        other_admin = self.super_admin("bo3.h34@x.example")
        stamp, epoch = already.admin_locked_at, self.epoch(already)
        fresh_epoch = self.epoch(fresh)

        with patch("users.sign_in_lock.invalidate_user_caches") as invalidated:
            self.run_action("lock_sign_in", fresh, already, other_admin, self.admin)

        fresh.refresh_from_db()
        already.refresh_from_db()
        other_admin.refresh_from_db()
        self.admin.refresh_from_db()
        self.assertIsNotNone(fresh.admin_locked_at)
        self.assertEqual(self.epoch(fresh), fresh_epoch + 1)
        self.assertEqual(already.admin_locked_at, stamp)
        self.assertEqual(self.epoch(already), epoch)
        self.assertIsNone(other_admin.admin_locked_at)
        self.assertIsNone(self.admin.admin_locked_at)
        events = AuditEvent.objects.filter(action="ACCOUNT_LOCK")
        self.assertEqual(
            [(e.target_id, e.actor_id) for e in events],
            [(str(fresh.pk), str(self.admin.pk))],
        )
        invalidated.assert_called_once()
        self.assertEqual([u.pk for u in invalidated.call_args.args[0]], [fresh.pk])

    def test_unlock_counts_only_rows_with_something_to_clear(self):
        locked = self.locked(self.person("bo4.h34@x.example"))
        five = self.person("bo5.h34@x.example")
        User.objects.filter(pk=five.pk).update(failed_login_attempts=2)
        clean = self.person("bo6.h34@x.example")

        self.run_action("unlock_sign_in", locked, five, clean)

        targets = set(
            AuditEvent.objects.filter(action="ACCOUNT_UNLOCK").values_list(
                "target_id", flat=True
            )
        )
        self.assertEqual(targets, {str(locked.pk), str(five.pk)})
        locked.refresh_from_db()
        five.refresh_from_db()
        self.assertIsNone(locked.admin_locked_at)
        self.assertEqual(five.failed_login_attempts, 0)

    def test_the_action_request_leaves_no_generic_event_beside_the_named_ones(self):
        target = self.person("bo7.h34@x.example")

        self.run_action("lock_sign_in", target)

        self.assertEqual(
            [e.action for e in self.events_naming(self.admin)], ["ACCOUNT_LOCK"]
        )


# ---------------------------------------------------------------------------
# Read-only fields for super admins (SM Q3)
# ---------------------------------------------------------------------------


@override_settings(CACHES=LOCMEM)
class ReadOnlyFieldsTests(Base):
    def test_a_super_admin_sees_both_fields(self):
        until = timezone.now() + timedelta(minutes=15)
        target = self.locked(self.person("f.h34@x.example"))
        User.objects.filter(pk=target.pk).update(locked_until=until)

        data = (
            self.as_user(self.admin)
            .get(reverse("user-detail", kwargs={"pk": target.pk}))
            .json()
        )

        self.assertIs(data["sign_in_locked"], True)
        self.assertIsNotNone(data["login_locked_until"])

    def test_nobody_else_sees_them(self):
        target = self.person("f2.h34@x.example")

        data = self.as_user(target).get(reverse("user-me")).json()

        self.assertNotIn("sign_in_locked", data)
        self.assertNotIn("login_locked_until", data)

    def test_the_sign_in_payload_does_not_carry_them(self):
        target = self.person("f3.h34@x.example")

        user = self.login(target.email).json()["user"]

        self.assertNotIn("sign_in_locked", user)
        self.assertNotIn("login_locked_until", user)

    def test_the_fields_cannot_be_written(self):
        target = self.person("f4.h34@x.example")

        self.as_user(self.admin).patch(
            reverse("user-detail", kwargs={"pk": target.pk}),
            {"sign_in_locked": True, "login_locked_until": timezone.now().isoformat()},
            format="json",
        )

        target.refresh_from_db()
        self.assertIsNone(target.admin_locked_at)
        self.assertIsNone(target.locked_until)


# ---------------------------------------------------------------------------
# L12 / L13: pins (what the lock must not change)
# ---------------------------------------------------------------------------


@override_settings(CACHES=LOCMEM)
class L12DeactivatedAnswerPinTests(Base):
    def test_a_deactivated_account_still_gets_invalid_credentials(self):
        """Green before and after: the lock adds a state, it does not change
        what a switched-off account is told."""
        target = self.person("d.h34@x.example")
        User.objects.filter(pk=target.pk).update(is_active=False)

        response = self.login(target.email)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self.fields(response)["reason_code"], "INVALID_CREDENTIALS")


@override_settings(CACHES=LOCMEM)
class L13LockLeavesTheRestTests(Base):
    def test_the_lock_leaves_enrollments_and_is_active_as_they_were(self):
        teacher = self.person("l13t.h34@x.example", school=self.school)
        student = self.person("l13s.h34@x.example", user_type=UserTypes.STUDENT)
        course = Course.objects.create(name="H34 L13", teacher=teacher)
        enrollment = StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

        self.lock(student)

        enrollment.refresh_from_db()
        student.refresh_from_db()
        self.assertEqual(enrollment.enrollment_status, EnrollmentStatusType.ENROLLED)
        self.assertTrue(student.is_active)
        self.assertIsNotNone(student.admin_locked_at)
