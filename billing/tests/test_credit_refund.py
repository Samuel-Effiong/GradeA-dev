"""
Coverage for SubscriptionService.refund_credits.

Before this change refund_credits existed but had zero callers anywhere in
the codebase, and had several defects that would have surfaced the moment
something finally called it: no is_refunded filter (so calling it twice
double-refunded), no row locking (a race with a concurrent refund or Celery
redelivery could double-refund too), and a bare F("used_credits") - amount
decrement with no floor, which can push a PositiveIntegerField below zero
and raise IntegrityError.

It is now called from billing/refunds.py::billing_refund_scope whenever a
multi-call AI pipeline (e.g. grading — see
ai_processor/services.py::grade_student_submission) fails partway through,
to reverse every charge the failed run made.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from billing.models import (
    BetaProfile,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditUsageLog,
    CreditWallet,
)
from billing.services import AnalyticsService, SubscriptionService
from users.models import CustomUser, UserTypes


class RefundCreditsTestCase(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="refund-teacher@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        now = timezone.now()

        # Sized so a single consume_credits call spans both buckets:
        # CARRY_OVER drains first (highest priority), then MONTHLY.
        self.carry_over = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.CARRY_OVER,
            total_credits=100,
            used_credits=0,
            expires_at=now + timedelta(days=30),
        )
        self.monthly = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=1_000,
            used_credits=0,
            expires_at=now + timedelta(days=30),
        )

    def _consume(self, amount, task_id):
        return self.wallet.consume_credits(
            amount=amount,
            feature="Grading Assignment",
            task_type="grade_assignment",
            task_id=task_id,
        )

    def test_refund_restores_exact_per_bucket_amounts_across_multiple_buckets(self):
        # 100 drains CARRY_OVER entirely, the remaining 50 spills into MONTHLY.
        self._consume(150, task_id="task-1")
        self.carry_over.refresh_from_db()
        self.monthly.refresh_from_db()
        self.assertEqual(self.carry_over.used_credits, 100)
        self.assertEqual(self.monthly.used_credits, 50)

        refunded = SubscriptionService.refund_credits("task-1", reason="test failure")

        self.assertEqual(refunded, 150)
        self.carry_over.refresh_from_db()
        self.monthly.refresh_from_db()
        self.assertEqual(self.carry_over.used_credits, 0)
        self.assertEqual(self.monthly.used_credits, 0)

        # One REFUND ledger row per bucket drawn from.
        refund_rows = CreditLedger.objects.filter(
            ledger_type=CreditLedgerType.REFUND
        ).order_by("amount")
        self.assertEqual(refund_rows.count(), 2)
        self.assertEqual(sorted(row.amount for row in refund_rows), [50, 100])
        for row in refund_rows:
            self.assertGreater(row.amount, 0)
            self.assertEqual(row.metadata["original_task_id"], "task-1")

        self.assertFalse(
            CreditUsageLog.objects.filter(task_id="task-1", is_refunded=False).exists()
        )

    def test_refund_returns_zero_for_unknown_task_id(self):
        self.assertEqual(SubscriptionService.refund_credits("no-such-task"), 0)

    def test_refund_is_idempotent(self):
        self._consume(30, task_id="task-2")

        first = SubscriptionService.refund_credits("task-2")
        self.assertEqual(first, 30)
        ledger_count_after_first = CreditLedger.objects.filter(
            ledger_type=CreditLedgerType.REFUND
        ).count()

        second = SubscriptionService.refund_credits("task-2")
        self.assertEqual(second, 0)

        self.carry_over.refresh_from_db()
        self.assertEqual(self.carry_over.used_credits, 0)
        self.assertEqual(
            CreditLedger.objects.filter(ledger_type=CreditLedgerType.REFUND).count(),
            ledger_count_after_first,
        )

    def test_refund_clamps_instead_of_going_negative(self):
        self._consume(500, task_id="task-3")
        # Simulate the bucket having been reset out-of-band (e.g. a plan
        # change) to less than what this task originally consumed from it.
        self.carry_over.refresh_from_db()
        self.carry_over.used_credits = 20
        self.carry_over.save(update_fields=["used_credits"])

        refunded = SubscriptionService.refund_credits("task-3")

        self.carry_over.refresh_from_db()
        self.assertEqual(self.carry_over.used_credits, 0)
        # Refunded only what was actually still marked used (clamped), not
        # the full originally-logged amount — and never raised IntegrityError.
        self.assertEqual(refunded, 20 + 400)  # carry_over clamp + monthly's share

    def test_refund_reverses_beta_analytics(self):
        # consume_credits itself doesn't touch BetaProfile — in production
        # that happens via the separate AnalyticsService.record_consumption
        # call inside execute_graded_task, in the same transaction as the
        # consume_credits call. Reproduce both here.
        self._consume(75, task_id="task-4")
        AnalyticsService.record_consumption(
            user=self.teacher, amount=75, feature="Grading Assignment"
        )
        profile = BetaProfile.objects.get(user=self.teacher)
        self.assertEqual(profile.total_credits_used, 75)
        self.assertEqual(profile.credits_used_grading, 75)

        SubscriptionService.refund_credits("task-4")

        profile.refresh_from_db()
        self.assertEqual(profile.total_credits_used, 0)
        self.assertEqual(profile.credits_used_grading, 0)

    def test_refund_with_no_task_id_is_a_noop(self):
        self.assertEqual(SubscriptionService.refund_credits(None), 0)
        self.assertEqual(SubscriptionService.refund_credits(""), 0)
