"""
H-181: the six functions that lock a wallet's buckets and then write the
wallet take the wallet row lock FIRST, the order `consume_credits` uses.

WHAT WAS WRONG (found by reading, 2026-10-07; not run until these tests)
------------------------------------------------------------------------
`CreditWallet.consume_credits` locks the wallet row FOR UPDATE and then the
wallet's consumable buckets. These six `SubscriptionService` functions lock
buckets first and only afterwards update the wallet row (`wallet.save`) or
insert a bucket (whose foreign-key check needs a share lock on the wallet
row, which a FOR UPDATE conflicts with):

    activate_subscription, apply_immediate_plan_change,
    process_mid_cycle_credit_grant, process_rollover_and_renewal,
    finalize_trial_conversion_via_stripe, finalize_trial_to_paid_conversion

A charge racing one of them, for the same user, can therefore deadlock:
Postgres aborts one. If the charge is aborted a finished AI answer is lost.
The refund side was fixed the same way before (billing/credit_reversal.py:
"WALLETS FIRST, THEN BUCKETS").

HOW THE TESTS FORCE THE RACE
----------------------------
Real threads against Postgres (TransactionTestCase: a plain TestCase wraps
the test in one transaction the workers cannot see, and no row lock would
contend). Worker 0 runs the function under test inside a transaction. A hook
on `CreditLedger.record`, which each of the six calls after its first bucket
lock, makes worker 0 stop there: it tells worker 1 "I hold the bucket" and
waits until worker 1 has started its charge and had a moment to take the
wallet lock. Then worker 0 goes on to write the wallet.

  * Old order: worker 1 holds the wallet and waits for the bucket; worker 0
    holds the bucket and waits for the wallet. Postgres aborts one of them
    (about one second): an error in `errors`, the test fails.
  * New order: worker 0 already holds the wallet; worker 1 waits for it,
    worker 0 finishes, worker 1 charges (or is refused cleanly because the
    function retired the bucket). No error.

Each worker's transaction has a `lock_timeout`, so a wait that never ends
fails the test instead of hanging it. Every test asserts that the hook was
reached (a fixture that never locks a bucket would otherwise pass without
testing anything) and that worker 1 really started its charge.

LIMITS, STATED
--------------
  * The wait at commit for the foreign-key share lock is Postgres behaviour
    these tests show or do not show; nothing here is read from a real service.
  * Nothing here touches a licence row (that is H-182).

THE LICENCE SIDE (same row, ruled by the Senior Manager 2026-10-08)
-------------------------------------------------------------------
`LicenseSubscriptionService._rollover_and_grant_monthly_bucket` locks the
teacher's MONTHLY bucket and then inserts buckets; its three callers
(`process_license_renewal`, `process_offline_renewal`,
`_refresh_teacher_credits`) write the wallet afterwards or at commit. Each
has a test below. `_enroll_teacher_internal` is NOT among them: it reads the
teacher's monthly bucket without a lock, so it holds no bucket lock for a
charge to wait on (read; stated in the evidence). The licence functions
catch a failure per teacher, so a deadlock victim there is usually the
CHARGE (the one that waited first); the tests also assert the renewal's own
result.

Run with:
    python manage.py test billing.tests.test_wallet_lock_first
"""

import threading
import time
import uuid
from datetime import timedelta
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from AutoGrader.testing.concurrency import run_concurrently
from billing.immutable import allow_unsafe_mutation
from billing.license_service import LicenseSubscriptionService
from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditWallet,
    InsufficientCreditsError,
    LicenseBillingMethod,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
    UserSubscription,
)
from billing.services import SubscriptionService
from billing.tests.tests_free_trial import make_individual_plan
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()

#: Seconds worker 0 waits, at the hook, after worker 1 has started: time for
#: worker 1 to take the wallet lock and block on the bucket worker 0 holds.
PAUSE = 0.8

#: Per-transaction bound on any single lock wait (eight seconds, in the
#: statement below), so a stuck wait FAILS instead of hanging the test.
SET_LOCK_TIMEOUT = "SET LOCAL lock_timeout = '8s'"

#: How long a worker waits for the other to reach its mark.
MARK_TIMEOUT = 10

CHARGE = 1_000

# The functions send mail-list syncs and queue tasks; with
# TransactionTestCase the after-commit callbacks really run, so the queueing
# is stubbed once for the module, from the main thread (a patch entered in a
# worker thread is not thread-safe).
_queueing = [
    patch("billing.services.queue_sync", return_value=None),
    patch("billing.services.safe_delay", return_value=None),
    patch("billing.license_service.queue_sync", return_value=None),
    patch("billing.license_service.safe_delay", return_value=None),
]


def setUpModule():
    for stub in _queueing:
        stub.start()


def tearDownModule():
    for stub in _queueing:
        stub.stop()


def make_annual_plan(name="PRO_ANNUAL", price_id="price_lockfirst_annual"):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=name,
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.PRO,
        interval=BillingInterval.ANNUAL,
        price_cents=4_999,
        monthly_credits=12_000_000,
        stripe_price_id=price_id,
        carry_over_percent=50,
        carry_over_expiry_months=1,
        is_active=True,
    )


class RaceTestCase(TransactionTestCase):
    """The harness: a user with a wallet, and `race()`, which runs a function
    in worker 0 and a charge in worker 1, forced to meet."""

    reset_sequences = True

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="lockfirst@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        # Registration signals may auto-activate a trial. Start from the
        # state the test chose, not one a signal chose.
        UserSubscription.objects.filter(user=self.user).delete()
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        self.wallet.buckets.all().delete()
        with allow_unsafe_mutation():
            CreditLedger.objects.filter(user_id=self.user.id).delete()

    # -- fixtures --------------------------------------------------------

    def charge_target_exists(self):
        """Something `consume_credits` can draw on, so the charge reaches
        the bucket lock the function under test holds."""
        return CreditBucket.objects.filter(
            wallet=self.wallet,
            bucket_type__in=[CreditBucketType.TRIAL, CreditBucketType.MONTHLY],
        ).exists()

    # -- the race ---------------------------------------------------------

    def race(self, under_test):
        """Run `under_test()` in worker 0 and a charge in worker 1, forced
        to meet. Returns `(results, errors, hook_calls)`."""
        self.assertTrue(
            self.charge_target_exists(), "fixture: nothing for the charge to draw on"
        )
        b_holds_bucket = threading.Event()
        a_started = threading.Event()
        hook_calls: list[str] = []
        real_record = CreditLedger.record

        def paused_record(*args, **kwargs):
            # Worker 0 only, and only the first call: the first ledger row
            # is written after the function's first bucket lock.
            if threading.current_thread().name.endswith("-0") and not hook_calls:
                hook_calls.append("paused")
                b_holds_bucket.set()
                if not a_started.wait(timeout=MARK_TIMEOUT):
                    raise AssertionError("the charge never started")
                time.sleep(PAUSE)
            return real_record(*args, **kwargs)

        def work(i):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(SET_LOCK_TIMEOUT)
                if i == 0:
                    return under_test()
                if not b_holds_bucket.wait(timeout=MARK_TIMEOUT):
                    raise AssertionError(
                        "the function never reached the ledger hook: it did "
                        "not lock a bucket and write a ledger row"
                    )
                a_started.set()
                try:
                    self.wallet.consume_credits(
                        CHARGE, feature="grading", task_id=str(uuid.uuid4())
                    )
                except InsufficientCreditsError:
                    return "refused"
                return "charged"

        with patch.object(CreditLedger, "record", paused_record):
            results, errors = run_concurrently(
                work, 2, test=self, name="lockfirst", join_timeout=90
            )
        self.assertTrue(a_started.is_set(), "worker 1 never started its charge")
        return results, errors, hook_calls

    def assertNoDeadlock(self, errors, hook_calls):
        self.assertEqual(hook_calls, ["paused"], "the hook was not reached once")
        self.assertEqual(
            errors,
            [],
            "the charge and the function deadlocked (or one waited out the "
            "lock timeout)",
        )


class WalletLockFirstTests(RaceTestCase):
    """One real-thread test per function: a charge racing the function must
    not deadlock."""

    def give_trial_bucket(self):
        """A live, unprocessed TRIAL bucket with credits left: the bucket
        `activate_subscription` forfeits (and so locks) on a paid plan."""
        return CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.TRIAL,
            total_credits=5_000_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=7),
        )

    def test_activate_subscription(self):
        self.give_trial_bucket()
        plan = make_individual_plan()

        results, errors, hook_calls = self.race(
            lambda: SubscriptionService.activate_subscription(self.user, plan)
        )

        self.assertNoDeadlock(errors, hook_calls)
        self.assertIsNotNone(results[0], "the function returned nothing")
        self.assertTrue(
            CreditBucket.objects.filter(
                wallet=self.wallet, bucket_type=CreditBucketType.MONTHLY
            ).exists(),
            "the new plan's monthly bucket was not granted",
        )

    def test_apply_immediate_plan_change(self):
        plan = make_individual_plan()
        sub = SubscriptionService.activate_subscription(self.user, plan)
        bigger = make_individual_plan(
            name=PlanType.PRO, display_name="Pro Grader", monthly_credits=20_000_000
        )
        bigger.tier = PlanTier.PRO
        bigger.save(update_fields=["tier"])

        results, errors, hook_calls = self.race(
            lambda: SubscriptionService.apply_immediate_plan_change(sub, bigger)
        )

        self.assertNoDeadlock(errors, hook_calls)
        self.assertIsNotNone(results[0], "the function returned nothing")
        self.assertEqual(
            CreditBucket.objects.filter(
                wallet=self.wallet, bucket_type=CreditBucketType.MONTHLY
            ).count(),
            2,
            "the new plan's monthly bucket was not granted",
        )

    def test_process_mid_cycle_credit_grant(self):
        plan = make_annual_plan()
        t0 = timezone.now().replace(microsecond=0) - relativedelta(months=1)
        sub = UserSubscription.objects.create(
            user=self.user,
            plan=plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=t0,
            billing_cycle_end=t0 + relativedelta(years=1),
            next_credit_grant_at=t0 + relativedelta(months=1) - timedelta(minutes=1),
        )
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=12_000_000,
            used_credits=3_000_000,
            expires_at=timezone.now() + timedelta(days=1),
        )

        results, errors, hook_calls = self.race(
            lambda: SubscriptionService.process_mid_cycle_credit_grant(sub)
        )

        self.assertNoDeadlock(errors, hook_calls)
        self.assertIsNotNone(results[0], "the grant did not happen")

    def test_process_rollover_and_renewal(self):
        plan = make_individual_plan()
        sub = SubscriptionService.activate_subscription(self.user, plan)

        results, errors, hook_calls = self.race(
            lambda: SubscriptionService.process_rollover_and_renewal(sub)
        )

        self.assertNoDeadlock(errors, hook_calls)
        self.assertIsNotNone(results[0], "the renewal returned nothing")

    def test_finalize_trial_conversion_via_stripe(self):
        annual = make_annual_plan()
        trial = SubscriptionService.activate_free_trial(self.user, annual)
        start = timezone.now()

        results, errors, hook_calls = self.race(
            lambda: SubscriptionService.finalize_trial_conversion_via_stripe(
                trial, period_start=start, period_end=start + relativedelta(years=1)
            )
        )

        self.assertNoDeadlock(errors, hook_calls)
        self.assertIsNotNone(results[0], "the conversion returned nothing")

    def test_finalize_trial_to_paid_conversion(self):
        annual = make_annual_plan()
        paid = make_annual_plan(name="PRO_ANNUAL_PAID", price_id="price_lockfirst_paid")
        trial = SubscriptionService.activate_free_trial(self.user, annual)

        results, errors, hook_calls = self.race(
            lambda: SubscriptionService.finalize_trial_to_paid_conversion(
                trial, paid, "sub_lockfirst"
            )
        )

        self.assertNoDeadlock(errors, hook_calls)
        self.assertIsNotNone(results[0], "the conversion returned nothing")


class LicenceWalletLockFirstTests(RaceTestCase):
    """The licence functions that go through the shared rollover helper."""

    def setUp(self):
        super().setUp()
        self.school = School.objects.create(name="Lock First High")
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.POWER_LICENSE,
            display_name="Power License",
            category=PlanCategory.LICENSE,
            tier=PlanTier.POWER,
            interval=BillingInterval.MONTHLY,
            price_cents=19_900,
            monthly_credits=12_000_000,
            carry_over_percent=50,
            carry_over_expiry_months=6,
            is_active=True,
        )
        self.admin = CustomUser.objects.create_user(
            email="lockfirst-admin@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
        )
        self.user.school = self.school
        self.user.save(update_fields=["school"])

    def make_licence(self, *, cycle_ended, method=LicenseBillingMethod.STRIPE):
        now = timezone.now()
        start = now - relativedelta(months=12) if cycle_ended else now
        end = now - timedelta(days=1) if cycle_ended else now + relativedelta(months=12)
        licence = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            contract_months=12,
            max_seats=2,
            billing_cycle_start=start,
            billing_cycle_end=end,
            billing_method=method,
            is_active=True,
            auto_renew=True,
        )
        allocation = SchoolCreditAllocation.objects.create(
            license_subscription=licence,
            user=self.user,
            monthly_allocation=12_000_000,
            is_active=True,
            next_credit_grant_at=now - timedelta(minutes=1),
        )
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=12_000_000,
            used_credits=3_000_000,
            expires_at=now + timedelta(days=1),
        )
        return licence, allocation

    def new_monthly_buckets(self):
        return CreditBucket.objects.filter(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            is_processed=False,
        )

    def test_process_license_renewal(self):
        licence, _ = self.make_licence(cycle_ended=True)

        results, errors, hook_calls = self.race(
            lambda: LicenseSubscriptionService.process_license_renewal(licence)
        )

        self.assertNoDeadlock(errors, hook_calls)
        self.assertEqual(
            self.new_monthly_buckets().count(), 1, "the teacher was not renewed"
        )

    def test_process_offline_renewal(self):
        licence, _ = self.make_licence(
            cycle_ended=True, method=LicenseBillingMethod.OFFLINE
        )

        results, errors, hook_calls = self.race(
            lambda: LicenseSubscriptionService.process_offline_renewal(
                licence,
                performed_by=self.admin,
                new_billing_cycle_end=timezone.now() + relativedelta(months=12),
            )
        )

        self.assertNoDeadlock(errors, hook_calls)
        self.assertEqual(
            self.new_monthly_buckets().count(), 1, "the teacher was not renewed"
        )

    def test_refresh_teacher_credits(self):
        _, allocation = self.make_licence(cycle_ended=False)

        results, errors, hook_calls = self.race(
            lambda: LicenseSubscriptionService._refresh_teacher_credits(allocation)
        )

        self.assertNoDeadlock(errors, hook_calls)
        self.assertEqual(
            self.new_monthly_buckets().count(), 1, "the teacher was not refreshed"
        )
