"""
Regression coverage for a reported bug: consume_credits' ordering used
to be PRIMARILY expiry-based ("soonest-expiring-first"), with bucket
type only a same-expiry tiebreaker. Since CARRY_OVER's expiry is driven
by `plan.carry_over_expiry_months` (independent of MONTHLY's fixed
1-month cadence), any plan configured with carry_over_expiry_months > 1
caused CARRY_OVER to expire LATER than the concurrent MONTHLY bucket —
inverting the intended priority and draining MONTHLY first while
CARRY_OVER sat untouched, exactly as reported: MONTHLY visibly dropping
on each grading run while CARRY_OVER stayed flat.

Fixed by making type_priority (CARRY_OVER -> TRIAL -> MONTHLY ->
MANUAL_GRANT -> OVERAGE) the PRIMARY sort key, with expires_at only a
secondary tiebreaker within the same type. See billing/models.py,
CreditWallet.consume_credits.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from billing.models import CreditBucket, CreditBucketType, CreditWallet
from users.models import CustomUser, UserTypes


class CarryOverBeforeMonthlyRegressionTestCase(TestCase):
    """Reproduces the exact reported scenario end-to-end."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="reporter@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        now = timezone.now()

        # MONTHLY: fixed ~1 month cadence.
        self.monthly = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=20_000,
            used_credits=0,
            expires_at=now + timedelta(days=30),
        )
        # CARRY_OVER: a plan with carry_over_expiry_months=2 (or more)
        # gives this a LATER expiry than MONTHLY — the exact condition
        # that broke the old "soonest-expiring-first" ordering.
        self.carry_over = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.CARRY_OVER,
            total_credits=2_495,
            used_credits=0,
            expires_at=now + timedelta(days=60),
        )

    def test_carry_over_drains_before_monthly_despite_later_expiry(self):
        # First grading run: 23 credits.
        self.wallet.consume_credits(23, feature="extraction", task_type="grade")
        self.monthly.refresh_from_db()
        self.carry_over.refresh_from_db()
        assert self.carry_over.used_credits == 23
        assert self.monthly.used_credits == 0

        # Second grading run: 18 more credits.
        self.wallet.consume_credits(18, feature="extraction", task_type="grade")
        self.monthly.refresh_from_db()
        self.carry_over.refresh_from_db()
        assert self.carry_over.used_credits == 41
        assert self.monthly.used_credits == 0

    def test_spills_into_monthly_once_carry_over_exhausted(self):
        # Drain the entire carry-over balance, plus a bit more.
        self.wallet.consume_credits(
            2_495 + 100, feature="extraction", task_type="grade"
        )
        self.monthly.refresh_from_db()
        self.carry_over.refresh_from_db()
        assert self.carry_over.used_credits == 2_495
        assert self.monthly.used_credits == 100


class FullTypePriorityOrderTestCase(TestCase):
    """
    Broader check across all five consumable bucket types at once, with
    expiry dates deliberately scrambled so they contradict type order —
    proving type_priority alone governs the sequence.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="allbuckets@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        now = timezone.now()

        # Expiries deliberately in the OPPOSITE order of intended
        # consumption priority (overage None is unaffected either way).
        self.manual_grant = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MANUAL_GRANT,
            total_credits=100,
            used_credits=0,
            expires_at=now + timedelta(days=5),
        )
        self.monthly = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100,
            used_credits=0,
            expires_at=now + timedelta(days=10),
        )
        self.trial = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.TRIAL,
            total_credits=100,
            used_credits=0,
            expires_at=now + timedelta(days=20),
        )
        self.carry_over = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.CARRY_OVER,
            total_credits=100,
            used_credits=0,
            expires_at=now + timedelta(days=90),  # latest expiry of all
        )
        self.overage = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.OVERAGE,
            total_credits=100,
            used_credits=0,
            expires_at=None,
        )

    def _used(self, bucket):
        bucket.refresh_from_db()
        return bucket.used_credits

    def test_consumption_follows_type_priority_not_expiry(self):
        # Consume exactly enough to drain CARRY_OVER, then TRIAL, then
        # MONTHLY, then MANUAL_GRANT, then spill into OVERAGE — in that
        # order, regardless of the scrambled expiry dates above.
        self.wallet.consume_credits(100, feature="t", task_type="t")
        assert self._used(self.carry_over) == 100
        assert self._used(self.trial) == 0

        self.wallet.consume_credits(100, feature="t", task_type="t")
        assert self._used(self.trial) == 100
        assert self._used(self.monthly) == 0

        self.wallet.consume_credits(100, feature="t", task_type="t")
        assert self._used(self.monthly) == 100
        assert self._used(self.manual_grant) == 0

        self.wallet.consume_credits(100, feature="t", task_type="t")
        assert self._used(self.manual_grant) == 100
        assert self._used(self.overage) == 0

        self.wallet.consume_credits(50, feature="t", task_type="t")
        assert self._used(self.overage) == 50
