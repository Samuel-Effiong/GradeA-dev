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


def _passthrough_message(error):
    """The verbatim message for a known user-authored exception, or None."""
    if isinstance(error, _user_facing_exception_types()):
        message = str(error).strip()
        if message:
            return message
    return None


def describe_user_error(error, fallback_message=None):
    """
    Turn a caught exception (or None, or a plain string) into a message
    that's safe to show a user.

    `fallback_message` should be a short, plain-language, operation-specific
    sentence ("We couldn't grade this submission. Please try again.").
    Falls back to a generic default if none is given.
    """
    return _passthrough_message(error) or fallback_message or DEFAULT_ERROR_MESSAGE


def _infra_error_categories():
    # Imported lazily for the same reason as _user_facing_exception_types():
    # keep this module cheap and dependency-order-agnostic to import.
    import requests
    from cloudinary.exceptions import Error as CloudinaryError
    from cloudinary.exceptions import RateLimited as CloudinaryRateLimited
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )
    from pdf2image.exceptions import (
        PDFInfoNotInstalledError,
        PDFPageCountError,
        PDFPopplerTimeoutError,
        PDFSyntaxError,
        PopplerNotInstalledError,
    )
    from PIL import UnidentifiedImageError

    from ai_processor.tools import ImageCompressionError

    # Checked in order, most specific first — several of these types are
    # subclasses of a more generic type checked further down (e.g.
    # APITimeoutError < APIConnectionError, and both TimeoutError and
    # ConnectionError < OSError), so ordering here is load-bearing.
    return (
        (
            (
                TimeoutError,
                APITimeoutError,
                requests.exceptions.Timeout,
                PDFPopplerTimeoutError,
            ),
            "Grading timed out before it could finish. This usually happens with "
            "large or complex files, or when the grading service is slow right "
            "now — please try again.",
        ),
        (
            (RateLimitError, CloudinaryRateLimited),
            "The grading service is temporarily at capacity. Please try again in "
            "a few minutes.",
        ),
        (
            (APIConnectionError, requests.exceptions.ConnectionError, ConnectionError),
            "We lost connection to the grading service partway through. Please "
            "try again.",
        ),
        (
            (InternalServerError,),
            "The grading service ran into an internal error on its end. Please "
            "try again, or contact support if this continues.",
        ),
        (
            (ImageCompressionError,),
            "This file is too large for us to process. Try a smaller file, "
            "fewer pages, or lower-resolution scans.",
        ),
        (
            (
                UnidentifiedImageError,
                PDFSyntaxError,
                PDFPageCountError,
                PopplerNotInstalledError,
                PDFInfoNotInstalledError,
            ),
            "We couldn't read this file — it may be corrupted, "
            "password-protected, or in an unsupported format.",
        ),
        (
            (CloudinaryError, OSError),
            "We couldn't save the file due to a storage issue on our end. "
            "Please try again, or contact support if this continues.",
        ),
    )


def classify_infra_error(error):
    """
    Recognize a small set of common infra failure modes (timeouts, dropped
    connections, unreadable/oversized files, storage failures) and return a
    short, distinct, actionable sentence for the ones we can identify.

    Walks `__cause__`/`__context__` since most of the grading pipeline
    catches these at the source and re-raises a bare `Exception(str(e))`,
    which would otherwise erase the original type before it gets here.

    Returns None when nothing in the chain matches a known category, so
    callers can fall back to their own operation-specific message.
    """
    if not isinstance(error, BaseException):
        return None

    categories = _infra_error_categories()

    seen_ids = set()
    current: BaseException | None = error
    depth = 0
    while current is not None and depth < 5 and id(current) not in seen_ids:
        seen_ids.add(id(current))
        for exception_types, message in categories:
            if isinstance(current, exception_types):
                return message
        current = current.__cause__ or current.__context__
        depth += 1

    return None


def describe_background_task_error(error, fallback_message=None):
    """
    Background-task flavor of describe_user_error: in addition to the
    known user-authored exceptions, it also recognizes common infra failure
    modes (see classify_infra_error) and gives each its own actionable
    sentence, instead of every non-whitelisted exception collapsing into
    one generic per-operation fallback.
    """
    passthrough = _passthrough_message(error)
    if passthrough:
        return passthrough

    infra_message = classify_infra_error(error)
    if infra_message:
        return infra_message

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
