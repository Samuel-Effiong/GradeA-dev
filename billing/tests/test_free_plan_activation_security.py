"""
Free-plan discovery and activation: every door, every role.

The attack this pins shut (reproduced live on 2026-09-17 before the fix):
any teacher could POST the BETA plan's id to /user-subscriptions or
/subscription as often as they liked, and each call ended their current
subscription and granted a fresh 10,000-credit monthly bucket. 30 rapid calls
all succeeded. School admins got TRIAL and the internal grading benchmark
plan the same way. /subscription/plan listed every plan (inactive, license,
internal), and an inactive free plan activated. A Stripe-billed subscriber
who did it was moved to BETA in the app while Stripe kept charging.

The rules now (billing/plan_policy.py):
  * POST /subscription and POST /user-subscriptions are superadmin-only.
  * Superadmin assignment goes through
    SubscriptionService.activate_plan_without_payment: an explicit plan
    allow-list, active plans only, BETA once per user ever and teachers
    only, never over a live Stripe subscription, never onto the license
    track, all under a per-user row lock.
  * Non-superadmin plan listings contain only the Stripe-priced
    self-service catalog, the same allow-list select-plan enforces.

Run with:
    python manage.py test billing.tests.test_free_plan_activation_security \
        --settings=settings_worktree
"""

import threading
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient, APITestCase

from billing.context import (
    clear_license_invitation_context,
    set_license_invitation_context,
)
from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.plan_policy import (
    PlanAssignmentRefused,
    admin_assignment_error,
    self_service_plans,
)
from billing.serializers import (
    SelectIndividualPlanSerializer,
    UserSubscriptionSerializer,
)
from billing.services import SubscriptionService
from classrooms.models import School
from users.models import CustomUser, UserTypes

PASSWORD = "Str0ng-test-pass!"  # pragma: allowlist secret
BETA_CREDITS = 10_000_000
ROUTES = ("subscription-list", "user-subscription-list")


class PlanCatalogMixin:
    """The plan rows present in the local database on 2026-09-17, plus the
    shapes the audit asked about (inactive, license, custom)."""

    beta_carry_over_percent = 0

    def create_plans(self):
        def plan(name, credits, price="0.00", **kw):
            defaults = {
                "category": PlanCategory.INDIVIDUAL,
                "tier": PlanTier.STANDARD,
                "interval": BillingInterval.MONTHLY,
                "is_active": True,
            }
            defaults.update(kw)
            return SubscriptionPlan.objects.create(
                name=name,
                display_name=name,
                monthly_credits=credits,
                price_cents=Decimal(price),
                **defaults,
            )

        self.beta = plan(
            PlanType.BETA,
            BETA_CREDITS,
            tier=PlanTier.BETA,
            carry_over_percent=self.beta_carry_over_percent,
            carry_over_expiry_months=1,
        )
        self.trial = plan(
            PlanType.TRIAL,
            5_000_000,
            tier=PlanTier.TRIAL,
            interval=BillingInterval.NONE,
        )
        self.benchmark = plan("Grading Benchmark Plan", 5_000_000)
        self.inactive_free = plan(
            PlanType.CUSTOM, 99_000_000, tier=PlanTier.CUSTOM, is_active=False
        )
        self.standard = plan(
            PlanType.STANDARD, 10_000_000, "1499.00", stripe_price_id="price_std"
        )
        self.pro = plan(
            PlanType.PRO,
            20_000_000,
            "2499.00",
            tier=PlanTier.PRO,
            stripe_price_id="price_pro",
        )
        self.standard_annual = plan(
            PlanType.STANDARD_ANNUAL,
            10_000_000,
            "14999.00",
            interval=BillingInterval.ANNUAL,
            stripe_price_id="price_std_annual",
        )
        self.inactive_paid = plan(
            PlanType.POWER,
            30_000_000,
            "3499.00",
            tier=PlanTier.POWER,
            stripe_price_id="price_power",
            is_active=False,
        )
        self.license_plan = plan(
            PlanType.PRO_LICENSE,
            20_000_000,
            "2499.00",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            stripe_price_id="price_pro_license",
        )
        # An internal plan someone linked to a Stripe price: active,
        # INDIVIDUAL and priced, so only the explicit allow-list keeps it out.
        self.internal_priced = plan(
            "INTERNAL_PRICED",
            50_000_000,
            "1.00",
            stripe_price_id="price_internal",
        )
        self.catalog_names = {
            PlanType.STANDARD,
            PlanType.PRO,
            PlanType.STANDARD_ANNUAL,
        }
        self.forbidden_plans = [
            self.beta,
            self.trial,
            self.benchmark,
            self.inactive_free,
            self.inactive_paid,
            self.license_plan,
            self.internal_priced,
        ]

    def make_licensed_school(self, license_plan=None):
        """A school license with an enrolled teacher and admin allocation,
        shaped like the license-track fixtures in test_track_separation."""
        school = School.objects.create(name=f"School {uuid.uuid4().hex[:6]}")
        admin = self.make_user(UserTypes.SCHOOL_ADMIN, school=school)
        # Invited the way LicenseSubscriptionService._get_or_invite_teacher
        # does it, so signup skips the automatic individual TRIAL.
        set_license_invitation_context(True)
        try:
            teacher = self.make_user(UserTypes.TEACHER, school=school)
        finally:
            clear_license_invitation_context()
        license_sub = LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=license_plan or self.license_plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            max_seats=5,
        )
        for user, is_admin in ((teacher, False), (admin, True)):
            SchoolCreditAllocation.objects.create(
                license_subscription=license_sub,
                user=user,
                is_active=True,
                is_admin_allocation=is_admin,
                monthly_allocation=1000,
            )
        return teacher, admin

    def make_user(self, user_type, **extra):
        user = CustomUser.objects.create_user(
            email=f"{user_type.lower()}-{uuid.uuid4().hex[:10]}@example.com",
            password=PASSWORD,
            user_type=user_type,
            is_active=True,
            **extra,
        )
        if user_type == UserTypes.SUPER_ADMIN:
            # Both flags, exactly as IsSuperAdmin requires.
            user.is_superuser = True
            user.is_staff = True
            user.save(update_fields=["is_superuser", "is_staff"])
        return user


def snapshot(user):
    """Every subscription, bucket and ledger row that belongs to `user`."""
    subs = list(
        UserSubscription.objects.filter(user=user)
        .order_by("created_at", "id")
        .values_list(
            "id", "plan__name", "is_active", "stripe_subscription_id", "stripe_status"
        )
    )
    buckets = list(
        CreditBucket.objects.filter(wallet__user=user)
        .order_by("created_at", "id")
        .values_list(
            "id",
            "bucket_type",
            "total_credits",
            "used_credits",
            "expires_at",
            "is_processed",
        )
    )
    ledger = sorted(
        str(pk)
        for pk in CreditLedger.objects.filter(user_id=user.id).values_list(
            "id", flat=True
        )
    )
    return subs, buckets, ledger


def beta_grants(user):
    """(BETA subscriptions ever, BETA-sized monthly buckets, BETA grant rows)."""
    return (
        UserSubscription.objects.filter(user=user, plan__name=PlanType.BETA).count(),
        CreditBucket.objects.filter(
            wallet__user=user,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=BETA_CREDITS,
        ).count(),
        CreditLedger.objects.filter(
            user_id=user.id,
            ledger_type=CreditLedgerType.GRANT,
            amount=BETA_CREDITS,
        ).count(),
    )


def live_balance(user):
    return sum(
        b.remaining_credits
        for b in CreditBucket.objects.filter(
            wallet__user=user, expires_at__gt=timezone.now()
        )
    )


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class PlanDiscoveryTests(PlanCatalogMixin, APITestCase):
    def setUp(self):
        self.create_plans()
        self.teacher = self.make_user(UserTypes.TEACHER)
        self.school_admin = self.make_user(UserTypes.SCHOOL_ADMIN)
        self.student = self.make_user(UserTypes.STUDENT)
        self.superadmin = self.make_user(UserTypes.SUPER_ADMIN)

    def names_from_subscription_plan(self, user):
        self.client.force_authenticate(user=user)
        response = self.client.get(reverse("subscription-plan"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return {row["name"] for row in response.data}

    def names_from_subscription_plans(self, user):
        self.client.force_authenticate(user=user)
        response = self.client.get(reverse("subscription-plan-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return {row["name"] for row in response.data["results"]}

    def test_ordinary_users_see_only_the_self_service_catalog(self):
        for user in (self.teacher, self.school_admin):
            with self.subTest(user_type=user.user_type):
                self.assertEqual(
                    self.names_from_subscription_plan(user), self.catalog_names
                )
                self.assertEqual(
                    self.names_from_subscription_plans(user), self.catalog_names
                )

    def test_students_see_only_the_catalog_where_they_can_list_at_all(self):
        self.client.force_authenticate(user=self.student)
        self.assertEqual(
            self.client.get(reverse("subscription-plan")).status_code,
            status.HTTP_403_FORBIDDEN,
        )
        self.assertEqual(
            self.names_from_subscription_plans(self.student), self.catalog_names
        )

    def test_forbidden_plans_are_never_listed(self):
        forbidden = {p.name for p in self.forbidden_plans}
        for user in (self.teacher, self.school_admin):
            with self.subTest(user_type=user.user_type):
                self.assertFalse(forbidden & self.names_from_subscription_plan(user))
                self.assertFalse(forbidden & self.names_from_subscription_plans(user))

    def test_a_catalog_plan_without_a_stripe_price_is_not_listed(self):
        SubscriptionPlan.objects.filter(pk=self.pro.pk).update(stripe_price_id="")
        self.assertNotIn(PlanType.PRO, self.names_from_subscription_plan(self.teacher))

    def test_a_catalog_plan_saved_without_a_price_is_neither_listed_nor_selectable(
        self,
    ):
        # price_cents defaults to 0; allow-listing alone must not make a
        # $0 plan a free self-serve plan.
        SubscriptionPlan.objects.filter(pk=self.pro.pk).update(price_cents=0)
        self.assertNotIn(PlanType.PRO, self.names_from_subscription_plan(self.teacher))
        self.assertNotIn(PlanType.PRO, self.names_from_subscription_plans(self.teacher))
        serializer = SelectIndividualPlanSerializer(
            data={
                "plan_id": str(self.pro.pk),
                "success_url": "https://example.test/ok",
                "cancel_url": "https://example.test/cancel",
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("no price", str(serializer.errors))

    def test_forbidden_plans_cannot_be_retrieved_by_id(self):
        # A school admin holds no subscription; the teacher is on the
        # automatic TRIAL from signup, which they may legitimately read.
        self.client.force_authenticate(user=self.school_admin)
        for plan in self.forbidden_plans:
            with self.subTest(plan=plan.name):
                response = self.client.get(
                    reverse("subscription-plan-detail", kwargs={"pk": plan.pk})
                )
                self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_user_can_still_retrieve_the_plan_they_are_on(self):
        SubscriptionService.activate_plan_without_payment(self.teacher, self.beta)
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(
            reverse("subscription-plan-detail", kwargs={"pk": self.beta.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # ...without it leaking into the selectable list.
        self.assertNotIn(
            PlanType.BETA, self.names_from_subscription_plans(self.teacher)
        )

    def test_superadmin_still_sees_every_plan(self):
        every = set(SubscriptionPlan.objects.values_list("name", flat=True))
        self.assertEqual(self.names_from_subscription_plan(self.superadmin), every)

    def test_self_service_selection_uses_the_same_allow_list(self):
        listed = set(self_service_plans().values_list("pk", flat=True))
        for plan in SubscriptionPlan.objects.all():
            with self.subTest(plan=plan.name):
                serializer = SelectIndividualPlanSerializer(
                    data={
                        "plan_id": str(plan.pk),
                        "success_url": "https://example.test/ok",
                        "cancel_url": "https://example.test/cancel",
                    }
                )
                self.assertEqual(serializer.is_valid(), plan.pk in listed)


# ---------------------------------------------------------------------------
# Ordinary users: both activation routes are closed
# ---------------------------------------------------------------------------


class OrdinaryUserActivationTests(PlanCatalogMixin, APITestCase):
    def setUp(self):
        self.create_plans()
        self.teacher = self.make_user(UserTypes.TEACHER)
        self.school_admin = self.make_user(UserTypes.SCHOOL_ADMIN)
        self.student = self.make_user(UserTypes.STUDENT)

    def post(self, client, route, user, plan_id):
        return client.post(
            reverse(route), {"user": str(user.id), "plan": str(plan_id)}, format="json"
        )

    def assert_refused_without_state_change(self, user, plan_ids, attempts=1):
        before = snapshot(user)
        self.client.force_authenticate(user=user)
        for route in ROUTES:
            for plan_id in plan_ids:
                for _ in range(attempts):
                    response = self.post(self.client, route, user, plan_id)
                    self.assertEqual(
                        response.status_code,
                        status.HTTP_403_FORBIDDEN,
                        f"{user.user_type} {route} {plan_id}: {response.data}",
                    )
        self.assertEqual(snapshot(user), before)

    def test_every_plan_is_refused_on_both_routes_for_every_ordinary_role(self):
        every = list(SubscriptionPlan.objects.values_list("pk", flat=True))
        for user in (self.teacher, self.school_admin, self.student):
            with self.subTest(user_type=user.user_type):
                self.assert_refused_without_state_change(user, every)

    def test_ten_repeated_beta_requests_grant_nothing(self):
        self.assert_refused_without_state_change(
            self.teacher, [self.beta.pk], attempts=10
        )
        self.assertEqual(beta_grants(self.teacher), (0, 0, 0))

    def test_thirty_rapid_requests_grant_nothing(self):
        self.assert_refused_without_state_change(
            self.teacher, [self.beta.pk, self.trial.pk, self.benchmark.pk], attempts=10
        )

    def test_school_admin_gets_no_trial_or_benchmark_credits(self):
        self.assert_refused_without_state_change(
            self.school_admin, [self.trial.pk, self.benchmark.pk], attempts=3
        )
        self.assertFalse(CreditBucket.objects.filter(wallet__user=self.school_admin))

    def test_an_arbitrary_or_unknown_plan_id_is_refused(self):
        self.assert_refused_without_state_change(self.teacher, [uuid.uuid4()])

    def test_a_fresh_login_does_not_reopen_the_route(self):
        client = APIClient()
        login = client.post(
            reverse("login"),
            {"email": self.teacher.email, "password": PASSWORD},
            format="json",
        )
        self.assertEqual(login.status_code, status.HTTP_200_OK, login.content)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        before = snapshot(self.teacher)
        for route in ROUTES:
            response = self.post(client, route, self.teacher, self.beta.pk)
            self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(snapshot(self.teacher), before)

    def test_a_stripe_billed_teacher_cannot_switch_themselves_to_beta(self):
        paid = SubscriptionService.activate_subscription(self.teacher, self.pro)
        paid.stripe_subscription_id = "sub_teacher_paid"
        paid.stripe_status = StripeSubscriptionStatus.ACTIVE
        paid.save(
            update_fields=["stripe_subscription_id", "stripe_status", "updated_at"]
        )

        with mock.patch("billing.stripe_service.stripe") as mock_stripe:
            self.assert_refused_without_state_change(
                self.teacher, [self.beta.pk, self.trial.pk], attempts=3
            )

        self.assertFalse(mock_stripe.method_calls)
        self.assertEqual(
            UserSubscription.objects.get(user=self.teacher, is_active=True).pk, paid.pk
        )

    def test_the_serializer_refuses_non_superadmins_on_its_own(self):
        """Defence in depth: if the serializer is ever wired to a view that
        forgets the permission, it still refuses."""
        request = mock.Mock(user=self.teacher)
        serializer = UserSubscriptionSerializer(
            data={"user": str(self.teacher.id), "plan": str(self.beta.pk)},
            context={"request": request},
        )
        self.assertFalse(serializer.is_valid())

    def single_flag_superusers(self):
        # create_superuser() leaves user_type at the TEACHER default.
        flag_only = CustomUser.objects.create_superuser(
            email=f"flag-only-{uuid.uuid4().hex[:8]}@example.com", password=PASSWORD
        )
        type_only = self.make_user(UserTypes.TEACHER)
        type_only.user_type = UserTypes.SUPER_ADMIN
        type_only.save(update_fields=["user_type"])
        return flag_only, type_only

    def test_single_flag_superusers_are_refused_by_both_routes(self):
        for actor in self.single_flag_superusers():
            with self.subTest(
                is_superuser=actor.is_superuser, user_type=actor.user_type
            ):
                before = snapshot(self.teacher)
                self.client.force_authenticate(user=actor)
                for route in ROUTES:
                    response = self.post(self.client, route, self.teacher, self.pro.pk)
                    self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
                self.assertEqual(snapshot(self.teacher), before)

    def test_the_serializer_refuses_single_flag_superusers_on_its_own(self):
        for actor in self.single_flag_superusers():
            with self.subTest(
                is_superuser=actor.is_superuser, user_type=actor.user_type
            ):
                serializer = UserSubscriptionSerializer(
                    data={"user": str(self.teacher.id), "plan": str(self.pro.pk)},
                    context={"request": mock.Mock(user=actor)},
                )
                self.assertFalse(serializer.is_valid())
                self.assertIn("Only a superadmin", str(serializer.errors))

    def free_custom_license_plan(self):
        # create-custom-license with no price: price_cents defaults to 0.
        return SubscriptionPlan.objects.create(
            name=PlanType.CUSTOM_LICENSE_STARTER,
            display_name="Custom license, no price",
            category=PlanCategory.LICENSE,
            tier=PlanTier.CUSTOM,
            interval=BillingInterval.MONTHLY,
            monthly_credits=40_000_000,
            is_active=True,
        )

    def test_a_licensed_teacher_cannot_activate_the_license_plan_from_me(self):
        free_license = self.free_custom_license_plan()
        teacher, _ = self.make_licensed_school(license_plan=free_license)
        self.client.force_authenticate(user=teacher)
        me = self.client.get(reverse("subscription-get-my-subscription"))
        self.assertEqual(me.status_code, status.HTTP_200_OK, me.data)
        self.assertEqual(me.data["subscription_source"], "LICENSE_TEACHER")
        license_plan_id = me.data["plan"]["id"]
        self.assertEqual(str(license_plan_id), str(free_license.pk))

        self.assert_refused_without_state_change(teacher, [license_plan_id], attempts=3)

    def test_a_school_admin_cannot_activate_license_zero_price_or_inactive_plans(self):
        free_license = self.free_custom_license_plan()
        _, admin = self.make_licensed_school(license_plan=free_license)
        for user in (admin, self.school_admin):
            with self.subTest(licensed=user is admin):
                self.assert_refused_without_state_change(
                    user,
                    [
                        free_license.pk,
                        self.license_plan.pk,
                        self.beta.pk,
                        self.trial.pk,
                        self.benchmark.pk,
                        self.inactive_free.pk,
                        self.inactive_paid.pk,
                    ],
                )

    def test_zero_price_custom_and_license_plans_are_refused_for_every_role(self):
        free_license = self.free_custom_license_plan()
        zero_custom_individual = self.inactive_free
        SubscriptionPlan.objects.filter(pk=zero_custom_individual.pk).update(
            is_active=True
        )
        for user in (self.teacher, self.school_admin, self.student):
            with self.subTest(user_type=user.user_type):
                self.assert_refused_without_state_change(
                    user, [free_license.pk, zero_custom_individual.pk]
                )

    def test_one_users_attempts_change_nothing_for_another_user(self):
        other = self.make_user(UserTypes.TEACHER)
        other_before = snapshot(other)
        self.client.force_authenticate(user=self.teacher)
        for route in ROUTES:
            for target in (self.teacher, other):
                for plan in (self.beta, self.trial, self.benchmark):
                    self.post(self.client, route, target, plan.pk)
        self.assertEqual(snapshot(other), other_before)


# ---------------------------------------------------------------------------
# Superadmin assignment: the stateful rules
# ---------------------------------------------------------------------------


class AdminAssignmentTests(PlanCatalogMixin, APITestCase):
    def setUp(self):
        self.create_plans()
        self.superadmin = self.make_user(UserTypes.SUPER_ADMIN)
        self.teacher = self.make_user(UserTypes.TEACHER)
        self.client.force_authenticate(user=self.superadmin)

    def assign(self, user, plan, route="subscription-list"):
        return self.client.post(
            reverse(route), {"user": str(user.id), "plan": str(plan.pk)}, format="json"
        )

    def assert_refused(self, user, plan, route="subscription-list"):
        before = snapshot(user)
        response = self.assign(user, plan, route)
        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.data
        )
        self.assertEqual(snapshot(user), before)
        return response

    def test_first_beta_assignment_grants_exactly_one_bucket(self):
        # Production signup gave this teacher the automatic TRIAL.
        self.assertTrue(
            UserSubscription.objects.filter(user=self.teacher, is_trial=True).exists()
        )

        response = self.assign(self.teacher, self.beta)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        active = UserSubscription.objects.get(user=self.teacher, is_active=True)
        self.assertEqual(active.plan, self.beta)
        self.assertEqual(beta_grants(self.teacher), (1, 1, 1))
        self.assertEqual(live_balance(self.teacher), BETA_CREDITS)
        self.assertFalse(
            CreditBucket.objects.filter(
                wallet__user=self.teacher, bucket_type=CreditBucketType.CARRY_OVER
            ).exists()
        )

    def test_second_beta_assignment_is_refused_on_both_routes(self):
        self.assertEqual(self.assign(self.teacher, self.beta).status_code, 201)
        for route in ROUTES:
            with self.subTest(route=route):
                response = self.assert_refused(self.teacher, self.beta, route)
                self.assertIn("only be granted once", str(response.data))
        self.assertEqual(beta_grants(self.teacher), (1, 1, 1))

    def test_ten_repeats_and_thirty_rapid_requests_yield_one_grant(self):
        codes = [self.assign(self.teacher, self.beta).status_code for _ in range(10)]
        codes += [
            self.assign(self.teacher, self.beta, ROUTES[i % 2]).status_code
            for i in range(30)
        ]
        self.assertEqual(codes.count(201), 1)
        self.assertEqual(codes.count(400), 39)
        self.assertEqual(beta_grants(self.teacher), (1, 1, 1))

    def test_beta_cannot_be_regranted_after_consuming_the_credits(self):
        self.assign(self.teacher, self.beta)
        wallet = CreditWallet.objects.get(user=self.teacher)
        wallet.consume_credits(live_balance(self.teacher), feature="grading")
        self.assertEqual(live_balance(self.teacher), 0)

        self.assert_refused(self.teacher, self.beta)
        self.assertEqual(live_balance(self.teacher), 0)

    def test_beta_cannot_be_regranted_after_the_subscription_expires(self):
        self.assign(self.teacher, self.beta)
        past = timezone.now() - timedelta(days=1)
        UserSubscription.objects.filter(user=self.teacher).update(
            is_active=False, billing_cycle_end=past
        )
        CreditBucket.objects.filter(wallet__user=self.teacher).update(expires_at=past)

        self.assert_refused(self.teacher, self.beta)
        self.assertFalse(
            UserSubscription.objects.filter(user=self.teacher, is_active=True).exists()
        )

    def test_switching_away_and_back_does_not_regrant_beta(self):
        self.assign(self.teacher, self.beta)
        self.assertEqual(self.assign(self.teacher, self.pro).status_code, 201)

        self.assert_refused(self.teacher, self.beta)
        self.assertEqual(
            UserSubscription.objects.get(user=self.teacher, is_active=True).plan,
            self.pro,
        )
        self.assertEqual(beta_grants(self.teacher), (1, 1, 1))

    def test_inactive_plans_never_activate_even_by_direct_id(self):
        for plan in (self.inactive_free, self.inactive_paid):
            with self.subTest(plan=plan.name):
                response = self.assert_refused(self.teacher, plan)
                self.assertIn("inactive", str(response.data))

    def test_trial_benchmark_and_license_plans_are_not_assignable(self):
        for plan in (
            self.trial,
            self.benchmark,
            self.license_plan,
            self.internal_priced,
        ):
            for route in ROUTES:
                with self.subTest(plan=plan.name, route=route):
                    self.assert_refused(self.teacher, plan, route)

    def test_beta_is_teacher_only(self):
        for user_type in (UserTypes.SCHOOL_ADMIN, UserTypes.STUDENT):
            with self.subTest(user_type=user_type):
                user = self.make_user(user_type)
                response = self.assert_refused(user, self.beta)
                self.assertIn("teacher", str(response.data))

    def test_a_stripe_billed_subscriber_cannot_be_moved_to_beta_or_any_plan(self):
        # Written the way checkout.session.completed writes a new subscriber.
        paid = SubscriptionService.activate_subscription(self.teacher, self.pro)
        paid.stripe_subscription_id = "sub_live_paid"
        paid.stripe_status = StripeSubscriptionStatus.ACTIVE
        paid.save(
            update_fields=["stripe_subscription_id", "stripe_status", "updated_at"]
        )

        with mock.patch("billing.stripe_service.stripe") as mock_stripe:
            for plan in (self.beta, self.standard):
                with self.subTest(plan=plan.name):
                    response = self.assert_refused(self.teacher, plan)
                    self.assertIn("Stripe", str(response.data))

        self.assertFalse(mock_stripe.method_calls)
        active = UserSubscription.objects.get(user=self.teacher, is_active=True)
        self.assertEqual(active.pk, paid.pk)
        # Stripe events for this subscription still resolve to the active row.
        self.assertEqual(
            UserSubscription.objects.get(stripe_subscription_id="sub_live_paid").pk,
            active.pk,
        )
        self.assertEqual(beta_grants(self.teacher), (0, 0, 0))

    def stripe_billed(self, user, status_value, **fields):
        # Written the way checkout.session.completed writes a subscriber,
        # then moved to the status under test the way the Stripe
        # subscription.updated / invoice handlers set it.
        paid = SubscriptionService.activate_subscription(user, self.pro)
        paid.stripe_subscription_id = f"sub_{uuid.uuid4().hex[:12]}"
        paid.stripe_status = status_value
        for name, value in fields.items():
            setattr(paid, name, value)
        paid.save()
        return paid

    def test_every_live_stripe_status_blocks_assignment(self):
        cases = [
            ("ACTIVE", StripeSubscriptionStatus.ACTIVE, {}),
            ("TRIALING", StripeSubscriptionStatus.TRIALING, {}),
            ("PAST_DUE", StripeSubscriptionStatus.PAST_DUE, {}),
            ("INCOMPLETE", StripeSubscriptionStatus.INCOMPLETE, {}),
            ("UNPAID", StripeSubscriptionStatus.UNPAID, {}),
            ("no status recorded", None, {}),
            (
                "cancel at period end",
                StripeSubscriptionStatus.ACTIVE,
                {"auto_renew": False, "cancelled_at": timezone.now()},
            ),
        ]
        for label, status_value, fields in cases:
            with self.subTest(case=label):
                teacher = self.make_user(UserTypes.TEACHER)
                paid = self.stripe_billed(teacher, status_value, **fields)
                for plan in (self.beta, self.standard):
                    self.assert_refused(teacher, plan)
                self.assertEqual(
                    UserSubscription.objects.get(user=teacher, is_active=True).pk,
                    paid.pk,
                )
                self.assertEqual(beta_grants(teacher), (0, 0, 0))

    def test_beta_granted_before_this_change_counts_as_history(self):
        # Exactly how the old self-service endpoint and the old signup
        # path wrote it: straight through activate_subscription.
        for label in ("still active", "expired"):
            with self.subTest(history=label):
                teacher = self.make_user(UserTypes.TEACHER)
                SubscriptionService.activate_subscription(teacher, self.beta)
                if label == "expired":
                    past = timezone.now() - timedelta(days=1)
                    UserSubscription.objects.filter(user=teacher).update(
                        is_active=False, billing_cycle_end=past
                    )
                    CreditBucket.objects.filter(wallet__user=teacher).update(
                        expires_at=past
                    )
                response = self.assert_refused(teacher, self.beta)
                self.assertIn("only be granted once", str(response.data))

    def test_non_assignable_plans_do_not_resolve_by_id(self):
        for plan in (
            self.trial,
            self.benchmark,
            self.license_plan,
            self.internal_priced,
        ):
            with self.subTest(plan=plan.name):
                serializer = UserSubscriptionSerializer(
                    context={"request": mock.Mock(user=self.superadmin)}
                )
                with self.assertRaises(ValidationError) as ctx:
                    serializer.to_internal_value(
                        {"user": str(self.teacher.id), "plan": str(plan.pk)}
                    )
                self.assertIn("Invalid plan id", str(ctx.exception.detail))

    def test_the_service_refuses_non_assignable_plans_without_the_serializer(self):
        """The no-payment service enforces the plan rules itself, so a
        caller that skips the serializer's plan lookup cannot bypass them."""
        for plan in (
            self.trial,
            self.benchmark,
            self.license_plan,
            self.internal_priced,
            self.inactive_free,
            self.inactive_paid,
        ):
            with self.subTest(plan=plan.name):
                before = snapshot(self.teacher)
                with self.assertRaises(PlanAssignmentRefused):
                    SubscriptionService.activate_plan_without_payment(
                        self.teacher, plan
                    )
                self.assertEqual(snapshot(self.teacher), before)

    def test_a_license_category_plan_sharing_a_catalog_name_is_refused(self):
        # test_track_separation creates exactly this shape: a LICENSE plan
        # named PRO. Only the category check tells it apart.
        license_named_pro = SubscriptionPlan(
            name=PlanType.PRO, category=PlanCategory.LICENSE, is_active=True
        )
        self.assertIn("INDIVIDUAL", admin_assignment_error(license_named_pro) or "")

    def test_assigning_one_user_changes_nothing_for_another(self):
        other = self.make_user(UserTypes.TEACHER)
        SubscriptionService.activate_plan_without_payment(other, self.beta)
        other_before = snapshot(other)

        self.assertEqual(self.assign(self.teacher, self.beta).status_code, 201)
        self.assign(self.teacher, self.beta)
        self.assign(self.teacher, self.pro)

        self.assertEqual(snapshot(other), other_before)

    def test_a_cancelled_stripe_subscription_does_not_block_assignment(self):
        paid = SubscriptionService.activate_subscription(self.teacher, self.pro)
        paid.stripe_subscription_id = "sub_cancelled"
        paid.stripe_status = StripeSubscriptionStatus.CANCELED
        paid.save(
            update_fields=["stripe_subscription_id", "stripe_status", "updated_at"]
        )

        self.assertEqual(self.assign(self.teacher, self.beta).status_code, 201)

    def test_a_comped_paid_plan_assignment_still_works(self):
        response = self.assign(
            self.teacher, self.standard_annual, "user-subscription-list"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(
            UserSubscription.objects.get(user=self.teacher, is_active=True).plan,
            self.standard_annual,
        )

    def test_licensed_teachers_and_license_admins_are_refused(self):
        teacher, admin = self.make_licensed_school()
        for user, plan in (
            (teacher, self.beta),
            (teacher, self.pro),
            (admin, self.pro),
        ):
            for route in ROUTES:
                with self.subTest(
                    user_type=user.user_type, plan=plan.name, route=route
                ):
                    response = self.assert_refused(user, plan, route)
                    self.assertIn("license", str(response.data).lower())

    def test_superadmin_cannot_assign_any_license_plan_even_at_zero_price(self):
        free_license = SubscriptionPlan.objects.create(
            name=PlanType.CUSTOM_LICENSE_STARTER,
            category=PlanCategory.LICENSE,
            tier=PlanTier.CUSTOM,
            interval=BillingInterval.MONTHLY,
            monthly_credits=40_000_000,
            is_active=True,
        )
        for plan in (free_license, self.license_plan):
            for route in ROUTES:
                with self.subTest(plan=plan.name, route=route):
                    response = self.assert_refused(self.teacher, plan, route)
                    self.assertIn("Invalid plan id", str(response.data))


class HighCarryOverOrdinaryUserTests(OrdinaryUserActivationTests):
    """The ordinary-user refusals again with BETA at 100% carry-over."""

    beta_carry_over_percent = 100


class HighCarryOverAssignmentTests(AdminAssignmentTests):
    """Every rule again with BETA configured to roll over 100% of unused
    credits and no max_bank, the configuration where repeated activation
    would have stacked buckets."""

    beta_carry_over_percent = 100

    def test_repeated_attempts_manufacture_no_carry_over(self):
        self.assign(self.teacher, self.beta)
        for _ in range(5):
            self.assign(self.teacher, self.beta)

        self.assertFalse(
            CreditBucket.objects.filter(
                wallet__user=self.teacher, bucket_type=CreditBucketType.CARRY_OVER
            ).exists()
        )
        self.assertEqual(live_balance(self.teacher), BETA_CREDITS)
        self.assertEqual(beta_grants(self.teacher), (1, 1, 1))


# ---------------------------------------------------------------------------
# Signup-time BETA uses the same rules
# ---------------------------------------------------------------------------


class SignupBetaTests(PlanCatalogMixin, TestCase):
    def setUp(self):
        self.create_plans()

    def test_signup_grants_beta_once_and_the_rule_then_holds(self):
        with self.settings(USE_BETA_PLAN_ON_SIGNUP=True):
            teacher = self.make_user(UserTypes.TEACHER)

        self.assertEqual(beta_grants(teacher), (1, 1, 1))
        with self.assertRaises(PlanAssignmentRefused):
            SubscriptionService.activate_plan_without_payment(teacher, self.beta)
        self.assertEqual(beta_grants(teacher), (1, 1, 1))

    def test_later_saves_never_grant_beta(self):
        """Only creation grants. Turning the flag on later, verifying or
        reactivating the account, or changing its type must not."""
        teacher = self.make_user(UserTypes.TEACHER)  # flag off: TRIAL only
        student = self.make_user(UserTypes.STUDENT)

        with self.settings(USE_BETA_PLAN_ON_SIGNUP=True):
            teacher.first_name = "Edited"
            teacher.save()
            teacher.is_active = False
            teacher.save()
            teacher.is_active = True
            teacher.save()
            student.user_type = UserTypes.TEACHER
            student.save()

        self.assertEqual(beta_grants(teacher), (0, 0, 0))
        self.assertEqual(beta_grants(student), (0, 0, 0))
        self.assertFalse(UserSubscription.objects.filter(user=student).exists())

    def test_signup_does_not_activate_an_inactive_beta_plan(self):
        SubscriptionPlan.objects.filter(pk=self.beta.pk).update(is_active=False)
        with self.settings(USE_BETA_PLAN_ON_SIGNUP=True):
            with self.assertLogs("users.signals", level="ERROR"):
                teacher = self.make_user(UserTypes.TEACHER)

        self.assertEqual(beta_grants(teacher), (0, 0, 0))


# ---------------------------------------------------------------------------
# Concurrency: real threads, real connections, real row locks
# ---------------------------------------------------------------------------


class CommitOrdering:
    """
    Stands in for SubscriptionService.activate_subscription and forces the
    interleaving that defeats a check-then-act guard: every request that
    has already passed the eligibility checks meets at a barrier, then the
    first proceeds and commits while the rest wait for that commit before
    writing. With the per-user lock in place a second request can never
    reach this point while the first holds the lock, so the barrier times
    out and the requests run one after another.
    """

    def __init__(self, original, parties, timeout=2.0):
        self.original = original
        self.barrier = threading.Barrier(parties, timeout=timeout)
        self.first_committed = threading.Event()
        self.lock = threading.Lock()
        self.calls = 0

    def __call__(self, *args, **kwargs):
        try:
            self.barrier.wait()
        except threading.BrokenBarrierError:
            pass
        with self.lock:
            self.calls += 1
            order = self.calls
        if order == 1:
            transaction.on_commit(self.first_committed.set)
        else:
            self.first_committed.wait(timeout=10)
        return self.original(*args, **kwargs)


class ConcurrentActivationTests(PlanCatalogMixin, TransactionTestCase):
    def setUp(self):
        self.create_plans()
        self.superadmin = self.make_user(UserTypes.SUPER_ADMIN)
        self.teacher = self.make_user(UserTypes.TEACHER)

    def run_concurrently(self, count, target):
        start = threading.Barrier(count)
        results: list = [None] * count

        def worker(index):
            try:
                start.wait(timeout=10)
                results[index] = target(index)
            except Exception as exc:  # recorded, asserted on below
                results[index] = exc
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        return results

    def http_assign(self, index):
        client = APIClient()
        client.force_authenticate(user=self.superadmin)
        return client.post(
            reverse(ROUTES[index % 2]),
            {"user": str(self.teacher.id), "plan": str(self.beta.pk)},
            format="json",
        ).status_code

    def test_racing_requests_that_all_pass_the_checks_yield_one_grant(self):
        original = SubscriptionService.activate_subscription
        ordering = CommitOrdering(original, parties=2)
        with mock.patch.object(
            SubscriptionService, "activate_subscription", side_effect=ordering
        ):
            codes = self.run_concurrently(2, self.http_assign)

        self.assertEqual(sorted(codes), [201, 400], codes)
        self.assertEqual(beta_grants(self.teacher), (1, 1, 1))
        self.assertEqual(live_balance(self.teacher), BETA_CREDITS)
        self.assertEqual(
            UserSubscription.objects.filter(user=self.teacher, is_active=True).count(),
            1,
        )

    def test_a_burst_of_simultaneous_requests_yields_one_grant(self):
        codes = self.run_concurrently(8, self.http_assign)

        self.assertEqual(codes.count(201), 1, codes)
        self.assertEqual(codes.count(400), 7, codes)
        self.assertEqual(beta_grants(self.teacher), (1, 1, 1))

    def test_concurrent_service_calls_yield_one_grant(self):
        def call(_):
            try:
                SubscriptionService.activate_plan_without_payment(
                    CustomUser.objects.get(pk=self.teacher.pk), self.beta
                )
                return "granted"
            except PlanAssignmentRefused:
                return "refused"

        results = self.run_concurrently(6, call)

        self.assertEqual(results.count("granted"), 1, results)
        self.assertEqual(results.count("refused"), 5, results)
        self.assertEqual(beta_grants(self.teacher), (1, 1, 1))
