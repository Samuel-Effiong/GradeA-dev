"""
billing/tests/test_refund_reconciliation_classification.py
==========================================================
Our own housekeeping must not look like an unexplained refund.

THE PROBLEM
-----------
`BillingTransactionService.handle_refund` flags any refund with no
matching BillingTransaction as needing manual reconciliation. That is
right for money moving that nobody can account for.

But this system issues refunds of its own. When a subscription interval
change makes Stripe raise a duplicate invoice for a period the customer
already paid for via Checkout, `_void_or_refund_side_effect_invoice`
refunds it. That invoice was DELIBERATELY never recorded as a customer
charge, so the resulting `charge.refunded` has nothing to match — and
every one of them raised a manual-review alert:

    charge.refunded for charge ch_3UE8wT... has no matching
    BillingTransaction — creating a standalone record for manual review.

Observed twice in a single live-QA run. An alarm that fires on routine
housekeeping is one nobody reads when a genuinely unexplained refund
appears, which is the failure that matters.

WHAT IS PINNED
--------------
The CLASSIFICATION, in both directions. Our refunds are recognised and
recorded quietly; anything we cannot account for still shouts. The second
half matters more than the first — it would be easy to fix the noise by
silencing the alert altogether, which trades a nuisance for a blind spot.
"""

import logging
from unittest.mock import MagicMock, patch

from django.test import TestCase

from billing.billing_transaction_service import BillingTransactionService
from billing.models import BillingTransaction, BillingTransactionStatus
from billing.payment_refunds import (
    SYSTEM_REFUND_INTERVAL_CHANGE_DUPLICATE,
    SYSTEM_REFUND_METADATA_KEY,
    system_refund_reason,
)


def charge(*, refund_metadata=None, charge_metadata=None, charge_id="ch_x"):
    return {
        "id": charge_id,
        "object": "charge",
        "payment_intent": "pi_unmatched",
        "invoice": None,
        "amount": 1500,
        "amount_captured": 1500,
        "amount_refunded": 1500,
        "currency": "usd",
        "receipt_url": "https://stripe.test/r/1",
        "metadata": charge_metadata or {},
        "refunds": {
            "object": "list",
            "data": [{"id": "re_1", "metadata": refund_metadata or {}}],
        },
    }


SYSTEM_META = {SYSTEM_REFUND_METADATA_KEY: SYSTEM_REFUND_INTERVAL_CHANGE_DUPLICATE}


class ReasonDetectionTests(TestCase):
    def test_a_refund_we_issued_is_recognised(self):
        self.assertEqual(
            system_refund_reason(charge(refund_metadata=SYSTEM_META)),
            SYSTEM_REFUND_INTERVAL_CHANGE_DUPLICATE,
        )

    def test_the_marker_is_also_read_from_the_charge(self):
        self.assertEqual(
            system_refund_reason(charge(charge_metadata=SYSTEM_META)),
            SYSTEM_REFUND_INTERVAL_CHANGE_DUPLICATE,
        )

    def test_an_ordinary_refund_is_not_claimed_as_ours(self):
        self.assertIsNone(system_refund_reason(charge()))

    def test_unrelated_metadata_does_not_count(self):
        self.assertIsNone(
            system_refund_reason(
                charge(refund_metadata={"note": "customer asked nicely"})
            )
        )

    def test_an_empty_marker_does_not_count(self):
        """`{key: ""}` is not a reason — it must not silence the alert."""
        self.assertIsNone(
            system_refund_reason(
                charge(refund_metadata={SYSTEM_REFUND_METADATA_KEY: ""})
            )
        )

    def test_a_charge_with_no_refunds_list_is_handled(self):
        bare = charge()
        bare.pop("refunds")
        self.assertIsNone(system_refund_reason(bare))


class SystemRefundIsRecordedQuietlyTests(TestCase):
    def test_it_does_NOT_raise_a_manual_review_warning(self):
        with self.assertLogs("billing.billing_transaction_service", level="INFO") as cm:
            BillingTransactionService.handle_refund(charge(refund_metadata=SYSTEM_META))

        self.assertFalse(
            any(record.levelno >= logging.WARNING for record in cm.records),
            "a refund this system issued on purpose was escalated for " "manual review",
        )

    def test_the_money_is_still_recorded(self):
        BillingTransactionService.handle_refund(charge(refund_metadata=SYSTEM_META))

        txn = BillingTransaction.objects.get(stripe_charge_id="ch_x")
        self.assertEqual(txn.amount_cents, 1500)
        self.assertEqual(txn.refunded_amount_cents, 1500)
        self.assertEqual(txn.status, BillingTransactionStatus.REFUNDED)

    def test_the_record_says_what_it_is(self):
        BillingTransactionService.handle_refund(charge(refund_metadata=SYSTEM_META))

        txn = BillingTransaction.objects.get(stripe_charge_id="ch_x")
        self.assertNotIn("needs manual reconciliation", txn.description)
        self.assertIn(SYSTEM_REFUND_INTERVAL_CHANGE_DUPLICATE, txn.description)

    def test_the_reason_is_queryable_not_just_prose(self):
        BillingTransactionService.handle_refund(charge(refund_metadata=SYSTEM_META))

        txn = BillingTransaction.objects.get(stripe_charge_id="ch_x")
        self.assertEqual(
            txn.metadata.get("system_refund_reason"),
            SYSTEM_REFUND_INTERVAL_CHANGE_DUPLICATE,
        )


class TheMarkerIsActuallyStampedTests(TestCase):
    """
    The producer end.

    Everything above tests the READER — that a marked refund is
    recognised. None of it proves the compensating refund is marked in the
    first place, and a mutation stripping the metadata from
    `Refund.create` passed the entire suite. Without this test the fix
    depends on an unverified write.
    """

    def _drive(self, refund_create):
        from billing.stripe_service import StripeSubscriptionMutationService

        invoice = {
            "id": "in_dup",
            "status": "paid",
            "payments": {"data": [{"payment": {"payment_intent": {"id": "pi_dup"}}}]},
        }
        with patch(
            "billing.stripe_service.stripe.Invoice.retrieve", return_value=invoice
        ):
            with patch(
                "billing.stripe_service.stripe.Subscription.retrieve",
                return_value={"latest_invoice": "in_dup"},
            ):
                with patch(
                    "billing.stripe_service.stripe.Refund.create", refund_create
                ):
                    StripeSubscriptionMutationService._void_or_refund_side_effect_invoice(
                        "sub_dup"
                    )

    def test_the_compensating_refund_carries_the_system_marker(self):
        refund_create = MagicMock()

        self._drive(refund_create)

        self.assertTrue(
            refund_create.called,
            "the duplicate invoice was not refunded at all",
        )
        metadata = refund_create.call_args.kwargs.get("metadata") or {}
        self.assertEqual(
            metadata.get(SYSTEM_REFUND_METADATA_KEY),
            SYSTEM_REFUND_INTERVAL_CHANGE_DUPLICATE,
            "the compensating refund was issued unmarked, so the refund it "
            "triggers will be filed as unexplained money movement",
        )

    def test_the_marker_survives_the_round_trip(self):
        """
        Producer to reader, end to end: whatever `Refund.create` was
        given must be what `system_refund_reason` later recognises. Two
        halves that agree only by convention drift apart silently.
        """
        refund_create = MagicMock()
        self._drive(refund_create)
        metadata = refund_create.call_args.kwargs.get("metadata") or {}

        self.assertEqual(
            system_refund_reason(charge(refund_metadata=metadata)),
            SYSTEM_REFUND_INTERVAL_CHANGE_DUPLICATE,
        )


class UnexplainedRefundStillShoutsTests(TestCase):
    """The half that must not regress while fixing the noise."""

    def test_an_unexplained_refund_still_warns(self):
        with self.assertLogs(
            "billing.billing_transaction_service", level="WARNING"
        ) as cm:
            BillingTransactionService.handle_refund(charge())

        self.assertTrue(
            any("manual review" in record.getMessage() for record in cm.records),
            "an unexplained refund stopped raising an alert",
        )

    def test_it_is_still_recorded_for_review(self):
        BillingTransactionService.handle_refund(charge())

        txn = BillingTransaction.objects.get(stripe_charge_id="ch_x")
        self.assertIn("needs manual reconciliation", txn.description)

    def test_a_forged_looking_marker_on_an_unrelated_key_does_not_silence_it(self):
        with self.assertLogs(
            "billing.billing_transaction_service", level="WARNING"
        ) as cm:
            BillingTransactionService.handle_refund(
                charge(refund_metadata={"system": "yes", "reason": "trust me"})
            )

        self.assertTrue(
            any("manual review" in record.getMessage() for record in cm.records)
        )
