"""
Shared "is this exception message safe to show a user" classifier.

Used anywhere a caught exception might otherwise leak straight into an API
response or a tracked background-task's error field: known, user-authored
exceptions (written by us specifically to be read by a teacher/student) pass
through as-is; everything else (bugs, network errors, raw third-party API
text, retry-exhaustion dumps) is replaced by a plain-language fallback
message, since a stack trace or exception class name is never actionable
for a non-technical reader. Callers are still expected to log the original
exception server-side — this module only decides what the *user* sees.
"""

from __future__ import annotations

DEFAULT_ERROR_MESSAGE = (
    "Something went wrong while processing your request. Please try again, "
    "and contact support if the problem continues."
)


def _user_facing_exception_types():
    # Imported lazily (at call time, not module load time) so this module
    # can be imported early/anywhere without pulling in the full import
    # chains of billing/students, and without caring which app happens to
    # finish loading first.
    from billing.access_control import AIFeatureNotAvailableError
    from billing.errors import InsufficientCreditsError
    from billing.license_service import IndividualSubscriptionConflictError
    from students.exceptions import CannotAssociateStudentError

    return (
        CannotAssociateStudentError,
        AIFeatureNotAvailableError,
        InsufficientCreditsError,
        IndividualSubscriptionConflictError,
    )


def describe_user_error(error, fallback_message=None):
    """
    Turn a caught exception (or None, or a plain string) into a message
    that's safe to show a user.

    `fallback_message` should be a short, plain-language, operation-specific
    sentence ("We couldn't grade this submission. Please try again.").
    Falls back to a generic default if none is given.
    """
    if isinstance(error, _user_facing_exception_types()):
        message = str(error).strip()
        if message:
            return message

    return fallback_message or DEFAULT_ERROR_MESSAGE


def describe_stripe_error(error, fallback_message=None):
    """
    Stripe exceptions carry their own customer-safe text in `.user_message`
    when the failure is something a cardholder can act on (declined card,
    insufficient funds, etc.). When Stripe doesn't provide one (API errors,
    connection errors, our own bugs), fall through to the generic classifier.
    """
    user_message = getattr(error, "user_message", None)
    if user_message:
        return user_message

    return describe_user_error(error, fallback_message)
