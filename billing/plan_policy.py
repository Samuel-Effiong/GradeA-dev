"""
Which plans may be reached through which door.

A plan is NOT selectable merely because it exists, is free, is active, or
because someone knows its UUID. Every entry point that lets a caller name a
plan must go through the explicit allow-lists below, and every endpoint that
lists plans to non-superadmins must use `self_service_plans()`, so the plans a
user can SEE and the plans a user can PICK are the same set.

Three doors exist:

1. Self-service (`POST /subscription/select-plan`, every ordinary user). Paid,
   Stripe-backed INDIVIDUAL plans only. Payment happens in Stripe Checkout.
2. Administrative assignment (`POST /subscription` and
   `POST /user-subscriptions`, superadmin only). Activates a plan with no
   payment step, so it goes through
   `SubscriptionService.activate_plan_without_payment`, which enforces the
   stateful rules (one-time entitlements, no silent replacement of a live
   Stripe subscription, license-track separation) under a per-user lock.
3. Automatic grants at signup (`users/signals.py`): BETA through the same
   guarded service call, or the automatic TRIAL through
   `activate_automatic_free_trial`.

Deliberately NOT eligibility rules: `price_cents == 0` (free plans are exactly
what must not be freely reachable), and `is_active` alone (the internal
"Grading Benchmark Plan" is active).
"""

from django.db.models import Q

from .models import PlanCategory, PlanType, SubscriptionPlan

# Door 1. Ordinary users may pick these, and only these, through self-service.
SELF_SERVICE_PLAN_NAMES = frozenset(
    {
        PlanType.STANDARD,
        PlanType.PRO,
        PlanType.POWER,
        PlanType.STANDARD_ANNUAL,
        PlanType.PRO_ANNUAL,
        PlanType.POWER_ANNUAL,
    }
)

# Granted at most once per user, ever. A user who has held one of these at
# any time (consumed, expired, switched away) can never be granted it again,
# by any path.
ONE_TIME_ENTITLEMENT_PLAN_NAMES = frozenset({PlanType.BETA})

# Door 2. A superadmin may assign the catalog plans (e.g. a comped account),
# BETA (subject to the one-time rule) and negotiated CUSTOM contracts.
# TRIAL is excluded: it is granted automatically at signup with its own
# trial semantics, and activating the TRIAL plan through the generic path
# would create a non-trial subscription with a monthly bucket. Internal
# plans such as the grading benchmark plan are excluded by omission.
ADMIN_ASSIGNABLE_PLAN_NAMES = SELF_SERVICE_PLAN_NAMES | {
    PlanType.BETA,
    PlanType.CUSTOM,
}


class PlanAssignmentRefused(ValueError):
    """A business rule refused a no-payment plan activation."""


def self_service_plans():
    """The only plans a non-superadmin may see in a plan listing."""
    # The allow-list is the eligibility rule. The price conditions are
    # extra refusals: an allow-listed plan saved without a price (price_cents
    # defaults to 0) or without a Stripe price is not purchasable either.
    return SubscriptionPlan.objects.filter(
        category=PlanCategory.INDIVIDUAL,
        is_active=True,
        name__in=SELF_SERVICE_PLAN_NAMES,
        price_cents__gt=0,
    ).exclude(Q(stripe_price_id__isnull=True) | Q(stripe_price_id=""))


def has_live_stripe_subscription(subscription) -> bool:
    """Whether Stripe may still bill (or is still collecting on) this row.

    Live: any row carrying a Stripe subscription id whose status is not
    CANCELED. That covers ACTIVE (including cancel-at-period-end, which
    Stripe keeps ACTIVE until the period ends), TRIALING, PAST_DUE (a
    user must not escape a failed payment by being moved to BETA),
    INCOMPLETE, UNPAID, and a missing status. Only the ACTIVE row is
    asked: renewals leave superseded rows holding the old Stripe id and
    a stale ACTIVE status, so checking history would block users who
    paid once and cancelled.
    """
    from .models import StripeSubscriptionStatus

    return bool(
        subscription
        and subscription.stripe_subscription_id
        and subscription.stripe_status != StripeSubscriptionStatus.CANCELED
    )


def admin_assignable_lookup():
    """Plans a superadmin may name by id. Anything else is 'Invalid plan id'.

    is_active is deliberately not filtered here, so an inactive allow-listed
    plan gets the explicit refusal from admin_assignment_error instead.
    """
    return SubscriptionPlan.objects.filter(
        category=PlanCategory.INDIVIDUAL, name__in=ADMIN_ASSIGNABLE_PLAN_NAMES
    )


def admin_assignment_error(plan):
    """Static (stateless) reasons a plan cannot be assigned by a superadmin.

    Returns a message, or None if the plan itself is assignable. Stateful
    rules about the target user live in activate_plan_without_payment.
    """
    if not plan.is_active:
        return "This plan is inactive and cannot be activated."
    if plan.category != PlanCategory.INDIVIDUAL:
        return (
            "Only INDIVIDUAL plans can be activated here. License plans are "
            "managed through the license endpoints."
        )
    if plan.name not in ADMIN_ASSIGNABLE_PLAN_NAMES:
        return f"The {plan.name} plan cannot be activated through this endpoint."
    return None
