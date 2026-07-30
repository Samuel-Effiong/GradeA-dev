"""
billing/tests/test_subscription_cycle_integrity.py
====================================================
Locks in the fix for the billing-cycle drift bug: an immediate,
same-interval plan change (upgrade via change_plan() / _apply_upgrade_directly()
/ the upgrade-checkout webhook) must NEVER reset billing_cycle_start,
billing_cycle_end, or next_credit_grant_at — Stripe's own
billing_cycle_anchor does not move on a plain item/price swap, so the
local record must not pretend otherwise. Only a genuine Stripe cycle
reset (brand new checkout, a real periodic renewal, or an
INTERVAL-CROSSING change) may reset those fields.

Covers, across all three immediate-upgrade entry points plus the
downstream consumers of billing_cycle_end:
  - StripeSubscriptionMutationService._apply_upgrade_directly()
  - StripeWebhookHandler._handle_individual_upgrade_checkout_completed()
  - SubscriptionService.apply_immediate_plan_change() directly
  - Downgrade scheduling built on billing_cycle_end after a prior upgrade
  - A multi-month, multi-year renewal simulation proving the real
    invoice.payment_succeeded renewal is no longer silently swallowed by
    the idempotency guard after a mid-cycle upgrade.

All Stripe API calls are mocked via `@patch("stripe.X")` on the real
`stripe` module's attributes, leaving `stripe.error.*` untouched as real
exception classes (see test_subscription_upgrade.py for why).
"""

import datetime
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    BillingInterval,
    CreditBucketType,
    CreditWallet,
    PlanCategory,
    PlanTier,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.services import SubscriptionService
from billing.stripe_service import (
    StripeSubscriptionMutationService,
    StripeSubscriptionScheduleService,
    StripeWebhookHandler,
)
from users.models import UserTypes

CustomUser = get_user_model()


class FakeStripeObject(dict):
    """
    Minimal stand-in for Stripe's response objects, which support both
    dict-style (`obj["id"]`) and attribute-style (`obj.id`) access. Plain
    dicts only support the former; some call sites in this codebase
    (e.g. StripeSubscriptionScheduleService._create_fresh_schedule) use
    the latter.
    """

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as exc:
            raise AttributeError(item) from exc


def make_plan(
    name,
    tier,
    price_cents,
    monthly_credits,
    stripe_price_id,
    category=PlanCategory.INDIVIDUAL,
    interval=BillingInterval.MONTHLY,
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
        is_active=True,
    )


class ApplyImmediatePlanChangeDirectTestCase(TestCase):
    """Unit tests for SubscriptionService.apply_immediate_plan_change() itself."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="direct@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.standard_plan = make_plan(
            "STANDARD", PlanTier.STANDARD, 999, 10_000_000, "price_standard"
        )
        self.pro_plan = make_plan("PRO", PlanTier.PRO, 2999, 30_000_000, "price_pro")
        self.annual_plan = make_plan(
            "PRO_ANNUAL",
            PlanTier.PRO,
            29999,
            30_000_000,
            "price_pro_annual",
            interval=BillingInterval.ANNUAL,
        )
        self.cycle_start = timezone.now()
        self.cycle_end = self.cycle_start + relativedelta(months=1)
        self.sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.standard_plan,
            is_active=True,
            billing_cycle_start=self.cycle_start,
            billing_cycle_end=self.cycle_end,
            next_credit_grant_at=self.cycle_end,
            stripe_subscription_id="sub_direct_1",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )
        CreditWallet.objects.get_or_create(user=self.user)

    def test_rejects_interval_crossing(self):
        with self.assertRaises(ValueError) as ctx:
            SubscriptionService.apply_immediate_plan_change(self.sub, self.annual_plan)
        self.assertIn("cannot cross billing intervals", str(ctx.exception))

    def test_rejects_inactive_subscription(self):
        self.sub.is_active = False
        self.sub.save(update_fields=["is_active"])
        with self.assertRaises(ValueError):
            SubscriptionService.apply_immediate_plan_change(self.sub, self.pro_plan)

    def test_preserves_cycle_and_swaps_plan_in_place(self):
        updated = SubscriptionService.apply_immediate_plan_change(
            self.sub, self.pro_plan
        )
        self.assertEqual(updated.id, self.sub.id)
        self.assertEqual(updated.plan_id, self.pro_plan.id)
        self.assertEqual(updated.billing_cycle_start, self.cycle_start)
        self.assertEqual(updated.billing_cycle_end, self.cycle_end)
        self.assertEqual(updated.next_credit_grant_at, self.cycle_end)
        self.assertEqual(UserSubscription.objects.filter(user=self.user).count(), 1)


class UpgradeEntryPointCycleIntegrityTestCase(TestCase):
    """
    All THREE live immediate-upgrade entry points must agree on which
    plan changes preserve the cycle (same-interval) vs reset it
    (interval-crossing).
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="entrypoints@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.standard_plan = make_plan(
            "STANDARD", PlanTier.STANDARD, 999, 10_000_000, "price_standard"
        )
        self.pro_plan = make_plan("PRO", PlanTier.PRO, 2999, 30_000_000, "price_pro")
        self.annual_plan = make_plan(
            "PRO_ANNUAL",
            PlanTier.PRO,
            29999,
            30_000_000,
            "price_pro_annual",
            interval=BillingInterval.ANNUAL,
        )
        self.cycle_start = timezone.now()
        self.cycle_end = self.cycle_start + relativedelta(months=1)
        CreditWallet.objects.get_or_create(user=self.user)

    def _make_sub(self, plan):
        return UserSubscription.objects.create(
            user=self.user,
            plan=plan,
            is_active=True,
            billing_cycle_start=self.cycle_start,
            billing_cycle_end=self.cycle_end,
            next_credit_grant_at=self.cycle_end,
            stripe_subscription_id="sub_entry_1",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )

    @patch("stripe.Subscription")
    def test_apply_upgrade_directly_preserves_cycle_for_same_interval(
        self, mock_subscription
    ):
        sub = self._make_sub(self.standard_plan)
        mock_subscription.modify.return_value = None

        updated = StripeSubscriptionMutationService._apply_upgrade_directly(
            sub, self.pro_plan, item_id="si_1"
        )

        self.assertEqual(updated.id, sub.id)
        self.assertEqual(updated.billing_cycle_end, self.cycle_end)
        self.assertEqual(updated.plan_id, self.pro_plan.id)

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_apply_upgrade_directly_resets_cycle_for_interval_crossing(
        self, mock_subscription, mock_invoice
    ):
        sub = self._make_sub(self.standard_plan)
        mock_subscription.modify.return_value = None
        mock_subscription.retrieve.return_value = {
            "id": "sub_entry_1",
            "latest_invoice": None,
        }

        before = timezone.now()
        updated = StripeSubscriptionMutationService._apply_upgrade_directly(
            sub, self.annual_plan, item_id="si_1"
        )
        after = timezone.now()

        self.assertGreaterEqual(updated.billing_cycle_start, before)
        self.assertLessEqual(updated.billing_cycle_start, after)
        self.assertGreater(updated.billing_cycle_end, self.cycle_end)
        self.assertEqual(updated.plan_id, self.annual_plan.id)

    def _webhook_metadata(self, sub, new_plan):
        return {
            "user_id": str(self.user.id),
            "user_subscription_id": str(sub.id),
            "new_plan_id": str(new_plan.id),
            "stripe_subscription_id": sub.stripe_subscription_id,
            "stripe_item_id": "si_1",
            "proration_amount": "500",
        }

    @patch("stripe.Subscription")
    def test_webhook_preserves_cycle_for_same_interval(self, mock_subscription):
        sub = self._make_sub(self.standard_plan)
        mock_subscription.modify.return_value = None

        session = {
            "id": "cs_test_1",
            "amount_total": 500,
            "currency": "usd",
            "payment_intent": "pi_test_1",
        }
        StripeWebhookHandler._handle_individual_upgrade_checkout_completed(
            session, self._webhook_metadata(sub, self.pro_plan)
        )

        sub.refresh_from_db()
        self.assertEqual(sub.plan_id, self.pro_plan.id)
        self.assertEqual(sub.billing_cycle_end, self.cycle_end)

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_webhook_resets_cycle_for_interval_crossing(
        self, mock_subscription, mock_invoice
    ):
        sub = self._make_sub(self.standard_plan)
        mock_subscription.modify.return_value = None
        mock_subscription.retrieve.return_value = {
            "id": "sub_entry_1",
            "latest_invoice": None,
        }

        session = {
            "id": "cs_test_2",
            "amount_total": 5000,
            "currency": "usd",
            "payment_intent": "pi_test_2",
        }
        before = timezone.now()
        StripeWebhookHandler._handle_individual_upgrade_checkout_completed(
            session, self._webhook_metadata(sub, self.annual_plan)
        )
        after = timezone.now()

        # Interval-crossing genuinely resets Stripe's cycle, so (like
        # activate_subscription() everywhere else) this deactivates the
        # OLD row and creates a NEW one — `sub` itself stays on the old
        # plan/row and must be looked up fresh.
        new_sub = UserSubscription.objects.get(user=self.user, is_active=True)
        sub.refresh_from_db()
        self.assertFalse(sub.is_active)
        self.assertEqual(new_sub.plan_id, self.annual_plan.id)
        self.assertGreaterEqual(new_sub.billing_cycle_start, before)
        self.assertLessEqual(new_sub.billing_cycle_start, after)
        self.assertGreater(new_sub.billing_cycle_end, self.cycle_end)


class DowngradeAfterUpgradeDateIntegrityTestCase(TestCase):
    """
    The cascading consequence of the drift bug: schedule_plan_change_on_stripe()
    uses billing_cycle_end verbatim as the Stripe SubscriptionSchedule's
    phase-boundary timestamp. If an earlier upgrade had drifted that field,
    a later downgrade would schedule Stripe to switch prices on a bogus,
    non-boundary date. With the fix, billing_cycle_end survives a
    same-interval upgrade untouched, so a subsequent downgrade schedule
    still targets the ORIGINAL, real cycle boundary.
    """

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_downgrade_after_upgrade_targets_the_original_cycle_boundary(
        self, mock_subscription, mock_invoice
    ):
        user = CustomUser.objects.create_user(
            email="downgrade-after-upgrade@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        standard_plan = make_plan(
            "STANDARD", PlanTier.STANDARD, 999, 10_000_000, "price_standard"
        )
        pro_plan = make_plan("PRO", PlanTier.PRO, 2999, 30_000_000, "price_pro")
        CreditWallet.objects.get_or_create(user=user)

        cycle_start = timezone.now()
        cycle_end = cycle_start + relativedelta(months=1)
        sub = UserSubscription.objects.create(
            user=user,
            plan=standard_plan,
            is_active=True,
            billing_cycle_start=cycle_start,
            billing_cycle_end=cycle_end,
            next_credit_grant_at=cycle_end,
            stripe_subscription_id="sub_dau_1",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )

        # Step 1: immediate same-interval upgrade, mid-cycle.
        mock_subscription.retrieve.side_effect = [
            {
                "id": "sub_dau_1",
                "items": {"data": [{"id": "si_1", "price": {"id": "price_standard"}}]},
                "latest_invoice": "in_1",
            },
            {
                "id": "sub_dau_1",
                "items": {"data": [{"id": "si_1", "price": {"id": "price_pro"}}]},
                "latest_invoice": "in_1",
            },
        ]
        mock_subscription.modify.return_value = None
        mock_invoice.retrieve.return_value = {
            "id": "in_1",
            "status": "paid",
            "payment_intent": None,
        }

        updated_sub = StripeSubscriptionMutationService.change_plan(sub, pro_plan)
        self.assertEqual(updated_sub.billing_cycle_end, cycle_end)

        # Step 2: now schedule a downgrade back to STANDARD. The Stripe
        # schedule's phase boundary must be the ORIGINAL cycle_end, not a
        # drifted "upgrade time + 1 month" value.
        with patch("stripe.SubscriptionSchedule") as mock_schedule:
            mock_schedule.create.return_value = FakeStripeObject(
                id="sched_1",
                phases=[{"start_date": int(cycle_start.timestamp())}],
            )
            mock_schedule.modify.return_value = None

            schedule_id = (
                StripeSubscriptionScheduleService.schedule_plan_change_on_stripe(
                    updated_sub, standard_plan
                )
            )

            self.assertEqual(schedule_id, "sched_1")
            _, kwargs = mock_schedule.modify.call_args
            phase_1_end = kwargs["phases"][0]["end_date"]
            phase_2_start = kwargs["phases"][1]["start_date"]

        expected_boundary = int(cycle_end.timestamp())
        self.assertEqual(phase_1_end, expected_boundary)
        self.assertEqual(phase_2_start, expected_boundary)


class MultiCycleRenewalSimulationTestCase(TestCase):
    """
    Simulates a subscription's life over many months/years, with a
    same-interval upgrade injected mid-cycle, to prove:
      1. billing_cycle_end advances by exactly one real period per real
         renewal, with no drift accumulating from the upgrade.
      2. The REAL invoice.payment_succeeded renewal at the genuine Stripe
         cycle boundary is not silently swallowed by
         _handle_individual_invoice_succeeded's idempotency guard — the
         bug this whole fix exists to close.
      3. An interval-crossing switch to ANNUAL further down the line
         resets the cycle correctly and mid-cycle monthly credit grants
         behave for a full year afterward.

    `django.utils.timezone.now` is patched globally so every module that
    does `from django.utils import timezone; timezone.now()` observes the
    same simulated clock (they all share the same underlying module
    object, so a single patch target covers services.py, stripe_service.py,
    etc.).
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="simulation@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.standard_plan = make_plan(
            "STANDARD", PlanTier.STANDARD, 999, 10_000_000, "price_standard"
        )
        self.pro_plan = make_plan("PRO", PlanTier.PRO, 2999, 30_000_000, "price_pro")
        self.annual_plan = make_plan(
            "PRO_ANNUAL",
            PlanTier.PRO,
            29999,
            30_000_000,
            "price_pro_annual",
            interval=BillingInterval.ANNUAL,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)

    @patch("django.utils.timezone.now")
    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_mid_cycle_upgrade_then_twelve_real_monthly_renewals(
        self, mock_subscription, mock_invoice, mock_now
    ):
        t0 = timezone.datetime(2030, 1, 1, tzinfo=datetime.timezone.utc)
        mock_now.return_value = t0

        sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.standard_plan,
            is_active=True,
            billing_cycle_start=t0,
            billing_cycle_end=t0 + relativedelta(months=1),
            next_credit_grant_at=t0 + relativedelta(months=1),
            stripe_subscription_id="sub_sim_1",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )
        real_cycle_end = sub.billing_cycle_end  # 2030-02-01, Stripe's real anchor

        # --- Mid-cycle same-interval upgrade on day 15 ---
        t_upgrade = t0 + relativedelta(days=14)
        mock_now.return_value = t_upgrade
        mock_subscription.retrieve.side_effect = [
            {
                "id": "sub_sim_1",
                "items": {"data": [{"id": "si_1", "price": {"id": "price_standard"}}]},
                "latest_invoice": "in_1",
            },
            {
                "id": "sub_sim_1",
                "items": {"data": [{"id": "si_1", "price": {"id": "price_pro"}}]},
                "latest_invoice": "in_1",
            },
        ]
        mock_subscription.modify.return_value = None
        mock_invoice.retrieve.return_value = {
            "id": "in_1",
            "status": "paid",
            "payment_intent": None,
        }

        sub = StripeSubscriptionMutationService.change_plan(sub, self.pro_plan)

        # The whole point of the fix: the upgrade must NOT have moved the
        # cycle boundary away from Stripe's real anchor.
        self.assertEqual(sub.billing_cycle_end, real_cycle_end)

        # From here on, every renewal's sync_price() call retrieves the
        # subscription to confirm Stripe is already billing the current
        # plan's price — already true post-upgrade, so it no-ops.
        mock_subscription.retrieve.side_effect = None
        mock_subscription.retrieve.return_value = {
            "id": "sub_sim_1",
            "items": {"data": [{"id": "si_1", "price": {"id": "price_pro"}}]},
        }

        # --- The REAL Stripe invoice fires exactly on the real boundary ---
        mock_now.return_value = real_cycle_end
        invoice = {"id": "in_real_1", "amount_paid": 2999, "currency": "usd"}
        StripeWebhookHandler._handle_individual_invoice_succeeded(
            sub, "subscription_cycle", invoice
        )

        # It must NOT have been swallowed by the idempotency guard —
        # process_rollover_and_renewal must have actually run, producing a
        # new active row for the next period.
        sub = UserSubscription.objects.get(user=self.user, is_active=True)
        self.assertEqual(sub.plan_id, self.pro_plan.id)
        self.assertEqual(sub.billing_cycle_start, real_cycle_end)
        self.assertEqual(
            sub.billing_cycle_end, real_cycle_end + relativedelta(months=1)
        )

        # --- Eleven more real monthly renewals: verify NO drift accumulates ---
        expected_cycle_end = sub.billing_cycle_end
        for _ in range(11):
            mock_now.return_value = expected_cycle_end
            invoice = {
                "id": f"in_real_{expected_cycle_end.isoformat()}",
                "amount_paid": 2999,
                "currency": "usd",
            }
            StripeWebhookHandler._handle_individual_invoice_succeeded(
                sub, "subscription_cycle", invoice
            )
            sub = UserSubscription.objects.get(user=self.user, is_active=True)
            expected_cycle_end = expected_cycle_end + relativedelta(months=1)
            self.assertEqual(sub.billing_cycle_end, expected_cycle_end)

        # A full year of real monthly renewals after the mid-cycle upgrade,
        # and billing_cycle_end landed exactly on 2031-02-01 with zero drift.
        self.assertEqual(sub.billing_cycle_end, real_cycle_end + relativedelta(years=1))

    @patch("django.utils.timezone.now")
    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_interval_crossing_to_annual_then_year_of_mid_cycle_grants(
        self, mock_subscription, mock_invoice, mock_now
    ):
        t0 = timezone.datetime(2030, 6, 1, tzinfo=datetime.timezone.utc)
        mock_now.return_value = t0

        sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.standard_plan,
            is_active=True,
            billing_cycle_start=t0,
            billing_cycle_end=t0 + relativedelta(months=1),
            next_credit_grant_at=t0 + relativedelta(months=1),
            stripe_subscription_id="sub_sim_2",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )

        # Switch to ANNUAL (interval-crossing): Stripe genuinely resets
        # its anchor here, so the local cycle SHOULD reset too.
        mock_subscription.retrieve.side_effect = [
            {
                "id": "sub_sim_2",
                "items": {"data": [{"id": "si_1", "price": {"id": "price_standard"}}]},
                "latest_invoice": "in_2",
            },
            {
                "id": "sub_sim_2",
                "items": {
                    "data": [{"id": "si_1", "price": {"id": "price_pro_annual"}}]
                },
                "latest_invoice": "in_2",
            },
        ]
        mock_subscription.modify.return_value = None
        mock_invoice.retrieve.return_value = {
            "id": "in_2",
            "status": "paid",
            "payment_intent": None,
        }

        sub = StripeSubscriptionMutationService.change_plan(sub, self.annual_plan)
        annual_start = sub.billing_cycle_start
        annual_end = sub.billing_cycle_end
        self.assertEqual(annual_start, t0)
        self.assertEqual(annual_end, t0 + relativedelta(years=1))
        self.assertEqual(sub.next_credit_grant_at, t0 + relativedelta(months=1))

        # From here on, sync_price()'s retrieve calls (fired on the real
        # annual renewal below) see Stripe already billing the current
        # plan's price, so it no-ops.
        mock_subscription.retrieve.side_effect = None
        mock_subscription.retrieve.return_value = {
            "id": "sub_sim_2",
            "items": {"data": [{"id": "si_1", "price": {"id": "price_pro_annual"}}]},
        }

        # 11 mid-cycle monthly credit grants (month 12's worth of refresh
        # is the real annual renewal itself, handled below).
        expected_grant_at = sub.next_credit_grant_at
        for _ in range(11):
            mock_now.return_value = expected_grant_at
            sub = SubscriptionService.process_mid_cycle_credit_grant(sub)
            expected_grant_at = min(
                expected_grant_at + relativedelta(months=1), annual_end
            )
            self.assertEqual(sub.next_credit_grant_at, expected_grant_at)
            # The mid-cycle grant task must never touch the annual anchor.
            self.assertEqual(sub.billing_cycle_end, annual_end)

        # The real annual renewal, exactly one year after the interval
        # crossing — must not have drifted.
        mock_now.return_value = annual_end
        invoice = {"id": "in_annual_renewal", "amount_paid": 29999, "currency": "usd"}
        StripeWebhookHandler._handle_individual_invoice_succeeded(
            sub, "subscription_cycle", invoice
        )
        sub = UserSubscription.objects.get(user=self.user, is_active=True)
        self.assertEqual(sub.billing_cycle_start, annual_end)
        self.assertEqual(sub.billing_cycle_end, annual_end + relativedelta(years=1))
