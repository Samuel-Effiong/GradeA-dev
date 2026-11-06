"""
billing/zero_credit.py
======================

The zero-credit assertion scope (FR-B-10, NFR-CRD-03).

Part 1 designates a set of operations as zero-credit: assignment copy, bulk
copy, library copy in both directions, lesson copy, lesson upload, feedback
editing, tag operations. Requirement 5.3 says these must be *provably*
zero-credit, "enforced in the metering layer and covered by an automated
test - not left to convention".

An operation proves it by running inside ``zero_credit_scope(<name>)``. While
a scope is open, anything that would spend or move credits, or reach a model
provider, raises ``ZeroCreditViolation`` instead of happening:

    MODEL_CALL         AIProcessor.execute_graded_task (entry, before any
                       provider call - the provider is called BEFORE the
                       charge is written, so a ledger-only guard would fire
                       after the provider had already been paid)
    WALLET_CONSUME     CreditWallet.consume_credits
    LEDGER_INSERT      CreditLedger row insert
    USAGE_LOG_INSERT   CreditUsageLog row insert
    BULK_INSERT        CreditLedger / CreditUsageLog bulk_create
    BUCKET_MUTATION    CreditBucket write (grant, spend, refund, reversal)

WHAT IT DOES NOT COVER
----------------------
* A ContextVar is not carried across a Celery hop. A zero-credit operation that
  runs in a task must enter the scope in the task body; ``@zero_credit(...)``
  is the decorator form for that.
* Raw SQL, ``QuerySet.update()`` on a credit table, and other processes. Same
  limits as billing/immutable.py.
* ``allow_unsafe_mutation()`` does NOT lift this guard: it concerns row
  immutability, a different question, and a data-repair session must not
  silently run inside a zero-credit scope.

ZeroCreditViolation is an ordinary ``Exception`` (not ``BaseException``) so
Celery and Django handle it normally and ``billing_refund_scope`` treats it as
a failed block. A broad ``except Exception`` in a caller can swallow it, so a
per-operation test asserts on the ledger and the provider call count at the
operation boundary, not only on "no exception escaped".
"""

import contextlib
import contextvars
import functools
import logging

logger = logging.getLogger(__name__)

# Innermost scope last. A ContextVar, not module state: the ai_processor
# singleton and this module are shared by every thread in the process
# (same reasoning as billing/refunds.py).
_active_scopes: contextvars.ContextVar = contextvars.ContextVar(
    "billing_zero_credit_scopes", default=()
)

# Every declared zero-credit operation. An epic that wraps an operation adds
# its name here (via register_zero_credit_operations) in the same change, so a
# typo cannot pass a test by opening an unregistered scope.
ZERO_CREDIT_OPERATIONS = {"_selftest"}

# Test-only hook: when a list, every scope entry appends its operation name.
# Set by billing.testing.zero_credit so the "runs inside a scope" assertion
# needs no patching of imported names. None in production.
_entry_recorder: list | None = None

# Stable codes for ZeroCreditViolation.attempted.
MODEL_CALL = "MODEL_CALL"
WALLET_CONSUME = "WALLET_CONSUME"
LEDGER_INSERT = "LEDGER_INSERT"
USAGE_LOG_INSERT = "USAGE_LOG_INSERT"
BULK_INSERT = "BULK_INSERT"
BUCKET_MUTATION = "BUCKET_MUTATION"


class ZeroCreditViolation(Exception):
    """A credit write or model call was attempted inside a zero-credit scope."""

    def __init__(self, operation, attempted):
        self.operation = operation
        self.attempted = attempted
        super().__init__(
            f"Zero-credit operation {operation!r} attempted {attempted}. "
            "Zero-credit operations must not spend credits or call a model."
        )


def register_zero_credit_operations(*names):
    """Declare zero-credit operation names. Idempotent."""
    for name in names:
        if not isinstance(name, str) or not name:
            raise ValueError("operation name must be a non-empty string")
        ZERO_CREDIT_OPERATIONS.add(name)


def in_zero_credit_scope():
    """Name of the innermost open scope, or None."""
    scopes = _active_scopes.get()
    return scopes[-1] if scopes else None


@contextlib.contextmanager
def zero_credit_scope(operation):
    """Forbid every credit write and model call inside this block."""
    if operation not in ZERO_CREDIT_OPERATIONS:
        raise ValueError(
            f"Unknown zero-credit operation {operation!r}. Register it with "
            "register_zero_credit_operations() first."
        )
    recorder = _entry_recorder
    if recorder is not None:
        recorder.append(operation)
    token = _active_scopes.set(_active_scopes.get() + (operation,))
    try:
        yield
    finally:
        _active_scopes.reset(token)


def zero_credit(operation):
    """Decorator form; enters the scope on every call. Use it as the first
    line of a Celery task body when the operation runs asynchronously."""

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with zero_credit_scope(operation):
                return fn(*args, **kwargs)

        return wrapper

    return decorate


def assert_not_zero_credit(attempted):
    """Called by the guard sites. Raises if a zero-credit scope is open."""
    operation = in_zero_credit_scope()
    if operation is None:
        return
    _report(operation, attempted)
    raise ZeroCreditViolation(operation, attempted)


def _report(operation, attempted):
    """Log and emit the anomaly metric. Never raises: a reporting problem
    must not mask the violation, and ids only, no student data (FR-A-04)."""
    request_id = None
    try:
        from AutoGrader.request_context import get_request_id

        request_id = get_request_id()
    except (
        Exception
    ):  # noqa: BLE001 - reporting must never mask the violation  # nosec B110
        pass
    logger.error(
        "Zero-credit violation: operation=%s attempted=%s request_id=%s",
        operation,
        attempted,
        request_id,
    )
    try:
        from audit import metrics as audit_metrics

        audit_metrics.count(
            "credit_ledger_anomaly",
            tags={"kind": "zero_credit_violation", "attempted": attempted},
        )
    except Exception:  # noqa: BLE001 - reporting must never mask the violation
        logger.debug("zero-credit metric emit failed", exc_info=True)


def guard_bucket_save(sender, instance, raw=False, update_fields=None, **kwargs):
    """pre_save receiver for CreditBucket. Connected in billing/models.py."""
    if raw:
        return
    if update_fields is not None and set(update_fields) <= {"updated_at"}:
        return
    assert_not_zero_credit(BUCKET_MUTATION)
