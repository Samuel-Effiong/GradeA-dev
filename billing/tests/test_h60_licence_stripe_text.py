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

from django.db import OperationalError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from billing.imports import stripe
from billing.license_service import LicenseSubscriptionService
from billing.license_stripe_mutation import LicenceStripe
from billing.models import LicenseStripeMutationStatus, PlanTier, PlanType
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


class NotRecordedRouteTests(LicencePhaseTestCase):
    """v2's finding (SM ruling): when Stripe applied a licence change that the
    application then couldn't record (H-28's LicenceStripeChangeNotRecorded),
    the route answered a 500, and its log line carried the chained Stripe
    text. The route now answers by outcome:

    - ESCALATED (live at Stripe, flagged for a human): 409, no Retry-After.
    - COMPENSATED (undone at Stripe, nothing changed): 503 + Retry-After.

    The failure is the real one: phase D's local write fails, and each route's
    own compensation decides the outcome."""

    SUB_ID = "sub_h60_nr"

    def setUp(self):
        super().setUp()
        self.cheaper_plan = _make_plan(
            PlanTier.STANDARD, PlanType.STANDARD, "500.00", "price_h60_std"
        )
        self.dearer_plan = _make_plan(
            PlanTier.POWER, PlanType.POWER, "2000.00", "price_h60_nr_power"
        )
        self.client = APIClient()
        self.client.force_authenticate(self.superadmin)

    def url(self, name):
        return reverse(f"license-subscription-{name}", kwargs={"pk": self.licence.pk})

    def post_with_local_write_failing(self, name, payload=None):
        with patch(
            "billing.license_stripe_mutation._finalise_with_retry",
            side_effect=OperationalError("server closed the connection"),
        ), self.assertNoLogs("billing.license_views", "ERROR"):
            with self.assertLogs(MUTATION_LOGGER, "WARNING") as logs:
                response = self.client.post(
                    self.url(name), payload or {}, format="json"
                )
        # ids only on the new line; H-28's own reconciliation lines are not
        # client-facing and carry the database error, never Stripe's text.
        self.assertTrue(
            any(str(self.only_intent().id) in line for line in logs.output), logs.output
        )
        return response

    def assert_escalated(self, response):
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.data)
        self.assertIn("flagged for manual reconciliation", response.data["detail"])
        self.assertNotIn("Retry-After", response)
        self.assertNotIn("server closed", response.content.decode())
        self.assertEqual(
            self.only_intent().status, LicenseStripeMutationStatus.ESCALATED
        )

    def assert_compensated(self, response):
        self.assertEqual(
            response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE, response.data
        )
        self.assertIn("Nothing was changed", response.data["detail"])
        self.assertEqual(response["Retry-After"], "30")
        self.assertNotIn("server closed", response.content.decode())
        self.assertEqual(
            self.only_intent().status, LicenseStripeMutationStatus.COMPENSATED
        )

    # -- update_seats ---------------------------------------------------------

    def test_seats_a_paid_increase_not_recorded_is_409(self):
        self.assert_escalated(
            self.post_with_local_write_failing(
                "update-seats", {"max_seats": self.SEATS + 5}
            )
        )

    def test_seats_a_decrease_not_recorded_is_undone_and_503(self):
        self.assert_compensated(
            self.post_with_local_write_failing(
                "update-seats", {"max_seats": self.SEATS - 3}
            )
        )
        self.assertEqual(self.stripe.quantity, self.SEATS)

    # -- change_plan ----------------------------------------------------------

    def test_plan_a_paid_upgrade_not_recorded_is_409(self):
        self.assert_escalated(
            self.post_with_local_write_failing(
                "change-plan", {"plan": str(self.dearer_plan.pk)}
            )
        )

    def test_plan_a_downgrade_not_recorded_is_undone_and_503(self):
        self.assert_compensated(
            self.post_with_local_write_failing(
                "change-plan", {"plan": str(self.cheaper_plan.pk)}
            )
        )

    # -- cancel ---------------------------------------------------------------

    def test_cancel_not_recorded_is_undone_and_503(self):
        self.assert_compensated(self.post_with_local_write_failing("cancel"))
        self.assertFalse(self.stripe.cancel_at_period_end)

    def test_cancel_not_recorded_and_not_undone_is_409(self):
        fake = self.stripe.subscription_modify

        def revert_refused(*args, **kwargs):
            if kwargs.get("cancel_at_period_end") is False:
                raise stripe.error.APIConnectionError("revert unreachable")
            return fake(*args, **kwargs)

        with patch.object(
            LicenceStripe, "modify_subscription", side_effect=revert_refused
        ):
            self.assert_escalated(self.post_with_local_write_failing("cancel"))

    # -- convert-to-offline ---------------------------------------------------

    def test_convert_to_offline_not_recorded_is_409(self):
        """A deleted subscription can't be restored, so convert never
        compensates: its only not-recorded outcome is the 409."""
        self.assert_escalated(self.post_with_local_write_failing("convert-to-offline"))
