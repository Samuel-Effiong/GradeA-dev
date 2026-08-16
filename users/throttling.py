"""
Named rate-limit buckets for the unauthenticated auth surface.

DRF resolves a scope from the `throttle_scope` attribute on the *view*, but
`AuthViewSet` hosts every auth action on one class, so a single attribute
cannot give `otp` and `reset_password` different budgets. One thin subclass
per scope sidesteps that: the scope travels with the throttle class, which
is attached per-action via `throttle_classes=[...]`.

All of these key on IP (inherited from `AnonRateThrottle`) because the
endpoints they guard are reached before the caller has a token. Rates are
defined in `REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]`.
"""

from rest_framework.throttling import AnonRateThrottle


class LoginThrottle(AnonRateThrottle):
    scope = "login"


class OTPRequestThrottle(AnonRateThrottle):
    """
    Guards the OTP *issuing* endpoint.

    This is load-bearing for the reset-password lockout, not just spam
    control: `PasswordResetOTP.generate_code()` resets the failed-attempt
    counter, so an attacker who can request unlimited fresh codes can clear
    the lockout and keep guessing. Removing this throttle re-opens the
    brute-force path even though the counter itself stays in place.
    """

    scope = "otp_request"


class PasswordResetThrottle(AnonRateThrottle):
    scope = "password_reset"


class RegisterThrottle(AnonRateThrottle):
    scope = "register"


class GoogleAuthThrottle(AnonRateThrottle):
    scope = "google_auth"
