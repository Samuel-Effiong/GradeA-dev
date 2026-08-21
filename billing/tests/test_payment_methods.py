"""
Tests for PaymentMethodViewSet (billing/payment_method_views.py) and the
supporting StripeCustomerService.get_customer_for_request_user /
create_setup_intent_for_request_user helpers, plus the
handle_setup_intent_succeeded webhook's metadata-driven default-setting
behavior.

All Stripe API calls are mocked via @patch("stripe.X") on the real
stripe module's attributes (same convention as
test_subscription_cycle_integrity.py).
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import (
    LicenseBillingMethod,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.stripe_service import StripeWebhookHandler
from billing.tests.test_subscription_cycle_integrity import FakeStripeObject
from classrooms.models import School
from users.models import CustomUser, UserTypes


class FakeStripeList:
    """Minimal stand-in for a Stripe ListObject — supports .data and
    .auto_paging_iter(), which is all this codebase's list endpoint uses."""

    def __init__(self, items):
        self.data = items

    def auto_paging_iter(self):
        return iter(self.data)


def make_card(pm_id, brand="visa", last4="1234", customer="cus_test"):
    return FakeStripeObject(
        {
            "id": pm_id,
            "customer": customer,
            "card": FakeStripeObject(
                {"brand": brand, "last4": last4, "exp_month": 3, "exp_year": 2030}
            ),
        }
    )


class PaymentMethodListTests(APITestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.individual_user = CustomUser.objects.create_user(
            email="solo@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Solo",
            last_name="User",
            user_type=UserTypes.TEACHER,
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Individual Plan",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            monthly_credits=5000,
        )
        UserSubscription.objects.create(
            user=self.individual_user,
            plan=self.plan,
            is_active=True,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
        )
        from billing.models import CreditWallet

        wallet, _ = CreditWallet.objects.get_or_create(user=self.individual_user)
        wallet.stripe_customer_id = "cus_individual"
        wallet.save(update_fields=["stripe_customer_id"])

        self.admin = CustomUser.objects.create_user(
            email="admin@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Admin",
            last_name="User",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.license_plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="License Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.license_sub = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.license_plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            stripe_customer_id="cus_license",
        )

        self.teacher = CustomUser.objects.create_user(
            email="teacher@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="User",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        SchoolCreditAllocation.objects.create(
            license_subscription=self.license_sub,
            user=self.teacher,
            monthly_allocation=20000,
            is_active=True,
            is_admin_allocation=False,
        )

        self.no_context_user = CustomUser.objects.create_user(
            email="nobody@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="No",
            last_name="Context",
            user_type=UserTypes.TEACHER,
        )

        self.list_url = reverse("payment-method-list")

    @patch("stripe.Customer")
    @patch("stripe.PaymentMethod")
    def test_list_individual_customer_marks_default(self, mock_pm, mock_customer):
        card1 = make_card("pm_1", customer="cus_individual")
        card2 = make_card("pm_2", customer="cus_individual")
        mock_pm.list.return_value = FakeStripeList([card1, card2])
        mock_customer.retrieve.return_value = FakeStripeObject(
            {"invoice_settings": {"default_payment_method": "pm_2"}}
        )

        self.client.force_authenticate(user=self.individual_user)
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_pm.list.assert_called_once_with(customer="cus_individual", type="card")
        by_id = {c["id"]: c for c in response.data}
        self.assertFalse(by_id["pm_1"]["is_default"])
        self.assertTrue(by_id["pm_2"]["is_default"])

    @patch("stripe.Customer")
    @patch("stripe.PaymentMethod")
    def test_list_license_admin_customer(self, mock_pm, mock_customer):
        card1 = make_card("pm_1", customer="cus_license")
        mock_pm.list.return_value = FakeStripeList([card1])
        mock_customer.retrieve.return_value = FakeStripeObject(
            {"invoice_settings": {"default_payment_method": None}}
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_pm.list.assert_called_once_with(customer="cus_license", type="card")

    @patch("stripe.Customer")
    @patch("stripe.PaymentMethod")
    def test_list_rejects_teacher(self, mock_pm, mock_customer):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        mock_pm.list.assert_not_called()

    @patch("stripe.Customer")
    @patch("stripe.PaymentMethod")
    def test_list_rejects_no_billing_context(self, mock_pm, mock_customer):
        self.client.force_authenticate(user=self.no_context_user)
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        mock_pm.list.assert_not_called()


class PaymentMethodAddTests(APITestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="solo@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Solo",
            last_name="User",
            user_type=UserTypes.TEACHER,
        )
        plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Individual Plan",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            monthly_credits=5000,
        )
        UserSubscription.objects.create(
            user=self.user,
            plan=plan,
            is_active=True,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
        )
        from billing.models import CreditWallet

        wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        wallet.stripe_customer_id = "cus_individual"
        wallet.save(update_fields=["stripe_customer_id"])
        self.create_url = reverse("payment-method-list")

    @patch("stripe.SetupIntent")
    def test_add_without_set_as_default(self, mock_setup_intent):
        mock_setup_intent.create.return_value = FakeStripeObject(
            {"client_secret": "seti_secret_1"}
        )
        self.client.force_authenticate(user=self.user)

        response = self.client.post(self.create_url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["client_secret"], "seti_secret_1")
        mock_setup_intent.create.assert_called_once_with(
            customer="cus_individual",
            payment_method_types=["card"],
            usage="off_session",
            metadata={"set_as_default": "false"},
        )

    @patch("stripe.SetupIntent")
    def test_add_with_set_as_default(self, mock_setup_intent):
        mock_setup_intent.create.return_value = FakeStripeObject(
            {"client_secret": "seti_secret_2"}
        )
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            self.create_url, {"set_as_default": True}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_setup_intent.create.assert_called_once_with(
            customer="cus_individual",
            payment_method_types=["card"],
            usage="off_session",
            metadata={"set_as_default": "true"},
        )


class SetupIntentWebhookTests(TestCase):
    """Regression coverage for handle_setup_intent_succeeded's switch from
    branching on metadata.license_id to branching on
    metadata.set_as_default."""

    @patch("stripe.Customer")
    def test_general_flow_set_as_default_false_does_not_change_default(
        self, mock_customer
    ):
        setup_intent = FakeStripeObject(
            {
                "id": "seti_1",
                "metadata": {"set_as_default": "false"},
                "payment_method": "pm_new",
                "customer": "cus_individual",
            }
        )

        StripeWebhookHandler.handle_setup_intent_succeeded(setup_intent)

        mock_customer.modify.assert_not_called()

    @patch("stripe.Customer")
    def test_general_flow_set_as_default_true_sets_default(self, mock_customer):
        setup_intent = FakeStripeObject(
            {
                "id": "seti_2",
                "metadata": {"set_as_default": "true"},
                "payment_method": "pm_new",
                "customer": "cus_individual",
            }
        )

        StripeWebhookHandler.handle_setup_intent_succeeded(setup_intent)

        mock_customer.modify.assert_called_once_with(
            "cus_individual",
            invoice_settings={"default_payment_method": "pm_new"},
        )

    @patch("stripe.Customer")
    def test_license_flow_still_sets_default(self, mock_customer):
        """Regression guard: create_license_setup_intent now explicitly
        sets set_as_default=true in metadata, so the license flow's
        existing always-default behavior must be unchanged."""
        setup_intent = FakeStripeObject(
            {
                "id": "seti_3",
                "metadata": {"license_id": "abc", "set_as_default": "true"},
                "payment_method": "pm_license_card",
                "customer": "cus_license",
            }
        )

        StripeWebhookHandler.handle_setup_intent_succeeded(setup_intent)

        mock_customer.modify.assert_called_once_with(
            "cus_license",
            invoice_settings={"default_payment_method": "pm_license_card"},
        )


class PaymentMethodDeleteAndSetDefaultTests(APITestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="solo@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Solo",
            last_name="User",
            user_type=UserTypes.TEACHER,
        )
        plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Individual Plan",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            monthly_credits=5000,
        )
        self.subscription = UserSubscription.objects.create(
            user=self.user,
            plan=plan,
            is_active=True,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            stripe_subscription_id="sub_active",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )
        from billing.models import CreditWallet

        wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        wallet.stripe_customer_id = "cus_individual"
        wallet.save(update_fields=["stripe_customer_id"])

        self.school = School.objects.create(name="Offline School")
        self.license_admin = CustomUser.objects.create_user(
            email="offlineadmin@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Offline",
            last_name="Admin",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        license_plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="License Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.offline_license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.license_admin,
            plan=license_plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            stripe_customer_id="cus_offline_license",
            billing_method=LicenseBillingMethod.OFFLINE,
        )

    def _delete_url(self, pm_id):
        return reverse("payment-method-detail", kwargs={"pk": pm_id})

    def _set_default_url(self, pm_id):
        return reverse("payment-method-set-default", kwargs={"pk": pm_id})

    @patch("stripe.PaymentMethod")
    def test_delete_rejects_not_owned_card(self, mock_pm):
        mock_pm.retrieve.return_value = make_card(
            "pm_other", customer="cus_someone_else"
        )
        self.client.force_authenticate(user=self.user)

        response = self.client.delete(self._delete_url("pm_other"))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_pm.detach.assert_not_called()

    @patch("stripe.PaymentMethod")
    def test_delete_blocks_last_card_with_active_subscription(self, mock_pm):
        card = make_card("pm_only", customer="cus_individual")
        mock_pm.retrieve.return_value = card
        mock_pm.list.return_value = FakeStripeList([card])
        self.client.force_authenticate(user=self.user)

        response = self.client.delete(self._delete_url("pm_only"))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_pm.detach.assert_not_called()

    @patch("stripe.PaymentMethod")
    def test_delete_allowed_when_multiple_cards_exist(self, mock_pm):
        card1 = make_card("pm_1", customer="cus_individual")
        card2 = make_card("pm_2", customer="cus_individual")
        mock_pm.retrieve.return_value = card1
        mock_pm.list.return_value = FakeStripeList([card1, card2])
        self.client.force_authenticate(user=self.user)

        response = self.client.delete(self._delete_url("pm_1"))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        mock_pm.detach.assert_called_once_with("pm_1")

    @patch("stripe.PaymentMethod")
    def test_delete_allowed_for_offline_license_regardless_of_card_count(self, mock_pm):
        card = make_card("pm_only", customer="cus_offline_license")
        mock_pm.retrieve.return_value = card
        mock_pm.list.return_value = FakeStripeList([card])
        self.client.force_authenticate(user=self.license_admin)

        response = self.client.delete(self._delete_url("pm_only"))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        mock_pm.detach.assert_called_once_with("pm_only")

    @patch("stripe.Customer")
    @patch("stripe.PaymentMethod")
    def test_set_default_rejects_not_owned_card(self, mock_pm, mock_customer):
        mock_pm.retrieve.return_value = make_card(
            "pm_other", customer="cus_someone_else"
        )
        self.client.force_authenticate(user=self.user)

        response = self.client.post(self._set_default_url("pm_other"))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_customer.modify.assert_not_called()

    @patch("stripe.Customer")
    @patch("stripe.PaymentMethod")
    def test_set_default_happy_path(self, mock_pm, mock_customer):
        mock_pm.retrieve.return_value = make_card("pm_1", customer="cus_individual")
        self.client.force_authenticate(user=self.user)

        response = self.client.post(self._set_default_url("pm_1"))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        mock_customer.modify.assert_called_once_with(
            "cus_individual", invoice_settings={"default_payment_method": "pm_1"}
        )
