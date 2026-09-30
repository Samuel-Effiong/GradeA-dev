"""
billing/tests/test_h60_licence_stripe_text.py
=============================================
H-60: a Stripe failure behind a licence change never shows the client
Stripe's own text (QA-ERR-03, "no raw library text").

Each licence route returned `str(ValueError)` as its 400, and the service
built that ValueError from Stripe's message: update_seats, change_plan
(through apply_licence_price_at_stripe), cancel and convert-to-offline.
Every site now raises fixed text. Stripe's message stays on the intent's
failure_reason for reconciliation, and the log line carries ids only.

Each test makes Stripe fail with a SENTINEL message at one site, then
asserts that the sentinel reaches neither the error the route would show
nor the log.
"""

from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from billing.imports import stripe
from billing.license_service import LicenseSubscriptionService
from billing.license_stripe_mutation import LicenceStripe
from billing.models import PlanTier, PlanType
from billing.tests.test_h28_cancel_phases import MUTATION_LOGGER, LicencePhaseTestCase
from billing.tests.test_h28_licence_stripe_divergence import _make_plan

SENTINEL = "SENTINEL_req_h60_sk_live_param_items[0]"


def refused(exc_type=stripe.error.InvalidRequestError):
    """A Stripe refusal (not an unknown outcome) carrying the sentinel."""
    if exc_type is stripe.error.InvalidRequestError:
        return exc_type(SENTINEL, "items")
    return exc_type(SENTINEL)


class LicenceStripeTextTests(LicencePhaseTestCase):
    SUB_ID = "sub_h60"

    def setUp(self):
        super().setUp()
        self.dearer_plan = _make_plan(
            PlanTier.POWER, PlanType.POWER, "2000.00", "price_h60_power"
        )

    def assert_no_stripe_text(self, run, *, fixed):
        """`run` raises a ValueError with fixed text; the sentinel is in
        neither it nor any log line, and the log names the intent."""
        with self.assertLogs(MUTATION_LOGGER, "WARNING") as logs:
            with self.assertRaises(ValueError) as raised:
                run()
        shown = str(raised.exception)
        self.assertNotIn(SENTINEL, shown)
        self.assertIn(fixed, shown)
        logged = "\n".join(logs.output)
        self.assertNotIn(SENTINEL, logged)
        self.assertIn(str(self.only_intent().id), logged)

    def card_error_with_sentinel(self):
        """The fake's card decline (which also leaves the change live, as
        Stripe does), re-raised with the sentinel as Stripe's message."""
        fake = self.stripe.subscription_modify
        self.stripe.card_error_on_modify = True

        def modify(*args, **kwargs):
            try:
                return fake(*args, **kwargs)
            except stripe.error.CardError as declined:
                raise stripe.error.CardError(
                    SENTINEL, None, "card_declined"
                ) from declined

        return patch.object(LicenceStripe, "modify_subscription", side_effect=modify)

    # -- update_seats ---------------------------------------------------------

    def update_seats(self):
        return LicenseSubscriptionService.update_seats(
            self.licence, self.SEATS + 5, performed_by=self.superadmin
        )

    def test_seats_unreadable_subscription(self):
        with patch.object(
            LicenceStripe, "retrieve_subscription", side_effect=refused()
        ):
            self.assert_no_stripe_text(
                self.update_seats, fixed="Stripe error while updating seats."
            )
        # Stripe's text is kept where reconciliation reads it.
        self.assertIn(SENTINEL, self.only_intent().failure_reason)

    def test_seats_card_declined(self):
        with self.card_error_with_sentinel():
            self.assert_no_stripe_text(
                self.update_seats, fixed="Seat increase payment failed."
            )

    def test_seats_refused_by_stripe(self):
        with patch.object(LicenceStripe, "modify_subscription", side_effect=refused()):
            self.assert_no_stripe_text(
                self.update_seats, fixed="Stripe error while updating seats."
            )
        self.assertIn(SENTINEL, self.only_intent().failure_reason)

    def test_the_seats_route_answers_with_the_fixed_text(self):
        """The view returns the ValueError's text as its 400, so this is
        what the client sees."""
        client = APIClient()
        client.force_authenticate(self.superadmin)
        url = reverse(
            "license-subscription-update-seats", kwargs={"pk": self.licence.pk}
        )
        with self.card_error_with_sentinel():
            response = client.post(url, {"max_seats": self.SEATS + 5}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn(SENTINEL, response.content.decode())
        self.assertIn("Seat increase payment failed.", response.data["detail"])

    # -- change_plan (apply_licence_price_at_stripe) --------------------------

    def change_plan(self):
        return LicenseSubscriptionService.change_license_plan(
            self.licence, self.dearer_plan, performed_by=self.superadmin
        )

    def test_plan_unreadable_subscription(self):
        with patch.object(
            LicenceStripe, "retrieve_subscription", side_effect=refused()
        ):
            self.assert_no_stripe_text(
                self.change_plan, fixed="Could not retrieve Stripe subscription."
            )

    def test_plan_price_creation_refused(self):
        with patch.object(LicenceStripe, "create_price", side_effect=refused()):
            self.assert_no_stripe_text(
                self.change_plan, fixed="Custom price creation failed."
            )

    def test_plan_card_declined(self):
        with self.card_error_with_sentinel():
            self.assert_no_stripe_text(self.change_plan, fixed="Card declined.")

    def test_plan_refused_by_stripe(self):
        with patch.object(LicenceStripe, "modify_subscription", side_effect=refused()):
            self.assert_no_stripe_text(
                self.change_plan, fixed="Stripe error while changing the plan."
            )

    # -- cancel and convert-to-offline ----------------------------------------

    def test_cancel_refused_by_stripe(self):
        with patch.object(LicenceStripe, "modify_subscription", side_effect=refused()):
            self.assert_no_stripe_text(
                lambda: LicenseSubscriptionService.cancel_license_subscription(
                    self.licence, performed_by=self.superadmin
                ),
                fixed="Failed to schedule Stripe cancellation.",
            )

    def test_convert_to_offline_refused_by_stripe(self):
        # Not InvalidRequestError: convert reads that one back as "maybe
        # already deleted", which is a different path.
        with patch.object(
            LicenceStripe,
            "delete_subscription",
            side_effect=refused(stripe.error.PermissionError),
        ):
            self.assert_no_stripe_text(
                lambda: LicenseSubscriptionService.convert_license_to_offline(
                    self.licence, performed_by=self.superadmin
                ),
                fixed="Failed to cancel Stripe subscription.",
            )
