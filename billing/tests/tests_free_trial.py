"""
billing/tests_free_trial.py
============================
Full test suite for the Free Trial feature.

Run with:
    python manage.py test billing.tests_free_trial

Coverage targets:
  - activate_free_trial() — happy path, all guards
  - expire_trial()        — happy path, guards, partial usage
  - TRIAL bucket ordering in consume_credits
  - Serializer trial fields

NOTE: this file used to also cover SubscriptionService.convert_trial_to_paid()
and the /start-trial/ and /convert-trial/ API actions. Both were removed —
convert_trial_to_paid() no longer exists (trial-to-paid conversion now goes
through Stripe Checkout + webhook finalization, see
StripeCheckoutService.create_trial_to_paid_session /
SubscriptionService.finalize_trial_to_paid_conversion in stripe_service.py),
and the start-trial/convert-trial ViewSet actions are commented out in
views.py in favor of the same Checkout-based flow. The tests for them were
dead weight testing removed code paths, so they were deleted rather than
fixed.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

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
        # Every user gets a CreditWallet automatically on registration (see
        # users/signals.py), so simulate the "missing" case explicitly
        # rather than assuming none exists yet.
        CreditWallet.objects.filter(user=self.user).delete()
        self.assertFalse(CreditWallet.objects.filter(user=self.user).exists())
        SubscriptionService.activate_free_trial(self.user, self.plan)
        self.assertTrue(CreditWallet.objects.filter(user=self.user).exists())

    def test_overage_blocks_reset_to_zero(self):
        # A wallet already exists (auto-created on registration) — update
        # it rather than creating a second one for the same user.
        wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        wallet.overage_blocks_used = 3
        wallet.save(update_fields=["overage_blocks_used"])
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
        case), the TRIAL bucket must drain first by TYPE priority (TRIAL=1,
        MONTHLY=2) — NOT because of relative expiry. Proven here by giving
        the MONTHLY bucket a SOONER expiry (1 day) than the trial (14
        days): under a "soonest-expiring-first" ordering this would drain
        MONTHLY first, which is exactly the bug class this test guards
        against.
        """
        wallet = CreditWallet.objects.get(user=self.user)

        # Manually inject a MONTHLY bucket (simulates an edge case) with a
        # SOONER expiry than the trial, to prove type beats expiry.
        monthly_bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=10_000 * CONVERSION_FACTOR,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=1),
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
        """
        CARRY_OVER must drain before TRIAL purely by TYPE priority
        (CARRY_OVER=0, TRIAL=1) — NOT because of relative expiry. Proven
        here by deliberately giving the CARRY_OVER bucket a LATER expiry
        than the trial (90 days vs. the trial's 14), the exact shape of
        bug this ordering guards against: consumption order must never
        regress to "soonest-expiring-first", since a longer-lived
        CARRY_OVER/TRIAL bucket would then wrongly sit unused while a
        shorter-lived one (or MONTHLY) drains first, risking permanent
        loss of the one-shot bucket once IT expires.
        """
        wallet = CreditWallet.objects.get(user=self.user)

        carry_bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.CARRY_OVER,
            total_credits=500 * CONVERSION_FACTOR,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=90),  # later than trial
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
        # get_trial_days_remaining() intentionally reports WHOLE days
        # remaining (floor, per its own docstring) — the instant after a
        # 14-day trial is created, less than 14 full days remain (a few
        # milliseconds have already elapsed), so this legitimately reads
        # 13, not 14.
        self.assertEqual(data["trial_days_remaining"], 13)
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
