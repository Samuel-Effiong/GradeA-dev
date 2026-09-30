"""Django system checks for the audit trail.

audit.E001 (v2's N1 on the beta merge-down, SM order): the failed-auth cap
(`audit.failed_auth_cap`, Epic A S1b) always writes an account's first
FAILED_AUTH_TARGET_FLOOR failed or refused sign-ins in a window. The events
that record a lock - the guess that spent a budget (`lock_triggered`) and the
refusals after it (`VERIFY_LOCKED`, `RESET_LOCKED`, `ACCOUNT_LOCKED`) - are
kept as individual rows only because every lock threshold is within that
floor. If an environment change raised a threshold above the floor, or
lowered the floor below a threshold, those rows could be folded into a
FAILED_AUTH_CAPPED summary, and the moment an account was locked would
vanish from the trail. Nothing would fail: this check is what notices.
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
    over = {name: value for name, value in lock_thresholds().items() if value > floor}
    if not over:
        return []
    return [
        Error(
            f"FAILED_AUTH_TARGET_FLOOR ({floor}) is below the lock threshold(s) "
            f"{dict(sorted(over.items()))}. The failed-auth cap could then fold "
            f"the event that sets a lock, and the refusals after it, into a "
            f"FAILED_AUTH_CAPPED summary, so the audit trail would lose when "
            f"an account was locked.",
            hint=(
                "Raise FAILED_AUTH_TARGET_FLOOR to at least the largest lock "
                "threshold, or lower the threshold."
            ),
            id="audit.E001",
        )
    ]
