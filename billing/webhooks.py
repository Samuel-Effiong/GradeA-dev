"""
billing/webhooks.py
====================
Single entrypoint for all incoming Stripe webhook events.

Responsibilities of THIS file, and only this file:
1. Verify the Stripe signature against the raw request body.
2. Claim the event in the StripeEvent ledger so it is processed exactly
   once — Stripe redelivers events on timeout/ambiguous response/manual
   retry, and none of the handlers in stripe_service.py are safe to run
   twice (they create CreditBucket/CreditLedger rows).
3. Dispatch to the appropriate handler in stripe_service.py and record
   the outcome.

No business logic lives here — see StripeWebhookHandler in
stripe_service.py for that, so it stays testable without a fake HTTP
request.

Wire this up in urls.py with CSRF exempt (already handled here via the
decorator) and make sure no auth middleware tries to authenticate this
request as a normal user — Stripe calls it unauthenticated, signature
verification IS the auth.


WHY THE LEDGER TRACKS STATUS INSTEAD OF MERE EXISTENCE
-------------------------------------------------------
This file used to treat "a row exists" as "already handled", and DELETED
the row when a handler raised so Stripe's retry would start fresh. That
combination lost billing events permanently:

  1. Delivery A inserts evt_123 and starts working (slowly).
  2. Stripe times out waiting on A and redelivers evt_123 as B.
  3. B sees the row, concludes "duplicate", and returns 200.
  4. Stripe records the event as DELIVERED and never sends it again.
  5. A's handler then fails, deletes the row, and returns 500 — but
     Stripe already stopped tracking A's attempt.

The customer paid, got no credits, and no record survived to explain it.

A second, quieter hole had the same shape: gunicorn kills any request
over --timeout (see Dockerfile), and `except Exception` cannot catch
that, so the row survived with the work undone and every later
redelivery was waved away as a duplicate — permanently stuck.

So the ledger now records WHAT HAPPENED (PROCESSING / SUCCEEDED /
FAILED) and rows are NEVER deleted. Only SUCCEEDED suppresses a
redelivery. An event still being processed answers 409 — "busy, retry" —
because answering 200 for work that has not finished is precisely the
bug above.

The claim itself is the same conditional-UPDATE idiom already proven in
students.services._claim_submission_for_grading.
"""

import logging
from datetime import timedelta
from enum import Enum

from django.conf import settings
from django.db.models import F
from django.http import HttpResponse, HttpResponseBadRequest
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .imports import stripe
from .models import StripeEvent, StripeEventStatus
from .stripe_service import StripeWebhookHandler

logger = logging.getLogger(__name__)

# gunicorn's --timeout in the Dockerfile CMD: the hard kill point for any
# webhook request. A worker still inside a handler past this is dead.
WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS = 100

# How long a PROCESSING claim may sit before another delivery may steal
# it as abandoned.
#
# !! IF gunicorn's --timeout IS EVER RAISED, RAISE THIS WITH IT. !!
# (A matching comment sits next to --timeout in the Dockerfile.)
#
# Derived from — not merely near — the hard kill point, exactly as
# students.services.GRADING_CLAIM_STALE_AFTER is: a request that somehow
# ran past gunicorn's kill point is gone by the time this window elapses,
# so a stale claim really is abandoned rather than merely slow. A tight
# window here is dangerous: the handlers make outbound Stripe calls
# (Refund.create, Subscription.modify) that a *concurrent* second run
# would duplicate and that no DB rollback can undo.
STRIPE_EVENT_CLAIM_STALE_AFTER = timedelta(
    seconds=WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS + 5 * 60
)

# Stripe retries a non-2xx response with exponential backoff for roughly
# this long, then gives up on the event forever. Past this point a FAILED
# row can only be repaired by hand (manage.py replay_stripe_events).
STRIPE_RETRY_WINDOW = timedelta(days=3)

# Single dispatch table shared by BOTH webhook endpoints. This used to be
# duplicated inline in stripe_webhook and thin_webhook, which meant every
# new event type had to be wired in two places or it silently no-op'd on
# one of them.
_EVENT_HANDLERS = {
    "checkout.session.completed": StripeWebhookHandler.handle_checkout_completed,
    "invoice.payment_succeeded": StripeWebhookHandler.handle_invoice_payment_succeeded,
    "invoice.payment_failed": StripeWebhookHandler.handle_invoice_payment_failed,
    "customer.subscription.deleted": StripeWebhookHandler.handle_subscription_deleted,
    "charge.refunded": StripeWebhookHandler.handle_charge_refunded,
    "payment_intent.succeeded": StripeWebhookHandler.handle_payment_intent_succeeded,
    "payment_intent.payment_failed": StripeWebhookHandler.handle_payment_intent_failed,
    "setup_intent.succeeded": StripeWebhookHandler.handle_setup_intent_succeeded,
}


class ClaimOutcome(Enum):
    CLAIMED = "claimed"  # we own it — run the handler
    IN_FLIGHT = "in_flight"  # another worker holds a fresh claim
    ALREADY_SUCCEEDED = "already_succeeded"  # genuinely done — skip


def _claim_stripe_event(event):
    """
    Atomically claim a Stripe event for processing.

    Returns (ClaimOutcome, claim_token). `claim_token` is the claimed_at
    value we wrote, and is None unless the outcome is CLAIMED — it is the
    fencing token _finish_stripe_event uses to prove it still owns the row.

    The claim is a single conditional UPDATE whose returned row count IS
    the result (the idiom from students.services._claim_submission_for_grading):
    two concurrent redeliveries serialize on the row lock, and exactly one
    wins because the loser's UPDATE re-evaluates its WHERE clause against
    the winner's already-committed PROCESSING state and matches zero rows.

    Claimable: brand new, FAILED (Stripe's retry should do the work), and
    a PROCESSING claim older than STRIPE_EVENT_CLAIM_STALE_AFTER (left
    behind by a killed worker). NOT claimable: SUCCEEDED, and a fresh
    PROCESSING claim.

    Deliberately NOT wrapped in transaction.atomic: the claim must be
    committed and its row lock released BEFORE the handler starts, because
    the handler makes outbound network calls to Stripe. Holding a row lock
    across those is how a webhook endpoint stalls under a Stripe slowdown —
    and, more importantly, an uncommitted claim is invisible to the racing
    delivery, which would silently restore the very bug this fixes.
    """
    now = timezone.now()
    stale_cutoff = now - STRIPE_EVENT_CLAIM_STALE_AFTER

    _, created = StripeEvent.objects.get_or_create(
        stripe_event_id=event["id"],
        defaults={
            "event_type": event["type"],
            "payload": event["data"],
            "status": StripeEventStatus.PROCESSING,
            "claimed_at": now,
            "attempts": 1,
        },
    )
    if created:
        return ClaimOutcome.CLAIMED, now

    # The row already existed. Read the prior state purely so the
    # stale-steal can be logged with the claim's age (see below) — the
    # claim decision itself is made by the UPDATE, not by this read.
    previous = (
        StripeEvent.objects.filter(stripe_event_id=event["id"])
        .values("status", "claimed_at")
        .first()
    )

    claimed = (
        StripeEvent.objects.filter(stripe_event_id=event["id"])
        .exclude(status=StripeEventStatus.SUCCEEDED)
        .exclude(status=StripeEventStatus.PROCESSING, claimed_at__gt=stale_cutoff)
        .update(
            status=StripeEventStatus.PROCESSING,
            claimed_at=now,
            attempts=F("attempts") + 1,
            event_type=event["type"],
            payload=event["data"],
            last_error="",
        )
    )

    if claimed:
        if previous and previous["status"] == StripeEventStatus.PROCESSING:
            # Stealing an abandoned claim. This should be rare; if it
            # shows up regularly in production, STRIPE_EVENT_CLAIM_STALE_AFTER
            # is too tight and a slow-but-alive worker is being robbed.
            age = now - previous["claimed_at"] if previous["claimed_at"] else None
            logger.warning(
                "Stripe event %s: stealing an abandoned PROCESSING claim "
                "(age=%s, cutoff=%s). Expected only after a worker was "
                "killed mid-handler; if this recurs, the staleness cutoff "
                "is too tight.",
                event["id"],
                age,
                STRIPE_EVENT_CLAIM_STALE_AFTER,
            )
        return ClaimOutcome.CLAIMED, now

    # Lost the claim. Distinguish "already done" (200) from "someone else
    # is mid-flight" (409).
    status = (
        StripeEvent.objects.filter(stripe_event_id=event["id"])
        .values_list("status", flat=True)
        .first()
    )
    if status == StripeEventStatus.SUCCEEDED:
        return ClaimOutcome.ALREADY_SUCCEEDED, None
    return ClaimOutcome.IN_FLIGHT, None


def _finish_stripe_event(event_id, claim_token, status, error=""):
    """
    Terminal write, fenced on the claim token.

    The claimed_at=claim_token filter is load-bearing: if this request was
    so slow that another delivery legitimately stole its claim as stale,
    this late write must NOT stomp the thief's result — otherwise a
    slow-but-failing original could flip a freshly SUCCEEDED row back to
    FAILED and invite a replay of non-idempotent Stripe side effects.
    """
    updated = StripeEvent.objects.filter(
        stripe_event_id=event_id,
        status=StripeEventStatus.PROCESSING,
        claimed_at=claim_token,
    ).update(
        status=status,
        completed_at=timezone.now(),
        last_error=error[:2000],
    )
    if not updated:
        logger.warning(
            "Stripe event %s: claim was stolen before the terminal %s write; "
            "leaving the current owner's result intact.",
            event_id,
            status,
        )
    return bool(updated)


def _record_and_dispatch(event, *, log_prefix):
    """
    Shared claim + dispatch core for both webhook endpoints.

    The row is NEVER deleted — see the module docstring for the event-loss
    bug that delete-on-failure caused.

    HTTP contract (Stripe retries any non-2xx with exponential backoff for
    ~3 days, and treats any 2xx as delivered):
      200 — handled now, or previously SUCCEEDED. Stop retrying: correct.
      409 — another worker holds a fresh claim. Deliberately NOT 200: we
            must not claim success for work that has not finished. Stripe
            retries, and by then the event is either SUCCEEDED (-> 200) or
            FAILED (-> we actually do the work).
      500 — the handler raised. Stripe retries; the FAILED row is claimable.
    """
    event_id = event["id"]
    event_type = event["type"]

    outcome, claim_token = _claim_stripe_event(event)

    if outcome is ClaimOutcome.ALREADY_SUCCEEDED:
        logger.info(
            "%s: duplicate delivery of already-processed event %s, skipping.",
            log_prefix,
            event_id,
        )
        return HttpResponse(status=200)

    if outcome is ClaimOutcome.IN_FLIGHT:
        # INFO, not ERROR: this is the expected shape of a Stripe timeout
        # redelivery and must not page anyone. It IS worth counting — a
        # non-trivial rate means handlers have become slow, and the fix is
        # to move their outbound Stripe calls out of the DB transaction,
        # never to answer 200 here (that is the original bug).
        logger.info(
            "%s: event %s is already being processed by another worker; "
            "returning 409 so Stripe retries rather than recording a "
            "success we cannot yet vouch for.",
            log_prefix,
            event_id,
        )
        return HttpResponse("Event already in flight", status=409)

    handler = _EVENT_HANDLERS.get(event_type)
    if handler is None:
        logger.debug("%s: unhandled event type %s.", log_prefix, event_type)
        _finish_stripe_event(event_id, claim_token, StripeEventStatus.SUCCEEDED)
        return HttpResponse(status=200)

    try:
        handler(event["data"]["object"])
    except Exception as exc:
        _finish_stripe_event(
            event_id, claim_token, StripeEventStatus.FAILED, error=repr(exc)
        )
        logger.exception(
            "%s: error processing event %s (%s).",
            log_prefix,
            event_id,
            event_type,
        )
        return HttpResponse(status=500)

    _finish_stripe_event(event_id, claim_token, StripeEventStatus.SUCCEEDED)
    return HttpResponse(status=200)


@csrf_exempt
@require_POST
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        logger.warning("Stripe webhook: invalid payload.")
        return HttpResponseBadRequest("Invalid payload")
    except stripe.error.SignatureVerificationError:
        logger.warning("Stripe webhook: signature verification failed.")
        return HttpResponseBadRequest("Invalid signature")

    return _record_and_dispatch(event, log_prefix="Stripe webhook")


@csrf_exempt
@require_POST
def thin_webhook(request):
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

    try:
        # Verify the signature
        thin_notification = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        logger.warning("Stripe thin webhook: invalid payload.")
        return HttpResponseBadRequest("Invalid payload")
    except stripe.error.SignatureVerificationError:
        logger.warning("Stripe thin webhook: signature verification failed.")
        return HttpResponseBadRequest("Invalid signature")

    event_id = thin_notification["id"]

    # Fetch the full event from Stripe API using the ID. Deliberately
    # BEFORE the ledger is touched: an event we could not retrieve has no
    # payload to record, and no row should exist for work never attempted.
    try:
        event = stripe.Event.retrieve(event_id)
    except Exception:
        logger.exception("Stripe thin webhook: failed to retrieve event %s", event_id)
        # 500, not 400: a Stripe API outage is our/their transient problem,
        # not a malformed request. Both are retried by Stripe, but the
        # status code is what a human reads during an incident.
        return HttpResponse("Failed to retrieve event", status=500)

    return _record_and_dispatch(event, log_prefix="Stripe thin webhook")
