"""
The one place that decides which AI failures are PERMANENT refusals.

A refusal is the system's final answer about THIS request by THIS user: the
plan doesn't include the feature, or the wallet can't pay for it. Asking again
gives the same answer until the user or an admin changes something, so a
refusal is never retried (not by an in-process *_with_retry loop, not by a
Celery self.retry) and is never a server fault (never a 500).

Everything else - provider timeouts, dropped connections, rate limits,
provider 5xx, malformed model output, bugs - is TRANSIENT and keeps whatever
retry behaviour its call site already has. Nothing here widens or narrows
that; it only pulls refusals out.

Classification is by exception TYPE, never by digging through __cause__ or
message text: a layer that rewraps a refusal in a bare Exception is the bug,
and is fixed at that layer. See docs/evidence/REFUSAL_HANDLING_EVIDENCE.md.
"""

from AutoGrader.reason_codes import coded_response
from billing.access_control import AIFeatureNotAvailableError
from billing.errors import InsufficientCreditsError

PERMANENT_AI_REFUSALS = (AIFeatureNotAvailableError, InsufficientCreditsError)


def is_permanent_refusal(error):
    return isinstance(error, PERMANENT_AI_REFUSALS)


def refusal_response(error):
    """The HTTP answer to a refusal: its status and the coded error body
    (AutoGrader.reason_codes, FR-A-06), which keeps the client-safe message
    and the legacy lowercase `code` beside the new `reason_code`. Returns
    None for anything that isn't a refusal, so a caller can fall through to
    its own handling."""
    if not is_permanent_refusal(error):
        return None
    return coded_response(error)


def log_refusal(logger, what, error, **extra):
    """A refusal is an expected business outcome, not a fault: WARNING, no
    stack trace. The raw text (which may carry balance or deficit detail the
    client is never shown) stays server-side here."""
    logger.warning(
        "%s refused: %s: %s",
        what,
        type(error).__name__,
        error,
        extra=extra or None,
    )
