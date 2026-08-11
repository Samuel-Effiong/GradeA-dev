"""
billing/tests/test_live_qa_scenario_registry.py
===============================================
Structural checks over the scenario registry, and a smoke run of every
scenario against a fully-faked Stripe.

WHAT THIS CAN AND CANNOT PROVE
------------------------------
A scenario's real value only exists against real Stripe — that is the
entire point of the suite, and no offline test can substitute for it.

What offline testing CAN do is catch the class of bug that would
otherwise be discovered at 1am: a misspelled method, a helper that no
longer exists, an argument that was renamed. Those are structural
mistakes, they are cheap to catch here, and catching them at 1am costs a
whole nightly cycle.

So each scenario is executed once against a harness that answers every
Stripe call with a plausible object. Assertions inside the scenario are
free to fail — that means nothing here. What must NOT happen is an
AttributeError, NameError or TypeError, because those are defects in the
scenario itself rather than findings about billing.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

import billing.live_qa  # noqa: F401  (registers fast + deep scenarios)
from billing.models import BillingInterval, PlanCategory, PlanTier, SubscriptionPlan
from billing.stripe_live_qa import CheckRecorder
from billing.stripe_live_qa_scenarios import (
    SCENARIO_TIERS,
    SCENARIOS,
    scenarios_for_tier,
)

TEST_KEY = "sk_test_fake"  # pragma: allowlist secret

STRUCTURAL_ERRORS = (AttributeError, NameError, TypeError, ImportError)


def make_plan(name, tier, interval, price_id, price_cents=999):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=name,
        category=PlanCategory.INDIVIDUAL,
        tier=tier,
        interval=interval,
        price_cents=price_cents,
        monthly_credits=10_000,
        stripe_price_id=price_id,
        carry_over_percent=0,
        carry_over_expiry_months=1,
        is_active=True,
    )


class ScenarioRegistryTests(TestCase):
    def test_every_scenario_is_callable(self):
        for name, fn in SCENARIOS.items():
            with self.subTest(scenario=name):
                self.assertTrue(callable(fn))

    def test_every_scenario_has_a_one_line_summary(self):
        """--list prints the first docstring line; a scenario without one
        shows up as a blank entry in the operator's only index."""
        for name, fn in SCENARIOS.items():
            with self.subTest(scenario=name):
                doc = (fn.__doc__ or "").strip()
                self.assertTrue(doc, f"{name} has no docstring")
                self.assertTrue(doc.splitlines()[0].strip())

    def test_every_scenario_has_a_tier(self):
        for name in SCENARIOS:
            with self.subTest(scenario=name):
                self.assertIn(SCENARIO_TIERS.get(name), ("fast", "deep"))

    def test_the_slow_scenarios_are_not_in_the_nightly_tier(self):
        """A nightly run that quietly includes hours of work stops being
        nightly."""
        fast = set(scenarios_for_tier("fast"))
        self.assertNotIn("long_horizon_monthly", fast)
        self.assertNotIn("long_horizon_annual", fast)

    def test_deep_includes_everything_fast_does(self):
        self.assertTrue(
            set(scenarios_for_tier("fast")) <= set(scenarios_for_tier("deep"))
        )

    def test_the_no_clock_advance_scenarios_are_all_fast(self):
        """These need no time travel, so there is no reason for them to
        sit in the slow tier."""
        for name in (
            "payment_method_lifecycle",
            "upgrade_proration_quote",
            "charge_refund_flow",
            "multiple_subscription_items",
            "discount_flow_through",
        ):
            with self.subTest(scenario=name):
                self.assertEqual(SCENARIO_TIERS.get(name), "fast")


class FakeSubscriber:
    """Whatever a scenario asks of a Subscriber, answered plausibly."""

    def __init__(self, user, plan):
        self.user = user
        self.plan = plan
        self.clock_id = "clock_fake"
        self.customer_id = "cus_fake"
        self.stripe_subscription_id = "sub_fake"

    def local(self):
        from billing.models import UserSubscription

        return UserSubscription.objects.filter(user=self.user).first()

    def wallet(self):
        from billing.models import CreditWallet

        return CreditWallet.objects.filter(user=self.user).first()

    def monthly_bucket_count(self):
        return 1


@override_settings(ENABLE_STRIPE_LIVE_QA=True, STRIPE_SECRET_KEY=TEST_KEY)
class ScenarioSmokeTests(TestCase):
    """Execute every FAST scenario against a fully-faked Stripe.

    Assertion failures inside a scenario are irrelevant here. A
    structural error is not: it means the scenario would have crashed on
    its first real run, wasting a nightly cycle to tell us something a
    unit test could have.
    """

    def setUp(self):
        make_plan("STANDARD", PlanTier.STANDARD, BillingInterval.MONTHLY, "price_std")
        make_plan("PRO", PlanTier.PRO, BillingInterval.MONTHLY, "price_pro", 4_999)
        make_plan(
            "STANDARD_ANNUAL",
            PlanTier.STANDARD,
            BillingInterval.ANNUAL,
            "price_std_annual",
            9_999,
        )

        from django.contrib.auth import get_user_model

        from users.models import UserTypes

        self.user = get_user_model().objects.create_user(
            email="smoke@stripe-live-qa.invalid",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )

    def _harness(self):
        harness = MagicMock()
        harness.run_id = "smoke"
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
        return harness

    def _fake_guarded_call(self, fn, *args, **kwargs):
        """Answer any Stripe call with something dict-shaped enough that a
        scenario can walk it without exploding."""
        return {
            "id": "obj_fake",
            "data": [{"id": "in_fake", "amount_paid": 1000, "status": "paid"}],
            "amount_paid": 1000,
            "amount_remaining": 0,
            "amount_total": 500,
            "status": "paid",
            "invoice_settings": {"default_payment_method": "pm_fake"},
            "metadata": {},
            "items": {"data": [{"id": "si_1", "price": {"id": "price_std"}}]},
            "payment_intent": {"id": "pi_fake"},
        }

    def _patches(self):
        fake_sub = FakeSubscriber(self.user, SubscriptionPlan.objects.first())
        return [
            patch(
                "billing.live_qa.scenarios_fast.guarded_call",
                side_effect=self._fake_guarded_call,
            ),
            patch(
                "billing.live_qa.scenarios_fast._establish_subscriber",
                return_value=fake_sub,
            ),
            patch(
                "billing.live_qa.scenarios_clock.guarded_call",
                side_effect=self._fake_guarded_call,
            ),
            patch(
                "billing.live_qa.scenarios_clock._establish_subscriber",
                return_value=fake_sub,
            ),
            patch(
                "billing.live_qa.scenarios_deep.guarded_call",
                side_effect=self._fake_guarded_call,
            ),
            patch(
                "billing.live_qa.scenarios_deep._establish_subscriber",
                return_value=fake_sub,
            ),
            patch(
                "billing.live_qa.clock.guarded_call",
                side_effect=self._fake_guarded_call,
            ),
            patch(
                "billing.stripe_live_qa_scenarios.guarded_call",
                side_effect=self._fake_guarded_call,
            ),
            patch(
                "billing.stripe_live_qa_scenarios._establish_subscriber",
                return_value=fake_sub,
            ),
        ]

    def _run_smoke(self, names):
        harness = self._harness()

        for name in sorted(names):
            fn = SCENARIOS[name]
            with self.subTest(scenario=name):
                patchers = [p.start() for p in self._patches()]
                try:
                    try:
                        result = fn(harness)
                    except STRUCTURAL_ERRORS as exc:
                        self.fail(
                            f"{name} has a structural defect and would crash on "
                            f"its first real run: {exc!r}"
                        )
                    except Exception:
                        # Any other exception is the scenario reacting to
                        # implausible fake data — not a defect in itself.
                        continue
                finally:
                    for p in patchers:
                        p.stop()

                self.assertIsInstance(
                    result,
                    CheckRecorder,
                    f"{name} must return a CheckRecorder so its findings "
                    "reach the report",
                )

    def test_every_fast_scenario_runs_without_a_structural_error(self):
        self._run_smoke(scenarios_for_tier("fast"))

    def test_every_deep_only_scenario_runs_without_a_structural_error(self):
        """Everything scenarios_for_tier("fast") does not already cover —
        including the many-cycle horizon scenarios, whose per-period logic
        can still hit a structural error on the very first iteration."""
        deep_only = set(scenarios_for_tier("deep")) - set(scenarios_for_tier("fast"))
        self.assertTrue(deep_only, "expected at least one deep-only scenario")
        self._run_smoke(deep_only)
