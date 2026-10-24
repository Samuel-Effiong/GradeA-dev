"""
billing/tests/test_annual_mid_cycle_grants.py
=============================================
Locks the fix for a bug that is INVISIBLE in month 1 and monotonically
wrong from month 2 to month 12 of every annual subscription.

THE BUG
-------
SubscriptionService.process_mid_cycle_credit_grant selected the bucket to
retire with:

    wallet.buckets.select_for_update()
        .filter(bucket_type=CreditBucketType.MONTHLY)
        .first()

There is no .order_by(), so Django applied CreditBucket.Meta.ordering —
["expires_at", "created_at"], i.e. OLDEST EXPIRY FIRST. A retired bucket
has expires_at set to the moment it was retired, which is always earlier
than the live bucket's expiry. So from the SECOND mid-cycle grant onward
`.first()` returned an ALREADY-RETIRED bucket.

That is a two-sided defect, not merely a stale read:
  * OVER-credit — month 1's unused balance is converted into a fresh
    CARRY_OVER bucket again every month for the rest of the year.
  * UNDER-credit — months 2..11's genuinely unused credits are never
    rolled over at all, and simply expire.

WHY IT SURVIVED
---------------
billing/tests/test_subscription_cycle_integrity.py loops eleven
mid-cycle grants but asserts only on next_credit_grant_at and
billing_cycle_end. Both are computed from `now` and are correct no
matter which bucket was picked. The defect is only observable in bucket
IDENTITY and in CARRY_OVER AMOUNTS, so this file asserts on both.

The load-bearing assertion is that the carry-over series is NON-CONSTANT.
Before the fix every month re-rolls month 1 and the series is flat; after
the fix each month reflects its own consumption.
"""

from datetime import timedelta
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.immutable import allow_unsafe_mutation
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
from billing.services import SubscriptionService
from billing.tasks import cleanup_expired_credit_buckets
from users.models import UserTypes

CustomUser = get_user_model()

MONTHLY_CREDITS = 10_000
CARRY_PERCENT = 50

# A distinct consumption each month, so every month's correct carry-over
# is a different number. With a flat series the bug is indistinguishable
# from correct behaviour.
CONSUMPTION_BY_MONTH = [4_000, 2_000, 6_000, 1_000]
# 50% of (10_000 - used), floored.
EXPECTED_CARRY_OVER = [3_000, 4_000, 2_000, 4_500]


def make_annual_plan():
    return SubscriptionPlan.objects.create(
        name="POWER_ANNUAL",
        display_name="Power Annual",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.POWER,
        interval=BillingInterval.ANNUAL,
        price_cents=99_900,
        monthly_credits=MONTHLY_CREDITS,
        stripe_price_id="price_annual_midcycle",
        carry_over_percent=CARRY_PERCENT,
        # Long enough that no carry-over bucket expires during the test —
        # expiry is a separate concern and would confuse these assertions.
        carry_over_expiry_months=6,
        max_bank=None,
        is_active=True,
    )


class AnnualMidCycleGrantTests(TestCase):
    """Multiple consecutive mid-cycle grants on one annual subscription."""

    def setUp(self):
        self.plan = make_annual_plan()
        self.user = CustomUser.objects.create_user(
            email="annual@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        # Registration signals may auto-activate a trial. Clear everything
        # so the test starts from the state it chose, not one a signal chose.
        UserSubscription.objects.filter(user=self.user).delete()
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        self.wallet.buckets.all().delete()
        # The ledger is append-only (billing/immutable.py), so clearing
        # signal-created rows needs the explicit test escape hatch. This
        # is fabricating a starting state, not editing history.
        with allow_unsafe_mutation():
            CreditLedger.objects.filter(user_id=self.user.id).delete()

        self.t0 = timezone.now().replace(microsecond=0)
        self.subscription = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=self.t0,
            billing_cycle_end=self.t0 + relativedelta(years=1),
            next_credit_grant_at=self.t0 + relativedelta(months=1),
        )
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=MONTHLY_CREDITS,
            used_credits=0,
            expires_at=self.t0 + relativedelta(months=1),
        )

    # -- helpers ---------------------------------------------------------

    def _live_monthly_bucket(self):
        """Newest MONTHLY bucket.

        Deliberately identified by created_at rather than is_processed:
        before the fix is_processed is never set, so an is_processed-based
        lookup would not work on the pre-fix code and the test could not
        demonstrate the bug.
        """
        return (
            CreditBucket.objects.filter(
                wallet=self.wallet, bucket_type=CreditBucketType.MONTHLY
            )
            .order_by("-created_at")
            .first()
        )

    def _carry_over_totals(self):
        return list(
            CreditBucket.objects.filter(
                wallet=self.wallet, bucket_type=CreditBucketType.CARRY_OVER
            )
            .order_by("created_at")
            .values_list("total_credits", flat=True)
        )

    def _run_months(self, count, run_cleanup_between=False):
        """Drive `count` consecutive mid-cycle grants, consuming a
        different amount each month. Returns the ids of the buckets that
        were live going into each grant, in order."""
        live_ids = []
        for index in range(count):
            live = self._live_monthly_bucket()
            live_ids.append(live.id)

            live.used_credits = CONSUMPTION_BY_MONTH[index]
            live.save(update_fields=["used_credits"])

            self.subscription.refresh_from_db()
            grant_moment = self.subscription.next_credit_grant_at

            with patch("django.utils.timezone.now", return_value=grant_moment):
                SubscriptionService.process_mid_cycle_credit_grant(self.subscription)
                if run_cleanup_between:
                    cleanup_expired_credit_buckets()

            self.subscription.refresh_from_db()
        return live_ids

    # -- tests -----------------------------------------------------------

    def test_each_grant_retires_the_bucket_that_was_actually_live(self):
        live_ids = self._run_months(len(CONSUMPTION_BY_MONTH))

        # Every bucket that was live going into a grant must now be
        # retired. Before the fix only the FIRST one ever is, because
        # month 1's bucket is re-selected every time.
        for month, bucket_id in enumerate(live_ids, start=1):
            bucket = CreditBucket.objects.get(id=bucket_id)
            self.assertTrue(
                bucket.is_processed,
                f"month {month}: the bucket that was live was never retired "
                f"(is_processed={bucket.is_processed!r}) — the grant retired "
                "a different, already-retired bucket instead.",
            )

    def test_carry_over_amounts_reflect_each_month_s_own_consumption(self):
        """The load-bearing assertion. A flat series means every month
        re-rolled month 1."""
        self._run_months(len(CONSUMPTION_BY_MONTH))

        self.assertEqual(
            self._carry_over_totals(),
            EXPECTED_CARRY_OVER,
            "carry-over amounts do not track each month's own unused "
            "credits; a constant series means the same stale bucket was "
            "rolled over repeatedly.",
        )

    def test_exactly_one_live_monthly_bucket_remains(self):
        self._run_months(len(CONSUMPTION_BY_MONTH))

        monthly = CreditBucket.objects.filter(
            wallet=self.wallet, bucket_type=CreditBucketType.MONTHLY
        )
        self.assertEqual(monthly.count(), len(CONSUMPTION_BY_MONTH) + 1)

        unprocessed = list(monthly.filter(is_processed=False))
        self.assertEqual(
            len(unprocessed),
            1,
            "exactly one MONTHLY bucket may be live after a series of "
            f"mid-cycle grants; found {len(unprocessed)}.",
        )
        self.assertEqual(unprocessed[0].id, self._live_monthly_bucket().id)

    def test_rollover_ledger_entries_match_the_granted_carry_over(self):
        self._run_months(len(CONSUMPTION_BY_MONTH))

        amounts = list(
            CreditLedger.objects.filter(
                user_id=self.user.id,
                ledger_type=CreditLedgerType.GRANT,
                reference__startswith="Mid-cycle rollover",
            )
            .order_by("created_at")
            .values_list("amount", flat=True)
        )
        self.assertEqual(amounts, EXPECTED_CARRY_OVER)

    def test_cleanup_task_running_between_grants_does_not_change_selection(self):
        """Proves the fix does not depend on the expiry sweeper's timing.
        cleanup_expired_credit_buckets runs at 05:00 while annual grants
        run at 02:00, so in production they genuinely interleave."""
        self._run_months(len(CONSUMPTION_BY_MONTH), run_cleanup_between=True)

        self.assertEqual(self._carry_over_totals(), EXPECTED_CARRY_OVER)

    def test_grant_dates_still_advance_correctly(self):
        """Non-regression: the property the existing cycle-integrity test
        already guards must not change."""
        self._run_months(len(CONSUMPTION_BY_MONTH))

        self.subscription.refresh_from_db()
        self.assertEqual(
            self.subscription.billing_cycle_end, self.t0 + relativedelta(years=1)
        )
        expected_next = self.t0 + relativedelta(months=len(CONSUMPTION_BY_MONTH) + 1)
        self.assertLess(
            abs(
                (self.subscription.next_credit_grant_at - expected_next).total_seconds()
            ),
            timedelta(days=2).total_seconds(),
            "next_credit_grant_at drifted away from the monthly cadence.",
        )
