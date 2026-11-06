from rest_framework import status
from rest_framework.exceptions import APIException


class BillingRequestError(ValueError):
    """A billing request the caller can fix; its text is written to be shown
    (H-265). A ValueError subclass, so every `except ValueError` keeps
    working; views show THIS text, and any other ValueError gets a fixed
    fallback (`AutoGrader.error_messages.describe_user_error`)."""


class InsufficientCreditsError(Exception):
    """Not enough credits for the request. The text is for logs only.

    `individual` (H-281): True when the billed user is an individual teacher
    (see is_individual_teacher), set where the refusal is raised. A client
    then reads the individual sentence (no school administrator to ask); see
    AutoGrader.reason_codes.credit_refusal_variant."""

    def __init__(self, *args, individual=False):
        super().__init__(*args)
        self.individual = individual


class EmptyWalletError(InsufficientCreditsError, APIException):
    """Raised by the HasCreditBalance permission. An InsufficientCreditsError
    like any other (users.exceptions answers it 402 "insufficient_credits"),
    and also an APIException, because DRF's own permission-check callers -
    the browsable API renderer's form checks - only catch APIException."""

    status_code = status.HTTP_402_PAYMENT_REQUIRED
    default_code = "insufficient_credits"


class StudentEmptyWalletError(EmptyWalletError):
    """HasCreditBalance's refusal of a STUDENT, whose AI calls are billed to
    their teacher (H-225). An EmptyWalletError in every other way;
    users.exceptions answers it with the one fixed student sentence and no
    remediation, so a student is never told about the teacher's wallet."""


# What a client is told for an InsufficientCreditsError (an individual teacher
# reads INSUFFICIENT_CREDITS_INDIVIDUAL_MESSAGE below). The exception's own
# text can carry the wallet's balance, the task's estimate, or an unsettled
# chargeback/refund deficit - internal billing state that belongs in server
# logs, never in an API error body or a polled task error.
INSUFFICIENT_CREDITS_MESSAGE = (
    "There aren't enough AI credits to do this. Top up your credits or ask "
    "your school administrator, then try again."
)
# H-281 (owner-approved 2026-11-03): the same refusal read by an individual
# teacher, who has no school administrator to ask.
INSUFFICIENT_CREDITS_INDIVIDUAL_MESSAGE = (
    "There aren't enough AI credits to do this. Top up your credits, then " "try again."
)


def is_individual_teacher(user):
    """True when `user`, the BILLED user of a credit refusal, is a teacher
    whose wallet no school license funds (H-281).

    The one place this is decided. Keyed on is_under_license(), not on
    school_id, as sessions are (classrooms/permissions.py): school_id stays
    set after a teacher is removed from a license or the license lapses, and
    such a teacher tops up on their own."""
    from users.models import UserTypes

    # getattr: no billed user (None), or an anonymous request user
    # (require_ai_access), is never individual.
    return (
        getattr(user, "user_type", None) == UserTypes.TEACHER
        and not user.is_under_license()
    )
