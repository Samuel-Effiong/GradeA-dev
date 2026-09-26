"""
billing/tests/test_chaos.py
============================
Offline coverage for billing/live_qa/chaos.py: everything that does NOT
need real Stripe — determinism, the prefix guarantee the shrinker relies
on, and the shrinker's binary search itself (algorithmically, against a
stubbed run_chaos_walk so it costs nothing and proves the search logic
independent of Stripe).

A real chaos walk (run_chaos_walk executing against real Stripe) is
exercised structurally in ScenarioSmokeTests-style fashion here too, the
same "fully-faked Stripe, only structural errors fail the test" contract
as test_live_qa_scenario_registry.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from billing.live_qa import chaos
from billing.live_qa.invariants import InvariantResult, violated
from billing.models import BillingInterval, PlanCategory, PlanTier, SubscriptionPlan

TEST_KEY = "sk_test_fake"  # pragma: allowlist secret

STRUCTURAL_ERRORS = (AttributeError, NameError, TypeError, ImportError)


class GenerateSequenceTests(TestCase):
    def test_same_seed_and_steps_is_deterministic(self):
        a = chaos.generate_sequence(42, 20)
        b = chaos.generate_sequence(42, 20)
        self.assertEqual(a, b)

    def test_a_shorter_draw_is_an_exact_prefix_of_a_longer_one(self):
        """The shrinker's binary search depends entirely on this holding
        for every seed, not just one lucky example."""
        for seed in (1, 2, 42, 12345, 999999):
            with self.subTest(seed=seed):
                long = chaos.generate_sequence(seed, 30)
                for k in (1, 5, 10, 29, 30):
                    short = chaos.generate_sequence(seed, k)
                    self.assertEqual(
                        short,
                        long[:k],
                        f"seed={seed} k={k}: shorter draw is not a prefix",
                    )

    def test_different_seeds_usually_differ(self):
        sequences = {tuple(chaos.generate_sequence(s, 20)) for s in range(10)}
        self.assertGreater(
            len(sequences), 1, "10 different seeds produced the same sequence"
        )

    def test_every_produced_name_is_in_the_action_pool(self):
        seq = chaos.generate_sequence(7, 50)
        for name in seq:
            with self.subTest(action=name):
                self.assertIn(name, chaos.DEFAULT_ACTIONS)


class ActionPoolTests(TestCase):
    def test_every_action_is_callable_with_a_positive_weight(self):
        for name, (weight, fn) in chaos.DEFAULT_ACTIONS.items():
            with self.subTest(action=name):
                self.assertTrue(callable(fn))
                self.assertIsInstance(weight, int)
                self.assertGreater(weight, 0)

    def test_cancel_is_in_the_pool(self):
        """The one action every other action must gracefully no-op after —
        losing it silently would mean the guard logic never gets exercised."""
        self.assertIn("cancel", chaos.DEFAULT_ACTIONS)


class ChaosWalkResultTests(TestCase):
    def _violation(self, key="test.invariant", step_label="step 3"):
        return InvariantResult(
            key=key,
            outcome=violated("boom", value="x"),
            step_label=step_label,
        )

    def test_no_violations_means_not_failed(self):
        result = chaos.ChaosWalkResult(seed=1, steps=5)
        result.executed = [
            chaos.ExecutedStep(index=0, action="advance_boundary", note="ok")
        ]
        self.assertFalse(result.failed)
        self.assertIsNone(result.first_violation_step())

    def test_a_violation_marks_the_walk_failed_and_locates_the_step(self):
        result = chaos.ChaosWalkResult(seed=1, steps=5)
        result.executed = [
            chaos.ExecutedStep(index=0, action="advance_boundary", note="ok"),
            chaos.ExecutedStep(
                index=1,
                action="toggle_payment_failure",
                note="ok",
                new_violations=[self._violation()],
            ),
        ]
        self.assertTrue(result.failed)
        self.assertEqual(result.first_violation_step(), 1)
        self.assertEqual(len(result.violations), 1)

    def test_infra_error_alone_marks_the_walk_failed(self):
        result = chaos.ChaosWalkResult(seed=1, steps=5, infra_error="boom")
        self.assertTrue(result.failed)


class ShrinkAlgorithmTests(TestCase):
    """Proves the binary search converges to the correct minimal step
    count, entirely offline: run_chaos_walk is stubbed with a pure
    function of `steps`, never touching Stripe."""

    def _stub_walk(self, fails_at):
        """A fake run_chaos_walk: fails (produces one violation) for any
        `steps >= fails_at`, per the prefix-monotonicity guarantee."""

        def _fake(seed, steps, *, actions=None, keep_objects=False):
            result = chaos.ChaosWalkResult(seed=seed, steps=steps)
            if steps >= fails_at:
                result.executed = [
                    chaos.ExecutedStep(
                        index=fails_at - 1,
                        action="cancel",
                        note="boom",
                        new_violations=[
                            InvariantResult(
                                key="fake.invariant",
                                outcome=violated("boom"),
                            )
                        ],
                    )
                ]
            return result

        return _fake

    def test_finds_the_exact_minimal_step_count(self):
        for fails_at in (1, 2, 7, 15, 30):
            with self.subTest(fails_at=fails_at):
                with patch.object(
                    chaos, "run_chaos_walk", side_effect=self._stub_walk(fails_at)
                ):
                    result = chaos.shrink_chaos_failure(
                        seed=1, original_steps=30, max_reruns=20
                    )
                self.assertEqual(result.minimal_steps, fails_at)

    def test_bounded_by_max_reruns(self):
        with patch.object(
            chaos, "run_chaos_walk", side_effect=self._stub_walk(30)
        ) as mock_walk:
            chaos.shrink_chaos_failure(seed=1, original_steps=30, max_reruns=4)
        self.assertLessEqual(mock_walk.call_count, 4)

    def test_no_repro_on_rerun_returns_none(self):
        def _never_fails(seed, steps, *, actions=None, keep_objects=False):
            return chaos.ChaosWalkResult(seed=seed, steps=steps)

        with patch.object(chaos, "run_chaos_walk", side_effect=_never_fails):
            result = chaos.shrink_chaos_failure(seed=1, original_steps=30)
        self.assertIsNone(result.minimal_steps)


def _make_plans():
    SubscriptionPlan.objects.create(
        name="STANDARD",
        display_name="Standard",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.STANDARD,
        interval=BillingInterval.MONTHLY,
        price_cents=999,
        monthly_credits=10_000,
        stripe_price_id="price_std",
        carry_over_percent=0,
        carry_over_expiry_months=1,
        is_active=True,
    )
    SubscriptionPlan.objects.create(
        name="PRO",
        display_name="Pro",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.PRO,
        interval=BillingInterval.MONTHLY,
        price_cents=4_999,
        monthly_credits=50_000,
        stripe_price_id="price_pro",
        carry_over_percent=0,
        carry_over_expiry_months=1,
        is_active=True,
    )
    SubscriptionPlan.objects.create(
        name="STANDARD_ANNUAL",
        display_name="Standard Annual",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.STANDARD,
        interval=BillingInterval.ANNUAL,
        price_cents=9_999,
        monthly_credits=10_000,
        stripe_price_id="price_std_annual",
        carry_over_percent=0,
        carry_over_expiry_months=1,
        is_active=True,
    )


@override_settings(ENABLE_STRIPE_LIVE_QA=True, STRIPE_SECRET_KEY=TEST_KEY)
class ChaosWalkSmokeTest(TestCase):
    """Same contract as ScenarioSmokeTests: a fully-faked Stripe, and only
    a structural error (typo, renamed helper) fails the test."""

    def setUp(self):
        _make_plans()

        from django.contrib.auth import get_user_model

        from users.models import UserTypes

        self.user = get_user_model().objects.create_user(
            email="chaos-smoke@stripe-live-qa.invalid",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )

    def _fake_subscriber(self, *a, **k):
        from billing.stripe_live_qa_scenarios import Subscriber

        return Subscriber(
            user=self.user,
            clock_id="clock_fake",
            customer_id="cus_fake",
            stripe_subscription_id="sub_fake",
            plan=SubscriptionPlan.objects.first(),
        )

    def _fake_guarded_call(self, fn, *args, **kwargs):
        return {
            "id": "obj_fake",
            "data": [{"id": "in_fake", "amount_paid": 1000, "status": "paid"}],
            "amount_paid": 1000,
            "status": "paid",
            "items": {"data": [{"id": "si_1", "price": {"id": "price_std"}}]},
            "payment_intent": {"id": "pi_fake"},
        }

    def test_a_short_walk_runs_without_a_structural_error(self):
        harness = MagicMock()
        harness.run_id = "chaos-smoke"
        harness.invariants = None
        harness.create_local_user.return_value = self.user
        harness.create_test_clock.return_value = {"id": "clock_fake"}
        harness.create_customer.return_value = {"id": "cus_fake"}
        harness.attach_card.return_value = {"id": "pm_fake"}
        harness.create_subscription.return_value = {
            "id": "sub_fake",
            "status": "active",
            "items": {
                "data": [
                    {
                        "id": "si_1",
                        "price": {"id": "price_std"},
                        "current_period_start": 1_000_000,
                        "current_period_end": 1_002_592,
                    }
                ]
            },
        }
        harness.retrieve_subscription.return_value = (
            harness.create_subscription.return_value
        )
        harness.drain_events.return_value = []

        with patch(
            "billing.live_qa.chaos.ConcurrentLiveQAHarness", return_value=harness
        ), patch(
            "billing.live_qa.chaos.guarded_call", side_effect=self._fake_guarded_call
        ), patch(
            "billing.live_qa.chaos._establish_subscriber",
            side_effect=self._fake_subscriber,
        ):
            try:
                result = chaos.run_chaos_walk(1, 8)
            except STRUCTURAL_ERRORS as exc:
                self.fail(
                    f"chaos walk has a structural defect and would crash on "
                    f"its first real run: {exc!r}"
                )

        self.assertIsInstance(result, chaos.ChaosWalkResult)
        self.assertEqual(result.steps, 8)
        # Every generated action must have produced SOME executed step
        # (even a "skipped: ..." note is fine) -- an action silently
        # missing from `executed` would mean its function crashed before
        # reaching the append, swallowed by the per-action try/except.
        self.assertEqual(len(result.executed), 8)
