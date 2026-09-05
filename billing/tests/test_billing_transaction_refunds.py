"""
billing/tests/test_billing_transaction_refunds.py
=================================================
`BillingTransactionService.handle_refund` and the status-precedence rule in
`record()` — the two pieces of money logic in
`billing/billing_transaction_service.py` (49.3% covered) that nothing
exercised.

WHY THIS FILE EXISTS
--------------------
Only `test_overage_cap.py` referenced this service at all, and only for the
recording path. The uncovered block was `handle_refund` in its entirety
plus most of the field-merge logic inside `record()`.

Two rules there are genuine money correctness, not plumbing:

1. FULL VS PARTIAL is decided by comparing `amount_refunded` against the
   transaction's own `amount_cents` (falling back to the charge's captured
   amount). Getting the comparison backwards or off-by-one mislabels a
   partial refund as full, and the row then reads as "customer was made
   whole" when they were not.

2. A REFUNDED / PARTIALLY_REFUNDED status IS NEVER DOWNGRADED by a later
   `record()` call. Stripe does not guarantee event ordering, so a
   `charge.refunded` can be processed before a slower `invoice.paid` for
   the same money. Without the guard, the later event would relabel a
   refunded transaction as PAID — the books would show revenue that was
   given back.

3. A refund with NO matching local transaction must create a standalone
   row flagged for manual reconciliation rather than being dropped. A
   silently-dropped refund is money that left the account with no record.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.billing_transaction_service import BillingTransactionService
from billing.models import (
    BillingTransaction,
    BillingTransactionSource,
    BillingTransactionStatus,
    BillingTransactionType,
)
from users.models import UserTypes

CustomUser = get_user_model()


def charge_payload(
    *,
    charge_id="ch_refund_1",
    invoice_id=None,
    payment_intent_id=None,
    amount_refunded=0,
    amount_captured=10_000,
    receipt_url=None,
):
    return {
        "id": charge_id,
        "invoice": invoice_id,
        "payment_intent": payment_intent_id,
        "amount_refunded": amount_refunded,
        "amount_captured": amount_captured,
        "amount": amount_captured,
        "currency": "usd",
        "receipt_url": receipt_url,
    }


class RefundApplicationTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="refund.subject@billing.test",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.txn = BillingTransaction.objects.create(
            id=uuid.uuid4(),
            source=BillingTransactionSource.INDIVIDUAL,
            transaction_type=(BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE),
            status=BillingTransactionStatus.PAID,
            user=self.user,
            amount_cents=10_000,
            currency="usd",
            stripe_invoice_id="in_refund_1",
            stripe_payment_intent_id="pi_refund_1",
            occurred_at=timezone.now(),
        )

    def test_a_full_refund_is_labelled_refunded(self):
        BillingTransactionService.handle_refund(
            charge_payload(invoice_id="in_refund_1", amount_refunded=10_000)
        )

        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, BillingTransactionStatus.REFUNDED)
        self.assertEqual(self.txn.refunded_amount_cents, 10_000)

    def test_a_partial_refund_is_labelled_partially_refunded(self):
        """
        The distinction is what tells you whether the customer was made
        whole. Mislabelling a partial refund as full overstates what was
        returned.
        """
        BillingTransactionService.handle_refund(
            charge_payload(invoice_id="in_refund_1", amount_refunded=2_500)
        )

        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, BillingTransactionStatus.PARTIALLY_REFUNDED)
        self.assertEqual(self.txn.refunded_amount_cents, 2_500)

    def test_a_refund_of_exactly_the_charge_is_full_not_partial(self):
        """The boundary: >= is the documented comparison, not >."""
        BillingTransactionService.handle_refund(
            charge_payload(invoice_id="in_refund_1", amount_refunded=10_000)
        )

        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, BillingTransactionStatus.REFUNDED)

    def test_one_cent_short_is_still_partial(self):
        BillingTransactionService.handle_refund(
            charge_payload(invoice_id="in_refund_1", amount_refunded=9_999)
        )

        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, BillingTransactionStatus.PARTIALLY_REFUNDED)

    def test_the_refund_matches_by_payment_intent_when_there_is_no_invoice(self):
        """Documented fallback order: invoice id first, then PaymentIntent."""
        BillingTransactionService.handle_refund(
            charge_payload(payment_intent_id="pi_refund_1", amount_refunded=10_000)
        )

        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, BillingTransactionStatus.REFUNDED)

    def test_the_charge_id_is_recorded_on_the_transaction(self):
        BillingTransactionService.handle_refund(
            charge_payload(
                charge_id="ch_the_refund", invoice_id="in_refund_1", amount_refunded=1
            )
        )

        self.txn.refresh_from_db()
        self.assertEqual(self.txn.stripe_charge_id, "ch_the_refund")

    def test_a_receipt_url_is_filled_in_but_never_overwritten(self):
        BillingTransactionService.handle_refund(
            charge_payload(
                invoice_id="in_refund_1",
                amount_refunded=1,
                receipt_url="https://stripe.test/receipt-a",
            )
        )
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.receipt_url, "https://stripe.test/receipt-a")

        BillingTransactionService.handle_refund(
            charge_payload(
                invoice_id="in_refund_1",
                amount_refunded=2,
                receipt_url="https://stripe.test/receipt-b",
            )
        )
        self.txn.refresh_from_db()
        self.assertEqual(
            self.txn.receipt_url,
            "https://stripe.test/receipt-a",
            "an existing receipt link was overwritten by a later event",
        )

    def test_no_extra_transaction_is_created_when_one_matches(self):
        before = BillingTransaction.objects.count()

        BillingTransactionService.handle_refund(
            charge_payload(invoice_id="in_refund_1", amount_refunded=10_000)
        )

        self.assertEqual(BillingTransaction.objects.count(), before)


class UnmatchedRefundTests(TestCase):
    """
    A refund we cannot match locally is money that left the account. It
    must surface as a row for a human, never be dropped.
    """

    def test_an_unmatched_refund_creates_a_standalone_record(self):
        before = BillingTransaction.objects.count()

        BillingTransactionService.handle_refund(
            charge_payload(
                charge_id="ch_orphan",
                invoice_id="in_never_seen",
                amount_refunded=7_500,
                amount_captured=7_500,
            )
        )

        self.assertEqual(BillingTransaction.objects.count(), before + 1)
        row = BillingTransaction.objects.get(stripe_charge_id="ch_orphan")
        self.assertEqual(row.status, BillingTransactionStatus.REFUNDED)
        self.assertEqual(row.refunded_amount_cents, 7_500)
        self.assertEqual(row.amount_cents, 7_500)

    def test_the_standalone_record_says_it_needs_reconciliation(self):
        """A row nobody can identify as needing attention is as good as lost."""
        BillingTransactionService.handle_refund(
            charge_payload(charge_id="ch_orphan2", amount_refunded=100)
        )

        row = BillingTransaction.objects.get(stripe_charge_id="ch_orphan2")
        self.assertIn("manual reconciliation", row.description.lower())
        self.assertEqual(row.transaction_type, BillingTransactionType.OTHER)

    def test_an_unmatched_partial_refund_is_labelled_partial(self):
        BillingTransactionService.handle_refund(
            charge_payload(
                charge_id="ch_orphan3",
                amount_refunded=400,
                amount_captured=1_000,
            )
        )

        row = BillingTransaction.objects.get(stripe_charge_id="ch_orphan3")
        self.assertEqual(row.status, BillingTransactionStatus.PARTIALLY_REFUNDED)

    def test_the_unmatched_refund_is_logged_as_a_warning(self):
        with self.assertLogs(
            "billing.billing_transaction_service", level="WARNING"
        ) as logs:
            BillingTransactionService.handle_refund(
                charge_payload(charge_id="ch_orphan4", amount_refunded=1)
            )

        self.assertTrue(
            any("no matching BillingTransaction" in line for line in logs.output),
            logs.output,
        )


class RefundStatusIsNeverDowngradedTests(TestCase):
    """
    Stripe does not guarantee event ordering. A `charge.refunded` can land
    before a slower event for the same money. If a later `record()` could
    relabel the row PAID, the books would show revenue that was given back.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="downgrade@billing.test",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.txn = BillingTransaction.objects.create(
            id=uuid.uuid4(),
            source=BillingTransactionSource.INDIVIDUAL,
            transaction_type=(BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE),
            status=BillingTransactionStatus.PAID,
            user=self.user,
            amount_cents=10_000,
            currency="usd",
            stripe_invoice_id="in_downgrade_1",
            occurred_at=timezone.now(),
        )

    def _refund_it(self, amount):
        BillingTransactionService.handle_refund(
            charge_payload(invoice_id="in_downgrade_1", amount_refunded=amount)
        )
        self.txn.refresh_from_db()

    def test_a_late_paid_event_cannot_unrefund_a_full_refund(self):
        self._refund_it(10_000)
        self.assertEqual(self.txn.status, BillingTransactionStatus.REFUNDED)

        BillingTransactionService.record(
            source=BillingTransactionSource.INDIVIDUAL,
            transaction_type=(BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE),
            status=BillingTransactionStatus.PAID,
            billing_method="STRIPE",
            amount_cents=10_000,
            currency="usd",
            stripe_invoice_id="in_downgrade_1",
            user=self.user,
            occurred_at=timezone.now(),
        )

        self.txn.refresh_from_db()
        self.assertEqual(
            self.txn.status,
            BillingTransactionStatus.REFUNDED,
            "a late PAID event relabelled a refunded transaction as revenue",
        )

    def test_a_late_paid_event_cannot_unrefund_a_partial_refund(self):
        self._refund_it(2_500)
        self.assertEqual(self.txn.status, BillingTransactionStatus.PARTIALLY_REFUNDED)

        BillingTransactionService.record(
            source=BillingTransactionSource.INDIVIDUAL,
            transaction_type=(BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE),
            status=BillingTransactionStatus.PAID,
            billing_method="STRIPE",
            amount_cents=10_000,
            currency="usd",
            stripe_invoice_id="in_downgrade_1",
            user=self.user,
            occurred_at=timezone.now(),
        )

        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, BillingTransactionStatus.PARTIALLY_REFUNDED)

    def test_the_refunded_amount_survives_a_later_record_call(self):
        self._refund_it(2_500)

        BillingTransactionService.record(
            source=BillingTransactionSource.INDIVIDUAL,
            transaction_type=(BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE),
            status=BillingTransactionStatus.PAID,
            billing_method="STRIPE",
            amount_cents=10_000,
            currency="usd",
            stripe_invoice_id="in_downgrade_1",
            user=self.user,
            occurred_at=timezone.now(),
        )

        self.txn.refresh_from_db()
        self.assertEqual(self.txn.refunded_amount_cents, 2_500)

    def test_a_normal_status_transition_still_applies(self):
        """
        The guard must protect refunds only — it must not freeze every
        status, or a PENDING charge could never be marked PAID.
        """
        pending = BillingTransaction.objects.create(
            id=uuid.uuid4(),
            source=BillingTransactionSource.INDIVIDUAL,
            transaction_type=(BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE),
            status=BillingTransactionStatus.PENDING,
            user=self.user,
            amount_cents=5_000,
            currency="usd",
            stripe_invoice_id="in_pending_1",
            occurred_at=timezone.now(),
        )

        BillingTransactionService.record(
            source=BillingTransactionSource.INDIVIDUAL,
            transaction_type=(BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE),
            status=BillingTransactionStatus.PAID,
            billing_method="STRIPE",
            amount_cents=5_000,
            currency="usd",
            stripe_invoice_id="in_pending_1",
            user=self.user,
            occurred_at=timezone.now(),
        )

        pending.refresh_from_db()
        self.assertEqual(pending.status, BillingTransactionStatus.PAID)
