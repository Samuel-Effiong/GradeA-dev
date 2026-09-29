"""
Hotfix: POST /license-subscriptions with more teachers than seats answered
500 "An unexpected error occurred".

create_license_subscription refused the request with a bare ValueError that
nothing between it and the view caught. The service now raises
LicenseRequestError (a ValueError subclass) for refusals the caller can fix,
the serializer turns ONLY that type into a 400 with the message, and the
Stripe checkout makes the same seat check before the school pays.
"""

from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from billing.license_service import LicenseRequestError, LicenseSubscriptionService
from billing.models import (
    LicenseBillingMethod,
    LicenseBillingRecord,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes

SEATS_2_TEACHERS_3 = (
    "This licence has 2 seats, but 3 teachers were added. "
    "Remove a teacher or increase Max seats."
)
SEATS_2_CARRIED_1_NEW_2 = (
    "This licence has 2 seats, but 3 teachers were added "
    "(1 carried over from the current licence + 2 new). "
    "Remove a teacher or increase Max seats."
)


class LicenseSeatCapIs400Test(APITestCase):
    def setUp(self):
        self.school = School.objects.create(name="Seat Cap School")
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Seat Cap License Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.school_admin = CustomUser.objects.create_user(
            email="admin@seatcap.edu",
            password="password123",  # pragma: allowlist secret
            first_name="Seat",
            last_name="Admin",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=True,
        )
        superadmin = CustomUser.objects.create_superuser(
            email="superadmin@seatcap.example",
            password="password123",  # pragma: allowlist secret
            first_name="Super",
            last_name="Admin",
        )
        superadmin.user_type = UserTypes.SUPER_ADMIN
        superadmin.is_active = True
        superadmin.save()
        self.client.force_authenticate(user=superadmin)
        self.url = reverse("license-subscription-list")
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

    def three_teachers(self):
        return [f"teacher{i}@seatcap.edu" for i in (1, 2, 3)]

    def assert_nothing_created(self):
        self.assertFalse(LicenseSubscription.objects.exists())
        self.assertFalse(LicenseBillingRecord.objects.exists())
        self.assertFalse(
            CustomUser.objects.filter(email__in=self.three_teachers()).exists()
        )

    def test_offline_create_over_the_seat_cap_is_a_400_with_the_message(self):
        response = self.client.post(
            self.url, self.payload(self.three_teachers()), format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        body = response.json()
        self.assertIs(body["success"], False)
        # One clean sentence: no field label, no numbered list.
        self.assertEqual(body["message"], SEATS_2_TEACHERS_3)
        self.assert_nothing_created()

    def test_carried_over_teachers_are_counted_and_the_old_licence_is_untouched(self):
        old = LicenseSubscriptionService.create_license_subscription(
            school=self.school,
            plan=self.plan,
            teacher_emails=["kept@seatcap.edu"],
            max_seats=5,
            billing_method=LicenseBillingMethod.OFFLINE,
        )
        allocations_before = list(
            old.allocations.order_by("pk").values_list("pk", "is_active")
        )

        response = self.client.post(
            self.url,
            self.payload(["new1@seatcap.edu", "new2@seatcap.edu"]),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["message"], SEATS_2_CARRIED_1_NEW_2)
        old.refresh_from_db()
        self.assertTrue(old.is_active)
        self.assertEqual(
            LicenseSubscription.objects.filter(school=self.school).count(), 1
        )
        self.assertEqual(
            list(old.allocations.order_by("pk").values_list("pk", "is_active")),
            allocations_before,
        )

    def test_two_teachers_on_two_seats_is_created(self):
        """Positive control: the cap refuses only what exceeds it."""
        response = self.client.post(
            self.url,
            self.payload(["teacher1@seatcap.edu", "teacher2@seatcap.edu"]),
            format="json",
        )

        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.content
        )
        license_sub = LicenseSubscription.objects.get(school=self.school)
        self.assertEqual(
            license_sub.allocations.filter(
                is_active=True, is_admin_allocation=False
            ).count(),
            2,
        )

    def test_stripe_checkout_over_the_seat_cap_is_a_400_before_stripe(self):
        """The school must not be able to pay for a licence the webhook
        would then refuse to create."""
        with patch("billing.stripe_service.stripe") as mock_stripe:
            # A real string, so a checkout that wrongly goes ahead renders
            # as a 200 rather than a MagicMock the JSON encoder chokes on.
            mock_stripe.checkout.Session.create.return_value.url = (
                "https://checkout.example/session"
            )
            response = self.client.post(
                self.url,
                self.payload(
                    self.three_teachers(), billing_method=LicenseBillingMethod.STRIPE
                ),
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.json()["message"], SEATS_2_TEACHERS_3)
        mock_stripe.checkout.Session.create.assert_not_called()
        self.assertFalse(LicenseSubscription.objects.exists())

    def test_a_bare_value_error_is_still_a_500(self):
        """Only LicenseRequestError is a user error; any other ValueError
        from the service is a bug and must not be dressed up as a 400."""
        self.client.raise_request_exception = False
        with patch.object(
            LicenseSubscriptionService,
            "create_license_subscription",
            side_effect=ValueError("a real bug"),
        ):
            response = self.client.post(
                self.url, self.payload(["teacher1@seatcap.edu"]), format="json"
            )

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertNotIn("a real bug", response.content.decode())


class SeatCapacityMessageTest(APITestCase):
    def test_singular_wording(self):
        with self.assertRaisesMessage(
            LicenseRequestError,
            "This licence has 1 seat, but 2 teachers were added. "
            "Remove a teacher or increase Max seats.",
        ):
            LicenseSubscriptionService.check_seat_capacity(
                existing_license=None,
                teacher_emails=["a@x.edu", "b@x.edu"],
                max_seats=1,
                carry_forward_teachers=True,
            )

    def test_is_still_a_value_error_for_existing_callers(self):
        self.assertTrue(issubclass(LicenseRequestError, ValueError))
