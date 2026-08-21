"""
billing/live_qa/invariants_global.py
====================================
Run-wide invariants over the Stripe webhook ledger.

These are scoped to the event ids THIS RUN dispatched, never to the table
as a whole — a pre-existing FAILED row from a real incident is not this
run's business, and flagging it would train people to ignore the check.
"""

from __future__ import annotations

from django.utils import timezone

from billing.models import StripeEvent, StripeEventStatus
from billing.webhooks import STRIPE_EVENT_CLAIM_STALE_AFTER

from .invariants import COST_CHEAP, SCOPE_GLOBAL, invariant, ok, skipped, violated


@invariant(
    "event.no_failed",
    scope=SCOPE_GLOBAL,
    cost=COST_CHEAP,
    description="No event this run dispatched ended in FAILED.",
)
def _no_failed_events(ctx):
    """A FAILED row means a handler raised on a REAL Stripe payload.

    That is the highest-signal failure the whole suite can produce: it is
    precisely the case mocked tests cannot reach, because a mock only ever
    sends the shape the test author already expected.
    """
    if not ctx.stripe_event_ids:
        return skipped("no events dispatched yet")

    failed = list(
        StripeEvent.objects.filter(
            stripe_event_id__in=ctx.stripe_event_ids,
            status=StripeEventStatus.FAILED,
        ).values_list("stripe_event_id", "event_type", "last_error")
    )
    if failed:
        return violated(
            "handler(s) raised while processing real Stripe payloads",
            failures=[
                {"event": e, "type": t, "error": (err or "")[:200]}
                for e, t, err in failed
            ],
        )
    return ok(dispatched=len(ctx.stripe_event_ids))


@invariant(
    "event.no_stuck_processing",
    scope=SCOPE_GLOBAL,
    cost=COST_CHEAP,
    description="No event is left holding a stale PROCESSING claim.",
)
def _no_stuck_processing(ctx):
    """A claim older than STRIPE_EVENT_CLAIM_STALE_AFTER means a worker
    died mid-handler. In production the sweeper settles those; inside a
    run it means the dispatch path leaked a claim, which would make every
    redelivery answer 409 forever."""
    if not ctx.stripe_event_ids:
        return skipped("no events dispatched yet")

    cutoff = timezone.now() - STRIPE_EVENT_CLAIM_STALE_AFTER
    stuck = list(
        StripeEvent.objects.filter(
            stripe_event_id__in=ctx.stripe_event_ids,
            status=StripeEventStatus.PROCESSING,
            claimed_at__lt=cutoff,
        ).values_list("stripe_event_id", flat=True)
    )
    if stuck:
        return violated(
            "event(s) left holding an abandoned PROCESSING claim",
            event_ids=list(stuck),
        )
    return ok()


@invariant(
    "event.every_dispatched_is_recorded",
    scope=SCOPE_GLOBAL,
    cost=COST_CHEAP,
    description="Every dispatched event has a ledger row.",
)
def _every_dispatched_recorded(ctx):
    """The ledger is the only thing standing between a Stripe redelivery
    and a double grant. An event that was dispatched but left no row is a
    hole in that protection."""
    if not ctx.stripe_event_ids:
        return skipped("no events dispatched yet")

    recorded = set(
        StripeEvent.objects.filter(
            stripe_event_id__in=ctx.stripe_event_ids
        ).values_list("stripe_event_id", flat=True)
    )
    missing = set(ctx.stripe_event_ids) - recorded
    if missing:
        return violated(
            "dispatched event(s) left no row in the idempotency ledger",
            event_ids=sorted(missing)[:20],
            missing_count=len(missing),
        )
    return ok(recorded=len(recorded))
