"""
billing/tests/test_license_cancellation.py
=============================================
Covers LicenseSubscriptionService.cancel_license_subscription() and the
POST .../cancel/ endpoint that replaces the framework-default DELETE route
on LicenseSubscriptionViewSet.

BACKGROUND -- this closes two real bugs found while documenting the
license subscription flow:

1. cancel_license_subscription() used to set is_active=False immediately
   for EVERY license regardless of billing_method, and never touched
   Stripe at all. For a STRIPE-billed license that meant the local row
   went dark while the real Stripe subscription kept renewing and
   charging the school -- exactly backwards from its own docstring's
   claim ("teachers keep credits until billing cycle end"), since
   access_control.py gates a teacher's active billing context on
   `license_subscription__is_active=True`.

2. LicenseSubscriptionViewSet was a bare ModelViewSet with no
   destroy()/perform_destroy() override, so DRF's default hard-DELETE
   was live: it CASCADE-deletes LicenseBillingRecord,
   LicenseOveragePurchaseIntent, LicenseOverageOfflineRequest, and
   SchoolCreditAllocation rows (billing/models.py), and never cancels a
   live Stripe subscription first.

Fix: cancel_license_subscription() now forks on billing_method (mirroring
every other mutation in license_service.py) and the DELETE route is gone
entirely -- see LicenseSubscriptionViewSet's docstring.
"""

from datetime import timedelta
from unittest.mock import patch

import stripe as real_stripe
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    LicenseBillingMethod,
    LicenseBillingRecord,
    LicenseBillingRecordType,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes


def _make_plan():
    return SubscriptionPlan.objects.create(
        name=PlanType.PRO,
        display_name="Test License Plan",
        category=PlanCategory.LICENSE,
        tier=PlanTier.PRO,
        monthly_credits=20_000,
        overage_block_size=5_000,
        overage_block_price=299,
    )


def _make_license(school, admin, plan, billing_method, **extra):
    fields = {
        "is_active": True,
        "auto_renew": True,
        **extra,
    }
    return LicenseSubscription.objects.create(
        school=school,
        admin_user=admin,
        plan=plan,
        billing_cycle_start=timezone.now(),
        billing_cycle_end=timezone.now() + timedelta(days=30),
        billing_method=billing_method,
        **fields,
    )


class LicenseCancellationServiceTests(TransactionTestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.admin = CustomUser.objects.create_user(
            email="admin@school.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.superadmin = CustomUser.objects.create_superuser(
            email="root@gradea.com",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
        )
        self.plan = _make_plan()

    # -- STRIPE billing_method ----------------------------------------------

    @patch("billing.license_service.stripe.Subscription.modify")
    def test_stripe_license_defers_deactivation_to_period_end(self, mock_modify):
        license_sub = _make_license(
            self.school,
            self.admin,
            self.plan,
            LicenseBillingMethod.STRIPE,
            stripe_subscription_id="sub_test123",
        )

        updated = LicenseSubscriptionService.cancel_license_subscription(
            license_sub, performed_by=self.superadmin
        )

        self.assertTrue(
            updated.is_active,
            "a STRIPE license must stay active until the real "
            "customer.subscription.deleted webhook lands -- teachers paid "
            "for the current period and shouldn't lose access early",
        )
        self.assertFalse(updated.auto_renew)

    @patch("billing.license_service.stripe.Subscription.modify")
    def test_stripe_license_tells_stripe_to_stop_renewing(self, mock_modify):
        license_sub = _make_license(
            self.school,
            self.admin,
            self.plan,
            LicenseBillingMethod.STRIPE,
            stripe_subscription_id="sub_test123",
        )

        LicenseSubscriptionService.cancel_license_subscription(license_sub)

        mock_modify.assert_called_once_with("sub_test123", cancel_at_period_end=True)

    @patch("billing.license_service.stripe.Subscription.modify")
    def test_stripe_failure_is_surfaced_and_leaves_local_state_untouched(
        self, mock_modify
    ):
        mock_modify.side_effect = real_stripe.error.StripeError("network blip")
        license_sub = _make_license(
            self.school,
            self.admin,
            self.plan,
            LicenseBillingMethod.STRIPE,
            stripe_subscription_id="sub_test123",
        )

        with self.assertRaises(ValueError):
            LicenseSubscriptionService.cancel_license_subscription(license_sub)

        license_sub.refresh_from_db()
        self.assertTrue(license_sub.is_active)
        self.assertTrue(
            license_sub.auto_renew,
            "fail-closed: if Stripe rejects the cancellation, auto_renew "
            "must not flip locally either, or the license would silently "
            "stop renewing while Stripe still thinks it will",
        )

    def test_stripe_license_with_no_subscription_id_cancels_locally_only(self):
        """Defensive path -- shouldn't happen for a real STRIPE license,
        but must not crash if it does."""
        license_sub = _make_license(
            self.school,
            self.admin,
            self.plan,
            LicenseBillingMethod.STRIPE,
            stripe_subscription_id="",
        )

        updated = LicenseSubscriptionService.cancel_license_subscription(license_sub)

        self.assertTrue(updated.is_active)
        self.assertFalse(updated.auto_renew)

    # -- OFFLINE billing_method ----------------------------------------------

    def test_offline_license_deactivates_immediately(self):
        """No Stripe billing cycle to defer to, and no automated sweep
        ever revisits an OFFLINE license after billing_cycle_end
        (process_license_renewals excludes billing_method=OFFLINE) -- so
        nothing else would ever turn it off."""
        license_sub = _make_license(
            self.school, self.admin, self.plan, LicenseBillingMethod.OFFLINE
        )

        updated = LicenseSubscriptionService.cancel_license_subscription(license_sub)

        self.assertFalse(updated.is_active)
        self.assertFalse(updated.auto_renew)

    # -- shared behavior ------------------------------------------------------

    @patch("billing.license_service.stripe.Subscription.modify")
    def test_cancellation_is_recorded_on_the_billing_ledger(self, mock_modify):
        license_sub = _make_license(
            self.school,
            self.admin,
            self.plan,
            LicenseBillingMethod.STRIPE,
            stripe_subscription_id="sub_test123",
        )

        LicenseSubscriptionService.cancel_license_subscription(
            license_sub, performed_by=self.superadmin, notes="school closing"
        )

        record = LicenseBillingRecord.objects.get(license_subscription=license_sub)
        self.assertEqual(record.record_type, LicenseBillingRecordType.CANCELLED)
        self.assertEqual(record.performed_by, self.superadmin)
        self.assertEqual(record.notes, "school closing")

    def test_already_inactive_license_is_rejected(self):
        license_sub = _make_license(
            self.school,
            self.admin,
            self.plan,
            LicenseBillingMethod.OFFLINE,
            is_active=False,
        )

        with self.assertRaises(ValueError):
            LicenseSubscriptionService.cancel_license_subscription(license_sub)

    def test_already_scheduled_to_cancel_is_rejected(self):
        """Idempotency guard -- calling cancel twice must not re-fire the
        Stripe call or write a second CANCELLED record."""
        license_sub = _make_license(
            self.school,
            self.admin,
            self.plan,
            LicenseBillingMethod.OFFLINE,
            auto_renew=False,
        )

        with self.assertRaises(ValueError):
            LicenseSubscriptionService.cancel_license_subscription(license_sub)


class LicenseCancellationApiTests(APITestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.admin = CustomUser.objects.create_user(
            email="admin@school.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.superadmin = CustomUser.objects.create_superuser(
            email="root@gradea.com",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
        )
        self.plan = _make_plan()
        self.license_sub = _make_license(
            self.school,
            self.admin,
            self.plan,
            LicenseBillingMethod.STRIPE,
            stripe_subscription_id="sub_test123",
        )
        self.detail_url = reverse(
            "license-subscription-detail", kwargs={"pk": self.license_sub.pk}
        )
        self.cancel_url = reverse(
            "license-subscription-cancel", kwargs={"pk": self.license_sub.pk}
        )

    def test_delete_is_no_longer_a_valid_method(self):
        """The whole point of this change: DELETE must not silently
        hard-wipe billing history / leave Stripe uncancelled anymore."""
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.delete(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.license_sub.refresh_from_db()  # row must still exist
        self.assertTrue(self.license_sub.is_active)

    def test_school_admin_cannot_cancel(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self.cancel_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_patching_is_active_directly_is_a_silent_no_op(self):
        """The second door into the same bug: before this fix a plain
        PATCH {"is_active": false} deactivated a STRIPE license exactly
        like the old DELETE did -- locally only, no Stripe call, school
        keeps being billed. is_active is now read-only on this
        serializer, so the request must succeed (it's a valid PATCH of
        zero effective fields) but leave is_active untouched."""
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.patch(
            self.detail_url, {"is_active": False}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            response.data["is_active"],
            "PATCHing is_active must not actually change it",
        )
        self.license_sub.refresh_from_db()
        self.assertTrue(self.license_sub.is_active)

    def test_patching_auto_renew_directly_still_works(self):
        """auto_renew stays PATCHable -- unlike is_active it has no
        Stripe-bypass hazard: the nightly process_license_renewals sweep
        already calls Stripe's cancel_at_period_end itself once a
        non-auto-renewing STRIPE license's billing_cycle_end arrives, so
        this is a safe, already-covered path to express "won't renew"
        intent without going through the cancel action."""
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.patch(
            self.detail_url, {"auto_renew": False}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["auto_renew"])
        self.license_sub.refresh_from_db()
        self.assertFalse(self.license_sub.auto_renew)

    @patch("billing.license_service.stripe.Subscription.modify")
    def test_super_admin_can_cancel_a_stripe_license(self, mock_modify):
        self.client.force_authenticate(user=self.superadmin)
        response = self.client.post(
            self.cancel_url, {"notes": "non-renewal requested"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["is_active"])
        self.assertFalse(response.data["auto_renew"])
        mock_modify.assert_called_once_with("sub_test123", cancel_at_period_end=True)

    def test_super_admin_can_cancel_an_offline_license(self):
        offline_license = _make_license(
            self.school, self.admin, self.plan, LicenseBillingMethod.OFFLINE
        )
        cancel_url = reverse(
            "license-subscription-cancel", kwargs={"pk": offline_license.pk}
        )

        self.client.force_authenticate(user=self.superadmin)
        response = self.client.post(cancel_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["is_active"])
        self.assertFalse(response.data["auto_renew"])

    @patch("billing.license_service.stripe.Subscription.modify")
    def test_cancelling_twice_is_rejected_with_a_400(self, mock_modify):
        self.client.force_authenticate(user=self.superadmin)
        first = self.client.post(self.cancel_url)
        self.assertEqual(first.status_code, status.HTTP_200_OK)

        second = self.client.post(self.cancel_url)
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
