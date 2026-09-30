"""
billing/tests/test_plan_change_retires_old_bucket.py
====================================================
H-76: an immediate plan change must retire the old MONTHLY bucket the way
its three sibling rollovers do (activate_subscription's upgrade rollover,
process_rollover_and_renewal, the licence rollover): expires_at = now AND
is_processed = True.

apply_immediate_plan_change set only expires_at. The bucket then looked,
to cleanup_expired_credit_buckets (05:00), like one that had simply
expired, and the cleanup wrote an EXPIRE row for its unused credits,
which the plan change had already rolled over into a CARRY_OVER bucket.
The ledger counted the same credits twice (once carried over, once
expired). Balances were not affected: the bucket had already expired.

Found by d5 while building the monthly rollover fix (2026-09-30).
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.immutable import allow_unsafe_mutation
from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    PlanTier,
    PlanType,
    UserSubscription,
)
from billing.services import SubscriptionService
from billing.tasks import cleanup_expired_credit_buckets
from billing.tests.tests_free_trial import make_individual_plan
from users.models import UserTypes

CustomUser = get_user_model()

USED = 4_000_000


class PlanChangeRetiresOldBucketTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="plan-change@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        UserSubscription.objects.filter(user=self.user).delete()
        wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        wallet.buckets.all().delete()
        with allow_unsafe_mutation():
            CreditLedger.objects.filter(user_id=self.user.id).delete()

        self.sub = SubscriptionService.activate_subscription(
            self.user, make_individual_plan()
        )
        self.old = CreditBucket.objects.get(
            wallet__user=self.user, bucket_type=CreditBucketType.MONTHLY
        )
        self.old.used_credits = USED
        self.old.save(update_fields=["used_credits"])
        self.bigger = make_individual_plan(
            name=PlanType.PRO,
            display_name="Pro Grader",
            monthly_credits=20_000_000,
        )
        self.bigger.tier = PlanTier.PRO
        self.bigger.save(update_fields=["tier"])

        SubscriptionService.apply_immediate_plan_change(self.sub, self.bigger)
        self.old.refresh_from_db()

    def expire_rows(self):
        return CreditLedger.objects.filter(
            bucket=self.old, ledger_type=CreditLedgerType.EXPIRE
        )

    def test_the_old_bucket_is_retired_as_processed(self):
        self.assertTrue(self.old.is_processed)
        retired_at = self.old.expires_at
        assert retired_at is not None
        self.assertLessEqual(retired_at, timezone.now())

    def test_the_cleanup_does_not_expire_what_was_rolled_over(self):
        carried = CreditBucket.objects.get(
            wallet__user=self.user, bucket_type=CreditBucketType.CARRY_OVER
        )
        self.assertGreater(carried.total_credits, 0, "nothing was rolled over")

        later = timezone.now() + timedelta(hours=6)
        with patch("django.utils.timezone.now", return_value=later):
            cleanup_expired_credit_buckets()

        self.assertEqual(self.expire_rows().count(), 0)
