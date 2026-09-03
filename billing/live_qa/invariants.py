"""
billing/live_qa/invariants.py
=============================
Properties that must hold after EVERY step of EVERY scenario.

WHY THIS IS THE HIGHEST-LEVERAGE PART OF THE SUITE
--------------------------------------------------
A scenario asserts what its author thought to check, in the order they
thought to check it. An invariant asserts something that must be true at
all times, so it catches bugs in sequences nobody scripted — which is the
only honest answer to "test every possible scenario", because the
sequences cannot be enumerated.

Concretely: all three bugs fixed in Phase 0 were found by reading code.
Each would have been caught automatically by an invariant here.

DESIGN NOTES THAT MATTER
------------------------
* Outcomes, never exceptions. An invariant that itself throws is recorded
  as ERROR, visibly distinct from VIOLATED — a buggy check must never
  masquerade as a billing bug, or the suite starts crying wolf.

* SKIPPED is a first-class outcome. "No active subscription right now"
  is not a pass and not a failure; counting it as a pass would inflate
  the numbers and hide an invariant that never actually ran.

* `cost` gates how often an invariant runs. CHEAP is pure database and
  runs after every step in every tier. STRIPE needs API reads and runs
  every step in the deep tier, at checkpoints in the fast one. A single
  memoized StripeSnapshot per step is what makes "after every step"
  affordable at all: ten Stripe-backed invariants cost one round trip.

* `observed` carries the ACTUAL values into the report. A nightly failure
  is read hours later by someone without the objects in hand, so
  "expected X, got Y" beats "invariant violated" by a wide margin.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from billing.stripe_live_qa import Check

logger = logging.getLogger(__name__)

# Scopes
SCOPE_INDIVIDUAL = "individual"
SCOPE_LICENSE = "license"
SCOPE_GLOBAL = "global"

# Costs
COST_CHEAP = "cheap"  # database only
COST_STRIPE = "stripe"  # needs the per-step Stripe snapshot

# Outcome statuses
OK = "ok"
VIOLATED = "violated"
SKIPPED = "skipped"
ERROR = "error"


@dataclass
class InvariantOutcome:
    status: str
    detail: str = ""
    observed: dict = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        # ERROR is a failure of the CHECK, not of billing — but it still
        # has to surface, because an invariant that silently stops
        # evaluating is worse than one that never existed.
        return self.status in (VIOLATED, ERROR)


def ok(detail: str = "", **observed) -> InvariantOutcome:
    return InvariantOutcome(status=OK, detail=detail, observed=observed)


def violated(detail: str, **observed) -> InvariantOutcome:
    return InvariantOutcome(status=VIOLATED, detail=detail, observed=observed)


def skipped(reason: str) -> InvariantOutcome:
    return InvariantOutcome(status=SKIPPED, detail=reason)


@dataclass(frozen=True)
class Invariant:
    key: str
    description: str
    scope: str
    cost: str
    check: Callable


# Flat registry populated by the @invariant decorator — deliberately the
# same shape as the existing SCENARIOS and _EVENT_HANDLERS dicts, so it
# reads as native to this codebase.
INVARIANTS: dict = {}


def invariant(key: str, *, scope: str, cost: str, description: str):
    def decorator(fn: Callable) -> Callable:
        if key in INVARIANTS:
            raise ValueError(f"duplicate invariant key: {key}")
        INVARIANTS[key] = Invariant(
            key=key, description=description, scope=scope, cost=cost, check=fn
        )
        return fn

    return decorator


@dataclass
class InvariantResult:
    key: str
    outcome: InvariantOutcome
    step_label: str = ""

    def to_check(self) -> Check:
        """Adapter so ScenarioResult.checks, failed_checks and the
        management command's reporting all work unchanged."""
        detail = self.outcome.detail
        if self.outcome.observed:
            detail = f"{detail} | observed: {self.outcome.observed}"
        if self.step_label:
            detail = f"[{self.step_label}] {detail}"
        return Check(
            name=self.key,
            passed=not self.outcome.failed,
            detail=detail,
        )


# --------------------------------------------------------------------------
# Per-step state
# --------------------------------------------------------------------------


@dataclass
class StepRecord:
    index: int = 0
    label: str = ""
    action: str = ""


@dataclass
class ActorHistory:
    """Carries what temporal invariants need across steps.

    Monotonicity is checked against a RUNNING MAXIMUM rather than the
    immediately preceding value, so a regression that later recovers is
    still caught — otherwise a bug that moves the cycle backwards and
    then forwards again would slip through unseen.
    """

    max_billing_cycle_end = None
    max_monthly_bucket_count: int = 0
    seen_paid_invoice_ids: set = field(default_factory=set)

    def observe(self, *, billing_cycle_end=None, monthly_bucket_count=None) -> None:
        if billing_cycle_end is not None:
            if (
                self.max_billing_cycle_end is None
                or billing_cycle_end > self.max_billing_cycle_end
            ):
                self.max_billing_cycle_end = billing_cycle_end
        if monthly_bucket_count is not None:
            self.max_monthly_bucket_count = max(
                self.max_monthly_bucket_count, monthly_bucket_count
            )


class StripeSnapshot:
    """
    One Stripe read per object per step, shared by every Stripe-backed
    invariant.

    Without this, running ten Stripe invariants after every step of a
    120-step long-horizon run would be 1,200 extra API calls per actor —
    which is both slow and a rate-limit problem. With it, it is 120.

    Failures are captured rather than raised: a Stripe read that fails
    should make dependent invariants SKIP, not make the whole step look
    like a billing violation.
    """

    def __init__(self, harness, *, subscription_id=None, customer_id=None):
        self.harness = harness
        self.subscription_id = subscription_id
        self.customer_id = customer_id
        self._subscription = None
        self._subscription_loaded = False
        self._invoices = None
        self._invoices_loaded = False
        self.errors: list = []

    @property
    def subscription(self):
        if not self._subscription_loaded:
            self._subscription_loaded = True
            if self.subscription_id:
                try:
                    self._subscription = self.harness.retrieve_subscription(
                        self.subscription_id
                    )
                except Exception as exc:  # noqa: BLE001
                    self.errors.append(f"Subscription.retrieve failed: {exc!r}")
                    logger.warning(
                        "[LIVE QA] snapshot could not read subscription %s: %r",
                        self.subscription_id,
                        exc,
                    )
        return self._subscription

    @property
    def paid_invoices(self) -> list:
        """Paid invoices for this subscription, newest first.

        Unlike Events, stripe.Invoice.list CAN filter by subscription —
        which is what makes the two-sided credit invariant affordable.
        """
        if not self._invoices_loaded:
            self._invoices_loaded = True
            self._invoices = []
            if self.subscription_id:
                try:
                    from billing.imports import stripe
                    from billing.stripe_live_qa import guarded_call

                    page = guarded_call(
                        stripe.Invoice.list,
                        subscription=self.subscription_id,
                        status="paid",
                        limit=100,
                    )
                    self._invoices = list(page.get("data") or [])
                except Exception as exc:  # noqa: BLE001
                    self.errors.append(f"Invoice.list failed: {exc!r}")
                    logger.warning(
                        "[LIVE QA] snapshot could not list invoices for %s: %r",
                        self.subscription_id,
                        exc,
                    )
        return self._invoices or []


@dataclass
class InvariantContext:
    """Everything an invariant is allowed to look at.

    Deliberately a value object: invariants must not mutate state or call
    Stripe outside the snapshot, or they stop being observations and
    start being part of the thing under test.
    """

    user: object = None
    subscription: object = None  # freshly re-fetched active UserSubscription
    wallet: object = None
    run_customer_id: Optional[str] = None
    run_subscription_id: Optional[str] = None
    stripe_event_ids: set = field(default_factory=set)
    step: StepRecord = field(default_factory=StepRecord)
    history: ActorHistory = field(default_factory=ActorHistory)
    snapshot: Optional[StripeSnapshot] = None


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def evaluate(
    context: InvariantContext,
    *,
    scopes=(SCOPE_INDIVIDUAL, SCOPE_GLOBAL),
    include_stripe: bool = True,
    keys=None,
) -> list:
    """
    Run every registered invariant matching `scopes`, returning one
    InvariantResult each. Never raises.
    """
    results: list = []
    for key, inv in INVARIANTS.items():
        if keys is not None and key not in keys:
            continue
        if inv.scope not in scopes:
            continue
        if inv.cost == COST_STRIPE and not include_stripe:
            continue

        try:
            outcome = inv.check(context)
            if not isinstance(outcome, InvariantOutcome):
                outcome = InvariantOutcome(
                    status=ERROR,
                    detail=(
                        f"invariant {key} returned {type(outcome).__name__}, "
                        "not an InvariantOutcome"
                    ),
                )
        except (
            Exception
        ) as exc:  # noqa: BLE001 - a broken CHECK is not a bug in billing
            outcome = InvariantOutcome(
                status=ERROR,
                detail=f"invariant {key} raised: {exc!r}",
            )
            logger.exception("[LIVE QA] invariant %s raised.", key)

        results.append(
            InvariantResult(key=key, outcome=outcome, step_label=context.step.label)
        )

    return results


def failures(results) -> list:
    return [r for r in results if r.outcome.failed]


def summarise(results) -> dict:
    counts: dict = {OK: 0, VIOLATED: 0, SKIPPED: 0, ERROR: 0}
    for result in results:
        counts[result.outcome.status] = counts.get(result.outcome.status, 0) + 1
    return counts
