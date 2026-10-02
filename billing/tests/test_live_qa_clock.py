"""
billing/tests/test_live_qa_clock.py
===================================
Long-horizon advancing, Stripe's test-clock ceiling, and simulated local
time. All mocked, no network.

THE THREE THINGS THAT MUST NOT GO WRONG
---------------------------------------
1. Hitting Stripe's ceiling must be reported as a NOTE, never a failure.
   The limit is undocumented and outside our control; a red nightly for
   "Stripe would not let us go further" is noise that trains people to
   ignore real failures.

2. Ceiling detection must be BEHAVIOURAL, not string-matched. Stripe can
   reword an error message at any time, and a string-gated classifier
   would turn that into a fake billing failure.

3. Local time must move WITH Stripe's clock. Otherwise every
   local-clock-driven task (annual credit grants, trial expiry, the
   reconcile sweep) matches nothing and passes vacuously — and an annual
   simulation would REPRODUCE Phase 0's Bug 2 instead of detecting it.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from billing.live_qa.clock import (
    CEILING_ABSOLUTE,
    CEILING_DISTANCE,
    CEILING_UNKNOWN,
    REACHED_BUDGET,
    REACHED_INFRASTRUCTURE,
    REACHED_NO_PERIOD,
    REACHED_STRIPE_CEILING,
    REACHED_TARGET,
    CeilingProbe,
    HorizonOutcome,
    LongHorizonRunner,
    QuiescenceError,
    run_local_clock_tasks,
    sim_now,
)
from billing.live_qa.concurrency import Deadline
from billing.stripe_live_qa import LiveQATimeout

TEST_KEY = "sk_test_fake"  # pragma: allowlist secret


def enabled(**extra):
    options = {"ENABLE_STRIPE_LIVE_QA": True, "STRIPE_SECRET_KEY": TEST_KEY}
    options.update(extra)
    return override_settings(**options)


def stripe_sub_with_period(period_end_ts):
    return {
        "id": "sub_h",
        "status": "active",
        "items": {
            "data": [
                {
                    "id": "si_1",
                    "current_period_start": period_end_ts - 2_592_000,
                    "current_period_end": period_end_ts,
                }
            ]
        },
    }


class FakeHarness:
    """Simulates a Stripe test clock in memory: advancing moves frozen
    time, and the subscription's period end follows it."""

    PERIOD_SECONDS = 2_592_000  # ~30 days

    def __init__(
        self,
        *,
        start=1_000_000,
        ceiling=None,
        per_advance_limit=None,
        no_period=False,
        hang=False,
    ):
        self.run_id = "clockrun"
        self.frozen = start
        self.ceiling = ceiling
        self.per_advance_limit = per_advance_limit
        # no_period: Stripe stops reporting a billing period (subscription
        # ended, or the field moved — the C1 shape).
        # hang: the clock never becomes ready, i.e. an infrastructure fault
        # that must NOT be mistaken for a ceiling.
        self.no_period = no_period
        self.hang = hang
        self.period_end = start + self.PERIOD_SECONDS
        self.advances: list = []
        self.drains: list = []
        self.invariants = None

    # -- clock -----------------------------------------------------------

    def advance_clock_to(self, clock_id, target):
        if self.hang:
            raise LiveQATimeout("clock never became ready")
        target = int(target)
        if self.ceiling is not None and target > self.ceiling:
            raise RuntimeError("stripe refuses: beyond the ceiling")
        if (
            self.per_advance_limit is not None
            and (target - self.frozen) > self.per_advance_limit
        ):
            raise RuntimeError("stripe refuses: advance too large")
        self.advances.append(target)
        self.frozen = target
        # Stripe rolls the period forward once the boundary is crossed.
        while self.period_end <= self.frozen:
            self.period_end += self.PERIOD_SECONDS
        return {"id": clock_id, "status": "ready", "frozen_time": self.frozen}

    def retrieve_subscription(self, subscription_id):
        if self.no_period:
            return {"id": subscription_id, "items": {"data": []}}
        return stripe_sub_with_period(self.period_end)

    def drain_events(self, *, customer_id):
        self.drains.append(customer_id)
        return []


def patched_stripe(harness):
    """Patch the module-level stripe used by clock.py so TestClock
    retrieve/advance reflect the fake harness."""
    mock = MagicMock()
    mock.api_key = TEST_KEY

    def retrieve(clock_id, *a, **kw):
        return {"id": clock_id, "status": "ready", "frozen_time": harness.frozen}

    mock.test_helpers.TestClock.retrieve.side_effect = retrieve
    return mock


# --------------------------------------------------------------------------
# HorizonOutcome
# --------------------------------------------------------------------------


class HorizonOutcomeTests(TestCase):
    def test_reaching_stripes_ceiling_is_not_a_failure(self):
        """It is a fact about how far the run got, not a defect."""
        outcome = HorizonOutcome(
            reached=REACHED_STRIPE_CEILING, periods_advanced=41, simulated_seconds=10**8
        )
        self.assertFalse(outcome.is_failure)
        self.assertIn("ceiling", outcome.as_note())

    def test_exhausting_the_budget_is_not_a_failure(self):
        outcome = HorizonOutcome(reached=REACHED_BUDGET, periods_advanced=3)
        self.assertFalse(outcome.is_failure)

    def test_an_infrastructure_fault_is_a_failure(self):
        outcome = HorizonOutcome(reached=REACHED_INFRASTRUCTURE, detail="poller died")
        self.assertTrue(outcome.is_failure)

    def test_simulated_years_are_reported(self):
        outcome = HorizonOutcome(
            reached=REACHED_TARGET, simulated_seconds=int(365.25 * 24 * 3600 * 3)
        )
        self.assertEqual(outcome.simulated_years, 3.0)

    def test_the_note_names_the_distance_travelled(self):
        outcome = HorizonOutcome(
            reached=REACHED_TARGET, periods_advanced=12, simulated_seconds=31_557_600
        )
        note = outcome.as_note()
        self.assertIn("12", note)
        self.assertIn("1.0", note)


# --------------------------------------------------------------------------
# Ceiling probe
# --------------------------------------------------------------------------


@enabled()
class CeilingProbeTests(TestCase):
    def setUp(self):
        self.harness = FakeHarness()
        patcher = patch("billing.live_qa.clock.stripe", patched_stripe(self.harness))
        patcher.start()
        self.addCleanup(patcher.stop)
        guard = patch("billing.stripe_live_qa.stripe")
        self.guard = guard.start()
        self.addCleanup(guard.stop)
        self.guard.api_key = TEST_KEY

    def test_a_working_small_advance_means_a_distance_limit(self):
        """The difference between truncating at year 3 and reaching year
        10 with smaller steps."""
        self.harness.per_advance_limit = 7200

        result = CeilingProbe(self.harness).classify(
            "clock_1", self.harness.frozen + 10**7, RuntimeError("too large")
        )

        self.assertEqual(result.kind, CEILING_DISTANCE)

    def test_a_refused_small_advance_means_an_absolute_wall(self):
        self.harness.ceiling = self.harness.frozen  # nothing further allowed

        result = CeilingProbe(self.harness).classify(
            "clock_1", self.harness.frozen + 10**7, RuntimeError("nope")
        )

        self.assertEqual(result.kind, CEILING_ABSOLUTE)

    def test_a_clock_that_actually_moved_was_a_transient_rejection(self):
        result = CeilingProbe(self.harness).classify(
            "clock_1", self.harness.frozen - 10, RuntimeError("spurious")
        )
        self.assertEqual(result.kind, CEILING_UNKNOWN)
        self.assertIn("actually moved", result.detail)

    def test_classification_does_not_depend_on_stripes_wording(self):
        """A string-gated classifier would turn a Stripe copy edit into a
        red nightly for a bug that does not exist."""
        self.harness.ceiling = self.harness.frozen

        for message in ("some entirely new wording", "", "ERR_9931"):
            with self.subTest(message=message):
                result = CeilingProbe(self.harness).classify(
                    "clock_1", self.harness.frozen + 10**7, RuntimeError(message)
                )
                self.assertEqual(result.kind, CEILING_ABSOLUTE)


# --------------------------------------------------------------------------
# LongHorizonRunner
# --------------------------------------------------------------------------


@enabled()
class LongHorizonRunnerTests(TestCase):
    def setUp(self):
        self.harness = FakeHarness()
        patcher = patch("billing.live_qa.clock.stripe", patched_stripe(self.harness))
        patcher.start()
        self.addCleanup(patcher.stop)
        guard = patch("billing.stripe_live_qa.stripe")
        self.guard = guard.start()
        self.addCleanup(guard.stop)
        self.guard.api_key = TEST_KEY

    def _runner(
        self,
        *,
        max_periods: int = 5,
        run_local_tasks: bool = False,
        deadline=None,
    ):
        return LongHorizonRunner(
            self.harness,
            clock_id="clock_1",
            customer_id="cus_1",
            subscription_id="sub_h",
            max_periods=max_periods,
            run_local_tasks=run_local_tasks,
            deadline=deadline,
        )

    def test_advances_one_period_at_a_time(self):
        """Not one big jump: every renewal is a real code path we want to
        execute, and a single leap would skip them all while looking like
        it covered the distance."""
        outcome = self._runner(max_periods=5).run()

        self.assertEqual(outcome.reached, REACHED_TARGET)
        self.assertEqual(outcome.periods_advanced, 5)
        self.assertEqual(len(self.harness.advances), 5)

    def test_drains_events_after_every_period(self):
        self._runner(max_periods=4).run()
        self.assertEqual(self.harness.drains, ["cus_1"] * 4)

    def test_simulated_time_accumulates(self):
        outcome = self._runner(max_periods=3).run()
        self.assertGreaterEqual(
            outcome.simulated_seconds, 3 * FakeHarness.PERIOD_SECONDS
        )

    def test_an_absolute_ceiling_stops_the_run_as_a_note(self):
        self.harness.ceiling = self.harness.frozen + FakeHarness.PERIOD_SECONDS + 100

        outcome = self._runner(max_periods=10).run()

        self.assertEqual(outcome.reached, REACHED_STRIPE_CEILING)
        self.assertFalse(outcome.is_failure)
        self.assertGreaterEqual(outcome.periods_advanced, 1)

    def test_a_distance_limit_is_worked_around_by_halving(self):
        """Stripe capping per-advance distance must not truncate the run."""
        self.harness.per_advance_limit = int(FakeHarness.PERIOD_SECONDS * 0.6)

        outcome = self._runner(max_periods=3).run()

        self.assertEqual(outcome.reached, REACHED_TARGET)
        self.assertGreater(len(self.harness.advances), 3)

    def test_the_wall_clock_budget_stops_the_run_as_a_note(self):
        outcome = self._runner(max_periods=50, deadline=Deadline(0)).run()
        self.assertEqual(outcome.reached, REACHED_BUDGET)
        self.assertFalse(outcome.is_failure)

    def test_a_missing_billing_period_stops_the_run_and_says_why(self):
        """Either the subscription ended, or Stripe moved the field —
        the C1 failure. Both need saying, not silent truncation."""
        self.harness.no_period = True

        outcome = self._runner(max_periods=5).run()

        self.assertEqual(outcome.reached, REACHED_NO_PERIOD)
        self.assertIn("C1", outcome.detail)

    def test_a_clock_that_never_becomes_ready_is_infrastructure_not_a_ceiling(self):
        """A hung clock is our problem to fix; papering over it as a
        ceiling would hide a real fault."""

        self.harness.hang = True

        with self.assertRaises(LiveQATimeout):
            self._runner(max_periods=2).run()

    def test_local_tasks_run_after_each_period_when_enabled(self):
        with patch("billing.live_qa.clock.run_local_clock_tasks") as run_tasks:
            run_tasks.return_value = {}
            self._runner(max_periods=3, run_local_tasks=True).run()

        self.assertEqual(run_tasks.call_count, 3)


# --------------------------------------------------------------------------
# Simulated local time
# --------------------------------------------------------------------------


class SimNowTests(TestCase):
    def test_local_time_moves_to_the_simulated_moment(self):
        """Without this every local-clock task matches nothing and passes
        vacuously — an annual run would reproduce Bug 2, not detect it."""
        future = timezone.now() + timezone.timedelta(days=400)

        with sim_now(future):
            self.assertEqual(timezone.now(), future)

        self.assertLess(timezone.now(), future)

    def test_refuses_to_patch_global_time_while_workers_are_running(self):
        """Patching process-global state mid-run would silently corrupt
        every other actor's results, which is worse than crashing."""
        pool = MagicMock()
        pool.active_count = 3

        with self.assertRaises(QuiescenceError):
            with sim_now(timezone.now(), pool=pool):
                pass

    def test_allows_patching_once_the_pool_is_parked(self):
        pool = MagicMock()
        pool.active_count = 0

        with sim_now(timezone.now(), pool=pool):
            pass  # must not raise


class RunLocalClockTasksTests(TestCase):
    def test_each_task_runs_under_simulated_time(self):
        seen = {}

        def fake_task():
            seen["now"] = timezone.now()
            return "ok"

        fake_task.__name__ = "fake_task"
        moment = datetime(2030, 6, 1, tzinfo=dt_timezone.utc)

        summaries = run_local_clock_tasks(moment, tasks=(fake_task,))

        self.assertEqual(seen["now"], moment)
        self.assertEqual(summaries["fake_task"], "ok")

    def test_one_raising_task_does_not_stop_the_rest(self):
        def bad():
            raise RuntimeError("task exploded")

        def good():
            return "fine"

        bad.__name__ = "bad"
        good.__name__ = "good"

        summaries = run_local_clock_tasks(timezone.now(), tasks=(bad, good))

        self.assertIn("RAISED", summaries["bad"])
        self.assertEqual(summaries["good"], "fine")

    def test_the_real_default_task_set_is_the_local_clock_driven_ones(self):
        """These are exactly the tasks that would pass vacuously if only
        Stripe's clock moved."""
        import inspect

        from billing.live_qa import clock as clock_module

        source = inspect.getsource(clock_module.run_local_clock_tasks)
        for task_name in (
            "process_annual_plan_credit_grants",
            "expire_active_trials",
            "cleanup_expired_credit_buckets",
            "reconcile_subscription_renewals",
        ):
            self.assertIn(task_name, source)


# --------------------------------------------------------------------------
# Tiering
# --------------------------------------------------------------------------


class ScenarioTierTests(TestCase):
    def test_long_horizon_scenarios_are_registered_as_deep(self):
        import billing.live_qa  # noqa: F401  (registers them)
        from billing.stripe_live_qa_scenarios import SCENARIO_TIERS

        self.assertEqual(SCENARIO_TIERS["long_horizon_monthly"], "deep")
        self.assertEqual(SCENARIO_TIERS["long_horizon_annual"], "deep")

    def test_fast_tier_excludes_the_hours_long_scenarios(self):
        import billing.live_qa  # noqa: F401
        from billing.stripe_live_qa_scenarios import scenarios_for_tier

        fast = scenarios_for_tier("fast")
        self.assertNotIn("long_horizon_monthly", fast)
        self.assertIn("renewals", fast)

    def test_deep_tier_is_a_superset_of_fast(self):
        """A weekly run must do everything a nightly run does."""
        import billing.live_qa  # noqa: F401
        from billing.stripe_live_qa_scenarios import scenarios_for_tier

        self.assertTrue(
            set(scenarios_for_tier("fast")) <= set(scenarios_for_tier("deep"))
        )

    def test_duplicate_scenario_registration_is_rejected(self):
        from billing.stripe_live_qa_scenarios import register_scenarios

        with self.assertRaises(ValueError):
            register_scenarios({"renewals": lambda h: None})
