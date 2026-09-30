"""
billing/tests/test_h28_plan_phases.py
=====================================
H-28 Change 1, commit 5: change_license_plan on the four-phase plumbing,
with F0 (a plan change never reached Stripe) and the payment-failure
branch F1-F3 (DESIGN_PROPOSAL.md §9d).

An upgrade is invoiced at once (proration "always_invoice"); a downgrade
moves no money (proration "none"):

  - an upgrade whose invoice is not paid (declined: F3; 3D Secure: F1) or
    whose card is refused (F2) is put back on the old price and ITS invoice
    voided -> FAILED, with the plan unchanged locally;
  - a downgrade whose local write fails is put back -> COMPENSATED;
  - a PAID upgrade whose local write fails is never refunded by code ->
    ESCALATED.

F1 is a customer-visible behaviour change: a 3D Secure upgrade used to be
left live at Stripe (and rolled back locally). Its 4-point record is in
docs/evidence/h28_p1b/PORT_H62.md.

The reproductions are in test_h28_licence_stripe_divergence (F0, F1-F3,
the latent F1-F3, and the two downgrade tests); this file tests the
machinery's states. Every Stripe response is a real StripeObject from the
stateful fake, never a mock (rule 14).
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
    PlanTier,
    PlanType,
)
from billing.stripe_service import StripeSubscriptionMutationService
from billing.tests.test_h28_cancel_phases import MUTATION_LOGGER, LicencePhaseTestCase
from billing.tests.test_h28_licence_stripe_divergence import CONTRACT_MONTHS, _make_plan


class PlanPhaseTests(LicencePhaseTestCase):
    SUB_ID = "sub_h28_plans"

    def setUp(self):
        super().setUp()
        self.old_plan = self.licence.plan
        self.cheaper_plan = _make_plan(
            PlanTier.STANDARD, PlanType.STANDARD, "500.00", "price_h28p_std"
        )
        self.dearer_plan = _make_plan(
            PlanTier.POWER, PlanType.POWER, "2000.00", "price_h28p_power"
        )
        self.old_stripe_price = self.stripe.price_id

    def change_plan(self, plan, **kwargs):
        return LicenseSubscriptionService.change_license_plan(
            self.licence, plan, performed_by=self.superadmin, **kwargs
        )

    def plan_charges(self):
        return BillingTransaction.objects.filter(
            license_subscription=self.licence,
            transaction_type=BillingTransactionType.LICENSE_PLAN_CHANGE_CHARGE,
        )

    def stripe_bills(self):
        """What Stripe bills per contract cycle, in cents."""
        return self.stripe.unit_amount()

    def price_creates(self):
        return [c for c in self.stripe.calls if c[0] == "Price.create"]

    def void_calls(self):
        return [c for c in self.stripe.calls if c[0] == "Invoice.void_invoice"]

    def assert_plan_unchanged_everywhere(self):
        licence = self.fresh_licence()
        self.assertEqual(licence.plan_id, self.old_plan.id)
        self.assertEqual(self.stripe.price_id, self.old_stripe_price)
        self.assertEqual(self.stripe.open_invoices(), [])
        self.assertFalse(self.plan_charges().exists())

    # -- F0 and the forward paths --------------------------------------------

    def test_F0_a_downgrade_reaches_stripe(self):
        updated = self.change_plan(self.cheaper_plan)

        self.assertEqual(updated.plan_id, self.cheaper_plan.id)
        self.assertEqual(
            self.stripe_bills(), int(self.cheaper_plan.price_cents) * CONTRACT_MONTHS
        )
        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.COMPLETE)
        self.assertEqual(intent.operation, LicenseStripeMutationOperation.CHANGE_PLAN)
        self.assertEqual(intent.requested_change["old_plan_id"], str(self.old_plan.id))
        self.assertEqual(
            intent.requested_change["new_plan_id"], str(self.cheaper_plan.id)
        )
        [(_, _, modify)] = self.modify_calls()
        self.assertEqual(modify["proration_behavior"], "none")
        self.assertEqual(modify["idempotency_key"], intent.idempotency_key("apply"))
        self.assert_no_stripe_call_inside_a_transaction()

    def test_a_contract_price_is_created_once_keyed_and_recorded(self):
        self.change_plan(self.cheaper_plan)

        intent = self.only_intent()
        [(_, _, create)] = self.price_creates()
        self.assertEqual(create["idempotency_key"], intent.idempotency_key("price"))
        self.assertEqual(
            create["unit_amount"], int(self.cheaper_plan.price_cents) * CONTRACT_MONTHS
        )
        self.assertEqual(create["recurring"]["interval_count"], CONTRACT_MONTHS)
        self.assertEqual(intent.stripe_result["created_price_id"], self.stripe.price_id)

    def test_a_paid_upgrade_completes_and_records_the_charge(self):
        updated = self.change_plan(self.dearer_plan)

        self.assertEqual(updated.plan_id, self.dearer_plan.id)
        self.assertEqual(
            self.stripe_bills(), int(self.dearer_plan.price_cents) * CONTRACT_MONTHS
        )
        self.assertEqual(
            self.only_intent().status, LicenseStripeMutationStatus.COMPLETE
        )
        [invoice_id] = self.stripe.invoices
        charge = self.plan_charges().get()
        self.assertEqual(charge.stripe_invoice_id, invoice_id)
        self.assertEqual(
            charge.amount_cents, self.stripe.invoices[invoice_id]["amount_paid"]
        )
        self.assertGreater(charge.amount_cents, 0)
        self.assert_no_stripe_call_inside_a_transaction()

    def test_a_custom_price_is_billed_at_stripe(self):
        updated = self.change_plan(self.old_plan, custom_price_cents=80_000)

        self.assertEqual(updated.custom_price_cents, 80_000)
        self.assertEqual(self.stripe_bills(), 80_000 * CONTRACT_MONTHS)
        self.assertEqual(
            self.only_intent().status, LicenseStripeMutationStatus.COMPLETE
        )

    # -- F1 / F2 / F3: the upgrade was not paid for --------------------------

    def _assert_reverted_and_voided(self, intent):
        self.assertEqual(intent.status, LicenseStripeMutationStatus.FAILED)
        self.assertIn("Payment not collected", intent.failure_reason)
        self.assert_plan_unchanged_everywhere()
        [(_, invoice_id, kwargs)] = self.void_calls()
        self.assertEqual(self.stripe.invoices[invoice_id]["status"], "void")
        self.assertEqual(kwargs["idempotency_key"], intent.idempotency_key("void"))
        revert = self.modify_calls()[-1][2]
        self.assertEqual(revert["items"][0]["price"], self.old_stripe_price)
        self.assertEqual(revert["proration_behavior"], "none")
        self.assertEqual(revert["idempotency_key"], intent.idempotency_key("revert"))
        self.assert_no_stripe_call_inside_a_transaction()

    def test_F1_an_upgrade_needing_3d_secure_is_reverted_and_voided(self):
        self.stripe.invoice_outcome = "open"
        self.stripe.pi_status = "requires_action"

        with self.assertRaisesRegex(ValueError, "3D Secure"):
            self.change_plan(self.dearer_plan)

        self._assert_reverted_and_voided(self.only_intent())

    def test_F2_a_card_error_is_reverted_and_voided(self):
        self.stripe.card_error_on_modify = True

        with self.assertRaisesRegex(ValueError, "Card declined"):
            self.change_plan(self.dearer_plan)

        self._assert_reverted_and_voided(self.only_intent())

    def test_F3_a_declined_upgrade_is_reverted_and_voided(self):
        self.stripe.invoice_outcome = "open"
        self.stripe.pi_status = "requires_payment_method"

        with self.assertRaisesRegex(ValueError, "Plan has not been changed"):
            self.change_plan(self.dearer_plan)

        self._assert_reverted_and_voided(self.only_intent())

    # -- after Stripe applied, before the local commit -----------------------

    def test_a_failed_local_write_after_a_downgrade_is_compensated(self):
        with patch.object(
            LicenseSubscription,
            "save",
            side_effect=OperationalError("server closed the connection"),
        ):
            with self.assertRaises(
                license_stripe_mutation.LicenceStripeChangeNotRecorded
            ):
                self.change_plan(self.cheaper_plan)

        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.COMPENSATED)
        self.assertEqual(self.stripe.price_id, self.old_stripe_price)
        self.assertEqual(self.fresh_licence().plan_id, self.old_plan.id)
        revert = self.modify_calls()[-1][2]
        self.assertEqual(revert["idempotency_key"], intent.idempotency_key("revert"))

    def test_a_failed_local_write_after_a_paid_upgrade_escalates(self):
        with patch.object(
            BillingTransactionService,
            "record",
            side_effect=OperationalError("server closed the connection"),
        ), self.assertLogs(MUTATION_LOGGER, level="ERROR") as logs:
            with self.assertRaises(
                license_stripe_mutation.LicenceStripeChangeNotRecorded
            ):
                self.change_plan(self.dearer_plan)

        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.ESCALATED)
        self.assertIn("MANUAL RECONCILIATION NEEDED", "\n".join(logs.output))
        # Paid for, so never reverted or voided by code.
        self.assertEqual(len(self.modify_calls()), 1)
        self.assertEqual(self.void_calls(), [])
        self.assertEqual(self.fresh_licence().plan_id, self.old_plan.id)

    def test_an_unreadable_upgrade_invoice_escalates_without_undoing(self):
        def unreadable(*args, **kwargs):
            raise stripe.error.APIConnectionError("Stripe unreachable")

        with patch.object(
            LicenceStripe, "retrieve_invoice", side_effect=unreadable
        ), self.assertLogs(MUTATION_LOGGER, level="ERROR"):
            with self.assertRaises(
                license_stripe_mutation.LicenceStripeChangeNotRecorded
            ):
                self.change_plan(self.dearer_plan)

        self.assertEqual(
            self.only_intent().status, LicenseStripeMutationStatus.ESCALATED
        )
        self.assertEqual(len(self.modify_calls()), 1)

    # -- before the change ---------------------------------------------------

    def test_a_failed_price_creation_changes_nothing_and_frees_the_licence(self):
        def refuse(*args, **kwargs):
            raise stripe.error.InvalidRequestError("No such product", "product")

        with patch.object(LicenceStripe, "create_price", side_effect=refuse):
            with self.assertRaisesRegex(ValueError, "Stripe price change failed"):
                self.change_plan(self.cheaper_plan)

        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.FAILED)
        self.assertIn("Not attempted", intent.failure_reason)
        self.assertEqual(self.modify_calls(), [])
        self.assertEqual(self.fresh_licence().plan_id, self.old_plan.id)

    def test_a_stripe_licence_without_a_subscription_is_refused(self):
        LicenseSubscription.objects.filter(pk=self.licence.pk).update(
            stripe_subscription_id=None
        )

        with self.assertRaisesRegex(ValueError, "no Stripe subscription ID"):
            self.change_plan(self.cheaper_plan)

        self.assertEqual(self.fresh_licence().plan_id, self.old_plan.id)
        self.assertEqual(LicenseStripeMutationIntent.objects.count(), 0)
        self.assertEqual(self.stripe.calls, [])

    # -- the paths that never reach Stripe -----------------------------------

    def test_an_offline_licence_changes_locally_without_an_intent(self):
        LicenseSubscription.objects.filter(pk=self.licence.pk).update(
            billing_method=LicenseBillingMethod.OFFLINE
        )

        updated = self.change_plan(self.dearer_plan)

        self.assertEqual(updated.plan_id, self.dearer_plan.id)
        self.assertEqual(LicenseStripeMutationIntent.objects.count(), 0)
        self.assertEqual(self.stripe.calls, [])
        self.assertTrue(
            BillingTransaction.objects.filter(
                license_subscription=self.licence,
                transaction_type=BillingTransactionType.LICENSE_OFFLINE_PLAN_CHANGE,
            ).exists()
        )

    def test_the_same_price_on_another_plan_needs_no_stripe_change(self):
        same_price_plan = _make_plan(
            PlanTier.PRO, PlanType.PRO_LICENSE, "1000.00", "price_h28p_same"
        )

        updated = self.change_plan(same_price_plan)

        self.assertEqual(updated.plan_id, same_price_plan.id)
        self.assertEqual(LicenseStripeMutationIntent.objects.count(), 0)
        self.assertEqual(self.stripe.calls, [])

    # -- guard, durability, and the old entry point --------------------------

    def test_a_request_while_another_change_is_in_flight_is_refused(self):
        LicenseStripeMutationIntent.objects.create(
            license_subscription=self.licence,
            operation=LicenseStripeMutationOperation.UPDATE_SEATS,
            stripe_subscription_id=self.SUB_ID,
            requested_change={"old_max_seats": 10, "new_max_seats": 12},
        )

        with self.assertRaises(license_stripe_mutation.LicenceBillingChangeInProgress):
            self.change_plan(self.dearer_plan)

        self.assertEqual(self.stripe.calls, [])
        self.assertEqual(self.fresh_licence().plan_id, self.old_plan.id)

    def test_refuses_to_run_inside_a_callers_transaction(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.change_plan(self.dearer_plan)

        self.assertEqual(self.stripe.calls, [])
        self.assertEqual(LicenseStripeMutationIntent.objects.count(), 0)

    def test_change_license_price_now_records_the_change_too(self):
        """It used to change Stripe only, leaving the local plan to a caller
        whose transaction might not commit."""
        StripeSubscriptionMutationService.change_license_price(
            self.licence, self.cheaper_plan, None, performed_by=self.superadmin
        )

        self.assertEqual(self.fresh_licence().plan_id, self.cheaper_plan.id)
        self.assertEqual(
            self.stripe_bills(), int(self.cheaper_plan.price_cents) * CONTRACT_MONTHS
        )
        self.assertEqual(
            self.only_intent().status, LicenseStripeMutationStatus.COMPLETE
        )
