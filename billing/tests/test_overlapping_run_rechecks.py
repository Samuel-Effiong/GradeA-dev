"""
billing/tests/test_overlapping_run_rechecks.py
==============================================
Two runs of the same nightly billing task that overlap (a redeploy
overlap, a second Beat, a manual run) must not both act on one row.

Each task reads its due rows WITHOUT a lock, then works through them. The
per-row service takes a row lock, but a lock alone doesn't help: the second
run waits on it while the first commits, then carries on with the copy it
read before. The fix is to re-check, under the lock, what the task selected
on. These tests drive exactly that interleaving: run B reads its rows, run A
runs to completion, then run B acts.

  * process_annual_plan_credit_grants: run B granted the month again. It
    retired the bucket run A had just granted, rolled part of it over as an
    unearned CARRY_OVER bucket, and wrote a second GRANT row.
  * expire_active_trials: run B wrote a second EXPIRE row. Worse, when the
    trial had converted to paid in between (Stripe's trial-end webhook),
    run B deactivated the subscription the user had just paid for.
  * expire_trial after cleanup_expired_credit_buckets (the trial bucket
    expires at trial_end, so the 05:00 cleanup often gets there first):
    a second EXPIRE row for the same remainder, with no overlap at all.

The threaded test proves the same outcome against a real row lock in
Postgres, with run B really blocked behind run A's uncommitted grant.

The F6 detection query (docs/evidence/midcycle-grant-recheck/) is tested
here too, on the double grant the pre-fix code wrote.
"""

import re
import threading
import time
from datetime import timedelta
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from AutoGrader import beat_locks
from AutoGrader.beat_locks import declared_lock, lock_key
from AutoGrader.testing.concurrency import run_concurrently
from billing.immutable import allow_unsafe_mutation
from billing.models import (
    BillingTransaction,
    BillingTransactionType,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    UserSubscription,
)
from billing.services import SubscriptionService
from billing.stripe_service import StripeWebhookHandler
from billing.tasks import (
    cleanup_expired_credit_buckets,
    expire_active_trials,
    process_annual_plan_credit_grants,
)
from billing.tests.test_annual_mid_cycle_grants import (
    CARRY_PERCENT,
    MONTHLY_CREDITS,
    make_annual_plan,
)
from billing.tests.tests_free_trial import make_individual_plan, make_teacher

EVIDENCE = settings.BASE_DIR / "docs" / "evidence" / "midcycle-grant-recheck"
DOUBLE_GRANTS_SQL = EVIDENCE / "detect_double_midcycle_grants.sql"
SWITCHED_OFF_SQL = EVIDENCE / "detect_paid_subscriptions_switched_off.sql"
DUPLICATE_EXPIRIES_SQL = EVIDENCE / "detect_duplicate_trial_expiries.sql"
DETECTION_QUERIES = (DOUBLE_GRANTS_SQL, SWITCHED_OFF_SQL, DUPLICATE_EXPIRIES_SQL)

GRANT = "billing.services.SubscriptionService.process_mid_cycle_credit_grant"
EXPIRE = "billing.services.SubscriptionService.expire_trial"

#: The carry-over the duplicate run made from the month run A had just
#: granted, none of it used.
UNEARNED_CARRY_OVER = MONTHLY_CREDITS * CARRY_PERCENT // 100


def run_detection(path):
    with connection.cursor() as cursor:
        cursor.execute(path.read_text())
        columns = [c.name for c in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def detect_double_grants():
    return run_detection(DOUBLE_GRANTS_SQL)


def detect_switched_off_paid_subscriptions():
    return run_detection(SWITCHED_OFF_SQL)


def detect_duplicate_trial_expiries():
    return run_detection(DUPLICATE_EXPIRIES_SQL)


def convert_via_checkout(subscription_pk):
    """A user paying during their trial: checkout.session.completed through
    the real handler, which locks the trial row, converts that same row to
    paid (finalize_trial_to_paid_conversion) and records the PAID trial
    conversion charge. This is the production conversion path."""
    trial = UserSubscription.objects.get(pk=subscription_pk)
    StripeWebhookHandler._handle_individual_checkout(
        {
            "id": f"cs_trial_{subscription_pk}",
            "subscription": f"sub_checkout_{subscription_pk}",
            "amount_total": 1_500,
            "currency": "usd",
        },
        {
            "user_id": str(trial.user_id),
            "plan_id": str(trial.plan_id),
            "trial_subscription_id": str(subscription_pk),
        },
    )


def convert_via_stripe_webhook(subscription_pk):
    """Stripe's trial-end invoice.payment_succeeded, through the real
    handler: the same row converted to paid, and the PAID trial conversion
    charge recorded."""
    period_start = timezone.now()
    period_end = period_start + relativedelta(months=1)
    invoice = {
        "id": f"in_trial_end_{subscription_pk}",
        "amount_paid": 1_500,
        "currency": "usd",
        "lines": {
            "data": [
                {
                    "period": {
                        "start": int(period_start.timestamp()),
                        "end": int(period_end.timestamp()),
                    }
                }
            ]
        },
    }
    StripeWebhookHandler._handle_individual_invoice_succeeded(
        UserSubscription.objects.get(pk=subscription_pk),
        "subscription_cycle",
        invoice,
    )


def run_b_after_run_a(task, service_path):
    """
    Run `task` as run B, with run A (the same task, start to finish)
    happening after run B has read its rows and before its first row is
    acted on. Returns (summary_a, summary_b).

    H-65 locks each Beat task to one run at a time, so an overlap now needs
    run B's lock to have LAPSED (a Redis blip, or a run past its max_hold):
    the key is deleted before run A, which then takes the lock itself. The
    per-row re-checks under test are the defence for exactly that case. Run
    B must notice at its end that it ran without its lock.
    """
    real = _unwrapped(service_path)
    lock = declared_lock(task)
    assert lock is not None, f"{task} holds no Beat lock"
    summaries = {}

    def run_b_acts_on_its_stale_row(row, *args, **kwargs):
        if "a" not in summaries:
            beat_locks._redis().delete(lock_key(lock.name))  # B's lock lapses
            with patch(service_path, side_effect=real):
                summaries["a"] = task()
        return real(row, *args, **kwargs)

    with patch(service_path, side_effect=run_b_acts_on_its_stale_row):
        with assert_logs("AutoGrader.beat_locks", "ERROR") as logs:
            summaries["b"] = task()
    assert any("finished without its lock" in line for line in logs.output), logs.output
    return summaries["a"], summaries["b"]


def assert_logs(logger, level):
    """TestCase.assertLogs outside a TestCase method."""
    return TestCase().assertLogs(logger, level)


def _unwrapped(service_path):
    name = service_path.rsplit(".", 1)[1]
    return getattr(SubscriptionService, name)


def _clear_signal_state(user):
    """Registration signals may create a trial, a wallet and ledger rows.
    Start from the state each test builds instead."""
    UserSubscription.objects.filter(user=user).delete()
    wallet, _ = CreditWallet.objects.get_or_create(user=user)
    wallet.buckets.all().delete()
    # The ledger is append-only (billing/immutable.py); this fabricates a
    # starting state, it doesn't edit history.
    with allow_unsafe_mutation():
        CreditLedger.objects.filter(user_id=user.id).delete()
    return wallet


class AnnualGrantFixture:
    def make_due_annual_subscription(self):
        self.plan = make_annual_plan()
        self.user = make_teacher("annual-overlap@example.com")
        self.wallet = _clear_signal_state(self.user)
        now = timezone.now()
        start = now - relativedelta(months=1, minutes=5)
        self.subscription = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=start,
            billing_cycle_end=start + relativedelta(years=1),
            next_credit_grant_at=now - timedelta(minutes=5),
        )
        # Month 1's bucket, fully used, so its own rollover is zero and
        # every carry-over in these tests comes from a double grant.
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=MONTHLY_CREDITS,
            used_credits=MONTHLY_CREDITS,
            expires_at=now - timedelta(minutes=5),
        )

    def mid_cycle_grants(self):
        return CreditLedger.objects.filter(
            user_id=self.user.id,
            ledger_type=CreditLedgerType.GRANT,
            metadata__grant_type="ANNUAL_MID_CYCLE",
        )

    def carry_over_buckets(self):
        return CreditBucket.objects.filter(
            wallet=self.wallet, bucket_type=CreditBucketType.CARRY_OVER
        )


class MidCycleGrantOverlapTests(AnnualGrantFixture, TestCase):
    def setUp(self):
        self.make_due_annual_subscription()
        self.due_at = self.subscription.next_credit_grant_at

    def test_an_overlapping_run_does_not_grant_the_month_again(self):
        summary_a, summary_b = run_b_after_run_a(
            process_annual_plan_credit_grants, GRANT
        )

        # First, so that on the pre-fix code the failure shows what the
        # F6 query finds on the real double grant.
        self.assertEqual(detect_double_grants(), [])
        self.assertEqual(self.mid_cycle_grants().count(), 1)
        self.assertFalse(self.carry_over_buckets().exists())
        self.assertEqual(self.wallet.total_remaining_credits(), MONTHLY_CREDITS)
        self.assertEqual(
            CreditBucket.objects.filter(
                wallet=self.wallet,
                bucket_type=CreditBucketType.MONTHLY,
                is_processed=False,
            ).count(),
            1,
        )
        # Moved on by exactly one month, not two.
        self.subscription.refresh_from_db()
        next_grant = self.subscription.next_credit_grant_at
        assert next_grant is not None
        self.assertGreater(next_grant, timezone.now())
        self.assertLess(next_grant, timezone.now() + relativedelta(months=1, minutes=1))
        self.assertIn("1 granted, 0 already granted", summary_a)
        self.assertIn("0 granted, 1 already granted by another run", summary_b)

    def test_the_service_refuses_a_stale_copy_that_is_no_longer_due(self):
        stale = UserSubscription.objects.get(pk=self.subscription.pk)
        self.assertIsNotNone(
            SubscriptionService.process_mid_cycle_credit_grant(
                UserSubscription.objects.get(pk=self.subscription.pk)
            )
        )
        self.assertIsNone(SubscriptionService.process_mid_cycle_credit_grant(stale))
        self.assertEqual(self.mid_cycle_grants().count(), 1)

    def test_each_eligibility_condition_is_rechecked_under_the_lock(self):
        """Every condition the task selects on, changed after the read."""
        now = timezone.now()
        changes: dict[str, dict[str, object]] = {
            "deactivated": {"is_active": False},
            "back on a trial": {"is_trial": True},
            "no next grant": {"next_credit_grant_at": None},
            "next grant in the future": {
                "next_credit_grant_at": now + timedelta(days=3)
            },
            "cycle already ended": {"billing_cycle_end": now - timedelta(minutes=1)},
        }
        for label, change in changes.items():
            with self.subTest(label), transaction.atomic():
                stale = UserSubscription.objects.get(pk=self.subscription.pk)
                UserSubscription.objects.filter(pk=stale.pk).update(**change)
                self.assertIsNone(
                    SubscriptionService.process_mid_cycle_credit_grant(stale)
                )
                self.assertEqual(self.mid_cycle_grants().count(), 0)
                transaction.set_rollback(True)

    def test_a_due_subscription_is_still_granted(self):
        """The control: one run, one grant."""
        summary = process_annual_plan_credit_grants()
        self.assertIn("1 granted, 0 already granted", summary)
        self.assertEqual(self.mid_cycle_grants().count(), 1)


class DoubleGrantDetectionQueryTests(AnnualGrantFixture, TestCase):
    """The F6 query finds a double grant exactly as the pre-fix code wrote
    it, and reports what it cost."""

    def setUp(self):
        self.make_due_annual_subscription()

    def write_the_pre_fix_double_grant(self):
        due_at = self.subscription.next_credit_grant_at
        process_annual_plan_credit_grants()
        # Put back what run B read before run A committed. From here the
        # service does precisely what the pre-fix code did for run B.
        UserSubscription.objects.filter(pk=self.subscription.pk).update(
            next_credit_grant_at=due_at
        )
        process_annual_plan_credit_grants()

    def test_it_finds_the_double_grant_and_its_cost(self):
        single_run_balance = MONTHLY_CREDITS
        self.write_the_pre_fix_double_grant()

        self.assertEqual(self.mid_cycle_grants().count(), 2)
        overpaid = self.wallet.total_remaining_credits() - single_run_balance
        self.assertEqual(overpaid, UNEARNED_CARRY_OVER)

        [row] = detect_double_grants()
        self.assertEqual(row["subscription_id"], str(self.subscription.id))
        self.assertEqual(row["wallet_id"], self.wallet.id)
        self.assertEqual(row["extra_grants"], 1)
        self.assertEqual(row["extra_monthly_grant_credits_raw"], MONTHLY_CREDITS)
        self.assertEqual(row["extra_carry_over_credits_raw"], overpaid)

    def test_monthly_grants_a_month_apart_are_not_reported(self):
        process_annual_plan_credit_grants()
        # The next month's genuine grant, a month later.
        later = timezone.now() + relativedelta(months=1, minutes=1)
        with patch("django.utils.timezone.now", return_value=later):
            self.assertIn("1 granted", process_annual_plan_credit_grants())
        self.assertEqual(self.mid_cycle_grants().count(), 2)
        self.assertEqual(detect_double_grants(), [])

    def test_its_output_carries_no_email(self):
        self.write_the_pre_fix_double_grant()
        [row] = detect_double_grants()
        self.assertNotIn("@", " ".join(str(value) for value in row.values()))
        self.assertEqual(
            set(row),
            {
                "subscription_id",
                "wallet_id",
                "extra_grants",
                "extra_monthly_grant_credits_raw",
                "extra_carry_over_credits_raw",
                "first_extra_grant_at",
                "last_extra_grant_at",
            },
        )

    def test_every_detection_query_is_read_only(self):
        for path in DETECTION_QUERIES:
            sql = re.sub(r"--[^\n]*", "", path.read_text()).upper()
            for verb in ("INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "TRUNCATE"):
                with self.subTest(path.name, verb=verb):
                    self.assertNotRegex(sql, rf"\b{verb}\b")


class MidCycleGrantRealLockTests(AnnualGrantFixture, TransactionTestCase):
    """The same interleaving with real threads and a real Postgres row
    lock: run A grants and holds its transaction open until run B is seen
    blocked on the row lock, then commits."""

    def setUp(self):
        self.make_due_annual_subscription()

    def test_run_b_blocked_behind_run_a_does_not_grant_again(self):
        granted = threading.Event()
        seen = {}

        def run_a():
            with transaction.atomic():
                SubscriptionService.process_mid_cycle_credit_grant(
                    UserSubscription.objects.get(pk=self.subscription.pk)
                )
                granted.set()
                seen["b_waited_on_the_lock"] = _wait_for_a_lock_waiter()
            return "committed"

        def run_b():
            if not granted.wait(timeout=30):
                raise AssertionError("run A never granted")
            return process_annual_plan_credit_grants()

        results, errors = run_concurrently(
            lambda i: (run_a, run_b)[i](), 2, test=self, name="overlap-run"
        )

        self.assertEqual(errors, [])
        # First, so that on the pre-fix code the failure shows what the F6
        # query finds on the real double grant.
        self.assertEqual(detect_double_grants(), [])
        self.assertTrue(
            seen.get("b_waited_on_the_lock"),
            "run B never blocked on the row lock, so the interleaving under "
            "test did not happen",
        )
        self.assertIn("0 granted, 1 already granted by another run", results[1])
        self.assertEqual(self.mid_cycle_grants().count(), 1)
        self.assertFalse(self.carry_over_buckets().exists())


def _wait_for_a_lock_waiter(timeout=20.0):
    """True once another backend of this database is waiting on a lock
    while querying the subscription table (the row lock under test).

    Called from inside the caller's open transaction, and Postgres
    snapshots pg_stat_activity once per transaction, so the snapshot is
    cleared before every look."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_stat_clear_snapshot()")
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND pid <> pg_backend_pid() "
                "AND wait_event_type = 'Lock' "
                "AND query ILIKE '%%billing_usersubscription%%'"
            )
            if cursor.fetchone()[0]:
                return True
        time.sleep(0.05)
    return False


class TrialExpiryFixture:
    def make_ended_trial(self, email):
        self.plan = make_individual_plan()
        self.user = make_teacher(email)
        _clear_signal_state(self.user)
        self.trial_sub = SubscriptionService.activate_free_trial(self.user, self.plan)
        ended = timezone.now() - timedelta(hours=1)
        self.trial_sub.trial_end = ended
        self.trial_sub.billing_cycle_end = ended
        self.trial_sub.stripe_subscription_id = f"sub_trial_{self.trial_sub.pk}"
        self.trial_sub.save(
            update_fields=["trial_end", "billing_cycle_end", "stripe_subscription_id"]
        )
        self.trial_bucket = CreditBucket.objects.get(
            wallet__user=self.user, bucket_type=CreditBucketType.TRIAL
        )
        CreditBucket.objects.filter(pk=self.trial_bucket.pk).update(expires_at=ended)

    def trial_expire_rows(self):
        return CreditLedger.objects.filter(
            bucket=self.trial_bucket, ledger_type=CreditLedgerType.EXPIRE
        )


class TrialExpiryOverlapTests(TrialExpiryFixture, TestCase):
    def setUp(self):
        self.make_ended_trial("trial-overlap@example.com")

    def test_an_overlapping_run_does_not_expire_the_trial_again(self):
        summary_a, summary_b = run_b_after_run_a(expire_active_trials, EXPIRE)

        self.assertEqual(detect_duplicate_trial_expiries(), [])
        self.assertEqual(self.trial_expire_rows().count(), 1)
        self.trial_sub.refresh_from_db()
        self.assertFalse(self.trial_sub.is_active)
        self.assertIn("1 expired (14-day limit)", summary_a)
        self.assertIn("0 expired (14-day limit)", summary_b)
        self.assertIn("1 already expired or converted", summary_b)

    def test_a_conversion_in_between_is_not_undone(self):
        """Stripe's trial-end webhook converts the trial on the same row
        after run B read it. Run B must leave the paid subscription alone."""

        def convert_to_paid():
            convert_via_stripe_webhook(self.trial_sub.pk)
            return "converted"

        real = SubscriptionService.expire_trial
        converted = []

        def run_b_acts_on_its_stale_row(row, *args, **kwargs):
            if not converted:
                converted.append(convert_to_paid())
            return real(row, *args, **kwargs)

        with patch(EXPIRE, side_effect=run_b_acts_on_its_stale_row):
            summary = expire_active_trials()

        self.assertEqual(detect_switched_off_paid_subscriptions(), [])
        self.trial_sub.refresh_from_db()
        self.assertTrue(
            self.trial_sub.is_active, "the paid subscription was deactivated"
        )
        self.assertFalse(self.trial_sub.is_trial)
        self.assertEqual(self.trial_expire_rows().count(), 0)
        self.assertIn("1 already expired or converted", summary)

    def expire_run_with_a_checkout_conversion_in_between(self):
        real = SubscriptionService.expire_trial
        converted = []

        def run_b_acts_on_its_stale_row(row, *args, **kwargs):
            if not converted:
                convert_via_checkout(self.trial_sub.pk)
                converted.append(True)
            return real(row, *args, **kwargs)

        with patch(EXPIRE, side_effect=run_b_acts_on_its_stale_row):
            summary = expire_active_trials()
        self.assertEqual(converted, [True], "the expiry never reached the trial")
        return summary

    def assert_still_paid(self, summary):
        self.assertEqual(detect_switched_off_paid_subscriptions(), [])
        self.trial_sub.refresh_from_db()
        self.assertTrue(
            self.trial_sub.is_active, "the paid subscription was deactivated"
        )
        self.assertFalse(self.trial_sub.is_trial)
        self.assertEqual(self.trial_sub.stripe_status, "ACTIVE")
        self.assertIn("1 already expired or converted", summary)

    def test_a_checkout_conversion_in_between_is_not_undone(self):
        """The production path, on the ended-trial (time) branch."""
        self.assert_still_paid(self.expire_run_with_a_checkout_conversion_in_between())

    def test_a_checkout_conversion_is_not_undone_on_the_credits_path(self):
        """The production path, on the credits-exhausted branch, which calls
        expire_trial(force=True) for a trial that hasn't ended yet."""
        UserSubscription.objects.filter(pk=self.trial_sub.pk).update(
            trial_end=timezone.now() + timedelta(days=3),
            billing_cycle_end=timezone.now() + timedelta(days=3),
        )
        CreditBucket.objects.filter(pk=self.trial_bucket.pk).update(
            expires_at=timezone.now() + timedelta(days=3),
            used_credits=self.trial_bucket.total_credits,
        )

        summary = self.expire_run_with_a_checkout_conversion_in_between()

        self.assertIn("0 expired (credits exhausted)", summary)
        self.assert_still_paid(summary)

    def test_cleanup_first_does_not_write_a_second_expire_row(self):
        cleanup_expired_credit_buckets()
        self.assertEqual(self.trial_expire_rows().count(), 1)

        summary = expire_active_trials()

        self.assertEqual(detect_duplicate_trial_expiries(), [])
        self.assertEqual(self.trial_expire_rows().count(), 1)
        self.trial_sub.refresh_from_db()
        self.assertFalse(self.trial_sub.is_active)
        self.assertIn("1 expired (14-day limit)", summary)

    def test_an_ended_trial_is_still_expired(self):
        """The control: one run, one EXPIRE row, trial deactivated."""
        self.assertIs(SubscriptionService.expire_trial(self.trial_sub), True)
        self.assertEqual(self.trial_expire_rows().count(), 1)
        self.trial_sub.refresh_from_db()
        self.assertFalse(self.trial_sub.is_active)

    def test_each_trial_condition_is_rechecked_under_the_lock(self):
        changes: dict[str, dict[str, object]] = {
            "deactivated": {"is_active": False},
            "converted to paid": {"is_trial": False},
        }
        for label, change in changes.items():
            with self.subTest(label), transaction.atomic():
                stale = UserSubscription.objects.get(pk=self.trial_sub.pk)
                UserSubscription.objects.filter(pk=stale.pk).update(**change)
                self.assertIs(SubscriptionService.expire_trial(stale), False)
                self.assertEqual(self.trial_expire_rows().count(), 0)
                transaction.set_rollback(True)

    def test_a_trial_extended_after_the_read_is_not_expired(self):
        """The has-it-ended guard reads the locked row, not the stale copy."""
        stale = UserSubscription.objects.get(pk=self.trial_sub.pk)
        UserSubscription.objects.filter(pk=stale.pk).update(
            trial_end=timezone.now() + timedelta(days=2)
        )
        with self.assertRaisesMessage(ValueError, "has not ended yet"):
            SubscriptionService.expire_trial(stale)
        self.trial_sub.refresh_from_db()
        self.assertTrue(self.trial_sub.is_active)
        self.assertEqual(self.trial_expire_rows().count(), 0)


class TrialConversionRealLockTests(TrialExpiryFixture, TransactionTestCase):
    """Stripe's trial-end conversion holds its transaction open (its UPDATE
    locks the row) while the expiry task, holding a copy read before, tries
    to expire the same trial. The expiry must wait, then leave the paid
    subscription alone."""

    def setUp(self):
        self.make_ended_trial("trial-race@example.com")

    def test_expiry_blocked_behind_a_conversion_leaves_it_paid(self):
        stale = UserSubscription.objects.get(pk=self.trial_sub.pk)
        converting = threading.Event()
        seen = {}

        def convert():
            with transaction.atomic():
                convert_via_stripe_webhook(self.trial_sub.pk)
                converting.set()
                seen["expiry_waited_on_the_lock"] = _wait_for_a_lock_waiter()
            return "converted"

        def expire():
            if not converting.wait(timeout=30):
                raise AssertionError("the conversion never ran")
            return SubscriptionService.expire_trial(stale)

        results, errors = run_concurrently(
            lambda i: (convert, expire)[i](), 2, test=self, name="trial-race"
        )

        self.assertEqual(errors, [])
        # First, so that on the pre-fix code the failure shows what the F6
        # query finds on the real switched-off subscription.
        self.assertEqual(detect_switched_off_paid_subscriptions(), [])
        self.assertTrue(
            seen.get("expiry_waited_on_the_lock"),
            "the expiry never blocked on the row lock, so the race under "
            "test did not happen",
        )
        self.assertIs(results[1], False)
        self.trial_sub.refresh_from_db()
        self.assertTrue(
            self.trial_sub.is_active, "the paid subscription was deactivated"
        )
        self.assertFalse(self.trial_sub.is_trial)
        self.assertEqual(self.trial_expire_rows().count(), 0)


class TrialDetectionQueryTests(TrialExpiryFixture, TestCase):
    """The two trial F6 queries find the damage exactly as the pre-fix code
    wrote it, and leave the legitimate cases alone."""

    def setUp(self):
        self.make_ended_trial("trial-detect@example.com")

    def write_the_pre_fix_switch_off(self):
        """What the pre-fix expire_trial saved from its stale copy after
        the conversion: is_active=False, is_trial=False, updated_at."""
        convert_via_stripe_webhook(self.trial_sub.pk)
        UserSubscription.objects.filter(pk=self.trial_sub.pk).update(
            is_active=False, is_trial=False, updated_at=timezone.now()
        )

    def test_it_finds_a_paid_subscription_switched_off(self):
        self.write_the_pre_fix_switch_off()
        conversion = BillingTransaction.objects.get(
            user_subscription_id=self.trial_sub.pk,
            transaction_type=BillingTransactionType.INDIVIDUAL_TRIAL_CONVERSION_CHARGE,
        )

        [row] = detect_switched_off_paid_subscriptions()
        self.assertEqual(row["subscription_id"], self.trial_sub.pk)
        self.assertEqual(row["user_id"], self.user.pk)
        self.assertEqual(row["conversion_transaction_id"], conversion.pk)
        self.assertEqual(row["stripe_status"], "ACTIVE")
        self.assertNotIn("@", " ".join(str(value) for value in row.values()))

    def test_legitimate_inactive_subscriptions_are_not_reported(self):
        convert_via_stripe_webhook(self.trial_sub.pk)
        self.assertEqual(detect_switched_off_paid_subscriptions(), [], "active")

        cases: dict[str, dict[str, object]] = {
            "cancelled at Stripe": {"stripe_status": "CANCELED"},
            "renewed after its period ended": {
                "billing_cycle_end": timezone.now() - timedelta(minutes=1)
            },
        }
        for label, change in cases.items():
            with self.subTest(label), transaction.atomic():
                UserSubscription.objects.filter(pk=self.trial_sub.pk).update(
                    is_active=False, updated_at=timezone.now(), **change
                )
                self.assertEqual(detect_switched_off_paid_subscriptions(), [])
                transaction.set_rollback(True)

    def test_a_trial_that_simply_expired_is_not_reported(self):
        expire_active_trials()
        self.assertEqual(detect_switched_off_paid_subscriptions(), [])

    def test_it_finds_a_trial_bucket_expired_twice(self):
        cleanup_expired_credit_buckets()
        # What the pre-fix expire_trial did next: it took the processed
        # bucket anyway and wrote the remainder off again.
        CreditBucket.objects.filter(pk=self.trial_bucket.pk).update(is_processed=False)
        SubscriptionService.expire_trial(self.trial_sub)
        remainder = self.trial_bucket.total_credits - self.trial_bucket.used_credits

        [row] = detect_duplicate_trial_expiries()
        self.assertEqual(row["bucket_id"], self.trial_bucket.pk)
        self.assertEqual(row["wallet_id"], self.trial_bucket.wallet_id)
        self.assertEqual(row["expire_rows"], 2)
        self.assertEqual(row["overstated_expired_credits_raw"], remainder)

    def test_a_single_expiry_is_not_reported(self):
        expire_active_trials()
        cleanup_expired_credit_buckets()
        self.assertEqual(self.trial_expire_rows().count(), 1)
        self.assertEqual(detect_duplicate_trial_expiries(), [])
