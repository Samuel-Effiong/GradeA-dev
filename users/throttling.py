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

import logging
import time

from django.conf import settings
from django.core.cache import cache
from rest_framework.throttling import AnonRateThrottle

logger = logging.getLogger(__name__)


class LoginThrottle(AnonRateThrottle):
    scope = "login"


class VerifyEmailThrottle(AnonRateThrottle):
    """
    Guards the account-activation verify endpoint (AuthViewSet.verify).

    The token it checks is intentionally a 6-digit numeric code (see
    users.models.ACTIVATION_TOKEN_VALIDITY for why), which is a small
    enough keyspace to brute-force for a known email address if this
    endpoint is left unthrottled - it previously fell back to the generic
    60/min AnonRateThrottle, which does essentially nothing to stop that.
    """

    scope = "verify_email"


class OTPRequestThrottle(AnonRateThrottle):
    """
    Guards the OTP *issuing* endpoint.

    Spam / email-bombing control. It is no longer what protects the
    reset-code guess budget: `PasswordResetOTP.generate_code()` does not
    refill the attempts counter while the account is locked (AUTHZ-L2), so
    the budget holds per account regardless of how many IPs re-request.
    """

    scope = "otp_request"


class PasswordResetThrottle(AnonRateThrottle):
    scope = "password_reset"


class RegisterThrottle(AnonRateThrottle):
    scope = "register"


class GoogleAuthThrottle(AnonRateThrottle):
    scope = "google_auth"


# H-47: POST /auth/register/student matches a 6-digit code against EVERY
# pending student at once, so a per-IP bucket alone doesn't bound a
# distributed guesser. This global budget of failed attempts does: once it's
# spent, the endpoint refuses everyone until the window rolls over. That
# trades a possible pause in student sign-ups for bounding total guesses.
def _register_student_failure_key(now=None):
    window = settings.REGISTER_STUDENT_FAILURE_WINDOW_SECONDS
    bucket = int((now if now is not None else time.time()) // window)
    return f"register_student:failures:{bucket}"


def register_student_failure_budget_spent():
    count = cache.get(_register_student_failure_key()) or 0
    return count >= settings.REGISTER_STUDENT_GLOBAL_FAILURE_LIMIT


def register_student_budget_retry_after(now=None):
    """Seconds until the current window rolls over (for Retry-After)."""
    window = settings.REGISTER_STUDENT_FAILURE_WINDOW_SECONDS
    now = now if now is not None else time.time()
    return max(1, int(window - (now % window)))


def log_register_student_refused_by_budget(door="register"):
    logger.warning(
        "register_student refused: global failure budget spent",
        extra={
            "event": "register_student.budget_refusal",
            "door": door,
            "limit": settings.REGISTER_STUDENT_GLOBAL_FAILURE_LIMIT,
            "window_seconds": settings.REGISTER_STUDENT_FAILURE_WINDOW_SECONDS,
        },
    )


def record_register_student_failure(reason):
    key = _register_student_failure_key()
    # add() sets the TTL only when the key is new; incr() is atomic on Redis.
    cache.add(key, 0, timeout=settings.REGISTER_STUDENT_FAILURE_WINDOW_SECONDS)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, timeout=settings.REGISTER_STUDENT_FAILURE_WINDOW_SECONDS)
        count = 1
    # No token, email or IP: the reason and running count are enough to see
    # a guessing run in the logs.
    logger.warning(
        "register_student failed attempt",
        extra={"reason": reason, "window_failures": count},
    )
    if count == settings.REGISTER_STUDENT_GLOBAL_FAILURE_LIMIT:
        # Once per window, at ERROR, so alerting can page on it: from here
        # every student sign-up is refused until the window rolls over.
        logger.error(
            "register_student global failure budget exhausted; "
            "student registration paused until the window rolls over",
            extra={
                "event": "register_student.budget_exhausted",
                "limit": settings.REGISTER_STUDENT_GLOBAL_FAILURE_LIMIT,
                "window_seconds": settings.REGISTER_STUDENT_FAILURE_WINDOW_SECONDS,
                "retry_after_seconds": register_student_budget_retry_after(),
            },
        )
    return count
