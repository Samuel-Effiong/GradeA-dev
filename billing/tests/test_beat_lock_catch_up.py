"""
billing/tests/test_beat_lock_catch_up.py
========================================
H-65: a skipped Beat run loses nothing, because the next run catches up.

THE POLICY
----------
`@single_instance` (AutoGrader/beat_locks.py) makes a run skip while
another run holds its task's lock, and fail closed (skip) when the lock
store can't be reached. The SM's ruling: "fail closed only for tasks that
catch up". Skipping is safe for a money or state task only because it
selects EVERYTHING DUE UP TO NOW, not "what became due since the last
run", so whatever a skipped run would have processed is still selected by
the next one.

That is a property of each task's query, not of the lock, so it is proven
here per task, on real rows:

  a. a backlog item that was already due BEFORE the skipped run (about 25
     hours ago for the daily tasks, about two intervals ago for the rest);
  b. with the lock held by another run, the task returns the skip summary,
     logs a WARNING, and leaves the item untouched;
  c. once the lock is released, the next run processes that same item.

AutoGrader/tests_beat_locks.py covers the lock itself (skip, lapse,
heartbeat, fail closed, coverage of every Beat entry). This module covers
only the catch-up half of the argument for the ten billing tasks whose
skipped runs involve money or subscription state.
"""

from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

import stripe as real_stripe
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from AutoGrader.beat_locks import SKIPPED_HELD, BeatLock, declared_lock
from billing.immutable import allow_unsafe_mutation
from billing.license_stripe_mutation import STALE_AFTER as INTENT_STALE_AFTER
from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    LicenseBillingMethod,
    LicenseStripeMutationIntent,
    LicenseStripeMutationOperation,
    LicenseStripeMutationStatus,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    StripeEvent,
    StripeEventStatus,
    StripeSubscriptionStatus,
    UserSubscription,
)
from billing.services import SubscriptionService
from billing.tasks import (
    cleanup_expired_credit_buckets,
    escalate_stale_licence_stripe_intents,
    expire_active_trials,
    process_annual_plan_credit_grants,
    process_license_monthly_credit_refreshes,
    process_license_renewals,
    reconcile_subscription_renewals,
    replay_safe_failed_stripe_events,
    sweep_missing_receipt_urls,
    sweep_stale_stripe_events,
)
from billing.tests.test_abandoned_claim_recovery import make_event
from billing.tests.test_annual_mid_cycle_grants import make_annual_plan
from billing.tests.test_event_replay import ReplayFixture
from billing.tests.test_overage_purchase_integrity import BLOCK
from billing.tests.test_overage_purchase_integrity import make_plan as make_overage_plan
from billing.tests.test_receipts import record as record_transaction
from billing.tests.test_renewal_guards import (
    STRIPE_SUB_ID,
    invoice_payload,
    make_plan,
    stripe_sub_payload,
)
from billing.tests.testing_fake_stripe import fake_stripe, invoice_url
from billing.tests.tests_free_trial import make_individual_plan
from billing.webhooks import STRIPE_EVENT_CLAIM_STALE_AFTER
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()

LOCK_LOGGER = "AutoGrader.beat_locks"

#: How long "another run" holds a lock in these tests: far longer than any
#: test, and released explicitly anyway.
HOLD_SECONDS = 600

#: A daily task's backlog item: due before the previous daily fire.
DAILY_BACKLOG = timedelta(hours=25)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@contextmanager
def held_by_another_run(name):
    """Hold the Beat lock `name` as a different run would, then release it.

    The same BeatLock instance acquires and releases: release is a
    compare-and-delete on this instance's token."""
    lock = BeatLock(name, HOLD_SECONDS, HOLD_SECONDS)
    token = f"another-run-of-{name}"
    if not lock.acquire(token):
        raise AssertionError(f"{name}: the lock was already held before the test")
    try:
        yield token
    finally:
        lock.release()


def make_clean_user(email, *, user_type=UserTypes.TEACHER, school=None):
    """A user with a wallet and nothing else.

    Registration signals may auto-activate a trial, with its bucket and
    ledger row. They are cleared so each test starts from the state it
    chose, the way test_annual_mid_cycle_grants.py does it. The ledger is
    append-only (billing/immutable.py), so clearing signal-created rows
    needs the explicit test escape hatch."""
    user = CustomUser.objects.create_user(
        email=email,
        password="testpass123",  # pragma: allowlist secret
        user_type=user_type,
        school=school,
    )
    UserSubscription.objects.filter(user=user).delete()
    wallet, _ = CreditWallet.objects.get_or_create(user=user)
    wallet.buckets.all().delete()
    with allow_unsafe_mutation():
        CreditLedger.objects.filter(user_id=user.id).delete()
    return user, wallet


def make_school_licence(tag, *, billing_cycle_start, billing_cycle_end, **fields):
    """A school, its admin and a Stripe-billed licence on a LICENSE plan."""
    school = School.objects.create(name=f"Catch-up School {tag}")
    admin = CustomUser.objects.create_user(
        email=f"licence-admin-{tag}@example.com",
        password="testpass123",  # pragma: allowlist secret
        user_type=UserTypes.SCHOOL_ADMIN,
        school=school,
    )
    plan = make_plan(
        PlanType.PRO_LICENSE,
        PlanTier.PRO,
        f"price_licence_{tag}",
        category=PlanCategory.LICENSE,
        credits=20_000_000,
    )
    licence = LicenseSubscription.objects.create(
        school=school,
        admin_user=admin,
        plan=plan,
        is_active=True,
        auto_renew=True,
        billing_cycle_start=billing_cycle_start,
        billing_cycle_end=billing_cycle_end,
        billing_method=LicenseBillingMethod.STRIPE,
        **fields,
    )
    return school, licence


@contextmanager
def stripe_billed_a_new_period(price_id, invoice):
    """Stripe's side of a renewal the webhook never delivered: an active
    subscription whose latest invoice is paid and covers a new period.
    Specific classmethods are patched with real payloads, so stripe.error
    stays real and no request leaves the process. Yields the
    Subscription.retrieve patch, so a skipped run can be shown to have
    made no Stripe call at all."""
    with patch.object(
        real_stripe.Subscription,
        "retrieve",
        return_value=stripe_sub_payload(price_id, latest_invoice=invoice["id"]),
    ) as subscription_retrieve, patch.object(
        real_stripe.Invoice, "retrieve", return_value=invoice
    ), patch.object(
        real_stripe.Invoice, "list", return_value={"data": [invoice]}
    ):
        yield subscription_retrieve


class CatchUpTestCase(TestCase):
    def assert_skipped_then_caught_up(
        self, task, name, *, untouched, processed, catch_up_logs=None
    ):
        """
        (b) With `name`'s lock held by another run, `task` skips: it returns
        the skip summary, logs a WARNING naming the holder, and `untouched()`
        holds. (c) Released, the next run processes the backlog:
        `processed(summary)` holds.

        `catch_up_logs` is an optional (logger, level) the catch-up run is
        expected to log at, asserted rather than left to the console.
        """
        lock = declared_lock(task)
        self.assertIsNotNone(lock, f"{name} declares no Beat lock")
        assert lock is not None
        self.assertEqual(lock.name, name, "the lock is named after its task")

        with held_by_another_run(name) as holder:
            with self.assertLogs(LOCK_LOGGER, "WARNING") as logs:
                skipped = task()

            self.assertEqual(
                skipped,
                f"{name}: {SKIPPED_HELD}.",
                "the run did not skip while another run held the lock",
            )
            [line] = logs.output
            self.assertIn("WARNING", line)
            self.assertIn(name, line)
            self.assertIn(f"held by run {holder}", line)
            self.assertEqual(
                BeatLock(name, 1, 1).holder(),
                holder,
                "the skipped run must leave the other run's lock alone",
            )
            untouched()

        if catch_up_logs is None:
            summary = task()
        else:
            with self.assertLogs(*catch_up_logs):
                summary = task()

        self.assertNotEqual(
            summary,
            f"{name}: {SKIPPED_HELD}.",
            "the run after the lock was released still skipped",
        )
        self.assertIsNone(
            BeatLock(name, 1, 1).holder(), "the catch-up run released its lock"
        )
        processed(summary)


# ---------------------------------------------------------------------------
# Daily tasks: backlog due ~25 hours ago
# ---------------------------------------------------------------------------


class LicenceRenewalCatchUpTests(CatchUpTestCase):
    def test_a_skipped_run_leaves_the_due_licence_for_the_next_run(self):
        now = timezone.now()
        cycle_end = now - DAILY_BACKLOG
        _, licence = make_school_licence(
            "renewal",
            billing_cycle_start=cycle_end - relativedelta(months=12),
            billing_cycle_end=cycle_end,
            stripe_subscription_id=STRIPE_SUB_ID,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )
        renewal = invoice_payload(
            invoice_id="in_licence_catch_up",
            period_end=cycle_end + relativedelta(months=12),
        )

        def untouched():
            subscription_retrieve.assert_not_called()
            licence.refresh_from_db()
            self.assertEqual(
                licence.billing_cycle_end,
                cycle_end,
                "a skipped run renewed the licence",
            )
            self.assertTrue(licence.is_active)

        def processed(summary):
            self.assertIn("1 renewed (fallback)", summary)
            licence.refresh_from_db()
            self.assertGreater(
                licence.billing_cycle_end,
                timezone.now() + relativedelta(months=11),
                "the next run did not renew the licence the skipped run left",
            )
            self.assertTrue(licence.is_active)

        with stripe_billed_a_new_period(
            licence.plan.stripe_price_id, renewal
        ) as subscription_retrieve:
            self.assert_skipped_then_caught_up(
                process_license_renewals,
                "billing.tasks.process_license_renewals",
                untouched=untouched,
                processed=processed,
            )


class AnnualMidCycleGrantCatchUpTests(CatchUpTestCase):
    def test_a_skipped_run_leaves_the_due_grant_for_the_next_run(self):
        now = timezone.now()
        due_at = now - DAILY_BACKLOG
        plan = make_annual_plan()
        user, wallet = make_clean_user("annual-catch-up@example.com")
        cycle_start = due_at - relativedelta(months=1)
        subscription = UserSubscription.objects.create(
            user=user,
            plan=plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=cycle_start,
            billing_cycle_end=cycle_start + relativedelta(years=1),
            next_credit_grant_at=due_at,
        )
        month_one = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=plan.monthly_credits,
            used_credits=4_000,
            expires_at=due_at,
        )

        def mid_cycle_grants():
            return CreditLedger.objects.filter(
                user_id=user.id,
                ledger_type=CreditLedgerType.GRANT,
                metadata__grant_type="ANNUAL_MID_CYCLE",
            )

        def untouched():
            subscription.refresh_from_db()
            month_one.refresh_from_db()
            self.assertEqual(subscription.next_credit_grant_at, due_at)
            self.assertFalse(month_one.is_processed)
            self.assertEqual(
                mid_cycle_grants().count(), 0, "a skipped run granted credits"
            )

        def processed(summary):
            self.assertIn("1 granted", summary)
            grants = list(mid_cycle_grants())
            self.assertEqual(len(grants), 1, "the next run did not grant the month")
            self.assertEqual(grants[0].amount, plan.monthly_credits)
            subscription.refresh_from_db()
            month_one.refresh_from_db()
            next_grant = subscription.next_credit_grant_at
            assert next_grant is not None
            self.assertGreater(
                next_grant, timezone.now(), "next_credit_grant_at did not move on"
            )
            self.assertTrue(month_one.is_processed, "month one was not retired")

        self.assert_skipped_then_caught_up(
            process_annual_plan_credit_grants,
            "billing.tasks.process_annual_plan_credit_grants",
            untouched=untouched,
            processed=processed,
        )


class LicenceMonthlyRefreshCatchUpTests(CatchUpTestCase):
    def test_a_skipped_run_leaves_the_due_refresh_for_the_next_run(self):
        now = timezone.now()
        due_at = now - DAILY_BACKLOG
        cycle_start = due_at - relativedelta(months=1)
        school, licence = make_school_licence(
            "refresh",
            billing_cycle_start=cycle_start,
            billing_cycle_end=cycle_start + relativedelta(months=12),
        )
        teacher, wallet = make_clean_user(
            "licence-teacher-catch-up@example.com", school=school
        )
        allocation = SchoolCreditAllocation.objects.create(
            license_subscription=licence,
            user=teacher,
            monthly_allocation=licence.plan.monthly_credits,
            is_active=True,
            next_credit_grant_at=due_at,
        )
        last_month = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=licence.plan.monthly_credits,
            used_credits=0,
            expires_at=due_at,
        )

        def refresh_grants():
            return CreditLedger.objects.filter(
                user_id=teacher.id,
                ledger_type=CreditLedgerType.GRANT,
                metadata__allocation_id=str(allocation.id),
            )

        def untouched():
            allocation.refresh_from_db()
            last_month.refresh_from_db()
            self.assertEqual(allocation.next_credit_grant_at, due_at)
            self.assertFalse(last_month.is_processed)
            self.assertEqual(
                refresh_grants().count(), 0, "a skipped run refreshed the credits"
            )

        def processed(summary):
            self.assertIn("1 refreshed", summary)
            grants = list(refresh_grants())
            self.assertEqual(len(grants), 1, "the next run did not refresh")
            self.assertEqual(grants[0].amount, allocation.monthly_allocation)
            allocation.refresh_from_db()
            last_month.refresh_from_db()
            next_grant = allocation.next_credit_grant_at
            assert next_grant is not None
            self.assertGreater(
                next_grant, timezone.now(), "next_credit_grant_at did not move on"
            )
            self.assertTrue(last_month.is_processed, "last month was not retired")

        self.assert_skipped_then_caught_up(
            process_license_monthly_credit_refreshes,
            "billing.tasks.process_license_monthly_credit_refreshes",
            untouched=untouched,
            processed=processed,
        )


class SubscriptionRenewalCatchUpTests(CatchUpTestCase):
    def test_a_skipped_run_leaves_the_due_renewal_for_the_next_run(self):
        now = timezone.now()
        cycle_end = now - DAILY_BACKLOG
        plan = make_plan("STANDARD", PlanTier.STANDARD, "price_standard_catch_up")
        user, _ = make_clean_user("renewal-catch-up@example.com")
        subscription = UserSubscription.objects.create(
            user=user,
            plan=plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=cycle_end - relativedelta(months=1),
            billing_cycle_end=cycle_end,
            next_credit_grant_at=cycle_end,
            stripe_subscription_id=STRIPE_SUB_ID,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )
        renewal = invoice_payload(
            invoice_id="in_renewal_catch_up",
            period_end=cycle_end + relativedelta(months=1),
        )

        def untouched():
            subscription_retrieve.assert_not_called()
            subscription.refresh_from_db()
            self.assertTrue(subscription.is_active)
            self.assertEqual(subscription.billing_cycle_end, cycle_end)
            self.assertEqual(
                UserSubscription.objects.filter(user=user).count(),
                1,
                "a skipped run renewed the subscription",
            )

        def processed(summary):
            self.assertIn("1 renewed", summary)
            subscription.refresh_from_db()
            self.assertFalse(
                subscription.is_active, "the elapsed cycle was not superseded"
            )
            renewed = UserSubscription.objects.get(user=user, is_active=True)
            self.assertNotEqual(renewed.pk, subscription.pk)
            self.assertGreater(renewed.billing_cycle_end, timezone.now())

        with stripe_billed_a_new_period(
            plan.stripe_price_id, renewal
        ) as subscription_retrieve:
            self.assert_skipped_then_caught_up(
                reconcile_subscription_renewals,
                "billing.tasks.reconcile_subscription_renewals",
                untouched=untouched,
                processed=processed,
            )


class ExpiredBucketCleanupCatchUpTests(CatchUpTestCase):
    def test_a_skipped_run_leaves_the_expired_bucket_for_the_next_run(self):
        _, wallet = make_clean_user("cleanup-catch-up@example.com")
        bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=1_000,
            used_credits=400,
            expires_at=timezone.now() - DAILY_BACKLOG,
        )

        def expire_rows():
            return CreditLedger.objects.filter(
                bucket=bucket, ledger_type=CreditLedgerType.EXPIRE
            )

        def untouched():
            bucket.refresh_from_db()
            self.assertFalse(bucket.is_processed)
            self.assertEqual(
                expire_rows().count(), 0, "a skipped run expired the bucket"
            )

        def processed(summary):
            self.assertIn("1 buckets processed, 600 raw credits expired", summary)
            bucket.refresh_from_db()
            self.assertTrue(bucket.is_processed, "the next run did not expire it")
            [row] = expire_rows()
            self.assertEqual(row.amount, 600)

        self.assert_skipped_then_caught_up(
            cleanup_expired_credit_buckets,
            "billing.tasks.cleanup_expired_credit_buckets",
            untouched=untouched,
            processed=processed,
        )


# ---------------------------------------------------------------------------
# Every 6 hours: backlog due ~2 intervals ago
# ---------------------------------------------------------------------------


class TrialExpiryCatchUpTests(CatchUpTestCase):
    def test_a_skipped_run_leaves_the_ended_trial_for_the_next_run(self):
        user, _ = make_clean_user("trial-catch-up@example.com")
        plan = make_individual_plan()
        trial = SubscriptionService.activate_free_trial(user, plan)
        # Ended two 6-hour intervals (and a bit) before this run.
        ended_at = timezone.now() - timedelta(hours=13)
        UserSubscription.objects.filter(pk=trial.pk).update(
            trial_end=ended_at,
            billing_cycle_start=ended_at - timedelta(days=14),
            billing_cycle_end=ended_at,
        )
        CreditBucket.objects.filter(
            wallet__user=user, bucket_type=CreditBucketType.TRIAL
        ).update(expires_at=ended_at)
        trial_bucket = CreditBucket.objects.get(
            wallet__user=user, bucket_type=CreditBucketType.TRIAL
        )

        def forfeits():
            return CreditLedger.objects.filter(
                user_id=user.id, ledger_type=CreditLedgerType.EXPIRE
            )

        def untouched():
            trial.refresh_from_db()
            trial_bucket.refresh_from_db()
            self.assertTrue(trial.is_active, "a skipped run expired the trial")
            self.assertTrue(trial.is_trial)
            self.assertFalse(trial_bucket.is_processed)
            self.assertEqual(forfeits().count(), 0)

        def processed(summary):
            self.assertIn("1 expired (14-day limit)", summary)
            trial.refresh_from_db()
            trial_bucket.refresh_from_db()
            self.assertFalse(trial.is_active, "the next run did not expire it")
            self.assertFalse(trial.is_trial)
            self.assertTrue(trial_bucket.is_processed)
            [row] = forfeits()
            self.assertEqual(row.amount, SubscriptionService.TRIAL_CREDITS_RAW)

        self.assert_skipped_then_caught_up(
            expire_active_trials,
            "billing.tasks.expire_active_trials",
            untouched=untouched,
            processed=processed,
        )


# ---------------------------------------------------------------------------
# Hourly and every-5-minute tasks: backlog due ~2 intervals ago
# ---------------------------------------------------------------------------


class StaleStripeEventSweepCatchUpTests(CatchUpTestCase):
    def test_a_skipped_run_leaves_the_abandoned_claims_for_the_next_run(self):
        """Both of the sweep's outcomes: a claim whose worker died before
        the handler started is re-dispatched; one whose handler had started
        is marked FAILED for a human."""
        stale_for = STRIPE_EVENT_CLAIM_STALE_AFTER + timedelta(hours=2)
        unstarted = make_event(event_id="evt_catch_up_unstarted", claimed_age=stale_for)
        started = make_event(
            event_id="evt_catch_up_started",
            claimed_age=stale_for,
            handler_started_at=timezone.now() - stale_for,
        )
        unstarted_claim = unstarted.claimed_at
        started_claim = started.claimed_at

        def untouched():
            delay.assert_not_called()
            unstarted.refresh_from_db()
            started.refresh_from_db()
            for event, claim in (
                (unstarted, unstarted_claim),
                (started, started_claim),
            ):
                self.assertEqual(event.status, StripeEventStatus.PROCESSING)
                self.assertEqual(event.claimed_at, claim, "a skipped run re-claimed")
                self.assertEqual(event.recovery_attempts, 0)

        def processed(summary):
            self.assertIn("1 abandoned claim(s) re-dispatched", summary)
            self.assertIn("1 abandoned claim(s) marked FAILED", summary)
            unstarted.refresh_from_db()
            started.refresh_from_db()
            delay.assert_called_once_with(
                unstarted.stripe_event_id, unstarted.claimed_at.isoformat()
            )
            self.assertEqual(unstarted.status, StripeEventStatus.PROCESSING)
            self.assertEqual(unstarted.recovery_attempts, 1)
            self.assertGreater(unstarted.claimed_at, unstarted_claim)
            self.assertEqual(started.status, StripeEventStatus.FAILED)
            self.assertIn("abandoned", started.last_error)

        with patch(
            "billing.tasks.process_stripe_event.delay", return_value=None
        ) as delay:
            self.assert_skipped_then_caught_up(
                sweep_stale_stripe_events,
                "billing.tasks.sweep_stale_stripe_events",
                untouched=untouched,
                processed=processed,
            )


class StaleLicenceIntentCatchUpTests(CatchUpTestCase):
    def test_a_skipped_run_leaves_the_stale_intent_for_the_next_run(self):
        now = timezone.now()
        _, licence = make_school_licence(
            "intent",
            billing_cycle_start=now - relativedelta(months=1),
            billing_cycle_end=now + relativedelta(months=11),
            stripe_subscription_id="sub_intent_catch_up",
        )
        intent = LicenseStripeMutationIntent.objects.create(
            license_subscription=licence,
            operation=LicenseStripeMutationOperation.UPDATE_SEATS,
            stripe_subscription_id="sub_intent_catch_up",
            requested_change={"old_max_seats": 10, "new_max_seats": 12},
            status=LicenseStripeMutationStatus.PENDING,
        )
        # Two 5-minute intervals past the point it became stale.
        LicenseStripeMutationIntent.objects.filter(pk=intent.pk).update(
            updated_at=now - INTENT_STALE_AFTER - timedelta(minutes=10)
        )

        def untouched():
            intent.refresh_from_db()
            self.assertEqual(
                intent.status,
                LicenseStripeMutationStatus.PENDING,
                "a skipped run escalated the intent",
            )
            self.assertIsNone(intent.escalated_at)

        def processed(summary):
            self.assertEqual(summary, "Stale licence Stripe intents escalated: 1")
            intent.refresh_from_db()
            self.assertEqual(intent.status, LicenseStripeMutationStatus.ESCALATED)
            self.assertIsNotNone(intent.escalated_at)
            self.assertIn("outcome at Stripe is unknown", intent.failure_reason or "")

        # The escalation alerts super admins through safe_delay; none exist
        # here, and the patch keeps it that way whatever the fixtures hold.
        with patch("AutoGrader.dispatch.safe_delay", return_value=None):
            self.assert_skipped_then_caught_up(
                escalate_stale_licence_stripe_intents,
                "billing.tasks.escalate_stale_licence_stripe_intents",
                untouched=untouched,
                processed=processed,
                catch_up_logs=("billing.license_stripe_mutation", "ERROR"),
            )


class MissingReceiptSweepCatchUpTests(CatchUpTestCase):
    def test_a_skipped_run_leaves_the_missing_link_for_the_next_run(self):
        row = record_transaction(
            stripe_invoice_id="in_receipt_catch_up",
            occurred_at=timezone.now() - timedelta(hours=2),
        )

        def untouched():
            self.assertEqual(fake.calls, [], "a skipped run called Stripe")
            row.refresh_from_db()
            self.assertIsNone(row.receipt_url, "a skipped run filled the link")

        def processed(summary):
            self.assertIn("1 filled", summary)
            row.refresh_from_db()
            self.assertEqual(row.receipt_url, invoice_url("in_receipt_catch_up"))

        with fake_stripe() as fake:
            self.assert_skipped_then_caught_up(
                sweep_missing_receipt_urls,
                "billing.tasks.sweep_missing_receipt_urls",
                untouched=untouched,
                processed=processed,
            )


class FailedEventReplayCatchUpTests(CatchUpTestCase, ReplayFixture):
    def setUp(self):
        self.stub_receipt_scheduling()
        self.plan = make_overage_plan()
        self.user, self.wallet = self.build(email="replay-catch-up@example.com")

    def test_a_skipped_run_leaves_the_failed_purchase_for_the_next_run(self):
        row = self.failed_event("evt_replay_catch_up", payment_intent="pi_catch_up")
        failed_at = timezone.now() - timedelta(hours=2)
        StripeEvent.objects.filter(pk=row.pk).update(
            processed_at=failed_at, completed_at=failed_at
        )

        def untouched():
            row.refresh_from_db()
            self.assertEqual(row.status, StripeEventStatus.FAILED)
            self.assertEqual(row.auto_replay_attempts, 0, "a skipped run replayed")
            self.assertEqual(row.auto_replay_note, "")
            self.assertEqual(self.granted_credits(self.wallet), 0)

        def processed(summary):
            self.assertIn("1 replayed", summary)
            row.refresh_from_db()
            self.assertEqual(row.status, StripeEventStatus.SUCCEEDED)
            self.assertEqual(row.auto_replay_attempts, 1)
            self.assertEqual(
                self.granted_credits(self.wallet),
                BLOCK,
                "the next run did not credit the paid purchase",
            )
            self.assertEqual(self.purchase_ledger(self.wallet).count(), 1)

        self.assert_skipped_then_caught_up(
            replay_safe_failed_stripe_events,
            "billing.tasks.replay_safe_failed_stripe_events",
            untouched=untouched,
            processed=processed,
            catch_up_logs=("billing.event_replay", "WARNING"),
        )
