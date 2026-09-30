"""
billing/tests/test_h28_convert_phases.py
========================================
H-28 Change 1, commit 6: convert_license_to_offline (P0) on the four-phase
plumbing.

The Stripe delete cannot be undone, so this flow has no compensation. What
it must get right is knowing whether the delete happened:

  - a lost response, or a refusal (a retried delete of an already-deleted
    subscription is refused), is READ BACK before it is classified;
  - if the local write fails after the delete, the intent is ESCALATED and
    a human told; code never re-creates a subscription.

The reproductions are in test_h28_licence_stripe_divergence (the two
convert tests); this file tests the machinery's states. Every Stripe
response is a real StripeObject from the stateful fake, never a mock
(rule 14).
"""

from unittest.mock import patch

from django.db import OperationalError, transaction

from billing import license_stripe_mutation
from billing.imports import stripe
from billing.license_service import LicenseSubscriptionService
from billing.models import (
    LicenseBillingMethod,
    LicenseBillingRecord,
    LicenseBillingRecordType,
    LicenseStripeMutationIntent,
    LicenseStripeMutationOperation,
    LicenseStripeMutationStatus,
    LicenseSubscription,
)
from billing.tests.test_h28_cancel_phases import MUTATION_LOGGER, LicencePhaseTestCase


class ConvertToOfflinePhaseTests(LicencePhaseTestCase):
    SUB_ID = "sub_h28_convert"

    def convert(self):
        return LicenseSubscriptionService.convert_license_to_offline(
            self.licence, performed_by=self.superadmin, notes="h28 phases"
        )

    def delete_calls(self):
        return [c for c in self.stripe.calls if c[0] == "Subscription.delete"]

    def conversion_records(self):
        return LicenseBillingRecord.objects.filter(
            license_subscription=self.licence,
            record_type=LicenseBillingRecordType.CONVERTED_TO_OFFLINE,
        ).count()

    def assert_still_on_stripe_everywhere(self):
        licence = self.fresh_licence()
        self.assertEqual(licence.billing_method, LicenseBillingMethod.STRIPE)
        self.assertEqual(licence.stripe_subscription_id, self.SUB_ID)
        self.assertEqual(self.stripe.status, "active")
        self.assertEqual(self.conversion_records(), 0)

    def _record_deletes(self):
        """Delete calls are not wrapped by the fixture; wrap them so the
        transaction check covers them too."""
        p = patch.object(
            stripe.Subscription,
            "delete",
            side_effect=self._recording(self.stripe.subscription_delete),
        )
        p.start()
        self.addCleanup(p.stop)

    def setUp(self):
        super().setUp()
        self._record_deletes()

    # -- the forward path ----------------------------------------------------

    def test_success_deletes_then_records_offline(self):
        updated = self.convert()

        self.assertEqual(updated.billing_method, LicenseBillingMethod.OFFLINE)
        self.assertIsNone(updated.stripe_subscription_id)
        self.assertEqual(self.stripe.status, "canceled")
        self.assertEqual(self.conversion_records(), 1)

        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.COMPLETE)
        self.assertEqual(
            intent.operation, LicenseStripeMutationOperation.CONVERT_TO_OFFLINE
        )
        self.assertEqual(
            intent.requested_change["deleted_stripe_subscription_id"], self.SUB_ID
        )
        [(_, sub_id, kwargs)] = self.delete_calls()
        self.assertEqual(sub_id, self.SUB_ID)
        self.assertEqual(kwargs["idempotency_key"], intent.idempotency_key("apply"))
        self.assert_no_stripe_call_inside_a_transaction()

    # -- whether the delete happened -----------------------------------------

    def test_a_lost_response_to_a_delete_that_landed_completes(self):
        self.stripe.lost_response_on = {"delete"}

        updated = self.convert()

        self.assertEqual(updated.billing_method, LicenseBillingMethod.OFFLINE)
        self.assertEqual(
            self.only_intent().status, LicenseStripeMutationStatus.COMPLETE
        )

    def test_a_refused_delete_of_a_subscription_already_gone_completes(self):
        """A retried delete whose first attempt landed is refused; the
        read-back shows it deleted, so it is recorded, not reported as a
        harmless failure."""

        def gone_already(*args, **kwargs):
            self.stripe.subscription_delete(*args, **kwargs)
            raise stripe.error.InvalidRequestError(
                "No such subscription", "id", http_status=404
            )

        with patch.object(stripe.Subscription, "delete", side_effect=gone_already):
            updated = self.convert()

        self.assertEqual(updated.billing_method, LicenseBillingMethod.OFFLINE)
        self.assertEqual(
            self.only_intent().status, LicenseStripeMutationStatus.COMPLETE
        )

    def test_a_real_refusal_changes_nothing_and_fails_the_intent(self):
        def refuse(*args, **kwargs):
            raise stripe.error.InvalidRequestError(
                "This subscription cannot be cancelled", "id"
            )

        with patch.object(stripe.Subscription, "delete", side_effect=refuse):
            with self.assertRaisesRegex(ValueError, "Failed to cancel Stripe"):
                self.convert()

        self.assert_still_on_stripe_everywhere()
        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.FAILED)
        self.assertIn("read-back shows not applied", intent.failure_reason)

    def test_an_unreadable_outcome_stays_pending_and_says_so(self):
        self.stripe.lost_response_on = {"delete"}

        def unreachable(*args, **kwargs):
            raise stripe.error.APIConnectionError("Stripe unreachable")

        with patch.object(
            stripe.Subscription, "retrieve", side_effect=unreachable
        ), self.assertLogs(MUTATION_LOGGER, level="ERROR") as logs:
            with self.assertRaises(ValueError):
                self.convert()

        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.PENDING)
        self.assertIn("MANUAL RECONCILIATION NEEDED", "\n".join(logs.output))
        self.assertEqual(
            self.fresh_licence().billing_method, LicenseBillingMethod.STRIPE
        )

    # -- after the delete, before the local commit ---------------------------

    def test_a_failed_local_write_escalates_and_never_recreates(self):
        with patch.object(
            LicenseSubscription,
            "save",
            side_effect=OperationalError("server closed the connection"),
        ), self.assertLogs(MUTATION_LOGGER, level="ERROR") as logs:
            with self.assertRaises(
                license_stripe_mutation.LicenceStripeChangeNotRecorded
            ):
                self.convert()

        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.ESCALATED)
        self.assertIsNotNone(intent.escalated_at)
        self.assertIn("MANUAL RECONCILIATION NEEDED", "\n".join(logs.output))
        # The subscription is gone, the application still says STRIPE, and
        # the escalated intent blocks any further change until a human acts.
        self.assertEqual(self.stripe.status, "canceled")
        self.assertEqual(
            self.fresh_licence().billing_method, LicenseBillingMethod.STRIPE
        )
        self.assertEqual(
            [c[0] for c in self.stripe.mutations()], ["Subscription.delete"]
        )
        with self.assertRaises(license_stripe_mutation.LicenceBillingChangeInProgress):
            self.convert()

    # -- refusals and the path that never reaches Stripe ---------------------

    def test_an_offline_licence_is_refused(self):
        LicenseSubscription.objects.filter(pk=self.licence.pk).update(
            billing_method=LicenseBillingMethod.OFFLINE
        )

        with self.assertRaisesRegex(ValueError, "already billed offline"):
            self.convert()

        self.assertEqual(self.stripe.calls, [])

    def test_a_licence_without_a_subscription_converts_locally(self):
        LicenseSubscription.objects.filter(pk=self.licence.pk).update(
            stripe_subscription_id=None
        )

        updated = self.convert()

        self.assertEqual(updated.billing_method, LicenseBillingMethod.OFFLINE)
        self.assertEqual(self.conversion_records(), 1)
        self.assertEqual(LicenseStripeMutationIntent.objects.count(), 0)
        self.assertEqual(self.stripe.calls, [])

    def test_a_request_while_another_change_is_in_flight_is_refused(self):
        LicenseStripeMutationIntent.objects.create(
            license_subscription=self.licence,
            operation=LicenseStripeMutationOperation.CANCEL,
            stripe_subscription_id=self.SUB_ID,
            requested_change={"auto_renew": [True, False]},
        )

        with self.assertRaises(license_stripe_mutation.LicenceBillingChangeInProgress):
            self.convert()

        self.assertEqual(self.stripe.calls, [])
        self.assert_still_on_stripe_everywhere()

    def test_refuses_to_run_inside_a_callers_transaction(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                self.convert()

        self.assertEqual(self.stripe.calls, [])
        self.assertEqual(LicenseStripeMutationIntent.objects.count(), 0)
