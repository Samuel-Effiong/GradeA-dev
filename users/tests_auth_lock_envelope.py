"""The sign-in locks answer with the coded envelope ADDED (v2's S6a N3, SM
ruling): every field and header their docs promise stays exactly as it was,
and each lock gains reason_code / error_class / remediation / retryable /
params / reference.

No new existence oracle (SM condition):
- /auth/verify locks an ADDRESS, known or not (H-53). A locked unknown
  address answers exactly like a locked known one: the same status, keys,
  codes and text (the reference is per request, and the seconds may tick).
- The login and reset-password locks exist only for real accounts. That is
  pre-existing (a distinct 401 message and `account_locked` code; the
  informative RESET_LOCKED answer, a founder decision of 2026-09-28). The
  added keys only restate the code those answers already carry. An unknown
  address's answers gain nothing.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from audit.enums import ReasonCode
from AutoGrader.reason_codes import (
    AUDIT_ONLY_CODES,
    ENVELOPE_KEYS,
    REASON_CODES,
    add_coded_envelope,
)
from users.models import PasswordResetOTP, UserTypes
from users.serializers import CustomTokenObtainPairSerializer

User = get_user_model()
PW = "Lock-envelope-pw-1"  # pragma: allowlist secret
NEW_PW = "a-brand-new-password-77"  # pragma: allowlist secret
LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
ADDED = {
    "reason_code",
    "error_class",
    "remediation",
    "retryable",
    "params",
    "reference",
}


def make_user(email, **extra):
    fields = {
        "is_active": True,
        "email_verified_at": timezone.now(),
        "user_type": UserTypes.TEACHER,
        **extra,
    }
    return User.objects.create_user(email=email, password=PW, **fields)


class LockBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

    def body(self, response):
        return response.json()["error"]["field_errors"]

    def assert_envelope(self, response, code, error_class="USER"):
        fields = self.body(response)
        self.assertTrue(ADDED <= set(fields), sorted(fields))
        self.assertEqual(fields["reason_code"], code)
        self.assertEqual(fields["error_class"], error_class)
        self.assertEqual(
            fields["remediation"], REASON_CODES[ReasonCode(code)].remediation
        )
        self.assertIs(fields["retryable"], True)
        self.assertEqual(fields["params"], {})
        self.assertEqual(fields["reference"], response["X-Request-ID"])
        return fields


@override_settings(CACHES=LOCMEM_CACHE)
class ResetLockedTests(LockBase):
    def test_the_documented_fields_stay_and_the_envelope_is_added(self):
        user = make_user("lock.reset@example.com")
        otp = PasswordResetOTP.objects.create(user=user)
        code = otp.generate_code()
        PasswordResetOTP.objects.filter(pk=otp.pk).update(
            locked_until=timezone.now() + timedelta(minutes=30)
        )

        response = self.client.post(
            reverse("auth-reset-password"),
            {"email": user.email, "otp": code, "new_password": NEW_PW},
            format="json",
        )

        self.assertEqual(response.status_code, 429)
        fields = self.assert_envelope(response, "RESET_LOCKED")
        # The fields the reset-password docs promise, unchanged.
        self.assertEqual(fields["code"], "RESET_LOCKED")
        self.assertIn("password reset is paused", fields["message"])
        self.assertIn("locked_until", fields)
        self.assertEqual(response["Retry-After"], str(fields["retry_after_seconds"]))
        # The display message is still that text alone.
        self.assertEqual(response.json()["message"], fields["message"])
        self.assertNotIn("error", fields)


@override_settings(CACHES=LOCMEM_CACHE)
class VerifyLockedTests(LockBase):
    def lock(self, email):
        for attempt in range(1, 6):
            self.client.post(
                reverse("auth-verify"),
                {"email": email, "token": "000000"},
                format="json",
                REMOTE_ADDR=f"10.60.0.{attempt}",
            )
        return self.client.post(
            reverse("auth-verify"),
            {"email": email, "token": "000000"},
            format="json",
            REMOTE_ADDR="10.60.1.1",
        )

    def test_the_lock_is_told_apart_from_the_per_ip_rate_limit(self):
        make_user(
            "lock.verify@example.com",
            is_active=False,
            email_verified_at=None,
            activation_token="123456",
            activation_expires=timezone.now() + timedelta(minutes=15),
        )
        response = self.lock("lock.verify@example.com")

        self.assertEqual(response.status_code, 429)
        fields = self.assert_envelope(response, "VERIFY_LOCKED")
        self.assertEqual(fields["code"], "VERIFY_LOCKED")
        self.assertIn("Too many incorrect codes", fields["detail"])
        self.assertTrue(response["Retry-After"])
        self.assertEqual(response.json()["message"], fields["detail"])

        # The per-IP limit (5/hour) is still the plain throttle, with no code.
        for _ in range(6):
            limited = self.client.post(
                reverse("auth-verify"),
                {"email": "someone.else@example.com", "token": "000000"},
                format="json",
                REMOTE_ADDR="10.61.0.1",
            )
        self.assertEqual(limited.status_code, 429)
        self.assertNotIn("code", self.body(limited))
        self.assertNotIn("reason_code", self.body(limited))

    def test_a_locked_unknown_address_answers_exactly_like_a_known_one(self):
        make_user(
            "known.locked@example.com",
            is_active=False,
            email_verified_at=None,
            activation_token="123456",
            activation_expires=timezone.now() + timedelta(minutes=15),
        )
        known = self.lock("known.locked@example.com")
        unknown = self.lock("nobody.locked@example.com")

        self.assertEqual(known.status_code, unknown.status_code)
        a, b = self.body(known), self.body(unknown)
        self.assertEqual(set(a), set(b))
        for key in set(a) - {"reference", "detail"}:
            self.assertEqual(a[key], b[key], key)
        strip = lambda text: text.split(" Expected available")[0]  # noqa: E731
        self.assertEqual(strip(a["detail"]), strip(b["detail"]))
        self.assertEqual(
            known.has_header("Retry-After"), unknown.has_header("Retry-After")
        )


@override_settings(CACHES=LOCMEM_CACHE)
class LoginLockedTests(LockBase):
    def login(self, email):
        return self.client.post(
            reverse("login"), {"email": email, "password": PW}, format="json"
        )

    def test_the_401_message_and_legacy_code_stay_and_the_envelope_is_added(self):
        user = make_user("lock.login@example.com")
        User.objects.filter(pk=user.pk).update(
            locked_until=timezone.now() + timedelta(minutes=15)
        )

        response = self.login(user.email)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["detail"].code, "account_locked")
        fields = self.assert_envelope(response, "ACCOUNT_LOCKED")
        self.assertEqual(fields["code"], "account_locked")
        self.assertEqual(
            fields["detail"], CustomTokenObtainPairSerializer.LOCKED_MESSAGE
        )
        self.assertEqual(
            response.json()["message"], CustomTokenObtainPairSerializer.LOCKED_MESSAGE
        )

    def test_an_unknown_address_gains_nothing(self):
        """Only a real account can be locked (pre-existing); an unknown
        address's 401 is unchanged - no code, no envelope."""
        response = self.login("nobody.login@example.com")

        self.assertEqual(response.status_code, 401)
        self.assertFalse(ADDED & set(self.body(response)))
        self.assertNotIn("code", self.body(response))


class CatalogueTests(APITestCase):
    def test_the_three_locks_are_user_facing_now(self):
        for code in ("RESET_LOCKED", "VERIFY_LOCKED", "ACCOUNT_LOCKED"):
            with self.subTest(code=code):
                self.assertIn(ReasonCode(code), REASON_CODES)
                self.assertNotIn(ReasonCode(code), AUDIT_ONLY_CODES)

    def test_the_envelope_is_added_never_replacing_a_key(self):
        data = {"code": "RESET_LOCKED", "message": "keep me", "reference": "mine"}
        add_coded_envelope(data, "RESET_LOCKED", "ignored", code_value="other")
        self.assertEqual(data["message"], "keep me")
        self.assertEqual(data["reference"], "mine")
        self.assertEqual(data["code"], "RESET_LOCKED")
        self.assertNotIn("error", data)
        self.assertTrue(ADDED - {"reference"} <= set(data))
        self.assertTrue(set(data) - {"message"} <= set(ENVELOPE_KEYS))
