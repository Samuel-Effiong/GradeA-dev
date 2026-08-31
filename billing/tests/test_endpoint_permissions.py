"""
Lockdown coverage for the credit/subscription write endpoints.

The credit models ARE the money: a CreditBucket row is balance, a
CreditLedger row is the audit trail, and UserSubscriptionSerializer.create
delegates straight to SubscriptionService.activate_subscription — which
activates a plan AND grants its full monthly credit bucket with no payment
step. These endpoints used to accept POST/PATCH/DELETE from any
authenticated non-student (the schema docstrings *claimed* superadmin-only,
but nothing enforced it), meaning any teacher could mint unlimited credits
into any wallet, rewrite billing history, or activate any paid plan for
free. These tests pin the enforcement that closed that.

Deliberately preserved behaviors, also locked here:
  - reads of one's own wallet/buckets/ledger still work,
  - superadmin write tooling still works,
  - self-service activation of a FREE plan (the BETA onboarding flow)
    still works.

Run with:
    python manage.py test billing.tests.test_endpoint_permissions
"""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    PlanCategory,
    PlanTier,
    SubscriptionPlan,
    UserSubscription,
)
from users.models import CustomUser, UserTypes


class EndpointLockdownTestBase(APITestCase):
    def setUp(self):
        self.teacher = self._make_user(UserTypes.TEACHER)
        self.other_teacher = self._make_user(UserTypes.TEACHER)
        self.superadmin = self._make_user(UserTypes.SUPER_ADMIN, is_superuser=True)

        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        self.other_wallet, _ = CreditWallet.objects.get_or_create(
            user=self.other_teacher
        )
        self.bucket = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=10_000,
            used_credits=1_000,
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.ledger_row = CreditLedger.record(
            user=self.teacher,
            bucket=self.bucket,
            ledger_type=CreditLedgerType.CONSUME,
            amount=-1_000,
            reference="seed consumption",
        )

    def _make_user(self, user_type, is_superuser=False):
        user = CustomUser.objects.create_user(
            email=f"{user_type.lower()}-{uuid4().hex[:10]}@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=user_type,
        )
        if is_superuser:
            user.is_superuser = True
            user.save(update_fields=["is_superuser"])
        return user

    def _make_plan(self, price_cents):
        return SubscriptionPlan.objects.create(
            name=f"plan-{uuid4().hex[:8]}",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.PRO,
            interval=BillingInterval.MONTHLY,
            monthly_credits=20_000,
            carry_over_percent=25,
            price_cents=Decimal(price_cents),
            is_active=True,
        )


class CreditBucketEndpointLockdownTests(EndpointLockdownTestBase):
    def test_teacher_cannot_mint_credits_into_own_wallet(self):
        self.client.force_authenticate(user=self.teacher)
        before = CreditBucket.objects.count()

        response = self.client.post(
            reverse("credit-bucket-list"),
            {
                "wallet": str(self.wallet.id),
                "bucket_type": CreditBucketType.MANUAL_GRANT,
                "total_credits": 99_999_999,
                "used_credits": 0,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(CreditBucket.objects.count(), before)

    def test_teacher_cannot_mint_credits_into_another_users_wallet(self):
        # create() is not scoped by get_queryset, so before the lockdown a
        # teacher could target ANY wallet id — pin that the permission gate
        # now covers that path too.
        self.client.force_authenticate(user=self.teacher)

        response = self.client.post(
            reverse("credit-bucket-list"),
            {
                "wallet": str(self.other_wallet.id),
                "bucket_type": CreditBucketType.MANUAL_GRANT,
                "total_credits": 99_999_999,
                "used_credits": 0,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(self.other_wallet.buckets.exists())

    def test_teacher_cannot_inflate_own_bucket_via_patch(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.patch(
            reverse("credit-bucket-detail", kwargs={"pk": self.bucket.pk}),
            {"total_credits": 99_999_999, "used_credits": 0},
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.bucket.refresh_from_db()
        self.assertEqual(self.bucket.total_credits, 10_000)
        self.assertEqual(self.bucket.used_credits, 1_000)

    def test_teacher_cannot_delete_own_bucket(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.delete(
            reverse("credit-bucket-detail", kwargs={"pk": self.bucket.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(CreditBucket.objects.filter(pk=self.bucket.pk).exists())

    def test_teacher_can_still_read_own_buckets(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(reverse("credit-bucket-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_superadmin_can_still_create_buckets(self):
        self.client.force_authenticate(user=self.superadmin)

        response = self.client.post(
            reverse("credit-bucket-list"),
            {
                "wallet": str(self.wallet.id),
                "bucket_type": CreditBucketType.MANUAL_GRANT,
                "total_credits": 5_000,
                "used_credits": 0,
            },
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class CreditWalletEndpointLockdownTests(EndpointLockdownTestBase):
    def test_teacher_cannot_reset_overage_counter(self):
        CreditWallet.objects.filter(pk=self.wallet.pk).update(overage_blocks_used=3)
        self.client.force_authenticate(user=self.teacher)

        response = self.client.patch(
            reverse("credit-wallet-detail", kwargs={"pk": self.wallet.pk}),
            {"overage_blocks_used": 0},
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.overage_blocks_used, 3)

    def test_teacher_cannot_delete_wallet(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.delete(
            reverse("credit-wallet-detail", kwargs={"pk": self.wallet.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(CreditWallet.objects.filter(pk=self.wallet.pk).exists())

    def test_teacher_can_still_read_own_wallet(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(reverse("credit-wallet-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)


class CreditLedgerEndpointLockdownTests(EndpointLockdownTestBase):
    """
    These once asserted 403: the ledger was a full ModelViewSet whose
    write methods existed but were gated to superadmins. It is now a
    ReadOnlyModelViewSet, so the methods do not exist for ANYONE and the
    rejection is 405.

    That is a strictly stronger guarantee - a permission check protects
    against the wrong caller, an absent method protects against every
    caller - so these tests assert the stronger contract rather than
    being relaxed to accept either code. See
    docs/ops/append-only-audit-tables.md.
    """

    def test_teacher_cannot_forge_ledger_entries(self):
        self.client.force_authenticate(user=self.teacher)
        before = CreditLedger.objects.count()

        response = self.client.post(
            reverse("credit-ledger-list"),
            {
                "user_id": str(self.teacher.id),
                "bucket": str(self.bucket.id),
                "ledger_type": CreditLedgerType.REFUND,
                "amount": 99_999,
                "reference": "totally legitimate refund",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(CreditLedger.objects.count(), before)

    def test_teacher_cannot_erase_billing_history(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.delete(
            reverse("credit-ledger-detail", kwargs={"pk": self.ledger_row.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(CreditLedger.objects.filter(pk=self.ledger_row.pk).exists())

    def test_superadmin_also_cannot_write_to_the_ledger(self):
        """
        The old design let superadmins POST/PATCH/DELETE. Nobody can now:
        an audit trail with a privileged write path is forgeable by
        whoever holds the privilege.
        """
        self.client.force_authenticate(user=self.superadmin)

        post = self.client.post(reverse("credit-ledger-list"), {})
        delete = self.client.delete(
            reverse("credit-ledger-detail", kwargs={"pk": self.ledger_row.pk})
        )

        self.assertEqual(post.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(delete.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertTrue(CreditLedger.objects.filter(pk=self.ledger_row.pk).exists())

    def test_teacher_can_still_read_own_ledger(self):
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(reverse("credit-ledger-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)


class SubscriptionCreateLockdownTests(EndpointLockdownTestBase):
    """
    POST /subscription and POST /user-subscriptions both feed
    UserSubscriptionSerializer.create -> activate_subscription (plan +
    full monthly credit grant, no payment). The serializer-level guard is
    what's pinned here, so it covers both routes at once.
    """

    def test_teacher_cannot_activate_a_paid_plan_for_free(self):
        paid_plan = self._make_plan(price_cents="2499.00")
        self.client.force_authenticate(user=self.teacher)

        response = self.client.post(
            reverse("subscription-list"),
            {"user": str(self.teacher.id), "plan": str(paid_plan.id)},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            UserSubscription.objects.filter(user=self.teacher).exists(),
            "no subscription may be activated without payment",
        )
        self.assertFalse(
            self.wallet.buckets.filter(
                bucket_type=CreditBucketType.MONTHLY, total_credits=20_000
            ).exists(),
            "no plan credits may be granted without payment",
        )

    def test_teacher_cannot_activate_paid_plan_via_user_subscriptions_route(self):
        paid_plan = self._make_plan(price_cents="2499.00")
        self.client.force_authenticate(user=self.teacher)

        response = self.client.post(
            reverse("user-subscription-list"),
            {"user": str(self.teacher.id), "plan": str(paid_plan.id)},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(UserSubscription.objects.filter(user=self.teacher).exists())

    def test_teacher_cannot_activate_a_plan_for_another_user(self):
        free_plan = self._make_plan(price_cents="0.00")
        self.client.force_authenticate(user=self.teacher)

        response = self.client.post(
            reverse("subscription-list"),
            {"user": str(self.other_teacher.id), "plan": str(free_plan.id)},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            UserSubscription.objects.filter(user=self.other_teacher).exists()
        )

    def test_teacher_can_still_self_activate_a_free_plan(self):
        # The preserved legitimate flow: free-plan (e.g. BETA) onboarding.
        free_plan = self._make_plan(price_cents="0.00")
        self.client.force_authenticate(user=self.teacher)

        response = self.client.post(
            reverse("subscription-list"),
            {"user": str(self.teacher.id), "plan": str(free_plan.id)},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            UserSubscription.objects.filter(
                user=self.teacher, plan=free_plan, is_active=True
            ).exists()
        )

    def test_superadmin_can_activate_a_paid_plan_on_a_users_behalf(self):
        paid_plan = self._make_plan(price_cents="2499.00")
        self.client.force_authenticate(user=self.superadmin)

        response = self.client.post(
            reverse("subscription-list"),
            {"user": str(self.teacher.id), "plan": str(paid_plan.id)},
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            UserSubscription.objects.filter(
                user=self.teacher, plan=paid_plan, is_active=True
            ).exists()
        )
