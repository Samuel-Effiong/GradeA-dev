"""FR-A-01 for credit transactions: exactly one well-formed AuditEvent per
CreditLedger row actually written - whether it went through the ~18
`CreditLedger.record()` call sites in billing/services.py, or through one of
the two paths that bypass `record()` entirely via `.build()` + `bulk_create()`
(`CreditWallet.consume_credits()` and `SubscriptionService.refund_credits()`'s
batch path). See docs/phase2/architecture/04_epic_a_implementation_plan.md
§0.6 for why both paths need their own instrumentation: `bulk_create()` never
emits `post_save`, so a hook placed only inside `record()` would silently
produce zero events for `CreditLedgerType.CONSUME` - the only ledger type
`consume_credits()` ever writes, and very likely the highest-volume one.

The paired `CreditUsageLog` row `consume_credits()` writes alongside each
`CreditLedger` row is deliberately NOT a second event: it is the same
economic event as its ledger row, not a distinct transaction.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from audit.enums import ActorRole, AuditAction, AuditOutcome
from audit.models import AuditEvent
from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditUsageLog,
    CreditWallet,
)
from billing.services import SubscriptionService
from users.models import CustomUser, UserTypes


def make_user(email, **overrides):
    defaults = {
        "email": email,
        "password": "testpass123",  # pragma: allowlist secret
        "user_type": UserTypes.TEACHER,
    }
    defaults.update(overrides)
    return CustomUser.objects.create_user(**defaults)


def credit_events():
    return AuditEvent.objects.filter(action=AuditAction.CREDIT_TRANSACTION)


class RecordEmitsCreditTransactionTests(TestCase):
    """The ~18 billing/services.py call sites all go through this one
    classmethod - covering it once covers all of them."""

    def setUp(self):
        self.user = make_user("ledger-record@example.com")
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        self.bucket = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MANUAL_GRANT,
            total_credits=500,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )

    def test_record_emits_exactly_one_event(self):
        ledger = CreditLedger.record(
            user=self.user,
            bucket=self.bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=500,
            reference="Manual grant",
        )

        events = credit_events()
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertIsNone(event.error_class)
        self.assertEqual(event.actor_id, self.user.id)
        self.assertEqual(event.actor_role, ActorRole.TEACHER)
        self.assertEqual(event.target_type, "CreditLedger")
        self.assertEqual(event.target_id, ledger.id)
        self.assertEqual(event.metadata, {"ledger_type": "GRANT", "credits": 500})


class ConsumeCreditsEmitsCreditTransactionTests(TestCase):
    """The bulk_create-only path `record()` never sees."""

    def setUp(self):
        self.user = make_user("consume@example.com")
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        self.bucket = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )

    def test_consume_emits_exactly_one_event(self):
        self.wallet.consume_credits(30, feature="extraction", task_type="grade")

        events = credit_events()
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertEqual(event.actor_id, self.user.id)
        self.assertEqual(event.actor_role, ActorRole.TEACHER)
        self.assertEqual(event.target_type, "CreditLedger")
        ledger_row = CreditLedger.objects.get(
            user_id=self.user.id, ledger_type=CreditLedgerType.CONSUME
        )
        self.assertEqual(event.target_id, ledger_row.id)
        self.assertEqual(event.metadata, {"ledger_type": "CONSUME", "credits": -30})

    def test_consume_spanning_two_buckets_emits_one_event_per_ledger_row(self):
        # A second, later-expiring bucket so the deficient first bucket's
        # remainder is drawn from it - two CreditLedger rows in one
        # bulk_create call, per CreditWallet.consume_credits' bucket loop.
        second_bucket = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.CARRY_OVER,
            total_credits=100,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=60),
        )
        # CARRY_OVER is drained before MONTHLY (see
        # test_credit_consumption_order.py), so exhaust it first.
        self.wallet.consume_credits(90, feature="extraction", task_type="grade")
        # AuditEvent is itself append-only, so isolate the second call's
        # events by ID rather than deleting the first call's.
        seen_ids = set(credit_events().values_list("id", flat=True))

        # This draws the remaining 10 from CARRY_OVER and 20 from MONTHLY -
        # two ledger rows in a single bulk_create call.
        self.wallet.consume_credits(30, feature="extraction", task_type="grade")

        events = credit_events().exclude(id__in=seen_ids)
        self.assertEqual(events.count(), 2)
        self.assertEqual(sorted(e.metadata["credits"] for e in events), [-20, -10])
        for event in events:
            self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
            self.assertEqual(event.actor_id, self.user.id)

        # The paired CreditUsageLog rows are not separately audited.
        self.assertEqual(CreditUsageLog.objects.filter(wallet=self.wallet).count(), 3)
        second_bucket.refresh_from_db()


class BatchRefundEmitsCreditTransactionTests(TestCase):
    """SubscriptionService.refund_credits' batch path - also bulk_create-only."""

    def setUp(self):
        self.user = make_user("refund@example.com")
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        self.bucket = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100,
            used_credits=40,
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.log = CreditUsageLog.objects.create(
            wallet=self.wallet,
            bucket=self.bucket,
            amount=40,
            feature="extraction",
            task_type="grade",
            task_id="task-123",
            is_refunded=False,
        )

    def test_refund_emits_exactly_one_event(self):
        refunded = SubscriptionService.refund_credits("task-123", reason="failed task")
        self.assertEqual(refunded, 40)

        events = credit_events()
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertEqual(event.actor_id, self.user.id)
        self.assertEqual(event.target_type, "CreditLedger")
        self.assertEqual(event.metadata, {"ledger_type": "REFUND", "credits": 40})

    def test_a_zero_amount_refund_emits_no_event(self):
        # amount is clamped to min(log.amount, bucket.used_credits); an
        # externally-drained bucket (used_credits already 0) means the
        # clamp is 0, and refund_credits' own comment says a zero-amount
        # clamp "is just audit noise" - no ledger row is built for it, so
        # no CREDIT_TRANSACTION event should exist either.
        self.bucket.used_credits = 0
        self.bucket.save(update_fields=["used_credits"])

        refunded = SubscriptionService.refund_credits("task-123", reason="failed task")
        self.assertEqual(refunded, 0)
        self.assertEqual(credit_events().count(), 0)
