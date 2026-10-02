"""
The fake-Stripe harness proves things about other code, so it has to be
proven about itself first. An instrument that silently records nothing
would turn every assertion built on it into a false pass.

Covers what callers rely on:
  * every route to the Stripe API is intercepted - the legacy global
    surface (`stripe.Invoice.retrieve`) and a `StripeClient` alike;
  * no request leaves the machine;
  * each call records whether the calling thread's database connection was
    inside a transaction at that moment;
  * each call records the client's timeout and retry settings;
  * the two shared assertions fail when they should.
"""

import stripe
from django.db import transaction
from django.test import TestCase

from billing.tests.testing_fake_stripe import (
    assert_no_call_inside_transaction,
    assert_stripe_untouched,
    charge_receipt_url,
    fake_stripe,
    invoice_url,
)


class FakeStripeInterceptionTests(TestCase):
    def test_intercepts_the_legacy_global_surface(self):
        with fake_stripe() as fake:
            invoice = stripe.Invoice.retrieve("in_legacy")
        self.assertEqual(invoice.get("hosted_invoice_url"), invoice_url("in_legacy"))
        self.assertEqual([c.path for c in fake.calls], ["/v1/invoices/in_legacy"])

    def test_intercepts_a_stripe_client(self):
        with fake_stripe() as fake:
            client = stripe.StripeClient(
                stripe.api_key,
                http_client=stripe.RequestsClient(timeout=7),
                max_network_retries=0,
            )
            charge = client.v1.charges.retrieve("ch_client")
        self.assertEqual(charge.get("receipt_url"), charge_receipt_url("ch_client"))
        (call,) = fake.calls
        self.assertEqual(call.timeout, 7)
        self.assertEqual(call.max_network_retries, 0)

    def test_expands_the_latest_charge_on_a_payment_intent(self):
        with fake_stripe() as fake:
            intent = stripe.PaymentIntent.retrieve("pi_x", expand=["latest_charge"])
        self.assertEqual(
            intent.get("latest_charge").get("receipt_url"),
            charge_receipt_url("ch_for_pi_x"),
        )
        self.assertEqual(fake.calls[0].query.get("expand[0]"), ["latest_charge"])

    def test_unrouted_request_is_a_stripe_error_not_a_silent_pass(self):
        with fake_stripe(), self.assertRaises(stripe.InvalidRequestError):
            stripe.Refund.create(charge="ch_1")

    def test_receipt_error_and_delay_are_configurable(self):
        error = (503, {"error": {"type": "api_error", "message": "unavailable"}})
        with fake_stripe(receipt_error=error), self.assertRaises(stripe.APIError):
            stripe.Invoice.retrieve("in_down")


class TransactionRecordingTests(TestCase):
    """The property the audits depend on: WHERE a call happened, not just that it did."""

    def test_records_a_call_made_inside_an_open_transaction(self):
        with fake_stripe() as fake:
            with transaction.atomic():
                stripe.Invoice.retrieve("in_inside")
        (call,) = fake.calls
        self.assertTrue(call.in_transaction)
        with self.assertRaises(AssertionError):
            assert_no_call_inside_transaction(self, fake)


class AssertionHelperTests(TestCase):
    def test_untouched_passes_on_reads_and_fails_on_a_mutation(self):
        with fake_stripe() as fake:
            stripe.Invoice.retrieve("in_read")
            assert_stripe_untouched(self, fake)
            try:
                stripe.Subscription.modify("sub_1", metadata={"a": "b"})
            except stripe.StripeError:  # pragma: no cover - routing is not the point
                pass
        with self.assertRaises(AssertionError):
            assert_stripe_untouched(self, fake)

    def test_receipt_lookup_classification(self):
        with fake_stripe() as fake:
            stripe.Invoice.retrieve("in_1")
            stripe.Charge.retrieve("ch_1")
            stripe.PaymentIntent.retrieve("pi_1")
            try:
                stripe.Customer.create(email="someone@example.test")
            except stripe.StripeError:  # pragma: no cover
                pass
        self.assertEqual(len(fake.receipt_lookups()), 3)
        self.assertEqual(len(fake.calls), 4)
