"""H-221: the two reset-password refusals that recorded the wrong event.

`POST /auth/reset-password` refuses two kinds of account before it looks at
the code:

- H-164: an account that never verified its email AND is switched off or has
  admin power;
- H-202: a verified account that is switched off.

Neither recorded its own event, so `AuditMiddleware` wrote its fallback: a
FAILURE with reason INVALID_REQUEST ("a malformed body") and no account. Now:

- H-164 records INVALID_CODE, FAILURE, naming the account, as verify and the
  school-admin registration do for the same rule;
- H-202 records ACCOUNT_DEACTIVATED, DENIED, naming the account, as Google
  sign-in does for the same state.

The answer is unchanged: the same 400 and body as an unknown address
(compared byte for byte). The code and the account are left as before. Each
request still leaves exactly one event.
"""

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from audit.enums import ActorRole
from audit.models import AuditEvent
from classrooms.models import School
from users.models import PasswordResetOTP
from users.tests_auth_audit_doors import NEW_PASSWORD, PASSWORD, DoorBase, make_user


class ResetRefusalsAreAuditedTest(DoorBase):
    def reset(self, email, code):
        return self.client.post(
            reverse("auth-reset-password"),
            {"email": email, "otp": code, "new_password": NEW_PASSWORD},
            format="json",
        )

    def with_code(self, user, *, locked=False):
        """A real reset code for `user`; `locked` sets the 30-minute lock
        after the code is made, as five wrong guesses would."""
        otp, _ = PasswordResetOTP.objects.get_or_create(user=user)
        code = otp.generate_code()
        if locked:
            PasswordResetOTP.objects.filter(pk=otp.pk).update(
                locked_until=timezone.now() + timedelta(minutes=30)
            )
        return otp, code

    def never_verified_switched_off(self):
        return make_user(
            "h221.pending@example.com", is_active=False, email_verified_at=None
        )

    def never_verified_admin_power(self):
        return make_user(
            "h221.staff@example.com", email_verified_at=None, is_staff=True
        )

    def switched_off(self, email="h221.off@example.com"):
        school = School.objects.create(name=f"School of {email}")
        return make_user(email, is_active=False, school=school)

    def the_one_new_event(self, email, code):
        """The response, and the one event the request left (checked by pk:
        users made in setUp leave their own history events)."""
        before = set(AuditEvent.objects.values_list("pk", flat=True))
        response = self.reset(email, code)
        new = AuditEvent.objects.exclude(pk__in=before)
        self.assertEqual(new.count(), 1)
        return response, new.get()

    def assert_refusal(self, event, *, outcome, reason, user):
        self.assertEqual(event.outcome, outcome)
        self.assertEqual(event.reason_code, reason)
        self.assertEqual(event.target_id, user.id)
        self.assertEqual(event.actor_role, ActorRole.ANONYMOUS)
        self.assertIsNone(event.actor_id)
        self.assertEqual(event.metadata.get("auth_method"), "password_reset")
        self.assertEqual(event.school_id, user.school_id)

    def test_a_never_verified_switched_off_account_is_recorded_as_a_wrong_code(self):
        user = self.never_verified_switched_off()
        _, code = self.with_code(user)

        response, event = self.the_one_new_event(user.email, code)

        self.assertEqual(response.status_code, 400)
        self.assert_refusal(event, outcome="FAILURE", reason="INVALID_CODE", user=user)

    def test_a_never_verified_account_with_admin_power_is_recorded_as_a_wrong_code(
        self,
    ):
        user = self.never_verified_admin_power()
        _, code = self.with_code(user)

        response, event = self.the_one_new_event(user.email, code)

        self.assertEqual(response.status_code, 400)
        self.assert_refusal(event, outcome="FAILURE", reason="INVALID_CODE", user=user)

    def test_a_switched_off_account_is_recorded_as_refused_for_deactivation(self):
        user = self.switched_off()
        _, code = self.with_code(user)

        response, event = self.the_one_new_event(user.email, code)

        self.assertEqual(response.status_code, 400)
        self.assert_refusal(
            event, outcome="DENIED", reason="ACCOUNT_DEACTIVATED", user=user
        )

    def test_a_switched_off_account_with_a_locked_reset_is_recorded_the_same_way(
        self,
    ):
        """H-202's refusal comes before the lock check: a 400, not RESET_LOCKED's
        429, and the same event as the unlocked case."""
        user = self.switched_off()
        _, code = self.with_code(user, locked=True)

        response, event = self.the_one_new_event(user.email, code)

        self.assertEqual(response.status_code, 400)
        self.assert_refusal(
            event, outcome="DENIED", reason="ACCOUNT_DEACTIVATED", user=user
        )

    def test_every_refusal_answers_byte_for_byte_like_an_unknown_address(self):
        unknown = self.reset("nobody.h221@example.com", "123456")
        cases = {
            "never verified, switched off": (self.never_verified_switched_off(), False),
            "never verified, admin power": (self.never_verified_admin_power(), False),
            "switched off": (self.switched_off(), False),
            "switched off, locked": (self.switched_off("h221.off2@example.com"), True),
        }
        self.assertEqual(unknown.status_code, 400)
        for name, (user, locked) in cases.items():
            with self.subTest(name):
                _, code = self.with_code(user, locked=locked)
                response = self.reset(user.email, code)
                self.assertEqual(response.status_code, unknown.status_code)
                self.assertEqual(response.content, unknown.content)

    def test_the_account_and_its_code_are_left_as_before(self):
        """H-164 spends no attempt; H-202 spends one unless the reset is
        already locked. No password changes. Unchanged by H-221."""
        cases = {
            "never verified, switched off": (
                self.never_verified_switched_off(),
                False,
                0,
            ),
            "never verified, admin power": (
                self.never_verified_admin_power(),
                False,
                0,
            ),
            "switched off": (self.switched_off(), False, 1),
            "switched off, locked": (
                self.switched_off("h221.off2@example.com"),
                True,
                0,
            ),
        }
        for name, (user, locked, spent) in cases.items():
            with self.subTest(name):
                otp, code = self.with_code(user, locked=locked)
                otp.refresh_from_db()
                attempts = otp.attempts

                self.reset(user.email, code)

                otp.refresh_from_db()
                user.refresh_from_db()
                self.assertEqual(otp.attempts, attempts + spent)
                self.assertTrue(user.check_password(PASSWORD))
