"""Alertable metrics for Epic A §9 / BE-A-09 ("Emit alertable metrics for
grading failure rate, model fallback rate, credit ledger anomalies, and the
rate of each reason code from BE-A-06. A spike in one reason code is the
earliest signal of a regression that users are experiencing but not
reporting.").

Built on Sentry's own metrics API (`sentry_sdk.metrics`), already wired into
this app in prod (AutoGrader/settings.py) - no new dependency, no new
service. This module only EMITS the named+tagged signals below; it does not
configure Sentry's Alert/Monitor rules. Whoever wires those up in the Sentry
UI should read the thresholds from this table, not re-derive them:

    grading_failure_rate        >5% over a rolling 15 min window
    model_fallback_rate         >10% over a rolling 15 min window
    credit_ledger_anomaly       any occurrence - page immediately (P0)
    reason_code_rate{code=...}  any single code >3x its own 7-day
                                 trailing average
    audit_emit_failures_total   >0 sustained for 5 min

`grading_failure_rate` and `model_fallback_rate` are emitted as
distributions of 0.0/1.0 per event (not pre-aggregated), so Sentry's own
`avg()` over a window IS the rate - the standard technique for a
rate-over-window alert on this API.

Degrades to a safe no-op wherever Sentry isn't installed or isn't live
(local/test envs, or a deploy that hasn't installed sentry-sdk yet) -
mirrors AutoGrader/settings.py's own guarded import for the same reason.
Never raises into a caller: a metrics failure must not be allowed to fail
the grading run, the audit write, or anything else it only describes.
"""

import logging

logger = logging.getLogger(__name__)

try:
    import sentry_sdk
except ImportError:  # pragma: no cover - depends on deploy state
    sentry_sdk = None


def _live() -> bool:
    if sentry_sdk is None:
        return False
    try:
        return sentry_sdk.is_initialized()
    except Exception:  # noqa: BLE001 - metrics must never break the caller
        return False


def count(name, value=1, *, tags=None):
    """Emit a counter metric. No-op when Sentry isn't live."""
    if not _live():
        return
    try:
        sentry_sdk.metrics.count(name, value, attributes=tags or {})
    except Exception:  # noqa: BLE001 - metrics must never break the caller
        logger.debug("metrics count failed: name=%s", name, exc_info=True)


def distribution(name, value, *, tags=None):
    """Emit a distribution metric (e.g. a 0.0/1.0 sample of a rate). No-op
    when Sentry isn't live."""
    if not _live():
        return
    try:
        sentry_sdk.metrics.distribution(name, value, attributes=tags or {})
    except Exception:  # noqa: BLE001 - metrics must never break the caller
        logger.debug("metrics distribution failed: name=%s", name, exc_info=True)
