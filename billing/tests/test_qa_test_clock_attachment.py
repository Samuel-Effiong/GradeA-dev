"""
Tests for QA Stripe Test Clock attachment at customer-creation time.

WHY THIS EXISTS
----------------
Stripe only allows a Test Clock to be attached when a Customer is
CREATED — never afterwards. StripeCustomerService created every customer
without one, so customers made through the normal checkout flow were
permanently clockless and every time-travel clock advance for them exited
with "not attached to a Test Clock". The clock half of the tool was inert
for anything but customers hand-built in the Stripe dashboard.

Attachment is opt-in and narrow: ENABLE_BILLING_TIME_TRAVEL, a Stripe
test key, AND the customer's email domain listed in
settings.BILLING_TEST_CLOCK_EMAIL_DOMAINS (default empty). The default
path is asserted here to be byte-for-byte what it always was.

MOCKING CONVENTION: patch attributes ON the real `stripe` module so
`stripe.error.*` stays a real exception class (see
test_subscription_upgrade.py's docstring for why).
"""

import sys
from unittest.mock import MagicMock, patch

import stripe as real_stripe
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from billing.models import CreditWallet
from billing.qa_time_travel import (
    new_customer_test_clock_kwargs,
    should_attach_test_clock,
)
from billing.stripe_service import StripeCustomerService
from users.models import UserTypes

CustomUser = get_user_model()

QA_ON = {"ENABLE_BILLING_TIME_TRAVEL": True}


def _test_key():
    """Patches only stripe.api_key, leaving the rest of the module real."""
    return patch.object(real_stripe, "api_key", "sk_test_abc123")


def _live_key():
    return patch.object(real_stripe, "api_key", "sk_live_abc123")


class ShouldAttachTestClockPolicyTests(SimpleTestCase):
    """The opt-in policy. No Stripe calls happen at this layer."""

    @override_settings(**QA_ON, BILLING_TEST_CLOCK_EMAIL_DOMAINS=["yopmail.com"])
    def test_attaches_for_listed_domain(self):
        with _test_key():
            self.assertTrue(should_attach_test_clock("qa1@yopmail.com"))

    @override_settings(**QA_ON, BILLING_TEST_CLOCK_EMAIL_DOMAINS=["yopmail.com"])
    def test_does_not_attach_for_unlisted_domain(self):
        with _test_key():
            self.assertFalse(should_attach_test_clock("real.customer@school.edu"))

    @override_settings(**QA_ON, BILLING_TEST_CLOCK_EMAIL_DOMAINS=[])
    def test_empty_domain_list_is_the_safe_default(self):
        """No configuration => nobody gets a clock, even with QA mode on."""
        with _test_key():
            self.assertFalse(should_attach_test_clock("qa1@yopmail.com"))

    @override_settings(**QA_ON, BILLING_TEST_CLOCK_EMAIL_DOMAINS=["*"])
    def test_wildcard_covers_every_customer(self):
        with _test_key():
            self.assertTrue(should_attach_test_clock("anyone@anywhere.com"))

    @override_settings(
        ENABLE_BILLING_TIME_TRAVEL=False, BILLING_TEST_CLOCK_EMAIL_DOMAINS=["*"]
    )
    def test_never_attaches_when_time_travel_flag_is_off(self):
        with _test_key():
            self.assertFalse(should_attach_test_clock("qa1@yopmail.com"))

    @override_settings(**QA_ON, BILLING_TEST_CLOCK_EMAIL_DOMAINS=["*"])
    def test_never_attaches_under_a_live_stripe_key(self):
        """The independent backstop: live key can never reach a test clock."""
        with _live_key():
            self.assertFalse(should_attach_test_clock("qa1@yopmail.com"))

    @override_settings(**QA_ON, BILLING_TEST_CLOCK_EMAIL_DOMAINS=["@YopMail.com "])
    def test_domain_entries_are_normalized(self):
        """Leading '@', surrounding space and case must all be tolerated."""
        with _test_key():
            self.assertTrue(should_attach_test_clock("QA1@YOPMAIL.com"))

    @override_settings(**QA_ON, BILLING_TEST_CLOCK_EMAIL_DOMAINS="yopmail.com,qa.test")
    def test_setting_may_be_a_delimited_string(self):
        with _test_key():
            self.assertTrue(should_attach_test_clock("a@qa.test"))
            self.assertTrue(should_attach_test_clock("b@yopmail.com"))
            self.assertFalse(should_attach_test_clock("c@other.com"))

    @override_settings(**QA_ON, BILLING_TEST_CLOCK_EMAIL_DOMAINS=["yopmail.com"])
    def test_malformed_emails_are_rejected_not_crashed(self):
        with _test_key():
            for email in (None, "", "no-at-sign", "@", 12345):
                with self.subTest(email=email):
                    self.assertFalse(should_attach_test_clock(email))


@override_settings(**QA_ON, BILLING_TEST_CLOCK_EMAIL_DOMAINS=["yopmail.com"])
class NewCustomerTestClockKwargsTests(SimpleTestCase):
    """Clock creation itself."""

    def test_returns_clock_id_for_a_qa_customer(self):
        with _test_key(), patch.object(real_stripe, "test_helpers") as helpers:
            helpers.TestClock.create.return_value = {"id": "clock_new_1"}

            kwargs = new_customer_test_clock_kwargs("qa1@yopmail.com")

        self.assertEqual(kwargs, {"test_clock": "clock_new_1"})
        helpers.TestClock.create.assert_called_once()
        # frozen_time is required by Stripe and must be a real timestamp.
        self.assertGreater(helpers.TestClock.create.call_args.kwargs["frozen_time"], 0)

    def test_returns_empty_and_creates_nothing_for_a_normal_customer(self):
        with _test_key(), patch.object(real_stripe, "test_helpers") as helpers:
            kwargs = new_customer_test_clock_kwargs("real@school.edu")

        self.assertEqual(kwargs, {})
        helpers.TestClock.create.assert_not_called()

    def test_raises_rather_than_creating_a_permanently_clockless_customer(self):
        """
        A clock cannot be attached after creation, so degrading to {} here
        would hand QA a customer that can never be time-travelled. Fail
        loudly instead — this only fires in a QA-configured environment.
        """
        with _test_key(), patch.object(real_stripe, "test_helpers") as helpers:
            helpers.TestClock.create.side_effect = real_stripe.error.APIConnectionError(
                "network down"
            )

            with self.assertRaises(ValueError) as ctx:
                new_customer_test_clock_kwargs("qa1@yopmail.com")

        self.assertIn("Test Clock", str(ctx.exception))

    def test_raises_when_stripe_returns_a_clock_without_an_id(self):
        with _test_key(), patch.object(real_stripe, "test_helpers") as helpers:
            helpers.TestClock.create.return_value = {}

            with self.assertRaises(ValueError):
                new_customer_test_clock_kwargs("qa1@yopmail.com")

    def test_raises_when_library_lacks_test_helpers(self):
        stripe_without_helpers = MagicMock()
        stripe_without_helpers.api_key = "sk_test_abc123"
        del stripe_without_helpers.test_helpers

        with patch("billing.qa_time_travel.stripe", stripe_without_helpers):
            with self.assertRaises(ValueError) as ctx:
                new_customer_test_clock_kwargs("qa1@yopmail.com")

        self.assertIn("test_helpers", str(ctx.exception))


class CustomerCreationIntegrationTests(TestCase):
    """StripeCustomerService must be unchanged unless QA is configured."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="qa1@yopmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )

    # -- preserved behaviour -------------------------------------------
    #
    # These pin the settings explicitly rather than inheriting the local
    # .env. A developer who switches the feature on for their own QA
    # domain must not flip the meaning of a test whose whole job is to
    # assert the untouched PRODUCTION path.

    @override_settings(
        ENABLE_BILLING_TIME_TRAVEL=False, BILLING_TEST_CLOCK_EMAIL_DOMAINS=[]
    )
    def test_default_settings_create_customer_without_a_test_clock(self):
        """The production path: no new kwargs, no TestClock call."""
        with patch.object(real_stripe, "Customer") as mock_customer, patch.object(
            real_stripe, "test_helpers"
        ) as helpers:
            mock_customer.create.return_value = MagicMock(id="cus_plain")

            customer_id = StripeCustomerService.get_or_create_customer(self.user)

        self.assertEqual(customer_id, "cus_plain")
        helpers.TestClock.create.assert_not_called()
        self.assertNotIn("test_clock", mock_customer.create.call_args.kwargs)
        self.assertEqual(
            mock_customer.create.call_args.kwargs["metadata"],
            {"user_id": str(self.user.id)},
        )

    @override_settings(
        ENABLE_BILLING_TIME_TRAVEL=False, BILLING_TEST_CLOCK_EMAIL_DOMAINS=[]
    )
    def test_existing_customer_id_short_circuits_without_any_stripe_call(self):
        CreditWallet.objects.update_or_create(
            user=self.user, defaults={"stripe_customer_id": "cus_existing"}
        )

        with patch.object(real_stripe, "Customer") as mock_customer:
            customer_id = StripeCustomerService.get_or_create_customer(self.user)

        self.assertEqual(customer_id, "cus_existing")
        mock_customer.create.assert_not_called()

    # -- new behaviour --------------------------------------------------

    @override_settings(**QA_ON, BILLING_TEST_CLOCK_EMAIL_DOMAINS=["yopmail.com"])
    def test_qa_customer_is_created_attached_to_a_fresh_test_clock(self):
        with _test_key(), patch.object(
            real_stripe, "Customer"
        ) as mock_customer, patch.object(real_stripe, "test_helpers") as helpers:
            helpers.TestClock.create.return_value = {"id": "clock_qa_1"}
            mock_customer.create.return_value = MagicMock(id="cus_qa")

            customer_id = StripeCustomerService.get_or_create_customer(self.user)

        self.assertEqual(customer_id, "cus_qa")
        self.assertEqual(
            mock_customer.create.call_args.kwargs["test_clock"], "clock_qa_1"
        )
        # Wallet still wired up exactly as before.
        self.assertEqual(
            CreditWallet.objects.get(user=self.user).stripe_customer_id, "cus_qa"
        )

    @override_settings(**QA_ON, BILLING_TEST_CLOCK_EMAIL_DOMAINS=["yopmail.com"])
    def test_non_qa_user_in_a_qa_environment_stays_clockless(self):
        other = CustomUser.objects.create_user(
            email="real@school.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )

        with _test_key(), patch.object(
            real_stripe, "Customer"
        ) as mock_customer, patch.object(real_stripe, "test_helpers") as helpers:
            mock_customer.create.return_value = MagicMock(id="cus_real")

            StripeCustomerService.get_or_create_customer(other)

        helpers.TestClock.create.assert_not_called()
        self.assertNotIn("test_clock", mock_customer.create.call_args.kwargs)

    @override_settings(**QA_ON, BILLING_TEST_CLOCK_EMAIL_DOMAINS=["*"])
    def test_qa_hook_is_deletable_without_breaking_customer_creation(self):
        """
        billing/qa_time_travel.py advertises itself as deletable in one
        piece. Simulate its absence: customer creation must still work,
        just without a clock.
        """
        with _test_key(), patch.dict(
            sys.modules, {"billing.qa_time_travel": None}
        ), patch.object(real_stripe, "Customer") as mock_customer:
            mock_customer.create.return_value = MagicMock(id="cus_no_qa_module")

            customer_id = StripeCustomerService.get_or_create_customer(self.user)

        self.assertEqual(customer_id, "cus_no_qa_module")
        self.assertNotIn("test_clock", mock_customer.create.call_args.kwargs)
