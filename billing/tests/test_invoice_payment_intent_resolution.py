"""
billing/tests/test_invoice_payment_intent_resolution.py
=======================================================
THE DOUBLE-CHARGE BLOCKER.

WHAT WENT WRONG
---------------
`invoice.payment_intent` was removed from the Stripe Invoice object in API
version 2025-03-31, as part of the same restructure that moved
`invoice.subscription` under `invoice.parent`. Stripe does NOT error on
`expand=["payment_intent"]` for the dead field — it silently ignores the
expand and the attribute simply is not there.

Four production sites read it. The financial one is
`_void_or_refund_side_effect_invoice`: on a monthly -> annual upgrade Stripe
raises a side-effect invoice duplicating money the customer already paid
through Checkout, and this compensating control is supposed to refund it.
`pi_id` came back None, so `if pi_id:` never fired. **No refund, no
exception, no log line — the customer stayed double-charged.**

Confirmed against the live test API on 2026-09-06 under the pinned version
2026-02-25.clover: top-level `payment_intent`, `charge` and `subscription`
are all ABSENT on a real paid invoice, and the PaymentIntent is reachable
only at `payments.data[].payment.payment_intent`.

WHY MOCKS COULD NOT SEE IT
--------------------------
Every mocked test builds its own invoice dict. If the author writes
`{"payment_intent": "pi_x"}`, the code passes — because the test asserts the
shape the author believed in, which is exactly the belief that went stale.
The fixtures below are therefore built from SHAPES OBSERVED ON THE LIVE API,
and `test_a_modern_invoice_has_no_top_level_payment_intent` pins that the
legacy field is genuinely absent so nobody "helpfully" adds it back.

The decisive regression guard is
`test_the_refund_is_issued_for_a_modern_invoice`: revert the fix and it
fails, because the refund is never attempted.
"""

from unittest.mock import patch

import stripe as real_stripe
from django.test import TestCase

from billing.stripe_service import (
    INVOICE_PAYMENT_INTENT_EXPAND,
    StripeSubscriptionMutationService,
    resolve_invoice_payment_intent,
)

PI_ID = "pi_3UBZl4Aq8voEV6Ea0vsU3bN7"


def modern_invoice(*, status="paid", pi_id=PI_ID, pi_status="succeeded", expanded=True):
    """
    The shape a real invoice has under API 2026-02-25.clover.

    Note what is NOT here: no top-level `payment_intent`, `charge` or
    `subscription`. That absence is the bug's whole cause and is asserted
    directly below.
    """
    payment_intent = (
        {"id": pi_id, "object": "payment_intent", "status": pi_status}
        if expanded
        else pi_id
    )
    return {
        "id": "in_modern_1",
        "status": status,
        "payments": {
            "object": "list",
            "data": [
                {
                    "id": "inpay_1",
                    "object": "invoice_payment",
                    "status": "paid",
                    "payment": {
                        "type": "payment_intent",
                        "payment_intent": payment_intent,
                    },
                }
            ],
        },
    }


def legacy_invoice(*, status="paid", pi_id=PI_ID, pi_status="succeeded"):
    """Pre-2025-03-31 shape, still supported by the fallback."""
    return {
        "id": "in_legacy_1",
        "status": status,
        "payment_intent": {"id": pi_id, "status": pi_status},
    }


class ResolveInvoicePaymentIntentTests(TestCase):
    def test_a_modern_invoice_has_no_top_level_payment_intent(self):
        """
        Pins the premise. If someone reintroduces the legacy key into these
        fixtures the other tests stop proving anything, so state it outright.
        """
        self.assertNotIn("payment_intent", modern_invoice())
        self.assertNotIn("charge", modern_invoice())
        self.assertNotIn("subscription", modern_invoice())

    def test_it_resolves_the_id_from_the_modern_location(self):
        pi_id, obj = resolve_invoice_payment_intent(modern_invoice())
        self.assertEqual(pi_id, PI_ID)
        self.assertEqual(obj["status"], "succeeded")

    def test_it_resolves_a_bare_id_string_when_not_fully_expanded(self):
        """`expand=["payments"]` yields an id, not an object — still usable
        for a refund, just without a status."""
        pi_id, obj = resolve_invoice_payment_intent(modern_invoice(expanded=False))
        self.assertEqual(pi_id, PI_ID)
        self.assertIsNone(obj)

    def test_it_still_reads_the_legacy_location(self):
        """An older pinned API version must keep working."""
        pi_id, obj = resolve_invoice_payment_intent(legacy_invoice())
        self.assertEqual(pi_id, PI_ID)
        self.assertEqual(obj["status"], "succeeded")

    def test_an_invoice_with_no_payment_resolves_to_nothing(self):
        self.assertEqual(
            resolve_invoice_payment_intent({"id": "in_x", "status": "draft"}),
            (None, None),
        )
        self.assertEqual(
            resolve_invoice_payment_intent({"id": "in_x", "payments": {"data": []}}),
            (None, None),
        )

    def test_a_succeeded_payment_is_preferred_over_a_failed_attempt(self):
        """
        An invoice can carry a decline followed by a retry. Refunding — or
        reading the status of — the failed attempt would be wrong.
        """
        invoice = {
            "id": "in_retry",
            "status": "paid",
            "payments": {
                "data": [
                    {
                        "status": "failed",
                        "payment": {"payment_intent": {"id": "pi_declined"}},
                    },
                    {
                        "status": "paid",
                        "payment": {"payment_intent": {"id": "pi_good"}},
                    },
                ]
            },
        }
        pi_id, _ = resolve_invoice_payment_intent(invoice)
        self.assertEqual(pi_id, "pi_good")

    def test_the_expand_targets_the_modern_path(self):
        """
        `expand=["payment_intent"]` is a silent no-op on the pinned version.
        The expand constant must name the real path or the object comes back
        as a bare string and every status check degrades to None.
        """
        self.assertEqual(
            INVOICE_PAYMENT_INTENT_EXPAND, ["payments.data.payment.payment_intent"]
        )


class CompensatingRefundTests(TestCase):
    """
    The financial regression guard: an already-paid customer must not be
    left double-charged after an interval-crossing upgrade.
    """

    def _run(self, invoice):
        with patch.object(real_stripe, "Subscription") as sub, patch.object(
            real_stripe, "Invoice"
        ) as inv, patch.object(real_stripe, "Refund") as refund:
            sub.retrieve.return_value = {"latest_invoice": "in_modern_1"}
            inv.retrieve.return_value = invoice
            StripeSubscriptionMutationService._void_or_refund_side_effect_invoice(
                "sub_1"
            )
            return refund, inv

    def test_the_refund_is_issued_for_a_modern_invoice(self):
        """
        THE REGRESSION. Before the fix `pi_id` was None, the `if pi_id:`
        branch never ran, and the duplicate charge was silently kept.
        """
        refund, _ = self._run(modern_invoice())

        refund.create.assert_called_once()
        kwargs = refund.create.call_args.kwargs
        self.assertEqual(
            kwargs["payment_intent"],
            PI_ID,
            "the compensating refund did not target the real PaymentIntent",
        )

    def test_the_refund_carries_a_stable_idempotency_key(self):
        """
        A replayed webhook or a rerun of the repair command must not refund
        the customer twice. Stripe refuses the second call on the same key.
        """
        refund, _ = self._run(modern_invoice())

        self.assertEqual(
            refund.create.call_args.kwargs["idempotency_key"],
            f"interval-change-refund-{PI_ID}",
        )

    def test_the_invoice_is_fetched_with_the_modern_expand(self):
        """
        Without the right expand the PaymentIntent is simply not in the
        payload, and the refund silently does not happen.
        """
        _, inv = self._run(modern_invoice())

        self.assertEqual(
            inv.retrieve.call_args.kwargs.get("expand"),
            INVOICE_PAYMENT_INTENT_EXPAND,
        )

    def test_a_legacy_shaped_invoice_still_refunds(self):
        refund, _ = self._run(legacy_invoice())

        refund.create.assert_called_once()
        self.assertEqual(refund.create.call_args.kwargs["payment_intent"], PI_ID)

    def test_an_unpaid_invoice_is_voided_rather_than_refunded(self):
        """The other branch must not regress: nothing to refund if unpaid."""
        with patch.object(real_stripe, "Subscription") as sub, patch.object(
            real_stripe, "Invoice"
        ) as inv, patch.object(real_stripe, "Refund") as refund:
            sub.retrieve.return_value = {"latest_invoice": "in_modern_1"}
            inv.retrieve.return_value = modern_invoice(status="open")
            StripeSubscriptionMutationService._void_or_refund_side_effect_invoice(
                "sub_1"
            )

        refund.create.assert_not_called()
        inv.void_invoice.assert_called_once()

    def test_an_invoice_with_no_resolvable_payment_intent_does_not_crash(self):
        refund, _ = self._run(
            {"id": "in_x", "status": "paid", "payments": {"data": []}}
        )

        refund.create.assert_not_called()
