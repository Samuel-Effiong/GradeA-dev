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

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from .imports import stripe
from .models import (
    LicenseStripeMutationIntent,
    LicenseStripeMutationStatus,
    LicenseSubscription,
)

logger = logging.getLogger(__name__)

GUARD_CONSTRAINT = "one_inflight_stripe_mutation_per_licence"


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


class LicenceMovedOn(Exception):
    """The licence changed between recording the intent and writing the
    local result, so the result can no longer be applied as planned."""


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
    latest = stripe_id(stripe.Subscription.retrieve(sub_id).get("latest_invoice"))
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
    logger.error(
        "MANUAL RECONCILIATION NEEDED — Stripe and local state may now "
        "disagree for licence %s (intent %s, %s on Stripe subscription %s): %s",
        intent.license_subscription_id,
        intent.id,
        intent.operation,
        intent.stripe_subscription_id,
        why,
    )


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
        result = call(idempotency_key=intent.idempotency_key("apply"))
    except payment_errors:
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
            applied = reached()
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
        revert(idempotency_key=intent.idempotency_key("revert"))
    except stripe.error.StripeError as exc:
        problems.append(f"the revert failed: {exc}")
    try:
        invoice_id = find_invoice()
        if invoice_id:
            invoice = stripe.Invoice.retrieve(invoice_id)
            if invoice.get("status") == "open":
                stripe.Invoice.void_invoice(
                    invoice_id, idempotency_key=intent.idempotency_key("void")
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

    If that fails, the Stripe change is undone by `compensate(
    idempotency_key=...)` where one is given — only for changes where no
    money has moved — and the intent becomes COMPENSATED. With no
    compensation, or if it fails, the intent becomes ESCALATED and a human
    is told: once money has moved or an object was destroyed, the code never
    moves more money to put things back.

    Raises LicenceStripeChangeNotRecorded in either failure case.
    """
    try:
        with transaction.atomic(durable=True):
            licence = LicenseSubscription.objects.select_for_update().get(
                pk=intent.license_subscription_id
            )
            revalidate(licence)
            write(licence)
            intent.status = LicenseStripeMutationStatus.COMPLETE
            intent.completed_at = timezone.now()
            intent.save(update_fields=["status", "completed_at", "updated_at"])
        return licence
    except Exception as exc:  # noqa: BLE001 - every failure takes this path
        cause = f"{type(exc).__name__}: {exc}"

        if compensate is not None:
            try:
                compensate(idempotency_key=intent.idempotency_key("revert"))
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
