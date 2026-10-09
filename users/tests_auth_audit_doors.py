"""S1 (plan 08 §2.2): every door that signs someone in records AUTH_LOGIN -
one event per success and per failed attempt, with `metadata.auth_method`
naming the door.

`/auth/login` is covered by tests_auth_audit_events.py. These are the other
token-issuing (or password-setting) routes, exercised through the real URLs.
"""

from datetime import timedelta
from typing import Any
from unittest.mock import patch

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from audit.enums import ActorRole, AuditAction, AuditOutcome, ErrorClass, ReasonCode
from audit.models import AuditEvent
from AutoGrader.reason_codes import AUDIT_ONLY_CODES, REASON_CODES
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
        else). Updated on purpose for Epic A S4: a privileged account made in
        setUp leaves its own history create event, which is not the
        request's."""
        # `contains`, not `metadata__source=`: a key lookup is NULL on an
        # event without the key, and exclude() would drop those too.
        requested = AuditEvent.objects.exclude(metadata__contains={"source": "create"})
        self.assertEqual(requested.count(), 1)
        return requested.get()

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

    def verify(self, email, token, ip="127.0.0.1"):
        """`ip` varies per attempt in the H-53 tests, so the per-IP
        VerifyEmailThrottle (5/hour) never answers in place of the
        per-address budget."""
        return self.client.post(
            reverse("auth-verify"),
            {"email": email, "token": token},
            format="json",
            REMOTE_ADDR=ip,
        )

    def spend_the_budget(self, email):
        """H-53: VERIFY_EMAIL_MAX_FAILURES wrong codes, one event each; the
        last one also sets the lock."""
        limit = settings.VERIFY_EMAIL_MAX_FAILURES
        for attempt in range(1, limit + 1):
            with self.subTest(attempt=attempt):
                before = set(AuditEvent.objects.values_list("pk", flat=True))
                response = self.verify(email, "000000", ip=f"10.53.0.{attempt}")
                new = AuditEvent.objects.exclude(pk__in=before)

                self.assertEqual(response.status_code, 400)
                self.assertEqual(new.count(), 1)
                event = new.get()
                self.assertEqual(event.outcome, AuditOutcome.FAILURE)
                self.assertEqual(event.reason_code, "INVALID_CODE")
                self.assertEqual(
                    event.metadata.get("lock_triggered"),
                    True if attempt == limit else None,
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

    def test_the_beta_refusals_are_audited_like_a_wrong_code(self):
        """Merge-down b14: H-164 (a never-verified account with admin power)
        and H-202 (a verified, switched-off account) are refused on
        /auth/verify with the wrong-code answer. On the Phase 2 line that
        refusal also writes the sign-in failure event (reason INVALID_CODE,
        the account named) and spends the per-address budget exactly as a
        wrong code on an ordinary account does (the lock after the last
        allowed guess is asserted in the two lock tests of
        users.tests_reset_for_an_invited_student and
        users.tests_switched_off_means_out)."""
        expires = timezone.now() + timedelta(minutes=15)
        cases: dict[str, dict[str, Any]] = {
            "admin.power@example.com": {
                "is_active": True,
                "email_verified_at": None,
                "is_staff": True,
                "activation_token": "123456",
                "activation_expires": expires,
            },
            "switched.off@example.com": {
                "is_active": False,
                "activation_token": "123456",
                "activation_expires": expires,
            },
        }
        for email, overrides in cases.items():
            with self.subTest(email=email):
                user = make_user(email, **overrides)
                before = set(AuditEvent.objects.values_list("pk", flat=True))

                response = self.verify(user.email, "123456")

                new = AuditEvent.objects.exclude(pk__in=before)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(new.count(), 1)
                self.assert_event(
                    new.get(),
                    outcome=AuditOutcome.FAILURE,
                    method="email_verification",
                    account=user,
                    reason="INVALID_CODE",
                )
                user.refresh_from_db()
                self.assertIsNotNone(user.activation_token)

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

    def test_the_guess_that_spends_the_budget_is_a_failure_that_set_the_lock(self):
        """SM ruling for the beta (H-53) merge: one event per attempt. Every
        wrong code is FAILURE INVALID_CODE; the one that spends the budget
        is flagged lock_triggered (H-53 still answers it 400)."""
        user = self.pending()
        self.spend_the_budget(user.email)
        self.assertTrue(
            AuditEvent.objects.filter(
                target_id=user.id, metadata__lock_triggered=True
            ).exists()
        )

    def test_a_locked_attempt_is_denied_even_with_the_right_code(self):
        """H-53's 429, recorded as one DENIED VERIFY_LOCKED naming the
        account. The right code does not verify while locked."""
        user = self.pending()
        self.spend_the_budget(user.email)

        before = set(AuditEvent.objects.values_list("pk", flat=True))
        response = self.verify(user.email, "123456", ip="10.53.1.1")

        self.assertEqual(response.status_code, 429)
        new = AuditEvent.objects.exclude(pk__in=before)
        self.assertEqual(new.count(), 1)
        self.assert_event(
            new.get(),
            outcome=AuditOutcome.DENIED,
            method="email_verification",
            account=user,
            reason="VERIFY_LOCKED",
        )
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertIsNone(user.email_verified_at)

    def test_a_locked_unknown_address_is_denied_with_no_account_and_no_email(self):
        self.spend_the_budget("nobody@example.com")

        before = set(AuditEvent.objects.values_list("pk", flat=True))
        response = self.verify("nobody@example.com", "000000", ip="10.53.1.2")

        self.assertEqual(response.status_code, 429)
        new = AuditEvent.objects.exclude(pk__in=before)
        self.assertEqual(new.count(), 1)
        self.assert_event(
            new.get(),
            outcome=AuditOutcome.DENIED,
            method="email_verification",
            account=None,
            reason="VERIFY_LOCKED",
        )
        self.assertNotIn(
            "nobody@example.com",
            " ".join(str(v) for v in AuditEvent.objects.values().values()),
        )

    def test_every_code_verify_records_is_in_the_catalogue(self):
        """S6a's emitter refuses a code outside the catalogue, which would
        leave a locked attempt with no event at all."""
        # Updated on purpose (the auth-lock envelope slice): VERIFY_LOCKED is
        # now a user-facing code; either list is the catalogue.
        for code in ("CODE_MISSING", "INVALID_CODE", "CODE_EXPIRED", "VERIFY_LOCKED"):
            with self.subTest(code=code):
                self.assertIn(ReasonCode(code), AUDIT_ONLY_CODES | set(REASON_CODES))

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
        lock_triggered. One event per attempt, checked request by request
        (v2's N1: event pks are random, so ordering the rows can't recover
        which request wrote which)."""
        wrong = "000000" if self.code != "000000" else "111111"
        for attempt in range(1, PasswordResetOTP.MAX_ATTEMPTS + 1):
            with self.subTest(attempt=attempt):
                before = set(AuditEvent.objects.values_list("pk", flat=True))
                response = self.reset(self.user.email, wrong)
                new = AuditEvent.objects.exclude(pk__in=before)

                last = attempt == PasswordResetOTP.MAX_ATTEMPTS
                self.assertEqual(response.status_code, 429 if last else 400)
                self.assertEqual(new.count(), 1)
                event = new.get()
                self.assert_event(
                    event,
                    outcome=AuditOutcome.FAILURE,
                    method="password_reset",
                    account=self.user,
                    reason="INVALID_CODE",
                )
                self.assertEqual(
                    event.metadata.get("lock_triggered"), True if last else None
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

    def test_an_admin_power_pending_row_is_refused_and_audited_like_a_wrong_token(
        self,
    ):
        """Merge-down b16: H-203 refuses a pending school-admin row that
        carries admin power even with the right token. On the Phase 2 line
        that refusal writes ONE failure event naming the account, with the
        reason of a wrong token, and the row stays as it was."""
        school = School.objects.create(name="Door School 3")
        admin = make_user(
            "admin.door3@example.com",
            password=None,
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
            is_active=False,
            is_staff=True,
            email_verified_at=None,
            activation_token="inv-token-3",
            activation_expires=timezone.now() + timedelta(days=1),
        )
        before = set(AuditEvent.objects.values_list("pk", flat=True))

        response = self.client.post(
            reverse("auth-register-school-admin"),
            {"email": admin.email, "token": "inv-token-3", "password": NEW_PASSWORD},
            format="json",
        )

        new = AuditEvent.objects.exclude(pk__in=before)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(new.count(), 1)
        self.assert_event(
            new.get(),
            outcome=AuditOutcome.FAILURE,
            method="school_admin_invitation",
            account=admin,
            reason="INVALID_CODE",
        )
        admin.refresh_from_db()
        self.assertFalse(admin.is_active)
        self.assertIsNone(admin.email_verified_at)
        self.assertEqual(admin.activation_token, "inv-token-3")


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

    def test_a_never_verified_admin_power_account_is_refused_and_named(self):
        """Merge-down b16: H-203 refuses a Google sign-in on a never-verified
        account with admin power. On the Phase 2 line the refusal writes ONE
        failure event naming that account (the neighbouring refusals do), and
        nothing is written to the account."""
        user = make_user(
            "g.door@gmail.com",
            password=None,
            is_staff=True,
            is_active=False,
            email_verified_at=None,
        )
        before = set(AuditEvent.objects.values_list("pk", flat=True))

        response = self.google()

        new = AuditEvent.objects.exclude(pk__in=before)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(new.count(), 1)
        self.assert_event(
            new.get(),
            outcome=AuditOutcome.FAILURE,
            method="google",
            account=user,
            reason="GOOGLE_SIGN_IN_REFUSED",
        )
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertIsNone(user.email_verified_at)

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
