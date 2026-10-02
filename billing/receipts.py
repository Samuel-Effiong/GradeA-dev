"""
billing/receipts.py
===================
Stripe receipt links for BillingTransaction rows, resolved AFTER the
transaction that recorded the purchase has committed. Never inside it.

WHY THE LINK IS NOT RESOLVED INLINE
-----------------------------------
Every checkout.session.completed flow used to call Stripe for the receipt
link inside the webhook's @transaction.atomic block, after it had written
the paid grant or activation and while it still held that work's row
locks. Production Postgres runs with idle_in_transaction_session_timeout =
60s (docs/ops/postgres-guard-rails.md), which is shorter than
stripe-python's default 80s request timeout. One slow Stripe response was
enough for Postgres to terminate the session and roll back a grant the
customer had already paid for. The webhook endpoint had already answered
Stripe 200 (dispatch is asynchronous), so Stripe never redelivered it, and
the customer received nothing until someone replayed the event by hand.
Until then the wallet (or, for a school overage purchase, every
allocation on the purchase) stayed locked and credit consumption failed.

A receipt link is a convenience. A grant is money. So the link is now:

  1. recorded as missing when the purchase is recorded;
  2. filled by `fill_billing_transaction_receipt_url` (a Celery task)
     queued with transaction.on_commit, so it cannot start until the grant
     is durable;
  3. filled by `sweep_missing_receipt_urls` (hourly celery-beat) for
     anything step 2 missed: broker down at commit time, worker lost, or
     Stripe unavailable when the task ran.

Every fill is a conditional UPDATE ... WHERE receipt_url IS NULL, so
duplicate tasks, overlapping sweeps and webhook redeliveries can race
freely: the first writer wins and nobody overwrites a link.

The lookup itself uses a dedicated Stripe client with a short timeout and
no automatic retries. Nothing waits on it, and the sweep is the retry.
"""

import logging
from datetime import timedelta
from enum import Enum
from time import monotonic
from typing import cast

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .imports import stripe
from .models import BillingTransaction

logger = logging.getLogger(__name__)

#: Per-request timeout for receipt lookups. Nothing waits on the answer.
RECEIPT_LOOKUP_TIMEOUT_SECONDS = 10

#: Rows younger than this are left to their own on_commit task, so a sweep
#: does not duplicate a lookup that is about to happen anyway.
RECEIPT_SWEEP_MIN_AGE = timedelta(minutes=5)

#: The sweep only looks this far back. Older rows missing a link are left
#: to `manage.py backfill_receipt_urls`, which has no age limit. Without
#: this bound, a purchase Stripe genuinely has no receipt for would be
#: looked up again on every sweep, forever.
RECEIPT_SWEEP_WINDOW = timedelta(days=3)

#: Upper bounds per sweep run, so a Stripe outage cannot make one run
#: overlap the next hour's.
RECEIPT_SWEEP_BATCH_SIZE = 200
RECEIPT_SWEEP_TIME_BUDGET_SECONDS = 10 * 60

_MISSING_RECEIPT = Q(receipt_url__isnull=True) | Q(receipt_url="")
_HAS_STRIPE_REFERENCE = (
    Q(stripe_invoice_id__isnull=False)
    | Q(stripe_charge_id__isnull=False)
    | Q(stripe_payment_intent_id__isnull=False)
)


class FillOutcome(str, Enum):
    FILLED = "filled"
    ALREADY_SET = "already_set"
    NO_STRIPE_REFERENCE = "no_stripe_reference"
    UNRESOLVED = "unresolved"
    MISSING = "missing"


def _receipt_stripe_client():
    # stripe.api_key is typed Optional; it is set at startup, and a missing
    # key fails the lookup the same way it always did (cast, not a check).
    return stripe.StripeClient(
        cast(str, stripe.api_key),
        http_client=stripe.RequestsClient(timeout=RECEIPT_LOOKUP_TIMEOUT_SECONDS),
        max_network_retries=0,
    )


def lookup_receipt_url(*, invoice_id=None, charge_id=None, payment_intent_id=None):
    """
    The Stripe-hosted receipt/invoice link for a purchase, in priority order
    invoice -> charge -> payment_intent (the same specificity order as
    BillingTransactionService._resolve_lookup).

    Returns None when Stripe has no link or cannot be reached, and on a
    malformed response. Never raises.

    Makes a network call, so it must never run inside a database
    transaction. See the module docstring.
    """
    try:
        client = _receipt_stripe_client()
        if invoice_id:
            return client.v1.invoices.retrieve(invoice_id).get("hosted_invoice_url")
        if charge_id:
            return client.v1.charges.retrieve(charge_id).get("receipt_url")
        if payment_intent_id:
            intent = client.v1.payment_intents.retrieve(
                payment_intent_id, params={"expand": ["latest_charge"]}
            )
            latest_charge = intent.get("latest_charge")
            if latest_charge is None:
                return None
            if isinstance(latest_charge, str):
                # `expand` was not honoured: an id instead of an object.
                logger.error(
                    "Receipt lookup for PaymentIntent %s got an unexpanded "
                    "latest_charge (%s); leaving the receipt link empty.",
                    payment_intent_id,
                    latest_charge,
                )
                return None
            return latest_charge.get("receipt_url")
    except stripe.StripeError as exc:
        logger.warning(
            "Receipt lookup failed (invoice=%s, charge=%s, payment_intent=%s): %s",
            invoice_id,
            charge_id,
            payment_intent_id,
            exc,
        )
    except Exception:  # noqa: BLE001 - a receipt link must never break a caller
        logger.exception(
            "Unexpected error in receipt lookup (invoice=%s, charge=%s, "
            "payment_intent=%s); leaving the receipt link empty.",
            invoice_id,
            charge_id,
            payment_intent_id,
        )
    return None


def fill_receipt_url(transaction_id):
    """
    Fill one BillingTransaction's receipt_url if it is still missing.

    Safe to run any number of times, concurrently: the write is a
    conditional UPDATE that only matches while the link is still empty.
    """
    row = (
        BillingTransaction.objects.filter(pk=transaction_id)
        .values(
            "receipt_url",
            "stripe_invoice_id",
            "stripe_charge_id",
            "stripe_payment_intent_id",
        )
        .first()
    )
    if row is None:
        return FillOutcome.MISSING
    if row["receipt_url"]:
        return FillOutcome.ALREADY_SET
    if not (
        row["stripe_invoice_id"]
        or row["stripe_charge_id"]
        or row["stripe_payment_intent_id"]
    ):
        return FillOutcome.NO_STRIPE_REFERENCE

    receipt_url = lookup_receipt_url(
        invoice_id=row["stripe_invoice_id"],
        charge_id=row["stripe_charge_id"],
        payment_intent_id=row["stripe_payment_intent_id"],
    )
    if not receipt_url:
        return FillOutcome.UNRESOLVED

    updated = (
        BillingTransaction.objects.filter(pk=transaction_id)
        .filter(_MISSING_RECEIPT)
        .update(receipt_url=receipt_url, updated_at=timezone.now())
    )
    return FillOutcome.FILLED if updated else FillOutcome.ALREADY_SET


def schedule_receipt_url_fill(billing_transaction):
    """
    Queue a receipt-link fill for a just-recorded BillingTransaction, to run
    only once the surrounding transaction has committed.

    Call this instead of resolving the link inline. If the transaction rolls
    back, nothing is queued. If queueing fails after commit (broker down),
    the error is logged and swallowed, so the committed purchase is never
    reported as failed, and the hourly sweep fills the link later.
    """
    if billing_transaction.receipt_url:
        return
    if not (
        billing_transaction.stripe_invoice_id
        or billing_transaction.stripe_charge_id
        or billing_transaction.stripe_payment_intent_id
    ):
        return

    transaction_id = str(billing_transaction.pk)

    def enqueue():
        from AutoGrader.dispatch import safe_delay

        from .tasks import fill_billing_transaction_receipt_url

        safe_delay(fill_billing_transaction_receipt_url, transaction_id)

    transaction.on_commit(enqueue, robust=True)


def sweep_missing_receipt_urls(*, now=None):
    """
    Fill receipt links that the on_commit task never filled. Bounded by
    RECEIPT_SWEEP_WINDOW, RECEIPT_SWEEP_BATCH_SIZE and
    RECEIPT_SWEEP_TIME_BUDGET_SECONDS. Newest rows first, so a backlog of
    rows Stripe has no receipt for cannot starve fresh purchases.
    """
    now = now or timezone.now()
    started = monotonic()

    candidate_ids = list(
        BillingTransaction.objects.filter(_MISSING_RECEIPT)
        .filter(_HAS_STRIPE_REFERENCE)
        .filter(
            occurred_at__gte=now - RECEIPT_SWEEP_WINDOW,
            occurred_at__lte=now - RECEIPT_SWEEP_MIN_AGE,
        )
        .order_by("-occurred_at")
        .values_list("pk", flat=True)[:RECEIPT_SWEEP_BATCH_SIZE]
    )

    counts = {outcome.value: 0 for outcome in FillOutcome}
    counts["out_of_time"] = 0
    for position, transaction_id in enumerate(candidate_ids):
        if monotonic() - started > RECEIPT_SWEEP_TIME_BUDGET_SECONDS:
            counts["out_of_time"] = len(candidate_ids) - position
            break
        counts[fill_receipt_url(transaction_id).value] += 1

    return counts
