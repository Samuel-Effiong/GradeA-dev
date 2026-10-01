"""
billing/tests/test_h28_seat_phases.py
=====================================
H-28 Change 1, commit 4: update_seats on the four-phase plumbing, with the
payment-failure branch F4/F5 (DESIGN_PROPOSAL.md §9d).

A seat increase is invoiced at once (proration "always_invoice"); a
decrease moves no money (proration "none"). That difference decides what
may be undone automatically:

  - an increase whose invoice is not paid (declined, 3D Secure) or whose
    card is refused is put back at Stripe and ITS invoice voided -> FAILED;
  - a decrease whose local write fails is put back -> COMPENSATED;
  - a PAID increase whose local write fails is never refunded by code ->
    ESCALATED, and a human rolls it forward.

The reproductions for this flow are in test_h28_licence_stripe_divergence
(the four seat tests and F4/F5); this file tests the machinery's states.
Every Stripe response is a real StripeObject from the stateful fake, never
a mock (rule 14).
"""

from unittest.mock import patch

from django.db import OperationalError, transaction

from billing import license_stripe_mutation
from billing.billing_transaction_service import BillingTransactionService
from billing.imports import stripe
from billing.license_service import LicenseSubscriptionService
from billing.license_stripe_mutation import LicenceStripe
from billing.models import (
    BillingTransaction,
    BillingTransactionType,
    LicenseBillingMethod,
    LicenseStripeMutationIntent,
    LicenseStripeMutationOperation,
    LicenseStripeMutationStatus,
    LicenseSubscription,
)
from billing.tests.test_h28_cancel_phases import MUTATION_LOGGER, LicencePhaseTestCase


class SeatPhaseTests(LicencePhaseTestCase):
    SUB_ID = "sub_h28_seats"

    def update_seats(self, new_max_seats):
        return LicenseSubscriptionService.update_seats(
            self.licence, new_max_seats, performed_by=self.superadmin
        )

    def seat_charges(self):
        return BillingTransaction.objects.filter(
            license_subscription=self.licence,
            transaction_type=BillingTransactionType.LICENSE_SEAT_CHANGE_CHARGE,
        )

    def assert_seats_unchanged_everywhere(self):
        self.assertEqual(self.fresh_licence().max_seats, self.SEATS)
        self.assertEqual(self.stripe.quantity, self.SEATS)
        self.assertEqual(self.stripe.open_invoices(), [])
        self.assertFalse(self.seat_charges().exists())

    def void_calls(self):
        return [c for c in self.stripe.calls if c[0] == "Invoice.void_invoice"]

    def _fail_recording_the_charge(self):
        return patch.object(
            BillingTransactionService,
            "record",
            side_effect=OperationalError("server closed the connection"),
        )

    # -- the forward paths ---------------------------------------------------

    def test_a_decrease_completes_outside_any_transaction(self):
        updated = self.update_seats(self.SEATS - 3)

        self.assertEqual(updated.max_seats, self.SEATS - 3)
        self.assertEqual(self.stripe.quantity, self.SEATS - 3)
        self.assertEqual(self.stripe.invoices, {})
        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.COMPLETE)
        self.assertEqual(intent.operation, LicenseStripeMutationOperation.UPDATE_SEATS)
        self.assertEqual(
            intent.requested_change,
            {"old_max_seats": self.SEATS, "new_max_seats": self.SEATS - 3},
        )
        [(_, _, kwargs)] = self.modify_calls()
        self.assertEqual(kwargs["proration_behavior"], "none")
        self.assertEqual(kwargs["idempotency_key"], intent.idempotency_key("apply"))
        self.assert_no_stripe_call_inside_a_transaction()

    def test_a_paid_increase_completes_and_records_the_charge(self):
        updated = self.update_seats(self.SEATS + 5)

        self.assertEqual(updated.max_seats, self.SEATS + 5)
        self.assertEqual(self.stripe.quantity, self.SEATS + 5)
        self.assertEqual(
            self.only_intent().status, LicenseStripeMutationStatus.COMPLETE
        )
        [invoice_id] = self.stripe.invoices
        charge = self.seat_charges().get()
        self.assertEqual(charge.stripe_invoice_id, invoice_id)
        self.assertEqual(
            charge.amount_cents, self.stripe.invoices[invoice_id]["amount_paid"]
        )
        self.assertGreater(charge.amount_cents, 0)
        self.assertEqual(charge.stripe_subscription_id, self.SUB_ID)
        self.assert_no_stripe_call_inside_a_transaction()

    # -- F4 / F5: the change was not paid for --------------------------------

    def _assert_reverted_and_voided(self, intent):
        self.assertEqual(intent.status, LicenseStripeMutationStatus.FAILED)
        self.assertIn("Payment not collected", intent.failure_reason)
        self.assert_seats_unchanged_everywhere()
        [(_, invoice_id, kwargs)] = self.void_calls()
        self.assertEqual(self.stripe.invoices[invoice_id]["status"], "void")
        self.assertEqual(kwargs["idempotency_key"], intent.idempotency_key("void"))
        revert = self.modify_calls()[-1][2]
        self.assertEqual(revert["items"][0]["quantity"], self.SEATS)
        self.assertEqual(revert["proration_behavior"], "none")
        self.assertEqual(revert["idempotency_key"], intent.idempotency_key("revert"))
        self.assert_no_stripe_call_inside_a_transaction()

    def test_F4_a_declined_increase_is_reverted_and_its_invoice_voided(self):
        self.stripe.invoice_outcome = "open"
        self.stripe.pi_status = "requires_payment_method"

        with self.assertRaisesRegex(ValueError, "Seats have not been increased"):
            self.update_seats(self.SEATS + 5)

        self._assert_reverted_and_voided(self.only_intent())

    def test_F4_an_increase_needing_3d_secure_is_reverted_and_its_invoice_voided(
        self,
    ):
        self.stripe.invoice_outcome = "open"
        self.stripe.pi_status = "requires_action"

        with self.assertRaisesRegex(ValueError, "Seats have not been increased"):
            self.update_seats(self.SEATS + 5)

        self._assert_reverted_and_voided(self.only_intent())

    def test_F5_a_card_error_is_not_taken_as_a_refusal(self):
        """Stripe applies the quantity in the same call that tries the card,
        so a CardError may leave the change live: it is reverted, not
        merely reported."""
        self.stripe.card_error_on_modify = True

        with self.assertRaisesRegex(ValueError, "card error"):
            self.update_seats(self.SEATS + 5)

        self._assert_reverted_and_voided(self.only_intent())

    def test_the_licence_stays_guarded_while_a_card_error_is_undone(self):
        """A CardError is not a refusal: the intent stays in flight (so the
        per-licence guard holds) until the undo has run, and only then
        becomes FAILED. Taken as a refusal, it would be FAILED at once and
        the licence open to a second change while Stripe is still being put
        back."""
        self.stripe.card_error_on_modify = True
        seen = []

        def revert_while_checking(*args, **kwargs):
            if kwargs["items"][0]["quantity"] == self.SEATS:
                seen.append(LicenseStripeMutationIntent.objects.get().status)
            return self.stripe.subscription_modify(*args, **kwargs)

        with patch.object(
            LicenceStripe, "modify_subscription", side_effect=revert_while_checking
        ):
            with self.assertRaisesRegex(ValueError, "card error"):
                self.update_seats(self.SEATS + 5)

        self.assertEqual(seen, [LicenseStripeMutationStatus.PENDING])
        self.assertEqual(self.only_intent().status, LicenseStripeMutationStatus.FAILED)

    def test_with_no_new_invoice_an_older_open_one_is_left_alone(self):
        """A declined change that raised no invoice of its own: the older
        open invoice is the subscription's latest, and must not be taken
        for the change's."""
        self.stripe.invoices["in_h28_renewal"] = {
            "status": "open",
            "amount_paid": 0,
            "pi_status": "requires_payment_method",
        }
        self.stripe.latest_invoice = "in_h28_renewal"

        def decline_before_invoicing(*args, **kwargs):
            if kwargs["items"][0]["quantity"] == self.SEATS + 5:
                raise stripe.error.CardError(
                    "Your card was declined.", None, "card_declined"
                )
            return self.stripe.subscription_modify(*args, **kwargs)

        with patch.object(
            LicenceStripe, "modify_subscription", side_effect=decline_before_invoicing
        ):
            with self.assertRaisesRegex(ValueError, "card error"):
                self.update_seats(self.SEATS + 5)

        self.assertEqual(self.stripe.invoices["in_h28_renewal"]["status"], "open")
        self.assertEqual(self.void_calls(), [])

    def test_an_older_open_invoice_is_never_voided(self):
        """Only the change's own invoice may be voided. An unpaid renewal
        already open on the subscription is the school's real debt."""
        self.stripe.invoices["in_h28_renewal"] = {
            "status": "open",
            "amount_paid": 0,
            "pi_status": "requires_payment_method",
        }
        self.stripe.latest_invoice = "in_h28_renewal"
        self.stripe.invoice_outcome = "open"

        with self.assertRaisesRegex(ValueError, "Seats have not been increased"):
            self.update_seats(self.SEATS + 5)

        self.assertEqual(self.stripe.invoices["in_h28_renewal"]["status"], "open")
        [(_, voided, _)] = self.void_calls()
        self.assertNotEqual(voided, "in_h28_renewal")

    def test_a_failed_undo_escalates_to_a_human(self):
        self.stripe.invoice_outcome = "open"

        def refuse_the_revert(*args, **kwargs):
            if kwargs["items"][0]["quantity"] == self.SEATS:
                raise stripe.error.APIConnectionError("Request timed out")
            return self.stripe.subscription_modify(*args, **kwargs)

        with patch.object(
            LicenceStripe, "modify_subscription", side_effect=refuse_the_revert
        ), self.assertLogs(MUTATION_LOGGER, level="ERROR") as logs:
            with self.assertRaises(
                license_stripe_mutation.LicenceStripeChangeNotRecorded
            ):
                self.update_seats(self.SEATS + 5)

        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.ESCALATED)
        self.assertIn("the revert failed", intent.failure_reason)
        self.assertIn("MANUAL RECONCILIATION NEEDED", "\n".join(logs.output))
        # The invoice is still voided, so nothing is collected for a change
        # the application does not show.
        self.assertEqual(self.stripe.open_invoices(), [])
        self.assertEqual(self.fresh_licence().max_seats, self.SEATS)

    def test_an_unreadable_invoice_after_an_increase_escalates_without_undoing(
        self,
    ):
        """Applied at Stripe, and whether it was paid is unknown: money may
        have moved, so nothing is reverted automatically."""

        def unreadable(*args, **kwargs):
            raise stripe.error.APIConnectionError("Stripe unreachable")

        with patch.object(
            LicenceStripe, "retrieve_invoice", side_effect=unreadable
        ), self.assertLogs(MUTATION_LOGGER, level="ERROR"):
            with self.assertRaises(
                license_stripe_mutation.LicenceStripeChangeNotRecorded
            ):
                self.update_seats(self.SEATS + 5)

        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.ESCALATED)
        self.assertEqual(self.stripe.quantity, self.SEATS + 5)
        self.assertEqual(len(self.modify_calls()), 1, "nothing may be reverted")
        self.assertEqual(self.void_calls(), [])

    # -- after Stripe applied, before the local commit -----------------------

    def test_a_failed_local_write_after_a_decrease_is_compensated(self):
        with patch.object(
            LicenseSubscription,
            "save",
            side_effect=OperationalError("server closed the connection"),
        ):
            with self.assertRaises(
                license_stripe_mutation.LicenceStripeChangeNotRecorded
            ):
                self.update_seats(self.SEATS - 3)

        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.COMPENSATED)
        self.assertEqual(self.stripe.quantity, self.SEATS)
        self.assertEqual(self.fresh_licence().max_seats, self.SEATS)
        revert = self.modify_calls()[-1][2]
        self.assertEqual(revert["idempotency_key"], intent.idempotency_key("revert"))

    def test_a_failed_local_write_after_a_paid_increase_escalates(self):
        with self._fail_recording_the_charge(), self.assertLogs(
            MUTATION_LOGGER, level="ERROR"
        ) as logs:
            with self.assertRaises(
                license_stripe_mutation.LicenceStripeChangeNotRecorded
            ):
                self.update_seats(self.SEATS + 5)

        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.ESCALATED)
        self.assertIn("MANUAL RECONCILIATION NEEDED", "\n".join(logs.output))
        # Paid for, so never reverted or voided by code: a human rolls it
        # forward.
        self.assertEqual(self.stripe.quantity, self.SEATS + 5)
        self.assertEqual(len(self.modify_calls()), 1)
        self.assertEqual(self.void_calls(), [])
        self.assertEqual(self.fresh_licence().max_seats, self.SEATS)

    def test_a_licence_changed_meanwhile_is_not_overwritten(self):
        def apply_then_the_licence_moves_on(*args, **kwargs):
            result = self.stripe.subscription_modify(*args, **kwargs)
            if kwargs["items"][0]["quantity"] == self.SEATS - 3:
                LicenseSubscription.objects.filter(pk=self.licence.pk).update(
                    max_seats=self.SEATS + 1
                )
            return result

        with patch.object(
            LicenceStripe,
            "modify_subscription",
            side_effect=apply_then_the_licence_moves_on,
        ):
            with self.assertRaises(
                license_stripe_mutation.LicenceStripeChangeNotRecorded
            ):
                self.update_seats(self.SEATS - 3)

        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.COMPENSATED)
        self.assertIn("LicenceMovedOn", intent.failure_reason)
        self.assertEqual(self.fresh_licence().max_seats, self.SEATS + 1)

    # -- before the change: the reads it needs -------------------------------

    def test_an_unreadable_subscription_frees_the_licence(self):
        def unreachable(*args, **kwargs):
            raise stripe.error.APIConnectionError("Stripe unreachable")

        with patch.object(
            LicenceStripe, "retrieve_subscription", side_effect=unreachable
        ):
            with self.assertRaisesRegex(ValueError, "Stripe error while updating"):
                self.update_seats(self.SEATS + 5)

        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.FAILED)
        self.assertIn("Not attempted", intent.failure_reason)
        self.assertEqual(self.modify_calls(), [])
        # FAILED is terminal, so the licence is free for the next attempt.
        self.assertEqual(self.update_seats(self.SEATS + 5).max_seats, self.SEATS + 5)

    def test_a_lost_response_on_a_decrease_is_read_back(self):
        self.stripe.lost_response_on = {"modify"}

        updated = self.update_seats(self.SEATS - 3)

        self.assertEqual(updated.max_seats, self.SEATS - 3)
        self.assertEqual(
            self.only_intent().status, LicenseStripeMutationStatus.COMPLETE
        )

    # -- guard, durability, and the path that never reaches Stripe -----------

    def test_a_request_while_another_change_is_in_flight_is_refused(self):
        LicenseStripeMutationIntent.objects.create(
            license_subscription=self.licence,
            operation=LicenseStripeMutationOperation.CANCEL,
            stripe_subscription_id=self.SUB_ID,
            requested_change={"auto_renew": [True, False]},
        )

        with self.assertRaises(license_stripe_mutation.LicenceBillingChangeInProgress):
            self.update_seats(self.SEATS + 5)

        self.assertEqual(self.stripe.calls, [])
        self.assertEqual(self.fresh_licence().max_seats, self.SEATS)

    def test_refuses_to_run_inside_a_callers_transaction(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.update_seats(self.SEATS + 5)

        self.assertEqual(self.stripe.calls, [])
        self.assertEqual(LicenseStripeMutationIntent.objects.count(), 0)

    def test_a_licence_without_a_subscription_changes_locally(self):
        LicenseSubscription.objects.filter(pk=self.licence.pk).update(
            stripe_subscription_id=None,
            billing_method=LicenseBillingMethod.OFFLINE,
        )

        updated = self.update_seats(self.SEATS + 5)

        self.assertEqual(updated.max_seats, self.SEATS + 5)
        self.assertEqual(LicenseStripeMutationIntent.objects.count(), 0)
        self.assertEqual(self.stripe.calls, [])
