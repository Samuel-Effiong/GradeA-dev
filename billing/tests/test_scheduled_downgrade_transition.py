"""
billing/tests/test_scheduled_downgrade_transition.py
====================================================
Scheduled (deferred) plan changes must actually take effect — without
turning mid-cycle prorations into free credit cycles.

THE BUG
-------
A Stripe SubscriptionSchedule phase transition — how a deferred downgrade
bills at the period boundary — raises its invoice with
`billing_reason="subscription_update"`, which was not in
`RENEWAL_BILLING_REASONS`. So the handler recorded the money and skipped
the renewal. Because the renewal is the ONLY thing that consumes
`pending_plan` (services.py: `target_plan = user_subscription.pending_plan
or user_subscription.plan`), the downgrade never landed.

Observed on a real Stripe subscription: a 1499c `subscription_update`
invoice was raised and PAID, a matching BillingTransaction was written,
and yet the local plan stayed PRO, `pending_plan` stayed STANDARD, and NO
new credit bucket was granted. The customer paid for a cycle and received
nothing, on a plan they had asked to leave.

WHY THE OBVIOUS FIX IS WRONG
----------------------------
Adding "subscription_update" to RENEWAL_BILLING_REASONS also matches
MID-CYCLE upgrade prorations, which would then grant a whole extra cycle
of credits — the exact incident that set of reasons was narrowed to
prevent (see test_renewal_guards.py).

THE DISCRIMINATOR, MEASURED AGAINST LIVE STRIPE (2026-09-07)
------------------------------------------------------------
    scheduled downgrade at boundary : subscription_update,
        period 2026-10-07 -> 2026-11-06   BEYOND the local cycle end
    mid-cycle upgrade always_invoice: subscription_update,
        period 2026-09-07 -> 2026-10-07   ENDS AT the local cycle end

A genuine period transition pays for time we have not granted yet. A
proration only ever covers the remainder of the cycle we are already in.
So the test is the INVOICE'S OWN PERIOD, not the reason and not the clock
— which also makes it idempotent for free: once the renewal has moved
`billing_cycle_end` to the invoice's period end, a redelivery of that same
invoice no longer extends past it.
"""

from datetime import timedelta
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    BillingInterval,
    BillingTransaction,
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    PendingChangeType,
    PlanCategory,
    PlanTier,
    PlanType,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.stripe_service import StripeWebhookHandler
from users.models import UserTypes

CustomUser = get_user_model()

STRIPE_SUB = "sub_sched_transition"


def invoice(*, billing_reason, period_start, period_end, amount=1499):
    """
    Shaped like a real invoice under API 2026-02-25.clover: no top-level
    `subscription` (it lives under `parent`), period on the line items.
    """
    return {
        "id": f"in_{billing_reason}_{int(period_end.timestamp())}",
        "status": "paid",
        "billing_reason": billing_reason,
        "amount_paid": amount,
        "currency": "usd",
        "hosted_invoice_url": "https://stripe.test/i/1",
        "parent": {"subscription_details": {"subscription": STRIPE_SUB}},
        "period_start": int(period_start.timestamp()),
        "period_end": int(period_end.timestamp()),
        "lines": {
            "data": [
                {
                    "period": {
                        "start": int(period_start.timestamp()),
                        "end": int(period_end.timestamp()),
                    }
                }
            ]
        },
    }


class ScheduledDowngradeTransitionTests(TestCase):
    def setUp(self):
        self.standard = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Standard",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            interval=BillingInterval.MONTHLY,
            monthly_credits=10_000,
            overage_block_size=500,
            stripe_price_id="price_std",
            is_active=True,
        )
        self.pro = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Pro",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.PRO,
            interval=BillingInterval.MONTHLY,
            monthly_credits=20_000,
            overage_block_size=500,
            stripe_price_id="price_pro",
            is_active=True,
        )
        self.user = CustomUser.objects.create_user(
            email="sched.downgrade@billing.test",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        CreditBucket.objects.filter(wallet=self.wallet).delete()

        # A PRO subscriber at the boundary with a downgrade scheduled.
        self.cycle_end = timezone.now() - timedelta(minutes=1)
        self.sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.pro,
            is_active=True,
            is_trial=False,
            billing_cycle_start=self.cycle_end - relativedelta(months=1),
            billing_cycle_end=self.cycle_end,
            next_credit_grant_at=self.cycle_end,
            stripe_subscription_id=STRIPE_SUB,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
            pending_plan=self.standard,
            pending_change_type=PendingChangeType.DOWNGRADE,
            stripe_schedule_id="sub_sched_1",
        )
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=20_000,
            used_credits=0,
            expires_at=self.cycle_end,
        )

    def _live(self):
        return (
            UserSubscription.objects.filter(user=self.user, is_active=True)
            .order_by("-created_at")
            .first()
        )

    def _monthly_buckets(self):
        return CreditBucket.objects.filter(
            wallet=self.wallet, bucket_type=CreditBucketType.MONTHLY
        ).count()

    def _deliver(self, inv):
        with patch(
            "billing.stripe_service.StripeSubscriptionMutationService.sync_price"
        ):
            StripeWebhookHandler.handle_invoice_payment_succeeded(inv)

    def schedule_transition_invoice(self):
        """What a SubscriptionSchedule phase change actually sends."""
        return invoice(
            billing_reason="subscription_update",
            period_start=self.cycle_end,
            period_end=self.cycle_end + relativedelta(months=1),
        )

    def proration_invoice(self):
        """A MID-CYCLE upgrade: covers only the rest of the current cycle."""
        return invoice(
            billing_reason="subscription_update",
            period_start=self.cycle_end - timedelta(days=10),
            period_end=self.cycle_end,
            amount=1000,
        )

    # --- the bug ---------------------------------------------------------

    def test_a_scheduled_downgrade_applies_at_the_boundary(self):
        """THE REGRESSION: the plan must actually change."""
        self._deliver(self.schedule_transition_invoice())

        live = self._live()
        self.assertEqual(
            live.plan_id,
            self.standard.id,
            "the scheduled downgrade did not take effect — the customer is "
            "still on the plan they asked to leave",
        )

    def test_the_pending_plan_is_cleared_once_applied(self):
        self._deliver(self.schedule_transition_invoice())

        self.assertIsNone(
            self._live().pending_plan_id,
            "pending_plan stayed set, so the change would try to apply again",
        )

    def test_the_customer_receives_exactly_one_new_credit_cycle(self):
        """
        Not zero (paid and got nothing — the observed bug) and not two
        (the double-grant this guard exists to prevent).
        """
        before = self._monthly_buckets()

        self._deliver(self.schedule_transition_invoice())

        self.assertEqual(self._monthly_buckets(), before + 1)

    def test_the_new_credits_are_the_NEW_plans_allocation(self):
        """Downgraded customers must get the downgraded allowance."""
        self._deliver(self.schedule_transition_invoice())

        newest = (
            CreditBucket.objects.filter(
                wallet=self.wallet, bucket_type=CreditBucketType.MONTHLY
            )
            .order_by("-created_at")
            .first()
        )
        self.assertEqual(newest.total_credits, self.standard.monthly_credits)

    def test_the_billing_period_advances_to_stripes_period(self):
        inv = self.schedule_transition_invoice()

        self._deliver(inv)

        self.assertGreater(self._live().billing_cycle_end, self.cycle_end)

    def test_the_money_is_still_recorded(self):
        self._deliver(self.schedule_transition_invoice())

        self.assertTrue(
            BillingTransaction.objects.filter(user=self.user).exists(),
            "the payment vanished from the money ledger",
        )

    # --- the thing that must NOT regress ---------------------------------

    def test_a_midcycle_proration_does_NOT_renew(self):
        """
        The previously-fixed double-credit bug. A proration carries the same
        billing_reason but buys no new time, so it must never grant a cycle.
        """
        # Put the subscription mid-cycle, where a proration actually occurs.
        future_end = timezone.now() + relativedelta(months=1)
        UserSubscription.objects.filter(pk=self.sub.pk).update(
            billing_cycle_end=future_end
        )
        self.sub.refresh_from_db()
        before_buckets = self._monthly_buckets()
        before_plan = self._live().plan_id

        self._deliver(
            invoice(
                billing_reason="subscription_update",
                period_start=timezone.now() - timedelta(days=10),
                period_end=future_end,  # ends AT the cycle end, not beyond
                amount=1000,
            )
        )

        self.assertEqual(
            self._monthly_buckets(),
            before_buckets,
            "a mid-cycle proration granted a free credit cycle",
        )
        self.assertEqual(self._live().plan_id, before_plan)

    def test_a_midcycle_proration_still_records_the_money(self):
        future_end = timezone.now() + relativedelta(months=1)
        UserSubscription.objects.filter(pk=self.sub.pk).update(
            billing_cycle_end=future_end
        )
        self.sub.refresh_from_db()

        self._deliver(
            invoice(
                billing_reason="subscription_update",
                period_start=timezone.now() - timedelta(days=10),
                period_end=future_end,
                amount=1000,
            )
        )

        self.assertTrue(BillingTransaction.objects.filter(user=self.user).exists())

    # --- idempotency ------------------------------------------------------

    def test_redelivering_the_transition_invoice_does_not_renew_twice(self):
        """
        Stripe redelivers. Once the renewal has moved billing_cycle_end to
        the invoice's period end, that invoice no longer extends past it —
        so the second delivery is inert without needing a separate ledger.
        """
        inv = self.schedule_transition_invoice()
        self._deliver(inv)
        after_first = self._monthly_buckets()

        self._deliver(inv)

        self.assertEqual(
            self._monthly_buckets(),
            after_first,
            "a redelivered schedule-transition invoice granted a second cycle",
        )

    def test_a_cancelled_row_is_not_resurrected_by_a_transition(self):
        UserSubscription.objects.filter(pk=self.sub.pk).update(is_active=False)

        self._deliver(self.schedule_transition_invoice())

        self.assertFalse(
            UserSubscription.objects.filter(
                user=self.user, is_active=True, plan=self.standard
            ).exists(),
            "a schedule transition resurrected a cancelled subscription",
        )

    # --- ordinary renewals must be untouched ------------------------------

    def test_a_normal_subscription_cycle_invoice_still_renews(self):
        UserSubscription.objects.filter(pk=self.sub.pk).update(
            pending_plan=None, pending_change_type=None, stripe_schedule_id=None
        )
        self.sub.refresh_from_db()
        before = self._monthly_buckets()

        self._deliver(
            invoice(
                billing_reason="subscription_cycle",
                period_start=self.cycle_end,
                period_end=self.cycle_end + relativedelta(months=1),
            )
        )

        self.assertEqual(self._monthly_buckets(), before + 1)
        self.assertEqual(self._live().plan_id, self.pro.id)

    def test_an_unrelated_billing_reason_still_does_not_renew(self):
        before = self._monthly_buckets()

        self._deliver(
            invoice(
                billing_reason="manual",
                period_start=self.cycle_end,
                period_end=self.cycle_end + relativedelta(months=1),
            )
        )

        self.assertEqual(self._monthly_buckets(), before)
