"""
billing/tests/test_overage_payment_intent_idempotency.py
========================================================
Duplicate-grant protection for the `payment_intent.succeeded` overage
fallback, and the query it uses to enforce it.

WHY THE QUERY CHANGED
---------------------
The guard was::

    CreditLedger.objects.filter(
        metadata__stripe_payment_intent_id=payment_intent["id"]
    ).exists()

`metadata` is an unindexed JSONB column, so this was a sequential scan of
CreditLedger — verified with `EXPLAIN` — inside a webhook transaction
holding row locks. CreditLedger is the fastest-growing table in the
system (17,761 rows for a single production teacher), so the cost grows
without bound against Stripe's webhook timeout.

WHY NOT KEY ON BillingTransaction
---------------------------------
`BillingTransaction.stripe_payment_intent_id` IS indexed and looks like
the obvious fix, but it is not usable as the source of truth: this flow
never writes a BillingTransaction — only the checkout.session.completed
handlers do. Keying on it would report "not granted" on every delivery
and re-grant the credits each time, turning a slow-but-correct guard into
a duplicate-credit bug. `test_billing_transaction_is_not_a_usable_key`
pins that fact so the "obvious" refactor cannot be made later by mistake.

WHAT IT IS NOW
--------------
The same predicate, scoped to the paying wallet's own ledger rows. That
cannot miss a true match — the grant always writes its ledger row against
a bucket belonging to that wallet — and it replaces the full-table scan
with a lookup driven by the indexed bucket FK.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    BillingTransaction,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditWallet,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
    UserSubscription,
)
from billing.stripe_service import StripeWebhookHandler
from users.models import UserTypes

CustomUser = get_user_model()

BLOCK_SIZE = 5_000_000
PAYMENT_INTENT_ID = "pi_overage_test_1"


def make_plan():
    return SubscriptionPlan.objects.create(
        name=PlanType.STANDARD,
        display_name="Standard",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.STANDARD,
        monthly_credits=10_000,
        is_active=True,
        overage_block_size=BLOCK_SIZE,
        overage_block_price=500,
        max_overage_blocks=5,
    )


def payment_intent(intent_id, wallet, plan):
    return {
        "id": intent_id,
        "metadata": {
            "flow": "overage_block_purchase",
            "wallet_id": str(wallet.id),
            "plan_id": str(plan.id),
        },
    }


class OverageGrantIdempotencyTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="overage.idem@gmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.plan = make_plan()
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        now = timezone.now()
        UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            billing_cycle_start=now,
            billing_cycle_end=now + timedelta(days=30),
        )

    def _overage_buckets(self):
        return CreditBucket.objects.filter(
            wallet=self.wallet, bucket_type=CreditBucketType.OVERAGE
        )

    def test_a_first_delivery_grants_the_credits(self):
        StripeWebhookHandler.handle_payment_intent_succeeded(
            payment_intent(PAYMENT_INTENT_ID, self.wallet, self.plan)
        )

        self.assertEqual(self._overage_buckets().count(), 1)
        self.assertEqual(self._overage_buckets().first().total_credits, BLOCK_SIZE)

    def test_a_redelivery_does_not_grant_again(self):
        """The property this guard exists for."""
        event = payment_intent(PAYMENT_INTENT_ID, self.wallet, self.plan)

        StripeWebhookHandler.handle_payment_intent_succeeded(event)
        StripeWebhookHandler.handle_payment_intent_succeeded(event)
        StripeWebhookHandler.handle_payment_intent_succeeded(event)

        self.assertEqual(
            self._overage_buckets().count(),
            1,
            "a repeated Stripe delivery granted overage credits more than once",
        )

    def test_a_different_payment_intent_still_grants(self):
        """The guard must not over-match and block a genuine second purchase."""
        StripeWebhookHandler.handle_payment_intent_succeeded(
            payment_intent("pi_first", self.wallet, self.plan)
        )
        StripeWebhookHandler.handle_payment_intent_succeeded(
            payment_intent("pi_second", self.wallet, self.plan)
        )

        self.assertEqual(self._overage_buckets().count(), 2)

    def test_another_wallets_grant_does_not_suppress_this_one(self):
        """
        The scoping must not create a false POSITIVE either: an identical
        intent id recorded against a different wallet must not be read as
        "already granted" here.
        """
        other_user = CustomUser.objects.create_user(
            email="overage.other@gmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        other_wallet, _ = CreditWallet.objects.get_or_create(user=other_user)
        StripeWebhookHandler.handle_payment_intent_succeeded(
            payment_intent(PAYMENT_INTENT_ID, other_wallet, self.plan)
        )

        StripeWebhookHandler.handle_payment_intent_succeeded(
            payment_intent(PAYMENT_INTENT_ID, self.wallet, self.plan)
        )

        self.assertEqual(self._overage_buckets().count(), 1)
        self.assertEqual(
            CreditBucket.objects.filter(
                wallet=other_wallet, bucket_type=CreditBucketType.OVERAGE
            ).count(),
            1,
        )

    def test_an_unrelated_flow_is_ignored(self):
        StripeWebhookHandler.handle_payment_intent_succeeded(
            {"id": "pi_other", "metadata": {"flow": "something_else"}}
        )

        self.assertEqual(self._overage_buckets().count(), 0)

    def test_the_grant_is_recorded_against_this_payment_intent(self):
        """The ledger metadata is what the guard reads, so pin it."""
        StripeWebhookHandler.handle_payment_intent_succeeded(
            payment_intent(PAYMENT_INTENT_ID, self.wallet, self.plan)
        )

        self.assertTrue(
            CreditLedger.objects.filter(
                bucket__wallet=self.wallet,
                metadata__stripe_payment_intent_id=PAYMENT_INTENT_ID,
            ).exists()
        )

    def test_billing_transaction_is_not_a_usable_key(self):
        """
        Documents WHY the indexed BillingTransaction column was not used.
        This flow writes no BillingTransaction, so keying idempotency on it
        would never match and would re-grant on every redelivery. If this
        ever starts failing, the trade-off should be revisited — the
        indexed column would then become the better guard.
        """
        StripeWebhookHandler.handle_payment_intent_succeeded(
            payment_intent(PAYMENT_INTENT_ID, self.wallet, self.plan)
        )

        self.assertFalse(
            BillingTransaction.objects.filter(
                stripe_payment_intent_id=PAYMENT_INTENT_ID
            ).exists(),
            "this flow now writes a BillingTransaction — reconsider using "
            "the indexed stripe_payment_intent_id as the idempotency key",
        )


class OverageGuardQueryShapeTests(TestCase):
    """The lookup must not scan the whole ledger."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="overage.plan@gmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)

    def test_the_guard_is_scoped_by_wallet_not_a_bare_json_filter(self):
        plan = StripeWebhookHandler._overage_already_granted
        self.assertFalse(plan(PAYMENT_INTENT_ID, self.wallet))

        sql = str(
            CreditLedger.objects.filter(
                bucket__wallet=self.wallet,
                metadata__stripe_payment_intent_id=PAYMENT_INTENT_ID,
            ).query
        )
        # The wallet predicate is what keeps this off a full-table scan.
        self.assertIn("wallet_id", sql)
        self.assertIn("stripe_payment_intent_id", sql)
