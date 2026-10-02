"""
billing/tests/test_rollover_not_double_counted.py
=================================================
The ledger must not record the same credits as both carried-over and
expired.

WHAT WAS WRONG
--------------
`SubscriptionService.activate_subscription` retired the outgoing MONTHLY
bucket with `expires_at = now` but left `is_processed = False`. The
nightly `cleanup_expired_credit_buckets` sweep selects on exactly
`(expires_at__lte=now, is_processed=False)`, so it later picked that
bucket up and `expire_bucket()` wrote an EXPIRE row for the full
`total - used` — including the slice that had just been re-granted as
CARRY_OVER moments earlier.

Measured before the fix, on a 10,000-credit bucket with 4,000 used:

    CARRY_OVER grant recorded : 6000
    EXPIRE row for the same bucket: 6000   <-- the same credits, twice

Wallet balances were never wrong (buckets hold the truth), but the
immutable audit trail was, on every upgrade/activation where the user
had unused credits.

The three sibling rollovers already set `is_processed = True` for this
reason — `process_mid_cycle_credit_grant`, `process_rollover_and_renewal`
and `LicenseSubscriptionService._rollover_and_grant_monthly_bucket`, the
first of which spells the rule out in a comment. `activate_subscription`
was the only one that didn't.

THE SECOND TEST IS THE IMPORTANT ONE
------------------------------------
`test_a_genuinely_expired_bucket_still_records_an_expire_row` exists so
the fix cannot be "solved" by disabling expiry accounting altogether. A
bucket that simply runs out of time, with nothing carried over, must
still produce its EXPIRE row.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
)
from billing.services import SubscriptionService
from billing.tasks import cleanup_expired_credit_buckets
from users.models import UserTypes

CustomUser = get_user_model()

MONTHLY_CREDITS = 10_000
USED_CREDITS = 4_000
EXPECTED_UNUSED = MONTHLY_CREDITS - USED_CREDITS  # 6,000


def make_plan(name, *, carry_over_percent=100):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=str(name),
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.STANDARD,
        monthly_credits=MONTHLY_CREDITS,
        is_active=True,
        carry_over_percent=carry_over_percent,
        carry_over_max=10**9,
        carry_over_expiry_months=1,
        max_bank=10**9,
    )


class RolloverNotDoubleCountedTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="rollover.audit@gmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.first_plan = make_plan(PlanType.STANDARD)
        self.second_plan = make_plan(PlanType.PRO)

    def _activate_and_spend(self):
        """Activate a plan, then spend part of the granted bucket."""
        SubscriptionService.activate_subscription(self.user, self.first_plan)
        wallet = self.user.credit_wallet
        bucket = wallet.buckets.get(bucket_type=CreditBucketType.MONTHLY)
        bucket.used_credits = USED_CREDITS
        bucket.save(update_fields=["used_credits"])
        return bucket

    def _expire_rows_for(self, bucket):
        return CreditLedger.objects.filter(
            ledger_type=CreditLedgerType.EXPIRE, bucket_id=bucket.pk
        )

    def test_upgrade_carries_credits_over_exactly_once(self):
        old_bucket = self._activate_and_spend()

        SubscriptionService.activate_subscription(self.user, self.second_plan)

        carry = self.user.credit_wallet.buckets.filter(
            bucket_type=CreditBucketType.CARRY_OVER
        )
        self.assertEqual(carry.count(), 1)
        self.assertEqual(carry.first().total_credits, EXPECTED_UNUSED)

        # The retired bucket must be marked processed, which is what keeps
        # the cleanup sweep away from it.
        old_bucket.refresh_from_db()
        self.assertTrue(old_bucket.is_processed)
        self.assertLessEqual(old_bucket.expires_at, timezone.now())

    def test_cleanup_does_not_expire_credits_that_were_carried_over(self):
        """The regression: the same 6,000 credits granted AND expired."""
        old_bucket = self._activate_and_spend()
        SubscriptionService.activate_subscription(self.user, self.second_plan)

        cleanup_expired_credit_buckets()

        self.assertEqual(
            self._expire_rows_for(old_bucket).count(),
            0,
            "the retired bucket produced an EXPIRE row for credits that had "
            "already been re-granted as CARRY_OVER",
        )

    def test_the_ledger_totals_reconcile_after_an_upgrade(self):
        """
        Grants minus expiries must equal what the wallet can actually spend.
        This is the property the double-count broke.
        """
        self._activate_and_spend()
        SubscriptionService.activate_subscription(self.user, self.second_plan)
        cleanup_expired_credit_buckets()

        granted = sum(
            row.amount
            for row in CreditLedger.objects.filter(
                user_id=self.user.id, ledger_type=CreditLedgerType.GRANT
            )
        )
        expired = sum(
            row.amount
            for row in CreditLedger.objects.filter(
                user_id=self.user.id, ledger_type=CreditLedgerType.EXPIRE
            )
        )

        # Granted: 10,000 (first plan) + 6,000 (carry) + 10,000 (second plan).
        # Expired: nothing yet — the carry and new buckets are both live, and
        # the retired bucket's balance was settled by the carry-over.
        self.assertEqual(granted, MONTHLY_CREDITS + EXPECTED_UNUSED + MONTHLY_CREDITS)
        self.assertEqual(expired, 0)

        # And the spendable balance agrees with grants-minus-spend.
        self.assertEqual(
            self.user.credit_wallet.total_remaining_credits(),
            granted - USED_CREDITS - EXPECTED_UNUSED,
        )

    def test_the_wallet_balance_is_unchanged_by_the_fix(self):
        """
        The bug was ledger-only. Balances must be exactly what they were:
        carry-over + the new plan's grant.
        """
        self._activate_and_spend()
        SubscriptionService.activate_subscription(self.user, self.second_plan)
        cleanup_expired_credit_buckets()

        self.assertEqual(
            self.user.credit_wallet.total_remaining_credits(),
            EXPECTED_UNUSED + MONTHLY_CREDITS,
        )

    # ---- the guard against "fixing" this by disabling expiry entirely ----

    def test_a_genuinely_expired_bucket_still_records_an_expire_row(self):
        """
        A bucket that just runs out of time, with no rollover involved,
        must still be swept and logged. Without this, setting
        is_processed=True everywhere would look like a passing fix while
        silently destroying expiry accounting.
        """
        wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        stale = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MANUAL_GRANT,
            total_credits=3_000,
            used_credits=1_000,
            expires_at=timezone.now() - timedelta(days=1),
            is_processed=False,
        )

        cleanup_expired_credit_buckets()

        rows = self._expire_rows_for(stale)
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().amount, 2_000)
        stale.refresh_from_db()
        self.assertTrue(stale.is_processed)

    def test_a_fully_spent_retired_bucket_produces_no_ledger_noise(self):
        """A bucket with nothing left should never generate an EXPIRE row."""
        SubscriptionService.activate_subscription(self.user, self.first_plan)
        wallet = self.user.credit_wallet
        bucket = wallet.buckets.get(bucket_type=CreditBucketType.MONTHLY)
        bucket.used_credits = MONTHLY_CREDITS  # fully consumed
        bucket.save(update_fields=["used_credits"])

        SubscriptionService.activate_subscription(self.user, self.second_plan)
        cleanup_expired_credit_buckets()

        self.assertEqual(self._expire_rows_for(bucket).count(), 0)
        self.assertEqual(
            wallet.buckets.filter(bucket_type=CreditBucketType.CARRY_OVER).count(),
            0,
            "nothing was left to carry over",
        )
