"""H-210: a per-address budget on POST /auth/register/school-admin.

The invitation token a pending school admin brings is the same
`activation_token` that POST /auth/verify accepts, and verify already has
H-53's per-address budget (VERIFY_EMAIL_MAX_FAILURES attempts, then a
VERIFY_EMAIL_LOCK_SECONDS lock). The school-admin door had only the per-IP
RegisterThrottle. Now it spends from the SAME budget (Senior Manager's
ruling): an attempt is spent before the token is checked, the guess that
spends the budget locks the address, and while the address is locked every
attempt is refused with verify's 429 VERIFY_LOCKED, whether or not the
address has an account. A success refunds the attempts.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from audit.enums import AuditAction
from audit.models import AuditEvent
from classrooms.models import School
from users.models import UserTypes
from users.throttling import _verify_address_key, lock_verify_address

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
TOKEN = "invite-h210-token"
STRONG = "A-strong-chosen-pw-210"  # pragma: allowlist secret


@override_settings(CACHES=LOCMEM, VERIFY_EMAIL_MAX_FAILURES=5)
class SchoolAdminDoorBudgetTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        mailerlite = patch("users.views.sync_user_to_mailerlite")
        mailerlite.start()
        self.addCleanup(mailerlite.stop)
        self.ip = 0
        self.school = School.objects.create(name="H-210 School")

    def pending(self, email="pending.h210@example.com"):
        return User.objects.create_user(
            email=email,
            password="Pending-pass-h210",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=False,
            activation_token=TOKEN,
            activation_expires=timezone.now() + timedelta(days=7),
        )

    def next_ip(self):
        """A fresh address per request, so the per-IP limits never answer."""
        self.ip += 1
        return f"10.210.{self.ip // 250}.{self.ip % 250 + 1}"

    def redeem(self, email, token):
        return self.client.post(
            reverse("auth-register-school-admin"),
            {"email": email, "token": token, "password": STRONG},
            format="json",
            REMOTE_ADDR=self.next_ip(),
        )

    def wrong(self, email, times):
        for n in range(times):
            self.redeem(email, f"not-the-token-{n}")

    def last_event(self, before):
        """The one sign-in event the request left."""
        new = AuditEvent.objects.exclude(pk__in=before).filter(
            action=AuditAction.AUTH_LOGIN
        )
        self.assertEqual(new.count(), 1)
        return new.get()

    def test_after_five_wrong_tokens_even_the_right_one_is_refused(self):
        """B1. Also the 429-not-500 test: the refusal is a Throttled raised
        inside the view's try, which its except clause must let through."""
        user = self.pending()
        self.wrong(user.email, 5)

        response = self.redeem(user.email, TOKEN)

        self.assertEqual(response.status_code, 429)
        self.assertEqual(
            response.json()["error"]["field_errors"]["reason_code"], "VERIFY_LOCKED"
        )
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_an_address_with_no_account_is_locked_the_same_way(self):
        """B2."""
        self.wrong("nobody.h210@example.com", 5)

        response = self.redeem("nobody.h210@example.com", "any-token")

        self.assertEqual(response.status_code, 429)

    def test_the_budget_is_shared_with_email_verification(self):
        """B3: five wrong codes on /auth/verify lock the school-admin door."""
        user = self.pending()
        for n in range(5):
            self.client.post(
                reverse("auth-verify"),
                {"email": user.email, "token": f"99999{n}"},
                format="json",
                REMOTE_ADDR=self.next_ip(),
            )

        response = self.redeem(user.email, TOKEN)

        self.assertEqual(response.status_code, 429)
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_the_guess_that_spends_the_budget_is_flagged(self):
        """B4."""
        user = self.pending()
        self.wrong(user.email, 4)
        before = set(AuditEvent.objects.values_list("pk", flat=True))

        self.redeem(user.email, "the-fifth-wrong-token")

        event = self.last_event(before)
        self.assertEqual(event.metadata.get("lock_triggered"), True)
        self.assertEqual(event.reason_code, "INVALID_CODE")

    def test_a_locked_attempt_is_recorded_as_refused(self):
        """B5."""
        user = self.pending()
        self.wrong(user.email, 5)
        before = set(AuditEvent.objects.values_list("pk", flat=True))

        self.redeem(user.email, "a-sixth-wrong-token")

        event = self.last_event(before)
        self.assertEqual(event.outcome, "DENIED")
        self.assertEqual(event.reason_code, "VERIFY_LOCKED")
        self.assertEqual(event.target_id, user.id)
        self.assertEqual(event.metadata.get("auth_method"), "school_admin_invitation")

    def test_a_locked_address_is_sent_no_new_invitation(self):
        """B6: the lock is the shared one, which /auth/otp reads before it
        re-sends an invitation."""
        user = self.pending()
        self.wrong(user.email, 5)

        self.client.post(
            reverse("auth-otp"),
            {"email": user.email, "otp_type": "VERIFY_EMAIL"},
            format="json",
            REMOTE_ADDR=self.next_ip(),
        )

        user.refresh_from_db()
        self.assertEqual(user.activation_token, TOKEN)

    def test_the_locked_answer_does_not_tell_whether_the_address_has_an_account(
        self,
    ):
        """B7."""
        user = self.pending()
        self.wrong(user.email, 5)
        self.wrong("nobody.h210@example.com", 5)

        known = self.redeem(user.email, TOKEN)
        unknown = self.redeem("nobody.h210@example.com", TOKEN)

        self.assertEqual(known.status_code, 429)
        self.assertEqual(unknown.status_code, known.status_code)
        self.assertEqual(unknown.json()["message"], known.json()["message"])
        self.assertEqual(
            unknown.json()["error"]["field_errors"]["reason_code"],
            known.json()["error"]["field_errors"]["reason_code"],
        )

    def test_a_lock_alone_refuses_after_the_attempt_window(self):
        """B8: the attempt count has expired but the lock stands (it lasts
        from the locking guess, the count from the first attempt)."""
        user = self.pending()
        lock_verify_address(user.email)

        response = self.redeem(user.email, TOKEN)

        self.assertEqual(response.status_code, 429)
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_under_the_budget_the_right_token_still_signs_in(self):
        """K1 (control)."""
        user = self.pending()
        self.wrong(user.email, 4)

        response = self.redeem(user.email, TOKEN)

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        user.refresh_from_db()
        self.assertTrue(user.is_active)

    def test_a_success_refunds_the_attempts(self):
        """K2 (control)."""
        user = self.pending()
        self.wrong(user.email, 3)

        self.redeem(user.email, TOKEN)

        self.assertIsNone(cache.get(_verify_address_key("attempts", user.email)))

    def test_under_the_budget_the_answers_are_unchanged(self):
        """K3 (control): a wrong token for a known and an unknown address."""
        user = self.pending()

        known = self.redeem(user.email, "not-the-token")
        unknown = self.redeem(f"{uuid.uuid4().hex}@example.com", "not-the-token")

        self.assertEqual(known.status_code, 400)
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(known.content, unknown.content)
