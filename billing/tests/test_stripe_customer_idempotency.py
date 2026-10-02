"""
billing/tests/test_stripe_customer_idempotency.py
=================================================
`get_or_create_customer` reads `wallet.stripe_customer_id`, and if it is
empty calls `stripe.Customer.create()`. Two simultaneous requests can both
pass that read and both create a Stripe Customer; the second DB write then
overwrites the first, orphaning a customer object that a card may later be
attached to.

WHY NOT A LOCK
--------------
All three callers (`get_customer_for_request_user`,
`create_individual_checkout_session`, `create_overage_checkout_session`)
are checkout/SetupIntent session builders that run OUTSIDE a transaction.
`select_for_update()` requires one, so locking would mean opening a
transaction and holding a row lock across an outbound Stripe call — the
very pattern flagged as a risk in the webhook handlers. Keying the remote
call is the smaller, safer fix, and `idempotency_key` is already the
convention in this module (see the interval-change refund).

Stripe returns the SAME customer for repeated calls sharing a key, so the
losing request's write becomes a no-op instead of creating a duplicate.

These tests pin the contract we control: that a stable, caller-specific
key is sent, and that an existing customer is still never re-created.
Proving Stripe's own dedup would require the live API.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    CreditWallet,
    LicenseBillingMethod,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
)
from billing.stripe_service import StripeCustomerService
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()


class StripeCustomerCreationIdempotencyTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="customer.idem@gmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )

    def test_creation_sends_a_stable_idempotency_key(self):
        with patch("billing.stripe_service.stripe.Customer.create") as create:
            create.return_value = SimpleNamespace(id="cus_abc")
            StripeCustomerService.get_or_create_customer(self.user)

        self.assertEqual(
            create.call_args.kwargs["idempotency_key"],
            f"customer-for-user-{self.user.id}",
        )

    def test_two_racing_creates_send_the_same_key(self):
        """
        The property that makes the race harmless: both requests key the
        remote call identically, so Stripe hands back one customer.
        """
        keys = []

        def _create(**kwargs):
            keys.append(kwargs["idempotency_key"])
            # Stripe's own dedup: same key -> same customer.
            return SimpleNamespace(id="cus_same")

        with patch(
            "billing.stripe_service.stripe.Customer.create", side_effect=_create
        ):
            # Simulate the race: both callers observed an empty wallet.
            first = StripeCustomerService.get_or_create_customer(self.user)
            CreditWallet.objects.filter(user=self.user).update(stripe_customer_id="")
            second = StripeCustomerService.get_or_create_customer(self.user)

        self.assertEqual(len(set(keys)), 1, "racing calls used different keys")
        self.assertEqual(first, second)

    def test_an_existing_customer_is_never_recreated(self):
        wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        wallet.stripe_customer_id = "cus_existing"
        wallet.save(update_fields=["stripe_customer_id"])

        with patch("billing.stripe_service.stripe.Customer.create") as create:
            result = StripeCustomerService.get_or_create_customer(self.user)

        create.assert_not_called()
        self.assertEqual(result, "cus_existing")

    def test_the_customer_id_is_persisted_to_the_wallet(self):
        with patch("billing.stripe_service.stripe.Customer.create") as create:
            create.return_value = SimpleNamespace(id="cus_persisted")
            StripeCustomerService.get_or_create_customer(self.user)

        wallet = CreditWallet.objects.get(user=self.user)
        self.assertEqual(wallet.stripe_customer_id, "cus_persisted")


class StripeLicenseCustomerIdempotencyTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Customer Idem School")
        self.admin = CustomUser.objects.create_user(
            email="cust.admin@school.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=True,
        )
        plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Pro",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=10_000,
            is_active=True,
        )
        now = timezone.now()
        self.license = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=plan,
            contract_months=1,
            max_seats=5,
            is_active=True,
            billing_method=LicenseBillingMethod.OFFLINE,
            billing_cycle_start=now,
            billing_cycle_end=now + timedelta(days=30),
        )

    def test_license_creation_sends_a_stable_idempotency_key(self):
        with patch("billing.stripe_service.stripe.Customer.create") as create:
            create.return_value = SimpleNamespace(id="cus_lic")
            StripeCustomerService.get_or_create_license_customer(
                self.license, self.admin
            )

        self.assertEqual(
            create.call_args.kwargs["idempotency_key"],
            f"customer-for-license-{self.license.id}",
        )

    def test_an_existing_license_customer_is_never_recreated(self):
        self.license.stripe_customer_id = "cus_lic_existing"
        self.license.save(update_fields=["stripe_customer_id"])

        with patch("billing.stripe_service.stripe.Customer.create") as create:
            result = StripeCustomerService.get_or_create_license_customer(
                self.license, self.admin
            )

        create.assert_not_called()
        self.assertEqual(result, "cus_lic_existing")
