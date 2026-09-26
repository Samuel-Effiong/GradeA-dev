"""
The individual track and the license track must not overlap on one account.

The product deliberately keeps a person's individual account and their school
account separate -- personal email + individual subscription on one side,
business email + school license on the other. The license side already
guards one direction rigorously: both
`LicenseSubscriptionService._get_or_invite_teacher` and
`_enroll_teacher_internal` raise `IndividualSubscriptionConflictError` rather
than pull an active individual subscriber onto a license seat.

That guard was one-directional. Nothing stopped the same person going the
OTHER way -- holding an active license seat and buying an individual plan
through `POST /subscriptions/select-plan`. It is not a cosmetic overlap:
`resolve_billing_context` resolves access license-first, so an active
allocation answers every credit request and the individual subscription the
user is being billed for every month is never consulted. They pay and get
nothing.

This suite pins both directions shut, plus the "half crossing" where a
personal-email account gets attached to a school without a seat.
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
    UserSubscription,
)
from billing.stripe_service import IndividualPlanChangeService
from classrooms.models import School
from users.models import CustomUser, UserTypes
from users.serializers import CustomUserSerializer

PASSWORD = "test123"  # pragma: allowlist secret


class TrackSeparationTestCase(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Acme School")

        self.admin = CustomUser.objects.create_user(
            email="admin@acme-school.org",
            password=PASSWORD,
            first_name="Admin",
            last_name="User",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )

        self.license_plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="License Pro",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.individual_plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Individual Standard",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            monthly_credits=5000,
            stripe_price_id="price_individual_basic",
        )

        self.license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.license_plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            max_seats=10,
        )

        self.teacher = CustomUser.objects.create_user(
            email="teacher@acme-school.org",
            password=PASSWORD,
            first_name="Licensed",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )

    def enroll(self, user, is_admin_allocation=False):
        return SchoolCreditAllocation.objects.create(
            license_subscription=self.license,
            user=user,
            is_active=True,
            is_admin_allocation=is_admin_allocation,
            monthly_allocation=1000,
        )

    def select_individual_plan(self, user):
        return IndividualPlanChangeService.select_plan(
            user=user,
            target_plan=self.individual_plan,
            success_url="https://example.test/ok",
            cancel_url="https://example.test/no",
        )


class LicenseToIndividualTests(TrackSeparationTestCase):
    """The direction that was wide open."""

    def test_licensed_teacher_cannot_buy_an_individual_plan(self):
        self.enroll(self.teacher)

        with self.assertRaises(ValueError) as ctx:
            self.select_individual_plan(self.teacher)

        self.assertIn("covered by Acme School's license", str(ctx.exception))
        self.assertFalse(UserSubscription.objects.filter(user=self.teacher).exists())

    def test_license_admin_cannot_buy_an_individual_plan(self):
        self.enroll(self.admin, is_admin_allocation=True)

        with self.assertRaises(ValueError) as ctx:
            self.select_individual_plan(self.admin)

        self.assertIn("license administrator", str(ctx.exception))

    def test_no_stripe_checkout_session_is_ever_opened(self):
        """The guard has to land before money moves, not after."""
        self.enroll(self.teacher)

        with patch("billing.stripe_service.stripe") as mock_stripe:
            with self.assertRaises(ValueError):
                self.select_individual_plan(self.teacher)

        mock_stripe.checkout.Session.create.assert_not_called()

    def test_the_checkout_builder_refuses_too(self):
        """Backstop: the guard is repeated at the last point before a Stripe
        session is created, so a future caller that skips select_plan can't
        reopen the hole."""
        from billing.stripe_service import StripeCheckoutService

        self.enroll(self.teacher)

        with patch("billing.stripe_service.stripe"):
            with self.assertRaises(ValueError) as ctx:
                StripeCheckoutService.create_individual_checkout_session(
                    self.teacher,
                    self.individual_plan,
                    "https://example.test/ok",
                    "https://example.test/no",
                )

        self.assertIn("license", str(ctx.exception).lower())

    def test_the_plan_change_lock_is_not_left_held(self):
        """The check runs before the lock is taken, so a rejection must not
        wedge every later plan change for that user."""
        from django.core.cache import cache

        self.enroll(self.teacher)

        with self.assertRaises(ValueError):
            self.select_individual_plan(self.teacher)

        self.assertIsNone(cache.get(f"billing:planchange:{self.teacher.id}"))

    def test_a_teacher_removed_from_the_license_may_subscribe(self):
        """Scoped to ACTIVE allocations: once a school drops a teacher, that
        teacher is on their own and must be able to buy their own plan."""
        allocation = self.enroll(self.teacher)
        allocation.is_active = False
        allocation.save(update_fields=["is_active"])

        # Reaches the real plan-selection logic instead of being turned away.
        try:
            IndividualPlanChangeService._assert_not_on_the_license_track(self.teacher)
        except ValueError as exc:  # pragma: no cover - failure path
            self.fail(f"Removed teacher was blocked: {exc}")

    def test_a_lapsed_license_does_not_block_its_teachers(self):
        self.enroll(self.teacher)
        self.license.is_active = False
        self.license.save(update_fields=["is_active"])

        try:
            IndividualPlanChangeService._assert_not_on_the_license_track(self.teacher)
        except ValueError as exc:  # pragma: no cover - failure path
            self.fail(f"Teacher of a lapsed license was blocked: {exc}")

    def test_an_unaffiliated_teacher_is_unaffected(self):
        solo = CustomUser.objects.create_user(
            email="solo@gmail.com",
            password=PASSWORD,
            first_name="Solo",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
        )

        try:
            IndividualPlanChangeService._assert_not_on_the_license_track(solo)
        except ValueError as exc:  # pragma: no cover - failure path
            self.fail(f"Individual teacher was blocked: {exc}")


class IndividualToLicenseTests(TrackSeparationTestCase):
    """The direction that was already guarded -- pinned so it stays that way."""

    def test_a_personal_email_teacher_cannot_be_invited_onto_a_license(self):
        with self.assertRaises(ValueError) as ctx:
            LicenseSubscriptionService._get_or_invite_teacher(
                "solo@gmail.com",
                self.school,
                self.admin,
                raise_on_conflict=True,
            )

        self.assertIn("not a business email", str(ctx.exception))
        self.assertFalse(CustomUser.objects.filter(email="solo@gmail.com").exists())

    def test_consumer_alias_domains_cannot_be_invited_either(self):
        """These all classified as BUSINESS before the classifier was
        rewritten, so each was a working way onto a license seat."""
        for email in [
            "solo@googlemail.com",
            "solo@yahoo.co.uk",
            "solo@proton.me",
            "solo@gmx.net",
            "solo@mailinator.com",
        ]:
            with self.subTest(email=email):
                with self.assertRaises(ValueError) as ctx:
                    LicenseSubscriptionService._get_or_invite_teacher(
                        email,
                        self.school,
                        self.admin,
                        raise_on_conflict=True,
                    )

                self.assertIn("not a business email", str(ctx.exception))
                self.assertFalse(CustomUser.objects.filter(email=email).exists())

    def test_an_individual_subscriber_cannot_be_enrolled(self):
        UserSubscription.objects.create(
            user=self.teacher,
            plan=self.individual_plan,
            is_active=True,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
        )

        with self.assertRaises(Exception) as ctx:
            LicenseSubscriptionService._enroll_teacher_internal(
                self.license, self.teacher
            )

        self.assertIn("individual subscription", str(ctx.exception))


class HalfCrossingTests(TrackSeparationTestCase):
    """A school attachment is the other half of a track crossing. It grants
    no seat on its own -- enrollment re-checks the email -- but it satisfies
    the school-membership half of that check."""

    def test_a_school_cannot_be_attached_to_a_personal_email_teacher(self):
        solo = CustomUser.objects.create_user(
            email="solo@gmail.com",
            password=PASSWORD,
            first_name="Solo",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
        )

        serializer = CustomUserSerializer(
            solo, data={"school": str(self.school.id)}, partial=True
        )
        serializer.fields["school"].read_only = False

        self.assertFalse(serializer.is_valid())
        self.assertIn("individual track", str(serializer.errors))

    def test_a_school_may_be_attached_to_a_business_email_teacher(self):
        invited = CustomUser.objects.create_user(
            email="newteacher@acme-school.org",
            password=PASSWORD,
            first_name="New",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
        )

        serializer = CustomUserSerializer(
            invited, data={"school": str(self.school.id)}, partial=True
        )
        serializer.fields["school"].read_only = False

        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_detaching_a_school_is_still_allowed(self):
        """`school: null` is the documented remedy for promoting a school
        member to SUPER_ADMIN and must not be caught by this rule."""
        serializer = CustomUserSerializer(
            self.teacher, data={"school": None}, partial=True
        )
        serializer.fields["school"].read_only = False

        self.assertTrue(serializer.is_valid(), serializer.errors)
