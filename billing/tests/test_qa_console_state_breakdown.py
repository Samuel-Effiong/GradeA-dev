"""
billing/tests/test_qa_console_state_breakdown.py
==================================================
Covers billing.qa_console._describe_state -- specifically the
per-bucket-type credit breakdown, which exists so that a wrong wallet
total is EXPLAINABLE rather than merely visible.

Its origin is a real bug: the console showed a flat "15,000,000
remaining" for a user who had just moved from trial to monthly and
should have had 10,000,000. A bare total gives you nowhere to go; the
same number split as "MONTHLY 10,000,000 + TRIAL 5,000,000" names the
defect (a leftover trial bucket stacking on the new grant, fixed in
SubscriptionService.activate_subscription -- see
test_trial_forfeiture_on_activation.py) on sight.

That makes the breakdown a diagnostic instrument, and an instrument
that lies is worse than no instrument. So the load-bearing test here is
test_live_subtotals_reconcile_with_the_wallet_total: the subtotals come
from a SEPARATE aggregate query than
CreditWallet.total_remaining_credits(), and the two must agree by
construction, not by luck. Everything is real DB objects and no Stripe
-- _describe_state only reads local state.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from billing.models import CreditBucket, CreditBucketType, CreditWallet
from billing.qa_console import _describe_state
from billing.services import SubscriptionService
from billing.stripe_live_qa_scenarios import Subscriber
from billing.tests.tests_free_trial import make_individual_plan, make_teacher


def _by_type(state):
    return {row["type"]: row for row in state["wallet"]["by_type"]}


class DescribeStateBreakdownTests(TestCase):
    def setUp(self):
        self.user = make_teacher("console-state@example.com")
        self.plan = make_individual_plan()
        self.sub = Subscriber(
            user=self.user,
            clock_id="clock_x",
            customer_id="cus_x",
            stripe_subscription_id="sub_x",
            plan=self.plan,
        )

    # -- the empty / degenerate shapes ------------------------------------

    def _wallet(self):
        """A CustomUser gets a CreditWallet automatically on creation, so
        these tests fetch that one rather than creating a second (which
        the OneToOne would reject)."""
        return CreditWallet.objects.get(user=self.user)

    def test_a_user_with_no_wallet_yet_reports_null_rather_than_crashing(self):
        CreditWallet.objects.filter(user=self.user).delete()
        state = _describe_state(self.sub)
        self.assertIsNone(state["wallet"])
        self.assertIsNone(state["local_subscription"])

    def test_a_wallet_with_no_buckets_has_an_empty_breakdown(self):
        state = _describe_state(self.sub)
        self.assertEqual(state["wallet"]["by_type"], [])
        self.assertEqual(state["wallet"]["total_remaining_credits"], 0)

    # -- the breakdown itself ----------------------------------------------

    def test_the_reported_bug_shape_is_split_by_type(self):
        """The exact console reading that started this: a trial bucket
        stacked on a monthly grant. Whatever the total says, the
        breakdown must attribute it to the two types separately."""
        SubscriptionService.activate_free_trial(self.user, self.plan)
        wallet = self._wallet()
        # Re-create the pre-fix state directly rather than calling
        # activate_subscription, which now (correctly) forfeits the
        # trial -- the point here is that the BREAKDOWN renders a
        # stacked wallet legibly, independent of whether the billing
        # bug that produced one still exists.
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=self.plan.monthly_credits,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )

        rows = _by_type(_describe_state(self.sub))

        self.assertEqual(
            rows[CreditBucketType.MONTHLY]["live_remaining_credits"],
            self.plan.monthly_credits,
        )
        self.assertEqual(
            rows[CreditBucketType.TRIAL]["live_remaining_credits"],
            SubscriptionService.TRIAL_CREDITS_RAW,
        )

    def test_after_the_forfeiture_fix_the_trial_row_reports_zero_live(self):
        """The other half of the same picture: once activate_subscription
        forfeits the trial, TRIAL must still APPEAR (its history is real
        and worth seeing) but contribute nothing live."""
        SubscriptionService.activate_free_trial(self.user, self.plan)
        SubscriptionService.activate_subscription(self.user, self.plan)

        rows = _by_type(_describe_state(self.sub))

        self.assertIn(CreditBucketType.TRIAL, rows)
        self.assertEqual(rows[CreditBucketType.TRIAL]["live_remaining_credits"], 0)
        self.assertEqual(rows[CreditBucketType.TRIAL]["live_bucket_count"], 0)
        # ...but its all-time grant is still on the record.
        self.assertEqual(
            rows[CreditBucketType.TRIAL]["total_credits"],
            SubscriptionService.TRIAL_CREDITS_RAW,
        )
        self.assertEqual(
            rows[CreditBucketType.MONTHLY]["live_remaining_credits"],
            self.plan.monthly_credits,
        )

    def test_used_credits_are_reported_separately_from_remaining(self):
        SubscriptionService.activate_free_trial(self.user, self.plan)
        bucket = CreditBucket.objects.get(
            wallet__user=self.user, bucket_type=CreditBucketType.TRIAL
        )
        bucket.used_credits = 2_000_000
        bucket.save(update_fields=["used_credits"])

        row = _by_type(_describe_state(self.sub))[CreditBucketType.TRIAL]

        self.assertEqual(row["total_credits"], SubscriptionService.TRIAL_CREDITS_RAW)
        self.assertEqual(row["used_credits"], 2_000_000)
        self.assertEqual(
            row["live_remaining_credits"],
            SubscriptionService.TRIAL_CREDITS_RAW - 2_000_000,
        )

    def test_multiple_buckets_of_one_type_are_summed_and_counted(self):
        wallet = self._wallet()
        for _ in range(3):
            CreditBucket.objects.create(
                wallet=wallet,
                bucket_type=CreditBucketType.MONTHLY,
                total_credits=1_000_000,
                used_credits=100_000,
                expires_at=timezone.now() + timedelta(days=30),
            )

        row = _by_type(_describe_state(self.sub))[CreditBucketType.MONTHLY]

        self.assertEqual(row["bucket_count"], 3)
        self.assertEqual(row["live_bucket_count"], 3)
        self.assertEqual(row["total_credits"], 3_000_000)
        self.assertEqual(row["used_credits"], 300_000)
        self.assertEqual(row["live_remaining_credits"], 2_700_000)

    def test_an_expired_bucket_is_excluded_from_live_but_kept_in_all_time(self):
        wallet = self._wallet()
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.CARRY_OVER,
            total_credits=500_000,
            used_credits=0,
            expires_at=timezone.now() - timedelta(days=1),
        )

        row = _by_type(_describe_state(self.sub))[CreditBucketType.CARRY_OVER]

        self.assertEqual(row["live_remaining_credits"], 0)
        self.assertEqual(row["live_bucket_count"], 0)
        self.assertEqual(row["bucket_count"], 1)
        self.assertEqual(row["total_credits"], 500_000)

    def test_a_never_expiring_bucket_counts_as_live(self):
        """OVERAGE buckets are created with expires_at=NULL. The wallet
        aggregate treats NULL as live, so the breakdown must too --
        filtering on `expires_at__gt=now` alone would silently drop
        every overage block a user paid for."""
        wallet = self._wallet()
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.OVERAGE,
            total_credits=5_000_000,
            used_credits=0,
            expires_at=None,
        )

        row = _by_type(_describe_state(self.sub))[CreditBucketType.OVERAGE]

        self.assertEqual(row["live_remaining_credits"], 5_000_000)
        self.assertEqual(row["live_bucket_count"], 1)

    def test_a_processed_bucket_that_has_not_expired_still_counts_as_live(self):
        """is_processed is NOT part of total_remaining_credits's filter.
        The breakdown deliberately restates that same filter, quirk
        included -- diverging would produce a mismatch that is an
        artifact of this console rather than a real billing fault, which
        is exactly the kind of false alarm that makes a diagnostic
        useless."""
        wallet = self._wallet()
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=1_000_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
            is_processed=True,
        )

        state = _describe_state(self.sub)
        row = _by_type(state)[CreditBucketType.MONTHLY]

        self.assertEqual(row["live_remaining_credits"], 1_000_000)
        self.assertEqual(
            row["live_remaining_credits"], state["wallet"]["total_remaining_credits"]
        )

    # -- the load-bearing one ----------------------------------------------

    def test_live_subtotals_reconcile_with_the_wallet_total(self):
        """The breakdown is computed by a different query than
        CreditWallet.total_remaining_credits(). If they ever disagree,
        the console is attributing a total to types that do not account
        for it -- so this asserts agreement across a deliberately messy
        wallet holding every bucket type at once, live, expired,
        never-expiring, partly used and fully drained."""
        wallet = self._wallet()
        future = timezone.now() + timedelta(days=30)
        past = timezone.now() - timedelta(days=1)
        specs = [
            (CreditBucketType.MONTHLY, 10_000_000, 1_500_000, future),
            (CreditBucketType.MONTHLY, 10_000_000, 10_000_000, past),
            (CreditBucketType.TRIAL, 5_000_000, 0, future),
            (CreditBucketType.CARRY_OVER, 1_500_000, 250_000, future),
            (CreditBucketType.CARRY_OVER, 900_000, 900_000, past),
            (CreditBucketType.OVERAGE, 5_000_000, 4_000_000, None),
        ]
        for bucket_type, total, used, expires in specs:
            CreditBucket.objects.create(
                wallet=wallet,
                bucket_type=bucket_type,
                total_credits=total,
                used_credits=used,
                expires_at=expires,
            )

        state = _describe_state(self.sub)
        live_sum = sum(r["live_remaining_credits"] for r in state["wallet"]["by_type"])

        self.assertEqual(live_sum, state["wallet"]["total_remaining_credits"])
        # And the same reconciliation for the overage-excluding figure,
        # since the console shows both side by side.
        plan_sum = sum(
            r["live_remaining_credits"]
            for r in state["wallet"]["by_type"]
            if r["type"] != CreditBucketType.OVERAGE
        )
        self.assertEqual(plan_sum, state["wallet"]["plan_remaining_credits"])

    def test_the_breakdown_covers_buckets_the_recent_list_truncates(self):
        """`buckets` is capped at the 10 most recent, so it cannot be
        summed to explain a total. The by_type aggregate must span
        everything -- otherwise the reconciliation above would start
        failing for any user with a long history, for no billing
        reason at all."""
        wallet = self._wallet()
        for _ in range(14):
            CreditBucket.objects.create(
                wallet=wallet,
                bucket_type=CreditBucketType.MONTHLY,
                total_credits=1_000_000,
                used_credits=0,
                expires_at=timezone.now() + timedelta(days=30),
            )

        state = _describe_state(self.sub)

        self.assertEqual(len(state["wallet"]["buckets"]), 10)
        row = _by_type(state)[CreditBucketType.MONTHLY]
        self.assertEqual(row["bucket_count"], 14)
        self.assertEqual(row["live_remaining_credits"], 14_000_000)
        self.assertEqual(
            row["live_remaining_credits"], state["wallet"]["total_remaining_credits"]
        )

    def test_per_bucket_rows_are_json_safe(self):
        """_describe_state's output goes straight into a JsonResponse.
        is_expired is a plain METHOD on CreditBucket, not a property, so
        reading it without calling would put a bound method in this dict
        and blow up serialization at request time rather than here."""
        import json

        SubscriptionService.activate_free_trial(self.user, self.plan)
        state = _describe_state(self.sub)

        json.dumps(state)  # must not raise
        bucket = state["wallet"]["buckets"][0]
        self.assertIsInstance(bucket["is_expired"], bool)
        self.assertIsInstance(bucket["remaining_credits"], int)
