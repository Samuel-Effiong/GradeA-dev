"""
H-182 (LOCK-2): the licence's consumption rollup is applied AFTER the charge
commits, not while the wallet and bucket locks are held.

WHAT WAS WRONG (found by reading, 2026-10-07; not run until these tests)
------------------------------------------------------------------------
`CreditWallet.consume_credits` locks the wallet row and then the buckets, and
then (`_record_license_consumption`) UPDATEs the teacher's school's
`LicenseSubscription` row, still holding the wallet and bucket locks.
`SubscriptionService.refund_credits` does the same at its end. The school
licence paths take the opposite order: they lock the licence row FIRST and then
write teachers' wallets (`_grant_overage_blocks` updates each teacher's wallet
row; the licence renewals write a whole school's wallets). A charge and such a
path can therefore deadlock: the charge holds the wallet and waits for the
licence row, the other holds the licence row and waits for the wallet.

The rule (Senior Manager, 2026-10-07): LICENCE row, then WALLET rows, then
BUCKET rows. The fix (ruled 2026-10-07): the rollup moves to
`transaction.on_commit`, the same F() update in its own short transaction, with
a FAILURE of the callback logged (licence id and amount) and never raised into
the caller. No outbox, no migration.

LIMIT, STATED
-------------
A process that dies between the commit and the callback loses that one
increment silently. The figure's only reader caps a newly enrolled teacher's
first-month grant, and a lost increment errs in the customer's favour by at most
part of one allocation. The exact-once version is Epic B's proposal.

An ordinary TestCase never runs `on_commit` callbacks: the tests use
`captureOnCommitCallbacks`, and the thread test is a TransactionTestCase.

Run with:
    python manage.py test billing.tests.test_licence_rollup_after_commit
"""

import threading
import time
import uuid
from datetime import timedelta
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.db import OperationalError, connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from AutoGrader.testing.concurrency import run_concurrently
from billing.immutable import allow_unsafe_mutation
from billing.license_service import LicenseSubscriptionService
from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from billing.services import SubscriptionService
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()

CHARGE = 1_000
PAUSE = 0.8
MARK_TIMEOUT = 10
SET_LOCK_TIMEOUT = "SET LOCAL lock_timeout = '8s'"

# Fixtures send mail-list syncs; with a TransactionTestCase the after-commit
# callbacks really run. Stubbed once for the module, from the main thread.
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


class LicensedTeacherFixture:
    """A teacher with an active seat under an active licence, a wallet and a
    live MONTHLY bucket. Mixed into a TestCase or a TransactionTestCase."""

    def build_licensed_teacher(self):
        self.school = School.objects.create(name="Rollup High")
        plan = SubscriptionPlan.objects.create(
            name=PlanType.POWER_LICENSE,
            display_name="Power License",
            category=PlanCategory.LICENSE,
            tier=PlanTier.POWER,
            interval=BillingInterval.MONTHLY,
            price_cents=19_900,
            monthly_credits=12_000_000,
            carry_over_percent=50,
            carry_over_expiry_months=6,
            overage_block_size=5_000_000,
            is_active=True,
        )
        admin = CustomUser.objects.create_user(
            email="rollup-admin@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.teacher = CustomUser.objects.create_user(
            email="rollup-teacher@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        # Registration signals may create a trial; start from the state the
        # test chose.
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        self.wallet.buckets.all().delete()
        with allow_unsafe_mutation():
            CreditLedger.objects.filter(user_id=self.teacher.id).delete()
        now = timezone.now()
        self.licence = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=admin,
            plan=plan,
            contract_months=12,
            max_seats=2,
            billing_cycle_start=now,
            billing_cycle_end=now + relativedelta(months=12),
            is_active=True,
            auto_renew=True,
        )
        SchoolCreditAllocation.objects.filter(user=self.teacher).delete()
        self.allocation = SchoolCreditAllocation.objects.create(
            license_subscription=self.licence,
            user=self.teacher,
            monthly_allocation=12_000_000,
            is_active=True,
            next_credit_grant_at=now + timedelta(days=30),
        )
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=12_000_000,
            used_credits=0,
            expires_at=now + timedelta(days=30),
        )

    def consumed(self):
        return LicenseSubscription.objects.get(
            pk=self.licence.pk
        ).total_credits_consumed

    def charge(self, task_id=None):
        task_id = task_id or str(uuid.uuid4())
        self.wallet.consume_credits(CHARGE, feature="grading", task_id=task_id)
        return task_id


class RollupAfterCommitTests(LicensedTeacherFixture, TestCase):
    def setUp(self):
        self.build_licensed_teacher()

    def test_a_charge_rolls_up_what_it_charged_once_it_commits(self):
        """The same figure the inline update gave. Green on the old code too:
        it holds the result, the next test holds WHEN."""
        with self.captureOnCommitCallbacks(execute=True):
            self.charge()

        self.assertEqual(self.consumed(), CHARGE)

    def test_the_figure_is_not_touched_inside_the_charge(self):
        """The point of the row: while the wallet and bucket locks are held
        the licence row is not written. The figure appears when the callback
        runs, once."""
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            self.charge()

            self.assertEqual(
                self.consumed(), 0, "the licence row was written inside the charge"
            )
        self.assertEqual(len(callbacks), 1, "exactly one callback was registered")
        self.assertEqual(self.consumed(), 0, "written before the callback ran")

        for callback in callbacks:
            callback()

        self.assertEqual(self.consumed(), CHARGE)

    def test_a_charge_that_rolls_back_rolls_up_nothing(self):
        """A gain over a naive move: nothing is registered for a charge that
        never committed. Green on the old code (the inline update rolled back
        with it); it fails a version that applies the update outside the
        charge's transaction."""
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            try:
                with transaction.atomic():
                    self.charge()
                    raise RuntimeError("the grading run failed after the charge")
            except RuntimeError:
                pass

        self.assertEqual(callbacks, [])
        self.assertEqual(self.consumed(), 0)

    def test_a_refund_takes_it_back_after_commit(self):
        with self.captureOnCommitCallbacks(execute=True):
            task_id = self.charge()
        self.assertEqual(self.consumed(), CHARGE)

        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            refunded = SubscriptionService.refund_credits(task_id)

            self.assertEqual(refunded, CHARGE)
            self.assertEqual(
                self.consumed(),
                CHARGE,
                "the licence row was written inside the refund",
            )
        self.assertEqual(len(callbacks), 1, "exactly one callback was registered")

        for callback in callbacks:
            callback()

        self.assertEqual(self.consumed(), 0)

    def test_a_refund_never_takes_the_figure_below_zero(self):
        """The window may have been reset between the charge and the refund.
        Green on the old code (it clamped too): held across the move."""
        with self.captureOnCommitCallbacks(execute=True):
            task_id = self.charge()
        LicenseSubscription.objects.filter(pk=self.licence.pk).update(
            total_credits_consumed=CHARGE // 4
        )

        with self.captureOnCommitCallbacks(execute=True):
            SubscriptionService.refund_credits(task_id)

        self.assertEqual(self.consumed(), 0)

    def test_a_failed_roll_up_is_logged_with_its_licence_and_amount_not_raised(self):
        with self.assertLogs("billing.licence_rollup", level="ERROR") as logged:
            with patch.object(
                LicenseSubscription.objects,
                "filter",
                side_effect=OperationalError("connection lost for someone@example.com"),
            ):
                with self.captureOnCommitCallbacks(execute=True):
                    self.charge()

        self.assertEqual(len(logged.records), 1)
        record = logged.records[0]
        # The exact fields: the licence id, the amount and the error's class.
        # Nothing else of the failure: not its text (which here carries an
        # address), not a traceback, no name or address of the teacher.
        self.assertEqual(
            record.getMessage(),
            f"Licence consumption roll-up FAILED after commit: licence "
            f"{self.licence.pk}, amount {CHARGE}, error OperationalError. The "
            f"figure is short by this amount until its window resets.",
        )
        self.assertIsNone(record.exc_info)
        full = "\n".join(logged.output)
        self.assertNotIn("someone@example.com", full)
        self.assertNotIn(self.teacher.email, full)
        self.assertEqual(self.consumed(), 0, "the figure moved although it failed")


class LicenceBeforeWalletRaceTests(LicensedTeacherFixture, TransactionTestCase):
    """REAL threads. Worker 0 is a licence path: it locks the licence row (as
    its callers do) and then writes the teacher's wallet
    (`_grant_overage_blocks`). Worker 1 is a charge for that teacher. On the
    old code the charge holds the wallet and waits for the licence row while
    worker 0 holds the licence row and waits for the wallet: Postgres aborts
    one. On the new code the charge commits first, and its rollup waits for
    the licence row AFTER the commit, holding nothing."""

    reset_sequences = True

    def setUp(self):
        self.build_licensed_teacher()

    def test_a_charge_racing_a_licence_overage_grant_does_not_deadlock(self):
        licence_held = threading.Event()
        charge_started = threading.Event()

        def work(i):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(SET_LOCK_TIMEOUT)
                if i == 0:
                    LicenseSubscription.objects.select_for_update().get(
                        pk=self.licence.pk
                    )
                    licence_held.set()
                    if not charge_started.wait(timeout=MARK_TIMEOUT):
                        raise AssertionError("the charge never started")
                    time.sleep(PAUSE)
                    allocation = SchoolCreditAllocation.objects.select_related(
                        "user"
                    ).get(pk=self.allocation.pk)
                    return LicenseSubscriptionService._grant_overage_blocks(
                        block_size=5_000_000,
                        blocks_by_teacher={str(self.teacher.id): 1},
                        allocation_by_teacher={str(self.teacher.id): allocation},
                        ledger_type=CreditLedgerType.PURCHASE,
                        reference_fn=lambda teacher_id, blocks: "lock-order test",
                        metadata_fn=lambda teacher_id, blocks: {},
                    )
                if not licence_held.wait(timeout=MARK_TIMEOUT):
                    raise AssertionError("the licence path never took the licence row")
                charge_started.set()
                self.charge()
                return "charged"

        results, errors = run_concurrently(
            work, 2, test=self, name="rollup-race", join_timeout=90
        )

        self.assertTrue(charge_started.is_set(), "worker 1 never started its charge")
        self.assertEqual(
            errors,
            [],
            "the charge and the licence path deadlocked (or one waited out the "
            "lock timeout)",
        )
        self.assertEqual(results[1], "charged")
        self.assertTrue(results[0], "the licence path granted nothing")
        self.assertEqual(self.consumed(), CHARGE, "the rollup was lost")
