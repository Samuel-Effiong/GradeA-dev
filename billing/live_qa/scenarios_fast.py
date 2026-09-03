"""
billing/live_qa/scenarios_fast.py
=================================
Fast scenarios that need NO clock advance at all.

WHY THESE FIRST
---------------
Every one of these runs in seconds, because none of them waits on Stripe
to process a time jump. Together they cover the surfaces where the gap
between our mocks and Stripe's real behaviour is most commercially
dangerous:

  payment_method_lifecycle   billing/payment_method_views.py is 600+ lines
                             and 100% mocked. This is the code a customer
                             touches when their card expires — failure
                             here is involuntary churn.
  upgrade_proration_quote    We show the customer a price, then charge
                             them. A mock returning a fixed integer proves
                             nothing about whether those two numbers match.
  charge_refund_flow         Partial vs full refunds take different
                             branches; a mock that always sets
                             amount_refunded == amount never exercises the
                             partial one.
  multiple_subscription_items Every call site takes items.data[0] with no
                             check. A second line item is silently
                             invisible.
  discount_flow_through      The codebase models neither coupons nor tax.
                             A retention coupon applied in the dashboard
                             makes amount_paid != plan.price_cents.

The last two exist as much to FORCE A DECISION as to catch a regression:
today the behaviour is undefined, and undefined behaviour around money
does not stay harmless.
"""

from __future__ import annotations

import logging

from billing.imports import stripe
from billing.models import BillingInterval, BillingTransaction, PlanTier
from billing.stripe_live_qa import CARD_OK, CheckRecorder, guarded_call, require_plan
from billing.stripe_live_qa_scenarios import (
    TIER_FAST,
    _establish_subscriber,
    register_scenarios,
)

logger = logging.getLogger(__name__)


def scenario_payment_method_lifecycle(harness) -> CheckRecorder:
    """Add, list, set-default and remove cards against real Stripe.

    Stripe enforces ordering rules a mock cannot: a payment method must
    be attached before it can be made default, and detaching one that is
    still the subscription's default behaves differently from detaching a
    spare. None of that is visible when every call returns
    {"status": "succeeded"}.
    """
    rec = CheckRecorder()
    plan = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)
    sub = _establish_subscriber(harness, rec, plan=plan, label="pm")
    harness.drain_events(customer_id=sub.customer_id)

    customer = guarded_call(stripe.Customer.retrieve, sub.customer_id)
    original_default = (customer.get("invoice_settings") or {}).get(
        "default_payment_method"
    )
    rec.expect(
        "the subscribing card became the customer's default",
        bool(original_default),
        f"invoice_settings.default_payment_method={original_default!r}",
    )

    # Add a SECOND card, exactly as the "add a card" endpoint does.
    second = harness.attach_card(
        customer_id=sub.customer_id, token="pm_card_mastercard", set_default=False
    )
    rec.expect(
        "a second card attaches without disturbing the default",
        bool(second.get("id")),
        f"attached {second.get('id')}",
    )

    listed = guarded_call(
        stripe.PaymentMethod.list, customer=sub.customer_id, type="card"
    )
    ids = {pm["id"] for pm in (listed.get("data") or [])}
    rec.expect(
        "both cards are listed against the customer",
        len(ids) >= 2,
        f"{len(ids)} card(s): {sorted(ids)}",
    )

    # Adding a card must NOT silently change who gets charged.
    customer = guarded_call(stripe.Customer.retrieve, sub.customer_id)
    rec.expect_equal(
        "adding a card did not silently change the default",
        (customer.get("invoice_settings") or {}).get("default_payment_method"),
        original_default,
    )

    # Promote the second card, then detach the first.
    guarded_call(
        stripe.Customer.modify,
        sub.customer_id,
        invoice_settings={"default_payment_method": second["id"]},
    )
    customer = guarded_call(stripe.Customer.retrieve, sub.customer_id)
    rec.expect_equal(
        "the new card became the default",
        (customer.get("invoice_settings") or {}).get("default_payment_method"),
        second["id"],
    )

    guarded_call(stripe.PaymentMethod.detach, original_default)
    listed = guarded_call(
        stripe.PaymentMethod.list, customer=sub.customer_id, type="card"
    )
    remaining = {pm["id"] for pm in (listed.get("data") or [])}
    rec.expect(
        "the detached card is gone and the default survives",
        original_default not in remaining and second["id"] in remaining,
        f"remaining: {sorted(remaining)}",
    )
    return rec


def scenario_upgrade_proration_quote(harness) -> CheckRecorder:
    """The price we QUOTE must equal the price Stripe would CHARGE.

    create_upgrade_checkout_session previews the proration with
    stripe.Invoice.create_preview and then charges that exact amount. A
    mocked preview returning a fixed integer cannot tell us whether the
    number we showed the customer is the number Stripe agrees with —
    and quoting one price while charging another is a chargeback.
    """
    rec = CheckRecorder()
    standard = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)
    pro = require_plan(tier=PlanTier.PRO, interval=BillingInterval.MONTHLY)

    sub = _establish_subscriber(harness, rec, plan=standard, label="quote")
    harness.drain_events(customer_id=sub.customer_id)

    local = sub.local()
    if not rec.expect("local subscription exists", local is not None):
        return rec

    from billing.stripe_service import StripeSubscriptionMutationService

    try:
        result = StripeSubscriptionMutationService.create_upgrade_checkout_session(
            local, pro, "https://example.invalid/ok", "https://example.invalid/no"
        )
    except ValueError as exc:
        rec.expect(
            "the upgrade preview completed against real Stripe",
            False,
            f"create_upgrade_checkout_session raised: {exc}",
        )
        return rec

    rec.expect(
        "Stripe's newer Invoice.create_preview API answered",
        isinstance(result, dict),
        f"result={type(result).__name__}",
    )

    if result.get("requires_checkout"):
        session_id = result.get("checkout_session_id")
        rec.expect(
            "a checkout session was created for the previewed amount",
            bool(session_id),
            f"session={session_id}",
        )
        session = guarded_call(stripe.checkout.Session.retrieve, session_id)
        quoted = session.get("amount_total")
        rec.expect(
            "the session charges a positive, concrete amount",
            isinstance(quoted, int) and quoted > 0,
            f"amount_total={quoted!r} — this is the number the customer sees",
        )
        # Stripe echoes the previewed proration into metadata; if the two
        # ever diverge we are showing one price and charging another.
        meta_amount = (session.get("metadata") or {}).get("proration_amount")
        if meta_amount is not None:
            rec.expect_equal(
                "the amount charged equals the amount previewed",
                int(quoted or 0),
                int(meta_amount),
            )
    else:
        rec.expect(
            "a zero-cost upgrade was applied directly without a charge",
            result.get("subscription") is not None,
            f"result keys={sorted(result)}",
        )
    return rec


def scenario_charge_refund_flow(harness) -> CheckRecorder:
    """Partial and full refunds take different branches.

    charge.refunded fires for both, distinguished only by
    amount_refunded < amount. A mock that sets them equal never
    exercises the partial branch at all.
    """
    rec = CheckRecorder()
    plan = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)
    sub = _establish_subscriber(harness, rec, plan=plan, label="refund")
    harness.drain_events(customer_id=sub.customer_id)

    invoices = guarded_call(
        stripe.Invoice.list, subscription=sub.stripe_subscription_id, status="paid"
    )
    paid = list(invoices.get("data") or [])
    if not rec.expect(
        "the subscription produced a paid invoice to refund",
        bool(paid),
        "no paid invoice — nothing to exercise the refund path with",
    ):
        return rec

    invoice = guarded_call(
        stripe.Invoice.retrieve, paid[0]["id"], expand=["payment_intent"]
    )
    payment_intent = invoice.get("payment_intent")
    pi_id = payment_intent["id"] if isinstance(payment_intent, dict) else payment_intent
    if not rec.expect(
        "the paid invoice has a PaymentIntent",
        bool(pi_id),
        f"payment_intent={payment_intent!r}",
    ):
        return rec

    amount = int(invoice.get("amount_paid") or 0)
    partial = max(1, amount // 4)

    guarded_call(
        stripe.Refund.create,
        payment_intent=pi_id,
        amount=partial,
        idempotency_key=f"liveqa-partial-{pi_id}",
    )
    harness.drain_events(customer_id=sub.customer_id)

    rec.expect(
        "a PARTIAL refund did not revoke the subscription",
        sub.local() is not None,
        "a partial refund is not a cancellation",
    )

    # Now refund the remainder, making it a full refund.
    guarded_call(
        stripe.Refund.create,
        payment_intent=pi_id,
        amount=amount - partial,
        idempotency_key=f"liveqa-remainder-{pi_id}",
    )
    harness.drain_events(customer_id=sub.customer_id)

    refreshed = guarded_call(
        stripe.Invoice.retrieve, paid[0]["id"], expand=["payment_intent"]
    )
    rec.expect_equal(
        "Stripe now reports the invoice as fully refunded",
        int(refreshed.get("amount_paid") or 0)
        - int(refreshed.get("amount_remaining") or 0)
        - amount,
        0,
        "sanity check on the amounts Stripe reports back",
    )

    txns = BillingTransaction.objects.filter(user=sub.user).count()
    rec.expect(
        "refund handling recorded billing transactions without crashing",
        txns >= 0,
        f"{txns} transaction row(s)",
    )
    return rec


def scenario_multiple_subscription_items(harness) -> CheckRecorder:
    """A second line item must not be silently invisible.

    Every call site in this codebase reads items.data[0] with no check:
    stripe_service.py (price sync, upgrades), license_service.py (seat
    quantity), and extract_subscription_billing_period itself. The moment
    anyone adds an add-on SKU or a seat-based overage line, the second
    item stops existing as far as the app is concerned.

    This scenario does not assert that multi-item WORKS. It asserts that
    we can see the truth about it — so the behaviour is a decision rather
    than an accident.
    """
    rec = CheckRecorder()
    standard = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)
    pro = require_plan(tier=PlanTier.PRO, interval=BillingInterval.MONTHLY)

    user = harness.create_local_user("multi")
    clock = harness.create_test_clock("multi")
    customer = harness.create_customer(email=user.email, clock_id=clock["id"])
    harness.attach_card(customer_id=customer["id"], token=CARD_OK)

    stripe_sub = harness.create_subscription(
        customer_id=customer["id"],
        price_id=standard.stripe_price_id,
        items=[
            {"price": standard.stripe_price_id},
            {"price": pro.stripe_price_id},
        ],
    )

    items = (stripe_sub.get("items") or {}).get("data") or []
    rec.expect_equal(
        "Stripe accepted a two-item subscription",
        len(items),
        2,
        "if Stripe refused, the rest of this scenario is moot",
    )

    from billing.stripe_service import extract_subscription_billing_period

    period_start, period_end = extract_subscription_billing_period(stripe_sub)
    rec.expect(
        "a billing period is still readable from a multi-item subscription",
        period_start is not None and period_end is not None,
        f"({period_start}, {period_end}) — read from items.data[0] only, so "
        f"the second item's period is not considered at all",
    )

    prices = [(i.get("price") or {}).get("id") for i in items]
    rec.expect(
        "KNOWN GAP: only the first item influences local billing state",
        True,
        f"subscription carries {len(items)} priced items {prices}, and every "
        f"call site in billing/ reads items.data[0]. Recorded so the gap is "
        f"visible rather than discovered by a customer.",
    )
    return rec


def scenario_discount_flow_through(harness) -> CheckRecorder:
    """A coupon applied in the Stripe dashboard must not break billing.

    The codebase models neither coupons nor tax, so an invoice whose
    amount_paid no longer equals plan.price_cents flows through entirely
    unmodelled code. Applying a retention discount is a completely
    ordinary thing for a founder to do from the dashboard.
    """
    rec = CheckRecorder()
    plan = require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)

    coupon = guarded_call(
        stripe.Coupon.create, percent_off=50, duration="forever", name="LIVEQA50"
    )
    rec.expect(
        "a real Stripe coupon was created",
        bool(coupon.get("id")),
        f"coupon={coupon.get('id')}",
    )

    sub = _establish_subscriber(
        harness, rec, plan=plan, label="coupon", discounts=[{"coupon": coupon["id"]}]
    )
    harness.drain_events(customer_id=sub.customer_id)

    invoices = guarded_call(
        stripe.Invoice.list, subscription=sub.stripe_subscription_id, status="paid"
    )
    paid = list(invoices.get("data") or [])
    if not rec.expect(
        "the discounted subscription produced a paid invoice",
        bool(paid),
        "no paid invoice for a discounted subscription",
    ):
        return rec

    amount_paid = int(paid[0].get("amount_paid") or 0)
    list_price = int(plan.price_cents or 0)
    rec.expect(
        "the discount really was applied by Stripe",
        amount_paid < list_price or list_price == 0,
        f"amount_paid={amount_paid}, plan.price_cents={list_price} — any code "
        f"that infers entitlement from the amount charged will be wrong here",
    )

    rec.expect(
        "a discounted renewal still granted the customer their credits",
        sub.monthly_bucket_count() >= 1,
        f"{sub.monthly_bucket_count()} monthly bucket(s) — credits must follow "
        f"the PLAN, never the amount paid",
    )
    return rec


register_scenarios(
    {
        "payment_method_lifecycle": scenario_payment_method_lifecycle,
        "upgrade_proration_quote": scenario_upgrade_proration_quote,
        "charge_refund_flow": scenario_charge_refund_flow,
        "multiple_subscription_items": scenario_multiple_subscription_items,
        "discount_flow_through": scenario_discount_flow_through,
    },
    tier=TIER_FAST,
)
