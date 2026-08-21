"""
billing/tests/test_r5_atomicity_and_refund_idempotency.py
===========================================================
Closes two gaps the C3 webhook-idempotency work left open:

1. handle_payment_intent_failed and handle_setup_intent_succeeded were
   the only two entries in StripeWebhookHandler's dispatch table
   (_EVENT_HANDLERS in webhooks.py) not wrapped in @transaction.atomic,
   even though every sibling handler in the same table is. Locks in
   that both are now atomic, so any DB write added to either handler in
   the future automatically inherits all-or-nothing commit semantics
   instead of silently relying on someone remembering the decorator.

2. _void_or_refund_side_effect_invoice's stripe.Refund.create call had
   no idempotency_key, so double-refunding the same duplicate invoice
   (e.g. from a retried request, or a manual replay via
   replay_stripe_events) rested entirely on human judgment. Locks in
   that the key is deterministic per PaymentIntent, so Stripe itself
   refuses a second refund for the same PaymentIntent within its
   idempotency window.
"""

from unittest.mock import patch

from django.test import TestCase

from billing.stripe_service import (
    StripeSubscriptionMutationService,
    StripeWebhookHandler,
)
from billing.tests.test_subscription_cycle_integrity import FakeStripeObject


class HandlersAreAtomicTests(TestCase):
    """Every handler StripeWebhookHandler dispatches to must be wrapped
    in @transaction.atomic. Checking __wrapped__ (set by
    functools.wraps inside Django's Atomic.__call__) proves the
    decorator is actually applied, not just that the function happens
    to behave correctly under today's inputs."""

    def test_handle_payment_intent_failed_is_atomic(self):
        self.assertTrue(
            hasattr(StripeWebhookHandler.handle_payment_intent_failed, "__wrapped__"),
            "handle_payment_intent_failed must be decorated with "
            "@transaction.atomic, matching every other handler in "
            "StripeWebhookHandler's dispatch table.",
        )

    def test_handle_setup_intent_succeeded_is_atomic(self):
        self.assertTrue(
            hasattr(StripeWebhookHandler.handle_setup_intent_succeeded, "__wrapped__"),
            "handle_setup_intent_succeeded must be decorated with "
            "@transaction.atomic, matching every other handler in "
            "StripeWebhookHandler's dispatch table.",
        )

    @patch("stripe.Customer")
    def test_setup_intent_handler_still_works_wrapped_in_atomic(self, mock_customer):
        """Regression guard: adding @transaction.atomic must not change
        observable behavior for the existing set_as_default=true path."""
        setup_intent = FakeStripeObject(
            {
                "id": "seti_atomic_check",
                "metadata": {"set_as_default": "true"},
                "payment_method": "pm_new",
                "customer": "cus_atomic_check",
            }
        )

        StripeWebhookHandler.handle_setup_intent_succeeded(setup_intent)

        mock_customer.modify.assert_called_once_with(
            "cus_atomic_check",
            invoice_settings={"default_payment_method": "pm_new"},
        )

    def test_payment_intent_failed_handler_still_works_wrapped_in_atomic(self):
        """Regression guard: adding @transaction.atomic must not change
        observable behavior for the non-overage (ignored) path."""
        payment_intent = FakeStripeObject(
            {
                "id": "pi_unrelated",
                "metadata": {},
                "last_payment_error": None,
            }
        )

        # Should simply return without raising.
        StripeWebhookHandler.handle_payment_intent_failed(payment_intent)

    def test_payment_intent_failed_logs_overage_decline(self):
        payment_intent = FakeStripeObject(
            {
                "id": "pi_overage_declined",
                "metadata": {"flow": "overage_block_purchase", "user_id": "42"},
                "last_payment_error": {"message": "card_declined"},
            }
        )

        with self.assertLogs("billing.stripe_service", level="WARNING") as cm:
            StripeWebhookHandler.handle_payment_intent_failed(payment_intent)

        self.assertTrue(
            any("Overage block purchase failed" in msg for msg in cm.output)
        )


class RefundIdempotencyKeyTests(TestCase):
    """Covers StripeSubscriptionMutationService._void_or_refund_side_effect_invoice's
    use of an idempotency_key on stripe.Refund.create."""

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_refund_includes_deterministic_idempotency_key(
        self, mock_subscription, mock_invoice
    ):
        mock_subscription.retrieve.return_value = FakeStripeObject(
            {"latest_invoice": "in_dup_1"}
        )
        mock_invoice.retrieve.return_value = FakeStripeObject(
            {
                "id": "in_dup_1",
                "status": "paid",
                "payment_intent": FakeStripeObject({"id": "pi_dup_1"}),
            }
        )

        with patch("stripe.Refund") as mock_refund:
            StripeSubscriptionMutationService._void_or_refund_side_effect_invoice(
                "sub_dup_1"
            )

        mock_refund.create.assert_called_once_with(
            payment_intent="pi_dup_1",
            idempotency_key="interval-change-refund-pi_dup_1",
        )

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_idempotency_key_is_stable_across_repeated_calls(
        self, mock_subscription, mock_invoice
    ):
        """Same PaymentIntent -> same key on every call, which is what
        lets Stripe recognize a replay as a duplicate rather than a new
        refund request."""
        mock_subscription.retrieve.return_value = FakeStripeObject(
            {"latest_invoice": "in_dup_2"}
        )
        mock_invoice.retrieve.return_value = FakeStripeObject(
            {
                "id": "in_dup_2",
                "status": "paid",
                "payment_intent": FakeStripeObject({"id": "pi_dup_2"}),
            }
        )

        with patch("stripe.Refund") as mock_refund:
            StripeSubscriptionMutationService._void_or_refund_side_effect_invoice(
                "sub_dup_2"
            )
            StripeSubscriptionMutationService._void_or_refund_side_effect_invoice(
                "sub_dup_2"
            )

        first_call, second_call = mock_refund.create.call_args_list
        self.assertEqual(
            first_call.kwargs["idempotency_key"],
            second_call.kwargs["idempotency_key"],
        )

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_different_payment_intents_get_different_keys(
        self, mock_subscription, mock_invoice
    ):
        mock_subscription.retrieve.side_effect = [
            FakeStripeObject({"latest_invoice": "in_a"}),
            FakeStripeObject({"latest_invoice": "in_b"}),
        ]
        mock_invoice.retrieve.side_effect = [
            FakeStripeObject(
                {
                    "id": "in_a",
                    "status": "paid",
                    "payment_intent": FakeStripeObject({"id": "pi_a"}),
                }
            ),
            FakeStripeObject(
                {
                    "id": "in_b",
                    "status": "paid",
                    "payment_intent": FakeStripeObject({"id": "pi_b"}),
                }
            ),
        ]

        with patch("stripe.Refund") as mock_refund:
            StripeSubscriptionMutationService._void_or_refund_side_effect_invoice(
                "sub_a"
            )
            StripeSubscriptionMutationService._void_or_refund_side_effect_invoice(
                "sub_b"
            )

        first_call, second_call = mock_refund.create.call_args_list
        self.assertNotEqual(
            first_call.kwargs["idempotency_key"],
            second_call.kwargs["idempotency_key"],
        )

    @patch("stripe.Invoice")
    @patch("stripe.Subscription")
    def test_open_invoice_is_voided_not_refunded_no_idempotency_key_needed(
        self, mock_subscription, mock_invoice
    ):
        """Void path never charged the customer, so there's nothing to
        double-refund -- confirms the idempotency key only applies to
        the refund branch, not the void branch."""
        mock_subscription.retrieve.return_value = FakeStripeObject(
            {"latest_invoice": "in_open"}
        )
        mock_invoice.retrieve.return_value = FakeStripeObject(
            {"id": "in_open", "status": "open"}
        )

        with patch("stripe.Refund") as mock_refund:
            StripeSubscriptionMutationService._void_or_refund_side_effect_invoice(
                "sub_open"
            )
            mock_refund.create.assert_not_called()

        mock_invoice.void_invoice.assert_called_once_with("in_open")
