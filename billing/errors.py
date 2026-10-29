from rest_framework import status
from rest_framework.exceptions import APIException


class BillingRequestError(ValueError):
    """A billing request the caller can fix; its text is written to be shown
    (H-265). A ValueError subclass, so every `except ValueError` keeps
    working; views show THIS text, and any other ValueError gets a fixed
    fallback (`AutoGrader.error_messages.describe_user_error`)."""


class InsufficientCreditsError(Exception):
    pass


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


# What a client is told for EVERY InsufficientCreditsError. The exception's own
# text can carry the wallet's balance, the task's estimate, or an unsettled
# chargeback/refund deficit - internal billing state that belongs in server
# logs, never in an API error body or a polled task error.
INSUFFICIENT_CREDITS_MESSAGE = (
    "There aren't enough AI credits available for this. The credit wallet "
    "needs to be topped up before it can run."
)
