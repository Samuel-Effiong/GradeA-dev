"""
H-275: live invoice transaction descriptions use plain wording instead of
Stripe's raw `billing_reason` (owner-approved table, 2026-11-02, American
spelling: "License").

Before: "Subscription invoice paid (subscription_cycle)".
After:  "Subscription renewal – payment received".

New rows only: stored descriptions are never rewritten.
"""

import importlib
from typing import Any
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from billing.models import BillingTransaction
from billing.stripe_service import StripeWebhookHandler
from billing.tests import test_invoice_payment_failed as failed_mod
from billing.tests import test_renewal_guards as guards_mod

# The owner-approved table, verbatim. Do not "improve" it here: change it
# only with a new owner approval.
APPROVED = {
    "subscription_create": (
        "Subscription started – payment received",
        "Subscription start – payment failed",
    ),
    "subscription_cycle": (
        "Subscription renewal – payment received",
        "Subscription renewal – payment failed",
    ),
    "subscription_update": (
        "Plan change – payment received",
        "Plan change – payment failed",
    ),
    "subscription_threshold": (
        "Usage charge – payment received",
        "Usage charge – payment failed",
    ),
    "manual": ("Invoice – payment received", "Invoice – payment failed"),
}
FALLBACK = ("Subscription payment received", "Subscription payment failed")


def license_form(text):
    return text.replace("Subscription", "License")


class WordingTableTests(SimpleTestCase):
    def _fn(self):
        # importlib so the red commit type-checks before the module exists
        return importlib.import_module("billing.invoice_wording").invoice_description

    def test_every_approved_reason_for_a_subscription(self):
        describe = self._fn()
        for reason, (paid, failed) in APPROVED.items():
            with self.subTest(reason=reason):
                self.assertEqual(
                    describe(billing_reason=reason, paid=True, license=False), paid
                )
                self.assertEqual(
                    describe(billing_reason=reason, paid=False, license=False),
                    failed,
                )

    def test_every_approved_reason_for_a_license(self):
        describe = self._fn()
        for reason, (paid, failed) in APPROVED.items():
            with self.subTest(reason=reason):
                self.assertEqual(
                    describe(billing_reason=reason, paid=True, license=True),
                    license_form(paid),
                )
                self.assertEqual(
                    describe(billing_reason=reason, paid=False, license=True),
                    license_form(failed),
                )

    def test_license_wording_is_american_spelling(self):
        describe = self._fn()
        text = describe(billing_reason="subscription_cycle", paid=True, license=True)
        self.assertEqual(text, "License renewal – payment received")
        self.assertNotIn("Licence", text)

    def test_unknown_missing_and_legacy_reasons_use_the_fallback(self):
        describe = self._fn()
        for reason in ("subscription", "something_new", "", None):
            with self.subTest(reason=reason):
                self.assertEqual(
                    describe(billing_reason=reason, paid=True, license=False),
                    FALLBACK[0],
                )
                self.assertEqual(
                    describe(billing_reason=reason, paid=False, license=True),
                    license_form(FALLBACK[1]),
                )

    def test_no_wording_ever_contains_the_raw_reason(self):
        describe = self._fn()
        for reason in [*APPROVED, "subscription", "something_new"]:
            for paid in (True, False):
                for lic in (True, False):
                    text = describe(billing_reason=reason, paid=paid, license=lic)
                    self.assertNotIn("_", text)
                    self.assertNotIn("(", text)


# --------------------------------------------------------------------------
# End to end: the four webhook sites write the new wording
# --------------------------------------------------------------------------


class IndividualPaidRowTests(TestCase):
    setUp = guards_mod.IndividualInvoiceRenewalGuardTests.setUp
    plan: Any  # set by the borrowed setUp
    sub: Any

    def _handle(self, invoice):
        with patch.object(guards_mod.real_stripe, "Subscription") as mock_sub:
            mock_sub.retrieve.return_value = guards_mod.stripe_sub_payload(
                self.plan.stripe_price_id
            )
            StripeWebhookHandler._handle_individual_invoice_succeeded(
                self.sub, invoice["billing_reason"], invoice
            )

    def test_renewal_row(self):
        self._handle(guards_mod.invoice_payload("subscription_cycle"))
        txn = BillingTransaction.objects.get(stripe_invoice_id="in_guard_1")
        self.assertEqual(txn.description, "Subscription renewal – payment received")

    def test_plan_change_row(self):
        self._handle(guards_mod.invoice_payload("subscription_update"))
        txn = BillingTransaction.objects.get(stripe_invoice_id="in_guard_1")
        self.assertEqual(txn.description, "Plan change – payment received")


class LicensePaidRowTests(TestCase):
    setUp = guards_mod.LicenseInvoiceRenewalGuardTests.setUp

    def test_renewal_row(self):
        with patch(
            "billing.stripe_service.LicenseSubscriptionService.process_license_renewal"
        ):
            StripeWebhookHandler.handle_invoice_payment_succeeded(
                guards_mod.invoice_payload("subscription_cycle")
            )
        txn = BillingTransaction.objects.get(stripe_invoice_id="in_guard_1")
        self.assertEqual(txn.description, "License renewal – payment received")


class IndividualFailedRowTests(TestCase):
    setUp = failed_mod.PaidSubscriptionPaymentFailedTests.setUp

    def test_renewal_row(self):
        StripeWebhookHandler.handle_invoice_payment_failed(
            failed_mod.invoice_payload("subscription_cycle")
        )
        txn = BillingTransaction.objects.get(stripe_invoice_id="in_payfail_1")
        self.assertEqual(txn.description, "Subscription renewal – payment failed")


class LicenseFailedRowTests(TestCase):
    setUp = failed_mod.LicenseInvoicePaymentFailedTests.setUp

    def test_plan_change_row(self):
        StripeWebhookHandler.handle_invoice_payment_failed(
            failed_mod.invoice_payload("subscription_update")
        )
        txn = BillingTransaction.objects.get(stripe_invoice_id="in_payfail_1")
        # The approved rule swaps "Subscription" for "License"; this phrase
        # has neither, so it reads the same on both tracks.
        self.assertEqual(txn.description, "Plan change – payment failed")

    def test_renewal_row(self):
        StripeWebhookHandler.handle_invoice_payment_failed(
            failed_mod.invoice_payload("subscription_cycle")
        )
        txn = BillingTransaction.objects.get(stripe_invoice_id="in_payfail_1")
        self.assertEqual(txn.description, "License renewal – payment failed")
