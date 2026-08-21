"""
billing/tests/test_trial_forfeiture_on_activation.py
======================================================
Locks in a real bug found via the QA console: a brand-new teacher gets
an automatic 14-day, 5,000-credit TRIAL bucket on signup
(SubscriptionService.activate_automatic_free_trial, fired from
users/signals.py). If that same user then converts to paid through
ANY path that doesn't explicitly know to call
finalize_trial_to_paid_conversion — including
StripeWebhookHandler._handle_individual_checkout's fallback branch,
reached whenever checkout completes without a trial_subscription_id in
its metadata — the trial bucket was never touched, so the user kept
its full balance stacked on top of their new paid plan's monthly grant
for however much of the 14 days remained.

The fix adds the same trial-forfeiture step activate_subscription's
sibling (finalize_trial_to_paid_conversion) already does, at the one
shared entry point every caller passes through, so the gap is closed
regardless of which caller reaches it.
"""

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
)
from billing.services import SubscriptionService
from billing.tests.tests_free_trial import make_individual_plan, make_teacher


class ActivateSubscriptionForfeitsLingeringTrialTests(TestCase):
    def setUp(self):
        self.user = make_teacher("newuser@example.com")
        self.plan = make_individual_plan()
        self.trial_sub = SubscriptionService.activate_free_trial(self.user, self.plan)
        self.wallet = CreditWallet.objects.get(user=self.user)
        self.trial_bucket = self.wallet.buckets.get(bucket_type=CreditBucketType.TRIAL)

    def test_lingering_trial_bucket_is_forfeited_on_activation(self):
        """The exact bug: activating a paid plan for a user who still has
        an un-expired trial must not leave that trial balance stacked on
        top of the new plan's grant."""
        SubscriptionService.activate_subscription(self.user, self.plan)

        self.trial_bucket.refresh_from_db()
        self.assertTrue(self.trial_bucket.is_processed)
        self.assertLessEqual(self.trial_bucket.expires_at, timezone.now())

    def test_forfeiture_logs_an_expire_ledger_entry_for_the_unused_amount(self):
        SubscriptionService.activate_subscription(self.user, self.plan)

        ledger = CreditLedger.objects.filter(
            user=self.user,
            bucket=self.trial_bucket,
            ledger_type=CreditLedgerType.EXPIRE,
        ).first()
        self.assertIsNotNone(ledger)
        self.assertEqual(ledger.amount, SubscriptionService.TRIAL_CREDITS_RAW)

    def test_total_remaining_credits_no_longer_double_counts_the_trial(self):
        """This is the number the QA console showed: 15,000,000 (10M
        monthly + 5M leftover trial) instead of the 10,000,000 a fresh
        paid activation should grant."""
        SubscriptionService.activate_subscription(self.user, self.plan)

        self.wallet.refresh_from_db()
        self.assertEqual(
            self.wallet.total_remaining_credits(), self.plan.monthly_credits
        )

    def test_partially_used_trial_only_forfeits_the_unused_remainder(self):
        """Mirrors finalize_trial_to_paid_conversion's own behaviour and
        expire_bucket's general contract: only the UNUSED portion is
        logged as forfeited, never the whole original grant."""
        consumed = 1_000_000
        self.trial_bucket.used_credits = consumed
        self.trial_bucket.save(update_fields=["used_credits"])

        SubscriptionService.activate_subscription(self.user, self.plan)

        ledger = CreditLedger.objects.filter(
            user=self.user,
            bucket=self.trial_bucket,
            ledger_type=CreditLedgerType.EXPIRE,
        ).first()
        self.assertEqual(
            ledger.amount, SubscriptionService.TRIAL_CREDITS_RAW - consumed
        )

    def test_fully_exhausted_trial_forfeits_with_no_ledger_noise(self):
        self.trial_bucket.used_credits = self.trial_bucket.total_credits
        self.trial_bucket.save(update_fields=["used_credits"])

        SubscriptionService.activate_subscription(self.user, self.plan)

        self.trial_bucket.refresh_from_db()
        self.assertTrue(self.trial_bucket.is_processed)
        self.assertFalse(
            CreditLedger.objects.filter(
                user=self.user,
                bucket=self.trial_bucket,
                ledger_type=CreditLedgerType.EXPIRE,
            ).exists()
        )

    def test_a_trial_bucket_already_processed_is_left_alone(self):
        """Idempotency: calling activate_subscription twice (or any
        caller that already forfeited the trial through a different
        path) must not double-log a forfeiture."""
        self.trial_bucket.is_processed = True
        self.trial_bucket.expires_at = timezone.now()
        self.trial_bucket.save(update_fields=["is_processed", "expires_at"])

        SubscriptionService.activate_subscription(self.user, self.plan)

        self.assertFalse(
            CreditLedger.objects.filter(
                user=self.user,
                bucket=self.trial_bucket,
                ledger_type=CreditLedgerType.EXPIRE,
            ).exists()
        )

    def test_user_with_no_trial_at_all_is_unaffected(self):
        """The common case (no lingering trial to worry about) must
        behave exactly as before -- no new bucket, no crash, no
        spurious ledger entries referencing a trial that never existed."""
        other = make_teacher("no-trial@example.com")
        SubscriptionService.activate_subscription(other, self.plan)

        wallet = CreditWallet.objects.get(user=other)
        self.assertFalse(
            wallet.buckets.filter(bucket_type=CreditBucketType.TRIAL).exists()
        )
        self.assertEqual(wallet.total_remaining_credits(), self.plan.monthly_credits)

    def test_only_the_current_users_trial_bucket_is_touched(self):
        """Sanity check on the select_for_update().filter(...) scoping:
        a DIFFERENT user's still-active trial must survive untouched."""
        other = make_teacher("other-trial@example.com")
        SubscriptionService.activate_free_trial(other, self.plan)
        other_bucket = CreditBucket.objects.get(
            wallet__user=other, bucket_type=CreditBucketType.TRIAL
        )

        SubscriptionService.activate_subscription(self.user, self.plan)

        other_bucket.refresh_from_db()
        self.assertFalse(other_bucket.is_processed)
