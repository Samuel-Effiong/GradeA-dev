"""Each lead row shows the teacher's plan and usage (beta, owner-approved
2026-11-12, S9b).

GET analytics beta/intent-signals rows gain plan_tier, plan_category,
plan_interval, credits_used, credits_left, papers_graded, assignments_created
and activated_at as top-level fields, with the names, types and nulls of the
user list's row (users.list_serializers.UserListSerializer) and its values
(users.user_list). The metrics block, the flags and every other field stay
as they were. The page costs the same number of queries at any page size.

    python manage.py test billing.tests.test_intent_signals_lead_usage_fields
"""

from datetime import timedelta

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from billing.models import BillingInterval, CreditWallet, PlanCategory, PlanTier
from users.models import CustomUser

from . import test_intent_signals as base
from .test_intent_signals_pins import NOW
from .test_intent_signals_usage_filters import UsageBase

URL = "analytics-intent-signals"
NEW = ("plan_tier", "plan_category", "plan_interval", "credits_used",
       "credits_left", "papers_graded", "assignments_created",
       "activated_at")  # fmt: skip
OLD = ("id", "name", "email", "score", "metrics", "flags")


class LeadUsageFieldsTests(UsageBase):
    def rows(self, **params):
        return {r["email"]: r for r in self.leads(page_size=100, **params)}

    def build(self):
        self.full = self.lead(score=10.0, used=3_500, left=7_500)
        self.subscribe(
            self.full,
            self.plan(
                PlanTier.PRO,
                category=PlanCategory.INDIVIDUAL,
                interval=BillingInterval.ANNUAL,
            ),
        )
        self.teaching(self.full, 3, 2)
        CustomUser.objects.filter(pk=self.full.pk).update(
            email_verified_at=NOW - timedelta(days=4)
        )
        self.bare = self.lead(score=20.0, left=None)  # no plan, no wallet
        CustomUser.objects.filter(pk=self.bare.pk).update(email_verified_at=None)

    def test_a_teacher_with_a_plan_and_usage(self):
        self.build()
        row = self.rows()[self.full.email]
        self.assertEqual(row["plan_tier"], "PRO")
        self.assertEqual(row["plan_category"], "INDIVIDUAL")
        self.assertEqual(row["plan_interval"], "ANNUAL")
        self.assertEqual(row["credits_used"], 3)  # whole credits, as the list
        self.assertEqual(row["credits_left"], 7)
        self.assertEqual(row["papers_graded"], 2)
        self.assertEqual(row["assignments_created"], 3)
        self.assertTrue(row["activated_at"].startswith("2026-06-11T12:00:00"))

    def test_a_teacher_with_no_plan_and_no_wallet(self):
        self.build()
        row = self.rows()[self.bare.email]
        self.assertIsNone(row["plan_tier"])
        self.assertIsNone(row["plan_category"])
        self.assertIsNone(row["plan_interval"])
        self.assertIsNone(row["credits_left"])
        self.assertIsNone(row["activated_at"])
        self.assertEqual(row["credits_used"], 0)
        self.assertEqual(row["papers_graded"], 0)
        self.assertEqual(row["assignments_created"], 0)

    def test_credits_used_is_all_time_not_the_beta_counter(self):
        self.build()
        type(self.full.beta_profile).objects.filter(user=self.full).update(
            total_credits_used=9_999
        )
        row = self.rows()[self.full.email]
        self.assertEqual(row["credits_used"], 3)
        self.assertEqual(row["metrics"]["credit_usage"], 9_999)

    def test_the_values_match_the_user_list(self):
        self.build()
        cache.clear()
        listed = self.client.get(reverse("user-list"), {"page_size": 100}).json()
        listed = listed.get("results", listed.get("data", {}).get("results", []))
        by_email = {u["email"]: u for u in listed}
        rows = self.rows()
        for user in (self.full, self.bare):
            for name in NEW:
                with self.subTest(user=user.email, field=name):
                    self.assertIn(name, by_email[user.email])
                    self.assertEqual(rows[user.email][name], by_email[user.email][name])

    def test_every_other_field_is_as_it_was(self):
        self.build()
        for row in self.rows().values():
            self.assertEqual(set(row), {*OLD, *NEW})
            self.assertEqual(
                set(row["metrics"]),
                {"credit_usage", "usage_percentage", "login_days", "last_active",
                 "primary_use_case"},  # fmt: skip
            )
            self.assertEqual(
                set(row["flags"]),
                {"at_80_percent", "active_last_week", "is_power_grader",
                 "frequent_user"},  # fmt: skip
            )

    def test_no_wallet_is_null_even_when_a_wallet_row_is_gone(self):
        self.build()
        self.assertFalse(CreditWallet.objects.filter(user=self.bare).exists())
        self.assertIsNone(self.rows()[self.bare.email]["credits_left"])


class LeadUsageQueryCountTests(UsageBase):
    def queries(self, page_size):
        self.client.get(reverse(URL), {"page_size": page_size})  # warm up
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse(URL), {"page_size": page_size})
        self.assertEqual(response.status_code, 200, response.content[:200])
        return len(ctx.captured_queries), len(response.json()["data"]["results"])

    def test_the_cost_is_the_same_at_every_page_size(self):
        for i in range(3):
            lead = self.lead(score=float(i), used=2_000, left=1_000)
            self.subscribe(lead, self.plan(PlanTier.PRO))
            self.teaching(lead, 2, 1)
        small, rows_small = self.queries(3)
        for i in range(12):
            lead = self.lead(score=float(i), used=2_000, left=1_000)
            self.subscribe(lead, self.plan(PlanTier.PRO))
            self.teaching(lead, 2, 1)
        large, rows_large = self.queries(15)
        self.assertEqual((rows_small, rows_large), (3, 15))
        self.assertEqual(small, large)
        with self.assertNumQueries(large):
            self.client.get(reverse(URL), {"page_size": 15})
        with self.assertNumQueries(small):
            self.client.get(reverse(URL), {"page_size": 3})


class AiPromptBlockUnchangedTests(base.LeadsBase):
    def test_the_lead_serializer_the_prompt_block_uses_has_no_new_fields(self):
        from billing.serializers import ConversionLeadSerializer

        self.assertEqual(set(ConversionLeadSerializer().fields), set(OLD))
