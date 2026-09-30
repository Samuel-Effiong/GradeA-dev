"""Django system checks for the audit trail.

audit.E001 (v2's N1 on the beta merge-down, SM order): the failed-auth cap
(`audit.failed_auth_cap`, Epic A S1b) always writes an account's first
FAILED_AUTH_TARGET_FLOOR failed or refused sign-ins in a window. The events
that record a lock - the guess that spent a budget (`lock_triggered`) and the
refusals after it (`VERIFY_LOCKED`, `RESET_LOCKED`, `ACCOUNT_LOCKED`) - are
kept as individual rows only while two things hold for every lock threshold
T (attempts 1..T are failures; the T-th sets the lock):

- T <= FAILED_AUTH_TARGET_FLOOR, so every attempt up to and including the
  one that sets the lock is always written;
- T < FAILED_AUTH_TARGET_LIMIT (v2's N2, SM ruling), so the first refusal
  after the lock - attempt T+1, which the cap writes only while it is within
  the per-target limit - is written too.

If an environment change broke either, those rows could be folded into a
FAILED_AUTH_CAPPED summary, and the moment an account was locked would
vanish from the trail. Nothing would fail: this check is what notices. (It
runs wherever `manage.py check` runs; whether the deploy runs it is a
separate backlog item.)
"""

from django.core.checks import Error, register


def lock_thresholds():
    """{name: attempts} for every sign-in lock the audit trail must record."""
    from django.conf import settings

    from users.models import CustomUser, PasswordResetOTP

    return {
        "VERIFY_EMAIL_MAX_FAILURES": settings.VERIFY_EMAIL_MAX_FAILURES,
        "CustomUser.MAX_LOGIN_ATTEMPTS": CustomUser.MAX_LOGIN_ATTEMPTS,
        "PasswordResetOTP.MAX_ATTEMPTS": PasswordResetOTP.MAX_ATTEMPTS,
    }


@register("audit")
def check_failed_auth_floor_covers_every_lock(app_configs, **kwargs):
    from django.conf import settings

    floor = settings.FAILED_AUTH_TARGET_FLOOR
    limit = settings.FAILED_AUTH_TARGET_LIMIT
    thresholds = lock_thresholds()
    errors = []
    over_floor = {name: value for name, value in thresholds.items() if value > floor}
    if over_floor:
        errors.append(
            Error(
                f"FAILED_AUTH_TARGET_FLOOR ({floor}) is below the lock "
                f"threshold(s) {dict(sorted(over_floor.items()))}. The "
                f"failed-auth cap could then fold the event that sets a lock "
                f"into a FAILED_AUTH_CAPPED summary, so the audit trail would "
                f"lose when an account was locked.",
                hint=(
                    "Raise FAILED_AUTH_TARGET_FLOOR to at least the largest "
                    "lock threshold, or lower the threshold."
                ),
                id="audit.E001",
            )
        )
    not_under_limit = {
        name: value for name, value in thresholds.items() if value >= limit
    }
    if not_under_limit:
        errors.append(
            Error(
                f"FAILED_AUTH_TARGET_LIMIT ({limit}) is not above the lock "
                f"threshold(s) {dict(sorted(not_under_limit.items()))}. The "
                f"failed-auth cap could then fold the first refusal after a "
                f"lock into a FAILED_AUTH_CAPPED summary.",
                hint=(
                    "Raise FAILED_AUTH_TARGET_LIMIT above the largest lock "
                    "threshold, or lower the threshold."
                ),
                id="audit.E001",
            )
        )
    return errors
