"""
billing/stripe_service.py
==========================
All Stripe-specific business logic lives here, isolated from
SubscriptionService and LicenseSubscriptionService the same way those two
are isolated from each other.

Design principle: Checkout Session creation NEVER grants credits or
creates local subscription rows. That only happens once a webhook confirms
payment (StripeWebhookHandler). This keeps "subscribed" and "paid" as two
separate, auditable steps — closing the gap in the pre-Stripe flow where
SubscriptionManagementViewSet.create()/upgrade() granted credits with no
payment gate at all.

Classes:
- StripeCustomerService    — get-or-create Stripe Customer objects
- StripeCheckoutService    — builds Checkout Sessions for every "pay to
                              unlock" flow (individual subscribe, individual
                              trial, license create)
- StripeSubscriptionMutationService — direct Subscription.modify() for
                              upgrades on an EXISTING Stripe subscription
                              (no Checkout redirect needed; card's already
                              on file)
- StripeOverageService     — explicit, user-confirmed overage block
                              purchases via PaymentIntent
- StripeWebhookHandler     — what to do for each Stripe event type. Called
                              from webhooks.py, which only handles signature
                              verification + idempotency.
"""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Optional

from django.core.cache import cache

# from django.conf import settings
from django.db import transaction
from django.utils import timezone

from AutoGrader.error_messages import describe_stripe_error
from classrooms.models import School
from users.models import CustomUser

from .billing_transaction_service import BillingTransactionService
from .imports import stripe
from .license_service import (
    LicenseSubscriptionService,
    sync_teachers_under_license_to_mailerlite,
)
from .models import (
    CONVERSION_FACTOR,
    PLAN_TIER_HIERARCHY,
    BillingInterval,
    BillingTransactionMethod,
    BillingTransactionSource,
    BillingTransactionStatus,
    BillingTransactionType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    LicenseBillingMethod,
    LicenseBillingRecord,
    LicenseBillingRecordType,
    LicenseOveragePurchaseIntent,
    LicenseOveragePurchaseStatus,
    LicenseSubscription,
    PendingChangeType,
    PlanCategory,
    SchoolCreditAllocation,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
    get_tier_rank,
)
from .services import SubscriptionService
from .subscription_resolver import (
    SOURCE_INDIVIDUAL,
    SOURCE_LICENSE_ADMIN,
    SOURCE_LICENSE_TEACHER,
    resolve_user_billing_context,
)

logger = logging.getLogger(__name__)


# Stripe `billing_reason` values that mean "a genuinely new billing period
# has started", i.e. the only ones allowed to drive a local renewal.
# Deliberately EXCLUDES:
#   subscription_create — handled by checkout.session.completed
#   subscription_update — an immediate upgrade's proration invoice, applied
#                         synchronously by the upgrade flow
#   manual / subscription_threshold — never a cycle rollover
# "subscription" is the legacy pre-2018 spelling of subscription_cycle,
# kept so an older pinned API version behaves identically.
RENEWAL_BILLING_REASONS = frozenset({"subscription_cycle", "subscription"})


def _coerce_stripe_timestamp(value):
    """
    Stripe timestamps are positive Unix ints. Anything else — None, 0, a
    string, a bool (an int subclass in Python, hence the explicit guard),
    a MagicMock leaking in from a test — is treated as absent rather than
    trusted. A bogus billing period is far worse than no period at all,
    so this never guesses.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        return datetime.fromtimestamp(int(value), tz=dt_timezone.utc)
    return None


def _stripe_get(obj, key):
    """Reads `key` off a StripeObject, a plain dict, or an attribute-only object."""
    if obj is None:
        return None
    getter = getattr(obj, "get", None)
    if callable(getter):
        try:
            return getter(key)
        except TypeError:
            pass
    return getattr(obj, key, None)


def extract_invoice_billing_period(invoice):
    """
    The billing period an invoice is paying FOR, as (start, end) datetimes,
    or (None, None) when the payload does not carry a usable one.

    Read from the invoice's LINE ITEMS rather than the invoice-level
    `period_start`/`period_end`. For a subscription renewal the line item's
    period is the new subscription cycle being charged, which is exactly
    what our billing_cycle_start/end should mirror; the invoice-level
    fields describe when invoice items were accumulated and are not
    reliably the same window. The invoice-level pair is kept only as a
    last-resort fallback.

    Where several lines carry periods (proration lines alongside the
    subscription line), the line covering the LONGEST span wins — a
    proration is by definition a fragment of a cycle, while the renewal
    line spans the whole one.

    Purely a parser: no API calls, no exceptions, safe inside a webhook.
    """
    best = None
    best_span = None

    lines = _stripe_get(invoice, "lines") or {}
    for line in _stripe_get(lines, "data") or []:
        period = _stripe_get(line, "period") or {}
        start = _coerce_stripe_timestamp(_stripe_get(period, "start"))
        end = _coerce_stripe_timestamp(_stripe_get(period, "end"))
        if start is None or end is None or end <= start:
            continue
        span = end - start
        if best_span is None or span > best_span:
            best, best_span = (start, end), span

    if best is not None:
        return best

    start = _coerce_stripe_timestamp(_stripe_get(invoice, "period_start"))
    end = _coerce_stripe_timestamp(_stripe_get(invoice, "period_end"))
    if start is not None and end is not None and end > start:
        return start, end

    return None, None


def extract_subscription_billing_period(stripe_subscription):
    """
    The CURRENT billing period of a Stripe Subscription, as (start, end)
    datetimes, or (None, None).

    Reads `items.data[].current_period_*` — Stripe moved these off the
    top-level Subscription object in API version 2025-03-31 and this
    project pins a release well past that — with the legacy top-level
    fields kept as a fallback so an older pinned API still works.

    Used after an interval-crossing plan change, where Stripe genuinely
    resets the billing anchor and the local cycle must mirror the new one.
    """
    items = _stripe_get(stripe_subscription, "items") or {}
    for item in _stripe_get(items, "data") or []:
        start = _coerce_stripe_timestamp(_stripe_get(item, "current_period_start"))
        end = _coerce_stripe_timestamp(_stripe_get(item, "current_period_end"))
        if start is not None and end is not None and end > start:
            return start, end

    start = _coerce_stripe_timestamp(
        _stripe_get(stripe_subscription, "current_period_start")
    )
    end = _coerce_stripe_timestamp(
        _stripe_get(stripe_subscription, "current_period_end")
    )
    if start is not None and end is not None and end > start:
        return start, end

    return None, None


def resolve_stripe_receipt_url(
    *,
    invoice=None,
    invoice_id=None,
    charge=None,
    charge_id=None,
    payment_intent_id=None,
):
    """
    Resolves the Stripe-hosted receipt/invoice link for a purchase, in
    priority order invoice -> charge -> payment_intent (mirrors
    BillingTransactionService._resolve_lookup's specificity ordering).

    Prefers an already-fetched `invoice`/`charge` object (zero extra API
    calls) over fetching by id. Never raises — a receipt link is a
    nice-to-have, not something that should break webhook processing or
    any surrounding business-logic transaction.
    """
    try:
        if invoice is not None:
            return invoice.get("hosted_invoice_url")
        if invoice_id:
            return stripe.Invoice.retrieve(invoice_id).get("hosted_invoice_url")

        if charge is not None:
            return charge.get("receipt_url")
        if charge_id:
            return stripe.Charge.retrieve(charge_id).get("receipt_url")

        if payment_intent_id:
            pi = stripe.PaymentIntent.retrieve(
                payment_intent_id, expand=["latest_charge"]
            )
            latest_charge = pi.get("latest_charge")
            return latest_charge.get("receipt_url") if latest_charge else None
    except stripe.error.StripeError as exc:
        logger.warning(
            "resolve_stripe_receipt_url failed (invoice_id=%s, charge_id=%s, "
            "payment_intent_id=%s): %s",
            invoice_id,
            charge_id,
            payment_intent_id,
            exc,
        )

    return None


class StripeCustomerService:
    """Get-or-create the Stripe Customer behind a CreditWallet."""

    @staticmethod
    def _qa_test_clock_kwargs(email, label=None) -> dict:
        """
        QA-only hook. Returns `{"test_clock": <id>}` when this environment
        is explicitly configured to put new customers on a Stripe Test
        Clock, and `{}` otherwise — which is the default everywhere, so
        production customer creation is byte-for-byte unchanged.

        This exists at CREATION time because Stripe allows attaching a
        Test Clock only when the Customer is created; there is no way to
        add one later. See billing/qa_time_travel.py for the guardrails.

        The import is local and ImportError-tolerant on purpose: that
        module advertises itself as deletable in one piece (the file, one
        urls.py line, and its settings flags) with zero impact on
        production code, and this hook must not be what breaks that.
        """
        try:
            from .qa_time_travel import new_customer_test_clock_kwargs
        except ImportError:
            return {}
        return new_customer_test_clock_kwargs(email, label)

    @staticmethod
    def get_or_create_customer(user) -> str:
        wallet, _ = CreditWallet.objects.get_or_create(user=user)
        if wallet.stripe_customer_id:
            return wallet.stripe_customer_id

        customer = stripe.Customer.create(
            email=user.email,
            name=user.get_full_name() or user.email,
            metadata={"user_id": str(user.id)},
            **StripeCustomerService._qa_test_clock_kwargs(
                user.email, label=f"QA time travel — user {user.email}"
            ),
        )
        wallet.stripe_customer_id = customer.id
        wallet.save(update_fields=["stripe_customer_id", "updated_at"])
        return customer.id

    @staticmethod
    def get_or_create_license_customer(license_sub, admin_user=None) -> str:
        """
        License-level equivalent of get_or_create_customer. Used both by
        offline-license overage purchases (lazily, on first attempt) and by
        the offline->Stripe conversion flow, which reuses whatever customer
        object overage purchases may have already created.
        """

        if license_sub.stripe_customer_id:
            return license_sub.stripe_customer_id

        contact = admin_user or license_sub.admin_user
        customer = stripe.Customer.create(
            email=contact.email,
            name=license_sub.school.name,
            metadata={
                "license_id": str(license_sub.id),
                "school_id": str(license_sub.school.id),
            },
            **StripeCustomerService._qa_test_clock_kwargs(
                contact.email,
                label=f"QA time travel — license {license_sub.school.name}",
            ),
        )
        license_sub.stripe_customer_id = customer.id
        license_sub.save(update_fields=["stripe_customer_id", "updated_at"])
        return customer.id

    @staticmethod
    def create_license_setup_intent(license_sub, admin_user):
        """
        Lets a school admin add a card to their license's Stripe customer
        WITHOUT a subscription driving it (no subscription exists yet for
        an offline license). The resulting default payment method is set
        via the setup_intent.succeeded webhook, not synchronously here —
        consistent with this codebase's rule that local/Stripe-side state
        changes should be webhook-confirmed rather than assumed from a
        client-reported success.

        set_as_default is explicitly "true" here (not left to the
        webhook's default) so handle_setup_intent_succeeded's branching
        logic is uniform across every SetupIntent origin — this flow's
        existing always-default behavior is preserved by making that
        choice explicit at creation time, not by an implicit fallback.
        """
        customer_id = StripeCustomerService.get_or_create_license_customer(
            license_sub, admin_user
        )
        return stripe.SetupIntent.create(
            customer=customer_id,
            payment_method_types=["card"],
            usage="off_session",
            metadata={"license_id": str(license_sub.id), "set_as_default": "true"},
        )

    @staticmethod
    def get_customer_for_request_user(user) -> str:
        """
        Resolves "the" Stripe customer for whoever is making a
        payment-methods request, based on their CURRENT billing context
        (a user has exactly one at a time — individual subscriber OR
        license admin, never both). Teachers never manage billing
        directly, so they (and anyone with no billing context at all)
        are rejected here rather than silently resolving to nothing.

        Raises:
            ValueError: if the user is a license teacher or has no
                billing context — callers should map this to 403.
        """
        context = resolve_user_billing_context(user)

        if context.source == SOURCE_INDIVIDUAL:
            return StripeCustomerService.get_or_create_customer(user)

        if context.source == SOURCE_LICENSE_ADMIN:
            return StripeCustomerService.get_or_create_license_customer(
                context.license_subscription, user
            )

        if context.source == SOURCE_LICENSE_TEACHER:
            raise ValueError(
                "Teachers don't manage billing directly — payment methods "
                "are managed by your school's license admin."
            )

        raise ValueError("No active subscription or license found for this account.")

    @staticmethod
    def create_setup_intent_for_request_user(user, set_as_default: bool = False):
        """
        General (non-license-specific) SetupIntent creator, used by the
        payment-methods "add a card" endpoint. Unlike
        create_license_setup_intent, set_as_default is caller-controlled
        — adding a card should NOT silently change the customer's
        default unless explicitly requested.
        """
        customer_id = StripeCustomerService.get_customer_for_request_user(user)
        return stripe.SetupIntent.create(
            customer=customer_id,
            payment_method_types=["card"],
            usage="off_session",
            metadata={"set_as_default": "true" if set_as_default else "false"},
        )

    _PORTAL_CONFIGURATION_CACHE_KEY = (
        "stripe_payment_methods_only_portal_configuration_id"
    )
    _PORTAL_CONFIGURATION_PURPOSE = "payment_methods_only"

    @staticmethod
    def _get_or_create_payment_methods_only_portal_configuration() -> str:
        """
        Stripe's Billing Portal is account-wide by default: whatever
        Configuration is marked is_default also exposes subscription
        cancel/pause/update controls (configured in the Stripe Dashboard,
        outside this codebase's control). Handing a portal session to a
        user WITHOUT pinning it to a locked-down Configuration would let
        them cancel or change plans directly on Stripe, bypassing every
        rule in SubscriptionManagementViewSet / LicenseSubscriptionViewSet
        (trial handling, proration, license seat limits, etc.) entirely.

        So every session this codebase creates is pinned to a dedicated
        Configuration with ONLY payment_method_update enabled — everything
        else (subscription_cancel, subscription_pause, subscription_update,
        customer_update) explicitly off. Lazily created once and reused
        (cached by ID) rather than requiring a manual one-time Dashboard
        setup step, mirroring this file's get_or_create_customer-style
        convention.
        """
        cached_id = cache.get(StripeCustomerService._PORTAL_CONFIGURATION_CACHE_KEY)
        if cached_id:
            return cached_id

        existing = stripe.billing_portal.Configuration.list(limit=100)
        for config in existing.auto_paging_iter():
            if (
                config.get("metadata", {}).get("purpose")
                == StripeCustomerService._PORTAL_CONFIGURATION_PURPOSE
            ):
                cache.set(
                    StripeCustomerService._PORTAL_CONFIGURATION_CACHE_KEY,
                    config.id,
                    timeout=None,
                )
                return config.id

        configuration = stripe.billing_portal.Configuration.create(
            business_profile={
                "headline": "Manage your payment methods",
            },
            features={
                "payment_method_update": {"enabled": True},
                "invoice_history": {"enabled": True},
                "customer_update": {"enabled": False},
                "subscription_cancel": {"enabled": False},
                "subscription_pause": {"enabled": False},
                "subscription_update": {"enabled": False},
            },
            metadata={"purpose": StripeCustomerService._PORTAL_CONFIGURATION_PURPOSE},
        )
        cache.set(
            StripeCustomerService._PORTAL_CONFIGURATION_CACHE_KEY,
            configuration.id,
            timeout=None,
        )
        return configuration.id

    @staticmethod
    def create_billing_portal_session_for_request_user(user, return_url: str):
        """
        Alternative to create_setup_intent_for_request_user: instead of this
        app driving card entry/list/delete/default itself via SetupIntent +
        Stripe Elements, this hands card management off to Stripe's own
        hosted Billing Portal. The customer resolution (individual vs.
        license admin vs. rejected teacher/no-context) is identical to
        get_customer_for_request_user — the only difference is what's done
        with the resolved customer_id once we have it.

        Deliberately pinned to the locked-down payment-methods-only
        Configuration (see _get_or_create_payment_methods_only_portal_configuration)
        AND deep-linked via flow_data straight into the payment_method_update
        flow — belt-and-suspenders so a user landing here can only ever
        manage cards, never subscriptions, regardless of what the account's
        default portal configuration happens to allow.
        """
        customer_id = StripeCustomerService.get_customer_for_request_user(user)
        configuration_id = (
            StripeCustomerService._get_or_create_payment_methods_only_portal_configuration()
        )
        return stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
            configuration=configuration_id,
            flow_data={"type": "payment_method_update"},
        )


class StripeCheckoutService:
    """
    Builds Stripe Checkout Sessions. Every session carries enough metadata
    for the webhook handler to reconstruct the local action it should take —
    we never trust client-supplied data after redirect, only what Stripe
    echoes back on the confirmed event.
    """

    @staticmethod
    def create_individual_checkout_session(user, plan, success_url, cancel_url):
        """
        Single Checkout Session builder for every case where Stripe does NOT yet
        have a chargeable subscription for this user, i.e. every situation where
        automatic charging is impossible and the user must be redirected to enter
        payment details:

          - brand new user who has never subscribed (no UserSubscription row at
            all, or a prior one that's since gone inactive)
          - a currently active free trial — regardless of whether trial_end has
            technically passed, since what matters here is whether the trial ROW
            is still is_active=True (i.e. the nightly expiry cleanup hasn't run
            yet). If it's still active, there's TRIAL-bucket state to finalize;
            if it's already been cleaned up, there's nothing to finalize and this
            behaves like a brand new signup.
          - an active PAID subscription with no stripe_subscription_id on file
            (e.g. a superadmin manual grant, or a Beta assignment) — locally
            "paid", but Stripe has no subscription to charge automatically, so
            it still has to go through checkout like a fresh signup.

        Snapshots which trial subscription (if any) needs to be finalized into
        Stripe metadata, so the webhook handler doesn't have to re-derive
        "was there a trial" from scratch and risk resolving differently than
        what was true at the moment the user clicked "subscribe" (e.g. if the
        trial gets cleaned up by Celery in the few seconds between session
        creation and the user completing payment on Stripe's page — see
        _handle_individual_checkout for how that race is still handled safely
        even so).

        """
        if plan.category != PlanCategory.INDIVIDUAL:
            raise ValueError("Checkout is only valid for INDIVIDUAL plans.")
        if not plan.stripe_price_id:
            raise ValueError(f"Plan {plan.name} has no stripe_price_id configured.")

        # Defensive guard: this method must never be used for a user who
        # already has a chargeable Stripe subscription — that case belongs to
        # StripeSubscriptionMutationService.change_plan() /
        # SubscriptionService.schedule_downgrade() instead. This is a backstop
        # in case something other than IndividualPlanChangeService.select_plan
        # ever calls this directly; the routing decision itself is made there.
        has_chargeable_subscription = (
            UserSubscription.objects.filter(user=user, is_active=True, is_trial=False)
            .exclude(stripe_subscription_id__isnull=True)
            .exclude(stripe_subscription_id="")
            .exists()
        )
        if has_chargeable_subscription:
            raise ValueError(
                "User already has a chargeable active subscription. Use the "
                "upgrade/downgrade flow instead of checkout."
            )

        # Same shape of backstop for the other track: never open a Stripe
        # checkout for someone a school license already covers. The routing
        # decision is made in IndividualPlanChangeService.select_plan, but
        # this is the last point before money changes hands, and a license
        # holder charged for an individual plan gets nothing for it (access
        # resolves license-first).
        IndividualPlanChangeService._assert_not_on_the_license_track(user)

        customer_id = StripeCustomerService.get_or_create_customer(user)

        # Only an *active* trial is eligible for in-place finalize at webhook
        # time. Anything else (no trial at all, or one that already expired
        # and was cleaned up) results in a fresh activation instead — see
        # _handle_individual_checkout.
        trial_sub = UserSubscription.objects.filter(
            user=user, is_active=True, is_trial=True
        ).first()

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": plan.stripe_price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "flow": "individual_checkout",
                "user_id": str(user.id),
                "plan_id": str(plan.id),
                "trial_subscription_id": str(trial_sub.id) if trial_sub else "",
            },
        )

        logger.info(
            "Created unified individual checkout session for user %s -> plan %s "
            "(trial_subscription_id=%s). Checkout session: %s.",
            user.email,
            plan.name,
            trial_sub.id if trial_sub else None,
            session.id,
        )
        return session

    @staticmethod
    def create_license_session(
        school,
        plan,
        admin_user,
        contract_months,
        max_seats,
        teacher_emails,
        custom_price_cents,
        success_url,
        cancel_url,
        carry_forward_teachers=True,
    ):
        """
        License checkout. The LicenseSubscription row does NOT exist yet —
        it's created in the webhook handler after payment, mirroring the
        individual flow. Everything LicenseSubscriptionService.
        create_license_subscription() needs is round-tripped through
        session metadata (Stripe metadata values must be strings).

        NOTE per the agreed default: only call this for self-serve
        Pro/Power license tiers. Custom / is_contact_sales plans should be
        set up manually in the Stripe dashboard and the resulting
        stripe_subscription_id attached directly — don't route those
        through this method.
        """

        if plan.is_contact_sales:
            raise ValueError(
                f"Plan {plan.name} is contact-sales only and must be set up "
                "manually, not through self-serve checkout."
            )

        LicenseSubscriptionService.validate_license_plan(plan)
        # Resolve (or vet) the managing admin BEFORE creating the checkout
        # session: the resolved id is what gets stamped into session
        # metadata and read back by the webhook, and a school with no
        # eligible admin must fail here rather than after it has paid.
        admin_user = LicenseSubscriptionService.resolve_admin_user(school, admin_user)

        if custom_price_cents:
            line_item = {
                "price_data": {
                    "currency": "usd",
                    "product": plan.product_id,
                    "recurring": {
                        "interval": "month",
                        "interval_count": contract_months,
                    },
                    "unit_amount": custom_price_cents * contract_months,
                },
                "quantity": max_seats,
            }
        else:
            if not plan.stripe_price_id:
                raise ValueError(f"Plan {plan.name} has no stripe_price_id configured.")
            line_item = {"price": plan.stripe_price_id, "quantity": max_seats}

        # Reuse the school's existing Stripe customer if they've billed
        # before (e.g. recreating a license after a prior cancellation).
        existing_license = (
            LicenseSubscription.objects.filter(
                school=school, stripe_customer_id__isnull=False
            )
            .order_by("-created_at")
            .first()
        )

        session_kwargs = {
            "mode": "subscription",
            "line_items": [line_item],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": {
                "flow": "license_create",
                "school_id": str(school.id),
                "plan_id": str(plan.id),
                "admin_user_id": str(admin_user.id),
                "contract_months": str(contract_months),
                "max_seats": str(max_seats),
                "teacher_emails": ",".join(teacher_emails or []),
                "custom_price_cents": (
                    str(custom_price_cents) if custom_price_cents else ""
                ),
                "carry_forward_teachers": "true" if carry_forward_teachers else "false",
            },
        }
        if existing_license:
            session_kwargs["customer"] = existing_license.stripe_customer_id
        else:
            session_kwargs["customer_email"] = admin_user.email

        return stripe.checkout.Session.create(**session_kwargs)

    @staticmethod
    def create_license_conversion_session(
        license_sub, initiated_by, success_url, cancel_url
    ):
        """
        Converts an OFFLINE license to Stripe billing. Anchors Stripe's
        billing cycle to the license's EXISTING billing_cycle_end
        (proration_behavior='none') so the school is not double-charged
        for time it already paid for offline — the first real Stripe
        invoice fires at the old cycle end, not today.

        Requires billing_cycle_end to still be in the future: Stripe
        requires billing_cycle_anchor to be a future timestamp. If the
        existing cycle has already lapsed, renew offline first (or decide
        to bill immediately by choosing a different anchor — not handled
        here).
        """
        if license_sub.billing_method != LicenseBillingMethod.OFFLINE:
            raise ValueError("License is not offline; nothing to convert.")

        plan = license_sub.plan
        if plan.is_contact_sales:
            raise ValueError(
                f"Plan {plan.name} is contact-sales only and must be set up "
                "manually in Stripe."
            )

        now = timezone.now()
        if license_sub.billing_cycle_end <= now:
            raise ValueError(
                "This license's current billing cycle has already ended. "
                "Renew it offline first, then convert to Stripe so the new "
                "cycle end can be used as the billing anchor."
            )

        customer_id = StripeCustomerService.get_or_create_license_customer(
            license_sub, license_sub.admin_user
        )

        if license_sub.custom_price_cents:
            line_item = {
                "price_data": {
                    "currency": "usd",
                    "product": plan.product_id,
                    "recurring": {
                        "interval": "month",
                        "interval_count": license_sub.contract_months,
                    },
                    "unit_amount": int(
                        license_sub.custom_price_cents * license_sub.contract_months
                    ),
                },
                "quantity": license_sub.max_seats,
            }
        else:
            if not plan.stripe_price_id:
                raise ValueError(f"Plan {plan.name} has no stripe_price_id configured.")
            line_item = {
                "price": plan.stripe_price_id,
                "quantity": license_sub.max_seats,
            }

        anchor_timestamp = int(license_sub.billing_cycle_end.timestamp())

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[line_item],
            subscription_data={
                "billing_cycle_anchor": anchor_timestamp,
                "proration_behavior": "none",
            },
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "flow": "license_convert_to_stripe",
                "license_id": str(license_sub.id),
                "initiated_by_user_id": str(initiated_by.id) if initiated_by else "",
            },
        )

        logger.info(
            "Created license conversion checkout session for license %s "
            "(anchor: %s). Checkout session: %s",
            license_sub.id,
            license_sub.billing_cycle_end.isoformat(),
            session.id,
        )
        return session


class SubscriptionExpiredDuringRequest(ValueError):
    """
    Raised by SubscriptionReactivationService.reactivate_if_cancelling when
    the subscription row is gone/inactive, or its billing cycle has ended,
    by the time the locked re-check runs — i.e. it was superseded by a
    genuine renewal or expiry that completed during the Stripe round trip.
    A ValueError subclass so callers that don't care about this specific
    race (e.g. IndividualPlanChangeService.select_plan) can treat it like
    any other rejection via a plain `except ValueError`, while callers that
    DO need to distinguish it (SubscriptionManagementViewSet.resume, to
    reproduce its existing "already_renewed" response) can catch it
    specifically.
    """


@dataclass(frozen=True)
class ReactivationResult:
    changed: bool
    stripe_changed: bool
    local_changed: bool
    warnings: list


class SubscriptionReactivationService:
    """
    Shared core of "undo a subscription's scheduled cancellation" —
    used by both SubscriptionManagementViewSet.resume (an explicit,
    standalone action) and IndividualPlanChangeService.select_plan (which
    auto-resumes a cancelling subscription on the user's behalf before
    applying a plan change, rather than making the user call resume first).

    Deliberately does NOT acquire the `billing:planchange:{user_id}` cache
    lock itself — both current callers already hold it before calling in.
    """

    @staticmethod
    def reactivate_if_cancelling(user_sub, now=None):
        """
        Args:
            user_sub (UserSubscription): the caller's current active
                subscription row.
            now (datetime | None): defaults to timezone.now() — accepted as
                a parameter so callers that already computed `now` for an
                earlier check (e.g. resume's pre-lock billing_cycle_end
                check) can pass the SAME value through, rather than a
                second, slightly later one.

        Returns:
            ReactivationResult

        Raises:
            ValueError: Stripe API failure, subscription fully canceled on
                Stripe's side, or (only reachable when cancel_at_period_end
                is True) the current billing period has already ended.
            SubscriptionExpiredDuringRequest: the subscription was
                superseded (renewed or expired) during the Stripe round
                trip — a ValueError subclass, see its docstring.
        """
        if now is None:
            now = timezone.now()

        if not user_sub.stripe_subscription_id:
            return ReactivationResult(
                changed=False, stripe_changed=False, local_changed=False, warnings=[]
            )

        try:
            stripe_sub = stripe.Subscription.retrieve(user_sub.stripe_subscription_id)
        except stripe.error.StripeError as exc:
            raise ValueError(
                "Could not verify your subscription with our payment provider: "
                + describe_stripe_error(exc, fallback_message="Please try again.")
            ) from exc

        stripe_status_value = stripe_sub.get("status")
        if stripe_status_value in ("canceled", "incomplete_expired"):
            raise ValueError(
                "This subscription has already been fully canceled with "
                "our payment provider and cannot be resumed. Please "
                "subscribe again via select-plan."
            )

        warnings = []
        if stripe_status_value == StripeSubscriptionStatus.PAST_DUE.lower():  # type: ignore[attr-defined]
            warnings.append(
                "This subscription has an outstanding payment issue that "
                "resuming does not resolve on its own — the next renewal "
                "invoice will need to succeed for service to continue "
                "uninterrupted."
            )

        stripe_changed = False
        local_changed = False

        if stripe_sub.get("cancel_at_period_end", False):
            if now >= user_sub.billing_cycle_end:
                raise ValueError(
                    "This subscription's current billing period has "
                    "already ended. Please subscribe again via select-plan."
                )

            try:
                stripe.Subscription.modify(
                    user_sub.stripe_subscription_id,
                    cancel_at_period_end=False,
                )
                stripe_changed = True
            except stripe.error.StripeError as exc:
                raise ValueError(
                    "Could not resume your subscription with our payment "
                    "provider: "
                    + describe_stripe_error(exc, fallback_message="Please try again.")
                ) from exc

            with transaction.atomic():
                locked_sub = (
                    UserSubscription.objects.select_for_update()
                    .filter(pk=user_sub.pk)
                    .first()
                )

                if (
                    not locked_sub
                    or not locked_sub.is_active
                    or now >= locked_sub.billing_cycle_end
                ):
                    raise SubscriptionExpiredDuringRequest(
                        "Your subscription ended during this request. "
                        "Please subscribe again via select-plan."
                    )

                local_update_fields = []
                if not locked_sub.is_trial and not locked_sub.auto_renew:
                    locked_sub.auto_renew = True
                    local_update_fields.append("auto_renew")
                if locked_sub.cancelled_at is not None:
                    # Cleared unconditionally (not gated on the auto_renew
                    # flip above) so a row whose auto_renew already drifted
                    # back to True still sheds its stale cancellation date —
                    # otherwise the frontend keeps rendering "you cancelled
                    # on ..." for a subscription that is renewing normally.
                    locked_sub.cancelled_at = None
                    local_update_fields.append("cancelled_at")

                if local_update_fields:
                    local_changed = True
                    try:
                        locked_sub.save(
                            update_fields=local_update_fields + ["updated_at"]
                        )
                    except Exception:
                        logger.exception(
                            "Reactivation: local save failed for "
                            "subscription %s after Stripe was already "
                            "updated. Attempting compensating revert.",
                            locked_sub.id,
                        )
                        try:
                            stripe.Subscription.modify(
                                user_sub.stripe_subscription_id,
                                cancel_at_period_end=True,
                            )
                        except stripe.error.StripeError:
                            logger.exception(
                                "Reactivation: compensating Stripe revert "
                                "ALSO failed for subscription %s "
                                "(stripe_subscription_id=%s). MANUAL "
                                "RECONCILIATION NEEDED — Stripe and local "
                                "state now disagree about renewal.",
                                locked_sub.id,
                                user_sub.stripe_subscription_id,
                            )
                        raise

        return ReactivationResult(
            changed=stripe_changed or local_changed,
            stripe_changed=stripe_changed,
            local_changed=local_changed,
            warnings=warnings,
        )


class StripeSubscriptionMutationService:
    """
    For an EXISTING Stripe subscription only (card already on file —
    no Checkout redirect needed). New subscribers go through
    StripeCheckoutService instead.
    """

    @staticmethod
    def change_plan(user_sub, new_plan, proration_behavior="always_invoice"):
        """
        Upgrades an existing paid subscription immediately via
        Stripe.Subscription.modify(). No Checkout redirect is needed since
        the customer's card is already on file.

        With proration_behavior="always_invoice", Stripe immediately
        creates and attempts to pay an invoice for the prorated difference
        as part of the modify() call. Credits for the new plan are granted
        ONLY if that invoice actually gets paid — otherwise the Stripe
        subscription item is reverted back to the old price, so Stripe and
        the app never disagree about which plan the user is actually
        paying for.

        Known limitation: if the proration charge requires 3D Secure
        authentication (payment_intent.status == "requires_action"), this
        method reverts the change and raises rather than completing the
        3DS flow inline. The user needs to retry after re-authenticating
        their payment method. Threading a requires_action response through
        this synchronous call would mean granting credits from a webhook
        instead, which risks double-granting against the synchronous
        success path below — deliberately avoided in favour of a simpler,
        always-consistent state machine. Worth revisiting if 3DS declines
        turn out to be common for your customer base.

        Downgrades should keep using SubscriptionService.schedule_downgrade()
        (deferred to cycle end) — sync_price() applies the Stripe-side price
        change for those, at renewal time, not this method.
        """
        if not user_sub.stripe_subscription_id:
            raise ValueError(
                "This subscription has no associated Stripe subscription. "
                "(it may have been granted manually). Contact support to upgrade."
            )
        if not new_plan.stripe_price_id:
            raise ValueError(f"Plan {new_plan.name} has no stripe_price_id configured.")

        old_plan = user_sub.plan
        stripe_subscription_id = user_sub.stripe_subscription_id

        # Interval-crossing changes (MONTHLY -> ANNUAL) genuinely reset
        # Stripe's billing_cycle_anchor as a side effect of the interval
        # change itself — activate_subscription()'s "reset from now"
        # semantics are correct there. A same-interval change (the common
        # case) does NOT move Stripe's anchor, so the local cycle dates
        # must be preserved instead — see apply_immediate_plan_change().
        is_interval_crossing = old_plan.interval != new_plan.interval

        if user_sub.stripe_schedule_id:
            StripeSubscriptionScheduleService.release_schedule(user_sub)

        try:
            stripe_sub = stripe.Subscription.retrieve(user_sub.stripe_subscription_id)
        except stripe.error.StripeError as exc:
            raise ValueError(
                "Could not retrieve Stripe subscription: "
                + describe_stripe_error(
                    exc, fallback_message="Please try again in a moment."
                )
            ) from exc

        item_id = stripe_sub["items"]["data"][0]["id"]
        old_price_id = stripe_sub["items"]["data"][0]["price"]["id"]

        try:
            stripe.Subscription.modify(
                user_sub.stripe_subscription_id,
                items=[{"id": item_id, "price": new_plan.stripe_price_id}],
                proration_behavior=proration_behavior,
            )
        except stripe.error.CardError as exc:
            # Declined synchronously during the modify call itself - rare
            # since the decline usually surfaces on the resulting invoice
            # instead, but Stripe can reject some cards immediately. Stripe
            # applies the subscription item change as part of the same
            # call that attempts payment, so the item swap may already be
            # live on Stripe's side even though this raised — best-effort
            # revert it back before surfacing the decline, exactly like
            # the invoice-status-based failure path below does.
            # _revert_to_previous_price already swallows/logs its own
            # StripeErrors internally rather than raising, so the ORIGINAL
            # decline reason below is always what reaches the caller.
            StripeSubscriptionMutationService._revert_to_previous_price(
                stripe_subscription_id, item_id, old_price_id, invoice=None
            )

            raise ValueError(
                "Card declined: "
                + describe_stripe_error(
                    exc, fallback_message="Please try a different payment method."
                )
            ) from exc
        except stripe.error.StripeError as exc:
            raise ValueError(
                "Stripe error while upgrading: "
                + describe_stripe_error(
                    exc, fallback_message="Please try again in a moment."
                )
            ) from exc

        # Re-retrieve to see the invoice Stripe generated as a side effect
        # of the price change. Only present when proration_behavior
        # actually creates one — e.g. no invoice is generated if the two
        # plans happen to be priced identically.
        stripe_sub_refreshed = stripe.Subscription.retrieve(stripe_subscription_id)
        latest_invoice_id = stripe_sub_refreshed.get("latest_invoice")
        invoice = None

        if latest_invoice_id:
            invoice = stripe.Invoice.retrieve(
                latest_invoice_id, expand=["payment_intent"]
            )

            if invoice.get("status") != "paid":
                # Payment didn't go through — declined, requires 3DS, etc.
                # Revert the subscription item so Stripe stops billing a
                # price the user never actually paid for, then raise.
                # Credits are NOT granted past this point.

                StripeSubscriptionMutationService._revert_to_previous_price(
                    stripe_subscription_id, item_id, old_price_id, invoice
                )

                payment_intent = invoice.get("payment_intent")

                pi_status = (
                    payment_intent.get("status")
                    if isinstance(payment_intent, dict)
                    else None
                )

                if pi_status == "requires_action":
                    raise ValueError(
                        "Upgrade payment requires additional authentication "
                        "(3D Secure) that can't be completed automatically here. "
                        "Please update your payment method and try again. "
                        "Your plan has not been changed."
                    )

                raise ValueError(
                    f"Upgrade payment failed (invoice status: {invoice['status']}). "
                    "Plan has not been changed."
                )

        # Payment succeeded (or no proration invoice was needed at all) —
        # grant credits for the new plan now. Interval-crossing changes
        # genuinely reset Stripe's billing cycle, so activate_subscription()
        # (which resets the local cycle to match) is correct there; a
        # same-interval change must preserve the existing cycle instead —
        # see apply_immediate_plan_change()'s docstring for why.
        if is_interval_crossing:
            updated_sub = SubscriptionService.activate_subscription(
                user_sub.user, new_plan
            )
        else:
            updated_sub = SubscriptionService.apply_immediate_plan_change(
                user_sub, new_plan
            )
        updated_sub.stripe_subscription_id = user_sub.stripe_subscription_id
        updated_sub.stripe_status = StripeSubscriptionStatus.ACTIVE
        updated_sub.save(
            update_fields=["stripe_subscription_id", "stripe_status", "updated_at"]
        )

        BillingTransactionService.record(
            source=BillingTransactionSource.INDIVIDUAL,
            transaction_type=BillingTransactionType.INDIVIDUAL_UPGRADE_CHARGE,
            status=BillingTransactionStatus.PAID,
            billing_method=BillingTransactionMethod.STRIPE,
            amount_cents=(invoice.get("amount_paid") if invoice else 0) or 0,
            currency=(invoice.get("currency") if invoice else "usd") or "usd",
            user=user_sub.user,
            user_subscription=updated_sub,
            stripe_invoice_id=latest_invoice_id,
            stripe_subscription_id=stripe_subscription_id,
            receipt_url=invoice.get("hosted_invoice_url") if invoice else None,
            description=f"Upgrade from {old_plan.name} to {new_plan.name}",
        )

        logger.info(
            "Upgraded subscription for user %s: %s -> %s (Stripe subscription %s).",
            user_sub.user.email,
            old_plan.name,
            new_plan.name,
            stripe_subscription_id,
        )
        return updated_sub

    @staticmethod
    def create_upgrade_checkout_session(user_sub, new_plan, success_url, cancel_url):
        """
        Previews the exact cost of upgrading `user_sub` to `new_plan` right
        now, then creates a one-time Checkout Session for that exact
        amount. The subscription's price is NOT changed yet — that only
        happens once payment is confirmed via
        `_handle_individual_upgrade_checkout_completed`.

        For a same-tier-or-up MONTHLY -> ANNUAL upgrade specifically, the
        preview itself is calculated with `proration_behavior="none"`
        instead of `"always_invoice"` — this correctly shows the FULL new
        annual price (no credit for unused monthly time), matching what
        Stripe will actually end up billing for an interval-crossing
        change regardless of what proration setting is used when the
        change is actually applied. Showing the customer an artificially
        small "delta" number here would be actively misleading for this
        specific transition.

        Args:
            user_sub (UserSubscription): The current active subscription,
                with `.plan` fresh (e.g. from
                IndividualPlanChangeService._determine_branch's row lock).
                The caller (IndividualPlanChangeService.select_plan) is
                expected to have already ensured this subscription isn't
                scheduled to cancel — via
                SubscriptionReactivationService.reactivate_if_cancelling —
                before calling in; this function does not itself check or
                clear cancel_at_period_end.
            new_plan (SubscriptionPlan): The plan being upgraded to.
            success_url (str): Where Checkout redirects on success.
            cancel_url (str): Where Checkout redirects if the user backs out.

        Returns:
            dict: either
                {"requires_checkout": True, "checkout_url": str, "checkout_session_id": str}
            or, when the previewed amount is <= 0:
                {"requires_checkout": False, "subscription": UserSubscription}
                (the upgrade has ALREADY been applied in this case — see
                _apply_upgrade_directly).

        Raises:
            ValueError: For missing Stripe configuration, or any Stripe
                API failure, with a clean user-facing message.
        """
        if not new_plan.stripe_price_id:
            raise ValueError(f"Plan {new_plan.name} has no stripe_price_id configured.")
        if not user_sub.stripe_subscription_id:
            raise ValueError(
                "This subscription has no associated Stripe subscription. "
                "(it may have been granted manually). Contact support to upgrade."
            )

        try:
            stripe_sub = stripe.Subscription.retrieve(user_sub.stripe_subscription_id)
        except stripe.error.StripeError as exc:
            raise ValueError(
                "Could not retrieve Stripe subscription: "
                + describe_stripe_error(
                    exc, fallback_message="Please try again in a moment."
                )
            ) from exc

        item_id = stripe_sub["items"]["data"][0]["id"]
        customer_id = stripe_sub["customer"]

        is_interval_crossing = (
            user_sub.plan.interval == BillingInterval.MONTHLY
            and new_plan.interval == BillingInterval.ANNUAL
        )
        preview_proration_behavior = (
            "none" if is_interval_crossing else "always_invoice"
        )
        # preview_proration_behavior = "always_invoice"

        try:
            preview = stripe.Invoice.create_preview(
                customer=customer_id,
                subscription=user_sub.stripe_subscription_id,
                subscription_details={
                    "items": [{"id": item_id, "price": new_plan.stripe_price_id}],
                    "proration_behavior": preview_proration_behavior,
                },
            )
        except stripe.error.StripeError as exc:
            raise ValueError(
                "Could not preview the upgrade cost: "
                + describe_stripe_error(
                    exc, fallback_message="Please try again in a moment."
                )
            ) from exc

        amount_due = preview["total"]

        if amount_due <= 0:
            # Nothing to charge -- e.g. a discounted higher tier that costs
            # the same or less than the credit already on the current plan.
            # Stripe's Checkout API requires a positive line-item amount
            # anyway, and there's no charge for the customer to explicitly
            # consent to here. Apply directly.
            updated_sub = StripeSubscriptionMutationService._apply_upgrade_directly(
                user_sub, new_plan, item_id
            )
            return {"requires_checkout": False, "subscription": updated_sub}

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="payment",
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {
                            "name": (
                                f"Upgrade to "
                                f"{new_plan.display_name or new_plan.name}"
                            ),
                        },
                        "unit_amount": amount_due,
                    },
                    "quantity": 1,
                }
            ],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "flow": "individual_upgrade_checkout",
                "user_id": str(user_sub.user_id),
                "user_subscription_id": str(user_sub.id),
                "new_plan_id": str(new_plan.id),
                "stripe_subscription_id": user_sub.stripe_subscription_id,
                "stripe_item_id": item_id,
                "proration_amount": str(amount_due),
            },
        )

        logger.info(
            "Created upgrade checkout session for user %s: %s -> %s, "
            "amount_due=%d cents. Checkout session: %s.",
            user_sub.user.email,
            user_sub.plan.name,
            new_plan.name,
            amount_due,
            session.id,
        )
        return {
            "requires_checkout": True,
            "checkout_url": session.url,
            "checkout_session_id": session.id,
        }

    @staticmethod
    def _apply_upgrade_directly(user_sub, new_plan, item_id):
        """
        Used only when create_upgrade_checkout_session's preview found
        nothing to charge (amount_due <= 0). Applies the plan swap
        immediately with no invoice — but STILL runs the interval-crossing
        safety net (see module docstring), since even a "nothing to
        charge" preview doesn't guarantee Stripe won't independently force
        a fresh full-price invoice as a side effect of an interval change
        (the preview and the live apply calculate differently for that
        specific case — belt and suspenders, cheap to check, never skipped).
        """
        is_interval_crossing = (
            user_sub.plan.interval == BillingInterval.MONTHLY
            and new_plan.interval == BillingInterval.ANNUAL
        )

        if user_sub.stripe_schedule_id:
            StripeSubscriptionScheduleService.release_schedule(user_sub)

        try:
            modified_sub = stripe.Subscription.modify(
                user_sub.stripe_subscription_id,
                items=[{"id": item_id, "price": new_plan.stripe_price_id}],
                proration_behavior="none",
            )
        except stripe.error.StripeError as exc:
            raise ValueError(
                "Could not apply the upgrade: "
                + describe_stripe_error(
                    exc, fallback_message="Please try again in a moment."
                )
            ) from exc

        if is_interval_crossing:
            StripeSubscriptionMutationService._void_or_refund_side_effect_invoice(
                user_sub.stripe_subscription_id
            )
            # Stripe genuinely resets the billing cycle anchor as part of
            # an interval change, so the local cycle must reset to match —
            # to the anchor Stripe just chose, read straight off the
            # response, not to our clock at the moment we got here.
            period_start, period_end = extract_subscription_billing_period(modified_sub)
            updated_sub = SubscriptionService.activate_subscription(
                user_sub.user,
                new_plan,
                period_start=period_start,
                period_end=period_end,
            )
        else:
            # Same-interval change: Stripe's anchor doesn't move, so the
            # local cycle must not either — see apply_immediate_plan_change().
            updated_sub = SubscriptionService.apply_immediate_plan_change(
                user_sub, new_plan
            )
        updated_sub.stripe_subscription_id = user_sub.stripe_subscription_id
        updated_sub.stripe_status = StripeSubscriptionStatus.ACTIVE
        updated_sub.save(
            update_fields=["stripe_subscription_id", "stripe_status", "updated_at"]
        )
        return updated_sub

    @staticmethod
    def _void_or_refund_side_effect_invoice(stripe_subscription_id):
        """
        Compensating control for the interval-crossing double-bill risk
        described in the module docstring. Call this IMMEDIATELY after any
        `Subscription.modify()` that changes a subscription's billing
        interval, once the customer has ALREADY paid the correct amount
        through a separate Checkout payment. Detects whatever invoice
        Stripe generated as a forced side effect of the interval reset and
        neutralizes it:
          - status == "paid": already collected -- refund it.
          - status == "open": not yet collected -- void it so it's never
            collected.
          - anything else (no invoice at all, or already voided/uncollectible):
            nothing to do.

        Logs at WARNING level whenever it actually has to act, since this
        represents a real (if brief) duplicate-charge attempt that's worth
        being able to audit later, even though it's fully compensated for.

        Failures here are logged at ERROR (not raised) — by this point the
        customer has already paid via Checkout and the plan has already
        been swapped; raising here would incorrectly fail an otherwise-
        successful request over a cleanup step. A failure here needs
        MANUAL reconciliation, flagged loudly in the log for that reason.
        """
        try:
            stripe_sub = stripe.Subscription.retrieve(stripe_subscription_id)
            latest_invoice_id = stripe_sub.get("latest_invoice")
            if not latest_invoice_id:
                return

            invoice = stripe.Invoice.retrieve(
                latest_invoice_id, expand=["payment_intent"]
            )
            status = invoice.get("status")

            if status == "paid":
                payment_intent = invoice.get("payment_intent")
                pi_id = (
                    payment_intent["id"]
                    if isinstance(payment_intent, dict)
                    else payment_intent
                )
                if pi_id:
                    # Idempotency key ties this refund to the specific
                    # PaymentIntent being neutralized: if this compensating
                    # control (or the replay_stripe_events command, or a
                    # retried request) ever runs twice for the same
                    # duplicate invoice, Stripe itself refuses to issue a
                    # second refund rather than relying solely on human
                    # judgment to catch it.
                    stripe.Refund.create(
                        payment_intent=pi_id,
                        idempotency_key=f"interval-change-refund-{pi_id}",
                    )
                    logger.warning(
                        "Refunded duplicate interval-change invoice %s "
                        "(PaymentIntent %s) for subscription %s — customer "
                        "already paid the equivalent amount via a separate "
                        "Checkout session.",
                        invoice["id"],
                        pi_id,
                        stripe_subscription_id,
                    )
            elif status == "open":
                stripe.Invoice.void_invoice(invoice["id"])
                logger.warning(
                    "Voided duplicate interval-change invoice %s for "
                    "subscription %s — customer already paid the "
                    "equivalent amount via a separate Checkout session.",
                    invoice["id"],
                    stripe_subscription_id,
                )
        except stripe.error.StripeError:
            logger.exception(
                "Failed to void/refund a potential duplicate invoice for "
                "subscription %s after an interval-crossing upgrade "
                "checkout. MANUAL RECONCILIATION NEEDED — check this "
                "subscription's invoices in the Stripe dashboard.",
                stripe_subscription_id,
            )

    @staticmethod
    def change_license_price(
        license_sub: LicenseSubscription,
        new_plan: SubscriptionPlan,
        new_custom_price_cents: Optional[int] = None,
        performed_by: Optional[CustomUser] = None,
    ) -> str:
        """
        Update the Stripe subscription price for a license.
        Uses smart proration: always_invoice for upgrades, none for downgrades.

        Args:
            license_sub: The license subscription.
            new_plan: The target plan (must have product_id and either stripe_price_id
                    or a custom price will be created).
            new_custom_price_cents: If provided, overrides the plan's default price for
                                    this license. If None, uses the plan's default stripe_price_id.

        Returns:
            str: The Stripe subscription ID (unchanged).

        Raises:
            ValueError: If Stripe update fails, payment fails, or invalid inputs.
        """
        if not license_sub.stripe_subscription_id:
            raise ValueError("License has no Stripe subscription ID.")

        # Determine old effective price (cents)
        old_price_cents = license_sub.custom_price_cents or license_sub.plan.price_cents

        # Determine new effective price
        if new_custom_price_cents is not None:
            new_price_cents = new_custom_price_cents
        else:
            # If no custom price, we must have stripe_price_id on the plan
            if not new_plan.stripe_price_id:
                raise ValueError(
                    f"Plan {new_plan.name} has no stripe_price_id and no custom price provided."
                )
            new_price_cents = new_plan.price_cents

        # If prices are identical, we can skip Stripe modification
        if old_price_cents == new_price_cents:
            logger.info(
                "License %s price unchanged (%d cents), skipping Stripe update.",
                license_sub.id,
                old_price_cents,
            )
            return license_sub.stripe_subscription_id

        # Determine proration behavior
        if new_price_cents > old_price_cents:
            proration_behavior = "always_invoice"  # upgrade
        else:
            proration_behavior = "none"  # downgrade

        # Get subscription item ID
        try:
            stripe_sub = stripe.Subscription.retrieve(
                license_sub.stripe_subscription_id
            )
        except stripe.error.StripeError as exc:
            raise ValueError(f"Could not retrieve Stripe subscription: {exc}") from exc

        items = stripe_sub.get("items", {}).get("data", [])
        if not items:
            raise ValueError("Stripe subscription has no items.")
        item_id = items[0]["id"]

        # Capture old price ID before modification
        old_price_id = items[0]["price"]["id"]

        # Determine the price ID to use
        if new_custom_price_cents is not None:
            # Create a custom Price for this license
            try:
                new_price_id = StripePriceService.create_custom_price(
                    product_id=new_plan.product_id,
                    unit_amount=int(
                        new_custom_price_cents * license_sub.contract_months
                    ),
                    interval_count=license_sub.contract_months,  # Deepseek
                )
            except ValueError as exc:
                raise ValueError(f"Custom price creation failed: {exc}") from exc
        else:
            if license_sub.contract_months == 1:
                if not new_plan.stripe_price_id:
                    raise ValueError(f"Plan {new_plan.name} has no stripe_price_id")
                new_price_id = new_plan.stripe_price_id
            else:
                new_price_id = StripePriceService.create_custom_price(
                    product_id=new_plan.product_id,
                    unit_amount=int(new_plan.price_cents * license_sub.contract_months),
                    interval_count=license_sub.contract_months,
                )

        # Perform the subscription modification
        try:
            stripe.Subscription.modify(
                license_sub.stripe_subscription_id,
                items=[{"id": item_id, "price": new_price_id}],
                proration_behavior=proration_behavior,
            )
        except stripe.error.CardError as exc:
            raise ValueError(f"Card declined: {exc}") from exc
        except stripe.error.StripeError as exc:
            raise ValueError(f"Stripe error: {exc}") from exc

        # For always_invoice, verify the invoice was paid
        if proration_behavior == "always_invoice":
            stripe_sub_refreshed = stripe.Subscription.retrieve(
                license_sub.stripe_subscription_id
            )
            latest_invoice_id = stripe_sub_refreshed.get("latest_invoice")
            if latest_invoice_id:
                invoice = stripe.Invoice.retrieve(
                    latest_invoice_id, expand=["payment_intent"]
                )
                if invoice.get("status") != "paid":
                    payment_intent = invoice.get("payment_intent")
                    pi_status = (
                        payment_intent.get("status")
                        if isinstance(payment_intent, dict)
                        else None
                    )
                    if pi_status == "requires_action":
                        # Revert the price back to old to be safe
                        # StripeSubscriptionMutationService._revert_license_price(
                        #     license_sub.stripe_subscription_id,
                        #     item_id,
                        #     old_price_cents,
                        #     license_sub.plan.product_id,
                        #     license_sub.custom_price_cents is not None,
                        # )
                        raise ValueError(
                            "Upgrade payment requires additional authentication (3D Secure). "
                            "Please update your payment method and retry."
                        )
                    # Revert and raise
                    # StripeSubscriptionMutationService._revert_license_price(
                    #     license_sub.stripe_subscription_id,
                    #     item_id,
                    #     old_price_cents,
                    #     license_sub.plan.product_id,
                    #     license_sub.custom_price_cents is not None,
                    # )

                    stripe.Subscription.modify(
                        license_sub.stripe_subscription_id,
                        items=[{"id": item_id, "price": old_price_id}],
                        proration_behavior="none",
                    )

                    raise ValueError(
                        f"Upgrade payment failed (invoice status: {invoice['status']}). "
                        "Plan has not been changed."
                    )

                BillingTransactionService.record(
                    source=BillingTransactionSource.LICENSE,
                    transaction_type=BillingTransactionType.LICENSE_PLAN_CHANGE_CHARGE,
                    status=BillingTransactionStatus.PAID,
                    billing_method=BillingTransactionMethod.STRIPE,
                    amount_cents=invoice.get("amount_paid") or 0,
                    currency=invoice.get("currency", "usd"),
                    license_subscription=license_sub,
                    stripe_invoice_id=latest_invoice_id,
                    stripe_subscription_id=license_sub.stripe_subscription_id,
                    receipt_url=invoice.get("hosted_invoice_url"),
                    performed_by=performed_by,
                    description=f"License plan change to {new_plan.name}",
                )

        logger.info(
            "License %s Stripe price updated: %d cents -> %d cents (proration: %s)",
            license_sub.id,
            old_price_cents,
            new_price_cents,
            proration_behavior,
        )
        return license_sub.stripe_subscription_id

    @staticmethod
    def _revert_to_previous_price(
        stripe_subscription_id, item_id, old_price_id, invoice
    ):
        """
        Best-effort rollback when a proration invoice didn't get paid: puts
        the Stripe subscription item back on the old price and voids the
        unpaid invoice so it doesn't linger as a dangling charge attempt.
        Logs (rather than raises) on failure here — the caller is already
        mid-error-handling for the original payment failure, and a failed
        rollback shouldn't mask that with a different exception. It does
        mean rare rollback failures need manual reconciliation in Stripe.

        `invoice` may be None — e.g. when Stripe declines the card
        synchronously during the modify() call itself, before any invoice
        object is available to the caller. In that case only the price is
        reverted; there's no invoice reference to void.
        """

        try:
            stripe.Subscription.modify(
                stripe_subscription_id,
                items=[{"id": item_id, "price": old_price_id}],
                proration_behavior="none",
            )
        except stripe.error.StripeError:
            logger.exception(
                "Failed to revert subscription %s to its previous price %s "
                "after a failed upgrade payment. Manual reconciliation needed.",
                stripe_subscription_id,
                old_price_id,
            )

        if invoice is None:
            return

        try:
            if invoice.get("status") == "open":
                stripe.Invoice.void_invoice(invoice["id"])
        except stripe.error.StripeError:
            logger.exception(
                "Failed to void unpaid upgrade invoice %s for subscription %s.",
                invoice.get("id"),
                stripe_subscription_id,
            )

    @staticmethod
    def sync_price(user_sub, stripe_subscription_id, proration_behavior="none"):
        """
        Called after every renewal to make sure Stripe is charging for
        whatever plan the renewal actually resolved to (plan, or
        pending_plan if a downgrade/upgrade was scheduled). The invoice that
        just succeeded was correctly billed at the OLD price — a scheduled
        change should only take effect going forward — so this only updates
        the subscription item for the NEXT cycle, with no proration, no-op
        if the price is already correct.
        """
        if not user_sub.plan.stripe_price_id:
            return

        stripe_sub = stripe.Subscription.retrieve(stripe_subscription_id)
        current_item = stripe_sub["items"]["data"][0]
        if current_item["price"]["id"] == user_sub.plan.stripe_price_id:
            return

        stripe.Subscription.modify(
            stripe_subscription_id,
            items=[{"id": current_item["id"], "price": user_sub.plan.stripe_price_id}],
            proration_behavior=proration_behavior,
        )
        logger.info(
            "Synced Stripe price for subscription %s to plan %s.",
            stripe_subscription_id,
            user_sub.plan.name,
        )


class StripeSubscriptionScheduleService:
    """
    Manages Stripe SubscriptionSchedules for deferred individual plan
    changes — downgrades, deferred upgrades (annual -> monthly), and
    lateral interval switches. This is what makes a "scheduled" change
    actually enforced on Stripe's side, rather than only tracked locally
    and reactively synced after Stripe has already billed the wrong price.
    See the module-level patch notes above for the full incident this
    fixes.
    """

    _SCHEDULE_CONFLICT_RE = re.compile(
        r"already attached to a schedule:\s*`(sub_sched_[A-Za-z0-9]+)`"
    )

    @staticmethod
    def schedule_plan_change_on_stripe(user_sub, new_plan):
        """
        Creates (or updates, if one already exists) a two-phase
        SubscriptionSchedule on `user_sub`'s Stripe subscription:

          Phase 1: `user_sub.plan`'s price, from the schedule's original
                   start date until `user_sub.billing_cycle_end`.
          Phase 2: `new_plan`'s price, starting at `billing_cycle_end`,
                   open-ended. `end_behavior="release"` means once this
                   phase is reached, Stripe hands the subscription back to
                   being a plain, directly-manageable Subscription — no
                   further schedule involvement needed unless another
                   change is scheduled later, which would reuse and update
                   this same schedule again (see the "already scheduled"
                   branch below).

        Both phases use `proration_behavior="none"`: phase 1 is a no-op
        continuation of what's already active (nothing to prorate), and
        phase 2 begins exactly at a natural period boundary — a full fresh
        period at the new price, not a partial/prorated one. This is
        precisely the point of deferring in the first place: nothing ever
        needs to be prorated, because the switch only ever happens at a
        clean boundary.

        Idempotent / re-callable: if `user_sub.stripe_schedule_id` is
        already set (an earlier scheduled change is being replaced with a
        different target), this UPDATES that existing schedule's phase 2
        rather than creating a second, conflicting one — Stripe only
        allows one active schedule per subscription, and attempting to
        create a second would fail outright.

        Args:
            user_sub (UserSubscription): The current active subscription,
                with `.plan` and `.billing_cycle_end` already correct (the
                caller is expected to have this fresh, e.g. from
                IndividualPlanChangeService._determine_branch's row lock).
                The caller (IndividualPlanChangeService.select_plan) is
                expected to have already ensured this subscription isn't
                scheduled to cancel — via
                SubscriptionReactivationService.reactivate_if_cancelling —
                before calling in; this function does not itself check or
                clear cancel_at_period_end.
            new_plan (SubscriptionPlan): The plan to switch to at cycle end.

        Returns:
            str: The Stripe SubscriptionSchedule ID (new or existing/updated).

        Raises:
            ValueError: If `new_plan` has no stripe_price_id configured, or
                if the Stripe API call fails for any other reason.
        """

        if not new_plan.stripe_price_id:
            raise ValueError(f"Plan {new_plan.name} has no stripe_price_id configured.")

        current_price_id = user_sub.plan.stripe_price_id
        if not current_price_id:
            raise ValueError(
                f"Current plan {user_sub.plan.name} has no stripe_price_id "
                f"configured — cannot build a schedule phase for it."
            )

        billing_cycle_end_ts = int(user_sub.billing_cycle_end.timestamp())

        if user_sub.stripe_schedule_id:
            schedule = None
            try:
                schedule = stripe.SubscriptionSchedule.retrieve(
                    user_sub.stripe_schedule_id
                )
            except stripe.error.InvalidRequestError:
                schedule = None

            if schedule and schedule.get("status") in ("not_started", "active"):
                phase_zero_start = schedule["phases"][0]["start_date"]
                try:
                    stripe.SubscriptionSchedule.modify(
                        user_sub.stripe_schedule_id,
                        end_behavior="release",
                        phases=[
                            {
                                "items": [{"price": current_price_id, "quantity": 1}],
                                "start_date": phase_zero_start,
                                "end_date": billing_cycle_end_ts,
                                "proration_behavior": "none",
                            },
                            {
                                "items": [
                                    {"price": new_plan.stripe_price_id, "quantity": 1}
                                ],
                                "start_date": billing_cycle_end_ts,
                                "proration_behavior": "none",
                                "billing_cycle_anchor": "phase_start",
                            },
                        ],
                    )
                except stripe.error.StripeError as exc:
                    raise ValueError(
                        "Could not update the existing scheduled change on "
                        "Stripe: "
                        + describe_stripe_error(
                            exc, fallback_message="Please try again in a moment."
                        )
                    ) from exc
                logger.info(
                    "Updated existing Stripe schedule %s for subscription "
                    "%s: phase 2 now %s, effective %s.",
                    user_sub.stripe_schedule_id,
                    user_sub.id,
                    new_plan.name,
                    user_sub.billing_cycle_end.isoformat(),
                )
                return user_sub.stripe_schedule_id
            # Existing reference is stale/terminal (released, completed,
            # canceled, or gone) — fall through to create a fresh one.

        try:
            return StripeSubscriptionScheduleService._create_fresh_schedule(
                user_sub, new_plan, current_price_id, billing_cycle_end_ts
            )
        except stripe.error.InvalidRequestError as exc:
            stale_schedule_id = (
                StripeSubscriptionScheduleService._extract_conflicting_schedule_id(exc)
            )
            if not stale_schedule_id:
                raise ValueError(
                    "Could not schedule the plan change on Stripe: "
                    + describe_stripe_error(
                        exc, fallback_message="Please try again in a moment."
                    )
                ) from exc

            logger.warning(
                "Subscription %s is attached to Stripe schedule %s that "
                "our local record (stripe_schedule_id=%r) didn't know "
                "about — releasing it and retrying schedule creation "
                "once. This points to a gap elsewhere that cleared "
                "stripe_schedule_id without releasing the Stripe-side "
                "schedule; worth investigating if this fires repeatedly.",
                user_sub.id,
                stale_schedule_id,
                user_sub.stripe_schedule_id,
            )

            try:
                stripe.SubscriptionSchedule.release(stale_schedule_id)
            except stripe.error.StripeError as release_exc:
                raise ValueError(
                    "Could not schedule the plan change on Stripe: a "
                    f"stale schedule ({stale_schedule_id}) is attached "
                    "and could not be released automatically: "
                    + describe_stripe_error(
                        release_exc,
                        fallback_message="Please try again in a moment.",
                    )
                ) from release_exc

            try:
                return StripeSubscriptionScheduleService._create_fresh_schedule(
                    user_sub, new_plan, current_price_id, billing_cycle_end_ts
                )
            except stripe.error.StripeError as retry_exc:
                raise ValueError(
                    "Could not schedule the plan change on Stripe even "
                    f"after releasing stale schedule {stale_schedule_id}: "
                    + describe_stripe_error(
                        retry_exc,
                        fallback_message="Please try again in a moment.",
                    )
                ) from retry_exc
        except stripe.error.StripeError as exc:
            raise ValueError(
                "Could not schedule the plan change on Stripe: "
                + describe_stripe_error(
                    exc, fallback_message="Please try again in a moment."
                )
            ) from exc

    @staticmethod
    def _create_fresh_schedule(
        user_sub, new_plan, current_price_id, billing_cycle_end_ts
    ):
        """
        Creates a brand-new two-phase SubscriptionSchedule from
        `user_sub`'s Stripe subscription and returns its ID. Factored out
        of schedule_plan_change_on_stripe so the stale-schedule retry
        path there can call this exact logic a second time without
        duplicating it.
        """
        schedule = stripe.SubscriptionSchedule.create(
            from_subscription=user_sub.stripe_subscription_id
        )
        stripe.SubscriptionSchedule.modify(
            schedule.id,
            end_behavior="release",
            phases=[
                {
                    "items": [{"price": current_price_id, "quantity": 1}],
                    "start_date": schedule["phases"][0]["start_date"],
                    "end_date": billing_cycle_end_ts,
                    "proration_behavior": "none",
                },
                {
                    "items": [{"price": new_plan.stripe_price_id, "quantity": 1}],
                    "start_date": billing_cycle_end_ts,
                    "proration_behavior": "none",
                    "billing_cycle_anchor": "phase_start",
                },
            ],
        )
        logger.info(
            "Created new Stripe schedule %s for subscription %s: %s "
            "until %s, then %s.",
            schedule.id,
            user_sub.id,
            user_sub.plan.name,
            user_sub.billing_cycle_end.isoformat(),
            new_plan.name,
        )
        return schedule.id

    @staticmethod
    def _extract_conflicting_schedule_id(exc):
        """
        Best-effort parse of Stripe's "already attached to a schedule"
        InvalidRequestError to pull out the conflicting schedule ID, e.g.
        from: "You cannot migrate a subscription that is already
        attached to a schedule: `sub_sched_1TweTwCsz2K1DeokqKVwQNTH`."
        Returns None if the message doesn't match this specific shape —
        callers should treat that as "not an error we know how to
        recover from" and re-raise normally rather than guess.
        """
        message = getattr(exc, "user_message", None) or str(exc)
        match = StripeSubscriptionScheduleService._SCHEDULE_CONFLICT_RE.search(message)
        return match.group(1) if match else None

    @staticmethod
    def release_schedule(user_sub):
        """
        Releases (cancels the scheduling of, without touching the
        underlying subscription) any active Stripe SubscriptionSchedule
        for `user_sub`. The subscription itself is left exactly as it
        currently is — whatever plan/price phase 1 has it on right now —
        so releasing correctly reverts "nothing changes, stay on your
        current plan" when a scheduled change is cancelled, and correctly
        clears the way for an immediate direct Subscription.modify() call
        when a scheduled change is superseded by an immediate one instead
        (Stripe explicitly documents that directly modifying a
        schedule-managed subscription's items can conflict with the
        schedule's own phase management, so this must always happen
        BEFORE any direct modify()).

        Safe/idempotent: does nothing if `user_sub.stripe_schedule_id` is
        not set, and treats "already released/invalid" as a no-op rather
        than an error — there's nothing left to release either way.

        Args:
            user_sub (UserSubscription): The subscription whose schedule
                should be released.

        Raises:
            ValueError: If the Stripe API call fails for a reason other
                than "already gone" (e.g. a genuine network/auth failure) —
                callers should NOT proceed to clear local state if this
                raises, so a failed release doesn't leave local state
                claiming "cancelled" while Stripe still executes the old
                transition.
        """
        if not user_sub.stripe_schedule_id:
            return

        try:
            stripe.SubscriptionSchedule.release(user_sub.stripe_schedule_id)
            logger.info(
                "Released Stripe schedule %s for subscription %s.",
                user_sub.stripe_schedule_id,
                user_sub.id,
            )
        except stripe.error.InvalidRequestError as exc:
            # Already released / completed / doesn't exist — nothing to do.
            logger.info(
                "Schedule %s for subscription %s already released or "
                "invalid, nothing to release: %s",
                user_sub.stripe_schedule_id,
                user_sub.id,
                exc,
            )
        except stripe.error.StripeError as exc:
            raise ValueError(
                "Could not release the existing scheduled change on "
                "Stripe: "
                + describe_stripe_error(
                    exc, fallback_message="Please try again in a moment."
                )
            ) from exc


class StripeOverageService:
    """Explicit, user-confirmed overage block purchases."""

    @staticmethod
    def create_overage_checkout_session(user, success_url, cancel_url, quantity):
        """
        Creates a one-time Checkout Session for purchasing ONE overage
        credit block. Nothing is granted until checkout.session.completed
        confirms payment (see _handle_overage_checkout_completed) — this
        replaces the old silent PaymentIntent-confirm flow
        (purchase_overage_block), which charged the customer's saved card
        without ever showing them the amount first.

        Validation mirrors purchase_overage_block exactly (active
        subscription required, plan must support overage, block cap not
        yet reached) — only HOW payment is collected changes. The cap is
        re-checked again at grant time under a row lock in the webhook
        handler, since a session being CREATED here doesn't reserve a slot
        — only a CONFIRMED payment should count against the cap.

        Args:
            user (CustomUser): The user purchasing overage credits.
            success_url (str): Where Checkout redirects on success.
            cancel_url (str): Where Checkout redirects if the user backs out.

        Returns:
            stripe.checkout.Session: the created Checkout Session (use
                `.url` and `.id` for the response).

        Raises:
            ValueError: For any validation failure or Stripe API error,
                with a clean user-facing message.
        """
        wallet, _ = CreditWallet.objects.get_or_create(user=user)

        user_sub = (
            user.subscriptions.filter(is_active=True).select_related("plan").first()
        )
        if not user_sub:
            raise ValueError("No active subscription found.")

        plan = user_sub.plan
        if plan.max_overage_blocks <= 0 or plan.overage_block_price <= 0:
            raise ValueError("This plan does not support overage credit purchases.")

        # Per-cycle cap: without this, plan.max_overage_blocks is decorative
        # (the request quantity is client-supplied and otherwise unbounded).
        # Re-checked at grant time too, since creating a session doesn't
        # reserve a slot against the cap.
        remaining_blocks = plan.max_overage_blocks - (wallet.overage_blocks_used or 0)
        if quantity > max(0, remaining_blocks):
            raise ValueError(
                f"This purchase would exceed your plan's overage limit of "
                f"{plan.max_overage_blocks} block(s) per cycle "
                f"({max(0, remaining_blocks)} remaining)."
            )

        customer_id = StripeCustomerService.get_or_create_customer(user)

        try:
            session = stripe.checkout.Session.create(
                customer=customer_id,
                mode="payment",
                line_items=[
                    {"price": plan.stripe_overage_price_id, "quantity": quantity},
                ],
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={
                    "flow": "overage_block_purchase_checkout",
                    "user_id": str(user.id),
                    "wallet_id": str(wallet.id),
                    "plan_id": str(plan.id),
                    "user_subscription_id": str(user_sub.id),
                    "quantity": str(quantity),
                },
            )
        except stripe.error.StripeError as exc:
            raise ValueError(
                "Could not start overage checkout: "
                + describe_stripe_error(
                    exc, fallback_message="Please try again in a moment."
                )
            ) from exc

        logger.info(
            "Created overage checkout session for user %s (plan %s, price "
            "%d cents). Checkout session: %s.",
            user.email,
            plan.name,
            plan.overage_block_price,
            session.id,
        )
        return session


class StripePriceService:
    @staticmethod
    def create_custom_price(
        product_id: str, unit_amount: int, interval_count: int
    ) -> str:
        """
        Create a new Price in Stripe for a custom amount with a specific interval count.
        interval_count: number of months between billing cycles (e.g., 9, 10, 12).
        """
        try:
            price = stripe.Price.create(
                product=product_id,
                unit_amount=unit_amount,
                currency="usd",
                recurring={
                    "interval": "month",
                    "interval_count": interval_count,
                },
            )
            return price.id
        except stripe.error.StripeError as exc:
            raise ValueError(f"Failed to create custom price: {exc}") from exc


class IndividualPlanChangeService:

    _LOCK_TIMEOUT_SECONDS = 30

    @staticmethod
    def _find_recommended_annual_plan(target_plan):
        """
        For the "upgrade_scheduled" case: finds the ANNUAL plan at the same
        tier as `target_plan` — the alternative that WOULD apply
        immediately, since it doesn't cross annual -> monthly. Returns None
        if no such plan exists in the catalog (e.g. a gap in plan setup) —
        callers fall back to generic phrasing / omit the structured field
        in that case, rather than erroring, since this is a "nice to have"
        recommendation, not a hard requirement for the deferred upgrade
        itself to proceed.
        """
        return (
            SubscriptionPlan.objects.filter(
                category=PlanCategory.INDIVIDUAL,
                tier=target_plan.tier,
                interval=BillingInterval.ANNUAL,
                is_active=True,
            )
            .exclude(id=target_plan.id)
            .first()
        )

    @staticmethod
    def _assert_not_on_the_license_track(user) -> None:
        """
        Refuse an individual plan purchase by someone their school's license
        already covers.

        The two tracks are deliberately separate, and the license side
        guards that separation carefully -- both _get_or_invite_teacher and
        _enroll_teacher_internal raise IndividualSubscriptionConflictError
        rather than let an individual subscriber be pulled onto a license.
        That guard was one-directional: nothing stopped the same person
        going the OTHER way and buying an individual plan while actively
        holding a license seat.

        This is not just a tidiness rule, it takes real money. Access is
        resolved license-first (see resolve_billing_context: an active
        allocation wins over any UserSubscription), so a license teacher who
        buys an individual plan is charged every month by Stripe for credits
        they can never spend -- their allocation answers every request. The
        first sign of it is a refund request.

        Scoped to ACTIVE allocations on ACTIVE licenses: a teacher who has
        been removed from their school's license, or whose school's license
        lapsed, is back on their own and must be able to subscribe.
        """
        allocation = (
            SchoolCreditAllocation.objects.filter(
                user=user,
                is_active=True,
                license_subscription__is_active=True,
            )
            .select_related("license_subscription__school")
            .first()
        )

        if not allocation:
            return

        school = allocation.license_subscription.school

        if allocation.is_admin_allocation:
            raise ValueError(
                f"Your account is the license administrator for "
                f"{school.name}, so it is already covered by that school's "
                "license. Individual plans are for personal accounts and "
                "cannot be combined with a school license."
            )

        raise ValueError(
            f"Your account is covered by {school.name}'s license, so an "
            "individual plan would be charged on top of a seat you already "
            "have. If you want a personal subscription as well, use a "
            "separate account on a personal email address."
        )

    @staticmethod
    def select_plan(user, target_plan, success_url=None, cancel_url=None):
        """
        Args:
            user (CustomUser): The user selecting a plan.
            target_plan (SubscriptionPlan): The plan being selected.
            success_url (str | None): Required only if the resolved action
                turns out to be "checkout".
            cancel_url (str | None): Required only if the resolved action
                turns out to be "checkout".

        Returns:
            dict: matches PlanChangeResultSerializer's shape.

        Raises:
            ValueError: For any business-rule rejection, including a
                failure to schedule/release the Stripe-side
                SubscriptionSchedule for a deferred change.
        """
        # Checked before the lock: a rejection here is about who the user is,
        # not about concurrent plan changes, so there is nothing to serialise.
        IndividualPlanChangeService._assert_not_on_the_license_track(user)

        lock_key = f"billing:planchange:{user.id}"
        if not cache.add(
            lock_key, "1", timeout=IndividualPlanChangeService._LOCK_TIMEOUT_SECONDS
        ):
            raise ValueError(
                "A plan change is already being processed for your account. "
                "Please wait a moment and try again."
            )

        try:
            branch, current_sub = IndividualPlanChangeService._determine_branch(
                user, target_plan
            )

            # Auto-resume: if the user's current subscription is scheduled
            # to cancel, undo that first rather than making them call
            # resume separately before picking a new plan. Scoped to the
            # branches that actually mutate an existing paid Stripe
            # subscription — "checkout" (no/trial/no-Stripe-id sub) and
            # "cancel_pending" (undoing a DIFFERENT pending change) never
            # have a cancelling subscription to reactivate.
            resumed_from_cancellation = False
            resume_warnings = []
            if branch in (
                "upgrade",
                "downgrade",
                "upgrade_scheduled",
                "lateral_scheduled",
            ):
                reactivation = SubscriptionReactivationService.reactivate_if_cancelling(
                    current_sub, now=timezone.now()
                )
                resumed_from_cancellation = reactivation.changed
                resume_warnings = reactivation.warnings

            def _with_resume_notice(message):
                if resumed_from_cancellation:
                    message = (
                        "We've undone the scheduled cancellation on your "
                        "subscription. " + message
                    )
                if resume_warnings:
                    message = message + " " + " ".join(resume_warnings)
                return message

            if branch == "cancel_pending":
                # Release Stripe's side FIRST. If this raises, we deliberately
                # do NOT proceed to clear local state — otherwise the user
                # would see "cancelled" while Stripe still executes the old
                # scheduled transition at cycle end.
                StripeSubscriptionScheduleService.release_schedule(current_sub)
                updated_sub = SubscriptionService.cancel_scheduled_plan_change(user)
                return {
                    "action": "scheduled_change_cancelled",
                    "message": (
                        f"Scheduled change cancelled. You'll stay on "
                        f"{updated_sub.plan.display_name or updated_sub.plan.name}."
                    ),
                    "subscription": updated_sub,
                }

            if branch == "downgrade":
                note = (
                    f"You'll move to "
                    f"{target_plan.display_name or target_plan.name} on "
                    f"{current_sub.billing_cycle_end.date().isoformat()}. "
                    f"You keep your current plan and credits until then."
                )
                schedule_id = (
                    StripeSubscriptionScheduleService.schedule_plan_change_on_stripe(
                        current_sub, target_plan
                    )
                )
                updated_sub = SubscriptionService.schedule_plan_change(
                    user,
                    target_plan,
                    PendingChangeType.DOWNGRADE,
                    note,
                    stripe_schedule_id=schedule_id,
                )
                return {
                    "action": "downgrade_scheduled",
                    "message": _with_resume_notice(note),
                    "pending_plan": target_plan,
                    "effective_date": updated_sub.billing_cycle_end,
                }

            if branch == "upgrade_scheduled":
                recommended_annual = (
                    IndividualPlanChangeService._find_recommended_annual_plan(
                        target_plan
                    )
                )
                note = IndividualPlanChangeService._build_deferred_upgrade_note(
                    current_sub, target_plan, recommended_annual
                )
                schedule_id = (
                    StripeSubscriptionScheduleService.schedule_plan_change_on_stripe(
                        current_sub, target_plan
                    )
                )
                updated_sub = SubscriptionService.schedule_plan_change(
                    user,
                    target_plan,
                    PendingChangeType.UPGRADE_DEFERRED,
                    note,
                    stripe_schedule_id=schedule_id,
                )
                return {
                    "action": "upgrade_scheduled",
                    "message": _with_resume_notice(note),
                    "pending_plan": target_plan,
                    "effective_date": updated_sub.billing_cycle_end,
                }

            if branch == "lateral_scheduled":
                note = IndividualPlanChangeService._build_lateral_change_note(
                    current_sub, target_plan
                )
                schedule_id = (
                    StripeSubscriptionScheduleService.schedule_plan_change_on_stripe(
                        current_sub, target_plan
                    )
                )
                updated_sub = SubscriptionService.schedule_plan_change(
                    user,
                    target_plan,
                    PendingChangeType.LATERAL_DEFERRED,
                    note,
                    stripe_schedule_id=schedule_id,
                )
                return {
                    "action": "lateral_change_scheduled",
                    "message": _with_resume_notice(note),
                    "pending_plan": target_plan,
                    "effective_date": updated_sub.billing_cycle_end,
                }

            if branch == "checkout":
                if not success_url or not cancel_url:
                    raise ValueError(
                        "success_url and cancel_url are required to start checkout."
                    )
                session = StripeCheckoutService.create_individual_checkout_session(
                    user=user,
                    plan=target_plan,
                    success_url=success_url,
                    cancel_url=cancel_url,
                )
                return {
                    "action": "checkout",
                    "message": (
                        "Redirecting to secure checkout to complete your "
                        "subscription."
                    ),
                    "checkout_url": session.url,
                    "checkout_session_id": session.id,
                }

            if branch == "upgrade":
                current_sub = UserSubscription.objects.select_related("plan").get(
                    id=current_sub.id
                )

                if not success_url or not cancel_url:
                    raise ValueError(
                        "success_url and cancel_url are required to complete an upgrade."
                    )

                result = (
                    StripeSubscriptionMutationService.create_upgrade_checkout_session(
                        current_sub, target_plan, success_url, cancel_url
                    )
                )

                if result["requires_checkout"]:
                    return {
                        "action": "upgrade_checkout",
                        "message": _with_resume_notice(
                            "Redirecting to secure checkout to confirm your upgrade "
                            "and the exact amount you'll be charged"
                        ),
                        "checkout_url": result["checkout_url"],
                        "checkout_session_id": result["checkout_session_id"],
                    }
                return {
                    "action": "upgraded",
                    "message": _with_resume_notice(
                        f"You've been upgraded to {target_plan.display_name or target_plan.name} "
                        f"No additional charge was needed right now."
                    ),
                    "subscription": result["subscription"],
                }
                # if current_sub.stripe_schedule_id:
                #     # A deferred change was previously scheduled but the
                #     # user is now choosing an immediate one instead — the
                #     # schedule must be released before directly modifying
                #     # the subscription's price, or the two can conflict on
                #     # Stripe's side. activate_subscription() (called inside
                #     # change_plan()) deactivates this row and creates a
                #     # fresh one with stripe_schedule_id=None, so no local
                #     # cleanup is needed here beyond the release itself.
                #     StripeSubscriptionScheduleService.release_schedule(current_sub)
                # updated_sub = StripeSubscriptionMutationService.change_plan(
                #     user_sub=current_sub, new_plan=target_plan
                # )
                # return {
                #     "action": "upgraded",
                #     "message": (
                #         f"You've been upgraded to "
                #         f"{target_plan.display_name or target_plan.name} and "
                #         f"charged the prorated difference immediately."
                #     ),
                #     "subscription": updated_sub,
                # }

            raise AssertionError(f"Unreachable branch: {branch!r}")  # pragma: no cover
        finally:
            cache.delete(lock_key)

    @staticmethod
    def _determine_branch(user, target_plan):
        """
        Read-only decision step, run under a row lock on the user's active
        subscription (if any) so concurrent calls for the same user
        serialize on the decision itself. Performs NO mutation and NO
        external calls — those happen afterward, outside this transaction,
        in select_plan().

        Returns:
            tuple[str, UserSubscription | None]: (branch, current_sub)
                where branch is one of "checkout", "upgrade", "downgrade",
                "upgrade_scheduled", "lateral_scheduled", "cancel_pending".

        Raises:
            ValueError: For business-rule rejections that stop here — past
                due, already on this plan with nothing pending, or an
                unranked (custom/contact-sales) tier on either side.
                Annual -> Monthly is deliberately NEVER a rejection here —
                see the class docstring.
        """
        with transaction.atomic():
            current_sub = (
                UserSubscription.objects.select_for_update()
                .filter(user=user, is_active=True)
                .select_related("plan")
                .first()
            )

            if current_sub is None:
                return "checkout", None

            if current_sub.is_trial:
                return "checkout", current_sub

            if not current_sub.stripe_subscription_id:
                return "checkout", current_sub

            if current_sub.stripe_status == StripeSubscriptionStatus.PAST_DUE:
                raise ValueError(
                    "Your current subscription has a payment issue. Please "
                    "update your payment method before changing plans."
                )

            if target_plan.id == current_sub.plan_id:
                if current_sub.pending_plan_id:
                    return "cancel_pending", current_sub
                raise ValueError("You are already subscribed to this plan.")

            if current_sub.plan.tier not in PLAN_TIER_HIERARCHY:
                raise ValueError(
                    "Your current plan is a custom/contact-sales plan and "
                    "can't be changed through self-serve plan selection. "
                    "Please contact support."
                )
            if target_plan.tier not in PLAN_TIER_HIERARCHY:
                raise ValueError(
                    "This plan isn't available for direct self-serve "
                    "switching right now. Please contact support."
                )

            current_rank = get_tier_rank(current_sub.plan.tier)
            target_rank = get_tier_rank(target_plan.tier)

            is_annual_to_monthly = (
                current_sub.plan.interval == BillingInterval.ANNUAL
                and target_plan.interval == BillingInterval.MONTHLY
            )

            if target_rank > current_rank:
                # Tier upgrade. Immediate UNLESS it crosses annual -> monthly,
                # in which case it's forced to defer — see class docstring
                # for why (Stripe's interval-crossing proration produces an
                # unrefunded credit balance rather than a clean charge).
                if is_annual_to_monthly:
                    return "upgrade_scheduled", current_sub
                return "upgrade", current_sub

            if target_rank < current_rank:
                # Tier downgrade. Always deferred regardless of interval —
                # this was already true before the annual->monthly
                # correction and doesn't need special-casing here.
                return "downgrade", current_sub

            # Equal tier rank.
            if (
                current_sub.plan.interval == BillingInterval.MONTHLY
                and target_plan.interval == BillingInterval.ANNUAL
            ):
                # Same-tier commitment increase: always immediate, never
                # blocked or deferred in this direction.
                return "upgrade", current_sub

            if is_annual_to_monthly:
                # Same tier, annual -> monthly: no tier change at all, but
                # still has to wait for the current annual term to end.
                return "lateral_scheduled", current_sub

            # Defensive fallback: same tier, some other interval combination
            # not covered above (e.g. BillingInterval.NONE on either side,
            # or two distinct plan rows with identical tier+interval — a
            # catalog configuration issue, not a normal user scenario).
            if target_plan.price_cents >= current_sub.plan.price_cents:
                return "upgrade", current_sub
            return "downgrade", current_sub

    @staticmethod
    def _build_deferred_upgrade_note(current_sub, target_plan, recommended_annual):
        """
        Composes the persisted, user-facing explanation for the
        "upgrade_scheduled" case: a genuine tier upgrade that can't apply
        immediately because it crosses from an annual plan to a monthly
        one. Explicitly tells the user their plan/features won't change
        yet, and recommends `recommended_annual` (if one was found by
        `_find_recommended_annual_plan`) as the immediate alternative.

        Args:
            current_sub (UserSubscription): The current active subscription.
            target_plan (SubscriptionPlan): The higher-tier monthly plan
                the user selected.
            recommended_annual (SubscriptionPlan | None): The equivalent
                annual plan at target_plan's tier, or None if the catalog
                has no such plan.
        """
        effective_date = current_sub.billing_cycle_end.date().isoformat()
        target_name = target_plan.display_name or target_plan.name

        if recommended_annual:
            recommendation_name = (
                recommended_annual.display_name or recommended_annual.name
            )
            recommendation = (
                f" If you'd like this to take effect right away, consider "
                f"{recommendation_name} instead — switching within annual "
                f"billing applies immediately."
            )
        else:
            recommendation = (
                " If you'd like this to take effect right away, consider "
                "staying on an annual plan at your new tier — switching "
                "within annual billing applies immediately."
            )

        return (
            f"You've selected {target_name}, which is a higher tier than "
            f"your current plan. Because you're currently on an annual "
            f"plan, this change can't apply immediately — your plan and "
            f"features will stay exactly as they are until your current "
            f"annual term ends on {effective_date}, at which point you'll "
            f"move to {target_name}." + recommendation
        )

    @staticmethod
    def _build_lateral_change_note(current_sub, target_plan):
        """
        Composes the persisted, user-facing explanation for the
        "lateral_scheduled" case: no tier change at all, just switching off
        annual billing at the current tier. Still has to wait for the
        current annual term to end.
        """
        effective_date = current_sub.billing_cycle_end.date().isoformat()
        target_name = target_plan.display_name or target_plan.name

        return (
            f"You've selected {target_name}. This doesn't change your plan "
            f"tier or features — it only switches your billing to monthly. "
            f"Since you're currently on an annual term, it still can't "
            f"take effect until that term ends on {effective_date}. Your "
            f"plan and features stay exactly the same until then."
        )


class StripeWebhookHandler:
    """
    What to do for each Stripe event type. Called from webhooks.py, which
    only handles signature verification + idempotency — no business logic
    there, so this stays testable without a fake HTTP request.
    """

    # ------------------------------------------------------------------
    # checkout.session.completed
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_invoice_subscription_id(invoice):
        """
        Returns the Stripe subscription ID an invoice belongs to, checking
        BOTH possible locations so this works regardless of which Stripe
        API version this account is on:

          - `invoice.subscription` — the field used in "classic" API
            versions (pre-2025-03-31 "basil").
          - `invoice.parent.subscription_details.subscription` — where
            this moved to as of API version 2025-03-31 and later, as part
            of Stripe's broader "parent" restructuring for invoicing
            objects.

        Returns None if neither location has a value (e.g. a genuinely
        standalone, non-subscription invoice).
        """
        subscription_id = invoice.get("subscription")
        if subscription_id:
            return subscription_id

        parent = invoice.get("parent") or {}
        subscription_details = parent.get("subscription_details") or {}
        return subscription_details.get("subscription")

    @staticmethod
    @transaction.atomic
    def handle_checkout_completed(session):
        metadata = session.get("metadata", {}) or {}
        flow = metadata.get("flow")

        if flow == "individual_checkout":
            StripeWebhookHandler._handle_individual_checkout(session, metadata)
        elif flow == "individual_upgrade_checkout":
            StripeWebhookHandler._handle_individual_upgrade_checkout_completed(
                session, metadata
            )
        elif flow == "license_overage_purchase_checkout":
            StripeWebhookHandler._handle_license_overage_checkout_completed(
                session, metadata
            )
        elif flow == "overage_block_purchase_checkout":
            StripeWebhookHandler._handle_overage_checkout_completed(session, metadata)
        elif flow == "individual_subscribe":
            # Retired flow — StripeCheckoutService.create_individual_subscribe_session
            # (the only thing that ever set this metadata) has been deleted; its
            # endpoint was removed even earlier. This branch is kept ONLY so that
            # manage.py replay_stripe_events can still correctly reprocess an old,
            # still-FAILED checkout.session.completed event from back when this
            # flow was live (it replays by re-calling handle_checkout_completed
            # with the stored payload, then this same metadata.flow dispatch
            # decides where it goes). Deleting this branch would make such a
            # replay silently no-op into the "else" warning below instead of
            # completing a real subscription a customer already paid for.
            StripeWebhookHandler._handle_individual_subscribe(session, metadata)
        elif flow == "trial_to_paid":
            # Retired flow, same reasoning as individual_subscribe above —
            # StripeCheckoutService.create_trial_to_paid_session has been
            # deleted (its only caller was already commented out), but this
            # dispatch branch and _handle_trial_to_paid stay for
            # replay_stripe_events to be able to reprocess an old FAILED event.
            StripeWebhookHandler._handle_trial_to_paid(session, metadata)
        elif flow == "license_create":
            StripeWebhookHandler._handle_license_create(session, metadata)
        elif flow == "license_convert_to_stripe":
            StripeWebhookHandler._handle_license_convert_to_stripe(session, metadata)
        else:
            logger.warning(
                "checkout.session.completed with unrecognized flow metadata: %r", flow
            )

    @staticmethod
    def _handle_individual_checkout(session, metadata):
        user = CustomUser.objects.get(id=metadata["user_id"])
        plan = SubscriptionPlan.objects.get(id=metadata["plan_id"])
        trial_subscription_id = metadata.get("trial_subscription_id") or None

        trial_sub = None
        if trial_subscription_id:
            trial_sub = (
                UserSubscription.objects.select_for_update()
                .filter(id=trial_subscription_id, is_active=True, is_trial=True)
                .first()
            )

        if trial_sub:
            updated_sub = SubscriptionService.finalize_trial_to_paid_conversion(
                trial_sub=trial_sub,
                new_plan=plan,
                stripe_subscription_id=session["subscription"],
            )

            BillingTransactionService.record(
                source=BillingTransactionSource.INDIVIDUAL,
                transaction_type=BillingTransactionType.INDIVIDUAL_TRIAL_CONVERSION_CHARGE,
                status=BillingTransactionStatus.PAID,
                billing_method=BillingTransactionMethod.STRIPE,
                amount_cents=session.get("amount_total") or 0,
                currency=session.get("currency", "usd"),
                user=user,
                user_subscription=updated_sub,
                stripe_invoice_id=session.get("invoice"),
                stripe_checkout_session_id=session.get("id"),
                stripe_subscription_id=session.get("subscription"),
                receipt_url=resolve_stripe_receipt_url(
                    invoice_id=session.get("invoice"),
                    payment_intent_id=session.get("payment_intent"),
                ),
                description=f"Trial converted to {plan.display_name or plan.name}",
            )

            logger.info(
                "Checkout completed: trial %s finalized to paid plan %s for "
                "user %s. Stripe subscription: %s.",
                trial_sub.id,
                plan.name,
                user.email,
                session["subscription"],
            )
            return

        already_active_same_plan = UserSubscription.objects.filter(
            user=user,
            is_active=True,
            plan=plan,
            stripe_subscription_id=session["subscription"],
        ).exists()
        if already_active_same_plan:
            logger.info(
                "individual_checkout webhook for user %s already applied "
                "(duplicate delivery for session %s) — skipping.",
                user.email,
                session["id"],
            )
            return

        subscription = SubscriptionService.activate_subscription(user, plan)
        subscription.stripe_subscription_id = session["subscription"]
        subscription.stripe_status = StripeSubscriptionStatus.ACTIVE
        subscription.save(
            update_fields=["stripe_subscription_id", "stripe_status", "updated_at"]
        )

        BillingTransactionService.record(
            source=BillingTransactionSource.INDIVIDUAL,
            transaction_type=BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE,
            status=BillingTransactionStatus.PAID,
            billing_method=BillingTransactionMethod.STRIPE,
            amount_cents=session.get("amount_total") or 0,
            currency=session.get("currency", "usd"),
            user=user,
            user_subscription=subscription,
            stripe_invoice_id=session.get("invoice"),
            stripe_checkout_session_id=session.get("id"),
            stripe_subscription_id=session.get("subscription"),
            receipt_url=resolve_stripe_receipt_url(
                invoice_id=session.get("invoice"),
                payment_intent_id=session.get("payment_intent"),
            ),
            description=f"New subscription — {plan.display_name or plan.name}",
        )

        logger.info(
            "Checkout completed: fresh activation of plan %s for user %s "
            "(no trial to finalize). Stripe subscription: %s.",
            plan.name,
            user.email,
            session["subscription"],
        )

    @staticmethod
    def _handle_overage_checkout_completed(session, metadata):
        """
        Handles flow='overage_block_purchase_checkout' — grants the
        overage credit bucket ONLY after checkout.session.completed
        confirms payment.

        Re-validates the block cap under a row lock at GRANT time, not
        just at session-creation time in create_overage_checkout_session —
        a session being created doesn't reserve a slot against the cap;
        only a confirmed payment should count. This protects against a
        user opening more checkout sessions than their remaining cap
        allows and completing more than one before the cap would
        otherwise be enforced.

        Deliberately not decorated with its own @transaction.atomic — like
        every other flow handler dispatched from handle_checkout_completed,
        it runs inside that method's outer atomic block.
        """
        wallet = CreditWallet.objects.select_for_update().get(id=metadata["wallet_id"])
        plan = SubscriptionPlan.objects.get(id=metadata["plan_id"])
        quantity = int(metadata["quantity"])

        if (wallet.overage_blocks_used or 0) + quantity > plan.max_overage_blocks:
            logger.error(
                "Overage checkout session %s completed for wallet %s but "
                "granting %d more block(s) would exceed the cap (%d used of "
                "%d) — credits NOT granted. Needs manual reconciliation "
                "(refund the payment via the Stripe dashboard).",
                session["id"],
                wallet.id,
                quantity,
                wallet.overage_blocks_used,
                plan.max_overage_blocks,
            )

            BillingTransactionService.record(
                source=BillingTransactionSource.INDIVIDUAL,
                transaction_type=BillingTransactionType.INDIVIDUAL_OVERAGE_PURCHASE,
                status=BillingTransactionStatus.PAID,
                billing_method=BillingTransactionMethod.STRIPE,
                amount_cents=session.get("amount_total") or 0,
                currency=session.get("currency", "usd"),
                user=wallet.user,
                stripe_invoice_id=session.get("invoice"),
                stripe_payment_intent_id=session.get("payment_intent"),
                stripe_checkout_session_id=session.get("id"),
                description=(
                    "Overage purchase paid but block cap already reached at "
                    "grant time — credits NOT granted, needs manual refund."
                ),
            )

            return

        SubscriptionService.grant_overage_bucket(
            wallet=wallet,
            plan=plan,
            quantity=quantity,
            stripe_payment_intent_id=session.get("payment_intent"),
        )

        BillingTransactionService.record(
            source=BillingTransactionSource.INDIVIDUAL,
            transaction_type=BillingTransactionType.INDIVIDUAL_OVERAGE_PURCHASE,
            status=BillingTransactionStatus.PAID,
            billing_method=BillingTransactionMethod.STRIPE,
            amount_cents=session.get("amount_total") or 0,
            currency=session.get("currency", "usd"),
            user=wallet.user,
            stripe_invoice_id=session.get("invoice"),
            stripe_payment_intent_id=session.get("payment_intent"),
            stripe_checkout_session_id=session.get("id"),
            receipt_url=resolve_stripe_receipt_url(
                invoice_id=session.get("invoice"),
                payment_intent_id=session.get("payment_intent"),
            ),
            description=(
                f"Overage purchase — "
                f"{quantity * plan.display_overage_block_size:,} AI credit(s)"
            ),
        )

        logger.info(
            "Overage checkout completed for wallet %s: granted %s block(s) of plan %s.",
            wallet.id,
            quantity,
            plan.name,
        )

    @staticmethod
    def _handle_license_overage_checkout_completed(session, metadata):
        """
        Handles flow='license_overage_purchase_checkout' — fulfills a
        LicenseOveragePurchaseIntent ONLY after Stripe confirms payment.

        Deliberately NOT its own @transaction.atomic — runs inside
        handle_checkout_completed's outer atomic block, same as every
        other flow dispatched from there. If anything below raises, the
        whole thing rolls back and the outer webhook dispatcher (see
        webhooks.py) deletes the StripeEvent record so Stripe's retry is
        treated as fresh, not a duplicate — the intent will still be
        PENDING on retry, so this is safely self-healing.
        """
        intent_id = metadata.get("intent_id")
        if not intent_id:
            logger.error(
                "license_overage_purchase_checkout session %s has no "
                "intent_id in metadata — cannot fulfill. Needs manual "
                "reconciliation.",
                session.get("id"),
            )
            return

        try:
            intent = (
                LicenseOveragePurchaseIntent.objects.select_for_update()
                .select_related(
                    "license_subscription", "license_subscription__plan", "initiated_by"
                )
                .get(id=intent_id)
            )
        except LicenseOveragePurchaseIntent.DoesNotExist:
            logger.error(
                "license_overage_purchase_checkout session %s references "
                "intent %s which does not exist — cannot fulfill. Needs "
                "manual reconciliation (refund via Stripe dashboard).",
                session.get("id"),
                intent_id,
            )
            return

        # Idempotency belt-and-suspenders: StripeEvent already blocks a
        # duplicate delivery of this exact event, but this guards against
        # any other path re-invoking this handler for the same intent.
        if intent.status == LicenseOveragePurchaseStatus.COMPLETED:
            logger.info(
                "Overage purchase intent %s already completed — skipping "
                "duplicate fulfillment (session %s).",
                intent.id,
                session.get("id"),
            )
            return

        payment_status = session.get("payment_status")
        if payment_status != "paid":
            # Delayed/async payment methods (bank debits, etc.) aren't
            # supported by this flow — this codebase doesn't handle
            # checkout.session.async_payment_succeeded anywhere else
            # either. Flag loudly rather than granting on unconfirmed
            # payment.
            logger.error(
                "license_overage_purchase_checkout session %s completed "
                "with payment_status=%r (not 'paid') for intent %s — "
                "credits NOT granted. Async payment methods are not "
                "supported by this flow. Needs manual reconciliation.",
                session.get("id"),
                payment_status,
                intent.id,
            )
            intent.status = LicenseOveragePurchaseStatus.FAILED
            intent.stripe_payment_intent_id = session.get("payment_intent")
            intent.failure_reason = (
                f"Unsupported payment_status at fulfillment: {payment_status!r}"
            )
            intent.save(
                update_fields=[
                    "status",
                    "stripe_payment_intent_id",
                    "failure_reason",
                    "updated_at",
                ]
            )
            return

        license_sub = LicenseSubscription.objects.select_for_update().get(
            pk=intent.license_subscription_id
        )

        if not license_sub.is_active:
            logger.error(
                "Overage purchase intent %s paid (session %s) but license "
                "%s is no longer active — credits NOT granted. Needs "
                "manual refund via the Stripe dashboard.",
                intent.id,
                session.get("id"),
                license_sub.id,
            )
            intent.status = LicenseOveragePurchaseStatus.FAILED
            intent.stripe_payment_intent_id = session.get("payment_intent")
            intent.failure_reason = "License no longer active at fulfillment time."
            intent.save(
                update_fields=[
                    "status",
                    "stripe_payment_intent_id",
                    "failure_reason",
                    "updated_at",
                ]
            )
            BillingTransactionService.record(
                source=BillingTransactionSource.LICENSE,
                transaction_type=BillingTransactionType.LICENSE_OVERAGE_PURCHASE,
                status=BillingTransactionStatus.PAID,
                billing_method=BillingTransactionMethod.STRIPE,
                amount_cents=session.get("amount_total") or intent.amount_cents,
                currency=session.get("currency", "usd"),
                license_subscription=license_sub,
                stripe_payment_intent_id=session.get("payment_intent"),
                stripe_checkout_session_id=session.get("id"),
                receipt_url=resolve_stripe_receipt_url(
                    payment_intent_id=session.get("payment_intent")
                ),
                performed_by=intent.initiated_by,
                description=(
                    f"Overage purchase "
                    f"({intent.total_blocks * (intent.block_size_snapshot // CONVERSION_FACTOR):,} "
                    f"AI credit(s)) paid but license was inactive at fulfillment "
                    f"time — credits NOT granted, needs manual refund."
                ),
            )
            return

        # Re-validate every teacher is STILL active — one may have been
        # removed between checkout creation and payment completion. Grant
        # to whoever is still valid; explicitly flag anyone skipped rather
        # than silently dropping them — a partial refund is a business
        # decision, not one this code makes unilaterally.
        teacher_ids = list(intent.allocations.keys())
        active_allocations = {
            str(a.user_id): a
            for a in SchoolCreditAllocation.objects.select_for_update()
            .filter(
                LicenseSubscriptionService._overage_eligible_allocations_q(license_sub),
                license_subscription=license_sub,
                user_id__in=teacher_ids,
                is_active=True,
            )
            .select_related("user")
        }

        skipped = []
        blocks_by_teacher = {}
        for teacher_id_str, blocks in intent.allocations.items():
            if teacher_id_str in active_allocations:
                blocks_by_teacher[teacher_id_str] = blocks
            else:
                skipped.append({"teacher_id": teacher_id_str, "blocks": blocks})

        fulfilled = LicenseSubscriptionService._grant_overage_blocks(
            block_size=intent.block_size_snapshot,
            blocks_by_teacher=blocks_by_teacher,
            allocation_by_teacher=active_allocations,
            ledger_type=CreditLedgerType.PURCHASE,
            reference_fn=lambda teacher_id_str, blocks: (
                f"Overage purchase via license {license_sub.id} (checkout)"
            ),
            metadata_fn=lambda teacher_id_str, blocks: {
                "license_id": str(license_sub.id),
                "intent_id": str(intent.id),
                "initiated_by": (
                    intent.initiated_by.email if intent.initiated_by else None
                ),
                "stripe_checkout_session_id": session.get("id"),
                "stripe_payment_intent_id": session.get("payment_intent"),
                "blocks_purchased": blocks,
                "display_credits": blocks
                * (intent.block_size_snapshot // CONVERSION_FACTOR),
            },
        )

        intent.status = (
            LicenseOveragePurchaseStatus.COMPLETED
            if not skipped
            else LicenseOveragePurchaseStatus.FAILED
        )
        intent.stripe_payment_intent_id = session.get("payment_intent")
        intent.completed_at = timezone.now()
        if skipped:
            intent.failure_reason = (
                f"Paid for {intent.total_blocks} block(s) but {len(skipped)} "
                f"teacher(s) were no longer active at fulfillment time and "
                f"were skipped: {skipped}. Needs manual review (partial "
                f"refund or manual grant)."
            )
        intent.save(
            update_fields=[
                "status",
                "stripe_payment_intent_id",
                "completed_at",
                "failure_reason",
                "updated_at",
            ]
        )

        BillingTransactionService.record(
            source=BillingTransactionSource.LICENSE,
            transaction_type=BillingTransactionType.LICENSE_OVERAGE_PURCHASE,
            status=BillingTransactionStatus.PAID,
            billing_method=BillingTransactionMethod.STRIPE,
            amount_cents=session.get("amount_total") or intent.amount_cents,
            currency=session.get("currency", "usd"),
            license_subscription=license_sub,
            stripe_payment_intent_id=session.get("payment_intent"),
            stripe_checkout_session_id=session.get("id"),
            receipt_url=resolve_stripe_receipt_url(
                payment_intent_id=session.get("payment_intent")
            ),
            performed_by=intent.initiated_by,
            description=(
                f"Overage purchase — "
                f"{intent.total_blocks * (intent.block_size_snapshot // CONVERSION_FACTOR):,} "
                f"AI credit(s) across {len(intent.allocations)} teacher(s)"
                + (f" ({len(skipped)} skipped, needs review)" if skipped else "")
            ),
        )

        if skipped:
            logger.error(
                "Overage purchase intent %s (session %s) PARTIALLY "
                "fulfilled: %d/%d teachers granted, %d skipped (no longer "
                "active). Needs manual review. License %s.",
                intent.id,
                session.get("id"),
                len(fulfilled),
                len(intent.allocations),
                len(skipped),
                license_sub.id,
            )
        else:
            logger.info(
                "Overage purchase intent %s (session %s) fulfilled: %d "
                "block(s) across %d teacher(s) for license %s.",
                intent.id,
                session.get("id"),
                intent.total_blocks,
                len(fulfilled),
                license_sub.id,
            )

    @staticmethod
    def _handle_individual_upgrade_checkout_completed(session, metadata):
        """
        Handles flow='individual_upgrade_checkout' — applies an immediate
        plan upgrade ONLY after the customer has explicitly seen and paid
        the exact prorated amount via the one-time Checkout Session
        created by create_upgrade_checkout_session.

        Applies the actual price change with proration_behavior="none" —
        deliberately NOT "always_invoice" — since the equivalent amount
        was already collected via the separate Checkout payment; letting
        Stripe ALSO generate its own proration invoice here would
        double-charge the customer for the non-interval-crossing case.

        For the interval-crossing case (MONTHLY -> ANNUAL), the forced
        side-effect invoice Stripe generates regardless of
        proration_behavior is detected and voided/refunded — see
        StripeSubscriptionMutationService._void_or_refund_side_effect_invoice
        and the module-level docstring in the companion patch for the full
        explanation.
        """
        user = CustomUser.objects.get(id=metadata["user_id"])
        new_plan = SubscriptionPlan.objects.get(id=metadata["new_plan_id"])
        old_user_sub = UserSubscription.objects.select_related("plan").get(
            id=metadata["user_subscription_id"]
        )
        stripe_subscription_id = metadata["stripe_subscription_id"]
        item_id = metadata["stripe_item_id"]

        # Staleness guard
        current_active_sub = (
            UserSubscription.objects.filter(user=user, is_active=True)
            .select_related("plan")
            .first()
        )

        if not current_active_sub or current_active_sub.id != old_user_sub.id:
            logger.warning(
                "Upgrade checkout session %s completed for user %s, but "
                "their active subscription has changed since this "
                "checkout was created (expected subscription %s, "
                "currently %s) — skipping to avoid overwriting a more "
                "recent change. The payment for this session already "
                "succeeded on Stripe and needs a manual refund if it "
                "shouldn't have gone through.",
                session["id"],
                user.email,
                old_user_sub.id,
                current_active_sub.id if current_active_sub else "none",
            )

            BillingTransactionService.record(
                source=BillingTransactionSource.INDIVIDUAL,
                transaction_type=BillingTransactionType.INDIVIDUAL_UPGRADE_CHARGE,
                status=BillingTransactionStatus.PAID,
                billing_method=BillingTransactionMethod.STRIPE,
                amount_cents=session.get("amount_total")
                or int(metadata.get("proration_amount") or 0),
                currency=session.get("currency", "usd"),
                user=user,
                stripe_payment_intent_id=session.get("payment_intent"),
                stripe_checkout_session_id=session.get("id"),
                stripe_subscription_id=stripe_subscription_id,
                receipt_url=resolve_stripe_receipt_url(
                    payment_intent_id=session.get("payment_intent")
                ),
                description=(
                    "Upgrade checkout paid but the user's active subscription "
                    "changed before this could be applied — needs manual review."
                ),
            )

            return

        is_interval_crossing = (
            old_user_sub.plan.interval == BillingInterval.MONTHLY
            and new_plan.interval == BillingInterval.ANNUAL
        )

        if old_user_sub.stripe_schedule_id:
            StripeSubscriptionScheduleService.release_schedule(old_user_sub)

        modified_sub = stripe.Subscription.modify(
            stripe_subscription_id,
            items=[{"id": item_id, "price": new_plan.stripe_price_id}],
            proration_behavior="none",
        )

        if is_interval_crossing:
            StripeSubscriptionMutationService._void_or_refund_side_effect_invoice(
                stripe_subscription_id
            )
            # Stripe genuinely resets the billing cycle anchor as part of
            # an interval change, so the local cycle must reset to match —
            # to the anchor Stripe just chose, read straight off the
            # response, not to our clock at the moment we got here.
            period_start, period_end = extract_subscription_billing_period(modified_sub)
            updated_sub = SubscriptionService.activate_subscription(
                user, new_plan, period_start=period_start, period_end=period_end
            )
        else:
            # Same-interval change: Stripe's anchor doesn't move, so the
            # local cycle must not either — see apply_immediate_plan_change().
            updated_sub = SubscriptionService.apply_immediate_plan_change(
                old_user_sub, new_plan
            )
        updated_sub.stripe_subscription_id = stripe_subscription_id
        updated_sub.stripe_status = StripeSubscriptionStatus.ACTIVE
        updated_sub.save(
            update_fields=["stripe_subscription_id", "stripe_status", "updated_at"]
        )

        BillingTransactionService.record(
            source=BillingTransactionSource.INDIVIDUAL,
            transaction_type=BillingTransactionType.INDIVIDUAL_UPGRADE_CHARGE,
            status=BillingTransactionStatus.PAID,
            billing_method=BillingTransactionMethod.STRIPE,
            amount_cents=session.get("amount_total")
            or int(metadata.get("proration_amount") or 0),
            currency=session.get("currency", "usd"),
            user=user,
            user_subscription=updated_sub,
            stripe_payment_intent_id=session.get("payment_intent"),
            stripe_checkout_session_id=session.get("id"),
            stripe_subscription_id=stripe_subscription_id,
            receipt_url=resolve_stripe_receipt_url(
                payment_intent_id=session.get("payment_intent")
            ),
            description=f"Upgrade from {old_user_sub.plan.name} to {new_plan.name}",
        )

        logger.info(
            "Upgrade checkout completed for user %s: %s -> %s (subscription "
            "%s, amount_paid=%s cents).",
            user.email,
            old_user_sub.plan.name,
            new_plan.name,
            stripe_subscription_id,
            metadata.get("proration_amount"),
        )

    @staticmethod
    def _handle_individual_subscribe(session, metadata):
        # Kept as a replay-only target for old FAILED events — see the
        # comment on the "individual_subscribe" branch in
        # handle_checkout_completed. Nothing creates new sessions with
        # this flow anymore.
        user = CustomUser.objects.get(id=metadata["user_id"])
        plan = SubscriptionPlan.objects.get(id=metadata["plan_id"])

        subscription = SubscriptionService.activate_subscription(user, plan)
        subscription.stripe_subscription_id = session["subscription"]
        subscription.stripe_status = StripeSubscriptionStatus.ACTIVE
        subscription.save(
            update_fields=["stripe_subscription_id", "stripe_status", "updated_at"]
        )

        BillingTransactionService.record(
            source=BillingTransactionSource.INDIVIDUAL,
            transaction_type=BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE,
            status=BillingTransactionStatus.PAID,
            billing_method=BillingTransactionMethod.STRIPE,
            amount_cents=session.get("amount_total") or 0,
            currency=session.get("currency", "usd"),
            user=user,
            user_subscription=subscription,
            stripe_invoice_id=session.get("invoice"),
            stripe_checkout_session_id=session.get("id"),
            stripe_subscription_id=session.get("subscription"),
            receipt_url=resolve_stripe_receipt_url(
                invoice_id=session.get("invoice"),
                payment_intent_id=session.get("payment_intent"),
            ),
            description=f"New subscription — {plan.display_name or plan.name}",
        )

        logger.info(
            "Stripe checkout completed: individual subscribe for user %s, plan %s.",
            user.email,
            plan.name,
        )

    @staticmethod
    def _handle_license_create(session, metadata):
        school = School.objects.get(id=metadata["school_id"])
        plan = SubscriptionPlan.objects.get(id=metadata["plan_id"])
        admin_user = CustomUser.objects.get(id=metadata["admin_user_id"])
        contract_months = int(metadata["contract_months"])
        max_seats = int(metadata["max_seats"])
        teacher_emails = [e for e in metadata.get("teacher_emails", "").split(",") if e]
        custom_price_cents = (
            int(metadata["custom_price_cents"])
            if metadata.get("custom_price_cents")
            else None
        )
        carry_forward_teachers = (
            metadata.get("carry_forward_teachers", "true") == "true"
        )

        # By the time this webhook fires the school HAS PAID, so an
        # unusable admin_user must not turn into a hard failure that leaves
        # them charged with no license. Sessions created before admin_user
        # was guarded can still be in flight carrying, say, a superadmin's
        # id. Vet it, and on rejection fall back to the school's own admin -
        # which is what it should have been - rather than refusing to
        # provision. Loud, because it means a stale session got through.
        try:
            LicenseSubscriptionService.validate_admin_user(admin_user, school)
        except ValueError as exc:
            logger.error(
                "Checkout session %s named an invalid license admin_user (%s) "
                "for school %s: %s. Falling back to the school's own admin so "
                "the paid license is still provisioned.",
                session.get("id"),
                admin_user.email,
                school.name,
                exc,
            )
            admin_user = None

        license_sub = LicenseSubscriptionService.create_license_subscription(
            school=school,
            plan=plan,
            admin_user=admin_user,
            teacher_emails=teacher_emails,
            contract_months=contract_months,
            max_seats=max_seats,
            custom_price_cents=custom_price_cents,
            carry_forward_teachers=carry_forward_teachers,
        )
        license_sub.stripe_subscription_id = session["subscription"]
        license_sub.stripe_customer_id = session["customer"]
        license_sub.stripe_status = StripeSubscriptionStatus.ACTIVE
        license_sub.save(
            update_fields=[
                "stripe_subscription_id",
                "stripe_customer_id",
                "stripe_status",
                "updated_at",
            ]
        )

        BillingTransactionService.record(
            source=BillingTransactionSource.LICENSE,
            transaction_type=BillingTransactionType.LICENSE_INITIAL_CHARGE,
            status=BillingTransactionStatus.PAID,
            billing_method=BillingTransactionMethod.STRIPE,
            amount_cents=session.get("amount_total") or 0,
            currency=session.get("currency", "usd"),
            user=admin_user,
            license_subscription=license_sub,
            stripe_invoice_id=session.get("invoice"),
            stripe_checkout_session_id=session.get("id"),
            stripe_subscription_id=session.get("subscription"),
            receipt_url=resolve_stripe_receipt_url(
                invoice_id=session.get("invoice"),
                payment_intent_id=session.get("payment_intent"),
            ),
            description=f"License created — {plan.display_name or plan.name}",
        )

        enrollment_results = getattr(
            license_sub,
            "_teacher_enrollment_results",
            {"successful": 0, "failed": 0, "errors": []},
        )
        if enrollment_results["failed"]:
            logger.error(
                "Stripe checkout completed: license created for school %s "
                "(plan %s), but %d/%d teacher invitations FAILED: %s. "
                "Admin %s should be notified to retry via add-teachers.",
                school.name,
                plan.name,
                enrollment_results["failed"],
                enrollment_results["successful"] + enrollment_results["failed"],
                enrollment_results["errors"],
                admin_user.email,
            )
        else:
            logger.info(
                "Stripe checkout completed: license created for school %s "
                "(plan %s). %d teacher(s) invited successfully.",
                school.name,
                plan.name,
                enrollment_results["successful"],
            )

        logger.info(
            "Stripe checkout completed: license created for school %s, plan %s.",
            school.name,
            plan.name,
        )

    @staticmethod
    def _handle_trial_to_paid(session, metadata):
        """
        Webhook handler for checkout.session.completed (flow='trial_to_paid').

        Kept as a replay-only target for an old FAILED event — see the
        comment on this flow's branch in handle_checkout_completed.
        create_trial_to_paid_session (the only thing that ever set this
        metadata) has been deleted; its own caller in views.py was already
        commented out before that. The LIVE mid-trial-upgrade path today
        goes through create_individual_checkout_session instead (flow=
        individual_checkout), reached via select_plan's "checkout" branch
        whenever the current subscription is_trial.

        Called after a trial user successfully completes Stripe checkout to
        upgrade to a paid plan.

        Conversion happens ONLY if:
        - trial_sub still exists and is_trial=True, is_active=True
        - new_plan exists and is valid
        - This is the ONLY place credits are granted for this flow
        """

        try:
            user = CustomUser.objects.get(id=metadata["user_id"])
            trial_sub = UserSubscription.objects.select_for_update().get(
                id=metadata["trial_subscription_id"]
            )
            new_plan = SubscriptionPlan.objects.get(id=metadata["new_plan_id"])
        except (
            CustomUser.DoesNotExist,
            UserSubscription.DoesNotExist,
            SubscriptionPlan.DoesNotExist,
        ) as exc:
            logger.error(
                "trial_to_paid checkout: missing database record. "
                "user_id=%s, trial_sub_id=%s, plan_id=%s. Error: %s",
                metadata.get("user_id"),
                metadata.get("trial_subscription_id"),
                metadata.get("new_plan_id"),
                str(exc),
            )
            raise

        # GUARD: Ensure trial_sub is still active and in trial state
        # (user could have manually expired it via API or Celery task)
        if not trial_sub.is_trial or not trial_sub.is_active:
            logger.warning(
                "trial_to_paid checkout: trial subscription %s for user %s is no longer active/trial. "
                "Skipping conversion. (is_trial=%s, is_active=%s)",
                trial_sub.id,
                user.email,
                trial_sub.is_trial,
                trial_sub.is_active,
            )
            return

        # Extract Stripe subscription ID from session
        stripe_subscription_id = session.get("subscription")
        if not stripe_subscription_id:
            logger.error(
                "trial_to_paid checkout: session %s has no subscription ID. "
                "Cannot attach to trial_sub %s. This should not happen.",
                session.get("id"),
                trial_sub.id,
            )
            raise ValueError("Checkout session missing subscription ID")

        # Finalize the conversion
        SubscriptionService.finalize_trial_to_paid_conversion(
            trial_sub=trial_sub,
            new_plan=new_plan,
            stripe_subscription_id=stripe_subscription_id,
        )

        BillingTransactionService.record(
            source=BillingTransactionSource.INDIVIDUAL,
            transaction_type=BillingTransactionType.INDIVIDUAL_TRIAL_CONVERSION_CHARGE,
            status=BillingTransactionStatus.PAID,
            billing_method=BillingTransactionMethod.STRIPE,
            amount_cents=session.get("amount_total") or 0,
            currency=session.get("currency", "usd"),
            user=user,
            user_subscription=trial_sub,
            stripe_invoice_id=session.get("invoice"),
            stripe_checkout_session_id=session.get("id"),
            stripe_subscription_id=stripe_subscription_id,
            receipt_url=resolve_stripe_receipt_url(
                invoice_id=session.get("invoice"),
                payment_intent_id=session.get("payment_intent"),
            ),
            description=f"Trial converted to {new_plan.display_name or new_plan.name}",
        )

        logger.info(
            "Stripe checkout completed: trial-to-paid conversion for user %s. "
            "Trial subscription: %s, New plan: %s, Stripe subscription: %s",
            user.email,
            trial_sub.id,
            new_plan.name,
            stripe_subscription_id,
        )

    # ------------------------------------------------------------------
    # invoice.payment_succeeded
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def handle_invoice_payment_succeeded(invoice):
        # stripe_subscription_id = invoice.get("subscription")

        # parent = invoice.get("parent") or {}

        # stripe_subscription_id = (
        #     invoice.get("subscription")
        #     or parent.get("subscription_details", {}).get("subscription")
        # )

        stripe_subscription_id = StripeWebhookHandler._extract_invoice_subscription_id(
            invoice
        )

        if not stripe_subscription_id:
            return

        billing_reason = invoice.get("billing_reason")

        # First, try individual subscription
        user_sub = (
            UserSubscription.objects.filter(
                stripe_subscription_id=stripe_subscription_id
            )
            .select_for_update()
            .first()
        )
        if user_sub:
            StripeWebhookHandler._handle_individual_invoice_succeeded(
                user_sub, billing_reason, invoice
            )
            return

        # Then, try license subscription
        license_sub = (
            LicenseSubscription.objects.filter(
                stripe_subscription_id=stripe_subscription_id
            )
            .select_for_update()
            .first()
        )
        if license_sub:
            if license_sub.billing_method == LicenseBillingMethod.OFFLINE:
                logger.warning(
                    "Stripe event for license %s ignored — license is now "
                    "billed offline (stale event, should be rare since "
                    "stripe_subscription_id is cleared on conversion).",
                    license_sub.id,
                )
                return

            txn_type = (
                BillingTransactionType.LICENSE_PLAN_CHANGE_CHARGE
                if billing_reason == "subscription_update"
                else BillingTransactionType.LICENSE_SUBSCRIPTION_CHARGE
            )
            BillingTransactionService.record(
                source=BillingTransactionSource.LICENSE,
                transaction_type=txn_type,
                status=BillingTransactionStatus.PAID,
                billing_method=BillingTransactionMethod.STRIPE,
                amount_cents=invoice.get("amount_paid") or 0,
                currency=invoice.get("currency", "usd"),
                license_subscription=license_sub,
                stripe_invoice_id=invoice.get("id"),
                stripe_subscription_id=stripe_subscription_id,
                receipt_url=invoice.get("hosted_invoice_url"),
                description=f"License Stripe invoice paid ({billing_reason})",
            )

            # Only act on actual renewal invoices, and never on a license
            # that is no longer active — an out-of-order or retried
            # delivery arriving after cancellation must not revive it.
            # (process_license_renewal also guards on is_active; this is
            # the explicit outer guard so the intent is visible here and
            # the status write below can be gated on the same condition.)
            if not license_sub.is_active:
                logger.warning(
                    "invoice.payment_succeeded for license %s ignored for "
                    "renewal: the license is no longer active. Invoice %s "
                    "was still recorded.",
                    license_sub.id,
                    invoice.get("id"),
                )
                return

            if billing_reason in RENEWAL_BILLING_REASONS:
                # Renew credits - idempotent; will skip if billing_cycle_end already in future
                LicenseSubscriptionService.process_license_renewal(license_sub)

            # Update status to reflect Stripe's state
            license_sub.stripe_status = StripeSubscriptionStatus.ACTIVE
            license_sub.save(update_fields=["stripe_status", "updated_at"])
            logger.info("License %s monthly Stripe charge succeeded.", license_sub.id)
            return

        logger.warning(
            "invoice.payment_succeeded for unrecognized stripe_subscription_id %s",
            stripe_subscription_id,
        )

    @staticmethod
    def _handle_individual_invoice_succeeded(user_sub, billing_reason, invoice):
        """
        Three independent conditions must ALL hold before this invoice is
        allowed to drive a renewal. Any one failing means we record the
        money, reconcile status/price, and stop:

        1. billing_reason is a real new-period reason. Stripe fires this
           event for upgrade prorations (subscription_update) and initial
           charges (subscription_create) too. subscription_create is
           already handled by checkout.session.completed, and an immediate
           upgrade is applied synchronously by the upgrade flow — letting
           either reach process_rollover_and_renewal grants a whole extra
           cycle of credits and rolls the billing period forward off an
           invoice that was never a renewal.
        2. The local period has actually elapsed. This is the redelivery /
           reconcile-already-won guard.
        3. The subscription row is still active. Stripe does not guarantee
           delivery order, so a retried payment_succeeded can legitimately
           arrive AFTER customer.subscription.deleted. Renewing then would
           resurrect a canceled subscription, because
           process_rollover_and_renewal -> activate_subscription creates a
           fresh active row.

        The nightly reconcile_subscription_renewals sweep remains the
        backstop for anything legitimately skipped here.
        """
        now = timezone.now()
        stripe_subscription_id = user_sub.stripe_subscription_id

        logger.debug(
            "invoice.payment_succeeded for individual subscription %s "
            "(billing_reason=%s, billing_cycle_end=%s, now=%s, is_active=%s).",
            user_sub.id,
            billing_reason,
            user_sub.billing_cycle_end.isoformat(),
            now.isoformat(),
            user_sub.is_active,
        )

        txn_type = (
            BillingTransactionType.INDIVIDUAL_UPGRADE_CHARGE
            if billing_reason == "subscription_update"
            else BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE
        )

        def _record(target_sub, record_txn_type):
            BillingTransactionService.record(
                source=BillingTransactionSource.INDIVIDUAL,
                transaction_type=record_txn_type,
                status=BillingTransactionStatus.PAID,
                billing_method=BillingTransactionMethod.STRIPE,
                amount_cents=invoice.get("amount_paid") or 0,
                currency=invoice.get("currency", "usd"),
                user=target_sub.user,
                user_subscription=target_sub,
                stripe_invoice_id=invoice.get("id"),
                stripe_subscription_id=stripe_subscription_id,
                receipt_url=invoice.get("hosted_invoice_url"),
                description=f"Subscription invoice paid ({billing_reason})",
            )

        skip_reason = None
        if billing_reason not in RENEWAL_BILLING_REASONS:
            skip_reason = (
                f"billing_reason={billing_reason!r} is not a new-period "
                "renewal reason"
            )
        elif user_sub.billing_cycle_end > now:
            skip_reason = (
                "the local billing period has not elapsed yet — already "
                "renewed by an earlier delivery or by the reconcile sweep"
            )
        elif not user_sub.is_active:
            skip_reason = (
                "the subscription row is no longer active (cancelled or "
                "superseded) — renewing would resurrect it"
            )

        if skip_reason is not None:
            # The money still happened, so it is always recorded.
            _record(user_sub, txn_type)

            if user_sub.is_active:
                user_sub.stripe_status = StripeSubscriptionStatus.ACTIVE
                user_sub.save(update_fields=["stripe_status", "updated_at"])

                # Ensure the Stripe price is in sync (if a pending plan exist)
                StripeSubscriptionMutationService.sync_price(
                    user_sub, stripe_subscription_id
                )
                logger.info(
                    "invoice.payment_succeeded for subscription %s recorded "
                    "without renewal: %s.",
                    user_sub.id,
                    skip_reason,
                )
            else:
                # Deliberately NOT flipping stripe_status back to ACTIVE:
                # that would leave an is_active=False row claiming an
                # active Stripe status.
                logger.warning(
                    "invoice.payment_succeeded for subscription %s (user %s) "
                    "ignored for renewal: %s. Invoice %s was still recorded. "
                    "This is the expected outcome for an out-of-order or "
                    "retried delivery arriving after cancellation.",
                    user_sub.id,
                    user_sub.user.email,
                    skip_reason,
                    invoice.get("id"),
                )
            return

        # Stripe's authoritative period for the cycle this invoice pays for.
        # Anchoring the local dates to it (instead of "whenever we processed
        # the webhook") is what stops billing_cycle_end drifting a little
        # further from Stripe's every month and turning the idempotency
        # guard above into a coin flip.
        period_start, period_end = extract_invoice_billing_period(invoice)
        if period_start is None:
            logger.warning(
                "Invoice %s for subscription %s carries no usable billing "
                "period; falling back to a wall-clock cycle. The local period "
                "will be offset from Stripe's by this webhook's latency.",
                invoice.get("id"),
                user_sub.id,
            )

        if user_sub.is_trial:
            # Trial just ended and the first real charge succeeded.

            txn_type = BillingTransactionType.INDIVIDUAL_TRIAL_CONVERSION_CHARGE
            updated_sub = SubscriptionService.finalize_trial_conversion_via_stripe(
                user_sub, period_start=period_start, period_end=period_end
            )
        else:
            # Normal monthly renewal. activate_subscription() (called inside
            # process_rollover_and_renewal) creates a NEW UserSubscription
            # row and deactivates this one — the Stripe subscription id has
            # to be re-attached to the new row, not this (now inactive) one.

            updated_sub = SubscriptionService.process_rollover_and_renewal(
                user_sub, period_start=period_start, period_end=period_end
            )

            if user_sub.stripe_schedule_id:
                updated_sub.stripe_schedule_id = user_sub.stripe_schedule_id

            # If schedule_downgrade() (or an upgrade) set a different plan
            # than what Stripe is currently billing, sync it now — this
            # invoice was correctly billed at the old price, so the change
            # only applies going forward.
            StripeSubscriptionMutationService.sync_price(
                updated_sub, stripe_subscription_id
            )

        _record(updated_sub, txn_type)

        updated_sub.stripe_subscription_id = stripe_subscription_id
        updated_sub.stripe_status = StripeSubscriptionStatus.ACTIVE
        update_fields = ["stripe_subscription_id", "stripe_status", "updated_at"]
        if not user_sub.is_trial and user_sub.stripe_schedule_id:
            # Only the renewal branch carries the old row's schedule id over
            # to the new row (assigned just above); including the field
            # unconditionally would write the trial branch's untouched
            # default over whatever the row already holds.
            update_fields.append("stripe_schedule_id")
        updated_sub.save(update_fields=update_fields)

    # ------------------------------------------------------------------
    # invoice.payment_failed
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def handle_invoice_payment_failed(invoice):
        # stripe_subscription_id = invoice.get("subscription")
        stripe_subscription_id = StripeWebhookHandler._extract_invoice_subscription_id(
            invoice
        )

        if not stripe_subscription_id:
            return

        user_sub = (
            UserSubscription.objects.filter(
                stripe_subscription_id=stripe_subscription_id
            )
            .select_for_update()
            .first()
        )
        if user_sub:
            billing_reason = invoice.get("billing_reason")
            txn_type = (
                BillingTransactionType.INDIVIDUAL_UPGRADE_CHARGE
                if billing_reason == "subscription_update"
                else BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE
            )
            BillingTransactionService.record(
                source=BillingTransactionSource.INDIVIDUAL,
                transaction_type=txn_type,
                status=BillingTransactionStatus.FAILED,
                billing_method=BillingTransactionMethod.STRIPE,
                amount_cents=invoice.get("amount_due") or 0,
                currency=invoice.get("currency", "usd"),
                user=user_sub.user,
                user_subscription=user_sub,
                stripe_invoice_id=invoice.get("id"),
                stripe_subscription_id=stripe_subscription_id,
                description=f"Subscription invoice payment failed ({billing_reason})",
            )

            if user_sub.is_trial:
                # Card declined at trial end — mirrors expire_trial(), no
                # conversion.
                #
                # force=True deliberately. Stripe's word that the card was
                # declined is authoritative regardless of what our clock
                # says about trial_end, and the two disagree routinely
                # (clock skew, webhook latency). With the default
                # force=False, expire_trial raises whenever this event
                # lands even a moment early — and because this handler is
                # @transaction.atomic and webhooks.py turns any handler
                # exception into a 500, that rolls back the audit row above
                # and sends Stripe into a ~3-day retry loop that can never
                # converge, since trial_end never moves. See
                # billing/tests/test_invoice_payment_failed.py.
                SubscriptionService.expire_trial(user_sub, force=True)
            else:
                user_sub.stripe_status = StripeSubscriptionStatus.PAST_DUE
                user_sub.save(update_fields=["stripe_status", "updated_at"])
            return

        license_sub = (
            LicenseSubscription.objects.filter(
                stripe_subscription_id=stripe_subscription_id
            )
            .select_for_update()
            .first()
        )
        if license_sub:
            billing_reason = invoice.get("billing_reason")
            txn_type = (
                BillingTransactionType.LICENSE_PLAN_CHANGE_CHARGE
                if billing_reason == "subscription_update"
                else BillingTransactionType.LICENSE_SUBSCRIPTION_CHARGE
            )
            BillingTransactionService.record(
                source=BillingTransactionSource.LICENSE,
                transaction_type=txn_type,
                status=BillingTransactionStatus.FAILED,
                billing_method=BillingTransactionMethod.STRIPE,
                amount_cents=invoice.get("amount_due") or 0,
                currency=invoice.get("currency", "usd"),
                license_subscription=license_sub,
                stripe_invoice_id=invoice.get("id"),
                stripe_subscription_id=stripe_subscription_id,
                description=f"License invoice payment failed ({billing_reason})",
            )

            license_sub.stripe_status = StripeSubscriptionStatus.PAST_DUE
            license_sub.save(update_fields=["stripe_status", "updated_at"])

    # ------------------------------------------------------------------
    # customer.subscription.updated
    # ------------------------------------------------------------------

    # Only statuses we have a local enum value for are synced. Stripe also
    # sends "paused" and "incomplete_expired", which have no local
    # equivalent — those are logged and left alone rather than guessed at.
    _SUBSCRIPTION_UPDATED_STATUS_MAP = {
        "trialing": StripeSubscriptionStatus.TRIALING,
        "active": StripeSubscriptionStatus.ACTIVE,
        "past_due": StripeSubscriptionStatus.PAST_DUE,
        "canceled": StripeSubscriptionStatus.CANCELED,
        "incomplete": StripeSubscriptionStatus.INCOMPLETE,
        "unpaid": StripeSubscriptionStatus.UNPAID,
    }
    _SUBSCRIPTION_UPDATED_DEACTIVATING_STATUSES = frozenset(
        {StripeSubscriptionStatus.CANCELED, StripeSubscriptionStatus.UNPAID}
    )

    @staticmethod
    def _stripe_timestamp_to_datetime(value):
        """
        Stripe sends timestamps as unix ints. Returns None for anything
        missing or unparseable rather than raising — a malformed
        timestamp must never be the reason a webhook 500s and retries
        for three days.
        """
        if not value:
            return None
        try:
            return datetime.fromtimestamp(int(value), tz=dt_timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            return None

    @staticmethod
    def _sync_cancellation_intent(user_sub, cancel_at_period_end, stripe_subscription):
        """
        Mirrors Stripe's `cancel_at_period_end` onto the local
        `auto_renew` / `cancelled_at` pair, and returns the field names
        it touched (empty when already in sync, so a replayed event
        writes nothing).

        Stripe is the source of truth here: this event fires for
        cancellations made in the Stripe dashboard, which no other code
        path in this app would ever hear about.

        Args:
            user_sub (UserSubscription): the locked local row.
            cancel_at_period_end (bool): Stripe's current flag.
            stripe_subscription (dict): the full event payload, read for
                `canceled_at` so the recorded date is Stripe's own rather
                than "whenever the webhook happened to land".

        Returns:
            list[str]: update_fields to include in the caller's save().
        """
        if user_sub.is_trial:
            # A trial's auto_renew is False by design — it means "will
            # not convert to paid without an explicit action", NOT "the
            # user cancelled". Mirroring Stripe onto it would either
            # fabricate a cancellation date or, in the un-cancel
            # direction, flip a trial into looking like a renewing paid
            # plan. Trials have no Stripe subscription in the current
            # flows, so this is belt-and-braces.
            return []

        if cancel_at_period_end:
            if not user_sub.auto_renew and user_sub.cancelled_at is not None:
                return []
            user_sub.auto_renew = False
            if user_sub.cancelled_at is None:
                # Only stamped when absent, so our own cancel endpoint's
                # timestamp wins over the echo of the very Stripe call it
                # just made, and a repeat event never slides the date.
                user_sub.cancelled_at = (
                    StripeWebhookHandler._stripe_timestamp_to_datetime(
                        stripe_subscription.get("canceled_at")
                    )
                    or timezone.now()
                )
            logger.info(
                "customer.subscription.updated: subscription %s is now "
                "scheduled to cancel at period end on Stripe — mirroring "
                "to auto_renew=False locally.",
                user_sub.id,
            )
            return ["auto_renew", "cancelled_at"]

        if user_sub.auto_renew and user_sub.cancelled_at is None:
            return []
        user_sub.auto_renew = True
        user_sub.cancelled_at = None
        logger.info(
            "customer.subscription.updated: subscription %s is no longer "
            "scheduled to cancel on Stripe — mirroring to auto_renew=True "
            "locally.",
            user_sub.id,
        )
        return ["auto_renew", "cancelled_at"]

    @staticmethod
    @transaction.atomic
    def handle_subscription_updated(stripe_subscription):
        """
        customer.subscription.updated fires for EVERY change to a
        subscription on Stripe's side, including ones our own code just
        made via Subscription.modify — so its arrival here is not itself
        evidence of anything unexpected.

        This handler deliberately syncs ONLY `stripe_status` (plus the
        `is_active` flag when the new status is terminal) and, for
        individual subscriptions, `cancel_at_period_end` -> `auto_renew`
        / `cancelled_at`. It does NOT touch plan, price or billing
        period: those are already owned by more specific paths (the
        upgrade/downgrade services, the renewal webhook, the reconcile
        sweep) that know exactly which local objects to create or how to
        attribute credits, and duplicating that logic here would risk
        conflicting with — or double-running — those decisions on the
        very same event.

        The gap this closes is narrower and specific: a change made
        directly on Stripe (dashboard, or Stripe itself e.g. pausing a
        subscription for chargeback risk) with no other webhook and no
        request from our own code. Before this handler existed, such a
        status change was invisible to the app until the next daily
        reconcile sweep — which reads status too, but only once every 24
        hours.

        `cancel_at_period_end` is synced here for exactly the same
        reason, and is NOT covered by the status sync: setting a
        subscription to cancel at period end leaves Stripe's `status` on
        "active", so a cancellation made in the Stripe dashboard changes
        no status at all and would otherwise never reach the database.
        The local row would keep claiming auto_renew=True right up until
        the subscription silently stopped renewing, and the user would
        never be offered the resume flow.
        """
        stripe_subscription_id = stripe_subscription["id"]
        new_status = StripeWebhookHandler._SUBSCRIPTION_UPDATED_STATUS_MAP.get(
            stripe_subscription.get("status")
        )
        if new_status is None:
            logger.info(
                "customer.subscription.updated for %s reported status %r, "
                "which has no local mapping — leaving local status "
                "unchanged.",
                stripe_subscription_id,
                stripe_subscription.get("status"),
            )

        deactivate = (
            new_status is not None
            and new_status
            in StripeWebhookHandler._SUBSCRIPTION_UPDATED_DEACTIVATING_STATUSES
        )

        # Read with .get() and checked against None rather than coerced:
        # a thin or partial payload that OMITS the flag must not be read
        # as "not cancelling", which would silently un-cancel a
        # subscription the user genuinely asked to end.
        cancel_at_period_end = stripe_subscription.get("cancel_at_period_end")

        user_sub = (
            UserSubscription.objects.filter(
                stripe_subscription_id=stripe_subscription_id, is_active=True
            )
            .select_for_update()
            .first()
        )
        if user_sub:
            update_fields = []

            if new_status is not None and (
                user_sub.stripe_status != new_status or deactivate
            ):
                logger.info(
                    "customer.subscription.updated: subscription %s status "
                    "%s -> %s%s.",
                    stripe_subscription_id,
                    user_sub.stripe_status,
                    new_status,
                    " (deactivating)" if deactivate else "",
                )
                user_sub.stripe_status = new_status
                update_fields.append("stripe_status")
                if deactivate:
                    user_sub.is_active = False
                    update_fields.append("is_active")

            if cancel_at_period_end is not None:
                update_fields += StripeWebhookHandler._sync_cancellation_intent(
                    user_sub, bool(cancel_at_period_end), stripe_subscription
                )

            if update_fields:
                user_sub.save(update_fields=update_fields + ["updated_at"])

            if deactivate:
                from users.tasks import sync_user_to_mailerlite

                sync_user_to_mailerlite.delay(str(user_sub.user_id))
            return

        license_sub = (
            LicenseSubscription.objects.filter(
                stripe_subscription_id=stripe_subscription_id, is_active=True
            )
            .select_for_update()
            .first()
        )
        if license_sub:
            if new_status is None:
                return
            if license_sub.stripe_status == new_status and not deactivate:
                return
            logger.info(
                "customer.subscription.updated: license subscription %s "
                "status %s -> %s%s.",
                stripe_subscription_id,
                license_sub.stripe_status,
                new_status,
                " (deactivating)" if deactivate else "",
            )
            license_sub.stripe_status = new_status
            update_fields = ["stripe_status", "updated_at"]
            if deactivate:
                license_sub.is_active = False
                update_fields.append("is_active")
            license_sub.save(update_fields=update_fields)

            if deactivate:
                sync_teachers_under_license_to_mailerlite(license_sub)

    # ------------------------------------------------------------------
    # customer.subscription.deleted
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def handle_subscription_deleted(stripe_subscription):
        stripe_subscription_id = stripe_subscription["id"]

        user_sub = (
            UserSubscription.objects.filter(
                stripe_subscription_id=stripe_subscription_id, is_active=True
            )
            .select_for_update()
            .first()
        )
        if user_sub:
            if user_sub.is_trial:
                if user_sub.trial_end and user_sub.trial_end > timezone.now():
                    # Cancelled mid-trial, before natural expiry —
                    # expire_trial() would reject this since it requires
                    # trial_end to have already passed, so deactivate directly.
                    user_sub.is_active = False
                    user_sub.is_trial = False
                    user_sub.save(update_fields=["is_active", "is_trial", "updated_at"])

                    from users.tasks import sync_user_to_mailerlite

                    sync_user_to_mailerlite.delay(str(user_sub.user_id))
                else:
                    SubscriptionService.expire_trial(user_sub)
            else:
                user_sub.is_active = False
                user_sub.stripe_status = StripeSubscriptionStatus.CANCELED
                user_sub.save(
                    update_fields=["is_active", "stripe_status", "updated_at"]
                )

                from users.tasks import sync_user_to_mailerlite

                sync_user_to_mailerlite.delay(str(user_sub.user_id))
            return

        license_sub = (
            LicenseSubscription.objects.filter(
                stripe_subscription_id=stripe_subscription_id, is_active=True
            )
            .select_for_update()
            .first()
        )
        if license_sub:
            license_sub.is_active = False
            license_sub.stripe_status = StripeSubscriptionStatus.CANCELED
            license_sub.save(update_fields=["is_active", "stripe_status", "updated_at"])
            sync_teachers_under_license_to_mailerlite(license_sub)

    @staticmethod
    @transaction.atomic
    def handle_charge_refunded(charge):
        BillingTransactionService.handle_refund(charge)

    # ------------------------------------------------------------------
    # payment_intent.succeeded (overage block fallback for 3DS / requires_action)
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def handle_payment_intent_succeeded(payment_intent):
        metadata = payment_intent.get("metadata", {}) or {}
        if metadata.get("flow") != "overage_block_purchase":
            return

        # Idempotency against the synchronous path in StripeOverageService,
        # which already grants the bucket when no further action is needed.
        # This handler is only the fallback for the requires_action case.
        already_granted = CreditLedger.objects.filter(
            metadata__stripe_payment_intent_id=payment_intent["id"]
        ).exists()
        if already_granted:
            return

        wallet_id = metadata.get("wallet_id")
        plan_id = metadata.get("plan_id")

        if wallet_id and plan_id:
            # --- Preferred path: grant details were snapshotted at
            # purchase time, so there's no ambiguity regardless of what
            # happened to the user's subscription in the meantime. ---

            try:
                wallet = CreditWallet.objects.select_related("user").get(id=wallet_id)
            except CreditWallet.DoesNotExist:
                logger.error(
                    "Overage PaymentIntent %s references wallet %s which no "
                    "longer exists — credits NOT granted. Needs manual "
                    "reconciliation (refund or manual grant).",
                    payment_intent["id"],
                    wallet_id,
                )
                return

            try:
                plan = SubscriptionPlan.objects.get(id=plan_id)
            except SubscriptionPlan.DoesNotExist:
                # SubscriptionPlan is on_delete=PROTECT everywhere it's
                # referenced, so this should be effectively unreachable —
                # guarded anyway since we're handling real money.
                logger.error(
                    "Overage PaymentIntent %s references plan %s which no "
                    "longer exists — credits NOT granted. Needs manual "
                    "reconciliation (refund or manual grant).",
                    payment_intent["id"],
                    plan_id,
                )
                return

            SubscriptionService.grant_overage_bucket(
                wallet=wallet,
                plan=plan,
                stripe_payment_intent_id=payment_intent["id"],
            )
            logger.info(
                "Overage PaymentIntent %s succeeded (snapshotted metadata "
                "path): granted overage bucket for plan %s to wallet %s.",
                payment_intent["id"],
                plan.name,
                wallet.id,
            )
            return

        # --- Legacy fallback: PaymentIntents created before this
        # metadata-snapshotting change went out won't have wallet_id/
        # plan_id. Fall back to the old best-effort behavior — resolve
        # from whatever subscription is active RIGHT NOW. This is the
        # race-prone path being replaced; it should only ever fire for
        # a short window during deploy, as old in-flight intents clear. ---
        logger.warning(
            "Overage PaymentIntent %s missing wallet/plan metadata (legacy "
            "format, pre-dates purchase-time snapshotting). Falling back to "
            "resolving from the user's current active subscription.",
            payment_intent["id"],
        )

        user_id = metadata.get("user_id")
        if not user_id:
            logger.error(
                "Overage PaymentIntent %s has no user_id in metadata at "
                "all — credits NOT granted. Needs manual reconciliation.",
                payment_intent["id"],
            )
            return

        try:
            user = CustomUser.objects.get(id=user_id)
        except CustomUser.DoesNotExist:
            logger.error(
                "Overage PaymentIntent %s references user %s who no longer "
                "exists — credits NOT granted. Needs manual reconciliation.",
                payment_intent["id"],
                user_id,
            )
            return

        wallet = user.credit_wallet
        user_sub = (
            user.subscriptions.filter(is_active=True).select_related("plan").first()
        )
        if not user_sub:
            # This is the exact scenario the comment described: Stripe
            # captured payment, but by the time we're here there's no
            # active subscription to attribute the grant to, and no
            # snapshotted metadata to fall back on either. There is no
            # safe automatic remedy left at this point — surfacing it
            # loudly is the correct behavior, not a gap to silently paper
            # over with a guess.
            logger.error(
                "Overage PaymentIntent %s succeeded for user %s but they "
                "have no active individual subscription and no snapshotted "
                "plan metadata — credits NOT granted. Needs manual "
                "reconciliation (refund or manual grant).",
                payment_intent["id"],
                user.email,
            )
            return

        SubscriptionService.grant_overage_bucket(
            wallet=wallet,
            plan=user_sub.plan,
            stripe_payment_intent_id=payment_intent["id"],
        )

    @staticmethod
    @transaction.atomic
    def handle_payment_intent_failed(payment_intent):
        """
        No credits were ever granted for this PaymentIntent — grant only
        happens on success — so there's nothing to roll back. This exists
        purely for observability: logs the decline so support can see why
        a user's overage purchase didn't complete (e.g. an abandoned or
        failed 3DS challenge after purchase_overage_block returned
        "requires_action").
        """
        metadata = payment_intent.get("metadata", {}) or {}
        if metadata.get("flow") != "overage_block_purchase":
            return

        last_error = payment_intent.get("last_payment_error") or {}
        logger.warning(
            "Overage block purchase failed for user %s (PaymentIntent %s): %s",
            metadata.get("user_id"),
            payment_intent.get("id"),
            last_error.get("message") or "unknown error",
        )

    @staticmethod
    def _handle_license_convert_to_stripe(session, metadata):
        license_sub = LicenseSubscription.objects.select_for_update().get(
            id=metadata["license_id"]
        )

        if license_sub.billing_method != LicenseBillingMethod.OFFLINE:
            logger.warning(
                "license_convert_to_stripe webhook for license %s which is "
                "already billing_method=%s. Ignoring (already converted, or "
                "duplicate delivery already handled by StripeEvent idempotency).",
                license_sub.id,
                license_sub.billing_method,
            )
            return

        license_sub.stripe_subscription_id = session["subscription"]
        license_sub.stripe_customer_id = session["customer"]
        license_sub.billing_method = LicenseBillingMethod.STRIPE
        license_sub.stripe_status = StripeSubscriptionStatus.ACTIVE
        license_sub.save(
            update_fields=[
                "stripe_subscription_id",
                "stripe_customer_id",
                "billing_method",
                "stripe_status",
                "updated_at",
            ]
        )

        performed_by = None
        initiated_by_id = metadata.get("initiated_by_user_id")
        if initiated_by_id:
            performed_by = CustomUser.objects.filter(id=initiated_by_id).first()

        LicenseBillingRecord.objects.create(
            license_subscription=license_sub,
            record_type=LicenseBillingRecordType.CONVERTED_TO_STRIPE,
            performed_by=performed_by,
            notes=f"Converted to Stripe billing via checkout session {session['id']}.",
        )

        logger.info(
            "License %s converted from OFFLINE to STRIPE billing (Stripe "
            "subscription %s).",
            license_sub.id,
            session["subscription"],
        )

    @staticmethod
    @transaction.atomic
    def handle_setup_intent_succeeded(setup_intent):
        """
        Optionally sets the newly-collected card as the customer's default
        invoice payment method, gated on metadata.set_as_default rather
        than on the presence of a license_id — this is now shared by the
        license admin flow (create_license_setup_intent, always
        set_as_default="true") and the general payment-methods "add a
        card" endpoint (create_setup_intent_for_request_user,
        caller-controlled). Every SetupIntent this codebase creates sets
        this metadata key explicitly, so there is no implicit default
        branch here.

        Deliberately webhook-driven rather than done synchronously in a
        "confirm" endpoint, so we never trust a client-supplied
        payment_method_id without Stripe having actually confirmed the
        SetupIntent succeeded. The card itself is already attached to the
        customer by Stripe once the SetupIntent succeeds, regardless of
        default status — nothing else needs to happen here when
        set_as_default is false.
        """
        metadata = setup_intent.get("metadata", {}) or {}
        should_set_default = metadata.get("set_as_default") == "true"
        if not should_set_default:
            return

        payment_method_id = setup_intent.get("payment_method")
        customer_id = setup_intent.get("customer")
        if not payment_method_id or not customer_id:
            logger.warning(
                "setup_intent.succeeded (id=%s) missing payment_method or "
                "customer on the event object.",
                setup_intent.get("id"),
            )
            return

        try:
            stripe.Customer.modify(
                customer_id,
                invoice_settings={"default_payment_method": payment_method_id},
            )
        except stripe.error.StripeError:
            logger.exception(
                "Failed to set default payment method for customer %s.",
                customer_id,
            )
            return

        logger.info(
            "Default payment method set for customer %s.",
            customer_id,
        )
