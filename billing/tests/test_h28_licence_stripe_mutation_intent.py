"""
billing/tests/test_h28_licence_stripe_mutation_intent.py
========================================================
The durable record of an irreversible Stripe change to a licence (H-28):
the per-licence in-flight guard, the idempotency keys, and the protections
that keep it trustworthy as evidence.

The guard is what replaces the row lock these operations held across the
Stripe call. Held in the database as a constraint, it serialises them the
way the lock did without keeping a transaction open while Stripe is slow.
These tests prove the constraint's shape; the concurrent race against real
Postgres is a Gate-3 test and lives with the service change.
"""

from datetime import timedelta

from django.contrib.admin.sites import AdminSite
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import RequestFactory, TestCase
from django.utils import timezone

from billing.admin import LicenseStripeMutationIntentAdmin
from billing.models import (
    LICENSE_STRIPE_MUTATION_IN_FLIGHT,
    LicenseBillingMethod,
    LicenseStripeMutationIntent,
    LicenseStripeMutationOperation,
    LicenseStripeMutationStatus,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes

TERMINAL = (
    LicenseStripeMutationStatus.COMPLETE,
    LicenseStripeMutationStatus.COMPENSATED,
    LicenseStripeMutationStatus.FAILED,
)


class LicenseStripeMutationIntentTests(TestCase):
    def setUp(self):
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="H-28 intent plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20_000,
            overage_block_size=5_000,
            overage_block_price=299,
        )
        self.licence = self._make_licence("H-28 Intent School", "a@h28i.edu")
        self.other_licence = self._make_licence("H-28 Other School", "b@h28i.edu")

    def _make_licence(self, school_name, admin_email):
        school = School.objects.create(name=school_name)
        admin = CustomUser.objects.create_user(
            email=admin_email,
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
        )
        return LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            billing_method=LicenseBillingMethod.STRIPE,
            is_active=True,
            auto_renew=True,
            stripe_subscription_id=f"sub_{school_name[:6]}",
        )

    def _intent(
        self, licence=None, status=LicenseStripeMutationStatus.PENDING, op=None
    ):
        licence = licence or self.licence
        return LicenseStripeMutationIntent.objects.create(
            license_subscription=licence,
            operation=op or LicenseStripeMutationOperation.UPDATE_SEATS,
            status=status,
            stripe_subscription_id=licence.stripe_subscription_id,
            requested_change={"old_max_seats": 1, "new_max_seats": 2},
        )

    # -- the per-licence guard ----------------------------------------------

    def test_in_flight_set_is_exactly_the_three_unresolved_states(self):
        self.assertEqual(
            set(LICENSE_STRIPE_MUTATION_IN_FLIGHT),
            {
                LicenseStripeMutationStatus.PENDING,
                LicenseStripeMutationStatus.STRIPE_APPLIED,
                LicenseStripeMutationStatus.ESCALATED,
            },
        )
        self.assertEqual(
            set(LICENSE_STRIPE_MUTATION_IN_FLIGHT) | set(TERMINAL),
            set(LicenseStripeMutationStatus.values),
            "every state must be classified as either in flight or terminal",
        )

    def test_each_in_flight_state_blocks_a_second_change_to_the_same_licence(self):
        for held in LICENSE_STRIPE_MUTATION_IN_FLIGHT:
            with self.subTest(held=held):
                first = self._intent(status=held)
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self._intent(status=LicenseStripeMutationStatus.PENDING)
                first.delete()

    def test_the_guard_is_per_licence_across_ALL_operations(self):
        """Deliberately not per (licence, operation): the row lock this
        replaces serialised every one of these operations on the same
        licence, so a seat change and a conversion to offline must not be
        able to interleave now where they never could before."""
        self._intent(op=LicenseStripeMutationOperation.UPDATE_SEATS)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._intent(op=LicenseStripeMutationOperation.CONVERT_TO_OFFLINE)

    def test_terminal_states_do_not_block(self):
        for done in TERMINAL:
            with self.subTest(done=done):
                self._intent(status=done)
        # Any number of finished intents, and still room for a new one.
        self._intent(status=LicenseStripeMutationStatus.PENDING)
        self.assertEqual(
            LicenseStripeMutationIntent.objects.filter(
                license_subscription=self.licence
            ).count(),
            len(TERMINAL) + 1,
        )

    def test_one_licence_in_flight_does_not_block_another_licence(self):
        self._intent(licence=self.licence)
        self._intent(licence=self.other_licence)

    def test_advancing_to_a_terminal_state_releases_the_licence(self):
        intent = self._intent(status=LicenseStripeMutationStatus.STRIPE_APPLIED)
        intent.status = LicenseStripeMutationStatus.COMPLETE
        intent.save(update_fields=["status", "updated_at"])
        self._intent(status=LicenseStripeMutationStatus.PENDING)

    def test_is_in_flight_matches_the_constraint(self):
        for status in LicenseStripeMutationStatus.values:
            with self.subTest(status=status):
                intent = LicenseStripeMutationIntent(status=status)
                self.assertEqual(
                    intent.is_in_flight, status in LICENSE_STRIPE_MUTATION_IN_FLIGHT
                )

    # -- idempotency keys ----------------------------------------------------

    def test_idempotency_key_is_stable_for_one_intent_and_step(self):
        intent = self._intent()
        self.assertEqual(
            intent.idempotency_key("apply"), intent.idempotency_key("apply")
        )
        self.assertIn(str(intent.id), intent.idempotency_key("apply"))

    def test_idempotency_key_differs_per_step_so_apply_and_revert_never_share(self):
        """Stripe rejects a reused key sent with different parameters, so a
        revert that reused the apply key would fail exactly when needed."""
        intent = self._intent()
        keys = {intent.idempotency_key(s) for s in ("apply", "revert", "void")}
        self.assertEqual(len(keys), 3)

    def test_idempotency_key_differs_between_intents(self):
        a = self._intent(status=LicenseStripeMutationStatus.COMPLETE)
        b = self._intent()
        self.assertNotEqual(a.idempotency_key("apply"), b.idempotency_key("apply"))

    # -- protection as evidence ---------------------------------------------

    def test_deleting_a_licence_cannot_take_its_intents_with_it(self):
        self._intent(status=LicenseStripeMutationStatus.COMPLETE)
        with self.assertRaises(ProtectedError):
            self.licence.delete()

    def test_admin_is_view_only(self):
        admin = LicenseStripeMutationIntentAdmin(
            LicenseStripeMutationIntent, AdminSite()
        )
        request = RequestFactory().get("/")
        request.user = CustomUser.objects.create_superuser(
            email="root@h28i.gradea.com",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
        )
        intent = self._intent()
        self.assertFalse(admin.has_add_permission(request))
        self.assertFalse(admin.has_change_permission(request, intent))
        self.assertFalse(admin.has_delete_permission(request, intent))
