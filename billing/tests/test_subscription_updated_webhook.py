"""
billing/tests/test_subscription_updated_webhook.py
====================================================
Locks in StripeWebhookHandler.handle_subscription_updated, added to close
a real gap found while building the live-Stripe QA suite:
customer.subscription.updated had no entry at all in _EVENT_HANDLERS, so a
subscription changed directly on Stripe (dashboard, or Stripe itself e.g.
pausing for chargeback risk) was invisible to the app until the next daily
reconcile sweep, up to 24 hours later.

SCOPE, DELIBERATELY NARROW
---------------------------
This handler syncs ONLY `stripe_status` (and `is_active` when the new
status is terminal). It must NOT touch plan, price or billing period --
those are owned by the upgrade/downgrade services, the renewal webhook and
the reconcile sweep, all of which know exactly which local objects to
create. customer.subscription.updated fires for changes OUR OWN code just
made too, so this handler running again on the same event must be a no-op
for anything it doesn't own.
"""

from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    BillingInterval,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.stripe_service import StripeWebhookHandler
from billing.webhooks import _EVENT_HANDLERS
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()

STRIPE_SUB_ID = "sub_updated_test"


def stripe_sub_payload(status):
    return {"id": STRIPE_SUB_ID, "status": status}


class DispatchTableTests(TestCase):
    def test_customer_subscription_updated_is_registered(self):
        self.assertIn("customer.subscription.updated", _EVENT_HANDLERS)
        self.assertIs(
            _EVENT_HANDLERS["customer.subscription.updated"],
            StripeWebhookHandler.handle_subscription_updated,
        )

    def test_handler_is_atomic(self):
        self.assertTrue(
            hasattr(StripeWebhookHandler.handle_subscription_updated, "__wrapped__"),
            "handle_subscription_updated must be decorated with "
            "@transaction.atomic, matching every other handler in "
            "StripeWebhookHandler's dispatch table.",
        )


class IndividualSubscriptionUpdatedTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="dashboard-edit@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = SubscriptionPlan.objects.create(
            name="STANDARD",
            display_name="Standard",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            interval=BillingInterval.MONTHLY,
            price_cents=999,
            monthly_credits=10_000,
            stripe_price_id="price_standard",
            carry_over_percent=0,
            carry_over_expiry_months=1,
            is_active=True,
        )
        now = timezone.now()
        self.sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            billing_cycle_start=now - relativedelta(days=5),
            billing_cycle_end=now + relativedelta(days=25),
            stripe_subscription_id=STRIPE_SUB_ID,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )
        CreditWallet.objects.get_or_create(
            user=self.user, defaults={"stripe_customer_id": "cus_dashboard"}
        )

    def _reload(self):
        return UserSubscription.objects.get(pk=self.sub.pk)

    def test_dashboard_side_pause_is_synced(self):
        """The exact gap this closes: a status change from Stripe's side
        with no request from our own code."""
        StripeWebhookHandler.handle_subscription_updated(stripe_sub_payload("past_due"))
        updated = self._reload()
        self.assertEqual(updated.stripe_status, StripeSubscriptionStatus.PAST_DUE)
        self.assertTrue(updated.is_active, "past_due must not deactivate the row")

    def test_canceled_from_dashboard_deactivates_the_row(self):
        StripeWebhookHandler.handle_subscription_updated(stripe_sub_payload("canceled"))
        updated = self._reload()
        self.assertEqual(updated.stripe_status, StripeSubscriptionStatus.CANCELED)
        self.assertFalse(updated.is_active)

    def test_unpaid_from_dashboard_deactivates_the_row(self):
        StripeWebhookHandler.handle_subscription_updated(stripe_sub_payload("unpaid"))
        updated = self._reload()
        self.assertEqual(updated.stripe_status, StripeSubscriptionStatus.UNPAID)
        self.assertFalse(updated.is_active)

    def test_unmapped_status_is_left_alone(self):
        """Stripe sends 'paused' and 'incomplete_expired' too, which have
        no local enum value. Guessing at a mapping is worse than leaving
        it untouched and logging."""
        with self.assertLogs("billing.stripe_service", level="INFO") as cm:
            StripeWebhookHandler.handle_subscription_updated(
                stripe_sub_payload("paused")
            )
        updated = self._reload()
        self.assertEqual(updated.stripe_status, StripeSubscriptionStatus.ACTIVE)
        self.assertTrue(updated.is_active)
        self.assertTrue(any("no local mapping" in msg for msg in cm.output))

    def test_same_status_replay_is_a_no_op(self):
        """The event fires for changes our OWN code made too -- syncing
        the same status Stripe already reports must not touch the row a
        second time."""
        with patch.object(UserSubscription, "save") as mock_save:
            StripeWebhookHandler.handle_subscription_updated(
                stripe_sub_payload("active")
            )
        mock_save.assert_not_called()

    def test_only_the_active_row_is_matched(self):
        """A stale/inactive row sharing the Stripe subscription id (e.g.
        after a plan-change created a new active row) must not be
        resurrected by a late-arriving event."""
        self.sub.is_active = False
        self.sub.save(update_fields=["is_active"])

        StripeWebhookHandler.handle_subscription_updated(stripe_sub_payload("past_due"))

        stale = self._reload()
        self.assertFalse(stale.is_active)
        self.assertEqual(stale.stripe_status, StripeSubscriptionStatus.ACTIVE)

    def test_does_not_touch_plan_or_billing_period(self):
        """This handler must stay narrowly scoped to status -- plan and
        period belong to the upgrade/downgrade/renewal paths."""
        before_plan_id = self.sub.plan_id
        before_end = self.sub.billing_cycle_end

        StripeWebhookHandler.handle_subscription_updated(stripe_sub_payload("past_due"))

        updated = self._reload()
        self.assertEqual(updated.plan_id, before_plan_id)
        self.assertEqual(updated.billing_cycle_end, before_end)


class LicenseSubscriptionUpdatedTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Dashboard Edit High")
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.POWER_LICENSE,
            display_name="Power License",
            category=PlanCategory.LICENSE,
            tier=PlanTier.POWER,
            interval=BillingInterval.MONTHLY,
            price_cents=19_900,
            monthly_credits=20_000,
            carry_over_percent=0,
            carry_over_expiry_months=1,
            is_active=True,
        )
        self.admin = CustomUser.objects.create_user(
            email="dashboard-admin@school.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        now = timezone.now()
        self.license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            contract_months=12,
            max_seats=5,
            billing_cycle_start=now,
            billing_cycle_end=now + relativedelta(months=12),
            is_active=True,
            auto_renew=True,
            stripe_subscription_id=STRIPE_SUB_ID,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )

    def _reload(self):
        return LicenseSubscription.objects.get(pk=self.license.pk)

    def test_dashboard_side_past_due_is_synced(self):
        StripeWebhookHandler.handle_subscription_updated(stripe_sub_payload("past_due"))
        updated = self._reload()
        self.assertEqual(updated.stripe_status, StripeSubscriptionStatus.PAST_DUE)
        self.assertTrue(updated.is_active)

    def test_canceled_from_dashboard_deactivates_the_license(self):
        with patch(
            "billing.stripe_service.sync_teachers_under_license_to_mailerlite"
        ) as mock_sync:
            StripeWebhookHandler.handle_subscription_updated(
                stripe_sub_payload("canceled")
            )
        updated = self._reload()
        self.assertEqual(updated.stripe_status, StripeSubscriptionStatus.CANCELED)
        self.assertFalse(updated.is_active)
        mock_sync.assert_called_once()

    def test_individual_lookup_does_not_fire_for_a_license_id(self):
        """A LicenseSubscription-only id must fall through to the license
        branch rather than being silently dropped."""
        StripeWebhookHandler.handle_subscription_updated(stripe_sub_payload("unpaid"))
        updated = self._reload()
        self.assertEqual(updated.stripe_status, StripeSubscriptionStatus.UNPAID)
        self.assertFalse(updated.is_active)


class NoMatchingSubscriptionTests(TestCase):
    def test_unknown_subscription_id_is_a_silent_no_op(self):
        """A late-arriving event for a subscription id we never
        recorded (or already fully cleaned up) must not raise."""
        StripeWebhookHandler.handle_subscription_updated(
            {"id": "sub_never_seen", "status": "active"}
        )
