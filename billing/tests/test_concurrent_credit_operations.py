"""
billing/tests/test_concurrent_credit_operations.py
==================================================
REAL concurrency against Postgres — threads, not mocks.

WHY THIS FILE EXISTS
--------------------
Every other concurrency claim in this suite is structural: the code calls
`select_for_update()`, therefore it must be safe. That reasoning is how
double-spends ship. These tests run genuinely parallel threads against the
real database and assert on the arithmetic afterwards.

`TransactionTestCase` is required — the standard `TestCase` wraps each test
in a transaction that the worker threads could not see, so row locks would
never actually contend.

WHAT IS PINNED
--------------
  * N threads consuming concurrently deduct EXACTLY N times — no lost
    update, which is the classic read-modify-write race on `used_credits`;
  * a wallet cannot be overdrawn by concurrent consumers racing past the
    balance check (the one that turns into free AI usage);
  * the append-only ledger records exactly one row per successful
    consumption, so the audit trail and the balance agree;
  * concurrent refunds of the SAME usage log refund once, not twice.

Threads each close their own DB connection: Django connections are not
thread-safe and leaking them wedges the test DB for later tests.
"""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TransactionTestCase
from django.utils import timezone

from AutoGrader.testing.concurrency import run_concurrently
from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditUsageLog,
    CreditWallet,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
    UserSubscription,
)
from users.models import UserTypes

CustomUser = get_user_model()


def run_in_threads(fn, count, *, test):
    """
    Fires `count` threads that all wait on one barrier, so they hit the
    contended row at the same moment rather than in sequence.

    Returns (results, errors): `results` holds the return value of every
    worker that SUCCEEDED, so len(results) is the success count. The
    waiting, the liveness check and the connection hygiene live in
    AutoGrader.testing.concurrency; a worker still running at the deadline
    fails the test instead of being counted as a lost update.
    """
    outcomes, errors = run_concurrently(
        lambda i: (fn(i),), count, test=test, name="credit-worker"
    )
    # A failed worker leaves None; a successful one a 1-tuple, so a worker
    # that legitimately returns None still counts as a success.
    return [outcome[0] for outcome in outcomes if outcome is not None], errors


class ConcurrentConsumptionTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Concurrency",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            interval=BillingInterval.MONTHLY,
            monthly_credits=10_000,
            overage_block_size=500,
            overage_block_price=10,
            max_overage_blocks=10,
            is_active=True,
        )
        self.user = CustomUser.objects.create_user(
            email="concurrent@billing.test",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        now = timezone.now()
        UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - timedelta(days=1),
            billing_cycle_end=now + timedelta(days=29),
            next_credit_grant_at=now + timedelta(days=29),
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        CreditBucket.objects.filter(wallet=self.wallet).delete()

    def _bucket(self, total):
        return CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=total,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=25),
        )

    def _consume(self, amount):
        def fn(i):
            wallet = CreditWallet.objects.get(pk=self.wallet.pk)
            wallet.consume_credits(
                amount,
                feature="Grading Assignment",
                task_type="GRADING",
                task_id=str(uuid.uuid4()),
            )
            return True

        return fn

    def test_concurrent_consumption_does_not_lose_updates(self):
        """
        The classic read-modify-write race. 10 threads each spend 100 from a
        10,000 bucket; exactly 1,000 must be gone. A lost update leaves the
        user with credits they already spent — free AI usage, paid by us.
        """
        bucket = self._bucket(10_000)
        threads, amount = 10, 100

        results, errors = run_in_threads(self._consume(amount), threads, test=self)

        self.assertEqual(errors, [], f"threads raised: {errors!r}")
        self.assertEqual(len(results), threads)

        bucket.refresh_from_db()
        self.assertEqual(
            bucket.used_credits,
            threads * amount,
            "LOST UPDATE: concurrent consumption did not deduct every charge",
        )

    def test_the_ledger_agrees_with_the_balance_after_contention(self):
        """
        Balance and audit trail must not diverge under load: exactly one
        CONSUME row per successful deduction, summing to the same total.
        """
        bucket = self._bucket(10_000)
        threads, amount = 10, 100

        run_in_threads(self._consume(amount), threads, test=self)

        bucket.refresh_from_db()
        rows = CreditLedger.objects.filter(bucket=bucket)
        self.assertEqual(rows.count(), threads, "ledger row count != deductions")
        self.assertEqual(
            -sum(r.amount for r in rows),
            bucket.used_credits,
            "the ledger and the bucket balance disagree after contention",
        )
        self.assertEqual(
            CreditUsageLog.objects.filter(wallet=self.wallet).count(), threads
        )

    def test_a_wallet_cannot_be_overdrawn_by_racing_consumers(self):
        """
        Only 500 credits exist and 10 threads each want 100. At most five
        may succeed. If more do, the balance check was read outside the
        lock and the customer got AI work they never paid for.
        """
        bucket = self._bucket(500)
        threads, amount = 10, 100

        results, errors = run_in_threads(self._consume(amount), threads, test=self)

        bucket.refresh_from_db()
        self.assertLessEqual(
            bucket.used_credits,
            500,
            f"OVERDRAWN: used={bucket.used_credits} of 500 "
            f"({len(results)} succeeded, {len(errors)} refused)",
        )
        self.assertEqual(
            len(results) * amount,
            bucket.used_credits,
            "successful consumptions do not match the deducted total",
        )
        self.assertGreater(len(errors), 0, "expected some threads to be refused")

    def test_contention_across_two_buckets_still_balances(self):
        """
        Consumption walks buckets in priority order. With two buckets and
        enough demand to exhaust the first, the totals must still add up.
        """
        b1 = self._bucket(300)
        b2 = self._bucket(700)
        threads, amount = 10, 100

        results, _ = run_in_threads(self._consume(amount), threads, test=self)

        b1.refresh_from_db()
        b2.refresh_from_db()
        self.assertEqual(
            b1.used_credits + b2.used_credits,
            len(results) * amount,
            "credits deducted across buckets do not match what was consumed",
        )
        self.assertLessEqual(b1.used_credits, 300)
        self.assertLessEqual(b2.used_credits, 700)


class ConcurrentRefundTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Refund Concurrency",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            interval=BillingInterval.MONTHLY,
            monthly_credits=10_000,
            overage_block_size=500,
            is_active=True,
        )
        self.user = CustomUser.objects.create_user(
            email="concurrent.refund@billing.test",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        now = timezone.now()
        UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - timedelta(days=1),
            billing_cycle_end=now + timedelta(days=29),
            next_credit_grant_at=now + timedelta(days=29),
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        CreditBucket.objects.filter(wallet=self.wallet).delete()
        self.bucket = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=10_000,
            used_credits=0,
            expires_at=now + timedelta(days=25),
        )
        self.task_id = str(uuid.uuid4())
        self.wallet.consume_credits(
            500,
            feature="Grading Assignment",
            task_type="GRADING",
            task_id=self.task_id,
        )
        self.bucket.refresh_from_db()
        self.assertEqual(self.bucket.used_credits, 500)

    def test_racing_refunds_of_the_same_charge_refund_once(self):
        """
        Two workers refunding the same task must not hand back double. The
        `is_refunded` flag is the one declared-mutable field on the
        append-only usage log, and it is what makes the refund idempotent.
        """
        from billing.services import SubscriptionService

        def fn(i):
            with transaction.atomic():
                return SubscriptionService.refund_credits(task_id=self.task_id)

        results, errors = run_in_threads(fn, 4, test=self)

        self.bucket.refresh_from_db()
        self.assertGreaterEqual(
            self.bucket.used_credits,
            0,
            "a double refund drove used_credits negative — credits were minted",
        )
        self.assertEqual(
            self.bucket.used_credits,
            0,
            f"expected exactly one refund of 500; used_credits="
            f"{self.bucket.used_credits} (results={results!r}, errors={errors!r})",
        )
        self.assertEqual(
            CreditUsageLog.objects.filter(
                task_id=self.task_id, is_refunded=True
            ).count(),
            1,
        )


class ConcurrentImmutabilityTests(TransactionTestCase):
    """The append-only guard must hold when several threads push at once."""

    reset_sequences = True

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="concurrent.immutable@billing.test",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        self.bucket = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=1_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=25),
        )
        self.row = CreditLedger.objects.create(
            id=uuid.uuid4(),
            user_id=self.user.id,
            user_email=self.user.email,
            bucket=self.bucket,
            ledger_type="GRANT",
            amount=1_000,
            reference="concurrency subject",
        )

    def test_concurrent_mutation_attempts_are_all_rejected(self):
        def fn(i):
            with transaction.atomic():
                CreditLedger.objects.filter(pk=self.row.pk).update(amount=i)
            return "MUTATED"

        results, errors = run_in_threads(fn, 8, test=self)

        self.assertEqual(
            results, [], "the append-only guard let a concurrent write through"
        )
        self.assertEqual(len(errors), 8)
        self.row.refresh_from_db()
        self.assertEqual(self.row.amount, 1_000)

    def test_concurrent_inserts_all_succeed(self):
        """The guard must not turn into a write lock on legitimate inserts."""

        def fn(i):
            CreditLedger.objects.create(
                id=uuid.uuid4(),
                user_id=self.user.id,
                user_email=self.user.email,
                bucket=self.bucket,
                ledger_type="CONSUME",
                amount=-1,
                reference=f"concurrent insert {i}",
            )
            return True

        results, errors = run_in_threads(fn, 8, test=self)

        self.assertEqual(errors, [], f"inserts were blocked: {errors!r}")
        self.assertEqual(len(results), 8)
        self.assertEqual(
            CreditLedger.objects.filter(
                reference__startswith="concurrent insert"
            ).count(),
            8,
        )
