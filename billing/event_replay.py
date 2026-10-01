"""
billing/event_replay.py
=======================
Automatic replay of FAILED Stripe webhook events — for ONE flow, by name,
and nothing else ever.

WHY THIS EXISTS
---------------
Webhook dispatch is asynchronous: the endpoint claims the event and
answers Stripe 200 in milliseconds, then a Celery worker runs the handler.
So when a handler fails, Stripe has already been told the delivery
succeeded and never redelivers it. The event sits FAILED, and
`sweep_stale_stripe_events` deliberately does not re-run it. For a paid
overage purchase that means the customer's money is gone and their credits
never arrived, until a human runs `manage.py replay_stripe_events`.

WHY IT IS NOT A GENERAL REPLAY LOOP
-----------------------------------
Most handlers move money in ways no database rollback can undo:
`stripe.Refund.create`, `Subscription.modify`, the side-effect invoice
void, licence creation with its teacher invitations. Re-running one of
those un-attended can refund a customer twice or double-charge them. That
is why the manual command exists and why it defaults to a dry run, and
none of that changes.

This task is the narrow exception, and it is narrow by construction rather
than by care:

  1. DENY BY DEFAULT. An event is replayed only if
     (event_type, metadata.flow) is a key of AUTO_REPLAYABLE below. A
     missing flow, an unknown flow, or an unlisted event type is skipped.
  2. THE FLOW HANDLER IS CALLED DIRECTLY, never the generic dispatcher.
     `handle_checkout_completed` fans out by flow, so dispatching through
     it would mean a widened allow-list could reach the upgrade path and
     its `Subscription.modify`. Mapping straight to the one flow function
     makes that impossible: an entry with no mapped function does nothing.
  3. A SECOND, INDEPENDENT CHECK. The mapped function's qualified name
     must appear in VETTED_HANDLERS. Adding an entry to AUTO_REPLAYABLE
     without also vetting its handler here is refused and logged at ERROR.
     This is the defence against a future careless edit, which is the most
     likely way this ever becomes dangerous.
  4. THE PAYLOAD IS THE ONLY SOURCE. The flow is read from the stored
     event payload and Stripe is never re-fetched: re-fetching would
     reintroduce the Stripe-divergence class this whole change exists to
     remove.
  5. EVERY DECISION IS RECORDED, per event, on the row itself
     (`auto_replay_note`) as well as in the log — "why was this one not
     replayed?" must be answerable months later, when the logs are gone.
  6. ATTEMPTS ARE CAPPED (`auto_replay_attempts`), so an event that fails
     the same way forever stops being retried and starts being reported.

WHAT MAKES THE ONE ALLOWED FLOW SAFE TO RE-RUN
-----------------------------------------------
`_handle_overage_checkout_completed` begins by asking
`_overage_already_granted(payment_intent_id, wallet)`, which is True as
soon as a CreditLedger row for that wallet carries the payment intent —
and `grant_overage_bucket` always writes exactly that row. So a grant that
committed is visible to every later replay, with no time window, and the
replay returns before granting anything. Its non-granting branches
(unpaid, over cap) record through the upsert keyed on the payment intent,
so they update rather than duplicate. Since the receipt lookup moved out
of the transaction (billing/receipts.py) the handler makes no outbound
Stripe call at all, so there is nothing for a replay to do twice.
"""

import logging
from enum import Enum

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import StripeEvent, StripeEventStatus
from .stripe_service import StripeWebhookHandler

logger = logging.getLogger(__name__)

#: (event_type, metadata.flow) -> the ONE flow handler that may be re-run.
#: Hardcoded on purpose: a setting or a database row can drift, and the
#: Senior Manager reviews changes to this mapping. Adding an entry requires
#: a written idempotency proof and a vetted handler below.
AUTO_REPLAYABLE = {
    (
        "checkout.session.completed",
        "overage_block_purchase_checkout",
    ): StripeWebhookHandler._handle_overage_checkout_completed,
}

#: Independent second gate: the qualified names of handlers reviewed and
#: found safe to run unattended. A widened AUTO_REPLAYABLE whose handler is
#: not named here is refused, so one careless edit is not enough to reach a
#: refund or a Subscription.modify.
VETTED_HANDLERS = frozenset({"StripeWebhookHandler._handle_overage_checkout_completed"})

#: Stop retrying, start reporting.
MAX_AUTO_REPLAY_ATTEMPTS = 3

#: Upper bound per run.
AUTO_REPLAY_BATCH_SIZE = 50


class ReplayOutcome(str, Enum):
    REPLAYED = "replayed"
    FAILED_AGAIN = "failed_again"
    NOT_ALLOW_LISTED = "not_allow_listed"
    NO_FLOW_IN_PAYLOAD = "no_flow_in_payload"
    HANDLER_NOT_VETTED = "handler_not_vetted"
    ATTEMPTS_EXHAUSTED = "attempts_exhausted"
    CLAIM_LOST = "claim_lost"


def event_flow(event_row) -> str:
    """
    The flow this event carries, read from the STORED payload only.

    Stripe is never re-fetched: the payload we recorded at delivery is the
    same object the handler will be given, and a re-fetch would put a
    network call back into the decision path.
    """
    payload = event_row.payload or {}
    obj = payload.get("object") or {}
    metadata = obj.get("metadata") or {}
    return metadata.get("flow") or ""


def classify(event_row):
    """(outcome, handler) — why this event will or will not be replayed."""
    flow = event_flow(event_row)
    if not flow:
        return ReplayOutcome.NO_FLOW_IN_PAYLOAD, None

    handler = AUTO_REPLAYABLE.get((event_row.event_type, flow))
    if handler is None:
        return ReplayOutcome.NOT_ALLOW_LISTED, None

    qualname = getattr(handler, "__qualname__", "")
    if qualname not in VETTED_HANDLERS:
        logger.error(
            "Stripe event %s matches the auto-replay allow-list for "
            "(%s, %s) but its handler %s is NOT vetted. Refusing to run it. "
            "An entry was added to AUTO_REPLAYABLE without vetting its "
            "handler in VETTED_HANDLERS.",
            event_row.stripe_event_id,
            event_row.event_type,
            flow,
            qualname or handler,
        )
        return ReplayOutcome.HANDLER_NOT_VETTED, None

    if event_row.auto_replay_attempts >= MAX_AUTO_REPLAY_ATTEMPTS:
        return ReplayOutcome.ATTEMPTS_EXHAUSTED, handler

    return None, handler


def _note(event_row, outcome, detail=""):
    """Record on the row itself why this event was or was not replayed."""
    note = f"{timezone.now():%Y-%m-%d %H:%M} auto-replay: {outcome.value}"
    if detail:
        note = f"{note} ({detail})"
    StripeEvent.objects.filter(pk=event_row.pk).update(auto_replay_note=note[:200])


def _claim_for_replay(event_row):
    """
    Take the row from FAILED to PROCESSING with a fresh fencing token.

    One conditional UPDATE, the same idiom the webhook claim uses: two
    sweeps racing, or a sweep racing a live redelivery, cannot both win,
    and the loser's WHERE clause simply matches nothing.
    """
    token = timezone.now()
    claimed = StripeEvent.objects.filter(
        pk=event_row.pk,
        status=StripeEventStatus.FAILED,
        auto_replay_attempts=event_row.auto_replay_attempts,
    ).update(
        status=StripeEventStatus.PROCESSING,
        claimed_at=token,
        handler_started_at=None,
        attempts=F("attempts") + 1,
        auto_replay_attempts=F("auto_replay_attempts") + 1,
        last_error="",
    )
    return token if claimed else None


def replay_one(event_row):
    """Replay a single FAILED event if — and only if — it is allow-listed."""
    outcome, handler = classify(event_row)
    if outcome is not None:
        _note(event_row, outcome, detail=event_flow(event_row) or "no flow")
        logger.info(
            "Auto-replay skipped Stripe event %s (%s): %s.",
            event_row.stripe_event_id,
            event_row.event_type,
            outcome.value,
        )
        return outcome

    token = _claim_for_replay(event_row)
    if token is None:
        logger.info(
            "Auto-replay did not claim Stripe event %s: another worker or "
            "delivery got there first.",
            event_row.stripe_event_id,
        )
        return ReplayOutcome.CLAIM_LOST

    from .webhooks import _run_handler_inline

    session = (event_row.payload or {}).get("object") or {}

    def run(obj):
        # The flow handler's own contract: it runs inside the transaction
        # that handle_checkout_completed would have opened for it.
        with transaction.atomic():
            handler(obj, obj.get("metadata") or {})

    event = {
        "id": event_row.stripe_event_id,
        "type": event_row.event_type,
        "data": {"object": session},
    }
    response = _run_handler_inline(event, run, token, log_prefix="Stripe auto-replay")

    if response.status_code == 200:
        _note(event_row, ReplayOutcome.REPLAYED)
        logger.warning(
            "Auto-replayed FAILED Stripe event %s (%s, attempt %d of %d) and "
            "it succeeded. A customer who had paid without receiving credits "
            "has now been credited.",
            event_row.stripe_event_id,
            event_row.event_type,
            event_row.auto_replay_attempts + 1,
            MAX_AUTO_REPLAY_ATTEMPTS,
        )
        return ReplayOutcome.REPLAYED

    # _run_handler_inline has already settled the row back to FAILED with
    # the error; it stays claimable for the next run, up to the cap.
    _note(event_row, ReplayOutcome.FAILED_AGAIN)
    logger.error(
        "Auto-replay of Stripe event %s (%s) failed again (attempt %d of "
        "%d). It stays FAILED.",
        event_row.stripe_event_id,
        event_row.event_type,
        event_row.auto_replay_attempts + 1,
        MAX_AUTO_REPLAY_ATTEMPTS,
    )
    return ReplayOutcome.FAILED_AGAIN


def replay_safe_failed_events(*, limit=AUTO_REPLAY_BATCH_SIZE):
    """
    Replay every FAILED event that the allow-list covers, oldest first.

    Oldest first on purpose: the customers who have been waiting longest
    are the ones this exists for, including events that failed long before
    this task existed.
    """
    candidate_ids = list(
        StripeEvent.objects.filter(
            status=StripeEventStatus.FAILED,
            event_type__in={event_type for event_type, _ in AUTO_REPLAYABLE},
            auto_replay_attempts__lt=MAX_AUTO_REPLAY_ATTEMPTS,
        )
        # processed_at is when the event was FIRST seen (write-once).
        .order_by("processed_at").values_list("pk", flat=True)[:limit]
    )

    counts = {outcome.value: 0 for outcome in ReplayOutcome}
    for pk in candidate_ids:
        event_row = StripeEvent.objects.filter(pk=pk).first()
        if event_row is None:
            continue
        counts[replay_one(event_row).value] += 1
    return counts
