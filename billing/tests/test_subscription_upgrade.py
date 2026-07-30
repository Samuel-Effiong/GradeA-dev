"""
billing/tests/test_subscription_upgrade.py
=============================================
Direct service-layer tests for
StripeSubscriptionMutationService.change_plan().

NOTE: an earlier version of this file drove these tests through
`/api/v1/subscription/upgrade` via APIClient. That URL/action does not
exist anywhere in billing/urls.py or SubscriptionManagementViewSet — the
live "upgrade" flow goes through select-plan ->
IndividualPlanChangeService.select_plan() -> create_upgrade_checkout_session
/ _apply_upgrade_directly, never change_plan() directly (change_plan() is
kept as a direct, no-checkout-confirmation mutation primitive, exercised
here at the service layer). Every test in the old version 404'd
unconditionally. This version calls the service method directly instead.

All Stripe API calls are mocked via `@patch("stripe.Subscription")` /
`@patch("stripe.Invoice")` — patching the attributes on the real `stripe`
module directly (rather than replacing the whole module reference inside
stripe_service.py) deliberately leaves `stripe.error.*` untouched as the
real exception classes. Patching the whole module would make any
`except stripe.error.StripeError` in the implementation raise a TypeError
the moment an exception needs to match against it, since a MagicMock
attribute isn't a real exception class.
"""

from unittest.mock import patch

import stripe as real_stripe
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    BillingInterval,
    CreditBucketType,
    CreditLedger,
    CreditWallet,
    PendingChangeType,
    PlanCategory,
    PlanTier,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.stripe_service import StripeSubscriptionMutationService
from users.models import UserTypes

CustomUser = get_user_model()


def make_plan(
    name,
    tier,
    price_cents,
    monthly_credits,
    stripe_price_id,
    category=PlanCategory.INDIVIDUAL,
    interval=BillingInterval.MONTHLY,
    carry_over_percent=0,
    carry_over_max=0,
    carry_over_expiry_months=1,
    max_bank=None,
):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=name,
        category=category,
        tier=tier,
        interval=interval,
        price_cents=price_cents,
        monthly_credits=monthly_credits,
        stripe_price_id=stripe_price_id,
        carry_over_percent=carry_over_percent,
        carry_over_max=carry_over_max,
        carry_over_expiry_months=carry_over_expiry_months,
        max_bank=max_bank,
        is_active=True,
    )


class SubscriptionUpgradeTestCase(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="teacher@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )

        self.standard_plan = make_plan(
            "STANDARD",
            PlanTier.STANDARD,
            price_cents=999,
            monthly_credits=10_000_000,
            stripe_price_id="price_standard",
        )
        self.pro_plan = make_plan(
            "PRO",
            PlanTier.PRO,
            price_cents=2999,
            monthly_credits=30_000_000,
            stripe_price_id="price_pro",
        )

        self.now = timezone.now()
        self.original_cycle_start = self.now
        self.original_cycle_end = self.now + relativedelta(months=1)
        self.current_sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.standard_plan,
            is_active=True,
            billing_cycle_start=self.original_cycle_start,
            billing_cycle_end=self.original_cycle_end,
            next_credit_grant_at=self.original_cycle_end,
            stripe_subscription_id="sub_test_123",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(
            user=self.user, defaults={"stripe_customer_id": "cus_test_123"}
        )

    @staticmethod
    def _stripe_subscription_payload(
        price_id, item_id="si_test_1", latest_invoice="in_test_1"
    ):
        return {
            "id": "sub_test_123",
            "items": {"data": [{"id": item_id, "price": {"id": price_id}}]},
            "latest_invoice": latest_invoice,
        }

    def _change_plan(self, new_plan, proration_behavior="always_invoice"):
        return StripeSubscriptionMutationService.change_plan(
            self.current_sub, new_plan, proration_behavior=proration_behavior
        )

    # ------------------------------------------------------------------
    # Validation — these must reject BEFORE touching Stripe at all
    # ------------------------------------------------------------------

    @patch("stripe.Subscription")
    def test_rejects_subscription_with_no_stripe_id(self, mock_subscription):
        self.current_sub.stripe_subscription_id = None
        self.current_sub.save(update_fields=["stripe_subscription_id"])

        with self.assertRaises(ValueError) as ctx:
            self._change_plan(self.pro_plan)

        self.assertIn("Contact support", str(ctx.exception))
        mock_subscription.modify.assert_not_called()

    @patch("stripe.Subscription")
    def test_rejects_new_plan_with_no_stripe_price_id(self, mock_subscription):
        self.pro_plan.stripe_price_id = ""
        self.pro_plan.save(update_fields=["stripe_price_id"])

        with self.assertRaises(ValueError) as ctx:
            self._change_plan(self.pro_plan)

        self.assertIn("stripe_price_id", str(ctx.exception))
        mock_subscription.modify.assert_not_called()

    # ------------------------------------------------------------------
    # Success path — same-interval (MONTHLY -> MONTHLY) immediate upgrade
    # ------------------------------------------------------------------

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_same_interval_upgrade_applies_in_place(
        self, mock_subscription, mock_invoice
    ):
        mock_subscription.retrieve.side_effect = [
            self._stripe_subscription_payload("price_standard"),
            self._stripe_subscription_payload("price_pro"),
        ]
        mock_subscription.modify.return_value = None
        mock_invoice.retrieve.return_value = {
            "id": "in_test_1",
            "status": "paid",
            "payment_intent": None,
        }

        updated_sub = self._change_plan(self.pro_plan)

        # Same row mutated in place — no deactivate + recreate, since
        # Stripe's billing_cycle_anchor does not move on a same-interval
        # price swap.
        self.assertEqual(updated_sub.id, self.current_sub.id)
        self.assertTrue(updated_sub.is_active)
        self.assertEqual(updated_sub.plan_id, self.pro_plan.id)
        self.assertEqual(updated_sub.stripe_subscription_id, "sub_test_123")
        self.assertEqual(updated_sub.stripe_status, StripeSubscriptionStatus.ACTIVE)
        self.assertEqual(updated_sub.billing_cycle_start, self.original_cycle_start)
        self.assertEqual(updated_sub.billing_cycle_end, self.original_cycle_end)

        self.assertEqual(UserSubscription.objects.filter(user=self.user).count(), 1)

        monthly_bucket = self.wallet.buckets.filter(
            bucket_type=CreditBucketType.MONTHLY
        ).first()
        self.assertIsNotNone(monthly_bucket)
        self.assertEqual(monthly_bucket.total_credits, self.pro_plan.monthly_credits)
        # New bucket expires on the EXISTING cycle clock, not a freshly
        # computed "now + 1 month".
        self.assertEqual(monthly_bucket.expires_at, self.original_cycle_end)

        mock_subscription.modify.assert_called_once()
        _, kwargs = mock_subscription.modify.call_args
        self.assertEqual(kwargs["proration_behavior"], "always_invoice")
        self.assertEqual(kwargs["items"][0]["price"], "price_pro")
        mock_invoice.void_invoice.assert_not_called()

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_upgrade_succeeds_when_no_proration_invoice_is_generated(
        self, mock_subscription, mock_invoice
    ):
        # E.g. the two plans happen to be priced identically — Stripe
        # doesn't generate an invoice at all in that case.
        after = self._stripe_subscription_payload("price_pro", latest_invoice=None)
        mock_subscription.retrieve.side_effect = [
            self._stripe_subscription_payload("price_standard"),
            after,
        ]
        mock_subscription.modify.return_value = None

        updated_sub = self._change_plan(self.pro_plan)

        self.assertEqual(updated_sub.plan_id, self.pro_plan.id)
        mock_invoice.retrieve.assert_not_called()

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_upgrade_clears_previously_scheduled_change(
        self, mock_subscription, mock_invoice
    ):
        """
        An immediate upgrade always supersedes any previously scheduled
        change (downgrade/deferred upgrade/lateral). Since the update now
        happens on the SAME row, apply_immediate_plan_change() must
        explicitly clear pending_plan/pending_change_type/
        pending_change_note/stripe_schedule_id — release_schedule() itself
        only touches Stripe's side, never the local fields.
        """
        self.current_sub.pending_plan = self.standard_plan
        self.current_sub.pending_change_type = PendingChangeType.DOWNGRADE
        self.current_sub.pending_change_note = "some stale note"
        self.current_sub.stripe_schedule_id = "sub_sched_stale123"
        self.current_sub.save(
            update_fields=[
                "pending_plan",
                "pending_change_type",
                "pending_change_note",
                "stripe_schedule_id",
            ]
        )

        mock_subscription.retrieve.side_effect = [
            self._stripe_subscription_payload("price_standard"),
            self._stripe_subscription_payload("price_pro"),
        ]
        mock_subscription.modify.return_value = None
        mock_invoice.retrieve.return_value = {
            "id": "in_test_1",
            "status": "paid",
            "payment_intent": None,
        }

        with patch(
            "billing.stripe_service.StripeSubscriptionScheduleService.release_schedule"
        ) as mock_release:
            updated_sub = self._change_plan(self.pro_plan)
            mock_release.assert_called_once()

        self.assertIsNone(updated_sub.pending_plan_id)
        self.assertIsNone(updated_sub.pending_change_type)
        self.assertIsNone(updated_sub.pending_change_note)
        self.assertIsNone(updated_sub.stripe_schedule_id)

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_upgrade_preserves_overage_blocks_used(
        self, mock_subscription, mock_invoice
    ):
        """
        A same-interval immediate upgrade does not start a new Stripe
        billing period, so the overage counter for the CURRENT period must
        not be reset — resetting it would hand out extra overage capacity
        for a cycle that hasn't actually renewed.
        """
        self.wallet.overage_blocks_used = 2
        self.wallet.save(update_fields=["overage_blocks_used"])

        mock_subscription.retrieve.side_effect = [
            self._stripe_subscription_payload("price_standard"),
            self._stripe_subscription_payload("price_pro"),
        ]
        mock_subscription.modify.return_value = None
        mock_invoice.retrieve.return_value = {
            "id": "in_test_1",
            "status": "paid",
            "payment_intent": None,
        }

        self._change_plan(self.pro_plan)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.overage_blocks_used, 2)

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_upgrade_rolls_over_unused_credits_under_new_plan_cap(
        self, mock_subscription, mock_invoice
    ):
        rollover_plan = make_plan(
            "PRO_ROLLOVER",
            PlanTier.PRO,
            price_cents=2999,
            monthly_credits=30_000_000,
            stripe_price_id="price_pro_rollover",
            carry_over_percent=50,
            carry_over_max=100_000_000,
            carry_over_expiry_months=1,
            max_bank=None,
        )

        old_bucket = self.wallet.buckets.create(
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=10_000_000,
            used_credits=6_000_000,  # 4,000,000 unused
            expires_at=self.original_cycle_end,
        )

        mock_subscription.retrieve.side_effect = [
            self._stripe_subscription_payload("price_standard"),
            self._stripe_subscription_payload("price_pro_rollover"),
        ]
        mock_subscription.modify.return_value = None
        mock_invoice.retrieve.return_value = {
            "id": "in_test_1",
            "status": "paid",
            "payment_intent": None,
        }

        self._change_plan(rollover_plan)

        old_bucket.refresh_from_db()
        self.assertLessEqual(old_bucket.expires_at, timezone.now())

        carry_bucket = self.wallet.buckets.filter(
            bucket_type=CreditBucketType.CARRY_OVER
        ).first()
        self.assertIsNotNone(carry_bucket)
        # 50% of 4,000,000 unused = 2,000,000, no max_bank cap in play.
        self.assertEqual(carry_bucket.total_credits, 2_000_000)

        rollover_ledger = CreditLedger.objects.filter(bucket=carry_bucket).first()
        self.assertIsNotNone(rollover_ledger)

    # ------------------------------------------------------------------
    # Interval-crossing (MONTHLY -> ANNUAL): Stripe DOES reset its own
    # billing cycle here, so the local cycle must reset to match instead
    # of being preserved.
    # ------------------------------------------------------------------

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_interval_crossing_upgrade_resets_billing_cycle(
        self, mock_subscription, mock_invoice
    ):
        annual_plan = make_plan(
            "PRO_ANNUAL",
            PlanTier.PRO,
            price_cents=29999,
            monthly_credits=30_000_000,
            stripe_price_id="price_pro_annual",
            interval=BillingInterval.ANNUAL,
        )

        mock_subscription.retrieve.side_effect = [
            self._stripe_subscription_payload("price_standard"),
            self._stripe_subscription_payload("price_pro_annual"),
        ]
        mock_subscription.modify.return_value = None
        mock_invoice.retrieve.return_value = {
            "id": "in_test_1",
            "status": "paid",
            "payment_intent": None,
        }

        before = timezone.now()
        updated_sub = self._change_plan(annual_plan)
        after = timezone.now()

        self.assertEqual(updated_sub.plan_id, annual_plan.id)
        # Cycle must have genuinely reset to "now" (interval crossing),
        # NOT preserved from the old monthly cycle.
        self.assertGreaterEqual(updated_sub.billing_cycle_start, before)
        self.assertLessEqual(updated_sub.billing_cycle_start, after)
        self.assertGreater(updated_sub.billing_cycle_end, self.original_cycle_end)
        # ANNUAL plans still refresh credits monthly.
        self.assertLess(updated_sub.next_credit_grant_at, updated_sub.billing_cycle_end)

    # ------------------------------------------------------------------
    # Failure paths — must revert the Stripe price AND grant no credits
    # ------------------------------------------------------------------

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_declined_invoice_reverts_price_and_grants_no_credits(
        self, mock_subscription, mock_invoice
    ):
        mock_subscription.retrieve.side_effect = [
            self._stripe_subscription_payload("price_standard"),
            self._stripe_subscription_payload("price_pro"),
        ]
        mock_subscription.modify.return_value = None
        mock_invoice.retrieve.return_value = {
            "id": "in_test_1",
            "status": "open",
            "payment_intent": {"status": "requires_payment_method"},
        }

        with self.assertRaises(ValueError) as ctx:
            self._change_plan(self.pro_plan)
        self.assertIn("payment failed", str(ctx.exception))

        # Local plan must NOT have changed.
        self.current_sub.refresh_from_db()
        self.assertTrue(self.current_sub.is_active)
        self.assertEqual(self.current_sub.plan_id, self.standard_plan.id)
        self.assertEqual(self.current_sub.billing_cycle_end, self.original_cycle_end)

        # No credits granted for a plan that was never actually paid for.
        self.assertFalse(
            self.wallet.buckets.filter(
                bucket_type=CreditBucketType.MONTHLY,
                total_credits=self.pro_plan.monthly_credits,
            ).exists()
        )

        # Subscription item must have been reverted back to the old price.
        self.assertEqual(mock_subscription.modify.call_count, 2)
        revert_call = mock_subscription.modify.call_args_list[-1]
        self.assertEqual(revert_call.kwargs["items"][0]["price"], "price_standard")
        self.assertEqual(revert_call.kwargs["proration_behavior"], "none")

        # Unpaid invoice should have been voided.
        mock_invoice.void_invoice.assert_called_once_with("in_test_1")

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_requires_action_gives_clear_message_and_reverts(
        self, mock_subscription, mock_invoice
    ):
        mock_subscription.retrieve.side_effect = [
            self._stripe_subscription_payload("price_standard"),
            self._stripe_subscription_payload("price_pro"),
        ]
        mock_subscription.modify.return_value = None
        mock_invoice.retrieve.return_value = {
            "id": "in_test_1",
            "status": "open",
            "payment_intent": {"status": "requires_action"},
        }

        with self.assertRaises(ValueError) as ctx:
            self._change_plan(self.pro_plan)
        self.assertIn("authentication", str(ctx.exception))

        self.current_sub.refresh_from_db()
        self.assertEqual(self.current_sub.plan_id, self.standard_plan.id)

        revert_call = mock_subscription.modify.call_args_list[-1]
        self.assertEqual(revert_call.kwargs["items"][0]["price"], "price_standard")

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_card_declined_synchronously_during_modify_reverts(
        self, mock_subscription, mock_invoice
    ):
        # Stripe can reject some cards immediately during modify() itself,
        # rather than via the resulting invoice — uses the REAL exception
        # class since stripe.error is untouched by these patches. Every
        # call to modify() (including the revert attempt) raises the same
        # error, exercising the "revert itself also fails" sub-case too.
        mock_subscription.retrieve.return_value = self._stripe_subscription_payload(
            "price_standard"
        )
        mock_subscription.modify.side_effect = real_stripe.error.CardError(
            message="Your card was declined.",
            param=None,
            code="card_declined",
        )

        with self.assertRaises(ValueError) as ctx:
            self._change_plan(self.pro_plan)
        self.assertIn("declined", str(ctx.exception))

        self.current_sub.refresh_from_db()
        self.assertEqual(self.current_sub.plan_id, self.standard_plan.id)
        mock_invoice.retrieve.assert_not_called()

        # The original modify() call, plus a best-effort revert attempt —
        # both raise CardError, but only the FIRST exception is what
        # surfaces to the caller (assertRaises above already confirmed
        # the message is the original decline, not a revert error).
        self.assertEqual(mock_subscription.modify.call_count, 2)
        revert_call = mock_subscription.modify.call_args_list[-1]
        self.assertEqual(revert_call.kwargs["items"][0]["price"], "price_standard")

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_card_declined_synchronously_reverts_price_when_revert_succeeds(
        self, mock_subscription, mock_invoice
    ):
        mock_subscription.retrieve.return_value = self._stripe_subscription_payload(
            "price_standard"
        )
        mock_subscription.modify.side_effect = [
            real_stripe.error.CardError(
                message="Your card was declined.", param=None, code="card_declined"
            ),
            None,  # the revert call succeeds
        ]

        with self.assertRaises(ValueError) as ctx:
            self._change_plan(self.pro_plan)
        self.assertIn("declined", str(ctx.exception))

        self.assertEqual(mock_subscription.modify.call_count, 2)
        revert_call = mock_subscription.modify.call_args_list[-1]
        self.assertEqual(revert_call.kwargs["items"][0]["price"], "price_standard")
        self.assertEqual(revert_call.kwargs["proration_behavior"], "none")
        # No invoice reference exists yet on this failure path — nothing to void.
        mock_invoice.void_invoice.assert_not_called()

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_revert_failure_after_declined_invoice_is_logged_not_raised(
        self, mock_subscription, mock_invoice
    ):
        """
        If the revert call ITSELF fails after a declined invoice, the
        original "payment failed" error should still surface to the user —
        not be replaced by the revert's own StripeError. The inconsistent
        Stripe state is logged for manual reconciliation instead.
        """
        mock_subscription.retrieve.side_effect = [
            self._stripe_subscription_payload("price_standard"),
            self._stripe_subscription_payload("price_pro"),
        ]
        mock_subscription.modify.side_effect = [
            None,  # the original upgrade modify() call succeeds
            real_stripe.error.APIConnectionError("network blip during revert"),
        ]
        mock_invoice.retrieve.return_value = {
            "id": "in_test_1",
            "status": "open",
            "payment_intent": {"status": "requires_payment_method"},
        }

        with self.assertRaises(ValueError) as ctx:
            self._change_plan(self.pro_plan)
        self.assertIn("payment failed", str(ctx.exception))
