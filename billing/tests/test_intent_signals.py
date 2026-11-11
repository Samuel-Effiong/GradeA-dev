"""
Signals sort and filter on the Credit Usage page's lead list (brief 4,
owner-approved for beta 2026-11-11; SM -31 rulings on checkpoint bc5eafb7c,
docs/evidence/signals-beta/DESIGN.md).

GET analytics beta/intent-signals gains an optional ordering (`signals` =
signal count ascending, `-signals` = descending; ties ALWAYS
conversion_probability DESC NULLS LAST, then id ascending) and the filters
signals_min (0-4), at_80_percent, frequent_user, active_last_week and
is_power_grader. With no param its order and payload are unchanged (pinned
in test_intent_signals_pins).

The four rules come from one module, billing/intent_signals.py, which also
serves the per-row flags, the distribution chart, the single-lead detail,
the cohort summary and the super admin user list. So these tests also check
that every surface agrees, and that `now` is read once per request through
billing.intent_signals.clock (injectable). The 7-day rule is inclusive
(Q-S4).
"""

from collections import Counter
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from billing.models import BetaProfile
from users.models import UserTypes

from .test_intent_signals_pins import NOW, SignalsFixture, profile

FLAGS = ("at_80_percent", "frequent_user", "active_last_week", "is_power_grader")
SIGNALS_ORDERING_DOC = (
    "signals: signal count ascending; -signals: signal count descending. "
    "Ties: conversion_probability descending, nulls last, then id ascending, "
    "in both directions."
)
ALL4 = {
    "has_hit_80_percent": True,
    "distinct_login_days": 9,
    "last_active_at": NOW - timedelta(days=1),
    "credits_used_grading": 5,
    "credits_used_creation": 1,
}
#: SignalsFixture's teachers, by signal count.
BY_COUNT = {
    4: "a.all4.sig@x.example",
    3: "b.three.sig@x.example",
    2: "e.two.sig@x.example",
    1: "c.one.sig@x.example",
    0: "d.none.sig@x.example",
}


class LeadsBase(SignalsFixture):
    _n = 0

    def leads(self, **params):
        response = self.client.get(reverse("analytics-intent-signals"), params)
        self.assertEqual(response.status_code, 200, response.content[:300])
        return response.json()["data"]["results"]

    def emails(self, **params):
        return [row["email"] for row in self.leads(**params)]

    def teacher(self, score=0.0, **fields):
        LeadsBase._n += 1
        n = LeadsBase._n
        user = self.person(f"t{n}.lead@x.example", "T", f"{n:03d}")
        profile(user, conversion_probability=score, **fields)
        return user


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


class LeadOrderingTests(LeadsBase):
    def test_no_param_keeps_todays_order(self):
        self.build()
        today = [
            "a.all4.sig@x.example",
            "b.three.sig@x.example",
            "e.two.sig@x.example",
            "c.one.sig@x.example",
            "d.none.sig@x.example",
        ]

        self.assertEqual(self.emails(), today)
        for ignored in ("", "score", "-conversion_probability", "signal_count"):
            with self.subTest(ordering=ignored):
                self.assertEqual(self.emails(ordering=ignored), today)

    # The fixture's score order equals its count order, so the ordering
    # tests use teachers whose scores disagree with their counts: the
    # default order (by score) must differ from the asserted one.

    def mixed(self):
        four = self.teacher(10.0, **ALL4)
        one = self.teacher(90.0, has_hit_80_percent=True)
        zero = self.teacher(50.0)
        return four, one, zero

    def test_signals_is_count_ascending(self):
        four, one, zero = self.mixed()

        self.assertEqual(
            self.emails(ordering="signals"), [zero.email, one.email, four.email]
        )

    def test_minus_signals_is_count_descending(self):
        four, one, zero = self.mixed()

        self.assertEqual(
            self.emails(ordering="-signals"), [four.email, one.email, zero.email]
        )

    def test_a_tie_is_broken_by_score_highest_first_in_both_directions(self):
        low = self.teacher(20.0, has_hit_80_percent=True)
        high = self.teacher(80.0, distinct_login_days=10)
        mid = self.teacher(50.0, **ALL4)

        self.assertEqual(
            self.emails(ordering="signals"), [high.email, low.email, mid.email]
        )
        self.assertEqual(
            self.emails(ordering="-signals"), [mid.email, high.email, low.email]
        )

    def test_id_breaks_a_tie_of_count_and_score_in_both_directions(self):
        people = [self.teacher(30.0, has_hit_80_percent=True) for _ in range(4)]
        by_id = [u.email for u in sorted(people, key=lambda u: str(u.pk))]
        # The highest score, the fewest signals: first by default, first for
        # `signals`, last for `-signals`.
        zero = self.teacher(90.0)

        self.assertEqual(self.emails(ordering="signals"), [zero.email, *by_id])
        self.assertEqual(self.emails(ordering="-signals"), [*by_id, zero.email])

    def test_ordering_combines_with_search_and_pages(self):
        self.build()

        self.assertEqual(
            self.emails(ordering="signals", search=".sig@", page_size=2),
            [BY_COUNT[0], BY_COUNT[1]],
        )
        self.assertEqual(
            self.emails(ordering="signals", search=".sig@", page_size=2, page=2),
            [BY_COUNT[2], BY_COUNT[3]],
        )


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class LeadFilterTests(LeadsBase):
    def test_signals_min(self):
        self.build()
        for minimum in range(5):
            with self.subTest(minimum):
                expected = {e for n, e in BY_COUNT.items() if n >= minimum}
                self.assertEqual(set(self.emails(signals_min=minimum)), expected)

    def test_each_flag_filter_true_and_false(self):
        self.build()
        flags = {row["email"]: row["flags"] for row in self.leads()}
        for flag in FLAGS:
            for value in (True, False):
                with self.subTest(flag=flag, value=value):
                    expected = {e for e, f in flags.items() if f[flag] is value}
                    self.assertEqual(
                        set(self.emails(**{flag: str(value).lower()})), expected
                    )

    def test_the_count_follows_the_filters(self):
        self.build()
        response = self.client.get(
            reverse("analytics-intent-signals"), {"signals_min": 3}
        )

        self.assertEqual(response.json()["data"]["count"], 2)

    def test_a_bad_value_is_a_400(self):
        for params in (
            {"signals_min": "5"},
            {"signals_min": "-1"},
            {"signals_min": "many"},
            {"is_power_grader": "sometimes"},
        ):
            with self.subTest(params):
                response = self.client.get(reverse("analytics-intent-signals"), params)
                self.assertEqual(response.status_code, 400)

    def test_filters_combine_with_search_and_ordering(self):
        self.build()

        self.assertEqual(
            self.emails(
                search=".sig@",
                signals_min=1,
                at_80_percent="true",
                ordering="signals",
            ),
            [BY_COUNT[2], BY_COUNT[3], BY_COUNT[4]],
        )


# ---------------------------------------------------------------------------
# The 7-day boundary (Q-S4) and the clock
# ---------------------------------------------------------------------------


class BoundaryTests(LeadsBase):
    def active(self, user):
        return {row["email"]: row["flags"] for row in self.leads()}[user.email][
            "active_last_week"
        ]

    def test_exactly_seven_days_ago_is_active(self):
        user = self.teacher(last_active_at=NOW - timedelta(days=7))

        self.assertTrue(self.active(user))

    def test_one_microsecond_earlier_is_not_active(self):
        user = self.teacher(last_active_at=NOW - timedelta(days=7, microseconds=1))

        self.assertFalse(self.active(user))

    def test_never_active_is_not_active(self):
        user = self.teacher(last_active_at=None)

        self.assertFalse(self.active(user))


class ClockTests(LeadsBase):
    def urls(self, people):
        return {
            "intent-signals": (
                reverse("analytics-intent-signals"),
                {"ordering": "-signals"},
            ),
            "distribution": (reverse("beta-chart-intent-signal-distribution"), {}),
            "detail": (
                reverse(
                    "analytics-intent-signal-detail",
                    kwargs={"user_id": people[BY_COUNT[4]].pk},
                ),
                {},
            ),
            "summary": (reverse("analytics-summary"), {}),
        }

    def test_each_surface_reads_the_clock_once_per_request(self):
        people = self.build()
        for name, (url, params) in self.urls(people).items():
            with self.subTest(name):
                with patch("billing.intent_signals.clock", return_value=NOW) as clock:
                    response = self.client.get(url, params)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(clock.call_count, 1)

    def test_the_injected_clock_decides_active_last_week_everywhere(self):
        people = self.build()
        later = NOW + timedelta(days=30)
        with patch("billing.intent_signals.clock", return_value=later):
            flags = {row["email"]: row["flags"] for row in self.leads()}
            summary = self.client.get(reverse("analytics-summary")).json()["data"]
            detail = self.client.get(
                reverse(
                    "analytics-intent-signal-detail",
                    kwargs={"user_id": people[BY_COUNT[4]].pk},
                )
            ).json()["data"]

        self.assertFalse(any(f["active_last_week"] for f in flags.values()))
        self.assertEqual(summary["active_last_7_days_count"], 0)
        self.assertFalse(detail["flags"]["active_last_week"])


# ---------------------------------------------------------------------------
# Every surface agrees (one shared function)
# ---------------------------------------------------------------------------


class AgreementTests(LeadsBase):
    def test_leads_detail_user_list_distribution_and_summary_agree(self):
        people = self.build()
        cache.clear()
        flags = {row["email"]: row["flags"] for row in self.leads()}
        user_rows = {
            str(r["id"]): r
            for r in self.client.get(reverse("user-list"), {"page_size": 100}).json()[
                "data"
            ]["results"]
        }

        for email, lead_flags in flags.items():
            with self.subTest(email):
                user = people[email]
                detail = self.client.get(
                    reverse(
                        "analytics-intent-signal-detail", kwargs={"user_id": user.pk}
                    )
                ).json()["data"]
                row = user_rows[str(user.pk)]
                self.assertEqual(detail["flags"], lead_flags)
                self.assertEqual({f: row[f] for f in FLAGS}, lead_flags)
                self.assertEqual(row["signal_count"], sum(lead_flags.values()))

        chart = self.client.get(
            reverse("beta-chart-intent-signal-distribution")
        ).json()["data"]["distribution"]
        counts = Counter(sum(f.values()) for f in flags.values())
        self.assertEqual(
            {d["signal_count"]: d["user_count"] for d in chart},
            {n: counts.get(n, 0) for n in (1, 2, 3, 4)},
        )

        # summary counts every profile; the non-teacher meets all four.
        summary = self.client.get(reverse("analytics-summary")).json()["data"]
        outsider = BetaProfile.objects.get(user__user_type=UserTypes.SCHOOL_ADMIN)
        self.assertTrue(outsider.has_hit_80_percent)
        for key, flag in (
            ("credit_used_greater_than_80_count", "at_80_percent"),
            ("login_greater_than_8_days_count", "frequent_user"),
            ("active_last_7_days_count", "active_last_week"),
            ("grading_percent_greater_than_creation_count", "is_power_grader"),
        ):
            with self.subTest(summary=key):
                teachers = sum(1 for f in flags.values() if f[flag])
                self.assertEqual(summary[key], teachers + 1)


# ---------------------------------------------------------------------------
# Cost and documentation
# ---------------------------------------------------------------------------


class LeadQueryCountTests(LeadsBase):
    def count(self, name, **params):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(reverse(name), params)
        self.assertEqual(response.status_code, 200, response.content[:300])
        return len(ctx.captured_queries)

    def test_the_lead_list_cost_is_fixed_with_ordering_and_filters(self):
        for i in range(30):
            self.teacher(float(i), has_hit_80_percent=bool(i % 2))
        base = self.count("analytics-intent-signals", page_size=25)
        for size in (5, 25):
            for params in (
                {},
                {"ordering": "signals"},
                {"ordering": "-signals"},
                {"signals_min": 1, "ordering": "-signals"},
                {"at_80_percent": "true", "frequent_user": "false"},
                {"active_last_week": "false", "is_power_grader": "false"},
            ):
                with self.subTest(size=size, **params):
                    self.assertEqual(
                        self.count(
                            "analytics-intent-signals", page_size=size, **params
                        ),
                        base,
                    )

    def test_the_distribution_cost_is_fixed(self):
        for i in range(5):
            self.teacher(float(i), has_hit_80_percent=True)
        five = self.count("beta-chart-intent-signal-distribution")
        for i in range(25):
            self.teacher(float(i), distinct_login_days=9)

        self.assertEqual(self.count("beta-chart-intent-signal-distribution"), five)


class LeadOpenApiTests(LeadsBase):
    def operation(self):
        from drf_spectacular import extensions
        from drf_spectacular.generators import SchemaGenerator

        # Schema generation mutates every extension's target_class; put
        # them back (see users.tests_user_list_sorting.OpenApiTests).
        registered = {
            ext
            for base in (
                extensions.OpenApiAuthenticationExtension,
                extensions.OpenApiSerializerExtension,
                extensions.OpenApiSerializerFieldExtension,
                extensions.OpenApiViewExtension,
                extensions.OpenApiFilterExtension,
            )
            for ext in base._registry
        }
        saved = {ext: ext.__dict__.get("target_class") for ext in registered}

        def restore():
            for ext, target in saved.items():
                ext.target_class = target

        self.addCleanup(restore)
        schema = SchemaGenerator().get_schema(request=None, public=True)
        paths = [p for p in schema["paths"] if p.endswith("beta/intent-signals/")]
        self.assertEqual(len(paths), 1, paths)
        return schema["paths"][paths[0]]["get"]

    def test_the_ordering_and_filters_are_documented(self):
        params = {p["name"]: p for p in self.operation().get("parameters", [])}

        ordering = params["ordering"]
        allowed = set(
            ordering["schema"].get("items", ordering["schema"]).get("enum", [])
        )
        self.assertEqual(allowed, {"signals", "-signals"})
        self.assertIn(SIGNALS_ORDERING_DOC, ordering.get("description", ""))
        for name in ("signals_min", *FLAGS):
            self.assertIn(name, params)

    def test_the_example_shows_all_four_flags(self):
        examples = self.operation()["responses"]["200"]["content"]["application/json"][
            "examples"
        ]
        for example in examples.values():
            for row in example["value"]:
                with self.subTest(row["email"]):
                    self.assertEqual(set(row["flags"]), set(FLAGS))
