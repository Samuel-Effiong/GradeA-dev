"""
billing/license_stripe_mutation.py
==================================
Running one irreversible Stripe change to a licence without holding a
database transaction open across it (H-28).

THE PROBLEM THIS SOLVES
-----------------------
Django rolls back the database half of a failed operation; Stripe does not.
The licence operations used to make their Stripe call inside the same
transaction as their local write — and, for all of them, while holding a
row lock. Production and beta run `idle_in_transaction_session_timeout =
60s`, shorter than stripe-python's 80 s request timeout, so a slow Stripe
call could have its transaction terminated underneath it: Stripe changed,
the local write rolled back, and for a web request nothing ever retries.
See docs/evidence/h28_p1b/.

THE SHAPE
---------
Four phases, following the pattern stripe_service.reactivate_if_cancelling
already uses in-tree:

  A  a short transaction: lock the licence, validate, record an intent
     (PENDING). Committed — and the lock released — before Stripe is called.
  B  the Stripe call, in NO transaction, carrying the intent's
     idempotency key.
  C  a short transaction recording that Stripe applied it (STRIPE_APPLIED),
     committed on its own so that fact survives whatever happens next.
  D  a short transaction re-checking the licence and writing the local
     state; the intent becomes COMPLETE.

At most one intent per licence may be in flight (a database constraint), so
these operations are serialised the way the row lock serialised them —
without a transaction open while Stripe is slow.

Every transaction here is `durable=True`: Django raises if one is ever
opened inside another transaction. No caller does that today; this makes it
impossible to do by accident, because wrapping these operations in an outer
transaction would put the Stripe call straight back inside it.

A STRIPE EXCEPTION IS NOT PROOF OF FAILURE
------------------------------------------
A timeout or a 5xx means the request may have landed. Treating it as a
failure is how a change that Stripe applied gets left out of the database.
So those outcomes are READ BACK from Stripe before being classified; only a
refusal (the request was processed and turned down) counts as not applied.
"""

import contextvars
import functools
import logging
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, cast

from django.db import (
    IntegrityError,
    InterfaceError,
    OperationalError,
    connection,
    connections,
    transaction,
)
from django.utils import timezone

from .imports import stripe
from .models import (
    LicenseStripeMutationIntent,
    LicenseStripeMutationStatus,
    LicenseSubscription,
)

logger = logging.getLogger(__name__)

#: The Stripe work of one licence operation must end well inside gunicorn's
#: 100 s request timeout (Dockerfile; WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS),
#: leaving time for the local write, a compensation and the alert
#: (DESIGN_PROPOSAL.md §9i (2)). Per REQUEST, not per call: stripe-python's
#: own default (80 s, two retries) is ~240 s for ONE call, and an operation
#: makes up to four.
REQUEST_BUDGET_SECONDS = 75.0

_deadline: contextvars.ContextVar = contextvars.ContextVar(
    "h28_licence_stripe_deadline", default=None
)
_CALLER_IN_ATOMIC = "h28_caller_in_atomic_block"

GUARD_CONSTRAINT = "one_inflight_stripe_mutation_per_licence"

#: The local write after Stripe applied a change is retried on these alone
#: (DESIGN_PROPOSAL.md §9e): a dropped or terminated connection. Anything
#: else (a constraint, a re-validation) is a logic failure that a retry
#: would only delay.
TRANSIENT_DB_ERRORS = (OperationalError, InterfaceError)
FINALISE_ATTEMPTS = 3
#: Seconds to wait before the 2nd and 3rd attempts.
FINALISE_BACKOFF_SECONDS = (0.2, 1.0)

#: An intent still PENDING or STRIPE_APPLIED after this long was abandoned
#: mid-flight: its worker was killed (gunicorn's 100 s timeout, a deploy)
#: and ran no code, so no alert was sent (DESIGN_PROPOSAL.md §9i).
STALE_AFTER = timedelta(minutes=10)


class LicenceBillingChangeInProgress(ValueError):
    """Another Stripe change to this licence is still in flight, or is
    awaiting reconciliation. A ValueError, so the views' existing contract
    turns it into a 400 the caller can retry later."""


class LicenceStripeChangeNotRecorded(Exception):
    """Stripe applied the change but the application could not record it.

    Deliberately NOT a ValueError: this is the server's failure, not a bad
    request, so the views report it as a 500 — as they did before, when the
    same failure surfaced as a database error. Whether Stripe was put back
    (COMPENSATED) or a human is needed (ESCALATED) is on the intent."""


class StripeBudgetExhausted(stripe.error.APIConnectionError):
    """This request's Stripe time ran out. `started` says whether the call
    had been sent: if so, it may still land, so its outcome is unknown."""

    def __init__(self, message, *, started):
        super().__init__(message)
        self.started = started


class LicenceMovedOn(Exception):
    """The licence changed between recording the intent and writing the
    local result, so the result can no longer be applied as planned."""


@contextmanager
def stripe_budget(seconds=None):
    """Give the Stripe calls made inside a shared deadline. Nested budgets
    keep the earlier deadline, so a delegating call cannot extend it."""
    seconds = REQUEST_BUDGET_SECONDS if seconds is None else seconds
    new = time.monotonic() + seconds
    current = _deadline.get()
    token = _deadline.set(new if current is None else min(current, new))
    try:
        yield
    finally:
        _deadline.reset(token)


def with_stripe_budget(operation):
    """Run a licence operation under one request budget."""

    @functools.wraps(operation)
    def run(*args, **kwargs):
        with stripe_budget():
            return operation(*args, **kwargs)

    return run


def caller_in_atomic_block() -> bool:
    """Whether the code that asked for this Stripe call held a transaction
    open. A call run on a budget thread reports its caller's state,
    recorded when it was dispatched; the thread's own connection never has
    a transaction, so reading that instead would prove nothing."""
    return getattr(
        threading.current_thread(), _CALLER_IN_ATOMIC, connection.in_atomic_block
    )


#: Network retries per Stripe call, inside its socket bound. stripe-python
#: resends the same idempotency key on its own retries.
CALL_MAX_NETWORK_RETRIES = 1
#: Each attempt's socket timeout is the time left divided by the attempts,
#: kept within these limits.
MIN_CALL_TIMEOUT_SECONDS = 1.0
MAX_CALL_TIMEOUT_SECONDS = 30.0
#: stripe-python sleeps before a retry (0.5 s for the first, with jitter);
#: allowed for per retry, so the socket gives up before the budget does.
RETRY_SLEEP_ALLOWANCE_SECONDS = 1.0
#: Fraction of the time left the attempts may use; the rest is margin, so
#: the socket bound, not the outer wait, is what ends a slow call.
CALL_SHARE_OF_REMAINING = 0.9
#: Tests point the client at a local server; None means Stripe.
_BASE_ADDRESSES = None


def call_timeout_seconds() -> float:
    """
    The socket timeout for one attempt of the next Stripe call: what is left
    of the request's budget, less the retry sleeps and a margin, shared
    between the call's attempts, so that the attempts and their sleeps end
    before the budget does. With no budget set, the maximum.
    """
    deadline = _deadline.get()
    if deadline is None:
        return MAX_CALL_TIMEOUT_SECONDS
    usable = (
        deadline
        - time.monotonic()
        - RETRY_SLEEP_ALLOWANCE_SECONDS * CALL_MAX_NETWORK_RETRIES
    ) * CALL_SHARE_OF_REMAINING
    share = usable / (CALL_MAX_NETWORK_RETRIES + 1)
    return max(MIN_CALL_TIMEOUT_SECONDS, min(MAX_CALL_TIMEOUT_SECONDS, share))


def _client(timeout: float):
    kwargs: dict = {
        # The app's key and Stripe API version, so licence calls never
        # drift to a different version from the rest of the app.
        "stripe_version": stripe.api_version,
        "http_client": stripe.RequestsClient(timeout=timeout),
        "max_network_retries": CALL_MAX_NETWORK_RETRIES,
    }
    if _BASE_ADDRESSES is not None:
        kwargs["base_addresses"] = _BASE_ADDRESSES
    return stripe.StripeClient(cast(str, stripe.api_key), **kwargs)


def _options(idempotency_key):
    return {"idempotency_key": idempotency_key} if idempotency_key else None


class LicenceStripe:
    """
    The licence flows' only route to Stripe (H-28).

    stripe-python 14's legacy resource API (stripe.Subscription.modify and
    the like) has no per-call timeout: its HTTP client is process-wide, so
    bounding it would change every Stripe call in the app. Here each call
    builds its own StripeClient whose socket timeout is sized from the time
    left in the request's budget (call_timeout_seconds), as
    billing/receipts.py does for receipt lookups. A slow Stripe fails the
    call at the socket; nothing is left running past it.
    """

    @staticmethod
    def retrieve_subscription(sub_id):
        return _client(call_timeout_seconds()).v1.subscriptions.retrieve(sub_id)

    @staticmethod
    def modify_subscription(sub_id, idempotency_key=None, **params):
        return _client(call_timeout_seconds()).v1.subscriptions.update(
            sub_id, params=cast(Any, params), options=_options(idempotency_key)
        )

    @staticmethod
    def delete_subscription(sub_id, idempotency_key=None):
        # DELETE /v1/subscriptions/{id}: the legacy Subscription.delete.
        return _client(call_timeout_seconds()).v1.subscriptions.cancel(
            sub_id, options=_options(idempotency_key)
        )

    @staticmethod
    def retrieve_invoice(invoice_id, expand=None):
        params = {"expand": expand} if expand else None
        return _client(call_timeout_seconds()).v1.invoices.retrieve(
            invoice_id, params=cast(Any, params)
        )

    @staticmethod
    def void_invoice(invoice_id, idempotency_key=None):
        return _client(call_timeout_seconds()).v1.invoices.void_invoice(
            invoice_id, options=_options(idempotency_key)
        )

    @staticmethod
    def create_price(idempotency_key=None, **params):
        return _client(call_timeout_seconds()).v1.prices.create(
            params=cast(Any, params), options=_options(idempotency_key)
        )


def call_stripe(fn, *args, **kwargs):
    """
    Make one Stripe call within the current request's budget.

    With no budget set it simply calls `fn`. With one, the call runs on a
    short-lived daemon thread and is waited for only as long as the budget
    allows. The call itself is bounded at the socket by LicenceStripe
    (each attempt's timeout is the time left shared between the attempts),
    so it normally fails there, inside the budget, and the thread ends with
    it. This wait is the outer bound only, for a reply that trickles in
    slower than the per-read timeout; a thread it abandons still ends at
    its own socket timeout, so abandoned threads cannot pile up.

    An abandoned call may still land at Stripe. So StripeBudgetExhausted
    says whether it had started, and callers never conclude that a started
    call did not happen.
    """
    deadline = _deadline.get()
    if deadline is None:
        return fn(*args, **kwargs)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise StripeBudgetExhausted(
            "This request's Stripe time budget ran out before the call was made.",
            started=False,
        )

    outcome = {}

    def run():
        try:
            outcome["result"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            outcome["error"] = exc
        finally:
            # The worker only calls Stripe. Should anything on it ever touch
            # the database, its thread's connection is closed here, so no
            # connection leaks against Postgres's limit.
            connections.close_all()

    # Run in a copy of the caller's context: a new thread does not inherit
    # contextvars, and LicenceStripe sizes its socket timeout from the
    # deadline (without this it saw no budget and used the maximum).
    context = contextvars.copy_context()
    worker = threading.Thread(
        target=context.run, args=(run,), name="h28-stripe-call", daemon=True
    )
    setattr(worker, _CALLER_IN_ATOMIC, connection.in_atomic_block)
    worker.start()
    worker.join(remaining)
    if worker.is_alive():
        raise StripeBudgetExhausted(
            "No reply from Stripe within this request's time budget "
            f"({REQUEST_BUDGET_SECONDS:.0f} s); the call may still land.",
            started=True,
        )
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]


def outcome_unknown(exc: Exception) -> bool:
    """True when a Stripe error does not tell us whether the request landed:
    a connection failure (including a timeout) or a server error. Everything
    else means Stripe processed the request and refused it."""
    if isinstance(exc, stripe.error.APIConnectionError):
        return True
    if isinstance(exc, stripe.error.APIError):
        status = getattr(exc, "http_status", None)
        return status is None or status >= 500
    return False


def is_guard_violation(exc: IntegrityError) -> bool:
    return GUARD_CONSTRAINT in str(exc)


#: Appended to every fixed message a licence route shows when Stripe fails.
TRY_AGAIN = " Please try again; if it keeps failing, contact support."


def log_provider_error(intent, exc: Exception) -> None:
    """H-60: a Stripe failure behind a licence change, for the server log.

    The client is shown fixed text instead of Stripe's message: that text
    names provider internals (request and object ids, parameter names) and
    is QA-ERR-03's "raw library text". Stripe's full message is already on
    the intent's failure_reason, for the reconciliation screens. This line
    carries ids only."""
    logger.warning(
        "Licence %s, intent %s: Stripe failed (%s, code=%s, request=%s)",
        intent.license_subscription_id,
        intent.id,
        type(exc).__name__,
        getattr(exc, "code", None),
        getattr(exc, "request_id", None),
    )


def busy_error() -> LicenceBillingChangeInProgress:
    return LicenceBillingChangeInProgress(
        "Another billing change for this licence is still in progress or "
        "awaiting reconciliation. Please try again shortly."
    )


def record_intent(
    licence: LicenseSubscription,
    operation: str,
    requested_change: dict,
    performed_by=None,
) -> LicenseStripeMutationIntent:
    """Phase A's write. Call inside the phase-A transaction, after
    validating; the caller turns a guard IntegrityError into busy_error()."""
    sub_id = licence.stripe_subscription_id
    if not sub_id:
        # Callers handle a licence with no Stripe subscription locally,
        # before recording anything; an intent must name what it changes.
        raise ValueError(f"Licence {licence.id} has no Stripe subscription to change.")
    return LicenseStripeMutationIntent.objects.create(
        license_subscription=licence,
        operation=operation,
        stripe_subscription_id=sub_id,
        requested_change=requested_change,
        performed_by=performed_by,
    )


def _set_status(intent, status, **fields) -> bool:
    """Advance an intent in its own short transaction. Best effort: if the
    database itself is what failed, the intent keeps its last committed
    state, which the stale-intent check reports. Returns whether it saved."""
    intent.status = status
    for name, value in fields.items():
        setattr(intent, name, value)
    try:
        with transaction.atomic(durable=True):
            intent.save(update_fields=["status", "updated_at", *fields])
        return True
    except Exception:  # noqa: BLE001 - a failed record must not mask the cause
        logger.exception(
            "Could not record H-28 intent %s as %s; it keeps its last "
            "committed state for the stale-intent check to report.",
            intent.id,
            status,
        )
        return False


def stripe_id(value):
    """The id of a Stripe reference that may be an id or an expanded object."""
    if value is None or isinstance(value, str):
        return value
    return value.get("id")


def new_invoice_since(sub_id: str, invoice_before):
    """The invoice a change raised: the subscription's latest invoice, if it
    is not the one that was latest before the change. Never an older one,
    so an unpaid renewal is never mistaken for the change's own invoice.
    Reads Stripe; a StripeError propagates."""
    latest = stripe_id(
        call_stripe(LicenceStripe.retrieve_subscription, sub_id).get("latest_invoice")
    )
    return latest if latest and latest != invoice_before else None


def record_stripe_result(intent, **values) -> None:
    """Keep what Stripe returned that a human reconciling this intent would
    need (a created Price, an invoice), in its own short transaction. Best
    effort, like every intent write."""
    intent.stripe_result = {**(intent.stripe_result or {}), **values}
    try:
        with transaction.atomic(durable=True):
            intent.save(update_fields=["stripe_result", "updated_at"])
    except Exception:  # noqa: BLE001 - a failed record must not mask the cause
        logger.exception(
            "Could not record Stripe's result %r on H-28 intent %s.", values, intent.id
        )


def _reconciliation_needed(intent, why: str) -> None:
    """Tell a human, through both channels of DESIGN_PROPOSAL.md §9g: an
    ERROR log (a Sentry event where Sentry is set up) and an email to every
    active super admin."""
    logger.error(
        "MANUAL RECONCILIATION NEEDED — Stripe and local state may now "
        "disagree for licence %s (intent %s, %s on Stripe subscription %s): %s",
        intent.license_subscription_id,
        intent.id,
        intent.operation,
        intent.stripe_subscription_id,
        why,
    )
    _email_super_admins(intent, why)


def _email_super_admins(intent, why: str) -> None:
    """
    Best effort, and never raises: the caller is already handling a
    failure, and a failed alert must not mask it. Money-related, so not
    gated on any notification preference, like
    LicenseSubscriptionService._notify_super_admins_offline_overage_pending.
    """
    try:
        from django.conf import settings

        from AutoGrader.dispatch import safe_delay
        from AutoGrader.tasks import send_email_task
        from users.models import CustomUser, UserTypes

        recipients = list(
            CustomUser.objects.filter(
                user_type=UserTypes.SUPER_ADMIN,
                is_superuser=True,
                is_active=True,
                email__isnull=False,
            )
            .exclude(email="")
            .values_list("email", flat=True)
        )
        message = (
            "A billing change to a school licence may have left Stripe and "
            "the application disagreeing, and needs a human.\n\n"
            f"Licence: {intent.license_subscription_id}\n"
            f"Intent: {intent.id} ({intent.operation}, now {intent.status})\n"
            f"Stripe subscription: {intent.stripe_subscription_id}\n"
            f"Why: {why}\n\n"
            "Reconcile Stripe and the licence, then close the intent with:\n"
            f"  python manage.py resolve_licence_stripe_intent {intent.id}\n"
            "(a dry run; it explains how to apply). Until then, no further "
            "Stripe change can be made to this licence."
        )
        for email in recipients:
            try:
                safe_delay(
                    send_email_task,
                    subject="Manual reconciliation needed: licence billing change",
                    message=message,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[email],
                )
            except Exception:  # noqa: BLE001 - one address must not stop the rest
                logger.exception(
                    "Could not queue the reconciliation alert for H-28 intent %s "
                    "to one super admin.",
                    intent.id,
                )
    except Exception:  # noqa: BLE001 - an alert must never raise
        logger.exception(
            "Could not send the reconciliation alert emails for H-28 intent %s.",
            intent.id,
        )


def escalate_stale_intents(now=None) -> int:
    """
    The stale-intent check (DESIGN_PROPOSAL.md §9i (1)): one query, no
    Stripe call. Every intent still PENDING or STRIPE_APPLIED after
    STALE_AFTER was abandoned by a worker that ran no code, so nobody was
    told. Each becomes ESCALATED and a human is alerted, once: ESCALATED is
    not picked up again. Returns how many were escalated.
    """
    now = now or timezone.now()
    stale = list(
        LicenseStripeMutationIntent.objects.filter(
            status__in=[
                LicenseStripeMutationStatus.PENDING,
                LicenseStripeMutationStatus.STRIPE_APPLIED,
            ],
            updated_at__lt=now - STALE_AFTER,
        )
    )
    escalated = 0
    for intent in stale:
        was = intent.status
        why = (
            "left PENDING: the outcome at Stripe is unknown; check Stripe"
            if was == LicenseStripeMutationStatus.PENDING
            else "left STRIPE_APPLIED: Stripe applied the change, the "
            "application never recorded it"
        )
        # Conditional, so a flow that finishes meanwhile is never overwritten.
        claimed = LicenseStripeMutationIntent.objects.filter(
            pk=intent.pk, status=was, updated_at=intent.updated_at
        ).update(
            status=LicenseStripeMutationStatus.ESCALATED,
            escalated_at=now,
            failure_reason=f"Stale for over {STALE_AFTER}: {why}",
            updated_at=now,
        )
        if claimed:
            intent.status = LicenseStripeMutationStatus.ESCALATED
            escalated += 1
            _reconciliation_needed(intent, f"stale intent, {why}")
    return escalated


def abandon(intent, why: str) -> None:
    """Close an intent whose Stripe change was never attempted (a read that
    had to come first failed, or showed the change cannot be made). Stripe
    is untouched, so FAILED is the truth, and the licence is free again."""
    _set_status(
        intent,
        LicenseStripeMutationStatus.FAILED,
        failure_reason=f"Not attempted: {why}",
    )


def escalate(intent, why: str) -> None:
    """Give up: a human must reconcile Stripe and the application. The
    intent stays in flight (ESCALATED), so it keeps the licence's guard
    closed until someone resolves it."""
    _set_status(
        intent,
        LicenseStripeMutationStatus.ESCALATED,
        escalated_at=timezone.now(),
        failure_reason=why,
    )
    _reconciliation_needed(intent, why)


def apply_at_stripe(intent, call, reached, payment_errors=(), read_back_on=()):
    """
    Phase B, then phase C.

    `call(idempotency_key=...)` makes the Stripe mutation. `reached()` reads
    Stripe (read-only) and says whether the target state is in place; it is
    consulted only when the outcome of `call` is unknown.

    Returns what `call` returned — or None when the call's response was lost
    but the read-back shows it applied. Re-raises the Stripe error when the
    change did not happen (the intent is then FAILED) or when it cannot be
    determined (the intent stays PENDING and a human is told).

    `payment_errors` are errors that do not prove the change was refused:
    Stripe applies an item change in the same call that attempts payment,
    so after a CardError the change may be live (DESIGN_PROPOSAL.md §9j).
    They are re-raised with the intent left PENDING, for the caller to
    settle with undo_unpaid_change.

    `read_back_on` are further errors to treat as an unknown outcome and
    read back rather than take as a refusal. A delete retried after its
    response was lost can be refused because the object is already gone,
    which makes a delete that happened look like one that did not (§9j).
    """
    try:
        result = call_stripe(call, idempotency_key=intent.idempotency_key("apply"))
    except payment_errors:
        raise
    except StripeBudgetExhausted as exc:
        if exc.started:
            # Still in flight at Stripe: a read-back now could miss it and
            # call a change that later lands "not applied". Leave it
            # PENDING, loudly; the stale-intent check follows it up.
            _reconciliation_needed(
                intent, f"{exc} The intent is left PENDING: check Stripe."
            )
        else:
            _set_status(
                intent,
                LicenseStripeMutationStatus.FAILED,
                failure_reason=f"Not attempted: {exc}",
            )
        raise
    except stripe.error.StripeError as exc:
        if not (outcome_unknown(exc) or isinstance(exc, read_back_on)):
            _set_status(
                intent,
                LicenseStripeMutationStatus.FAILED,
                failure_reason=f"Stripe refused: {exc}",
            )
            raise
        try:
            applied = call_stripe(reached)
        except stripe.error.StripeError as read_exc:
            _reconciliation_needed(
                intent,
                f"outcome unknown ({exc}) and the read-back also failed "
                f"({read_exc}); the intent is left PENDING",
            )
            raise exc from read_exc
        if not applied:
            _set_status(
                intent,
                LicenseStripeMutationStatus.FAILED,
                failure_reason=f"Outcome unknown ({exc}); read-back shows not applied",
            )
            raise
        logger.warning(
            "H-28 intent %s: Stripe response lost (%s) but the read-back shows "
            "the change applied; continuing.",
            intent.id,
            exc,
        )
        result = None

    _set_status(intent, LicenseStripeMutationStatus.STRIPE_APPLIED)
    return result


def undo_unpaid_change(intent, revert, find_invoice, why: str) -> bool:
    """
    The payment-failure branch (DESIGN_PROPOSAL.md §9d, F1-F5): Stripe may
    have applied the change but collected nothing. Put Stripe back with
    `revert(idempotency_key=...)`, then void the change's own invoice if it
    is still open, so nothing is left for Stripe to collect later.
    `find_invoice()` returns that invoice's id, or None when the change
    raised none; it must never return an invoice the change did not create.

    Both done: the two sides stand where they began, and the intent is
    FAILED. Returns True. Either step failed: ESCALATED, a human is told,
    and it returns False.
    """
    problems = []
    try:
        call_stripe(revert, idempotency_key=intent.idempotency_key("revert"))
    except stripe.error.StripeError as exc:
        problems.append(f"the revert failed: {exc}")
    try:
        invoice_id = find_invoice()
        if invoice_id:
            invoice = call_stripe(LicenceStripe.retrieve_invoice, invoice_id)
            if invoice.get("status") == "open":
                call_stripe(
                    LicenceStripe.void_invoice,
                    invoice_id,
                    idempotency_key=intent.idempotency_key("void"),
                )
    except stripe.error.StripeError as exc:
        problems.append(f"voiding the change's invoice failed: {exc}")

    if problems:
        escalate(intent, f"Payment not collected ({why}); " + "; ".join(problems))
        return False
    _set_status(
        intent,
        LicenseStripeMutationStatus.FAILED,
        failure_reason=f"Payment not collected ({why}); reverted at Stripe",
    )
    return True


def finalise(intent, revalidate, write, compensate=None):
    """
    Phase D: re-lock the licence, re-check it, write the local result and
    mark the intent COMPLETE — all in one short transaction.

    A transient database failure (TRANSIENT_DB_ERRORS: the connection was
    dropped, or Postgres terminated it) is retried a bounded number of
    times (§9e). Before each retry the dead connection is closed, so the
    next attempt runs on a fresh one, in a new top-level transaction, never
    nested in the one that failed. Each attempt re-reads, re-validates and
    writes absolute values, so a retry cannot apply a change twice; and if
    an earlier attempt's commit did land (its acknowledgement lost with the
    connection), the intent is already COMPLETE and nothing is written
    again.

    If it still fails, the Stripe change is undone by `compensate(
    idempotency_key=...)` where one is given — only for changes where no
    money has moved — and the intent becomes COMPENSATED. With no
    compensation, or if it fails, the intent becomes ESCALATED and a human
    is told: once money has moved or an object was destroyed, the code never
    moves more money to put things back.

    Raises LicenceStripeChangeNotRecorded in either failure case.
    """
    try:
        return _finalise_with_retry(intent, revalidate, write)
    except Exception as exc:  # noqa: BLE001 - every failure takes this path
        cause = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, TRANSIENT_DB_ERRORS):
            # The intent's own status is written next; do not write it on
            # the connection that just failed.
            connection.close()

        if compensate is not None:
            try:
                call_stripe(
                    compensate, idempotency_key=intent.idempotency_key("revert")
                )
            except stripe.error.StripeError as revert_exc:
                cause = f"{cause}; the revert at Stripe also failed: {revert_exc}"
            else:
                _set_status(
                    intent,
                    LicenseStripeMutationStatus.COMPENSATED,
                    failure_reason=f"Local write failed ({cause}); reverted at Stripe",
                )
                logger.warning(
                    "H-28 intent %s: the local write failed (%s), so the "
                    "Stripe change was reverted. Nothing was changed.",
                    intent.id,
                    cause,
                )
                raise LicenceStripeChangeNotRecorded(
                    "The change could not be recorded, so it was undone. "
                    "Nothing was changed; please try again."
                ) from exc

        escalate(intent, cause)
        raise LicenceStripeChangeNotRecorded(
            "The change was applied at our payment provider but could not be "
            "recorded. It has been flagged for manual reconciliation."
        ) from exc


def _finalise_with_retry(intent, revalidate, write):
    for attempt in range(1, FINALISE_ATTEMPTS + 1):
        try:
            with transaction.atomic(durable=True):
                licence = LicenseSubscription.objects.select_for_update().get(
                    pk=intent.license_subscription_id
                )
                if (
                    attempt > 1
                    and LicenseStripeMutationIntent.objects.filter(
                        pk=intent.pk, status=LicenseStripeMutationStatus.COMPLETE
                    ).exists()
                ):
                    # An earlier attempt committed; only its reply was lost.
                    intent.status = LicenseStripeMutationStatus.COMPLETE
                    return licence
                revalidate(licence)
                write(licence)
                intent.status = LicenseStripeMutationStatus.COMPLETE
                intent.completed_at = timezone.now()
                intent.save(update_fields=["status", "completed_at", "updated_at"])
            return licence
        except TRANSIENT_DB_ERRORS as exc:
            if attempt == FINALISE_ATTEMPTS:
                raise
            logger.warning(
                "H-28 intent %s: the local write failed on attempt %d of %d "
                "(%s: %s); retrying on a fresh connection.",
                intent.id,
                attempt,
                FINALISE_ATTEMPTS,
                type(exc).__name__,
                exc,
            )
            connection.close()
            time.sleep(FINALISE_BACKOFF_SECONDS[attempt - 1])
    raise AssertionError("unreachable: the last attempt returns or raises")
