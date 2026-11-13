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
NEW = ("plan_tier", "plan_category", "plan_interval",
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
        # One usage number, from the wallet, in whole credits (S10).
        self.assertEqual(row["metrics"]["credit_usage"], 3)
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
        self.assertEqual(row["metrics"]["credit_usage"], 0)
        self.assertEqual(row["metrics"]["usage_percentage"], 0.0)
        self.assertEqual(row["papers_graded"], 0)
        self.assertEqual(row["assignments_created"], 0)

    def test_credit_usage_is_the_wallets_not_the_beta_tally(self):
        self.build()
        type(self.full.beta_profile).objects.filter(user=self.full).update(
            total_credits_used=9_999, initial_beta_credits=10_000
        )
        row = self.rows()[self.full.email]
        self.assertEqual(row["metrics"]["credit_usage"], 3)
        # 3,500 raw used of 10,000: the percentage follows the wallet value.
        self.assertEqual(row["metrics"]["usage_percentage"], 35.0)

    def test_the_detail_shows_the_wallet_value_and_percentage(self):
        self.build()
        type(self.full.beta_profile).objects.filter(user=self.full).update(
            total_credits_used=9_999, initial_beta_credits=10_000
        )
        response = self.client.get(
            reverse("analytics-intent-signal-detail", kwargs={"user_id": self.full.pk})
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()
        lead = body.get("data", body)
        self.assertEqual(lead["metrics"]["credit_usage"], 3)
        self.assertEqual(lead["metrics"]["usage_percentage"], 35.0)

    def test_the_ai_prompt_block_rows_show_the_wallet_value(self):
        from billing.views import lead_rows, top_leads

        self.build()
        type(self.full.beta_profile).objects.filter(user=self.full).update(
            total_credits_used=9_999, initial_beta_credits=10_000
        )
        rows = {r["email"]: r for r in lead_rows(top_leads(NOW, 10))}
        self.assertEqual(rows[self.full.email]["metrics"]["credit_usage"], 3)
        self.assertEqual(rows[self.full.email]["metrics"]["usage_percentage"], 35.0)
        self.assertEqual(rows[self.bare.email]["metrics"]["credit_usage"], 0)

    def test_there_is_no_top_level_credits_used(self):
        self.build()
        for row in self.rows().values():
            self.assertNotIn("credits_used", row)

    def test_the_old_names_are_not_accepted(self):
        self.build()
        everyone = set(self.rows())
        # An unknown filter is ignored; an unknown sort key is dropped.
        self.assertEqual(set(self.rows(credits_used_min=99)), everyone)
        self.assertEqual(set(self.rows(credits_used_max=-1)), everyone)
        self.assertEqual(
            list(self.rows(ordering="credits_used")),
            list(self.rows()),
        )
        self.assertEqual(
            list(self.rows(ordering="-credits_used")),
            list(self.rows()),
        )

    def test_the_values_match_the_user_list(self):
        self.build()
        cache.clear()
        listed = self.client.get(reverse("user-list"), {"page_size": 100}).json()
        listed = listed.get("results", listed.get("data", {}).get("results", []))
        by_email = {u["email"]: u for u in listed}
        rows = self.rows()
        for user in (self.full, self.bare):
            self.assertEqual(
                rows[user.email]["metrics"]["credit_usage"],
                by_email[user.email]["credit_usage"],
            )
            self.assertNotIn("credits_used", by_email[user.email])
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


class SwaggerExamplesShowEveryFieldTests(UsageBase):
    """The examples on Swagger are a row of the real list: same keys, same
    order (S9c, owner 2026-11-12), so they cannot drift from the serializer."""

    def examples(self):
        operation = base.LeadOpenApiTests.operation(self)  # type: ignore[arg-type]
        found = operation["responses"]["200"]["content"]["application/json"]["examples"]
        self.assertEqual(len(found), 2)
        for example in found.values():
            value = example["value"]
            yield from value["results"] if isinstance(value, dict) else value

    def test_every_example_row_has_exactly_the_serializer_fields_in_order(self):
        from billing.serializers import (
            ConversionLeadFlagsSerializer,
            ConversionLeadListSerializer,
            ConversionLeadMetricsSerializer,
        )

        fields = list(ConversionLeadListSerializer().fields)
        for row in self.examples():
            with self.subTest(email=row.get("email")):
                self.assertEqual(list(row), fields)
                self.assertEqual(
                    list(row["metrics"]), list(ConversionLeadMetricsSerializer().fields)
                )
                self.assertEqual(
                    list(row["flags"]), list(ConversionLeadFlagsSerializer().fields)
                )

    def test_the_values_are_real_choices_and_agree(self):
        rows = list(self.examples())
        for row in rows:
            self.assertRegex(row["id"], r"^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
            self.assertTrue(row["activated_at"].startswith("2026-"))
            self.assertTrue(row["metrics"]["last_active"].startswith("2026-"))
            if row["plan_tier"] is not None:
                self.assertIn(row["plan_tier"], PlanTier.values)
                self.assertIn(row["plan_category"], PlanCategory.values)
                self.assertIn(row["plan_interval"], BillingInterval.values)
        self.assertTrue(any(r["plan_tier"] is None for r in rows))
        for row in rows:
            self.assertEqual(
                row["metrics"]["primary_use_case"],
                "Grading" if row["flags"]["is_power_grader"] else "Creation",
            )


class LeadDetailFieldsTests(UsageBase):
    """GET intent-signal-detail/<user_id> shows the lead row's plan and usage
    fields (S11): same names, types, nulls and values, in the row's order,
    after flags and before daily_usage."""

    full: CustomUser
    bare: CustomUser

    def build(self):
        return LeadUsageFieldsTests.build(self)  # type: ignore[arg-type]

    def detail(self, user):
        response = self.client.get(
            reverse("analytics-intent-signal-detail", kwargs={"user_id": user.pk})
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()
        return body.get("data", body)

    def row(self, user):
        found = [r for r in self.leads(page_size=100) if r["email"] == user.email]
        self.assertEqual(len(found), 1)
        return found[0]

    def test_a_teacher_with_a_plan_and_usage(self):
        self.build()
        detail = self.detail(self.full)
        self.assertEqual(detail["plan_tier"], "PRO")
        self.assertEqual(detail["plan_category"], "INDIVIDUAL")
        self.assertEqual(detail["plan_interval"], "ANNUAL")
        self.assertEqual(detail["credits_left"], 7)
        self.assertEqual(detail["papers_graded"], 2)
        self.assertEqual(detail["assignments_created"], 3)
        self.assertTrue(detail["activated_at"].startswith("2026-06-11T12:00:00"))
        self.assertEqual(detail["metrics"]["credit_usage"], 3)

    def test_a_teacher_with_no_plan_and_no_wallet(self):
        self.build()
        detail = self.detail(self.bare)
        for name in ("plan_tier", "plan_category", "plan_interval", "credits_left",
                     "activated_at"):  # fmt: skip
            with self.subTest(name):
                self.assertIsNone(detail[name])
        self.assertEqual(detail["papers_graded"], 0)
        self.assertEqual(detail["assignments_created"], 0)
        self.assertNotIn("credits_used", detail)

    def test_the_values_equal_the_leads_row_and_the_users_list(self):
        self.build()
        cache.clear()
        listed = self.client.get(reverse("user-list"), {"page_size": 100}).json()
        listed = listed.get("results", listed.get("data", {}).get("results", []))
        by_email = {u["email"]: u for u in listed}
        for user in (self.full, self.bare):
            detail, row = self.detail(user), self.row(user)
            for name in NEW:
                with self.subTest(user=user.email, field=name):
                    self.assertEqual(detail[name], row[name])
                    self.assertEqual(detail[name], by_email[user.email][name])
            self.assertEqual(detail["metrics"], row["metrics"])

    def test_the_keys_and_their_order_are_the_serializers(self):
        from billing.serializers import (
            BetaUserDetailResponseSerializer,
            ConversionLeadFlagsSerializer,
            ConversionLeadMetricsSerializer,
        )

        self.build()
        detail = self.detail(self.full)
        self.assertEqual(list(detail), list(BetaUserDetailResponseSerializer().fields))
        self.assertEqual(
            list(detail["metrics"]), list(ConversionLeadMetricsSerializer().fields)
        )
        self.assertEqual(
            list(detail["flags"]), list(ConversionLeadFlagsSerializer().fields)
        )
        # The lead row's order, after flags and before daily_usage.
        keys = list(detail)
        self.assertEqual(keys[keys.index("flags") + 1 : keys.index("daily_usage")],
                         [*NEW])  # fmt: skip

    def test_the_cost_does_not_depend_on_the_lead(self):
        self.build()
        busy = self.lead(score=50.0, used=5_000, left=2_000)
        self.subscribe(busy, self.plan(PlanTier.PRO))
        self.teaching(busy, 6, 5)
        url = lambda u: reverse(  # noqa: E731
            "analytics-intent-signal-detail", kwargs={"user_id": u.pk}
        )
        self.client.get(url(self.bare))  # warm up
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(url(self.bare))
        quiet = len(ctx.captured_queries)
        with self.assertNumQueries(quiet):
            self.client.get(url(busy))
