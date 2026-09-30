"""S1 (plan 08 §2.2): every door that signs someone in records AUTH_LOGIN -
one event per success and per failed attempt, with `metadata.auth_method`
naming the door.

`/auth/login` is covered by tests_auth_audit_events.py. These are the other
token-issuing (or password-setting) routes, exercised through the real URLs.
"""

from datetime import timedelta
from unittest.mock import patch

import requests
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from audit.enums import ActorRole, AuditAction, AuditOutcome, ErrorClass
from audit.models import AuditEvent
from classrooms.models import School
from users.models import PasswordResetOTP, UserTypes

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD = "a-strong-password-42"  # pragma: allowlist secret
NEW_PASSWORD = "a-brand-new-password-77"  # pragma: allowlist secret


def make_user(email, **overrides):
    defaults = {
        "email": email,
        "password": PASSWORD,
        "first_name": "Door",
        "last_name": "Audit",
        "user_type": UserTypes.TEACHER,
        "is_active": True,
        "email_verified_at": timezone.now(),
    }
    defaults.update(overrides)
    return User.objects.create_user(**defaults)


@override_settings(CACHES=LOCMEM_CACHE)
class DoorBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        mailerlite = patch("users.views.sync_user_to_mailerlite")
        mailerlite.start()
        self.addCleanup(mailerlite.stop)

    def only_event(self):
        """The one event this request recorded (failure paths record nothing
        else)."""
        self.assertEqual(AuditEvent.objects.count(), 1)
        return AuditEvent.objects.get()

    def the_login_event(self):
        """Success paths may also record side events (e.g. a first credit
        grant); there must be exactly one AUTH_LOGIN and no generic event."""
        events = AuditEvent.objects.filter(action=AuditAction.AUTH_LOGIN)
        self.assertEqual(events.count(), 1)
        self.assertFalse(
            AuditEvent.objects.filter(action=AuditAction.STATE_CHANGE).exists()
        )
        return events.get()

    def assert_event(
        self, event, *, outcome, method, account, reason=None, signed_in=None
    ):
        """Success: the user is the actor. Failure (SM ruling): the targeted
        account is only the TARGET; the actor is the signed-in requester if
        any (`signed_in`), else ANONYMOUS - never the account holder merely
        because their account was targeted."""
        self.assertEqual(event.action, AuditAction.AUTH_LOGIN)
        self.assertEqual(event.outcome, outcome)
        self.assertEqual(event.metadata.get("auth_method"), method)
        self.assertEqual(event.reason_code, reason)
        self.assertEqual(event.target_id, account.id if account else None)
        if outcome == AuditOutcome.SUCCESS:
            self.assertEqual(event.actor_id, account.id)
        elif signed_in is not None:
            self.assertEqual(event.actor_id, signed_in.id)
        else:
            self.assertEqual(event.actor_role, ActorRole.ANONYMOUS)
            self.assertIsNone(event.actor_id)
            self.assertIsNone(event.actor_email)


class VerifyEmailDoorTests(DoorBase):
    def pending(self, **overrides):
        return make_user(
            "verify.door@example.com",
            is_active=False,
            email_verified_at=None,
            activation_token="123456",
            activation_expires=timezone.now() + timedelta(minutes=15),
            **overrides,
        )

    def verify(self, email, token):
        return self.client.post(
            reverse("auth-verify"), {"email": email, "token": token}, format="json"
        )

    def test_success(self):
        user = self.pending()
        self.assertEqual(self.verify(user.email, "123456").status_code, 202)
        self.assert_event(
            self.the_login_event(),
            outcome=AuditOutcome.SUCCESS,
            method="email_verification",
            account=user,
        )

    def test_wrong_code_names_the_account(self):
        user = self.pending()
        self.assertEqual(self.verify(user.email, "000000").status_code, 400)
        self.assert_event(
            self.only_event(),
            outcome=AuditOutcome.FAILURE,
            method="email_verification",
            account=user,
            reason="INVALID_CODE",
        )

    def test_a_failure_is_scoped_to_the_targets_school(self):
        school = School.objects.create(name="Verify Scope School")
        user = self.pending(school=school)
        self.verify(user.email, "000000")
        self.assertEqual(self.only_event().school_id, school.id)

    def test_unknown_email_stores_no_account_and_no_email(self):
        self.assertEqual(self.verify("nobody@example.com", "000000").status_code, 400)
        event = self.only_event()
        self.assert_event(
            event,
            outcome=AuditOutcome.FAILURE,
            method="email_verification",
            account=None,
            reason="INVALID_CODE",
        )
        self.assertNotIn(
            "nobody@example.com",
            " ".join(str(v) for v in AuditEvent.objects.values().get().values()),
        )

    def test_expired_code(self):
        user = self.pending()
        User.objects.filter(pk=user.pk).update(
            activation_expires=timezone.now() - timedelta(minutes=1)
        )
        self.assertEqual(self.verify(user.email, "123456").status_code, 400)
        self.assert_event(
            self.only_event(),
            outcome=AuditOutcome.FAILURE,
            method="email_verification",
            account=user,
            reason="CODE_EXPIRED",
        )

    def test_the_user_still_signs_in_when_the_audit_store_is_down(self):
        """FR-A-11."""
        user = self.pending()
        with patch.object(
            AuditEvent.objects, "create", side_effect=RuntimeError("store down")
        ):
            self.assertEqual(self.verify(user.email, "123456").status_code, 202)


class ResetPasswordDoorTests(DoorBase):
    def setUp(self):
        super().setUp()
        self.user = make_user("reset.door@example.com")
        self.otp = PasswordResetOTP.objects.create(user=self.user)
        self.code = self.otp.generate_code()

    def reset(self, email, code):
        return self.client.post(
            reverse("auth-reset-password"),
            {"email": email, "otp": code, "new_password": NEW_PASSWORD},
            format="json",
        )

    def test_success(self):
        self.assertEqual(self.reset(self.user.email, self.code).status_code, 200)
        self.assert_event(
            self.the_login_event(),
            outcome=AuditOutcome.SUCCESS,
            method="password_reset",
            account=self.user,
        )

    def test_wrong_code(self):
        wrong = "000000" if self.code != "000000" else "111111"
        self.assertEqual(self.reset(self.user.email, wrong).status_code, 400)
        self.assert_event(
            self.only_event(),
            outcome=AuditOutcome.FAILURE,
            method="password_reset",
            account=self.user,
            reason="INVALID_CODE",
        )

    def test_locked_is_denied(self):
        """batch-2a (L2) behaviour, merged into Epic A: a locked reset
        answers 429 RESET_LOCKED (it used to be S1's 400), and the attempt is
        recorded as DENIED."""
        PasswordResetOTP.objects.filter(pk=self.otp.pk).update(
            locked_until=timezone.now() + timedelta(minutes=30)
        )
        self.assertEqual(self.reset(self.user.email, self.code).status_code, 429)
        self.assert_event(
            self.only_event(),
            outcome=AuditOutcome.DENIED,
            method="password_reset",
            account=self.user,
            reason="RESET_LOCKED",
        )

    def test_the_guess_that_spends_the_budget_is_a_failure_that_set_the_lock(self):
        """SM ruling for the 2a merge: that guess answers 429 at once (L2),
        and is recorded as what it was - a wrong code - flagged
        lock_triggered. One event per attempt."""
        wrong = "000000" if self.code != "000000" else "111111"
        statuses = [
            self.reset(self.user.email, wrong).status_code
            for _ in range(PasswordResetOTP.MAX_ATTEMPTS)
        ]

        self.assertEqual(statuses, [400] * (PasswordResetOTP.MAX_ATTEMPTS - 1) + [429])
        events = list(AuditEvent.objects.order_by("occurred_at", "pk"))
        self.assertEqual(len(events), PasswordResetOTP.MAX_ATTEMPTS)
        for event in events:
            self.assert_event(
                event,
                outcome=AuditOutcome.FAILURE,
                method="password_reset",
                account=self.user,
                reason="INVALID_CODE",
            )
        self.assertEqual(
            [event.metadata.get("lock_triggered") for event in events],
            [None] * (PasswordResetOTP.MAX_ATTEMPTS - 1) + [True],
        )

    def test_unknown_email(self):
        self.assertEqual(self.reset("ghost@example.com", "123456").status_code, 400)
        self.assert_event(
            self.only_event(),
            outcome=AuditOutcome.FAILURE,
            method="password_reset",
            account=None,
            reason="INVALID_CODE",
        )


class ChangePasswordDoorTests(DoorBase):
    def setUp(self):
        super().setUp()
        self.user = make_user("change.door@example.com")
        self.client.force_authenticate(user=self.user)

    def change(self, current):
        return self.client.post(
            reverse("auth-change-password"),
            {"current_password": current, "new_password": NEW_PASSWORD},
            format="json",
        )

    def test_success(self):
        self.assertEqual(self.change(PASSWORD).status_code, 200)
        self.assert_event(
            self.the_login_event(),
            outcome=AuditOutcome.SUCCESS,
            method="password_change",
            account=self.user,
        )

    def test_wrong_current_password_is_one_event_not_two(self):
        """A named failure, so the authenticated request gets no generic
        STATE_CHANGE on top."""
        self.assertEqual(self.change("not-my-password").status_code, 400)
        self.assert_event(
            self.only_event(),
            outcome=AuditOutcome.FAILURE,
            method="password_change",
            account=self.user,
            reason="WRONG_PASSWORD",
            signed_in=self.user,
        )


class InvitationDoorTests(DoorBase):
    def test_school_admin_invitation_success(self):
        school = School.objects.create(name="Door School")
        admin = make_user(
            "admin.door@example.com",
            password=None,
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
            is_active=False,
            email_verified_at=None,
            activation_token="inv-token-1",
            activation_expires=timezone.now() + timedelta(days=1),
        )
        response = self.client.post(
            reverse("auth-register-school-admin"),
            {"email": admin.email, "token": "inv-token-1", "password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assert_event(
            self.the_login_event(),
            outcome=AuditOutcome.SUCCESS,
            method="school_admin_invitation",
            account=admin,
        )

    def test_school_admin_bad_code_survives_the_rollback(self):
        """Refused inside the view's atomic block; the event is written after
        it, so it is not rolled back with it."""
        school = School.objects.create(name="Door School 2")
        admin = make_user(
            "admin.door2@example.com",
            password=None,
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
            is_active=False,
            email_verified_at=None,
            activation_token="inv-token-2",
            activation_expires=timezone.now() + timedelta(days=1),
        )
        response = self.client.post(
            reverse("auth-register-school-admin"),
            {"email": admin.email, "token": "wrong-token", "password": NEW_PASSWORD},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assert_event(
            self.only_event(),
            outcome=AuditOutcome.FAILURE,
            method="school_admin_invitation",
            account=admin,
            reason="INVALID_CODE",
        )

    def test_student_invitation_bad_code_survives_the_rollback(self):
        response = self.client.post(
            reverse("auth-register-student"),
            {
                "token": "no-such-token",
                "password": NEW_PASSWORD,
                "first_name": "Stu",
                "last_name": "Dent",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assert_event(
            self.only_event(),
            outcome=AuditOutcome.FAILURE,
            method="student_invitation",
            account=None,
            reason="INVALID_CODE",
        )


class GoogleDoorTests(DoorBase):
    def google(self, email="g.door@gmail.com", verified=True, post_error=None):
        with patch("requests.post") as mocked_post, patch(
            "users.views.id_token.verify_oauth2_token"
        ) as mocked_verify:
            if post_error is not None:
                mocked_post.side_effect = post_error
            else:
                mocked_post.return_value.raise_for_status.return_value = None
                mocked_post.return_value.json.return_value = {
                    "id_token": "fake-id-token",
                    "access_token": "fake-access-token",
                    "expires_in": 3600,
                }
            mocked_verify.return_value = {
                "email": email,
                "email_verified": verified,
                "given_name": "Goo",
                "family_name": "Gle",
            }
            return self.client.post(
                reverse("auth-google-auth"), {"code": "oauth-code"}, format="json"
            )

    def test_success_for_an_existing_account(self):
        user = make_user("g.door@gmail.com", password=None)
        self.assertEqual(self.google().status_code, 200)
        self.assert_event(
            self.the_login_event(),
            outcome=AuditOutcome.SUCCESS,
            method="google",
            account=user,
        )

    def test_a_deactivated_account_is_denied_and_named(self):
        """Refused inside the view's atomic block; still recorded."""
        user = make_user("g.door@gmail.com", password=None)
        User.objects.filter(pk=user.pk).update(is_active=False)
        self.assertEqual(self.google().status_code, 401)
        self.assert_event(
            self.only_event(),
            outcome=AuditOutcome.DENIED,
            method="google",
            account=user,
            reason="ACCOUNT_DEACTIVATED",
        )

    def test_unverified_google_email(self):
        self.assertEqual(self.google(verified=False).status_code, 400)
        self.assert_event(
            self.only_event(),
            outcome=AuditOutcome.FAILURE,
            method="google",
            account=None,
            reason="GOOGLE_EMAIL_UNVERIFIED",
        )

    def test_google_unreachable_is_a_provider_failure(self):
        response = self.google(post_error=requests.exceptions.ConnectionError("dns"))
        self.assertEqual(response.status_code, 400)
        event = self.only_event()
        self.assert_event(
            event,
            outcome=AuditOutcome.FAILURE,
            method="google",
            account=None,
            reason="GOOGLE_EXCHANGE_FAILED",
        )
        self.assertEqual(event.error_class, ErrorClass.PROVIDER)
