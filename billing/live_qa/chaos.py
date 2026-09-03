"""
billing/live_qa/chaos.py
========================
A seeded random walk of real actions against one real Stripe subscriber,
checked against the SAME invariant suite every named scenario uses — plus
a shrinker that reduces a failing walk to the shortest one that still
reproduces the violation.

WHY THIS EXISTS
---------------
Every named scenario (renewals, failed_renewal, void_or_refund_...) tests
one HAND-PICKED story. Real customers do not follow hand-picked stories:
they upgrade, then hit a card decline, then get a refund, then upgrade
again, in whatever order their life happens to go. A bug that only shows
up from a specific INTERLEAVING of actions — not any single action in
isolation — is exactly the class this suite's fixed scenarios cannot
find by construction, no matter how many of them are written.

REPRODUCIBILITY IS THE WHOLE POINT
-----------------------------------
`generate_sequence(seed, steps)` uses a plain `random.Random(seed)`, never
the shared/global RNG. The same seed always produces the same sequence
of action names, which is what makes both "run it again to confirm" and
the shrinker below possible — an unseeded chaos test would report a bug
nobody could ever ask it to reproduce.

SHRINKING IS PREFIX-ONLY, DELIBERATELY
----------------------------------------
A full delta-debugging shrinker (able to drop actions from the MIDDLE of
a sequence) would need to re-validate that the remaining actions are
still causally sound after a removal (e.g. "recover_payment" with no
preceding "fail_payment" is a no-op, not a smaller repro). Prefix
shrinking sidesteps that entirely: `random.Random(seed).choices(..., k=M)`
for M < N is GUARANTEED to be an exact prefix of the k=N draw from a
freshly-seeded RNG (the same fixed sequence of internal draws, just
fewer of them consumed) — so every candidate the shrinker tries is, by
construction, a real story that could have happened. It answers "how
early does this first go wrong" rather than "what is the provably
smallest repro," which is the useful question at real-Stripe prices:
each candidate costs a full fresh subscriber and a real replay, so the
search has to be cheap in the number of real runs (O(log N) via binary
search), not in the size of the output.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Callable, Optional

from billing.imports import stripe
from billing.models import (
    BillingInterval,
    PendingChangeType,
    PlanTier,
    SubscriptionPlan,
)
from billing.stripe_live_qa import (
    CARD_FAILS_ON_CHARGE,
    CARD_OK,
    CheckRecorder,
    LiveQAHarness,
    LiveQAInfrastructureError,
    guarded_call,
    require_plan,
)
from billing.stripe_live_qa_scenarios import (
    ADVANCE_OVERSHOOT_SECONDS,
    Subscriber,
    _establish_subscriber,
    _stripe_period_end,
)
from billing.stripe_service import (
    StripeSubscriptionMutationService,
    StripeSubscriptionScheduleService,
)

from .harness import ConcurrentLiveQAHarness

logger = logging.getLogger(__name__)


@dataclass
class ChaosContext:
    # LiveQAHarness (the base class), not ConcurrentLiveQAHarness
    # specifically: run_chaos_walk always constructs the concurrent
    # subclass (for its automatic invariant checkpointing), but
    # billing/qa_console.py's interactive Console tab reuses these same
    # action functions against a plain LiveQAHarness reconstructed fresh
    # per HTTP request — every action here only calls methods that
    # exist on the base class (attach_card, retrieve_subscription,
    # advance_clock_to, drain_events), so the base type is what this
    # dataclass actually needs.
    harness: LiveQAHarness
    sub: Subscriber
    rec: CheckRecorder
    monthly_plan: SubscriptionPlan
    pro_plan: SubscriptionPlan
    annual_plan: SubscriptionPlan
    using_failing_card: bool = False
    cancelled: bool = False


ActionFn = Callable[[ChaosContext], str]


def _drain(ctx: ChaosContext) -> None:
    ctx.harness.drain_events(customer_id=ctx.sub.customer_id)


def _action_advance_boundary(ctx: ChaosContext) -> str:
    if ctx.cancelled:
        return "skipped: subscription already cancelled"
    _, period_end = _stripe_period_end(ctx.harness, ctx.sub.stripe_subscription_id)
    if period_end is None:
        return "skipped: Stripe reports no current period (likely cancelled)"
    ctx.harness.advance_clock_to(
        ctx.sub.clock_id, int(period_end.timestamp()) + ADVANCE_OVERSHOOT_SECONDS
    )
    _drain(ctx)
    return f"advanced past {period_end.isoformat()}"


def _action_toggle_payment_failure(ctx: ChaosContext) -> str:
    if ctx.cancelled:
        return "skipped: subscription already cancelled"
    token = CARD_OK if ctx.using_failing_card else CARD_FAILS_ON_CHARGE
    card = ctx.harness.attach_card(
        customer_id=ctx.sub.customer_id, token=token, set_default=True
    )
    guarded_call(
        stripe.Subscription.modify,
        ctx.sub.stripe_subscription_id,
        default_payment_method=card["id"],
    )
    ctx.using_failing_card = not ctx.using_failing_card
    _drain(ctx)
    return f"default payment method -> {'failing' if not ctx.using_failing_card else 'working'} card"


def _action_upgrade_same_interval(ctx: ChaosContext) -> str:
    if ctx.cancelled:
        return "skipped: subscription already cancelled"
    local = ctx.sub.local()
    if local is None:
        return "skipped: no active local subscription"
    if local.plan.interval != BillingInterval.MONTHLY:
        return "skipped: only defined for a monthly plan"
    target = ctx.pro_plan if local.plan_id == ctx.monthly_plan.id else ctx.monthly_plan
    stripe_sub = ctx.harness.retrieve_subscription(ctx.sub.stripe_subscription_id)
    items = (stripe_sub.get("items") or {}).get("data") or []
    if not items:
        return "skipped: Stripe subscription has no items"
    try:
        StripeSubscriptionMutationService._apply_upgrade_directly(
            local, target, items[0]["id"]
        )
    except ValueError as exc:
        return f"skipped: {exc}"
    _drain(ctx)
    return f"same-interval swap -> {target.name}"


def _action_interval_cross_up(ctx: ChaosContext) -> str:
    if ctx.cancelled:
        return "skipped: subscription already cancelled"
    local = ctx.sub.local()
    if local is None:
        return "skipped: no active local subscription"
    if local.plan.interval != BillingInterval.MONTHLY:
        return "skipped: already annual, nothing to cross up from"
    stripe_sub = ctx.harness.retrieve_subscription(ctx.sub.stripe_subscription_id)
    items = (stripe_sub.get("items") or {}).get("data") or []
    if not items:
        return "skipped: Stripe subscription has no items"
    try:
        StripeSubscriptionMutationService._apply_upgrade_directly(
            local, ctx.annual_plan, items[0]["id"]
        )
    except ValueError as exc:
        return f"skipped: {exc}"
    _drain(ctx)
    return "crossed monthly -> annual"


def _action_schedule_downgrade(ctx: ChaosContext) -> str:
    if ctx.cancelled:
        return "skipped: subscription already cancelled"
    local = ctx.sub.local()
    if local is None:
        return "skipped: no active local subscription"
    if local.plan_id == ctx.monthly_plan.id:
        return "skipped: already on the downgrade target"
    from billing.services import SubscriptionService

    try:
        schedule_id = StripeSubscriptionScheduleService.schedule_plan_change_on_stripe(
            local, ctx.monthly_plan
        )
        SubscriptionService.schedule_plan_change(
            ctx.sub.user,
            ctx.monthly_plan,
            PendingChangeType.DOWNGRADE,
            "Chaos walk deferred downgrade.",
            stripe_schedule_id=schedule_id,
        )
    except ValueError as exc:
        return f"skipped: {exc}"
    _drain(ctx)
    return f"scheduled downgrade -> {ctx.monthly_plan.name}"


def _action_partial_refund_latest_invoice(ctx: ChaosContext) -> str:
    if ctx.cancelled:
        return "skipped: subscription already cancelled"
    invoices = guarded_call(
        stripe.Invoice.list,
        subscription=ctx.sub.stripe_subscription_id,
        status="paid",
        limit=1,
    )
    paid = list(invoices.get("data") or [])
    if not paid:
        return "skipped: no paid invoice to refund"
    invoice = guarded_call(
        stripe.Invoice.retrieve, paid[0]["id"], expand=["payment_intent"]
    )
    payment_intent = invoice.get("payment_intent")
    pi_id = payment_intent["id"] if isinstance(payment_intent, dict) else payment_intent
    if not pi_id:
        return "skipped: paid invoice has no PaymentIntent"
    amount = int(invoice.get("amount_paid") or 0)
    if amount <= 0:
        return "skipped: paid invoice has a zero amount"
    partial = max(1, amount // 4)
    try:
        guarded_call(
            stripe.Refund.create,
            payment_intent=pi_id,
            amount=partial,
            idempotency_key=f"chaos-refund-{pi_id}",
        )
    except stripe.error.StripeError as exc:
        return f"skipped: {exc}"
    _drain(ctx)
    return f"partial refund of {partial} on {paid[0]['id']}"


def _action_add_payment_method(ctx: ChaosContext) -> str:
    if ctx.cancelled:
        return "skipped: subscription already cancelled"
    ctx.harness.attach_card(
        customer_id=ctx.sub.customer_id, token=CARD_OK, set_default=False
    )
    _drain(ctx)
    return "attached a spare (non-default) card"


def _action_cancel(ctx: ChaosContext) -> str:
    if ctx.cancelled:
        return "skipped: already cancelled"
    guarded_call(stripe.Subscription.delete, ctx.sub.stripe_subscription_id)
    ctx.cancelled = True
    _drain(ctx)
    return "cancelled the subscription"


# name -> (weight, function). Weight is relative, not a percentage — see
# generate_sequence.
DEFAULT_ACTIONS: dict = {
    "advance_boundary": (30, _action_advance_boundary),
    "toggle_payment_failure": (15, _action_toggle_payment_failure),
    "upgrade_same_interval": (15, _action_upgrade_same_interval),
    "interval_cross_up": (10, _action_interval_cross_up),
    "schedule_downgrade": (10, _action_schedule_downgrade),
    "partial_refund_latest_invoice": (10, _action_partial_refund_latest_invoice),
    "add_payment_method": (5, _action_add_payment_method),
    "cancel": (5, _action_cancel),
}


def generate_sequence(seed: int, steps: int, actions: Optional[dict] = None) -> list:
    """
    Deterministic: same (seed, steps, actions) always produces the same
    list. A FRESH `random.Random(seed)` every call is what makes a
    shorter draw (smaller `steps`) a guaranteed PREFIX of a longer one —
    see the module docstring. Never reuse a single Random instance
    across calls with different `steps` for the same seed.
    """
    pool = actions or DEFAULT_ACTIONS
    names = list(pool)
    weights = [pool[n][0] for n in names]
    rng = random.Random(seed)
    return rng.choices(names, weights=weights, k=steps)


@dataclass
class ExecutedStep:
    index: int
    action: str
    note: str
    new_violations: list = field(default_factory=list)


@dataclass
class ChaosWalkResult:
    seed: int
    steps: int
    run_id: str = ""
    sequence: list = field(default_factory=list)
    executed: list = field(default_factory=list)
    infra_error: str = ""
    cleanup_errors: list = field(default_factory=list)

    @property
    def violations(self) -> list:
        out = []
        for step in self.executed:
            out.extend(step.new_violations)
        return out

    @property
    def failed(self) -> bool:
        return bool(self.violations) or bool(self.infra_error)

    def first_violation_step(self) -> Optional[int]:
        for step in self.executed:
            if step.new_violations:
                return step.index
        return None

    def summary(self) -> str:
        if self.infra_error:
            return (
                f"seed={self.seed} steps={self.steps}: INFRASTRUCTURE ERROR — "
                f"{self.infra_error}"
            )
        if not self.violations:
            return (
                f"seed={self.seed} steps={self.steps}: no invariant violation "
                f"across {len(self.executed)} executed step(s)"
            )
        first = self.first_violation_step()
        keys = sorted({v.key for v in self.violations})
        return (
            f"seed={self.seed} steps={self.steps}: {len(self.violations)} "
            f"violation(s) first at step {first} — {', '.join(keys)}"
        )


def run_chaos_walk(
    seed: int,
    steps: int,
    *,
    actions: Optional[dict] = None,
    keep_objects: bool = False,
) -> ChaosWalkResult:
    """
    Establish ONE real subscriber, replay the seeded sequence against it,
    and return every invariant violation observed along the way — each
    attributed to the step that produced it, since invariants run
    automatically inside drain_events (ConcurrentLiveQAHarness), and
    every action here drains after mutating.
    """
    pool = actions or DEFAULT_ACTIONS
    sequence = generate_sequence(seed, steps, pool)
    harness = ConcurrentLiveQAHarness(run_id=f"chaos-{seed}-{steps}")
    result = ChaosWalkResult(
        seed=seed, steps=steps, run_id=harness.run_id, sequence=sequence
    )

    try:
        if harness.invariants is not None:
            harness.invariants.begin_scenario()

        monthly = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)
        pro = require_plan(tier=PlanTier.PRO, interval=BillingInterval.MONTHLY)
        annual = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.ANNUAL)

        rec = CheckRecorder()
        sub = _establish_subscriber(harness, rec, plan=monthly, label=f"chaos-{seed}")
        harness.drain_events(customer_id=sub.customer_id)

        ctx = ChaosContext(
            harness=harness,
            sub=sub,
            rec=rec,
            monthly_plan=monthly,
            pro_plan=pro,
            annual_plan=annual,
        )

        before_count = (
            len(harness.invariants.collect()) if harness.invariants is not None else 0
        )
        for i, action_name in enumerate(sequence):
            fn = pool[action_name][1]
            try:
                note = fn(ctx)
            except LiveQAInfrastructureError:
                raise
            except Exception as exc:  # noqa: BLE001 - a chaos action failing IS data
                note = f"RAISED: {exc!r}"
                logger.exception(
                    "[LIVE QA %s] chaos step %d (%s) raised.",
                    harness.run_id,
                    i,
                    action_name,
                )

            all_violations = (
                harness.invariants.collect() if harness.invariants is not None else []
            )
            new_violations = all_violations[before_count:]
            before_count = len(all_violations)

            result.executed.append(
                ExecutedStep(
                    index=i,
                    action=action_name,
                    note=note,
                    new_violations=new_violations,
                )
            )
            logger.info(
                "[LIVE QA %s] chaos step %d/%d: %s -> %s%s",
                harness.run_id,
                i + 1,
                steps,
                action_name,
                note,
                f" ({len(new_violations)} violation(s))" if new_violations else "",
            )
    except (
        Exception
    ) as exc:  # noqa: BLE001 - setup/config faults must not crash the walk
        result.infra_error = repr(exc)
        logger.exception("[LIVE QA %s] chaos walk could not complete.", harness.run_id)
    finally:
        if keep_objects:
            logger.warning(
                "[LIVE QA %s] keep_objects set — NOT cleaning up.", harness.run_id
            )
        else:
            result.cleanup_errors = harness.cleanup()

    return result


@dataclass
class ShrinkResult:
    seed: int
    original_steps: int
    minimal_steps: Optional[int]
    attempts: list = field(default_factory=list)  # list[ChaosWalkResult]

    def summary(self) -> str:
        if self.minimal_steps is None:
            return (
                f"seed={self.seed}: could not reproduce a violation at "
                f"original_steps={self.original_steps} on re-run — nothing to "
                f"shrink (the failure may be Stripe-state-dependent, not "
                f"purely a function of the seed)"
            )
        return (
            f"seed={self.seed}: shrank {self.original_steps} -> "
            f"{self.minimal_steps} step(s) in {len(self.attempts)} real re-run(s)"
        )


def shrink_chaos_failure(
    seed: int,
    original_steps: int,
    *,
    actions: Optional[dict] = None,
    max_reruns: int = 12,
) -> ShrinkResult:
    """
    Binary-search the shortest PREFIX length (see module docstring for
    why prefix-only) that still reproduces at least one violation,
    bounded to `max_reruns` real Stripe walks regardless of
    `original_steps` — each candidate is a full fresh subscriber plus a
    real replay, so the search must be cheap in re-run COUNT.
    """
    attempts: list = []

    def _fails(steps: int) -> bool:
        walk = run_chaos_walk(seed, steps, actions=actions)
        attempts.append(walk)
        return bool(walk.violations)

    if len(attempts) >= max_reruns or not _fails(original_steps):
        return ShrinkResult(
            seed=seed,
            original_steps=original_steps,
            minimal_steps=None,
            attempts=attempts,
        )

    lo, hi = 1, original_steps
    minimal = original_steps
    while lo <= hi and len(attempts) < max_reruns:
        mid = (lo + hi) // 2
        if mid == 0:
            break
        if _fails(mid):
            minimal = mid
            hi = mid - 1
        else:
            lo = mid + 1

    return ShrinkResult(
        seed=seed,
        original_steps=original_steps,
        minimal_steps=minimal,
        attempts=attempts,
    )
