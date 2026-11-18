"""S16 (owner go 2026-11-18, beta): four more sorts on the leads list -
plan_tier, plan_category, plan_interval and last_login. Each takes `-`,
puts empty values last both ways, breaks ties by user id, and matches the
user list's order for the same teachers."""

from datetime import timedelta

from django.db import connection
from django.test.utils import CaptureQueriesContext

from billing.models import BillingInterval, PlanCategory, PlanTier
from users import user_list
from users.models import CustomUser

from .test_intent_signals_lead_filters import LeadFilterBase
from .test_intent_signals_pins import NOW

NEW = ("plan_tier", "plan_category", "plan_interval", "last_login")
ORDERING_TAIL = (
    "You can also sort by `credit_usage`, `credits_left`, `papers_graded`, "
    "`assignments_created`, `activated_at`, `plan_tier`, `plan_category`, "
    "`plan_interval` or `last_login`. `plan_tier` goes by plan level, from "
    "Trial to Custom. Add `-` in front for the largest, newest or highest plan "
    "first. Empty values come last either way, and ties are broken by ID."
)


class PlanSortBase(LeadFilterBase):
    def build(self):
        specs = (
            (PlanTier.TRIAL, PlanCategory.INDIVIDUAL, BillingInterval.NONE, 5),
            (PlanTier.STANDARD, PlanCategory.LICENSE, BillingInterval.ANNUAL, 4),
            (PlanTier.PRO, PlanCategory.INDIVIDUAL, BillingInterval.MONTHLY, 3),
            (PlanTier.POWER, PlanCategory.INDIVIDUAL, BillingInterval.ANNUAL, 2),
            (PlanTier.CUSTOM, PlanCategory.LICENSE, BillingInterval.MONTHLY, 1),
        )
        self.people = []
        for i, (tier, category, interval, days) in enumerate(specs):
            user = self.teacher(float(i))
            self.subscribe(user, self.plan(tier, category, interval))
            self.signed_in(user, NOW - timedelta(days=days))
            self.people.append(user)
        self.empty = self.teacher(99.0)  # no plan, never signed in
        CustomUser.objects.filter(pk=self.empty.pk).update(last_login=None)
        self.people.append(self.empty)
        return self

    def users_list_order(self, ordering):
        queryset = CustomUser.objects.filter(pk__in=[u.pk for u in self.people])
        terms = [ordering]
        return [u.email for u in user_list.apply_ordering(queryset, terms, now=NOW)]


class NewSortTests(PlanSortBase):
    def test_each_sort_matches_the_users_list_both_ways(self):
        self.build()
        for key in NEW:
            for ordering in (key, f"-{key}"):
                with self.subTest(ordering):
                    self.assertEqual(
                        self.emails(ordering=ordering),
                        self.users_list_order(ordering),
                    )

    def test_plan_tier_goes_by_level_and_empty_is_last_both_ways(self):
        self.build()
        trial, standard, pro, power, custom = self.people[:5]
        self.assertEqual(
            self.emails(ordering="plan_tier"),
            [u.email for u in (trial, standard, pro, power, custom, self.empty)],
        )
        self.assertEqual(
            self.emails(ordering="-plan_tier"),
            [u.email for u in (custom, power, pro, standard, trial, self.empty)],
        )

    def test_last_login_both_ways_never_signed_in_last(self):
        self.build()
        oldest_first = [u.email for u in self.people[:5]]
        self.assertEqual(
            self.emails(ordering="last_login"), [*oldest_first, self.empty.email]
        )
        self.assertEqual(
            self.emails(ordering="-last_login"),
            [*reversed(oldest_first), self.empty.email],
        )

    def test_plan_category_and_interval_put_empty_last_both_ways(self):
        self.build()
        for key in ("plan_category", "plan_interval"):
            for ordering in (key, f"-{key}"):
                with self.subTest(ordering):
                    self.assertEqual(
                        self.emails(ordering=ordering)[-1], self.empty.email
                    )

    def test_ties_go_by_user_id_both_ways(self):
        plan = self.plan(PlanTier.PRO)
        twins = []
        for i in range(4):
            user = self.teacher(float(i))
            self.subscribe(user, plan)
            self.signed_in(user, NOW)
            twins.append(user)
        by_id = [u.email for u in sorted(twins, key=lambda u: u.pk)]
        for key in NEW:
            for ordering in (key, f"-{key}"):
                with self.subTest(ordering):
                    self.assertEqual(self.emails(ordering=ordering), by_id)

    def test_it_combines_with_filters(self):
        self.build()
        self.assertEqual(
            self.emails(ordering="-plan_tier", plan_category="LICENSE"),
            [self.people[4].email, self.people[1].email],
        )

    def test_the_query_count_does_not_grow_with_rows(self):
        self.build()

        def count():
            with CaptureQueriesContext(connection) as queries:
                self.emails(ordering="-plan_tier")
            return len(queries)

        before = count()
        for i in range(6):
            user = self.teacher(float(i))
            self.subscribe(user, self.plan(PlanTier.PRO))
        self.assertEqual(count(), before)


class NewSortSwaggerTests(PlanSortBase):
    def test_ordering_lists_the_eight_new_values_and_ends_with_the_text(self):
        from . import test_intent_signals as base

        params = {
            p["name"]: p
            for p in base.LeadOpenApiTests.operation(self).get("parameters", [])  # type: ignore[arg-type]
        }
        ordering = params["ordering"]
        allowed = set(
            ordering["schema"].get("items", ordering["schema"]).get("enum", [])
        )
        for key in NEW:
            self.assertIn(key, allowed)
            self.assertIn(f"-{key}", allowed)
        self.assertTrue(
            ordering["description"].endswith(f" {ORDERING_TAIL}"),
            ordering["description"],
        )
