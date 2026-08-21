"""
billing/live_qa/invariants_individual.py
========================================
Invariants for the individual/teacher billing track.

Each one states a property that must hold no matter what sequence of
actions produced the current state. Where an invariant maps onto a bug
this codebase has actually had, the docstring says so — that is the
evidence it is worth its runtime.
"""

from __future__ import annotations

from billing.models import (
    BillingTransaction,
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    StripeSubscriptionStatus,
    UserSubscription,
)
from billing.stripe_service import extract_subscription_billing_period

from .invariants import (
    COST_CHEAP,
    COST_STRIPE,
    SCOPE_INDIVIDUAL,
    invariant,
    ok,
    skipped,
    violated,
)

# Stripe timestamps are whole seconds and our write lands a moment later,
# so exact equality would be flaky for reasons that are not bugs.
PERIOD_TOLERANCE_SECONDS = 180

# Stripe statuses that should map to a locally ACTIVE subscription.
STRIPE_HEALTHY = {"active", "trialing"}
STRIPE_UNHEALTHY = {"past_due", "unpaid", "canceled", "incomplete_expired"}


# --------------------------------------------------------------------------
# Subscription shape (cheap)
# --------------------------------------------------------------------------


@invariant(
    "sub.single_active",
    scope=SCOPE_INDIVIDUAL,
    cost=COST_CHEAP,
    description="A user may never have more than one active subscription.",
)
def _single_active(ctx):
    """Backed by the one_active_subscription_per_user DB constraint, so a
    violation normally raises at write time. Asserted anyway because
    activate_subscription deactivates-then-creates, and a bug in that
    ordering is exactly the kind that would surface as a constraint error
    at 1am with no context."""
    if ctx.user is None:
        return skipped("no user in context")
    count = UserSubscription.objects.filter(user=ctx.user, is_active=True).count()
    if count > 1:
        return violated(
            "more than one active subscription for this user", active_count=count
        )
    return ok(active_count=count)


@invariant(
    "sub.cycle_ordered",
    scope=SCOPE_INDIVIDUAL,
    cost=COST_CHEAP,
    description="billing_cycle_start must precede billing_cycle_end.",
)
def _cycle_ordered(ctx):
    sub = ctx.subscription
    if sub is None:
        return skipped("no active subscription")
    if sub.billing_cycle_start >= sub.billing_cycle_end:
        return violated(
            "billing cycle start is not before its end",
            start=sub.billing_cycle_start.isoformat(),
            end=sub.billing_cycle_end.isoformat(),
        )
    return ok()


@invariant(
    "sub.period_monotonic",
    scope=SCOPE_INDIVIDUAL,
    cost=COST_CHEAP,
    description="billing_cycle_end must never move backwards.",
)
def _period_monotonic(ctx):
    """Compared against the running MAXIMUM, not the previous step, so a
    cycle that regresses and later recovers is still caught."""
    sub = ctx.subscription
    if sub is None:
        return skipped("no active subscription")
    previous = ctx.history.max_billing_cycle_end
    if previous is not None and sub.billing_cycle_end < previous:
        return violated(
            "billing_cycle_end moved backwards — a customer's paid-through "
            "date must never regress",
            current=sub.billing_cycle_end.isoformat(),
            previous_max=previous.isoformat(),
        )
    return ok(current=sub.billing_cycle_end.isoformat())


@invariant(
    "sub.pending_consistency",
    scope=SCOPE_INDIVIDUAL,
    cost=COST_CHEAP,
    description="pending_plan and pending_change_type are set together.",
)
def _pending_consistency(ctx):
    """A half-set pending change is unreachable by design and would make
    the renewal path pick a plan the UI never showed the customer."""
    sub = ctx.subscription
    if sub is None:
        return skipped("no active subscription")
    has_plan = sub.pending_plan_id is not None
    has_type = bool(sub.pending_change_type)
    if has_plan != has_type:
        return violated(
            "pending_plan and pending_change_type disagree",
            pending_plan_id=str(sub.pending_plan_id),
            pending_change_type=sub.pending_change_type,
        )
    return ok(pending=has_plan)


@invariant(
    "sub.trial_flags_consistent",
    scope=SCOPE_INDIVIDUAL,
    cost=COST_CHEAP,
    description="A trial subscription must carry a trial_end.",
)
def _trial_flags(ctx):
    """is_trial is null=True, i.e. three-valued, so this deliberately
    tests `is True` rather than truthiness."""
    sub = ctx.subscription
    if sub is None:
        return skipped("no active subscription")
    if sub.is_trial is True and sub.trial_end is None:
        return violated(
            "subscription is marked as a trial but has no trial_end, so "
            "nothing will ever expire it",
            is_trial=sub.is_trial,
        )
    return ok(is_trial=sub.is_trial)


@invariant(
    "sub.grant_date_present_for_annual",
    scope=SCOPE_INDIVIDUAL,
    cost=COST_CHEAP,
    description="An active annual plan must have next_credit_grant_at set.",
)
def _annual_grant_date(ctx):
    """This is Phase 0's Bug 2 as a standing check.

    process_annual_plan_credit_grants filters next_credit_grant_at__lte=now,
    and SQL excludes NULL — so a NULL here means the subscriber silently
    receives nothing for the rest of a year they have paid for.
    """
    sub = ctx.subscription
    if sub is None:
        return skipped("no active subscription")
    from billing.models import BillingInterval

    if sub.plan.interval != BillingInterval.ANNUAL:
        return skipped("not an annual plan")
    if sub.is_trial is True:
        return skipped("still a trial — no monthly cadence yet")
    if sub.next_credit_grant_at is None:
        return violated(
            "annual subscription has no next_credit_grant_at; the monthly "
            "grant task can never pick it up again",
            plan=sub.plan.name,
        )
    return ok(next_credit_grant_at=sub.next_credit_grant_at.isoformat())


# --------------------------------------------------------------------------
# Wallet and buckets (cheap)
# --------------------------------------------------------------------------


@invariant(
    "wallet.customer_id_matches",
    scope=SCOPE_INDIVIDUAL,
    cost=COST_CHEAP,
    description="The wallet points at the Stripe customer this run created.",
)
def _wallet_customer(ctx):
    """If these diverge, the next service call mints a SECOND Stripe
    customer — one with no test clock, which cannot be advanced. The
    scenario then fails for a reason that has nothing to do with billing."""
    if ctx.wallet is None or not ctx.run_customer_id:
        return skipped("no wallet or no run customer")
    if ctx.wallet.stripe_customer_id != ctx.run_customer_id:
        return violated(
            "wallet points at a different Stripe customer than this run created",
            wallet_customer=ctx.wallet.stripe_customer_id,
            run_customer=ctx.run_customer_id,
        )
    return ok()


@invariant(
    "bucket.used_within_total",
    scope=SCOPE_INDIVIDUAL,
    cost=COST_CHEAP,
    description="Every credit bucket has 0 <= used_credits <= total_credits.",
)
def _bucket_bounds(ctx):
    """There is no CheckConstraint enforcing this — only PositiveIntegerField
    and application logic — so it is worth asserting directly."""
    if ctx.wallet is None:
        return skipped("no wallet")
    bad = [
        {
            "id": str(b.id),
            "type": b.bucket_type,
            "used": b.used_credits,
            "total": b.total_credits,
        }
        for b in CreditBucket.objects.filter(wallet=ctx.wallet)
        if b.used_credits > b.total_credits
    ]
    if bad:
        return violated("bucket(s) consumed beyond their total", buckets=bad)
    return ok()


@invariant(
    "bucket.overage_never_expires",
    scope=SCOPE_INDIVIDUAL,
    cost=COST_CHEAP,
    description="OVERAGE buckets never expire — the customer paid for them.",
)
def _overage_never_expires(ctx):
    if ctx.wallet is None:
        return skipped("no wallet")
    expiring = list(
        CreditBucket.objects.filter(
            wallet=ctx.wallet, bucket_type=CreditBucketType.OVERAGE
        )
        .exclude(expires_at__isnull=True)
        .values_list("id", flat=True)
    )
    if expiring:
        return violated(
            "purchased overage credits were given an expiry date",
            bucket_ids=[str(i) for i in expiring],
        )
    return ok()


@invariant(
    "bucket.monthly_count_monotonic",
    scope=SCOPE_INDIVIDUAL,
    cost=COST_CHEAP,
    description="Granted monthly buckets are never destroyed.",
)
def _monthly_monotonic(ctx):
    """Buckets are retired by setting expires_at/is_processed, never
    deleted. A decreasing count means something removed evidence of a
    grant."""
    if ctx.wallet is None:
        return skipped("no wallet")
    count = CreditBucket.objects.filter(
        wallet=ctx.wallet, bucket_type=CreditBucketType.MONTHLY
    ).count()
    if count < ctx.history.max_monthly_bucket_count:
        return violated(
            "the number of monthly credit buckets decreased",
            current=count,
            previous_max=ctx.history.max_monthly_bucket_count,
        )
    return ok(monthly_buckets=count)


@invariant(
    "wallet.single_wallet",
    scope=SCOPE_INDIVIDUAL,
    cost=COST_CHEAP,
    description="A user has exactly one credit wallet.",
)
def _single_wallet(ctx):
    if ctx.user is None:
        return skipped("no user in context")
    count = CreditWallet.objects.filter(user=ctx.user).count()
    if count > 1:
        return violated("user has multiple credit wallets", wallet_count=count)
    return ok(wallet_count=count)


@invariant(
    "txn.unique_per_invoice",
    scope=SCOPE_INDIVIDUAL,
    cost=COST_CHEAP,
    description="One Stripe invoice produces at most one BillingTransaction.",
)
def _txn_unique(ctx):
    """Backed by a partial unique constraint, asserted here because a
    duplicate would mean a customer was recorded as paying twice."""
    if ctx.user is None:
        return skipped("no user in context")
    seen: dict = {}
    duplicates: list = []
    rows = BillingTransaction.objects.filter(user=ctx.user).exclude(
        stripe_invoice_id__isnull=True
    )
    for invoice_id in rows.values_list("stripe_invoice_id", flat=True):
        if not invoice_id:
            continue
        seen[invoice_id] = seen.get(invoice_id, 0) + 1
        if seen[invoice_id] == 2:
            duplicates.append(invoice_id)
    if duplicates:
        return violated(
            "the same Stripe invoice produced multiple billing transactions",
            invoice_ids=duplicates,
        )
    return ok()


# --------------------------------------------------------------------------
# Local state vs Stripe (needs the snapshot)
# --------------------------------------------------------------------------


@invariant(
    "sub.period_matches_stripe",
    scope=SCOPE_INDIVIDUAL,
    cost=COST_STRIPE,
    description="Local billing_cycle_end tracks Stripe's real period end.",
)
def _period_matches_stripe(ctx):
    """This is C1 and C2 as a standing check, and the single most valuable
    Stripe-backed invariant: if Stripe moves the field again, or if any
    path falls back to wall-clock arithmetic and drifts, this catches it
    on the very next step."""
    sub = ctx.subscription
    snapshot = ctx.snapshot
    if sub is None or snapshot is None:
        return skipped("no active subscription or no snapshot")
    stripe_sub = snapshot.subscription
    if stripe_sub is None:
        return skipped("Stripe subscription unavailable this step")
    if sub.is_trial is True:
        return skipped("trial — local cycle tracks trial_end, not the invoice")

    _, stripe_end = extract_subscription_billing_period(stripe_sub)
    if stripe_end is None:
        return violated(
            "could not read a billing period from a real Stripe subscription "
            "— this is exactly the C1 failure (Stripe moved the field)",
            stripe_subscription=stripe_sub.get("id"),
        )

    delta = abs((sub.billing_cycle_end - stripe_end).total_seconds())
    if delta > PERIOD_TOLERANCE_SECONDS:
        return violated(
            "local billing_cycle_end has drifted from Stripe's period end",
            local=sub.billing_cycle_end.isoformat(),
            stripe=stripe_end.isoformat(),
            delta_seconds=round(delta),
        )
    return ok(delta_seconds=round(delta))


@invariant(
    "sub.subscription_id_stable",
    scope=SCOPE_INDIVIDUAL,
    cost=COST_CHEAP,
    description="The local row keeps pointing at the Stripe subscription.",
)
def _subscription_id_stable(ctx):
    """Renewal creates a NEW UserSubscription row and does not carry the
    Stripe ids across — callers must re-stamp them. A mismatch here means
    the row has fallen out of reconcile_subscription_renewals' filter and
    can never be repaired automatically."""
    sub = ctx.subscription
    if sub is None or not ctx.run_subscription_id:
        return skipped("no active subscription or no run subscription id")
    if sub.is_trial is True and not sub.stripe_subscription_id:
        return skipped("local-only trial")
    if sub.stripe_subscription_id != ctx.run_subscription_id:
        return violated(
            "local row lost or changed its Stripe subscription id",
            local=sub.stripe_subscription_id,
            expected=ctx.run_subscription_id,
        )
    return ok()


@invariant(
    "sub.status_matches_stripe",
    scope=SCOPE_INDIVIDUAL,
    cost=COST_STRIPE,
    description="An unhealthy Stripe subscription is not locally ACTIVE.",
)
def _status_matches_stripe(ctx):
    """Deliberately one-directional. Local status lags Stripe by design
    (it is written by webhooks), so `Stripe healthy + local not yet
    updated` is normal. The dangerous direction is the other one: Stripe
    says past_due/canceled while we still treat the customer as active
    and keep granting credits."""
    sub = ctx.subscription
    snapshot = ctx.snapshot
    if sub is None or snapshot is None:
        return skipped("no active subscription or no snapshot")
    stripe_sub = snapshot.subscription
    if stripe_sub is None:
        return skipped("Stripe subscription unavailable this step")

    stripe_status = stripe_sub.get("status")
    if stripe_status in STRIPE_UNHEALTHY and (
        sub.stripe_status == StripeSubscriptionStatus.ACTIVE
    ):
        return violated(
            "Stripe considers this subscription unhealthy while we still "
            "record it as ACTIVE",
            stripe_status=stripe_status,
            local_status=sub.stripe_status,
        )
    return ok(stripe_status=stripe_status, local_status=sub.stripe_status)


@invariant(
    "credit.paid_period_invoice_grants_credits",
    scope=SCOPE_INDIVIDUAL,
    cost=COST_STRIPE,
    description="Every paid period invoice is matched by granted credits.",
)
def _paid_invoice_grants_credits(ctx):
    """The money invariant: "the customer paid and got nothing".

    Only the FORWARD direction is asserted. The reverse ("granted without
    paying") cannot be checked safely yet, because same-interval upgrades
    legitimately grant a bucket with no new period invoice, and this
    context has no record of which action produced the step. Asserting it
    now would produce false positives on every upgrade, and an invariant
    that cries wolf gets muted. It arrives with the action tracking in
    the chaos-walk phase.
    """
    snapshot = ctx.snapshot
    if snapshot is None or ctx.wallet is None:
        return skipped("no snapshot or no wallet")

    from billing.stripe_service import RENEWAL_BILLING_REASONS

    grant_reasons = set(RENEWAL_BILLING_REASONS) | {"subscription_create"}
    period_invoice_ids = {
        inv["id"]
        for inv in snapshot.paid_invoices
        if inv.get("billing_reason") in grant_reasons
    }
    if not period_invoice_ids:
        return skipped("no paid period invoices yet")

    monthly_buckets = CreditBucket.objects.filter(
        wallet=ctx.wallet, bucket_type=CreditBucketType.MONTHLY
    ).count()

    if monthly_buckets < len(period_invoice_ids):
        return violated(
            "more paid period invoices than monthly credit grants — a "
            "customer has paid for a cycle without receiving credits",
            paid_period_invoices=len(period_invoice_ids),
            monthly_buckets=monthly_buckets,
        )
    return ok(
        paid_period_invoices=len(period_invoice_ids),
        monthly_buckets=monthly_buckets,
    )
