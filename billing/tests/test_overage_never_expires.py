"""
Locks in that OVERAGE credit buckets never expire (expires_at=None),
across every path that grants them:

- SubscriptionService.grant_overage_bucket (individual, direct primitive)
- StripeWebhookHandler._handle_overage_checkout_completed (individual,
  Stripe Checkout confirmation)
- StripeWebhookHandler.handle_payment_intent_succeeded (individual,
  legacy PaymentIntent fallback — "preferred" snapshotted-metadata branch)
- LicenseSubscriptionService._grant_overage_blocks, exercised via the
  Stripe license-overage-checkout webhook
  (StripeWebhookHandler._handle_license_overage_checkout_completed)

License-side coverage for the superadmin-grant and offline-approval
paths (which also route through _grant_overage_blocks) lives in
test_license_overage_offline.py, alongside its own existing fixtures.

Also locks in that consumption ORDER is unaffected by the removal of
overage's expiry: OVERAGE must still be drawn from last, even though it
(along with MANUAL_GRANT) has no expires_at — consume_credits already
supported null-expiry buckets before this change (that's how
MANUAL_GRANT always worked), this just confirms OVERAGE behaves the
same way now.

Stripe API calls are avoided entirely in the webhook-level tests by
constructing session/payment_intent payloads with no invoice/charge/
payment_intent id set — resolve_stripe_receipt_url only calls out to
Stripe when one of those is present, so leaving them None keeps these
tests network-call-free without needing to mock `stripe.*`.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.services import SubscriptionService
from billing.stripe_service import StripeWebhookHandler
from classrooms.models import School
from users.models import CustomUser, UserTypes


class GrantOverageBucketNeverExpiresTestCase(TestCase):
    """Direct coverage of the shared individual-side primitive."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="teacher@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Pro",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.PRO,
            monthly_credits=30_000_000,
            overage_block_size=5_000_000,
            overage_block_price=500,
            max_overage_blocks=5,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)

    def test_grant_overage_bucket_sets_no_expiry(self):
        bucket = SubscriptionService.grant_overage_bucket(
            wallet=self.wallet,
            plan=self.plan,
            quantity=2,
            stripe_payment_intent_id="pi_test_123",
        )

        assert bucket.bucket_type == CreditBucketType.OVERAGE
        assert bucket.total_credits == self.plan.overage_block_size * 2
        assert bucket.expires_at is None

    def test_grant_overage_bucket_no_longer_accepts_expires_at_kwarg(self):
        # Locks in the signature change — expires_at is gone, not just
        # ignored, so a caller can't accidentally believe it still works.
        import inspect

        sig = inspect.signature(SubscriptionService.grant_overage_bucket)
        assert "expires_at" not in sig.parameters


class OverageWebhookNeverExpiresTestCase(TestCase):
    """
    Individual-side webhook handlers that grant overage credits after
    Stripe confirms payment — both the live Checkout-based flow and the
    legacy PaymentIntent fallback.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="teacher@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Pro",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.PRO,
            monthly_credits=30_000_000,
            overage_block_size=5_000_000,
            overage_block_price=500,
            max_overage_blocks=5,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        self.user_sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            next_credit_grant_at=timezone.now() + timedelta(days=30),
            stripe_subscription_id="sub_test_123",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )

    def test_checkout_completed_webhook_grants_non_expiring_bucket(self):
        session = {
            "id": "cs_test_1",
            "payment_intent": None,
            "invoice": None,
            "amount_total": 500,
            "currency": "usd",
        }
        metadata = {
            "flow": "overage_block_purchase_checkout",
            "wallet_id": str(self.wallet.id),
            "plan_id": str(self.plan.id),
            "quantity": "1",
        }

        StripeWebhookHandler._handle_overage_checkout_completed(session, metadata)

        bucket = CreditBucket.objects.get(
            wallet=self.wallet, bucket_type=CreditBucketType.OVERAGE
        )
        assert bucket.total_credits == self.plan.overage_block_size
        assert bucket.expires_at is None

    def test_payment_intent_succeeded_preferred_path_grants_non_expiring_bucket(self):
        """
        The "preferred path" branch (wallet_id + plan_id snapshotted in
        PaymentIntent metadata) no longer requires or reads
        overage_expires_at — confirms it still grants correctly (and
        without crashing on the now-removed metadata key) with no
        expiry on the resulting bucket.
        """
        payment_intent = {
            "id": "pi_test_456",
            "metadata": {
                "flow": "overage_block_purchase",
                "wallet_id": str(self.wallet.id),
                "plan_id": str(self.plan.id),
            },
        }

        StripeWebhookHandler.handle_payment_intent_succeeded(payment_intent)

        bucket = CreditBucket.objects.get(
            wallet=self.wallet, bucket_type=CreditBucketType.OVERAGE
        )
        assert bucket.total_credits == self.plan.overage_block_size
        assert bucket.expires_at is None

    def test_payment_intent_succeeded_preferred_path_does_not_double_grant(self):
        """
        Regression lock for a pre-existing bug this work surfaced: the
        preferred-path branch (wallet_id + plan_id present) previously had
        no `return` after granting, so it fell through into the legacy
        fallback section and — for a realistic PaymentIntent that ALSO
        carries user_id in its metadata — would attempt to grant overage
        credits a second time for the same payment. Includes user_id here
        specifically to prove the fallback path is never reached at all.
        """
        payment_intent = {
            "id": "pi_test_789",
            "metadata": {
                "flow": "overage_block_purchase",
                "wallet_id": str(self.wallet.id),
                "plan_id": str(self.plan.id),
                "user_id": str(self.user.id),
            },
        }

        StripeWebhookHandler.handle_payment_intent_succeeded(payment_intent)

        buckets = CreditBucket.objects.filter(
            wallet=self.wallet, bucket_type=CreditBucketType.OVERAGE
        )
        assert buckets.count() == 1
        assert buckets.first().total_credits == self.plan.overage_block_size


class LicenseOverageCheckoutWebhookNeverExpiresTestCase(TestCase):
    """
    License-side Stripe checkout fulfillment — exercises
    _grant_overage_blocks (shared with the offline/superadmin paths
    already covered in test_license_overage_offline.py) through the
    Stripe webhook specifically, since it's the one remaining call site
    not covered there.
    """

    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.admin = CustomUser.objects.create_user(
            email="admin@school.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="License Pro",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
            overage_block_size=5000,
            overage_block_price=299,
        )
        self.license_sub = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
        )
        self.teacher = CustomUser.objects.create_user(
            email="teacher@school.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        from billing.license_service import LicenseSubscriptionService

        LicenseSubscriptionService.add_teacher_to_license(
            self.license_sub, self.teacher.email
        )

    def test_license_overage_checkout_completed_grants_non_expiring_bucket(self):
        from billing.models import (
            LicenseOveragePurchaseIntent,
            LicenseOveragePurchaseStatus,
        )

        intent = LicenseOveragePurchaseIntent.objects.create(
            license_subscription=self.license_sub,
            initiated_by=self.admin,
            total_blocks=2,
            allocations={str(self.teacher.id): 2},
            block_size_snapshot=self.plan.overage_block_size,
            unit_price_cents_snapshot=self.plan.overage_block_price,
            amount_cents=2 * self.plan.overage_block_price,
            status=LicenseOveragePurchaseStatus.PENDING,
            stripe_checkout_session_id="cs_license_test",
        )

        session = {
            "id": "cs_license_test",
            "payment_status": "paid",
            "payment_intent": None,
            "invoice": None,
            "amount_total": 2 * self.plan.overage_block_price,
            "currency": "usd",
        }
        metadata = {
            "flow": "license_overage_purchase_checkout",
            "license_id": str(self.license_sub.id),
            "intent_id": str(intent.id),
        }

        StripeWebhookHandler._handle_license_overage_checkout_completed(
            session, metadata
        )

        bucket = CreditBucket.objects.get(
            wallet__user=self.teacher, bucket_type=CreditBucketType.OVERAGE
        )
        assert bucket.total_credits == 2 * self.plan.overage_block_size
        assert bucket.expires_at is None

        intent.refresh_from_db()
        assert intent.status == LicenseOveragePurchaseStatus.COMPLETED


class OverageConsumptionOrderTestCase(TestCase):
    """
    Confirms removing overage's expiry does NOT change consumption
    order: OVERAGE must still be drawn from strictly last, after a
    live, time-bounded MONTHLY bucket, even though OVERAGE now has no
    expires_at at all (previously it did, but ordering never depended
    on that — the sentinel-based sort already pushes OVERAGE to the
    back unconditionally).
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="teacher@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        now = timezone.now()

        self.monthly_bucket = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=1000,
            used_credits=0,
            expires_at=now + timedelta(days=30),
        )
        self.overage_bucket = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.OVERAGE,
            total_credits=1000,
            used_credits=0,
            expires_at=None,
        )

    def test_overage_consumed_only_after_monthly_exhausted(self):
        # Consume exactly the MONTHLY balance — OVERAGE must be untouched.
        self.wallet.consume_credits(1000, feature="test", task_type="test")
        self.monthly_bucket.refresh_from_db()
        self.overage_bucket.refresh_from_db()
        assert self.monthly_bucket.used_credits == 1000
        assert self.overage_bucket.used_credits == 0

        # Now consume more, forcing a spillover into OVERAGE despite it
        # having no expires_at at all.
        self.wallet.consume_credits(400, feature="test", task_type="test")
        self.overage_bucket.refresh_from_db()
        assert self.overage_bucket.used_credits == 400

    def test_overage_with_no_expiry_still_counted_as_available(self):
        total = self.wallet.total_remaining_credits()
        assert total == 2000
