"""
billing/live_qa/checkpoints.py
==============================
Evaluates invariants after every step, without scenarios having to ask.

HOW IT ATTACHES
---------------
Every scenario in this suite drains events immediately after doing
something that could change billing state — that is what a "step" IS
here. So the checkpoint hangs off drain_events rather than off a hook
threaded through each scenario's signature. Existing scenarios need one
line (registering their actor) and get per-step invariant evaluation for
free; nothing else about them changes.

ATTRIBUTION
-----------
One harness is shared by every concurrently-running scenario, so results
are collected in THREAD-LOCAL storage. Each scenario owns exactly one
worker thread, which makes the thread the natural (and lock-free) unit of
attribution — no scenario can be credited with another's violations.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    UserSubscription,
)

from .invariants import (
    SCOPE_GLOBAL,
    SCOPE_INDIVIDUAL,
    ActorHistory,
    InvariantContext,
    StepRecord,
    StripeSnapshot,
    evaluate,
)

logger = logging.getLogger(__name__)


@dataclass
class ActorState:
    customer_id: str
    user: object = None
    subscription_id: Optional[str] = None
    history: ActorHistory = field(default_factory=ActorHistory)
    step_index: int = 0


class InvariantRunner:
    """Owns actor state and per-thread result collection."""

    def __init__(self, harness, *, include_stripe: bool = True):
        self.harness = harness
        self.include_stripe = include_stripe
        self._actors: dict = {}
        self._lock = threading.Lock()
        self._local = threading.local()
        self.evaluations = 0

    # -- actors ----------------------------------------------------------

    def register_actor(
        self, customer_id: str, *, user=None, subscription_id: Optional[str] = None
    ) -> ActorState:
        with self._lock:
            state = self._actors.get(customer_id)
            if state is None:
                state = ActorState(customer_id=customer_id)
                self._actors[customer_id] = state
            if user is not None:
                state.user = user
            if subscription_id is not None:
                state.subscription_id = subscription_id
            return state

    def actor_for(self, customer_id: str) -> Optional[ActorState]:
        with self._lock:
            return self._actors.get(customer_id)

    # -- per-thread collection -------------------------------------------

    def begin_scenario(self) -> None:
        self._local.results = []

    def collect(self) -> list:
        return list(getattr(self._local, "results", []))

    def _record(self, results) -> None:
        bucket = getattr(self._local, "results", None)
        if bucket is None:
            bucket = []
            self._local.results = bucket
        bucket.extend(results)

    # -- the checkpoint --------------------------------------------------

    def checkpoint(self, customer_id: str, label: str = "") -> list:
        """
        Evaluate every applicable invariant for one actor. Never raises —
        a checkpoint that blows up must not take the scenario with it, or
        an observation tool becomes a source of failures.
        """
        state = self.actor_for(customer_id)
        if state is None:
            # Not an error: some customers (a bare payment-method actor,
            # say) have no subscription to evaluate.
            return []

        try:
            state.step_index += 1
            context = self._build_context(state, label)
            results = evaluate(
                context,
                scopes=(SCOPE_INDIVIDUAL, SCOPE_GLOBAL),
                include_stripe=self.include_stripe,
            )
            self._update_history(state, context)
            self.evaluations += 1

            failed = [r for r in results if r.outcome.failed]
            if failed:
                self._record(failed)
                for result in failed:
                    logger.error(
                        "[LIVE QA %s] INVARIANT %s at %s: %s | observed: %s",
                        self.harness.run_id,
                        result.key,
                        label or f"step {state.step_index}",
                        result.outcome.detail,
                        result.outcome.observed,
                    )
            return results
        except Exception:  # noqa: BLE001 - observation must not break the run
            logger.exception(
                "[LIVE QA %s] invariant checkpoint failed for %s.",
                self.harness.run_id,
                customer_id,
            )
            return []

    def _build_context(self, state: ActorState, label: str) -> InvariantContext:
        subscription = None
        wallet = None
        if state.user is not None:
            subscription = (
                UserSubscription.objects.filter(user=state.user, is_active=True)
                .select_related("plan", "pending_plan")
                .first()
            )
            wallet = CreditWallet.objects.filter(user=state.user).first()

        snapshot = None
        if self.include_stripe:
            snapshot = StripeSnapshot(
                self.harness,
                subscription_id=state.subscription_id,
                customer_id=state.customer_id,
            )

        stream = self.harness.bus.stream_for(state.customer_id)
        event_ids = set(stream.dispatched_event_ids) if stream is not None else set()

        return InvariantContext(
            user=state.user,
            subscription=subscription,
            wallet=wallet,
            run_customer_id=state.customer_id,
            run_subscription_id=state.subscription_id,
            stripe_event_ids=event_ids,
            step=StepRecord(
                index=state.step_index,
                label=label or f"step {state.step_index}",
            ),
            history=state.history,
            snapshot=snapshot,
        )

    def _update_history(self, state: ActorState, context: InvariantContext) -> None:
        """Recorded AFTER evaluation, so an invariant compares the current
        state against everything that came BEFORE it — not against itself."""
        monthly = None
        if context.wallet is not None:
            monthly = CreditBucket.objects.filter(
                wallet=context.wallet, bucket_type=CreditBucketType.MONTHLY
            ).count()
        # getattr rather than attribute access: InvariantContext types its
        # model slots as `object` (they are optional and cross-app), so a
        # direct read is not statically checkable here.
        state.history.observe(
            billing_cycle_end=getattr(context.subscription, "billing_cycle_end", None),
            monthly_bucket_count=monthly,
        )
