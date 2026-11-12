"""The sales-leads list takes the user list's plan and last-login filters
(beta bug fix, owner-approved 2026-11-12).

GET analytics beta/intent-signals (super admin only) gains last_login_after,
last_login_before, plan_tier, plan_category, plan_interval and has_plan. Each
means exactly what it means on GET /api/v1/users/: the matching teachers are
found with the user list's own code (users.user_list.ensure), so the current
plan is the one billing resolves. With none of them in the request the order
and payload are what they were. Every parameter of the endpoint also has a
plain description on Swagger.

    python manage.py test billing.tests.test_intent_signals_lead_filters
"""

import json
from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from billing.models import (
    BetaProfile,
    BillingInterval,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    SchoolCreditAllocation,
    SubscriptionPlan,
    UserSubscription,
)
from classrooms.models import School
from users.models import CustomUser

from . import test_intent_signals as base
from .test_intent_signals_pins import NOW

URL = "analytics-intent-signals"


class LeadFilterBase(base.LeadsBase):
    _plans = 0

    def plan(self, tier, category=PlanCategory.INDIVIDUAL, interval=None):
        LeadFilterBase._plans += 1
        return SubscriptionPlan.objects.create(
            name=f"Lead plan {LeadFilterBase._plans}",
            category=category,
            tier=tier,
            interval=interval or BillingInterval.MONTHLY,
            monthly_credits=10_000,
            is_active=True,
        )

    def subscribe(self, user, plan):
        return UserSubscription.objects.create(
            user=user,
            plan=plan,
            is_active=True,
            billing_cycle_start=NOW,
            billing_cycle_end=NOW + timedelta(days=30),
        )

    def seat(self, user, plan):
        """A school license seat: the current plan comes first from here."""
        license_sub = LicenseSubscription.objects.create(
            school=School.objects.create(name=f"School {plan.pk}"),
            admin_user=self.admin,
            plan=plan,
            contract_months=12,
            max_seats=10,
            billing_cycle_start=NOW,
            billing_cycle_end=NOW + timedelta(days=365),
            is_active=True,
            auto_renew=True,
        )
        return SchoolCreditAllocation.objects.create(
            license_subscription=license_sub,
            user=user,
            monthly_allocation=20_000,
            is_active=True,
            is_admin_allocation=False,
            next_credit_grant_at=NOW + timedelta(days=30),
        )

    def signed_in(self, user, when):
        CustomUser.objects.filter(pk=user.pk).update(last_login=when)

    def build_plans(self):
        """pro: own PRO monthly subscription. seat: a license STANDARD annual
        seat. none: no plan at all."""
        self.pro = self.teacher(10.0)
        self.subscribe(self.pro, self.plan(PlanTier.PRO))
        self.seat_holder = self.teacher(20.0)
        self.seat(
            self.seat_holder,
            self.plan(
                PlanTier.STANDARD,
                category=PlanCategory.LICENSE,
                interval=BillingInterval.ANNUAL,
            ),
        )
        self.none = self.teacher(30.0)
        return {
            "pro": self.pro.email,
            "seat": self.seat_holder.email,
            "none": self.none.email,
        }

    def kept(self, **params):
        return set(self.emails(**params))


class PlanFiltersTests(LeadFilterBase):
    def setUp(self):
        super().setUp()
        self.who = self.build_plans()

    def test_plan_tier(self):
        self.assertEqual(self.kept(plan_tier="PRO"), {self.who["pro"]})
        self.assertEqual(self.kept(plan_tier="STANDARD"), {self.who["seat"]})
        self.assertEqual(self.kept(plan_tier="POWER"), set())

    def test_plan_category(self):
        self.assertEqual(self.kept(plan_category="INDIVIDUAL"), {self.who["pro"]})
        self.assertEqual(self.kept(plan_category="LICENSE"), {self.who["seat"]})

    def test_plan_interval(self):
        self.assertEqual(self.kept(plan_interval="MONTHLY"), {self.who["pro"]})
        self.assertEqual(self.kept(plan_interval="ANNUAL"), {self.who["seat"]})

    def test_has_plan(self):
        self.assertEqual(
            self.kept(has_plan="true"), {self.who["pro"], self.who["seat"]}
        )
        self.assertEqual(self.kept(has_plan="false"), {self.who["none"]})

    def test_the_seat_wins_over_an_own_subscription(self):
        """Billing's order: a license seat as a teacher comes first."""
        self.subscribe(self.seat_holder, self.plan(PlanTier.POWER))
        self.assertEqual(self.kept(plan_tier="STANDARD"), {self.who["seat"]})
        self.assertNotIn(self.who["seat"], self.kept(plan_tier="POWER"))

    def test_a_plan_filter_and_a_signal_filter_combine(self):
        for email in (self.who["pro"], self.who["none"]):
            BetaProfile.objects.filter(user__email=email).update(
                has_hit_80_percent=True, distinct_login_days=9
            )
        self.assertEqual(self.kept(has_plan="true", signals_min=2), {self.who["pro"]})
        self.assertEqual(self.kept(has_plan="false", signals_min=2), {self.who["none"]})
        self.assertEqual(self.kept(plan_tier="STANDARD", signals_min=1), set())

    def test_the_filters_leave_the_order_alone(self):
        self.assertEqual(
            self.emails(has_plan="true"),
            [e for e in self.emails() if e != self.who["none"]],
        )


class LastLoginFiltersTests(LeadFilterBase):
    def setUp(self):
        super().setUp()
        self.old = self.teacher(10.0)
        self.mid = self.teacher(20.0)
        self.new = self.teacher(30.0)
        self.never = self.teacher(40.0)
        self.signed_in(self.old, NOW - timedelta(days=30))
        self.signed_in(self.mid, NOW - timedelta(days=10))
        self.signed_in(self.new, NOW - timedelta(days=1))
        self.signed_in(self.never, None)

    def test_after_keeps_the_given_time_and_later_and_drops_never(self):
        bound = (NOW - timedelta(days=10)).isoformat()
        self.assertEqual(
            self.kept(last_login_after=bound), {self.mid.email, self.new.email}
        )

    def test_before_keeps_the_given_time_and_earlier_and_drops_never(self):
        bound = (NOW - timedelta(days=10)).isoformat()
        self.assertEqual(
            self.kept(last_login_before=bound), {self.old.email, self.mid.email}
        )

    def test_a_range(self):
        self.assertEqual(
            self.kept(
                last_login_after=(NOW - timedelta(days=20)).isoformat(),
                last_login_before=(NOW - timedelta(days=5)).isoformat(),
            ),
            {self.mid.email},
        )

    def test_a_never_signed_in_teacher_is_in_neither_bound(self):
        far_past = "2000-01-01T00:00:00Z"
        far_future = "2100-01-01T00:00:00Z"
        self.assertNotIn(self.never.email, self.kept(last_login_after=far_past))
        self.assertNotIn(self.never.email, self.kept(last_login_before=far_future))
        self.assertIn(self.never.email, self.kept())


class BadValuesTests(LeadFilterBase):
    def test_an_invalid_value_is_a_400(self):
        self.build_plans()
        for params in (
            {"plan_tier": "GOLD"},
            {"plan_category": "FREE"},
            {"plan_interval": "WEEKLY"},
            {"has_plan": "perhaps"},
            {"last_login_after": "yesterday"},
            {"last_login_before": "not-a-date"},
        ):
            with self.subTest(params):
                response = self.client.get(reverse(URL), params)
                self.assertEqual(response.status_code, 400, response.content[:200])

    def test_the_400_has_the_shape_the_signal_filters_give(self):
        bad_signal = self.client.get(reverse(URL), {"signals_min": "9"})
        bad_plan = self.client.get(reverse(URL), {"plan_tier": "GOLD"})
        self.assertEqual(bad_plan.status_code, bad_signal.status_code)
        self.assertEqual(set(bad_plan.json()), set(bad_signal.json()), bad_plan.json())


class NothingChangesWithoutTheNewParamsTests(LeadFilterBase):
    def test_the_payload_is_what_it_was(self):
        self.build_plans()
        self.signed_in(self.pro, NOW - timedelta(days=2))
        plain = self.client.get(reverse(URL))
        self.assertEqual(plain.status_code, 200)
        # Empty values do nothing, so the bytes match the request without them.
        empty = self.client.get(
            reverse(URL),
            {
                "plan_tier": "",
                "plan_category": "",
                "plan_interval": "",
                "has_plan": "",
                "last_login_after": "",
                "last_login_before": "",
            },
        )
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.content, plain.content)
        # The rows carry no plan or login fields: the payload keys are as before.
        row = json.loads(plain.content)["data"]["results"][0]
        self.assertNotIn("plan_tier", row)
        self.assertNotIn("last_login", row)


class QueryCountTests(LeadFilterBase):
    def count(self, **params):
        self.client.get(reverse(URL), params)  # warm up
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse(URL), params)
        self.assertEqual(response.status_code, 200, response.content[:200])
        return len(ctx.captured_queries)

    def test_the_cost_does_not_grow_with_the_leads(self):
        cases = (
            {"plan_tier": "PRO"},
            {"plan_category": "LICENSE"},
            {"plan_interval": "ANNUAL"},
            {"has_plan": "true"},
            {"last_login_after": "2000-01-01T00:00:00Z"},
            {"has_plan": "false", "signals_min": 1},
        )
        for i in range(5):
            self.subscribe(self.teacher(float(i)), self.plan(PlanTier.PRO))
        small = {tuple(c.items()): self.count(page_size=25, **c) for c in cases}
        for i in range(25):
            self.subscribe(self.teacher(float(i)), self.plan(PlanTier.PRO))
        for c in cases:
            with self.subTest(**c):
                self.assertEqual(self.count(page_size=25, **c), small[tuple(c.items())])


class EveryParameterIsDescribedTests(LeadFilterBase):
    def operation(self):
        return base.LeadOpenApiTests.operation(self)  # type: ignore[arg-type]

    PARAMS = (
        "ordering",
        "signals_min",
        "at_80_percent",
        "frequent_user",
        "active_last_week",
        "is_power_grader",
        "last_login_after",
        "last_login_before",
        "plan_tier",
        "plan_category",
        "plan_interval",
        "has_plan",
    )

    def test_every_filter_has_a_description(self):
        params = {p["name"]: p for p in self.operation().get("parameters", [])}
        for name in self.PARAMS:
            with self.subTest(name=name):
                self.assertIn(name, params)
                self.assertGreater(len(params[name].get("description", "")), 30)

    def test_no_description_says_super_admins_only(self):
        for p in self.operation().get("parameters", []):
            with self.subTest(name=p["name"]):
                self.assertNotIn("super admin", p.get("description", "").lower())

    def test_the_text_is_the_user_lists_own(self):
        from users.filters import FILTER_HELP

        params = {p["name"]: p for p in self.operation().get("parameters", [])}
        for name in self.PARAMS[1:]:
            with self.subTest(name=name):
                approved = FILTER_HELP[name].replace(
                    " Works for super admins only; for anyone else it is ignored.", ""
                )
                self.assertEqual(params[name]["description"], approved)
