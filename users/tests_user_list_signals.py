"""
Signals on the super admin user list (brief 4, owner-approved for beta
2026-11-11; SM -31 rulings on checkpoint bc5eafb7c,
docs/evidence/signals-beta/DESIGN.md).

The list gains five read-only values from the teacher's BetaProfile, through
billing/intent_signals.py (the same rules as the Credit Usage page):

- at_80_percent: has_hit_80_percent
- frequent_user: distinct_login_days >= 8
- active_last_week: last_active_at >= now - 7 days (null -> False)
- is_power_grader: credits_used_grading > credits_used_creation
- signal_count: how many of the four are True (0-4)

TEACHER only (Q-S3): any other user, and a teacher with no profile, gets 0,
every flag False and a null score. Ordering `signals` = count ascending,
`-signals` = count descending; ties ALWAYS conversion_probability DESC NULLS
LAST, then id ascending (Q-S2). Filters signals_min (0-4) and one boolean per
flag, gated to super admins like the other list filters. Retrieve and every
non-list response carry none of the five keys.
"""

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from freezegun import freeze_time

from billing.models import BetaProfile, CreditWallet
from billing.tests.test_intent_signals_pins import NOW, profile
from users.models import UserTypes

from .tests_user_list_sorting import SIGNAL_KEYS, Base

FLAGS = ("at_80_percent", "frequent_user", "active_last_week", "is_power_grader")
#: The ordering parameter's description must say this about `signals`
#: (Q-S2's rule; brief 6 put it in plain words at the owner's request, SM -cf
#: ruling 2, 2026-11-11). Compared with the line breaks folded to spaces.
SIGNALS_ORDERING_DOC = (
    "Users with the same number are sorted by conversion score, highest "
    "first, with users who have no score last, and then by ID. That order for "
    "equal numbers stays the same with `-signals`."
)
ALL4 = {
    "has_hit_80_percent": True,
    "distinct_login_days": 9,
    "last_active_at": NOW - timedelta(days=1),
    "credits_used_grading": 5,
    "credits_used_creation": 1,
}


class SignalBase(Base):
    def setUp(self):
        freezer = freeze_time(NOW)
        freezer.start()
        self.addCleanup(freezer.stop)
        super().setUp()

    def teacher(self, score=None, **fields):
        user = self.person()
        if score is not None or fields:
            profile(user, conversion_probability=score or 0.0, **fields)
        return user

    def signals_of(self, user, **params):
        row = self.row(user, **params)
        return {key: row[key] for key in SIGNAL_KEYS}


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


class SignalValueTests(SignalBase):
    def test_each_flag_follows_its_rule(self):
        cases: dict[str, dict[str, Any]] = {
            "at_80_percent": {"has_hit_80_percent": True},
            "frequent_user": {"distinct_login_days": 8},
            "active_last_week": {"last_active_at": NOW - timedelta(days=2)},
            "is_power_grader": {"credits_used_grading": 2, "credits_used_creation": 1},
        }
        users = {flag: self.teacher(**fields) for flag, fields in cases.items()}
        for flag, user in users.items():
            with self.subTest(flag):
                expected: dict[str, Any] = {f: f == flag for f in FLAGS}
                expected["signal_count"] = 1
                self.assertEqual(self.signals_of(user), expected)

    def test_signal_count_is_the_number_of_true_flags(self):
        user = self.teacher(**ALL4)

        self.assertEqual(
            self.signals_of(user), {**dict.fromkeys(FLAGS, True), "signal_count": 4}
        )

    def test_just_below_each_threshold_is_false(self):
        user = self.teacher(
            distinct_login_days=7,
            last_active_at=NOW - timedelta(days=7, microseconds=1),
            credits_used_grading=3,
            credits_used_creation=3,
        )

        self.assertEqual(
            self.signals_of(user), {**dict.fromkeys(FLAGS, False), "signal_count": 0}
        )

    def test_exactly_seven_days_ago_is_active_last_week(self):
        user = self.teacher(last_active_at=NOW - timedelta(days=7))

        self.assertTrue(self.row(user)["active_last_week"])

    def test_no_profile_gives_zero_and_every_flag_false(self):
        user = self.person()
        BetaProfile.objects.filter(user=user).delete()

        self.assertEqual(
            self.signals_of(user), {**dict.fromkeys(FLAGS, False), "signal_count": 0}
        )

    def test_a_non_teacher_profile_gives_zero_and_every_flag_false(self):
        # Q-S3: the list agrees with the Credit Usage page (teachers only).
        admin = self.person(user_type=UserTypes.SCHOOL_ADMIN)
        profile(admin, **ALL4)

        self.assertEqual(
            self.signals_of(admin), {**dict.fromkeys(FLAGS, False), "signal_count": 0}
        )


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


class SignalOrderingTests(SignalBase):
    """The default order is first_name, then id. Each test names its users
    so that order cannot satisfy the assertion by chance."""

    def named(self, user, name):
        return self.stamp(user, first_name=name)

    def test_signals_is_count_ascending(self):
        four = self.named(self.teacher(10.0, **ALL4), "A")
        one = self.named(self.teacher(90.0, has_hit_80_percent=True), "B")
        zero = self.named(self.teacher(50.0), "C")

        self.assertEqual(
            self.order_of([four, one, zero], ordering="signals"), [zero, one, four]
        )

    def test_minus_signals_is_count_descending(self):
        four = self.named(self.teacher(10.0, **ALL4), "C")
        one = self.named(self.teacher(90.0, has_hit_80_percent=True), "B")
        zero = self.named(self.teacher(50.0), "A")

        self.assertEqual(
            self.order_of([four, one, zero], ordering="-signals"), [four, one, zero]
        )

    def test_a_tie_is_broken_by_score_highest_first_in_both_directions(self):
        low = self.named(self.teacher(20.0, has_hit_80_percent=True), "A")
        high = self.named(self.teacher(80.0, distinct_login_days=10), "B")

        for ordering in ("signals", "-signals"):
            with self.subTest(ordering):
                self.assertEqual(
                    self.order_of([low, high], ordering=ordering), [high, low]
                )

    def test_no_score_sorts_last_in_both_directions(self):
        scored = self.named(self.teacher(0.0, credits_used_creation=1), "Z")
        no_profile = self.person()
        BetaProfile.objects.filter(user=no_profile).delete()
        not_teacher = self.person(user_type=UserTypes.SCHOOL_ADMIN)
        profile(not_teacher, conversion_probability=99.0)

        for ordering in ("signals", "-signals"):
            with self.subTest(ordering):
                listed = self.order_of(
                    [scored, no_profile, not_teacher], ordering=ordering
                )
                self.assertEqual(listed[0], scored)
                self.assertEqual(set(listed[1:]), {no_profile, not_teacher})

    def test_id_breaks_a_tie_of_count_and_score_in_both_directions(self):
        people = [self.teacher(30.0, has_hit_80_percent=True) for _ in range(4)]
        by_id = sorted(people, key=lambda u: str(u.pk))
        # The default order is first_name, then id: name them against the id
        # order so the default cannot pass this test.
        for name, user in zip("DCBA", by_id, strict=True):
            self.stamp(user, first_name=name)

        for ordering in ("signals", "-signals"):
            with self.subTest(ordering):
                self.assertEqual(self.order_of(people, ordering=ordering), by_id)

    def test_signals_combines_with_another_key(self):
        # Two "A"s: the lower id gets no signals, so id order (the default
        # tie-break) is the reverse of -signals.
        first, second = sorted(
            (self.person(first_name="A"), self.person(first_name="A")),
            key=lambda u: str(u.pk),
        )
        profile(first, conversion_probability=5.0)
        profile(second, conversion_probability=5.0, has_hit_80_percent=True)
        b = self.teacher(5.0, **ALL4)
        self.stamp(b, first_name="B")

        self.assertEqual(
            self.order_of([first, second, b], ordering="first_name,-signals"),
            [second, first, b],
        )


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class SignalFilterTests(SignalBase):
    def make(self):
        return {
            0: self.teacher(1.0),
            1: self.teacher(2.0, has_hit_80_percent=True),
            2: self.teacher(3.0, has_hit_80_percent=True, distinct_login_days=8),
            4: self.teacher(4.0, **ALL4),
        }

    def listed(self, people, **params):
        return set(self.order_of(list(people.values()), **params))

    def test_signals_min(self):
        people = self.make()
        for minimum in range(5):
            with self.subTest(minimum):
                expected = {u for n, u in people.items() if n >= minimum}
                self.assertEqual(self.listed(people, signals_min=minimum), expected)

    def test_each_flag_filter_true_and_false(self):
        people = self.make()
        rows = {str(r["id"]): r for r in self.rows()}
        for flag in FLAGS:
            for value in (True, False):
                with self.subTest(flag=flag, value=value):
                    expected = {
                        u for u in people.values() if rows[str(u.pk)][flag] is value
                    }
                    self.assertEqual(
                        self.listed(people, **{flag: str(value).lower()}), expected
                    )

    def test_a_bad_value_is_a_400(self):
        for params in (
            {"signals_min": "5"},
            {"signals_min": "-1"},
            {"signals_min": "many"},
            {"at_80_percent": "sometimes"},
        ):
            with self.subTest(params):
                self.assertEqual(self.get_list(**params).status_code, 400)

    def test_filters_combine_with_each_other_and_ordering(self):
        people = self.make()

        self.assertEqual(
            self.order_of(
                list(people.values()),
                signals_min=1,
                frequent_user="true",
                ordering="-signals",
            ),
            [people[4], people[2]],
        )

    def test_the_signal_filters_do_nothing_off_the_list_for_others(self):
        # The gate from brief 2: filter_queryset also runs in get_object().
        teacher = self.teacher(1.0)
        client = self.as_user(teacher)
        detail = reverse("user-detail", kwargs={"pk": teacher.pk})
        for params in (
            {"signals_min": 4},
            {"at_80_percent": "true"},
            {"frequent_user": "true"},
            {"active_last_week": "true"},
            {"is_power_grader": "true"},
        ):
            with self.subTest(params):
                cache.clear()
                self.assertEqual(client.get(detail, params).status_code, 200)


# ---------------------------------------------------------------------------
# The list only, the cache key, the cost, the clock
# ---------------------------------------------------------------------------


class SignalListOnlyTests(SignalBase):
    def test_non_list_responses_carry_none_of_the_five(self):
        teacher = self.teacher(50.0, **ALL4)
        for who, client in (
            ("super admin", self.as_user(self.admin)),
            ("teacher", self.as_user(teacher)),
        ):
            for name, url in (
                ("retrieve", reverse("user-detail", kwargs={"pk": teacher.pk})),
                ("me", reverse("user-me")),
            ):
                with self.subTest(who=who, response=name):
                    cache.clear()
                    response = client.get(url)
                    self.assertEqual(response.status_code, 200, response.content)
                    data = response.data
                    self.assertEqual(sorted(set(SIGNAL_KEYS) & set(data)), [])

    def test_the_list_carries_all_five(self):
        row = self.row(self.teacher(1.0))

        for key in SIGNAL_KEYS:
            self.assertIn(key, row)


class SignalCacheKeyTests(SignalBase):
    """No cache.clear() between requests: one generation, two keys each."""

    def test_ordering_in_both_directions_from_one_generation(self):
        few, many = self.teacher(1.0), self.teacher(2.0, **ALL4)

        self.assertEqual(self.order_of([few, many], ordering="signals"), [few, many])
        self.assertEqual(self.order_of([few, many], ordering="-signals"), [many, few])

    def test_each_filter_from_one_generation(self):
        none, every = self.teacher(1.0), self.teacher(2.0, **ALL4)
        for name, on, off in (
            ("signals_min", 4, 0),
            *((flag, "true", "false") for flag in FLAGS),
        ):
            with self.subTest(name):
                self.assertEqual(self.order_of([none, every], **{name: on}), [every])
                self.assertEqual(
                    set(self.order_of([none, every], **{name: off})),
                    {none} if name != "signals_min" else {none, every},
                )


class SignalQueryCountTests(SignalBase):
    def make_teachers(self, n):
        for i in range(n):
            user = self.teacher(float(i), has_hit_80_percent=bool(i % 2))
            CreditWallet.objects.get_or_create(user=user)

    def count(self, page_size, **params):
        cache.clear()
        with CaptureQueriesContext(connection) as ctx:
            response = self.get_list(page_size=page_size, **params)
        self.assertEqual(response.status_code, 200, response.content)
        return len(ctx.captured_queries)

    def test_sorting_and_filtering_by_signals_keep_the_cost_fixed(self):
        self.make_teachers(30)
        base = self.count(25)
        for size in (5, 25):
            for params in (
                {"ordering": "signals"},
                {"ordering": "-signals"},
                {"signals_min": 0, "at_80_percent": "true", "ordering": "-signals"},
                {"frequent_user": "false", "active_last_week": "false"},
                {"is_power_grader": "false"},
            ):
                with self.subTest(size=size, **params):
                    self.assertEqual(self.count(size, **params), base)


class SignalClockTests(SignalBase):
    def test_the_list_reads_the_clock_once_per_request(self):
        self.teacher(1.0, **ALL4)
        with patch("billing.intent_signals.clock", return_value=NOW) as clock:
            self.rows(ordering="-signals", active_last_week="true")

        self.assertEqual(clock.call_count, 1)

    def test_the_injected_clock_decides_active_last_week(self):
        user = self.teacher(1.0, last_active_at=NOW - timedelta(days=3))
        later = NOW + timedelta(days=5)
        with patch("billing.intent_signals.clock", return_value=later):
            self.assertFalse(self.row(user)["active_last_week"])


class SignalOpenApiTests(SignalBase):
    def list_parameters(self):
        from drf_spectacular import extensions
        from drf_spectacular.generators import SchemaGenerator

        # The same snapshot/restore as users.tests_user_list_sorting
        # (schema generation mutates every extension's target_class).
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
        operation = schema["paths"]["/api/v1/users"]["get"]
        return {p["name"]: p for p in operation.get("parameters", [])}

    def test_signals_is_an_ordering_value_with_the_tie_break_documented(self):
        ordering = self.list_parameters()["ordering"]
        allowed = set(
            ordering["schema"].get("items", ordering["schema"]).get("enum", [])
        )

        self.assertLessEqual({"signals", "-signals"}, allowed)
        description = " ".join(ordering.get("description", "").split())
        self.assertIn(SIGNALS_ORDERING_DOC, description)

    def test_every_signal_filter_is_documented(self):
        params = self.list_parameters()

        for name in ("signals_min", *FLAGS):
            self.assertIn(name, params)
