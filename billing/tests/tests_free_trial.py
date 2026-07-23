"""
billing/tests_free_trial.py
============================
Full test suite for the Free Trial feature.

Run with:
    python manage.py test billing.tests_free_trial

Coverage targets:
  - activate_free_trial() — happy path, all guards
  - expire_trial()        — happy path, guards, partial usage
  - convert_trial_to_paid() — happy path, guards, credit forfeit
  - TRIAL bucket ordering in consume_credits
  - API: POST /start-trial/
  - API: POST /convert-trial/
  - Serializer trial fields
"""

from datetime import timedelta

# import pytest
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import (
    CONVERSION_FACTOR,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
    UserSubscription,
)
from billing.services import SubscriptionService
from users.models import CustomUser, UserTypes

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def make_teacher(
    email="teacher@example.com", password="password123"  # pragma: allowlist secret
):  # pragma: allowlist secret
    return CustomUser.objects.create_user(
        email=email,
        password=password,  # pragma: allowlist secret
        user_type=UserTypes.TEACHER,
        is_active=True,
    )


def make_individual_plan(
    name=PlanType.STANDARD,
    display_name="Standard Grader",
    monthly_credits=10_000_000,  # 10K display
    carry_over_percent=15,
    carry_over_max=1_500_000,
    carry_over_expiry_months=1,
):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=display_name,
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.STANDARD,
        monthly_credits=monthly_credits,
        carry_over_percent=carry_over_percent,
        carry_over_max=carry_over_max,
        carry_over_expiry_months=carry_over_expiry_months,
        overage_block_size=5_000_000,
        overage_block_price=500,
        max_overage_blocks=5,
        is_active=True,
    )


def make_license_plan():
    return SubscriptionPlan.objects.create(
        name=PlanType.PRO,
        display_name="Pro License",
        category=PlanCategory.LICENSE,
        tier=PlanTier.PRO,
        monthly_credits=20_000_000,
        carry_over_percent=25,
        carry_over_max=5_000_000,
        carry_over_expiry_months=1,
        is_active=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Service layer — activate_free_trial()
# ─────────────────────────────────────────────────────────────────────────────


class TestActivateFreeTrial(TestCase):

    def setUp(self):
        self.user = make_teacher()
        self.plan = make_individual_plan()

    # ── Happy path ────────────────────────────────────────────────────────────

    def test_creates_trial_subscription(self):
        sub = SubscriptionService.activate_free_trial(self.user, self.plan)

        self.assertTrue(sub.is_trial)
        self.assertTrue(sub.is_active)
        self.assertIsNotNone(sub.trial_end)
        self.assertEqual(sub.user, self.user)
        self.assertEqual(sub.plan, self.plan)
        self.assertFalse(sub.auto_renew)

    def test_trial_duration_is_14_days(self):
        before = timezone.now()
        sub = SubscriptionService.activate_free_trial(self.user, self.plan)
        after = timezone.now()

        expected_min = before + timedelta(days=14)
        expected_max = after + timedelta(days=14)

        self.assertGreaterEqual(sub.trial_end, expected_min)
        self.assertLessEqual(sub.trial_end, expected_max)

    def test_billing_cycle_end_equals_trial_end(self):
        sub = SubscriptionService.activate_free_trial(self.user, self.plan)
        self.assertEqual(sub.billing_cycle_end, sub.trial_end)

    def test_creates_trial_credit_bucket(self):
        SubscriptionService.activate_free_trial(self.user, self.plan)
        wallet = CreditWallet.objects.get(user=self.user)
        bucket = wallet.buckets.get(bucket_type=CreditBucketType.TRIAL)

        self.assertEqual(
            bucket.total_credits,
            SubscriptionService.TRIAL_CREDITS_RAW,
        )
        self.assertEqual(bucket.used_credits, 0)
        self.assertEqual(
            bucket.expires_at,
            CreditWallet.objects.get(user=self.user)
            .buckets.get(bucket_type=CreditBucketType.TRIAL)
            .expires_at,
        )

    def test_trial_credits_are_5k_display(self):
        SubscriptionService.activate_free_trial(self.user, self.plan)
        wallet = CreditWallet.objects.get(user=self.user)
        bucket = wallet.buckets.get(bucket_type=CreditBucketType.TRIAL)
        self.assertEqual(bucket.total_credits // CONVERSION_FACTOR, 5_000)

    def test_trial_bucket_expires_at_trial_end(self):
        sub = SubscriptionService.activate_free_trial(self.user, self.plan)
        wallet = CreditWallet.objects.get(user=self.user)
        bucket = wallet.buckets.get(bucket_type=CreditBucketType.TRIAL)
        self.assertEqual(bucket.expires_at, sub.trial_end)

    def test_creates_grant_ledger_entry(self):
        SubscriptionService.activate_free_trial(self.user, self.plan)
        wallet = CreditWallet.objects.get(user=self.user)
        bucket = wallet.buckets.get(bucket_type=CreditBucketType.TRIAL)
        ledger = CreditLedger.objects.get(
            user=self.user,
            bucket=bucket,
            ledger_type=CreditLedgerType.GRANT,
        )
        self.assertEqual(ledger.amount, SubscriptionService.TRIAL_CREDITS_RAW)
        self.assertIn("FREE_TRIAL", ledger.metadata.get("grant_type", ""))

    def test_creates_wallet_if_missing(self):
        self.assertFalse(CreditWallet.objects.filter(user=self.user).exists())
        SubscriptionService.activate_free_trial(self.user, self.plan)
        self.assertTrue(CreditWallet.objects.filter(user=self.user).exists())

    def test_overage_blocks_reset_to_zero(self):
        wallet = CreditWallet.objects.create(user=self.user, overage_blocks_used=3)
        SubscriptionService.activate_free_trial(self.user, self.plan)
        wallet.refresh_from_db()
        self.assertEqual(wallet.overage_blocks_used, 0)

    # ── Guards ────────────────────────────────────────────────────────────────

    def test_rejects_license_plan(self):
        license_plan = make_license_plan()
        with self.assertRaises(ValueError) as ctx:
            SubscriptionService.activate_free_trial(self.user, license_plan)
        self.assertIn("INDIVIDUAL", str(ctx.exception))

    def test_rejects_second_trial(self):
        SubscriptionService.activate_free_trial(self.user, self.plan)
        # Manually expire to simulate it ended
        sub = UserSubscription.objects.get(user=self.user, is_trial=True)
        sub.is_active = False
        sub.save()

        with self.assertRaises(ValueError) as ctx:
            SubscriptionService.activate_free_trial(self.user, self.plan)
        self.assertIn("already used its free trial", str(ctx.exception))

    def test_rejects_user_with_active_subscription(self):
        # Give user a paid subscription first
        SubscriptionService.activate_subscription(self.user, self.plan)

        with self.assertRaises(ValueError) as ctx:
            SubscriptionService.activate_free_trial(self.user, self.plan)
        self.assertIn("active subscription", str(ctx.exception))

    def test_one_active_subscription_constraint_enforced(self):
        """DB-level unique constraint: only one active subscription per user."""
        SubscriptionService.activate_free_trial(self.user, self.plan)
        # Should only be one active subscription
        active = UserSubscription.objects.filter(user=self.user, is_active=True)
        self.assertEqual(active.count(), 1)


# ─────────────────────────────────────────────────────────────────────────────
# Service layer — expire_trial()
# ─────────────────────────────────────────────────────────────────────────────


class TestExpireTrial(TestCase):

    def setUp(self):
        self.user = make_teacher()
        self.plan = make_individual_plan()
        self.trial_sub = SubscriptionService.activate_free_trial(self.user, self.plan)

    def _force_trial_expired(self):
        """Backdate trial_end to the past so expire_trial() accepts it."""
        past = timezone.now() - timedelta(days=1)
        self.trial_sub.trial_end = past
        self.trial_sub.billing_cycle_end = past
        self.trial_sub.save(update_fields=["trial_end", "billing_cycle_end"])

    # ── Happy path — no credits used ─────────────────────────────────────────

    def test_deactivates_subscription_on_expiry(self):
        self._force_trial_expired()
        SubscriptionService.expire_trial(self.trial_sub)
        self.trial_sub.refresh_from_db()
        self.assertFalse(self.trial_sub.is_active)
        self.assertFalse(self.trial_sub.is_trial)

    def test_creates_expire_ledger_for_unused_credits(self):
        self._force_trial_expired()
        SubscriptionService.expire_trial(self.trial_sub)

        wallet = CreditWallet.objects.get(user=self.user)
        bucket = wallet.buckets.get(bucket_type=CreditBucketType.TRIAL)
        ledger = CreditLedger.objects.filter(
            user=self.user,
            bucket=bucket,
            ledger_type=CreditLedgerType.EXPIRE,
        ).first()

        self.assertIsNotNone(ledger)
        self.assertEqual(ledger.amount, SubscriptionService.TRIAL_CREDITS_RAW)

    def test_trial_bucket_marked_processed(self):
        self._force_trial_expired()
        SubscriptionService.expire_trial(self.trial_sub)

        wallet = CreditWallet.objects.get(user=self.user)
        bucket = wallet.buckets.get(bucket_type=CreditBucketType.TRIAL)
        self.assertTrue(bucket.is_processed)

    # ── Happy path — partial credits used ────────────────────────────────────

    def test_expire_ledger_only_logs_unused_portion(self):
        wallet = CreditWallet.objects.get(user=self.user)
        bucket = wallet.buckets.get(bucket_type=CreditBucketType.TRIAL)

        # Simulate 1K display credits consumed
        consumed_raw = 1_000 * CONVERSION_FACTOR
        bucket.used_credits = consumed_raw
        bucket.save()

        self._force_trial_expired()
        SubscriptionService.expire_trial(self.trial_sub)

        ledger = CreditLedger.objects.filter(
            user=self.user,
            bucket=bucket,
            ledger_type=CreditLedgerType.EXPIRE,
        ).first()

        expected_unused = SubscriptionService.TRIAL_CREDITS_RAW - consumed_raw
        self.assertEqual(ledger.amount, expected_unused)

    def test_no_expire_ledger_if_all_credits_consumed(self):
        wallet = CreditWallet.objects.get(user=self.user)
        bucket = wallet.buckets.get(bucket_type=CreditBucketType.TRIAL)

        # Exhaust all trial credits
        bucket.used_credits = bucket.total_credits
        bucket.save()

        self._force_trial_expired()
        SubscriptionService.expire_trial(self.trial_sub)

        expire_entries = CreditLedger.objects.filter(
            user=self.user,
            bucket=bucket,
            ledger_type=CreditLedgerType.EXPIRE,
        )
        self.assertEqual(expire_entries.count(), 0)

    # ── Guards ────────────────────────────────────────────────────────────────

    def test_rejects_non_trial_subscription(self):
        paid_sub = SubscriptionService.activate_subscription(
            make_teacher("other@example.com"), self.plan
        )
        with self.assertRaises(ValueError) as ctx:
            SubscriptionService.expire_trial(paid_sub)
        self.assertIn("not a trial subscription", str(ctx.exception))

    def test_rejects_trial_that_has_not_ended(self):
        # trial_end is 14 days in the future — should raise
        with self.assertRaises(ValueError) as ctx:
            SubscriptionService.expire_trial(self.trial_sub)
        self.assertIn("has not ended yet", str(ctx.exception))

    def test_user_has_no_active_subscription_after_expiry(self):
        self._force_trial_expired()
        SubscriptionService.expire_trial(self.trial_sub)
        active = UserSubscription.objects.filter(user=self.user, is_active=True)
        self.assertEqual(active.count(), 0)


# ─────────────────────────────────────────────────────────────────────────────
# Service layer — convert_trial_to_paid()
# ─────────────────────────────────────────────────────────────────────────────


class TestConvertTrialToPaid(TestCase):

    def setUp(self):
        self.user = make_teacher()
        self.plan = make_individual_plan()
        self.trial_sub = SubscriptionService.activate_free_trial(self.user, self.plan)

    # ── Happy path ────────────────────────────────────────────────────────────

    def test_returns_new_paid_subscription(self):
        new_sub = SubscriptionService.convert_trial_to_paid(self.user, self.plan)

        self.assertFalse(new_sub.is_trial)
        self.assertTrue(new_sub.is_active)
        self.assertIsNone(new_sub.trial_end)
        self.assertTrue(new_sub.auto_renew)
        self.assertEqual(new_sub.plan, self.plan)

    def test_trial_subscription_is_deactivated(self):
        SubscriptionService.convert_trial_to_paid(self.user, self.plan)
        self.trial_sub.refresh_from_db()
        self.assertFalse(self.trial_sub.is_active)

    def test_trial_credits_are_forfeited_on_conversion(self):
        SubscriptionService.convert_trial_to_paid(self.user, self.plan)

        wallet = CreditWallet.objects.get(user=self.user)

        # TRIAL bucket must be expired (expires_at <= now)
        trial_bucket = wallet.buckets.filter(bucket_type=CreditBucketType.TRIAL).first()
        self.assertIsNotNone(trial_bucket)
        self.assertLessEqual(trial_bucket.expires_at, timezone.now())
        self.assertTrue(trial_bucket.is_processed)

    def test_expire_ledger_entry_created_for_forfeited_credits(self):
        SubscriptionService.convert_trial_to_paid(self.user, self.plan)

        wallet = CreditWallet.objects.get(user=self.user)
        trial_bucket = wallet.buckets.get(bucket_type=CreditBucketType.TRIAL)

        expire_entry = CreditLedger.objects.filter(
            user=self.user,
            bucket=trial_bucket,
            ledger_type=CreditLedgerType.EXPIRE,
        ).first()
        self.assertIsNotNone(expire_entry)

    def test_new_monthly_bucket_is_created(self):
        SubscriptionService.convert_trial_to_paid(self.user, self.plan)

        wallet = CreditWallet.objects.get(user=self.user)
        monthly = wallet.buckets.filter(
            bucket_type=CreditBucketType.MONTHLY,
            expires_at__gt=timezone.now(),
        ).first()

        self.assertIsNotNone(monthly)
        self.assertEqual(monthly.total_credits, self.plan.monthly_credits)

    def test_can_convert_to_different_plan(self):
        """User trials Standard but converts to Pro — common real-world path."""
        pro_plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Pro Grader",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.PRO,
            monthly_credits=20_000_000,
            carry_over_percent=25,
            carry_over_max=5_000_000,
            carry_over_expiry_months=1,
            overage_block_size=5_000_000,
            overage_block_price=400,
            max_overage_blocks=5,
            is_active=True,
        )
        new_sub = SubscriptionService.convert_trial_to_paid(self.user, pro_plan)
        self.assertEqual(new_sub.plan, pro_plan)

    def test_no_expire_ledger_when_trial_already_exhausted(self):
        """If user burned all trial credits before converting, no EXPIRE entry."""
        wallet = CreditWallet.objects.get(user=self.user)
        bucket = wallet.buckets.get(bucket_type=CreditBucketType.TRIAL)
        bucket.used_credits = bucket.total_credits
        bucket.save()

        SubscriptionService.convert_trial_to_paid(self.user, self.plan)

        expire_entries = CreditLedger.objects.filter(
            user=self.user,
            bucket=bucket,
            ledger_type=CreditLedgerType.EXPIRE,
        )
        self.assertEqual(expire_entries.count(), 0)

    def test_only_one_active_subscription_after_conversion(self):
        SubscriptionService.convert_trial_to_paid(self.user, self.plan)
        active = UserSubscription.objects.filter(user=self.user, is_active=True)
        self.assertEqual(active.count(), 1)

    def test_converted_user_can_renew_on_next_cycle(self):
        """Smoke test: the paid subscription is in a state that process_rollover_and_renewal accepts."""
        new_sub = SubscriptionService.convert_trial_to_paid(self.user, self.plan)
        self.assertTrue(new_sub.auto_renew)
        self.assertFalse(new_sub.is_trial)
        self.assertIsNone(new_sub.trial_end)

    # ── Guards ────────────────────────────────────────────────────────────────

    def test_rejects_license_plan(self):
        license_plan = make_license_plan()
        with self.assertRaises(ValueError) as ctx:
            SubscriptionService.convert_trial_to_paid(self.user, license_plan)
        self.assertIn("INDIVIDUAL", str(ctx.exception))

    def test_rejects_user_with_no_active_trial(self):
        user2 = make_teacher("notrial@example.com")
        with self.assertRaises(ValueError) as ctx:
            SubscriptionService.convert_trial_to_paid(user2, self.plan)
        self.assertIn("does not have an active free trial", str(ctx.exception))

    def test_rejects_user_on_paid_subscription_not_trial(self):
        """User on a paid plan should not be able to call convert_trial."""
        user2 = make_teacher("paid@example.com")
        SubscriptionService.activate_subscription(user2, self.plan)
        with self.assertRaises(ValueError) as ctx:
            SubscriptionService.convert_trial_to_paid(user2, self.plan)
        self.assertIn("does not have an active free trial", str(ctx.exception))


# ─────────────────────────────────────────────────────────────────────────────
# Credit consumption ordering — TRIAL drains before MONTHLY
# ─────────────────────────────────────────────────────────────────────────────


class TestTrialCreditConsumptionOrdering(TestCase):

    def setUp(self):
        self.user = make_teacher()
        self.plan = make_individual_plan()
        self.trial_sub = SubscriptionService.activate_free_trial(self.user, self.plan)

    def test_trial_credits_consumed_before_monthly(self):
        """
        If a user somehow also has MONTHLY credits (e.g. after a conversion edge
        case), the TRIAL bucket must drain first because it expires sooner.
        """
        wallet = CreditWallet.objects.get(user=self.user)

        # Manually inject a MONTHLY bucket (simulates an edge case)
        monthly_bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=10_000 * CONVERSION_FACTOR,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )

        consume_amount = 100 * CONVERSION_FACTOR
        wallet.consume_credits(
            consume_amount, feature="Test", task_type="test", task_id="t1"
        )

        trial_bucket = wallet.buckets.get(bucket_type=CreditBucketType.TRIAL)
        monthly_bucket.refresh_from_db()

        self.assertEqual(trial_bucket.used_credits, consume_amount)
        self.assertEqual(monthly_bucket.used_credits, 0)

    def test_spills_into_monthly_when_trial_exhausted(self):
        wallet = CreditWallet.objects.get(user=self.user)
        trial_bucket = wallet.buckets.get(bucket_type=CreditBucketType.TRIAL)

        monthly_bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=10_000 * CONVERSION_FACTOR,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )

        # Consume all trial credits + 100 more → should spill into monthly
        total = SubscriptionService.TRIAL_CREDITS_RAW + (100 * CONVERSION_FACTOR)
        wallet.consume_credits(total, feature="Test", task_type="test", task_id="t2")

        trial_bucket.refresh_from_db()
        monthly_bucket.refresh_from_db()

        self.assertEqual(trial_bucket.used_credits, trial_bucket.total_credits)
        self.assertEqual(monthly_bucket.used_credits, 100 * CONVERSION_FACTOR)

    def test_carry_over_consumed_before_trial(self):
        """CARRY_OVER expires before TRIAL (it was created before trial), so it drains first."""
        wallet = CreditWallet.objects.get(user=self.user)

        # CARRY_OVER bucket with an expiry earlier than the trial bucket
        carry_bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.CARRY_OVER,
            total_credits=500 * CONVERSION_FACTOR,
            used_credits=0,
            expires_at=timezone.now()
            + timedelta(days=7),  # expires before trial (14 days)
        )

        consume_amount = 100 * CONVERSION_FACTOR
        wallet.consume_credits(
            consume_amount, feature="Test", task_type="test", task_id="t3"
        )

        carry_bucket.refresh_from_db()
        trial_bucket = wallet.buckets.get(bucket_type=CreditBucketType.TRIAL)

        self.assertEqual(carry_bucket.used_credits, consume_amount)
        self.assertEqual(trial_bucket.used_credits, 0)


# ─────────────────────────────────────────────────────────────────────────────
# API — POST /start-trial/
# ─────────────────────────────────────────────────────────────────────────────


class TestStartTrialAPI(APITestCase):

    def setUp(self):
        self.user = make_teacher()
        self.plan = make_individual_plan()
        self.url = reverse("subscription-management-start-trial")

    def test_201_on_success(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.url, {"plan": str(self.plan.id)})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_response_contains_trial_fields(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.url, {"plan": str(self.plan.id)})
        data = response.data
        self.assertIn("is_trial", data)
        self.assertIn("trial_end", data)
        self.assertIn("trial_days_remaining", data)
        self.assertIn("trial_credits_total", data)
        self.assertIn("trial_credits_remaining", data)
        self.assertTrue(data["is_trial"])

    def test_trial_credits_total_is_5k(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.url, {"plan": str(self.plan.id)})
        self.assertEqual(response.data["trial_credits_total"], 5_000)

    def test_trial_credits_remaining_equals_total_at_start(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.url, {"plan": str(self.plan.id)})
        self.assertEqual(
            response.data["trial_credits_remaining"],
            response.data["trial_credits_total"],
        )

    def test_401_unauthenticated(self):
        response = self.client.post(self.url, {"plan": str(self.plan.id)})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_400_missing_plan(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_404_plan_not_found(self):
        self.client.force_authenticate(user=self.user)
        import uuid

        response = self.client.post(self.url, {"plan": str(uuid.uuid4())})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_400_license_plan(self):
        self.client.force_authenticate(user=self.user)
        license_plan = make_license_plan()
        response = self.client.post(self.url, {"plan": str(license_plan.id)})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_409_already_trialled(self):
        self.client.force_authenticate(user=self.user)
        # First trial
        self.client.post(self.url, {"plan": str(self.plan.id)})
        # Expire the trial
        sub = UserSubscription.objects.get(user=self.user, is_trial=True)
        sub.is_active = False
        sub.save()
        # Second attempt
        response = self.client.post(self.url, {"plan": str(self.plan.id)})
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_400_user_has_active_subscription(self):
        self.client.force_authenticate(user=self.user)
        # Give user a paid sub first
        SubscriptionService.activate_subscription(self.user, self.plan)
        response = self.client.post(self.url, {"plan": str(self.plan.id)})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("active subscription", response.data["detail"])

    def test_students_cannot_start_trial(self):
        student = CustomUser.objects.create_user(
            email="student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            is_active=True,
        )
        self.client.force_authenticate(user=student)
        response = self.client.post(self.url, {"plan": str(self.plan.id)})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


# ─────────────────────────────────────────────────────────────────────────────
# API — POST /convert-trial/
# ─────────────────────────────────────────────────────────────────────────────


class TestConvertTrialAPI(APITestCase):

    def setUp(self):
        self.user = make_teacher()
        self.plan = make_individual_plan()
        # Start a trial first
        SubscriptionService.activate_free_trial(self.user, self.plan)
        self.url = reverse("subscription-management-convert-trial")

    def test_200_on_success(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.url, {"plan": str(self.plan.id)})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_response_is_paid_subscription(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.url, {"plan": str(self.plan.id)})
        self.assertFalse(response.data["is_trial"])
        self.assertTrue(response.data["is_active"])
        self.assertTrue(response.data["auto_renew"])

    def test_trial_days_remaining_is_none_after_conversion(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.url, {"plan": str(self.plan.id)})
        self.assertIsNone(response.data.get("trial_days_remaining"))

    def test_401_unauthenticated(self):
        response = self.client.post(self.url, {"plan": str(self.plan.id)})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_400_missing_plan(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_404_plan_not_found(self):
        self.client.force_authenticate(user=self.user)
        import uuid

        response = self.client.post(self.url, {"plan": str(uuid.uuid4())})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_400_no_active_trial(self):
        user2 = make_teacher("notrial@example.com")
        self.client.force_authenticate(user=user2)
        response = self.client.post(self.url, {"plan": str(self.plan.id)})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("does not have an active free trial", response.data["detail"])

    def test_400_license_plan(self):
        self.client.force_authenticate(user=self.user)
        license_plan = make_license_plan()
        response = self.client.post(self.url, {"plan": str(license_plan.id)})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


# ─────────────────────────────────────────────────────────────────────────────
# Serializer — trial fields on UserSubscriptionSerializer
# ─────────────────────────────────────────────────────────────────────────────


class TestUserSubscriptionSerializerTrialFields(TestCase):

    def setUp(self):
        self.user = make_teacher()
        self.plan = make_individual_plan()

    def test_is_trial_false_for_paid_subscription(self):
        from billing.serializers import UserSubscriptionSerializer

        sub = SubscriptionService.activate_subscription(self.user, self.plan)
        data = UserSubscriptionSerializer(sub).data
        self.assertFalse(data["is_trial"])
        self.assertIsNone(data["trial_end"])
        self.assertIsNone(data["trial_days_remaining"])
        self.assertIsNone(data["trial_credits_remaining"])

    def test_is_trial_true_for_trial_subscription(self):
        from billing.serializers import UserSubscriptionSerializer

        sub = SubscriptionService.activate_free_trial(self.user, self.plan)
        data = UserSubscriptionSerializer(sub).data
        self.assertTrue(data["is_trial"])
        self.assertIsNotNone(data["trial_end"])
        self.assertIsNotNone(data["trial_days_remaining"])
        self.assertEqual(data["trial_days_remaining"], 14)
        self.assertEqual(data["trial_credits_remaining"], 5_000)

    def test_trial_credits_remaining_reflects_consumption(self):
        from billing.serializers import UserSubscriptionSerializer

        sub = SubscriptionService.activate_free_trial(self.user, self.plan)
        wallet = CreditWallet.objects.get(user=self.user)

        # Consume 1K display credits
        wallet.consume_credits(
            1_000 * CONVERSION_FACTOR,
            feature="Test",
            task_type="test",
            task_id="t99",
        )

        data = UserSubscriptionSerializer(sub).data
        self.assertEqual(data["trial_credits_remaining"], 4_000)
