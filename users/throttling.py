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

import hashlib
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


# DEAD since H-152 closed the old student door (nothing calls it); kept in this merge-down; removal is H-207.
def register_student_failure_budget_spent():
    count = cache.get(_register_student_failure_key()) or 0
    return count >= settings.REGISTER_STUDENT_GLOBAL_FAILURE_LIMIT


# DEAD since H-152 closed the old student door (nothing calls it); kept in this merge-down; removal is H-207.
def register_student_budget_retry_after(now=None):
    """Seconds until the current window rolls over (for Retry-After)."""
    window = settings.REGISTER_STUDENT_FAILURE_WINDOW_SECONDS
    now = now if now is not None else time.time()
    return max(1, int(window - (now % window)))


# DEAD since H-152 closed the old student door (nothing calls it); kept in this merge-down; removal is H-207.
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


# DEAD since H-152 closed the old student door (nothing calls it); kept in this merge-down; removal is H-207.
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


# ---------------------------------------------------------------------------
# H-53: per-address budget of attempts on POST /auth/verify.
#
# VerifyEmailThrottle is per IP only, so with enough addresses an attacker
# who registered with someone else's email could keep guessing that
# account's 6-digit code. Like AUTHZ-L2's reset budget, this one belongs to
# the ADDRESS: after VERIFY_EMAIL_MAX_FAILURES attempts that don't verify,
# from any number of IPs, the address is locked for
# VERIFY_EMAIL_LOCK_SECONDS, and every verify attempt (a correct code or a
# freshly re-sent one included) is refused until the lock ends. A re-sent
# code never refills the budget, which was L2's lesson.
#
# Unlike L2, the lock does NOT clear the stored code: activation_token also
# holds student and school-admin invitations, which anyone could otherwise
# destroy with a few wrong guesses. VERIFY_EMAIL_LOCK_SECONDS must stay
# longer than a sign-up code's 15 minutes, so that code is dead when the
# lock ends; a 24-hour invitation code gets at most MAX_FAILURES guesses
# per lock period.
#
# The attempt is spent BEFORE the code is checked (`reserve_verify_attempt`)
# and refunded only by a successful verify. Counting failures afterwards
# would let a burst of simultaneous guesses from many IPs all pass the
# check before the one that locks the address has finished.
#
# Keyed on the normalised address, NOT the account, and applied to unknown
# addresses too, so a lock says nothing about whether an account exists.
# Counters live in the cache (atomic add + incr, as H-47's budget does); if
# the cache is unavailable the budget fails open and logs, rather than
# refusing every sign-up.


def _verify_address_key(kind, email):
    digest = hashlib.sha256((email or "").strip().lower().encode()).hexdigest()
    return f"verify_email:{kind}:{digest[:32]}"


def verify_lock_until(email):
    """The epoch second the address's lock ends, or None if not locked."""
    try:
        until = cache.get(_verify_address_key("locked", email))
    except Exception:  # noqa: BLE001 - fail open, see above
        logger.error("verify_email budget unavailable (cache read failed)")
        return None
    if until and until > time.time():
        return until
    return None


def reserve_verify_attempt(email):
    """Spend one attempt from `email`'s budget before its code is checked.
    Returns the attempt's number in the window, or None if the cache is
    unavailable (fail open)."""
    window = settings.VERIFY_EMAIL_LOCK_SECONDS
    key = _verify_address_key("attempts", email)
    try:
        cache.add(key, 0, timeout=window)
        try:
            return cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=window)
            return 1
    except Exception:  # noqa: BLE001 - fail open, see above
        logger.error("verify_email budget unavailable (cache write failed)")
        return None


def verify_budget_spent(attempt):
    return attempt is not None and attempt >= settings.VERIFY_EMAIL_MAX_FAILURES


def verify_attempt_over_budget(attempt):
    return attempt is not None and attempt > settings.VERIFY_EMAIL_MAX_FAILURES


def lock_verify_address(email):
    """The budget is spent: lock `email` for VERIFY_EMAIL_LOCK_SECONDS. The
    attempt counter needs no reset: its window began at the first attempt,
    so it has expired by the time the lock ends."""
    window = settings.VERIFY_EMAIL_LOCK_SECONDS
    try:
        if not cache.add(
            _verify_address_key("locked", email), time.time() + window, timeout=window
        ):
            return
    except Exception:  # noqa: BLE001 - fail open, see above
        logger.error("verify_email budget unavailable (cache write failed)")
        return
    # No address or code in the log: the event is enough to see a guessing run.
    logger.warning(
        "verify_email address locked after too many attempts",
        extra={
            "event": "verify_email.locked",
            "limit": settings.VERIFY_EMAIL_MAX_FAILURES,
        },
    )


def clear_verify_failures(email):
    try:
        cache.delete(_verify_address_key("attempts", email))
    except Exception:  # noqa: BLE001
        pass
