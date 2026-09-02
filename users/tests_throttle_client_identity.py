"""
users/tests_throttle_client_identity.py
=======================================
Locks down WHICH CLIENT a rate limit is counted against.

Every throttle on the unauthenticated auth surface (users/throttling.py)
inherits `AnonRateThrottle`, so its bucket is keyed on the caller's IP as
resolved by `BaseThrottle.get_ident()`. That resolution depends entirely
on `NUM_PROXIES`:

    # rest_framework/throttling.py
    if num_proxies is not None:
        if num_proxies == 0 or xff is None:
            return remote_addr
        addrs = xff.split(',')
        client_addr = addrs[-min(num_proxies, len(addrs))]   # from the RIGHT
        return client_addr.strip()

    return ''.join(xff.split()) if xff else remote_addr      # WHOLE chain

With `NUM_PROXIES` unset, the key is the ENTIRE X-Forwarded-For chain.
The app runs behind Railway's edge, which appends to that header rather
than replacing it, so the caller controls its left-hand portion. Every
distinct value the caller invents is a distinct bucket, and the limit is
reset at will.

That is not theoretical. Against the live beta service, 14 rapid login
attempts (limit 10/min) returned 401 fourteen times and never once 429,
and 7 password-reset OTP requests (limit 5/hour) all returned 202.

This matters most for `otp`: users/throttling.py documents that
`PasswordResetOTP.generate_code()` resets the failed-attempt counter, so
unlimited code requests re-open the brute-force path that the per-account
lockout is supposed to close.

`test_forged_x_forwarded_for_cannot_reset_the_bucket` is the regression
test. It FAILS with NUM_PROXIES unset and passes once it is set.
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import UserTypes
from users.throttling import LoginThrottle

LOCMEM_CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
}

# X-Forwarded-For semantics: each proxy APPENDS the address of the peer it
# received the request from. So the edge appends the true client and the
# real client ends up RIGHTMOST; anything a caller sends of its own sits to
# the LEFT of that. The edge's own address is what Django sees as
# REMOTE_ADDR, and never appears in the header.
REAL_CLIENT = "198.51.100.7"
OTHER_CLIENT = "203.0.113.55"
EDGE = "10.0.0.1"  # Railway's edge, as seen in REMOTE_ADDR


@override_settings(CACHES=LOCMEM_CACHES)
class LoginThrottleClientIdentityTests(APITestCase):
    """
    Uses the login endpoint because its budget is the smallest to exhaust
    (10/min in settings; patched lower here). The keying behaviour under
    test is shared by every AnonRateThrottle subclass in
    users/throttling.py, so what holds here holds for otp,
    reset_password, register, verify and google_auth too.
    """

    def setUp(self):
        cache.clear()
        self.url = reverse("login")
        self.body = {
            "email": "no-such-account@example.invalid",
            "password": "wrong-password",  # pragma: allowlist secret
        }

    def _post(self, xff=None):
        extra = {"REMOTE_ADDR": EDGE}
        if xff is not None:
            extra["HTTP_X_FORWARDED_FOR"] = xff
        return self.client.post(self.url, self.body, format="json", **extra)

    @patch.object(LoginThrottle, "rate", "3/min", create=True)
    def test_throttle_fires_for_a_single_consistent_client(self):
        """Baseline: the limit works when the caller does not move."""
        chain = REAL_CLIENT

        for i in range(3):
            self.assertNotEqual(
                self._post(chain).status_code,
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"request {i + 1} should be within budget",
            )

        self.assertEqual(
            self._post(chain).status_code,
            status.HTTP_429_TOO_MANY_REQUESTS,
            "the 4th request from the same client must be throttled",
        )

    @patch.object(LoginThrottle, "rate", "3/min", create=True)
    def test_forged_x_forwarded_for_cannot_reset_the_bucket(self):
        """
        THE REGRESSION TEST.

        The caller spends its budget, then invents a new left-hand entry
        in X-Forwarded-For. Railway still appends the true client, so the
        rightmost entries are unchanged and identical in both chains.

        With NUM_PROXIES unset the key is the whole chain, the forged
        prefix makes it a different key, and the 4th request sails
        through - a complete bypass of every auth rate limit.
        """
        for _ in range(3):
            self._post(REAL_CLIENT)

        # The caller prepends junk; the edge still appends the truth.
        forged = f"203.0.113.99, {REAL_CLIENT}"

        self.assertEqual(
            self._post(forged).status_code,
            status.HTTP_429_TOO_MANY_REQUESTS,
            "a caller reset its own rate limit by prepending a fake entry "
            "to X-Forwarded-For — every throttle in users/throttling.py is "
            "bypassable. Set NUM_PROXIES so get_ident() reads the client "
            "from the RIGHT of the chain, where the edge writes it.",
        )

    @patch.object(LoginThrottle, "rate", "3/min", create=True)
    def test_distinct_real_clients_keep_separate_budgets(self):
        """
        The fix must not over-correct into one shared global bucket:
        exhausting one client's budget must not throttle a different one.
        Guards against setting NUM_PROXIES too high, which would read the
        edge's own address for everybody.
        """
        for _ in range(3):
            self._post(REAL_CLIENT)

        other = self._post(OTHER_CLIENT)

        self.assertNotEqual(
            other.status_code,
            status.HTTP_429_TOO_MANY_REQUESTS,
            "a different client was throttled by someone else's usage — "
            "NUM_PROXIES is too high and every caller shares one bucket.",
        )


@override_settings(CACHES=LOCMEM_CACHES)
class OTPThrottleScopeTests(APITestCase):
    """
    The otp and reset-password buckets must stay separate. They are the
    pair users/throttling.py calls load-bearing: generate_code() clears
    the failed-attempt counter, so if issuing shared a budget with
    guessing, an attacker could trade guesses for fresh codes.
    """

    def setUp(self):
        cache.clear()

    def test_requesting_a_code_does_not_spend_the_reset_budget(self):
        from users.throttling import OTPRequestThrottle, PasswordResetThrottle

        self.assertNotEqual(
            OTPRequestThrottle.scope,
            PasswordResetThrottle.scope,
            "otp issuing and password reset share a bucket; requesting "
            "codes would consume the guess budget and vice versa",
        )
        self.assertEqual(OTPRequestThrottle.scope, "otp_request")
        self.assertEqual(PasswordResetThrottle.scope, "password_reset")


@override_settings(CACHES=LOCMEM_CACHES)
class RegistrationThrottleScopeTests(APITestCase):
    """
    Documents the CURRENT pooling of the three register endpoints and
    renew-student-token into one scope, so that if it is ever split the
    change is deliberate. See the discussion in
    docs/ops/append-only-audit-tables.md's sibling note - the pooling is
    intentional today because renew-student-token is both a token-guessing
    oracle and a free outbound-mail trigger (classrooms/views.py).
    """

    def test_registration_endpoints_currently_share_one_scope(self):
        from users.throttling import RegisterThrottle

        self.assertEqual(RegisterThrottle.scope, "register")

    def test_student_type_exists_for_future_split(self):
        self.assertTrue(hasattr(UserTypes, "STUDENT"))
