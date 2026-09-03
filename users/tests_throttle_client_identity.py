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

# These constants mirror a REAL reading taken from the deployed beta
# service via /api/v1/health/client, not an assumed model of how proxies
# behave. An earlier version of this file assumed the edge APPENDS the
# true client, putting it rightmost. That is wrong for this platform and
# the assumption cost several deploy cycles. What Railway actually does:
#
#   x_forwarded_for: "129.222.206.195, 152.233.29.4"
#                     ^ true client    ^ edge instance, ROTATES per request
#   x_real_ip:       "129.222.206.195"
#   remote_addr:     "100.64.0.3"      (internal mesh, not in the chain)
#
# Two consequences, both load-bearing:
#
#  1. The true client is SECOND FROM THE RIGHT -> NUM_PROXIES = 2. With 1,
#     get_ident() returns the rotating edge address, so nearly every
#     request lands in a fresh bucket and NO limit can ever be reached.
#     That was the live bug: not a bypass, simply no rate limiting at all.
#
#  2. A client-supplied X-Forwarded-For is STRIPPED, not appended to. A
#     forged header never reaches the chain, so forgery is not the threat
#     here - the rotating edge was.
REAL_CLIENT = "198.51.100.7"
OTHER_CLIENT = "203.0.113.55"
# Rotates per request in production; two values suffice to prove the key
# must not depend on it.
EDGE_A = "10.0.0.1"
EDGE_B = "10.0.0.2"
MESH = "100.64.0.3"  # what Django sees as REMOTE_ADDR


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

    def _post(self, client_addr, edge=EDGE_A):
        """One request as `client_addr`, arriving via `edge`."""
        return self.client.post(
            self.url,
            self.body,
            format="json",
            REMOTE_ADDR=MESH,
            HTTP_X_FORWARDED_FOR=f"{client_addr}, {edge}",
        )

    @patch.object(LoginThrottle, "rate", "3/min", create=True)
    def test_throttle_fires_for_a_single_consistent_client(self):
        """Baseline: the limit works when the caller does not move."""
        for i in range(3):
            self.assertNotEqual(
                self._post(REAL_CLIENT).status_code,
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"request {i + 1} should be within budget",
            )

        self.assertEqual(
            self._post(REAL_CLIENT).status_code,
            status.HTTP_429_TOO_MANY_REQUESTS,
            "the 4th request from the same client must be throttled",
        )

    @patch.object(LoginThrottle, "rate", "3/min", create=True)
    def test_a_rotating_edge_address_cannot_reset_the_bucket(self):
        """
        THE REGRESSION TEST, and the bug that was actually live.

        Railway's edge address is the RIGHTMOST entry and rotates between
        instances request to request. Measured on beta: consecutive calls
        reported 152.233.29.4, then 46.151.193.242, then 46.151.193.241.

        With NUM_PROXIES=1 get_ident() returns that rotating value, so a
        single caller lands in a new bucket almost every request and no
        limit is EVER reached - which is what production looked like: 14
        rapid logins against a 10/min cap, all 401, never a 429.

        The same caller arriving via a different edge must share one
        bucket.
        """
        for _ in range(3):
            self._post(REAL_CLIENT, edge=EDGE_A)

        self.assertEqual(
            self._post(REAL_CLIENT, edge=EDGE_B).status_code,
            status.HTTP_429_TOO_MANY_REQUESTS,
            "the same client got a fresh budget merely by being routed "
            "through a different edge instance — get_ident() is keying on "
            "the rotating right-hand entry, so no rate limit can ever be "
            "reached. NUM_PROXIES must point at the true client "
            "(second from the right on this platform).",
        )

    @patch.object(LoginThrottle, "rate", "3/min", create=True)
    def test_distinct_real_clients_keep_separate_budgets(self):
        """
        The fix must not over-correct into one shared global bucket:
        exhausting one client's budget must not throttle a different one.
        Note on the opposite error: setting NUM_PROXIES too HIGH does not
        fail here, and that is correct rather than a gap in the test. DRF
        clamps with `addrs[-min(num_proxies, len(addrs))]`, so against
        this platform's two-entry chain any value >= 2 resolves to the
        same client entry. Over-setting only starts reading an upstream
        address - identical for everybody, collapsing all callers into
        one bucket - if a longer chain ever appears, e.g. another proxy
        (a CDN) is put in front. Verified: NUM_PROXIES=3 passes today.
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
