"""
billing/tests/test_live_qa_invariants.py
========================================
Every invariant, exercised in BOTH directions. No Stripe, no network.

WHY THIS FILE IS NOT OPTIONAL
-----------------------------
The invariant engine is what catches bugs in sequences nobody scripted.
If an invariant is silently broken — always returning ok, never
registering, raising on a shape it did not expect — the suite reports
green while checking nothing. That is a worse failure than having no
invariant at all, because it manufactures confidence.

So each invariant gets two tests: one where the property holds, one where
it is deliberately broken. An invariant that cannot FAIL is not an
invariant, it is decoration.

InvariantCoverageTests then enforces that pairing automatically, by
comparing the registry against the test methods present here. Adding an
invariant without a violation test breaks the build.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.live_qa.invariants import (
    ERROR,
    INVARIANTS,
    OK,
    SKIPPED,
    VIOLATED,
    ActorHistory,
    InvariantContext,
    InvariantOutcome,
    StepRecord,
    evaluate,
    failures,
    invariant,
    summarise,
)
from billing.models import (
    BillingInterval,
    BillingTransaction,
    BillingTransactionSource,
    BillingTransactionType,
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    PlanCategory,
    PlanTier,
    StripeEvent,
    StripeEventStatus,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.webhooks import STRIPE_EVENT_CLAIM_STALE_AFTER
from users.models import UserTypes

CustomUser = get_user_model()

CUSTOMER_ID = "cus_inv_test"
SUBSCRIPTION_ID = "sub_inv_test"
MONTHLY_CREDITS = 10_000


class FakeSnapshot:
    """Stand-in for StripeSnapshot with no network. Attributes are set
    directly by each test to describe what Stripe currently says."""

    def __init__(self, subscription=None, paid_invoices=None):
        self.subscription = subscription
        self.paid_invoices = paid_invoices or []
        self.errors: list = []


def stripe_subscription(
    *, period_end, status="active", subscription_id=SUBSCRIPTION_ID
):
    """A Stripe Subscription shaped the way API 2025-03-31+ shapes it —
    the period lives on items.data[], which is exactly what C1 was about."""
    return {
        "id": subscription_id,
        "status": status,
        "items": {
            "data": [
                {
                    "id": "si_1",
                    "current_period_start": int(
                        (period_end - relativedelta(months=1)).timestamp()
                    ),
                    "current_period_end": int(period_end.timestamp()),
                }
            ]
        },
    }


def paid_invoice(invoice_id, billing_reason="subscription_cycle"):
    return {"id": invoice_id, "status": "paid", "billing_reason": billing_reason}


class InvariantTestBase(TestCase):
    """A healthy individual subscriber. Each test breaks exactly one thing."""

    def setUp(self):
        self.plan = SubscriptionPlan.objects.create(
            name="PRO",
            display_name="Pro",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.PRO,
            interval=BillingInterval.MONTHLY,
            price_cents=4_999,
            monthly_credits=MONTHLY_CREDITS,
            stripe_price_id="price_pro",
            carry_over_percent=0,
            carry_over_expiry_months=1,
            is_active=True,
        )
        self.user = CustomUser.objects.create_user(
            email="inv@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        UserSubscription.objects.filter(user=self.user).delete()
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        self.wallet.buckets.all().delete()
        self.wallet.stripe_customer_id = CUSTOMER_ID
        self.wallet.save(update_fields=["stripe_customer_id"])

        self.now = timezone.now().replace(microsecond=0)
        self.period_end = self.now + relativedelta(months=1)
        self.subscription = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=self.now,
            billing_cycle_end=self.period_end,
            next_credit_grant_at=self.period_end,
            stripe_subscription_id=SUBSCRIPTION_ID,
            stripe_customer_id=CUSTOMER_ID,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=MONTHLY_CREDITS,
            used_credits=0,
            expires_at=self.period_end,
        )

    # -- context ---------------------------------------------------------

    def context(self, **overrides):
        self.subscription.refresh_from_db()
        self.wallet.refresh_from_db()
        defaults = {
            "user": self.user,
            "subscription": self.subscription,
            "wallet": self.wallet,
            "run_customer_id": CUSTOMER_ID,
            "run_subscription_id": SUBSCRIPTION_ID,
            "stripe_event_ids": set(),
            "step": StepRecord(index=1, label="step1"),
            "history": ActorHistory(),
            "snapshot": FakeSnapshot(
                subscription=stripe_subscription(period_end=self.period_end),
                paid_invoices=[],
            ),
        }
        defaults.update(overrides)
        return InvariantContext(**defaults)

    # -- assertions ------------------------------------------------------

    def run_one(self, key, ctx):
        results = evaluate(ctx, scopes=("individual", "global"), keys={key})
        self.assertEqual(len(results), 1, f"{key} did not evaluate")
        return results[0].outcome

    def assertHolds(self, key, ctx):
        outcome = self.run_one(key, ctx)
        self.assertIn(
            outcome.status,
            (OK, SKIPPED),
            f"{key} should hold here but reported {outcome.status}: {outcome.detail}",
        )
        return outcome

    def assertViolated(self, key, ctx):
        outcome = self.run_one(key, ctx)
        self.assertEqual(
            outcome.status,
            VIOLATED,
            f"{key} should have been violated but reported "
            f"{outcome.status}: {outcome.detail}",
        )
        self.assertTrue(outcome.detail, f"{key} violated with no explanation")
        return outcome


# --------------------------------------------------------------------------
# Healthy baseline
# --------------------------------------------------------------------------


class HealthyStateTests(InvariantTestBase):
    def test_nothing_is_violated_in_a_healthy_state(self):
        results = evaluate(self.context(), scopes=("individual", "global"))
        self.assertEqual(
            failures(results),
            [],
            "a healthy subscriber must not trip any invariant",
        )

    def test_every_registered_invariant_actually_evaluates(self):
        """Guards against an invariant that is registered but never run —
        e.g. a scope typo, which would make it silently vacuous."""
        results = evaluate(self.context(), scopes=("individual", "global"))
        self.assertEqual(len(results), len(INVARIANTS))

    def test_summary_counts_add_up(self):
        results = evaluate(self.context(), scopes=("individual", "global"))
        counts = summarise(results)
        self.assertEqual(sum(counts.values()), len(results))


# --------------------------------------------------------------------------
# One violation test per invariant. Names drive the coverage guard below.
# --------------------------------------------------------------------------


class InvariantViolationTests(InvariantTestBase):
    def test_violated__sub_single_active(self):
        """The duplicate state cannot be created here — the
        one_active_subscription_per_user constraint refuses it, which is
        exactly right. So this exercises the part of the invariant this
        codebase owns: the decision it makes about what it observes.
        Enforcement is Django's job; interpreting the count is ours.
        (Skipping instead would leave an invariant nothing proves can
        fail, which is the failure mode this whole file guards against.)"""
        with patch(
            "billing.live_qa.invariants_individual.UserSubscription"
        ) as mock_model:
            mock_model.objects.filter.return_value.count.return_value = 2
            self.assertViolated("sub.single_active", self.context())

    def test_violated__sub_cycle_ordered(self):
        self.subscription.billing_cycle_end = self.subscription.billing_cycle_start
        self.subscription.save(update_fields=["billing_cycle_end"])
        self.assertViolated("sub.cycle_ordered", self.context())

    def test_violated__sub_period_monotonic(self):
        history = ActorHistory()
        history.observe(billing_cycle_end=self.period_end + relativedelta(months=6))
        self.assertViolated("sub.period_monotonic", self.context(history=history))

    def test_violated__sub_pending_consistency(self):
        self.subscription.pending_plan = self.plan
        self.subscription.pending_change_type = None
        self.subscription.save(update_fields=["pending_plan", "pending_change_type"])
        self.assertViolated("sub.pending_consistency", self.context())

    def test_violated__sub_trial_flags_consistent(self):
        self.subscription.is_trial = True
        self.subscription.trial_end = None
        self.subscription.save(update_fields=["is_trial", "trial_end"])
        self.assertViolated("sub.trial_flags_consistent", self.context())

    def test_violated__sub_grant_date_present_for_annual(self):
        """Phase 0's Bug 2, as a standing check."""
        annual = SubscriptionPlan.objects.create(
            name="PRO_ANNUAL",
            display_name="Pro Annual",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.PRO,
            interval=BillingInterval.ANNUAL,
            price_cents=49_999,
            monthly_credits=MONTHLY_CREDITS,
            is_active=True,
        )
        self.subscription.plan = annual
        self.subscription.next_credit_grant_at = None
        self.subscription.save(update_fields=["plan", "next_credit_grant_at"])
        self.assertViolated("sub.grant_date_present_for_annual", self.context())

    def test_violated__sub_subscription_id_stable(self):
        self.subscription.stripe_subscription_id = "sub_something_else"
        self.subscription.save(update_fields=["stripe_subscription_id"])
        self.assertViolated("sub.subscription_id_stable", self.context())

    def test_violated__sub_period_matches_stripe(self):
        """Local cycle drifted from Stripe's real period — C1/C2."""
        snapshot = FakeSnapshot(
            subscription=stripe_subscription(
                period_end=self.period_end + relativedelta(months=3)
            )
        )
        self.assertViolated(
            "sub.period_matches_stripe", self.context(snapshot=snapshot)
        )

    def test_violated__sub_period_matches_stripe__when_stripe_moves_the_field(self):
        """The literal C1 shape: the period is not where we look for it."""
        snapshot = FakeSnapshot(
            subscription={"id": SUBSCRIPTION_ID, "items": {"data": []}}
        )
        outcome = self.assertViolated(
            "sub.period_matches_stripe", self.context(snapshot=snapshot)
        )
        self.assertIn("C1", outcome.detail)

    def test_violated__sub_status_matches_stripe(self):
        snapshot = FakeSnapshot(
            subscription=stripe_subscription(
                period_end=self.period_end, status="past_due"
            )
        )
        self.assertViolated(
            "sub.status_matches_stripe", self.context(snapshot=snapshot)
        )

    def test_violated__wallet_customer_id_matches(self):
        self.wallet.stripe_customer_id = "cus_someone_else"
        self.wallet.save(update_fields=["stripe_customer_id"])
        self.assertViolated("wallet.customer_id_matches", self.context())

    def test_violated__wallet_single_wallet(self):
        """As with sub.single_active: the OneToOne makes the bad state
        unreachable from here, so this proves the invariant's decision
        rather than Django's enforcement."""
        with patch("billing.live_qa.invariants_individual.CreditWallet") as mock_model:
            mock_model.objects.filter.return_value.count.return_value = 2
            self.assertViolated("wallet.single_wallet", self.context())

    def test_violated__bucket_used_within_total(self):
        bucket = CreditBucket.objects.filter(wallet=self.wallet).first()
        CreditBucket.objects.filter(pk=bucket.pk).update(
            used_credits=bucket.total_credits + 1
        )
        self.assertViolated("bucket.used_within_total", self.context())

    def test_violated__bucket_overage_never_expires(self):
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.OVERAGE,
            total_credits=500,
            used_credits=0,
            expires_at=self.now + timedelta(days=30),
        )
        self.assertViolated("bucket.overage_never_expires", self.context())

    def test_violated__bucket_monthly_count_monotonic(self):
        history = ActorHistory()
        history.observe(monthly_bucket_count=5)
        self.assertViolated(
            "bucket.monthly_count_monotonic", self.context(history=history)
        )

    def test_violated__txn_unique_per_invoice(self):
        # bulk_create so the partial unique constraint (which is exactly
        # what stops this in production) does not reject the second row —
        # the point is to prove the invariant NOTICES a double charge, not
        # to re-test Postgres.
        BillingTransaction.objects.bulk_create(
            [
                BillingTransaction(
                    source=BillingTransactionSource.INDIVIDUAL,
                    transaction_type=(
                        BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE
                    ),
                    user=self.user,
                    amount_cents=4_999,
                    stripe_invoice_id="in_duplicate",
                    occurred_at=timezone.now(),
                )
                for _ in range(2)
            ],
            ignore_conflicts=True,
        )
        if (
            BillingTransaction.objects.filter(
                user=self.user, stripe_invoice_id="in_duplicate"
            ).count()
            < 2
        ):
            # The constraint held, so drive the invariant's decision
            # directly rather than leaving its failing branch unproven.
            with patch(
                "billing.live_qa.invariants_individual.BillingTransaction"
            ) as mock_model:
                mock_model.objects.filter.return_value.exclude.return_value.values_list.return_value = [  # noqa: E501
                    "in_duplicate",
                    "in_duplicate",
                ]
                self.assertViolated("txn.unique_per_invoice", self.context())
            return

        self.assertViolated("txn.unique_per_invoice", self.context())

    def test_violated__credit_paid_period_invoice_grants_credits(self):
        """The money invariant: paid for three cycles, granted one."""
        snapshot = FakeSnapshot(
            paid_invoices=[paid_invoice(f"in_{i}") for i in range(3)]
        )
        self.assertViolated(
            "credit.paid_period_invoice_grants_credits", self.context(snapshot=snapshot)
        )

    def test_violated__event_no_failed(self):
        StripeEvent.objects.create(
            stripe_event_id="evt_failed",
            event_type="invoice.payment_succeeded",
            status=StripeEventStatus.FAILED,
            last_error="handler blew up on a real payload",
        )
        self.assertViolated(
            "event.no_failed", self.context(stripe_event_ids={"evt_failed"})
        )

    def test_violated__event_no_stuck_processing(self):
        StripeEvent.objects.create(
            stripe_event_id="evt_stuck",
            event_type="invoice.payment_succeeded",
            status=StripeEventStatus.PROCESSING,
            claimed_at=timezone.now()
            - STRIPE_EVENT_CLAIM_STALE_AFTER
            - timedelta(minutes=5),
        )
        self.assertViolated(
            "event.no_stuck_processing", self.context(stripe_event_ids={"evt_stuck"})
        )

    def test_violated__event_every_dispatched_is_recorded(self):
        self.assertViolated(
            "event.every_dispatched_is_recorded",
            self.context(stripe_event_ids={"evt_never_recorded"}),
        )


# --------------------------------------------------------------------------
# The healthy direction, per invariant
# --------------------------------------------------------------------------


class InvariantHoldsTests(InvariantTestBase):
    def test_all_individual_invariants_hold_for_a_healthy_subscriber(self):
        for key in sorted(INVARIANTS):
            if INVARIANTS[key].scope != "individual":
                continue
            with self.subTest(invariant=key):
                self.assertHolds(key, self.context())

    def test_event_invariants_hold_for_successfully_dispatched_events(self):
        StripeEvent.objects.create(
            stripe_event_id="evt_good",
            event_type="invoice.payment_succeeded",
            status=StripeEventStatus.SUCCEEDED,
            completed_at=timezone.now(),
        )
        ctx = self.context(stripe_event_ids={"evt_good"})
        for key in (
            "event.no_failed",
            "event.no_stuck_processing",
            "event.every_dispatched_is_recorded",
        ):
            with self.subTest(invariant=key):
                self.assertHolds(key, ctx)

    def test_a_fresh_processing_claim_is_not_stuck(self):
        """Only a STALE claim is a violation — an in-flight one is normal."""
        StripeEvent.objects.create(
            stripe_event_id="evt_inflight",
            event_type="invoice.payment_succeeded",
            status=StripeEventStatus.PROCESSING,
            claimed_at=timezone.now(),
        )
        self.assertHolds(
            "event.no_stuck_processing", self.context(stripe_event_ids={"evt_inflight"})
        )

    def test_pre_existing_failed_events_outside_this_run_are_ignored(self):
        """Flagging someone else's incident would train people to ignore
        this check."""
        StripeEvent.objects.create(
            stripe_event_id="evt_not_ours",
            event_type="invoice.payment_succeeded",
            status=StripeEventStatus.FAILED,
        )
        self.assertHolds("event.no_failed", self.context(stripe_event_ids={"evt_mine"}))

    def test_healthy_stripe_status_with_lagging_local_status_is_allowed(self):
        """Local status is written by webhooks and legitimately lags."""
        self.subscription.stripe_status = StripeSubscriptionStatus.PAST_DUE
        self.subscription.save(update_fields=["stripe_status"])
        snapshot = FakeSnapshot(
            subscription=stripe_subscription(
                period_end=self.period_end, status="active"
            )
        )
        self.assertHolds("sub.status_matches_stripe", self.context(snapshot=snapshot))

    def test_monthly_plan_without_grant_date_is_not_flagged(self):
        annual_only = "sub.grant_date_present_for_annual"
        self.subscription.next_credit_grant_at = None
        self.subscription.save(update_fields=["next_credit_grant_at"])
        outcome = self.run_one(annual_only, self.context())
        self.assertEqual(outcome.status, SKIPPED)

    def test_trial_period_is_not_compared_against_the_invoice(self):
        self.subscription.is_trial = True
        self.subscription.trial_end = self.now + timedelta(days=14)
        self.subscription.billing_cycle_end = self.subscription.trial_end
        self.subscription.save(
            update_fields=["is_trial", "trial_end", "billing_cycle_end"]
        )
        outcome = self.run_one("sub.period_matches_stripe", self.context())
        self.assertEqual(outcome.status, SKIPPED)


# --------------------------------------------------------------------------
# Engine behaviour
# --------------------------------------------------------------------------


class InvariantEngineTests(InvariantTestBase):
    def test_a_raising_invariant_is_an_ERROR_not_a_billing_violation(self):
        """A buggy check must never masquerade as a billing bug."""

        @invariant(
            "test.raises",
            scope="individual",
            cost="cheap",
            description="always raises",
        )
        def _boom(ctx):
            raise RuntimeError("check is broken")

        self.addCleanup(INVARIANTS.pop, "test.raises", None)

        outcome = self.run_one("test.raises", self.context())
        self.assertEqual(outcome.status, ERROR)
        self.assertIn("check is broken", outcome.detail)

    def test_an_invariant_returning_the_wrong_type_is_an_ERROR(self):
        @invariant(
            "test.bad_return",
            scope="individual",
            cost="cheap",
            description="returns nonsense",
        )
        def _bad(ctx):
            return "looks fine to me"

        self.addCleanup(INVARIANTS.pop, "test.bad_return", None)

        outcome = self.run_one("test.bad_return", self.context())
        self.assertEqual(outcome.status, ERROR)

    def test_duplicate_registration_is_rejected(self):
        with self.assertRaises(ValueError):

            @invariant(
                "sub.single_active",
                scope="individual",
                cost="cheap",
                description="dupe",
            )
            def _dupe(ctx):
                return InvariantOutcome(status=OK)

    def test_stripe_invariants_are_skipped_when_excluded(self):
        results = evaluate(self.context(), scopes=("individual",), include_stripe=False)
        keys = {r.key for r in results}
        self.assertNotIn("sub.period_matches_stripe", keys)
        self.assertIn("sub.single_active", keys)

    def test_skipped_is_neither_a_pass_nor_a_failure(self):
        ctx = self.context(subscription=None)
        outcome = self.run_one("sub.cycle_ordered", ctx)
        self.assertEqual(outcome.status, SKIPPED)
        self.assertFalse(outcome.failed)

    def test_results_convert_to_checks_carrying_the_observed_values(self):
        self.subscription.billing_cycle_end = self.subscription.billing_cycle_start
        self.subscription.save(update_fields=["billing_cycle_end"])

        results = evaluate(self.context(), keys={"sub.cycle_ordered"})
        check = results[0].to_check()

        self.assertFalse(check.passed)
        self.assertEqual(check.name, "sub.cycle_ordered")
        self.assertIn("observed:", check.detail)
        self.assertIn("step1", check.detail)

    def test_history_tracks_a_running_maximum_not_the_last_value(self):
        history = ActorHistory()
        history.observe(billing_cycle_end=self.period_end)
        history.observe(billing_cycle_end=self.period_end - relativedelta(months=2))
        self.assertEqual(history.max_billing_cycle_end, self.period_end)


# --------------------------------------------------------------------------
# Checkpoint wiring
# --------------------------------------------------------------------------


class FakeBus:
    def __init__(self):
        self.streams = {}

    def stream_for(self, customer_id):
        return self.streams.get(customer_id)


class FakeHarness:
    """Minimal stand-in: the InvariantRunner only needs a run_id, a bus,
    and a way to read a Stripe subscription."""

    def __init__(self, stripe_sub=None):
        self.run_id = "fakerun"
        self.bus = FakeBus()
        self._stripe_sub = stripe_sub
        # The concurrent runner reads this; declared here so the stand-in
        # matches the real harness's shape.
        self.invariants = None

    def retrieve_subscription(self, subscription_id):
        if self._stripe_sub is None:
            raise RuntimeError("no stripe in this test")
        return self._stripe_sub


class CheckpointTests(InvariantTestBase):
    """The checkpoint is what turns a catalogue of invariants into
    something that actually runs. If it silently no-ops, every invariant
    above is decoration."""

    def _runner(self, *, include_stripe=False, stripe_sub=None):
        from billing.live_qa.checkpoints import InvariantRunner

        harness = FakeHarness(stripe_sub=stripe_sub)
        runner = InvariantRunner(harness, include_stripe=include_stripe)
        runner.begin_scenario()
        runner.register_actor(
            CUSTOMER_ID, user=self.user, subscription_id=SUBSCRIPTION_ID
        )
        return runner

    def test_a_healthy_actor_produces_no_collected_violations(self):
        runner = self._runner()
        runner.checkpoint(CUSTOMER_ID, "step 1")
        self.assertEqual(runner.collect(), [])

    def test_a_violation_is_collected_for_the_scenario(self):
        self.subscription.billing_cycle_end = self.subscription.billing_cycle_start
        self.subscription.save(update_fields=["billing_cycle_end"])

        runner = self._runner()
        runner.checkpoint(CUSTOMER_ID, "step 1")

        collected = runner.collect()
        self.assertEqual([r.key for r in collected], ["sub.cycle_ordered"])
        self.assertIn("step 1", collected[0].to_check().detail)

    def test_an_unregistered_customer_is_a_no_op_not_an_error(self):
        """A payment-method-only actor has no subscription to evaluate."""
        runner = self._runner()
        self.assertEqual(runner.checkpoint("cus_unknown", "step"), [])
        self.assertEqual(runner.collect(), [])

    def test_checkpoint_never_raises_even_if_evaluation_blows_up(self):
        """An observation tool must not become a source of failures."""
        runner = self._runner()
        with patch(
            "billing.live_qa.checkpoints.evaluate", side_effect=RuntimeError("boom")
        ):
            self.assertEqual(runner.checkpoint(CUSTOMER_ID, "step"), [])

    def test_history_is_updated_after_evaluation_not_before(self):
        """Otherwise a monotonicity invariant would compare the current
        value against itself and could never fail."""
        runner = self._runner()
        runner.checkpoint(CUSTOMER_ID, "step 1")

        # Now move the cycle backwards, as a broken renewal would.
        self.subscription.billing_cycle_end = self.period_end - relativedelta(months=2)
        self.subscription.save(update_fields=["billing_cycle_end"])

        runner.checkpoint(CUSTOMER_ID, "step 2")

        keys = [r.key for r in runner.collect()]
        self.assertIn("sub.period_monotonic", keys)

    def test_stripe_invariants_are_skipped_when_stripe_is_disabled(self):
        runner = self._runner(include_stripe=False)
        results = runner.checkpoint(CUSTOMER_ID, "step")
        self.assertNotIn("sub.period_matches_stripe", {r.key for r in results})

    def test_a_scenario_that_passes_its_own_checks_still_fails_on_a_violation(self):
        """The point of the whole engine: a scenario asserting only what
        its author thought of must not be able to report success while
        leaving the database inconsistent."""
        from billing.live_qa.runner import _run_scenario
        from billing.stripe_live_qa import CheckRecorder

        self.subscription.billing_cycle_end = self.subscription.billing_cycle_start
        self.subscription.save(update_fields=["billing_cycle_end"])

        harness = FakeHarness()
        from billing.live_qa.checkpoints import InvariantRunner

        harness.invariants = InvariantRunner(harness, include_stripe=False)
        harness.invariants.register_actor(
            CUSTOMER_ID, user=self.user, subscription_id=SUBSCRIPTION_ID
        )

        def scenario(h):
            rec = CheckRecorder()
            rec.expect("the thing I thought to check", True)
            h.invariants.checkpoint(CUSTOMER_ID, "after my step")
            return rec

        result = _run_scenario(harness, "my_scenario", scenario)

        self.assertFalse(
            result.passed,
            "a scenario must not pass while an invariant is violated",
        )
        self.assertIn("sub.cycle_ordered", [c.name for c in result.checks])

    def test_violations_are_attributed_per_thread(self):
        """One harness is shared by every concurrent scenario, so a
        violation must never be credited to the wrong one."""
        import threading

        runner = self._runner()
        self.subscription.billing_cycle_end = self.subscription.billing_cycle_start
        self.subscription.save(update_fields=["billing_cycle_end"])

        other_thread_results = {}

        def other_thread():
            from django.db import connections

            try:
                runner.begin_scenario()
                other_thread_results["collected"] = runner.collect()
            finally:
                connections.close_all()

        runner.checkpoint(CUSTOMER_ID, "my step")
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join(timeout=10)

        self.assertTrue(runner.collect(), "this thread should own its violation")
        self.assertEqual(
            other_thread_results.get("collected"),
            [],
            "another thread must not inherit this thread's violations",
        )


# --------------------------------------------------------------------------
# Coverage guard
# --------------------------------------------------------------------------


class InvariantCoverageTests(TestCase):
    """Adding an invariant without a violation test must break the build.

    Without this, the natural failure mode is an invariant that quietly
    never fails — which manufactures confidence rather than providing it.
    """

    def test_every_registered_invariant_has_a_violation_test(self):
        tested = {
            name[len("test_violated__") :].split("__")[0]
            for name in dir(InvariantViolationTests)
            if name.startswith("test_violated__")
        }
        registered = {key.replace(".", "_") for key in INVARIANTS}

        missing = registered - tested
        self.assertEqual(
            missing,
            set(),
            "these invariants have no violation test, so nothing proves "
            f"they can fail: {sorted(missing)}",
        )

    def test_no_orphan_violation_tests(self):
        tested = {
            name[len("test_violated__") :].split("__")[0]
            for name in dir(InvariantViolationTests)
            if name.startswith("test_violated__")
        }
        registered = {key.replace(".", "_") for key in INVARIANTS}

        orphans = tested - registered
        self.assertEqual(
            orphans,
            set(),
            f"violation tests for invariants that no longer exist: {sorted(orphans)}",
        )
