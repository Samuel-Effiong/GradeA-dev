"""
H-58 / H-59: POST /license-subscriptions counts seats correctly.

H-58: the seat check lower-cased the listed emails but did not de-duplicate
them, and compared them case-sensitively with the stored emails of the
teachers carried over from the current licence. The same teacher listed
twice ("dup@", "DUP@"), or listed once when already carried over under a
differently-cased stored address, was counted twice - a school with enough
seats was refused, or had to buy one more.

H-59: max_seats is optional and the view fell back to 0, which the seat
check read as "unlimited". On STRIPE the school went through checkout with
quantity 0 and the webhook's positive-seats guard refused the licence AFTER
payment. The shared seat check now refuses it before checkout.
"""

from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from billing.license_service import LicenseRequestError, LicenseSubscriptionService
from billing.models import (
    LicenseBillingMethod,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes

NO_SEATS = "max_seats must be a positive integer"


def licence_plan():
    return SubscriptionPlan.objects.create(
        name=PlanType.PRO,
        display_name="Seat Counting Plan",
        category=PlanCategory.LICENSE,
        tier=PlanTier.PRO,
        monthly_credits=20000,
        stripe_price_id="price_seatcount",
    )


def school_admin(email, school):
    return CustomUser.objects.create_user(
        email=email,
        password="password123",  # pragma: allowlist secret
        first_name="Seat",
        last_name="Admin",
        user_type=UserTypes.SCHOOL_ADMIN,
        school=school,
        is_active=True,
    )


class LicenseSeatCountingTest(APITestCase):
    def setUp(self):
        self.school = School.objects.create(name="Seat Count School")
        self.plan = licence_plan()
        school_admin("admin@seatcap.edu", self.school)
        superadmin = CustomUser.objects.create_superuser(
            email="superadmin@seatcount.example",
            password="password123",  # pragma: allowlist secret
            first_name="Super",
            last_name="Admin",
        )
        superadmin.user_type = UserTypes.SUPER_ADMIN
        superadmin.save()
        self.client.force_authenticate(user=superadmin)
        mail = patch("billing.license_service.send_email_task")
        mail.start()
        self.addCleanup(mail.stop)

    def payload(self, emails, max_seats=2, billing_method=LicenseBillingMethod.OFFLINE):
        return {
            "school": str(self.school.id),
            "plan": str(self.plan.id),
            "contract_months": 12,
            "max_seats": max_seats,
            "billing_method": billing_method,
            "custom_price_cents": 10000,
            "teacher_emails": emails,
        }

    def post_stripe(self, emails, max_seats):
        with patch("billing.stripe_service.stripe") as mock_stripe:
            mock_stripe.checkout.Session.create.return_value.url = (
                "https://checkout.example/session"
            )
            response = self.client.post(
                reverse("license-subscription-list"),
                self.payload(
                    emails,
                    max_seats=max_seats,
                    billing_method=LicenseBillingMethod.STRIPE,
                ),
                format="json",
            )
        return response, mock_stripe.checkout.Session.create

    def post_offline(self, emails, max_seats):
        return self.client.post(
            reverse("license-subscription-list"),
            self.payload(emails, max_seats=max_seats),
            format="json",
        )

    def teacher_count(self, license_sub):
        return license_sub.allocations.filter(
            is_active=True, is_admin_allocation=False
        ).count()

    def carry_over_a_teacher_stored_as(self, stored_email):
        """A current licence whose one teacher's stored address has
        capitals in its local part (normalize_email keeps them)."""
        LicenseSubscriptionService.create_license_subscription(
            school=self.school,
            plan=self.plan,
            teacher_emails=["kept@seatcap.edu"],
            max_seats=5,
            billing_method=LicenseBillingMethod.OFFLINE,
        )
        CustomUser.objects.filter(email="kept@seatcap.edu").update(email=stored_email)

    # --- H-58: one teacher is one seat ---

    def test_the_same_teacher_listed_twice_takes_one_seat(self):
        response = self.post_offline(["dup@seatcap.edu", "DUP@seatcap.edu "], 1)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.json())
        license_sub = LicenseSubscription.objects.get(is_active=True)
        self.assertEqual(self.teacher_count(license_sub), 1)
        self.assertEqual(response.json()["data"]["teacher_invitations"]["failed"], 0)

    def test_stripe_checkout_counts_a_repeated_teacher_once(self):
        response, session_create = self.post_stripe(
            ["dup@seatcap.edu", "Dup@SeatCap.edu"], max_seats=1
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        session_create.assert_called_once()

    def test_a_carried_over_teacher_listed_again_takes_one_seat(self):
        """Stored 'Kept@', listed 'kept@': one teacher, so 1 carried + 1 new
        fits 2 seats."""
        self.carry_over_a_teacher_stored_as("Kept@seatcap.edu")

        response = self.post_offline(["kept@seatcap.edu", "new@seatcap.edu"], 2)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.json())

    def test_stripe_checkout_matches_a_carried_over_teacher_by_any_case(self):
        self.carry_over_a_teacher_stored_as("Kept@seatcap.edu")

        response, session_create = self.post_stripe(
            ["kept@seatcap.edu", "new@seatcap.edu"], max_seats=2
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        session_create.assert_called_once()

    def test_the_refusal_counts_distinct_teachers(self):
        response = self.post_offline(
            ["a@seatcap.edu", "A@seatcap.edu", "b@seatcap.edu", "c@seatcap.edu"], 2
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            response.json()["message"],
            "This licence has 2 seats, but 3 teachers were added. "
            "Remove a teacher or increase Max seats.",
        )
        self.assertFalse(LicenseSubscription.objects.exists())

    # --- H-59: no seats is refused before anyone pays ---

    def test_stripe_checkout_without_max_seats_is_refused_before_payment(self):
        payload = self.payload(
            ["teacher1@seatcap.edu"], billing_method=LicenseBillingMethod.STRIPE
        )
        del payload["max_seats"]
        with patch("billing.stripe_service.stripe") as mock_stripe:
            response = self.client.post(
                reverse("license-subscription-list"), payload, format="json"
            )
        session_create = mock_stripe.checkout.Session.create

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["message"], NO_SEATS)
        session_create.assert_not_called()

    def test_the_shared_seat_check_refuses_no_seats(self):
        """The check both the checkout and the webhook's create run."""
        for max_seats in (0, -1):
            with self.subTest(max_seats=max_seats):
                with self.assertRaisesMessage(LicenseRequestError, NO_SEATS):
                    LicenseSubscriptionService.check_seat_capacity(
                        existing_license=None,
                        teacher_emails=[],
                        max_seats=max_seats,
                        carry_forward_teachers=True,
                    )


class AddTeachersSeatCountingTest(APITestCase):
    """The school admin's Add teachers: one teacher is one seat, and an
    address with capitals is the same account."""

    def setUp(self):
        self.school = School.objects.create(name="Add Teachers Count School")
        self.plan = licence_plan()
        self.client.force_authenticate(
            user=school_admin("admin@teacherchange.edu", self.school)
        )
        mail = patch("billing.license_service.send_email_task")
        mail.start()
        self.addCleanup(mail.stop)

    def licence(self, teachers, max_seats=2):
        return LicenseSubscriptionService.create_license_subscription(
            school=self.school,
            plan=self.plan,
            teacher_emails=teachers,
            max_seats=max_seats,
            billing_method=LicenseBillingMethod.OFFLINE,
        )

    def add(self, licence, emails):
        return self.client.post(
            reverse("license-subscription-add-teachers", kwargs={"pk": licence.pk}),
            {"teacher_emails": emails},
            format="json",
        )

    def teacher_allocations(self, licence):
        return SchoolCreditAllocation.objects.filter(
            license_subscription=licence, is_admin_allocation=False
        )

    def test_the_same_teacher_listed_twice_takes_one_seat(self):
        licence = self.licence(["t1@teacherchange.edu"])

        response = self.add(
            licence, ["new@teacherchange.edu", " NEW@teacherchange.edu"]
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        self.assertEqual(response.json()["data"]["successful"], 1)
        self.assertEqual(
            self.teacher_allocations(licence).filter(is_active=True).count(), 2
        )

    def test_an_active_teacher_in_capitals_takes_no_seat(self):
        """A full licence: re-adding one of its own teachers in capitals is
        not a new teacher, so it is not refused for lack of seats."""
        licence = self.licence(["t1@teacherchange.edu", "t2@teacherchange.edu"])

        response = self.add(licence, ["T1@TeacherChange.edu"])

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        self.assertEqual(
            self.teacher_allocations(licence).filter(is_active=True).count(), 2
        )

    def test_an_account_stored_with_capitals_is_not_duplicated(self):
        """Stored 'Mixed.Case@', added as 'mixed.case@': the existing account
        is enrolled, and no second account is created."""
        existing = CustomUser.objects.create_user(
            email="Mixed.Case@teacherchange.edu",
            password="password123",  # pragma: allowlist secret
            first_name="Mixed",
            last_name="Case",
            user_type=UserTypes.TEACHER,
            school=self.school,
            is_active=True,
        )
        licence = self.licence(["t1@teacherchange.edu"])

        response = self.add(licence, ["mixed.case@teacherchange.edu"])

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.json())
        self.assertEqual(
            CustomUser.objects.filter(
                email__iexact="mixed.case@teacherchange.edu"
            ).count(),
            1,
        )
        self.assertTrue(
            self.teacher_allocations(licence)
            .filter(user=existing, is_active=True)
            .exists()
        )
