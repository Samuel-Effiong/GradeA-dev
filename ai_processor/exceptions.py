"""Coded failures raised by the AI pipeline (FR-A-06)."""

from audit.enums import ReasonCode
from AutoGrader.reason_codes import REASON_CODES, CodedError
from billing.refunds import charges_in_open_scope

REFUNDED = "The credits were refunded."
NOT_CHARGED = "No credits were charged."


class ProviderFailureError(CodedError):
    """#9 PROVIDER_FAILURE (503 + Retry-After, retryable): the grading or
    answer-extraction service could not finish the item after its retries.
    Raised `from` the last attempt's error, so the cause (a timeout, a rate
    limit, a 5xx, unusable output) stays on __cause__ for the logs and the
    classifier, while the user sees only the coded message."""

    reason_code = ReasonCode.PROVIDER_FAILURE
    status_code = REASON_CODES[ReasonCode.PROVIDER_FAILURE].http_status


def provider_failure(last_error, attempts):
    """The error to raise when every attempt failed. F1: the message says
    "The credits were refunded." when the run's refund scope holds a charge
    (it refunds it as this unwinds), otherwise "No credits were charged."
    (a failed call itself is never charged)."""
    return ProviderFailureError(
        params={"credit_clause": REFUNDED if charges_in_open_scope() else NOT_CHARGED},
        detail=f"All {attempts} attempts failed. Last error: {last_error!r}",
    )
