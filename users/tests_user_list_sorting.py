"""
User list sorting and filters (owner-approved 2026-11-11; SM -16 rulings
D1-D7 on checkpoint e2a0a146a, docs/evidence/user-list-sorting/DESIGN.md).

GET /api/v1/users/ (super admin only) gains nine read-only values on the
LIST response, nine ordering keys and eight filters:

- last_login, activated_at (email_verified_at)
- plan_tier, plan_category, plan_interval: the current plan, resolved as
  billing resolves it (license teacher > license admin > individual)
- assignments_created, papers_graded: by course.teacher (D5)
- credits_left: billing's spendable_credits(wallet) // CONVERSION_FACTOR,
  null with no wallet (D4)
- credits_used: Sum(amount) of unrefunded CreditUsageLog // CONVERSION_FACTOR

Every null sorts last in both directions (D4); id breaks every tie.
Retrieve, me, patch, lock, unlock and the sign-in payload carry none of
the nine keys (D7). The values add no per-row query (D1). The list cache
key varies by every new param (D2).
"""

from datetime import timedelta
from typing import Any
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APITestCase
from rest_framework.throttling import SimpleRateThrottle

from assignments.models import Assignment
from billing.holds import spendable_credits
from billing.models import (
    CONVERSION_FACTOR,
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditUsageLog,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
    UserSubscription,
)
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from students.models import StudentSubmission
from users.models import UserTypes

User = get_user_model()

PW = "List-sort-pass-1"  # pragma: allowlist secret
RATES = {"login": "1000/min", "anon": "1000/min"}

NEW_KEYS = (
    "last_login",
    "activated_at",
    "plan_tier",
    "plan_category",
    "plan_interval",
    "assignments_created",
    "papers_graded",
    "credits_left",
    "credit_usage",
)
#: Brief 4 (signals, users.tests_user_list_signals): five more list-only keys.
SIGNAL_KEYS = (
    "signal_count",
    "at_80_percent",
    "frequent_user",
    "active_last_week",
    "is_power_grader",
)
#: "username" was removed (brief 6, owner 2026-11-11): CustomUser has no
#: username field, so it was a 500.
OLD_ORDERING_KEYS = ("first_name", "last_name", "email")
FILTER_PARAMS = (
    "last_login_after",
    "last_login_before",
    "activated_after",
    "activated_before",
    "plan_tier",
    "plan_category",
    "plan_interval",
    "has_plan",
)


class Base(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        throttles = patch.dict(SimpleRateThrottle.THROTTLE_RATES, RATES)
        throttles.start()
        self.addCleanup(throttles.stop)
        self.admin = self.super_admin("operator.sort@x.example")
        self.now = timezone.now()
        self._n = 0

    # -- people ----------------------------------------------------------

    def person(self, email=None, user_type=UserTypes.TEACHER, **extra):
        self._n += 1
        fields = {"is_active": True, "user_type": user_type, **extra}
        return User.objects.create_user(
            email=email or f"p{self._n}.sort@x.example", password=PW, **fields
        )

    def super_admin(self, email):
        user = User.objects.create_superuser(email=email, password=PW)
        User.objects.filter(pk=user.pk).update(
            user_type=UserTypes.SUPER_ADMIN, email_verified_at=timezone.now()
        )
        user.refresh_from_db()
        return user

    def stamp(self, user, **fields):
        User.objects.filter(pk=user.pk).update(**fields)
        user.refresh_from_db()
        return user

    def as_user(self, user):
        client = self.client_class()
        client.force_authenticate(user=user)
        return client

    # -- billing ---------------------------------------------------------

    def plan(self, name, tier, category=PlanCategory.INDIVIDUAL, interval=None):
        return SubscriptionPlan.objects.create(
            name=name,
            category=category,
            tier=tier,
            interval=interval or BillingInterval.MONTHLY,
            monthly_credits=10_000,
            is_active=True,
        )

    def subscribe(self, user, plan, is_active=True, start=None):
        UserSubscription.objects.filter(user=user, is_active=True).update(
            is_active=False
        )
        start = start or self.now
        return UserSubscription.objects.create(
            user=user,
            plan=plan,
            is_active=is_active,
            billing_cycle_start=start,
            billing_cycle_end=start + timedelta(days=30),
        )

    def license(self, plan, admin_user, is_active=True):
        self._n += 1
        return LicenseSubscription.objects.create(
            school=School.objects.create(name=f"School {self._n}"),
            admin_user=admin_user,
            plan=plan,
            contract_months=12,
            max_seats=10,
            billing_cycle_start=self.now,
            billing_cycle_end=self.now + timedelta(days=365),
            is_active=is_active,
            auto_renew=True,
        )

    def allocate(self, license_sub, user, is_admin=False, is_active=True):
        return SchoolCreditAllocation.objects.create(
            license_subscription=license_sub,
            user=user,
            monthly_allocation=20_000,
            is_active=is_active,
            is_admin_allocation=is_admin,
            next_credit_grant_at=self.now + timedelta(days=30),
        )

    def wallet(self, user, reserved=0):
        wallet, _ = CreditWallet.objects.get_or_create(user=user)
        CreditBucket.objects.filter(wallet=wallet).delete()
        CreditWallet.objects.filter(pk=wallet.pk).update(reserved_credits=reserved)
        wallet.refresh_from_db()
        return wallet

    def bucket(self, wallet, total, used=0, expires_in=30, kind=None):
        return CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=kind or CreditBucketType.MONTHLY,
            total_credits=total,
            used_credits=used,
            expires_at=(
                None if expires_in is None else self.now + timedelta(days=expires_in)
            ),
        )

    def used(self, user, amount, refunded=False, feature="Grading Assignment"):
        wallet = CreditWallet.objects.get(user=user)
        # A log needs a bucket. With none, an expired empty one: it counts
        # in no balance, active count or breakdown.
        bucket = wallet.buckets.order_by("pk").first() or self.bucket(
            wallet, 0, expires_in=-400
        )
        return CreditUsageLog.objects.create(
            user_id=user.id,
            user_email=user.email,
            wallet=wallet,
            bucket=bucket,
            amount=amount,
            feature=feature,
            is_refunded=refunded,
        )

    # -- teaching --------------------------------------------------------

    def course(self, teacher):
        session = Session.objects.create(name="Term", teacher=teacher)
        return Course.objects.create(name="Biology", teacher=teacher, session=session)

    def assignment(self, course, **extra):
        return Assignment.objects.create(
            title="Quiz", course=course, total_points=10, **extra
        )

    def submission(self, assignment, ai_graded=True):
        self._n += 1
        student = self.person(user_type=UserTypes.STUDENT)
        return StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers={"q1": "a"},
            ai_graded_at=self.now if ai_graded else None,
        )

    # -- the list --------------------------------------------------------

    def get_list(self, client=None, **params):
        params.setdefault("page_size", 100)
        return (client or self.as_user(self.admin)).get(reverse("user-list"), params)

    def rows(self, **params):
        response = self.get_list(**params)
        self.assertEqual(response.status_code, 200, response.content)
        data = response.data
        return data["results"] if isinstance(data, dict) else data

    def row(self, user, **params):
        found = [r for r in self.rows(**params) if str(r["id"]) == str(user.pk)]
        self.assertEqual(len(found), 1, f"{user.email} not listed once")
        return found[0]

    def order_of(self, users, **params):
        """The listed order of `users` (everyone else dropped)."""
        wanted = {str(u.pk): u for u in users}
        return [
            wanted[str(r["id"])] for r in self.rows(**params) if str(r["id"]) in wanted
        ]


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


class DateValueTests(Base):
    def test_last_login_and_activated_at_are_listed(self):
        signed_in = self.now - timedelta(days=3)
        activated = self.now - timedelta(days=9)
        user = self.stamp(
            self.person(), last_login=signed_in, email_verified_at=activated
        )

        row = self.row(user)

        self.assertEqual(parse_datetime(row["last_login"]), signed_in)
        self.assertEqual(parse_datetime(row["activated_at"]), activated)

    def test_never_signed_in_or_activated_is_null(self):
        user = self.stamp(self.person(), last_login=None, email_verified_at=None)

        row = self.row(user)

        self.assertIsNone(row["last_login"])
        self.assertIsNone(row["activated_at"])


class PlanValueTests(Base):
    def plan_of(self, user):
        row = self.row(user)
        return (row["plan_tier"], row["plan_category"], row["plan_interval"])

    def test_an_individual_subscription_gives_its_plan(self):
        user = self.person()
        self.subscribe(
            user, self.plan(PlanType.PRO_ANNUAL, PlanTier.PRO, interval="ANNUAL")
        )

        self.assertEqual(self.plan_of(user), ("PRO", "INDIVIDUAL", "ANNUAL"))

    def test_an_inactive_subscription_gives_no_plan(self):
        user = self.person()
        self.subscribe(user, self.plan(PlanType.PRO, PlanTier.PRO), is_active=False)

        self.assertEqual(self.plan_of(user), (None, None, None))

    def test_no_subscription_gives_no_plan(self):
        self.assertEqual(self.plan_of(self.person()), (None, None, None))

    def test_a_license_teacher_allocation_wins_over_an_individual_one(self):
        teacher = self.person()
        admin = self.person(user_type=UserTypes.SCHOOL_ADMIN)
        self.subscribe(teacher, self.plan(PlanType.STANDARD, PlanTier.STANDARD))
        lic = self.license(
            self.plan(PlanType.POWER_LICENSE, PlanTier.POWER, PlanCategory.LICENSE),
            admin,
        )
        self.allocate(lic, teacher)

        self.assertEqual(self.plan_of(teacher), ("POWER", "LICENSE", "MONTHLY"))

    def test_a_license_admin_allocation_gives_the_license_plan(self):
        admin = self.person(user_type=UserTypes.SCHOOL_ADMIN)
        lic = self.license(
            self.plan(PlanType.PRO_LICENSE, PlanTier.PRO, PlanCategory.LICENSE),
            admin,
        )
        self.allocate(lic, admin, is_admin=True)

        self.assertEqual(self.plan_of(admin), ("PRO", "LICENSE", "MONTHLY"))

    def test_a_teacher_allocation_wins_over_an_admin_allocation(self):
        user = self.person()
        admin = self.person(user_type=UserTypes.SCHOOL_ADMIN)
        teacher_lic = self.license(
            self.plan(PlanType.POWER_LICENSE, PlanTier.POWER, PlanCategory.LICENSE),
            admin,
        )
        admin_lic = self.license(
            self.plan(PlanType.PRO_LICENSE, PlanTier.PRO, PlanCategory.LICENSE),
            admin,
        )
        self.allocate(admin_lic, user, is_admin=True)
        self.allocate(teacher_lic, user)

        self.assertEqual(self.plan_of(user)[0], "POWER")

    def test_an_inactive_allocation_or_license_is_skipped(self):
        user = self.person()
        admin = self.person(user_type=UserTypes.SCHOOL_ADMIN)
        self.subscribe(user, self.plan(PlanType.STANDARD, PlanTier.STANDARD))
        dead_license = self.license(
            self.plan(PlanType.POWER_LICENSE, PlanTier.POWER, PlanCategory.LICENSE),
            admin,
            is_active=False,
        )
        live_license = self.license(
            self.plan(PlanType.PRO_LICENSE, PlanTier.PRO, PlanCategory.LICENSE), admin
        )
        self.allocate(dead_license, user)
        self.allocate(live_license, user, is_active=False)

        self.assertEqual(self.plan_of(user), ("STANDARD", "INDIVIDUAL", "MONTHLY"))


class CountValueTests(Base):
    def test_assignments_created_counts_the_course_teachers_assignments(self):
        teacher = self.person()
        other = self.person()
        course = self.course(teacher)
        self.assignment(course)
        self.assignment(course)
        # Assignment.teacher is "in review for removal"; course.teacher owns.
        self.assignment(self.course(other), teacher=teacher)

        self.assertEqual(self.row(teacher)["assignments_created"], 2)
        self.assertEqual(self.row(other)["assignments_created"], 1)

    def test_papers_graded_counts_ai_graded_submissions_only(self):
        teacher = self.person()
        quiz = self.assignment(self.course(teacher))
        self.submission(quiz)
        self.submission(quiz)
        self.submission(quiz, ai_graded=False)
        self.submission(self.assignment(self.course(self.person())))

        self.assertEqual(self.row(teacher)["papers_graded"], 2)

    def test_counts_are_zero_not_null(self):
        row = self.row(self.person())

        self.assertEqual(row["assignments_created"], 0)
        self.assertEqual(row["papers_graded"], 0)


class CreditValueTests(Base):
    def test_credits_used_is_unrefunded_usage_in_display_units(self):
        user = self.person()
        self.wallet(user)
        self.used(user, 2_500)
        self.used(user, 1_700)
        self.used(user, 9_000, refunded=True)

        self.assertEqual(self.row(user)["credit_usage"], 4_200 // CONVERSION_FACTOR)

    def test_credits_used_is_zero_without_usage(self):
        self.assertEqual(self.row(self.person())["credit_usage"], 0)

    def test_credits_left_is_null_without_a_wallet(self):
        user = self.person()
        CreditWallet.objects.filter(user=user).delete()

        self.assertIsNone(self.row(user)["credits_left"])


class CreditsLeftParityTests(Base):
    """SQL credits_left == billing's spendable_credits(wallet) // 1000."""

    FIXTURES: dict[str, tuple[list[dict[str, Any]], int]] = {
        "no buckets": ([], 0),
        "one live bucket": ([{"total": 7_400}], 0),
        "partly used": ([{"total": 7_400, "used": 2_100}], 0),
        "expired bucket": ([{"total": 9_000, "expires_in": -1}], 0),
        "never expires": ([{"total": 3_000, "expires_in": None}], 0),
        "overage bucket": (
            [{"total": 2_000}, {"total": 5_000, "kind": CreditBucketType.OVERAGE}],
            0,
        ),
        "a hold": ([{"total": 10_000}], 4_300),
        "a hold above the balance": ([{"total": 1_000}], 6_000),
        "live, expired and used": (
            [
                {"total": 4_000, "used": 500},
                {"total": 8_000, "expires_in": -2},
                {"total": 1_999, "expires_in": None},
            ],
            999,
        ),
    }

    def test_each_fixture_matches_spendable_credits(self):
        users = {}
        for label, (buckets, reserved) in self.FIXTURES.items():
            user = self.person()
            wallet = self.wallet(user, reserved=reserved)
            for spec in buckets:
                self.bucket(wallet, **spec)
            users[label] = (user, wallet)

        listed = {str(r["id"]): r["credits_left"] for r in self.rows()}
        for label, (user, wallet) in users.items():
            with self.subTest(label):
                expected = spendable_credits(wallet) // CONVERSION_FACTOR
                self.assertEqual(listed[str(user.pk)], expected)

    def test_the_fixtures_are_not_all_zero(self):
        # Guard against a vacuous parity check.
        user = self.person()
        wallet = self.wallet(user, reserved=1_000)
        self.bucket(wallet, 7_400)

        self.assertEqual(self.row(user)["credits_left"], 6)


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


class OrderingTests(Base):
    def assert_order(self, key, expected_asc, nulls=()):
        """Ascending gives expected_asc then the nulls; descending gives
        expected_asc reversed then the nulls (D4)."""
        people = [*expected_asc, *nulls]
        with self.subTest(key=key, direction="asc"):
            got = self.order_of(people, ordering=key)
            self.assertEqual(got[: len(expected_asc)], list(expected_asc))
            self.assertCountEqual(got[len(expected_asc) :], list(nulls))
        with self.subTest(key=key, direction="desc"):
            got = self.order_of(people, ordering=f"-{key}")
            self.assertEqual(got[: len(expected_asc)], list(reversed(expected_asc)))
            self.assertCountEqual(got[len(expected_asc) :], list(nulls))

    def named(self, *names):
        # Default order is first_name: name them against the expected order.
        return [self.person(first_name=n) for n in names]

    def test_last_login(self):
        late, early, never = self.named("A", "B", "C")
        self.stamp(early, last_login=self.now - timedelta(days=5))
        self.stamp(late, last_login=self.now - timedelta(days=1))
        self.stamp(never, last_login=None)

        self.assert_order("last_login", [early, late], nulls=[never])

    def test_activated_at(self):
        late, early, never = self.named("A", "B", "C")
        self.stamp(early, email_verified_at=self.now - timedelta(days=50))
        self.stamp(late, email_verified_at=self.now - timedelta(days=2))
        self.stamp(never, email_verified_at=None)

        self.assert_order("activated_at", [early, late], nulls=[never])

    def test_plan_tier_follows_the_ruled_rank(self):
        # D3: TRIAL < BETA < STANDARD < PRO < POWER < CUSTOM. Users first,
        # plans after, so no automatic trial attaches itself.
        custom, power, pro, standard, beta, trial, none = self.named(
            "A", "B", "C", "D", "E", "F", "G"
        )
        for user, name, tier in (
            (trial, PlanType.TRIAL, PlanTier.TRIAL),
            (beta, PlanType.BETA, PlanTier.BETA),
            (standard, PlanType.STANDARD, PlanTier.STANDARD),
            (pro, PlanType.PRO, PlanTier.PRO),
            (power, PlanType.POWER, PlanTier.POWER),
            (custom, PlanType.CUSTOM, PlanTier.CUSTOM),
        ):
            self.subscribe(user, self.plan(name, tier))

        self.assert_order(
            "plan_tier", [trial, beta, standard, pro, power, custom], nulls=[none]
        )

    def test_plan_category(self):
        lic_teacher, individual, none = self.named("A", "B", "C")
        admin = self.person(user_type=UserTypes.SCHOOL_ADMIN)
        self.subscribe(individual, self.plan(PlanType.PRO, PlanTier.PRO))
        lic = self.license(
            self.plan(PlanType.PRO_LICENSE, PlanTier.PRO, PlanCategory.LICENSE), admin
        )
        self.allocate(lic, lic_teacher)

        self.assert_order("plan_category", [individual, lic_teacher], nulls=[none])

    def test_plan_interval(self):
        monthly, annual, none = self.named("A", "B", "C")
        self.subscribe(monthly, self.plan(PlanType.PRO, PlanTier.PRO))
        self.subscribe(
            annual, self.plan(PlanType.PRO_ANNUAL, PlanTier.PRO, interval="ANNUAL")
        )

        self.assert_order("plan_interval", [annual, monthly], nulls=[none])

    def test_assignments_created(self):
        many, one, zero = self.named("A", "B", "C")
        self.assignment(self.course(one))
        course = self.course(many)
        self.assignment(course)
        self.assignment(course)

        self.assert_order("assignments_created", [zero, one, many])

    def test_papers_graded(self):
        many, one, zero = self.named("A", "B", "C")
        self.submission(self.assignment(self.course(one)))
        quiz = self.assignment(self.course(many))
        self.submission(quiz)
        self.submission(quiz)

        self.assert_order("papers_graded", [zero, one, many])

    def test_credits_left(self):
        rich, poor, held, no_wallet = self.named("A", "B", "C", "D")
        self.bucket(self.wallet(poor), 2_000)
        self.bucket(self.wallet(rich), 9_000)
        self.bucket(self.wallet(held, reserved=4_000), 9_000)
        CreditWallet.objects.filter(user=no_wallet).delete()

        self.assert_order("credits_left", [poor, held, rich], nulls=[no_wallet])

    def test_credits_left_sorts_on_raw_credits(self):
        # 1_900 and 1_100 both show 1; the raw value still orders them.
        more, less = self.named("A", "B")
        self.bucket(self.wallet(more), 1_900)
        self.bucket(self.wallet(less), 1_100)

        self.assertEqual(
            self.order_of([more, less], ordering="credits_left"), [less, more]
        )

    def test_credit_usage(self):
        heavy, light, idle = self.named("A", "B", "C")
        for user in (heavy, light, idle):
            self.wallet(user)
        self.used(light, 1_000)
        self.used(heavy, 5_000)
        self.used(idle, 7_000, refunded=True)

        self.assert_order("credit_usage", [idle, light, heavy])

    def test_id_breaks_every_tie_in_both_directions(self):
        people = sorted(self.named("Z", "Y", "X", "W"), key=lambda u: u.pk)
        for key in NEW_KEYS:
            for ordering in (key, f"-{key}"):
                with self.subTest(ordering=ordering):
                    self.assertEqual(self.order_of(people, ordering=ordering), people)

    def test_an_unknown_key_is_ignored(self):
        b, a = self.named("B", "A")

        self.assertEqual(self.order_of([a, b], ordering="password"), [a, b])

    def test_the_old_keys_still_work(self):
        b, a = self.named("B", "A")

        self.assertEqual(self.order_of([a, b], ordering="-first_name"), [b, a])

    def test_two_keys_combine(self):
        a1, a2, b1 = self.named("A", "A", "B")
        self.stamp(a1, last_login=self.now - timedelta(days=1))
        self.stamp(a2, last_login=self.now - timedelta(days=2))
        self.stamp(b1, last_login=self.now - timedelta(days=3))

        self.assertEqual(
            self.order_of([a1, a2, b1], ordering="first_name,last_login"),
            [a2, a1, b1],
        )


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class FilterTests(Base):
    def listed(self, people, **params):
        return set(self.order_of(people, **params))

    def test_last_login_after_and_before(self):
        old, new, never = (self.person() for _ in range(3))
        self.stamp(old, last_login=self.now - timedelta(days=10))
        self.stamp(new, last_login=self.now - timedelta(days=1))
        self.stamp(never, last_login=None)
        cut = (self.now - timedelta(days=5)).isoformat()
        people = [old, new, never]

        self.assertEqual(self.listed(people, last_login_after=cut), {new})
        self.assertEqual(self.listed(people, last_login_before=cut), {old})

    def test_activated_after_and_before(self):
        old, new, never = (self.person() for _ in range(3))
        self.stamp(old, email_verified_at=self.now - timedelta(days=10))
        self.stamp(new, email_verified_at=self.now - timedelta(days=1))
        self.stamp(never, email_verified_at=None)
        cut = (self.now - timedelta(days=5)).isoformat()
        people = [old, new, never]

        self.assertEqual(self.listed(people, activated_after=cut), {new})
        self.assertEqual(self.listed(people, activated_before=cut), {old})

    def test_the_date_bounds_are_inclusive(self):
        user = self.stamp(self.person(), last_login=self.now)
        at = self.now.isoformat()

        self.assertEqual(self.listed([user], last_login_after=at), {user})
        self.assertEqual(self.listed([user], last_login_before=at), {user})

    def test_plan_filters(self):
        pro, power_annual, lic_teacher, none = (self.person() for _ in range(4))
        admin = self.person(user_type=UserTypes.SCHOOL_ADMIN)
        self.subscribe(pro, self.plan(PlanType.PRO, PlanTier.PRO))
        self.subscribe(
            power_annual,
            self.plan(PlanType.POWER_ANNUAL, PlanTier.POWER, interval="ANNUAL"),
        )
        lic = self.license(
            self.plan(PlanType.PRO_LICENSE, PlanTier.PRO, PlanCategory.LICENSE), admin
        )
        self.allocate(lic, lic_teacher)
        people = [pro, power_annual, lic_teacher, none]

        self.assertEqual(self.listed(people, plan_tier="PRO"), {pro, lic_teacher})
        self.assertEqual(self.listed(people, plan_tier="POWER"), {power_annual})
        self.assertEqual(self.listed(people, plan_category="LICENSE"), {lic_teacher})
        self.assertEqual(
            self.listed(people, plan_category="INDIVIDUAL"), {pro, power_annual}
        )
        self.assertEqual(self.listed(people, plan_interval="ANNUAL"), {power_annual})
        self.assertEqual(self.listed(people, has_plan="false"), {none})
        self.assertEqual(
            self.listed(people, has_plan="true"), {pro, power_annual, lic_teacher}
        )

    def test_a_bad_value_is_a_400(self):
        for params in (
            {"plan_tier": "GOLD"},
            {"plan_category": "FAMILY"},
            {"plan_interval": "WEEKLY"},
            {"last_login_after": "not-a-date"},
        ):
            with self.subTest(params):
                self.assertEqual(self.get_list(**params).status_code, 400)

    def test_filters_combine_with_ordering(self):
        a, b = (self.person() for _ in range(2))
        for user, days in ((a, 1), (b, 2)):
            self.stamp(user, last_login=self.now - timedelta(days=days))
        cut = (self.now - timedelta(days=5)).isoformat()

        self.assertEqual(
            self.order_of([a, b], last_login_after=cut, ordering="last_login"), [b, a]
        )


# ---------------------------------------------------------------------------
# D1, no per-row query
# ---------------------------------------------------------------------------


class QueryCountTests(Base):
    """D1 (B, owner 2026-11-11): a page costs a fixed number of queries,
    whatever its size. The base commit paid about 12 a row (NOTES M0)."""

    def make_teachers(self, n):
        # Every row lights each per-row path the payload had: a wallet with
        # buckets, an individual plan, settings.
        plan = None
        for i in range(n):
            user = self.person(first_name=f"T{i:02d}")
            self.bucket(self.wallet(user, reserved=100), 5_000, used=1_000)
            if plan is None:
                plan = self.plan(PlanType.PRO, PlanTier.PRO)
            self.subscribe(user, plan)
            self.used(user, 1_000)

    def count(self, page_size, **params):
        cache.clear()
        with CaptureQueriesContext(connection) as ctx:
            response = self.get_list(page_size=page_size, **params)
        self.assertEqual(response.status_code, 200, response.content)
        return len(ctx.captured_queries)

    def test_a_5_row_and_a_25_row_page_cost_the_same(self):
        self.make_teachers(30)

        self.assertEqual(self.count(5), self.count(25))

    def test_sorting_and_filtering_keep_the_cost_fixed(self):
        self.make_teachers(30)
        base = self.count(25)
        for size in (5, 25):
            for key in NEW_KEYS:
                with self.subTest(size=size, ordering=key):
                    self.assertEqual(self.count(size, ordering=f"-{key}"), base)
            with self.subTest(size=size, filters=True):
                self.assertEqual(
                    # Filters that keep every row: an empty page skips
                    # its prefetches and would compare nothing.
                    self.count(
                        size,
                        plan_tier="PRO",
                        has_plan="true",
                        plan_category="INDIVIDUAL",
                        ordering="credits_left",
                    ),
                    base,
                )


# ---------------------------------------------------------------------------
# D2, the cache key varies by every new param
# ---------------------------------------------------------------------------


class CacheKeyTests(Base):
    """No cache.clear() between requests: one generation, two keys."""

    def ids(self, people, **params):
        return self.order_of(people, **params)

    def test_ordering_in_both_directions_from_one_generation(self):
        low, high = self.person(first_name="A"), self.person(first_name="B")
        self.bucket(self.wallet(low), 1_000)
        self.bucket(self.wallet(high), 9_000)

        self.assertEqual(self.ids([low, high], ordering="credits_left"), [low, high])
        self.assertEqual(self.ids([low, high], ordering="-credits_left"), [high, low])

    def test_each_filter_from_one_generation(self):
        pro, power = self.person(), self.person()
        self.subscribe(pro, self.plan(PlanType.PRO, PlanTier.PRO))
        self.subscribe(power, self.plan(PlanType.POWER, PlanTier.POWER))

        self.assertEqual(self.ids([pro, power], plan_tier="PRO"), [pro])
        self.assertEqual(self.ids([pro, power], plan_tier="POWER"), [power])


# ---------------------------------------------------------------------------
# D7, the nine keys on the list only
# ---------------------------------------------------------------------------


class ListOnlyTests(Base):
    """Only the list carries the nine keys. No existing test pins a whole
    key set on these responses (D7 survey, NOTES); these pin absence."""

    def assert_none_of_the_keys(self, data, label):
        self.assertTrue(data.get("email"), f"{label}: not a user payload")
        present = sorted(set(NEW_KEYS) & set(data))
        self.assertEqual(present, [], label)

    def test_the_list_carries_all_nine(self):
        row = self.row(self.person())

        for key in NEW_KEYS:
            self.assertIn(key, row)

    def test_super_admin_non_list_responses(self):
        target = self.person()
        admin = self.as_user(self.admin)
        detail = reverse("user-detail", kwargs={"pk": target.pk})
        responses = {
            "retrieve": admin.get(detail),
            "retrieve with list params": admin.get(
                detail, {"ordering": "-credits_left", "has_plan": "false"}
            ),
            "me": admin.get(reverse("user-me")),
            "patch": admin.patch(detail, {"first_name": "New"}, format="json"),
            "lock": admin.post(
                reverse("user-lock", kwargs={"pk": target.pk}), {}, format="json"
            ),
            "unlock": admin.post(
                reverse("user-unlock", kwargs={"pk": target.pk}), {}, format="json"
            ),
        }
        for label, response in responses.items():
            with self.subTest(label):
                self.assertEqual(response.status_code, 200, response.content)
                self.assert_none_of_the_keys(response.data, label)

    def test_teacher_non_list_responses(self):
        teacher = self.stamp(self.person(), email_verified_at=self.now)
        client = self.as_user(teacher)
        detail = reverse("user-detail", kwargs={"pk": teacher.pk})
        responses = {
            "retrieve": client.get(detail),
            "me": client.get(reverse("user-me")),
            "patch": client.patch(detail, {"first_name": "New"}, format="json"),
        }
        for label, response in responses.items():
            with self.subTest(label):
                self.assertEqual(response.status_code, 200, response.content)
                self.assert_none_of_the_keys(response.data, label)

    def test_the_sign_in_payload(self):
        teacher = self.stamp(self.person(), email_verified_at=self.now)

        response = self.client_class().post(
            reverse("login"), {"email": teacher.email, "password": PW}, format="json"
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assert_none_of_the_keys(response.data["user"], "sign-in")

    def test_the_list_filters_do_nothing_off_the_list_for_others(self):
        # Added after R1 (NOTES): filter_queryset runs under get_object(), so
        # for a teacher a plan or date filter on a retrieve would answer
        # 200-vs-404 about another account. For anyone but a super admin
        # the new filters are no-ops.
        teacher = self.stamp(self.person(), email_verified_at=self.now)
        course = self.course(teacher)
        student = self.person(user_type=UserTypes.STUDENT)
        StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        client = self.as_user(teacher)
        detail = reverse("user-detail", kwargs={"pk": student.pk})
        self.assertEqual(client.get(detail).status_code, 200)

        # The retrieve cache key is the instance id alone, so each request
        # starts cold: a cached answer would hide the filter (Q-gate R1).
        for params in (
            {"plan_tier": "PRO"},
            {"has_plan": "true"},
            {"last_login_after": self.now.isoformat()},
            {"activated_before": (self.now - timedelta(days=999)).isoformat()},
        ):
            with self.subTest(params):
                cache.clear()
                self.assertEqual(client.get(detail, params).status_code, 200)

    def test_a_teacher_still_cannot_list(self):
        teacher = self.person()

        response = self.get_list(client=self.as_user(teacher), ordering="credits_left")

        self.assertEqual(response.status_code, 403)

    def test_only_the_super_admin_list_builds_the_list_values(self):
        """The per-teacher counts (course__teacher, D5) are built by
        list_queryset() only, and only for a super admin's list. The H-38
        sweep's ALLOWED entries for users/user_list.py rely on this."""
        import users.views

        createsuperuser_account = User.objects.create_superuser(
            email="cs.sort@x.example", password=PW
        )
        createsuperuser_account.refresh_from_db()
        self.assertNotEqual(createsuperuser_account.user_type, UserTypes.SUPER_ADMIN)
        others = (
            self.person(),
            self.person(user_type=UserTypes.SCHOOL_ADMIN),
            self.person(user_type=UserTypes.STUDENT),
            createsuperuser_account,
        )
        with patch.object(
            users.views, "list_queryset", wraps=users.views.list_queryset
        ) as built:
            for other in others:
                with self.subTest(other.user_type, superuser=other.is_superuser):
                    cache.clear()
                    response = self.get_list(
                        client=self.as_user(other), ordering="papers_graded"
                    )
                    self.assertEqual(response.status_code, 403)
                    self.assertEqual(built.call_count, 0)

            cache.clear()
            self.assertEqual(self.get_list().status_code, 200)
            # Twice: UserCacheMixin.get_cache_key reads get_queryset() for
            # the model name (never evaluated), then the page itself.
            self.assertGreater(built.call_count, 0)


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------


class OpenApiTests(Base):
    def list_parameters(self):
        from drf_spectacular import extensions
        from drf_spectacular.generators import SchemaGenerator

        # Generating a schema resolves every registered extension's
        # target_class from its import path to the class, in place, for the
        # rest of the process; assignments.tests_schema_extension pins the
        # path. Put each one back afterwards.
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

    def test_ordering_lists_every_allowed_value(self):
        ordering = self.list_parameters()["ordering"]
        allowed = set(
            ordering["schema"].get("items", ordering["schema"]).get("enum", [])
        )

        for key in (*NEW_KEYS, *OLD_ORDERING_KEYS):
            for value in (key, f"-{key}"):
                self.assertIn(value, allowed)
        self.assertNotIn("username", allowed)
        self.assertNotIn("-username", allowed)

    def test_every_new_filter_is_documented(self):
        params = self.list_parameters()

        for name in FILTER_PARAMS:
            self.assertIn(name, params)
