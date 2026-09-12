"""
billing/tests/test_overage_refund_lifecycle.py
==============================================
Refunding an overage purchase must take back what it bought.

THE DEFECT
----------
`charge.refunded` recorded the refund against the BillingTransaction and
stopped there. Measured on a real handler run before the fix:

    after purchase       credits 500, blocks used 1, txn PAID
    after FULL refund    credits 500, blocks used 1, txn REFUNDED

Every cent back, every credit kept. Repeated, that is unlimited free AI
credits: buy, use, refund, repeat.

THE INVARIANT UNDER TEST
------------------------
    Financial records and customer entitlement must never silently
    diverge from the final payment state.

The rules being pinned here are stated in full in billing/payment_refunds.py.
Each rule the owner asked about has at least one test named after it:
full, partial, before consumption, after partial consumption, after full
consumption, multiple refunds, duplicate and out-of-order delivery,
refund-then-repurchase, and failure-then-retry.

WHY EVERY QUANTITY IS A CUMULATIVE TARGET
-----------------------------------------
Stripe's `amount_refunded` is cumulative over the charge, so each
delivery states a total rather than an increment. Reversal is computed as
"bring the total reclaimed up to X" and never as "reclaim Y more", which
is what makes duplicates inert, out-of-order deliveries harmless and
retries convergent — all three are asserted below rather than assumed.

THE INTERACTION THAT IS EASIEST TO GET WRONG
--------------------------------------------
A payment can be partly refunded and then disputed for the rest. Both
paths reduce entitlement, so a shared view of what has already been
settled is the only thing standing between us and reversing the same
credits twice — once by each cause. The final class drives exactly that,
in both orders, and asserts the total never exceeds what the payment
granted.
"""

import threading
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from billing.models import (
    BillingInterval,
    BillingTransaction,
    BillingTransactionStatus,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    InsufficientCreditsError,
    PaymentRefund,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
    UserSubscription,
)
from billing.stripe_service import StripeWebhookHandler
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()

BLOCK = 500
PRICE = 1000  # cents per block


def make_plan(name=PlanType.STANDARD, tier=PlanTier.STANDARD, max_blocks=10):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=str(name),
        category=PlanCategory.INDIVIDUAL,
        tier=tier,
        interval=BillingInterval.MONTHLY,
        monthly_credits=10_000,
        overage_block_size=BLOCK,
        overage_block_price=10,
        max_overage_blocks=max_blocks,
        stripe_overage_price_id="price_overage",
        is_active=True,
    )


class RefundFixture:
    """A subscribed teacher who has bought overage blocks."""

    plan: SubscriptionPlan

    def build(self, email="refund@billing.test", plan=None, school=None):
        plan = plan or self.plan
        user = CustomUser.objects.create_user(
            email=email,
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
            school=school,
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=user)
        # Start from an empty wallet so every credit under test is one this
        # purchase put there — otherwise a monthly allocation would mask
        # whether the reversal hit the right bucket.
        CreditBucket.objects.filter(wallet=wallet).delete()
        now = timezone.now()
        UserSubscription.objects.create(
            user=user,
            plan=plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - timedelta(days=1),
            billing_cycle_end=now + timedelta(days=29),
            next_credit_grant_at=now + timedelta(days=29),
        )
        return user, wallet

    # -- driving the flows -------------------------------------------------

    def purchase(
        self,
        wallet,
        *,
        payment_intent="pi_ref_1",
        session_id="cs_ref_1",
        quantity=1,
        plan=None,
    ):
        """A completed individual overage checkout — flow 1."""
        plan = plan or self.plan
        metadata = {
            "flow": "overage_block_purchase_checkout",
            "user_id": str(wallet.user.id),
            "wallet_id": str(wallet.id),
            "plan_id": str(plan.id),
            "quantity": str(quantity),
        }
        StripeWebhookHandler.handle_checkout_completed(
            {
                "id": session_id,
                "object": "checkout.session",
                "amount_total": PRICE * quantity,
                "currency": "usd",
                "payment_status": "paid",
                "payment_intent": payment_intent,
                "invoice": None,
                "metadata": metadata,
            }
        )

    def refund(
        self,
        *,
        payment_intent="pi_ref_1",
        charge="ch_ref_1",
        captured=PRICE,
        refunded=PRICE,
        metadata=None,
        refund_metadata=None,
    ):
        """
        A `charge.refunded` event.

        `refunded` is CUMULATIVE, exactly as Stripe sends it — the total
        returned so far on this charge, not the size of the latest refund.
        """
        charge_obj = {
            "id": charge,
            "object": "charge",
            "payment_intent": payment_intent,
            "invoice": None,
            "amount": captured,
            "amount_captured": captured,
            "amount_refunded": refunded,
            "currency": "usd",
            "receipt_url": "https://stripe.test/r/1",
            "metadata": metadata or {},
        }
        if refund_metadata is not None:
            charge_obj["refunds"] = {
                "object": "list",
                "data": [{"id": "re_1", "metadata": refund_metadata}],
            }
        return StripeWebhookHandler.handle_charge_refunded(charge_obj)

    def chargeback(
        self, *, payment_intent, dispute_id="dp_1", status="lost", amount=PRICE
    ):
        return StripeWebhookHandler.handle_dispute_event(
            {
                "id": dispute_id,
                "status": status,
                "reason": "fraudulent",
                "amount": amount,
                "currency": "usd",
                "created": int(timezone.now().timestamp()),
                "payment_intent": payment_intent,
                "charge": "ch_dispute",
                "balance_transactions": [{"fee": 1500}],
            }
        )

    # -- state readers -----------------------------------------------------

    def remaining(self, wallet):
        wallet.refresh_from_db()
        return wallet.total_remaining_credits()

    def reversal_rows(self, wallet):
        return CreditLedger.objects.filter(
            bucket__wallet=wallet,
            ledger_type__in=(
                CreditLedgerType.REFUND_REVERSAL,
                CreditLedgerType.DISPUTE_REVERSAL,
            ),
        )

    def reversed_total(self, wallet):
        return sum(-row.amount for row in self.reversal_rows(wallet))


class FullRefundTests(TestCase, RefundFixture):
    """Rule 1 — the money went back, so the credits come back."""

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build()
        self.purchase(self.wallet)

    def test_THE_REGRESSION_a_full_refund_reclaims_every_credit(self):
        self.refund()

        self.assertEqual(
            self.remaining(self.wallet),
            0,
            "the customer was refunded in full and kept the credits — money "
            "and entitlement have diverged",
        )

    def test_the_money_is_still_recorded_as_refunded(self):
        """The clawback must not cost us the money record."""
        self.refund()

        txn = BillingTransaction.objects.get(stripe_payment_intent_id="pi_ref_1")
        self.assertEqual(txn.status, BillingTransactionStatus.REFUNDED)
        self.assertEqual(txn.refunded_amount_cents, PRICE)

    def test_the_reversal_is_appended_not_edited_into_the_grant(self):
        """
        Append-only means the history still shows both halves: 500 given,
        500 taken back. A smaller PURCHASE row would be a rewritten past.
        """
        self.refund()

        grant = CreditLedger.objects.get(
            bucket__wallet=self.wallet, ledger_type=CreditLedgerType.PURCHASE
        )
        reversal = CreditLedger.objects.get(
            bucket__wallet=self.wallet, ledger_type=CreditLedgerType.REFUND_REVERSAL
        )
        self.assertEqual(grant.amount, BLOCK)
        self.assertEqual(reversal.amount, -BLOCK)

    def test_the_reversal_is_attributed_to_its_payment(self):
        self.refund()

        reversal = CreditLedger.objects.get(
            ledger_type=CreditLedgerType.REFUND_REVERSAL
        )
        self.assertEqual(reversal.stripe_payment_intent_id, "pi_ref_1")
        self.assertEqual(reversal.user_id, self.user.id)

    def test_the_reversal_uses_its_own_ledger_type_not_REFUND(self):
        """
        REFUND already means the opposite thing — credits handed BACK to a
        user whose task failed. Reusing it would invert the sign of every
        report that reads it.
        """
        self.refund()

        self.assertFalse(
            CreditLedger.objects.filter(
                bucket__wallet=self.wallet, ledger_type=CreditLedgerType.REFUND
            ).exists()
        )

    def test_a_full_refund_returns_the_block_allowance(self):
        """The cap must not punish a purchase that was undone."""
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.overage_blocks_used, 1)

        self.refund()

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.overage_blocks_used, 0)

    def test_a_refund_before_consumption_leaves_no_debt(self):
        """Rule 3 — nothing was spent, so nothing is owed."""
        self.refund()

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.refund_deficit_credits, 0)
        self.assertFalse(self.wallet.is_consumption_blocked)

    def test_a_PaymentRefund_row_records_the_outcome(self):
        self.refund()

        row = PaymentRefund.objects.get(stripe_payment_intent_id="pi_ref_1")
        self.assertEqual(row.credits_granted, BLOCK)
        self.assertEqual(row.credits_reversed, BLOCK)
        self.assertEqual(row.credits_deficit, 0)
        self.assertTrue(row.is_fully_refunded)


class PartialRefundTests(TestCase, RefundFixture):
    """Rule 2 — reclaim the same proportion of credits as of money."""

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build()
        self.purchase(self.wallet)

    def test_half_the_money_back_reclaims_half_the_credits(self):
        self.refund(refunded=PRICE // 2)

        self.assertEqual(self.remaining(self.wallet), BLOCK // 2)

    def test_the_transaction_reads_partially_refunded(self):
        self.refund(refunded=PRICE // 2)

        txn = BillingTransaction.objects.get(stripe_payment_intent_id="pi_ref_1")
        self.assertEqual(txn.status, BillingTransactionStatus.PARTIALLY_REFUNDED)

    def test_rounding_favours_the_customer(self):
        """
        A third of 500 is 166.67. Flooring reclaims 166 and leaves 334 —
        rounding up would take a credit the customer still paid for.
        """
        self.refund(refunded=PRICE // 3)

        self.assertEqual(self.remaining(self.wallet), BLOCK - 166)

    def test_a_partial_refund_does_NOT_return_the_block_allowance(self):
        """
        Half a block is not a block. The counter resets at renewal anyway,
        so leaving it alone costs at most one block for the rest of the
        cycle — much cheaper than letting the cap be gamed.
        """
        self.refund(refunded=PRICE // 2)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.overage_blocks_used, 1)

    def test_a_partial_refund_of_a_spent_purchase_owes_only_its_share(self):
        self.wallet.consume_credits(BLOCK, feature="grading", task_id=str(uuid.uuid4()))

        self.refund(refunded=PRICE // 2)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.refund_deficit_credits, BLOCK // 2)


class RefundAfterConsumptionTests(TestCase, RefundFixture):
    """Rules 4 and 5 — what is spent becomes a debt, never a deletion."""

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build()
        self.purchase(self.wallet)

    def _spend(self, amount):
        self.wallet.consume_credits(
            amount, feature="grading", task_id=str(uuid.uuid4())
        )

    def test_partially_spent_reclaims_the_rest_and_owes_the_difference(self):
        self._spend(300)

        self.refund()

        self.wallet.refresh_from_db()
        self.assertEqual(self.remaining(self.wallet), 0)
        self.assertEqual(self.wallet.refund_deficit_credits, 300)

    def test_fully_spent_owes_the_whole_purchase(self):
        self._spend(BLOCK)

        self.refund()

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.refund_deficit_credits, BLOCK)

    def test_a_debt_blocks_further_consumption(self):
        self._spend(BLOCK)
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=10_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=20),
        )

        self.refund()

        with self.assertRaises(InsufficientCreditsError):
            self._spend(10)

    def test_usage_history_is_never_rewritten(self):
        """
        The work really happened and really cost us. Deleting the usage
        rows to make the balance tidy would make the ledger lie.
        """
        self._spend(BLOCK)
        before = list(
            CreditLedger.objects.filter(
                bucket__wallet=self.wallet, ledger_type=CreditLedgerType.CONSUME
            ).values_list("id", "amount")
        )

        self.refund()

        after = list(
            CreditLedger.objects.filter(
                bucket__wallet=self.wallet, ledger_type=CreditLedgerType.CONSUME
            ).values_list("id", "amount")
        )
        self.assertEqual(before, after)

    def test_the_debt_is_not_cleared_by_buying_more_credits(self):
        """
        Rule 8. The debt is money owed for work already delivered; a new
        purchase is a new entitlement, not a payment against it.
        """
        self._spend(BLOCK)
        self.refund()

        self.purchase(self.wallet, payment_intent="pi_ref_2", session_id="cs_ref_2")

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.refund_deficit_credits, BLOCK)
        self.assertTrue(self.wallet.is_consumption_blocked)


class RefundIdempotencyTests(TestCase, RefundFixture):
    """Rules 6, 7 and 9 — duplicates, ordering, multiple refunds, retry."""

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build()
        self.purchase(self.wallet)

    def test_a_duplicate_delivery_does_not_reverse_twice(self):
        self.refund()
        self.refund()

        self.assertEqual(self.reversed_total(self.wallet), BLOCK)
        self.assertEqual(self.remaining(self.wallet), 0)

    def test_a_duplicate_delivery_of_a_SPENT_purchase_does_not_double_the_debt(
        self,
    ):
        """
        The subtle one. A reclaim leaves a negative ledger row a duplicate
        can see; a DEBT leaves no ledger row at all, because no bucket
        moved. Counting only the ledger made the second delivery record the
        same debt again — measured at 1000 owed on a 500 credit purchase.
        """
        self.wallet.consume_credits(BLOCK, feature="grading", task_id=str(uuid.uuid4()))

        self.refund()
        self.refund()

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.refund_deficit_credits, BLOCK)

    def test_ten_duplicate_deliveries_are_still_one_reversal(self):
        for _ in range(10):
            self.refund()

        self.assertEqual(self.reversed_total(self.wallet), BLOCK)

    def test_successive_partial_refunds_accumulate_to_the_total(self):
        """
        Rule 6. Stripe's amount_refunded is cumulative, so three 20%
        deliveries mean 60% refunded — not 20% three times.
        """
        self.refund(refunded=200)
        self.refund(refunded=400)
        self.refund(refunded=600)

        self.assertEqual(self.reversed_total(self.wallet), 300)
        self.assertEqual(self.remaining(self.wallet), BLOCK - 300)

    def test_a_partial_then_full_refund_ends_at_fully_reversed(self):
        self.refund(refunded=PRICE // 2)
        self.refund(refunded=PRICE)

        self.assertEqual(self.remaining(self.wallet), 0)
        self.assertEqual(self.reversed_total(self.wallet), BLOCK)

    def test_an_out_of_order_older_event_does_not_un_reverse(self):
        """
        Rule 7. Stripe does not guarantee ordering, so the 20% delivery can
        land after the 100% one. It must be inert, not a partial restore.
        """
        self.refund(refunded=PRICE)
        self.refund(refunded=200)

        self.assertEqual(self.remaining(self.wallet), 0)
        self.assertEqual(self.reversed_total(self.wallet), BLOCK)

    def test_the_stored_refund_total_is_monotonic(self):
        self.refund(refunded=PRICE)
        self.refund(refunded=200)

        row = PaymentRefund.objects.get(stripe_payment_intent_id="pi_ref_1")
        self.assertEqual(row.amount_refunded_cents, PRICE)

    def test_a_reversal_can_never_exceed_what_the_payment_granted(self):
        """The ceiling, asserted directly against an absurd payload."""
        self.refund(captured=PRICE, refunded=PRICE * 10)

        self.assertEqual(self.reversed_total(self.wallet), BLOCK)

    def test_a_retry_after_a_failed_run_converges_rather_than_compounds(self):
        """
        Rule 9. The handler runs in one transaction; a failure rolls back
        and Celery retries. Simulated by delivering the same event again
        after a rolled-back attempt.
        """
        from django.db import transaction

        try:
            with transaction.atomic():
                self.refund()
                raise RuntimeError("simulated failure after the reversal")
        except RuntimeError:
            pass

        self.assertEqual(
            self.remaining(self.wallet),
            BLOCK,
            "the rollback did not undo the reversal, so the retry below "
            "would not be testing a retry",
        )

        self.refund()

        self.assertEqual(self.remaining(self.wallet), 0)
        self.assertEqual(self.reversed_total(self.wallet), BLOCK)


class RefundScopeTests(TestCase, RefundFixture):
    """What a refund must NOT reach."""

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build()
        self.other, self.other_wallet = self.build(email="other@billing.test")

    def test_a_refund_never_touches_another_wallet(self):
        self.purchase(self.wallet, payment_intent="pi_mine", session_id="cs_mine")
        self.purchase(
            self.other_wallet, payment_intent="pi_theirs", session_id="cs_theirs"
        )

        self.refund(payment_intent="pi_mine")

        self.assertEqual(self.remaining(self.wallet), 0)
        self.assertEqual(
            self.remaining(self.other_wallet),
            BLOCK,
            "refunding one customer reclaimed another customer's credits",
        )

    def test_a_refund_only_reclaims_the_block_ITS_payment_bought(self):
        """
        Two purchases, one refunded. Reclaiming "the newest bucket" instead
        of the refunded payment's own would take the wrong block.
        """
        self.purchase(self.wallet, payment_intent="pi_first", session_id="cs_first")
        self.purchase(self.wallet, payment_intent="pi_second", session_id="cs_second")

        self.refund(payment_intent="pi_first", charge="ch_first")

        self.assertEqual(self.remaining(self.wallet), BLOCK)
        first_bucket_ledger = CreditLedger.objects.get(
            stripe_payment_intent_id="pi_first",
            ledger_type=CreditLedgerType.REFUND_REVERSAL,
        )
        second_grant = CreditLedger.objects.get(
            stripe_payment_intent_id="pi_second",
            ledger_type=CreditLedgerType.PURCHASE,
        )
        self.assertEqual(
            first_bucket_ledger.bucket_id,
            CreditLedger.objects.get(
                stripe_payment_intent_id="pi_first",
                ledger_type=CreditLedgerType.PURCHASE,
            ).bucket_id,
        )
        self.assertNotEqual(first_bucket_ledger.bucket_id, second_grant.bucket_id)

    def test_a_refund_of_a_payment_that_granted_nothing_is_money_only(self):
        """
        A subscription invoice refund. Deliberately out of scope — the
        allocation is entangled with plan changes and prorations — so it
        records the money and leaves entitlement alone.
        """
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=10_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=20),
        )
        BillingTransaction.objects.create(
            source="INDIVIDUAL",
            transaction_type="SUBSCRIPTION_PAYMENT",
            status=BillingTransactionStatus.PAID,
            billing_method="STRIPE",
            amount_cents=1999,
            currency="usd",
            user=self.user,
            stripe_payment_intent_id="pi_subscription",
            occurred_at=timezone.now(),
        )

        result = self.refund(
            payment_intent="pi_subscription", captured=1999, refunded=1999
        )

        self.assertIsNone(result)
        self.assertEqual(self.remaining(self.wallet), 10_000)
        txn = BillingTransaction.objects.get(stripe_payment_intent_id="pi_subscription")
        self.assertEqual(txn.status, BillingTransactionStatus.REFUNDED)

    def test_a_refund_with_no_payment_intent_is_money_only(self):
        result = self.refund(payment_intent=None)

        self.assertIsNone(result)


class DeliberateRetentionTests(TestCase, RefundFixture):
    """
    Absorbing the loss on purpose is supported — but it has to be ASKED
    for, and it gets recorded as a decision rather than happening by
    accident.
    """

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build()
        self.purchase(self.wallet)

    def test_retain_credits_on_the_refund_keeps_the_credits(self):
        self.refund(refund_metadata={"retain_credits": "true"})

        self.assertEqual(self.remaining(self.wallet), BLOCK)

    def test_retain_credits_on_the_charge_also_works(self):
        self.refund(metadata={"retain_credits": "true"})

        self.assertEqual(self.remaining(self.wallet), BLOCK)

    def test_the_decision_is_recorded_not_silent(self):
        self.refund(refund_metadata={"retain_credits": "true"})

        row = PaymentRefund.objects.get(stripe_payment_intent_id="pi_ref_1")
        self.assertTrue(row.retain_credits)
        self.assertIn("deliberately", row.notes)

    def test_the_money_is_still_recorded_as_refunded(self):
        self.refund(refund_metadata={"retain_credits": "true"})

        txn = BillingTransaction.objects.get(stripe_payment_intent_id="pi_ref_1")
        self.assertEqual(txn.status, BillingTransactionStatus.REFUNDED)

    def test_an_unrelated_metadata_value_does_not_trigger_retention(self):
        """The flag must be opt-in, not "any metadata present"."""
        self.refund(refund_metadata={"note": "customer complained"})

        self.assertEqual(self.remaining(self.wallet), 0)

    def test_a_falsey_flag_does_not_trigger_retention(self):
        self.refund(refund_metadata={"retain_credits": "false"})

        self.assertEqual(self.remaining(self.wallet), 0)


class RefundAndDisputeInteractionTests(TestCase, RefundFixture):
    """
    The same payment reversed by two different causes.

    A partial refund does not stop a customer disputing the rest, so both
    paths can act on one payment. Neither may reverse what the other
    already did.
    """

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build()
        self.purchase(self.wallet, payment_intent="pi_both")

    def test_a_chargeback_on_an_overage_purchase_now_reclaims(self):
        """
        Previously nothing: the reversal only understood subscription
        payments, so a charged-back overage block was reclaimed in full...
        of zero credits.
        """
        self.chargeback(payment_intent="pi_both")

        self.assertEqual(self.remaining(self.wallet), 0)

    def test_refund_then_chargeback_does_not_reverse_twice(self):
        self.refund(payment_intent="pi_both")
        self.chargeback(payment_intent="pi_both")

        self.assertEqual(
            self.reversed_total(self.wallet),
            BLOCK,
            "the same credits were reclaimed by both the refund and the " "chargeback",
        )

    def test_chargeback_then_refund_does_not_reverse_twice(self):
        self.chargeback(payment_intent="pi_both")
        self.refund(payment_intent="pi_both")

        self.assertEqual(self.reversed_total(self.wallet), BLOCK)

    def test_a_partial_refund_then_a_chargeback_reclaims_only_the_remainder(self):
        self.refund(payment_intent="pi_both", refunded=PRICE // 2)
        self.chargeback(payment_intent="pi_both")

        self.assertEqual(self.reversed_total(self.wallet), BLOCK)
        self.assertEqual(self.remaining(self.wallet), 0)

    def test_the_debt_is_not_double_counted_across_causes(self):
        """
        Fully spent, then refunded, then charged back. One 500-credit debt,
        not two.
        """
        self.wallet.consume_credits(BLOCK, feature="grading", task_id=str(uuid.uuid4()))

        self.refund(payment_intent="pi_both")
        self.chargeback(payment_intent="pi_both")

        self.wallet.refresh_from_db()
        self.assertEqual(
            self.wallet.total_deficit_credits,
            BLOCK,
            "the customer was charged twice for one unrecoverable purchase",
        )

    def test_a_late_won_chargeback_restores_only_what_IT_took(self):
        """
        Half refunded, then charged back for the rest, then the chargeback
        is overturned. The chargeback's half comes back; the refunded half
        must not — we really did return that money.
        """
        self.refund(payment_intent="pi_both", refunded=PRICE // 2)
        self.chargeback(payment_intent="pi_both", status="lost")
        self.assertEqual(self.remaining(self.wallet), 0)

        self.chargeback(payment_intent="pi_both", status="won")

        self.assertEqual(
            self.remaining(self.wallet),
            BLOCK // 2,
            "the late win gave back credits the REFUND had reclaimed",
        )

    def test_a_won_chargeback_does_not_clear_a_refund_debt(self):
        """
        Both counters feed one block flag. Clearing it from the dispute
        counter alone would unblock an account that still owes for a
        refund.
        """
        self.wallet.consume_credits(BLOCK, feature="grading", task_id=str(uuid.uuid4()))
        self.refund(payment_intent="pi_both")
        self.wallet.refresh_from_db()
        self.assertTrue(self.wallet.is_consumption_blocked)

        self.chargeback(payment_intent="pi_both", status="lost")
        self.chargeback(payment_intent="pi_both", status="won")

        self.wallet.refresh_from_db()
        self.assertTrue(
            self.wallet.is_consumption_blocked,
            "a won chargeback unblocked an account that still owes for a " "refund",
        )


class SchoolRefundAttributionTests(TestCase, RefundFixture):
    """
    A school purchase spreads one payment across several teachers, so a
    refund of it has to land on all of them — in the shape the purchase
    had, and never on a teacher the payment did not touch.
    """

    def setUp(self):
        self.plan = make_plan()
        self.school = School.objects.create(name="Refund School")
        self.other_school = School.objects.create(name="Untouched School")
        self.teacher_a, self.wallet_a = self.build(
            email="ref.a@school.test", school=self.school
        )
        self.teacher_b, self.wallet_b = self.build(
            email="ref.b@school.test", school=self.school
        )
        self.outsider, self.wallet_out = self.build(
            email="ref.out@other.test", school=self.other_school
        )

    def _shared_payment_grant(self, payment_intent="pi_school"):
        """
        One payment, two teachers — the shape LicenseSubscriptionService
        writes: a grant row per teacher, all carrying the same
        PaymentIntent.
        """
        from billing.services import SubscriptionService

        for wallet in (self.wallet_a, self.wallet_b):
            SubscriptionService.grant_overage_bucket(
                wallet=wallet,
                plan=self.plan,
                quantity=1,
                stripe_payment_intent_id=payment_intent,
            )

    def _uneven_payment_grant(self, payment_intent="pi_uneven"):
        """Two blocks for teacher A, one for teacher B — 2:1, so the
        proportional split does not divide evenly."""
        from billing.services import SubscriptionService

        SubscriptionService.grant_overage_bucket(
            wallet=self.wallet_a,
            plan=self.plan,
            quantity=2,
            stripe_payment_intent_id=payment_intent,
        )
        SubscriptionService.grant_overage_bucket(
            wallet=self.wallet_b,
            plan=self.plan,
            quantity=1,
            stripe_payment_intent_id=payment_intent,
        )

    def test_an_uneven_split_floors_each_share_and_gives_the_remainder_to_the_largest(
        self,
    ):
        """
        The rounding rule, pinned where it is actually visible.

        Every other refund test splits evenly, where floor and ceiling
        agree — so a mutation making the split round UP passed the whole
        suite. A 2:1 grant refunded by a third is the smallest case where
        the two disagree:

            granted 1500, target 500
            floored : A = 500*1000//1500 = 333  (+1 remainder) = 334
                      B = 500* 500//1500 = 166
            ceiling : A = 334, B = 167          <- reclaims 1 more from B

        Flooring is what keeps rounding in the customer's favour; the
        single leftover credit goes to the largest share so the total is
        exact.
        """
        self._uneven_payment_grant()

        # A third of the money back: 500 of the 1500 credits granted.
        self.refund(payment_intent="pi_uneven", captured=PRICE * 3, refunded=PRICE)

        self.assertEqual(
            self.remaining(self.wallet_a),
            2 * BLOCK - 334,
            "teacher A's share was not floored-plus-remainder",
        )
        self.assertEqual(
            self.remaining(self.wallet_b),
            BLOCK - 166,
            "teacher B's share was rounded UP, taking a credit they had "
            "not been refunded for",
        )

    def test_an_uneven_split_still_reclaims_exactly_the_target_in_total(self):
        self._uneven_payment_grant()

        self.refund(payment_intent="pi_uneven", captured=PRICE * 3, refunded=PRICE)

        reclaimed = (3 * BLOCK) - (
            self.remaining(self.wallet_a) + self.remaining(self.wallet_b)
        )
        self.assertEqual(
            reclaimed,
            500,
            "the shares did not add back up to the target — rounding lost "
            "or invented credits",
        )

    def test_a_refund_reclaims_from_every_teacher_the_payment_credited(self):
        self._shared_payment_grant()

        self.refund(payment_intent="pi_school", captured=PRICE * 2, refunded=PRICE * 2)

        self.assertEqual(self.remaining(self.wallet_a), 0)
        self.assertEqual(self.remaining(self.wallet_b), 0)

    def test_a_refund_never_reaches_a_teacher_the_payment_did_not_credit(self):
        self._shared_payment_grant()
        from billing.services import SubscriptionService

        SubscriptionService.grant_overage_bucket(
            wallet=self.wallet_out,
            plan=self.plan,
            quantity=1,
            stripe_payment_intent_id="pi_someone_else",
        )

        self.refund(payment_intent="pi_school", captured=PRICE * 2, refunded=PRICE * 2)

        self.assertEqual(
            self.remaining(self.wallet_out),
            BLOCK,
            "one school's refund reclaimed another school's credits",
        )

    def test_a_partial_refund_splits_proportionally_across_teachers(self):
        self._shared_payment_grant()

        self.refund(payment_intent="pi_school", captured=PRICE * 2, refunded=PRICE)

        self.assertEqual(self.remaining(self.wallet_a), BLOCK // 2)
        self.assertEqual(self.remaining(self.wallet_b), BLOCK // 2)

    def test_the_debt_lands_on_the_teacher_who_actually_spent(self):
        """
        Teacher A spent their share, teacher B did not. Covering A's
        shortfall out of B's balance would take credits from a teacher who
        did nothing wrong.
        """
        self._shared_payment_grant()
        self.wallet_a.consume_credits(
            BLOCK, feature="grading", task_id=str(uuid.uuid4())
        )

        self.refund(payment_intent="pi_school", captured=PRICE * 2, refunded=PRICE * 2)

        self.wallet_a.refresh_from_db()
        self.wallet_b.refresh_from_db()
        self.assertEqual(self.wallet_a.refund_deficit_credits, BLOCK)
        self.assertEqual(
            self.wallet_b.refund_deficit_credits,
            0,
            "an innocent teacher was billed for a colleague's spending",
        )
        self.assertEqual(self.remaining(self.wallet_b), 0)

    def test_a_PARTIAL_refund_does_not_take_one_teachers_spare_to_cover_another(
        self,
    ):
        """
        The mutation that a FULL refund cannot see.

        On a full refund every bucket is claimed to the last credit, so
        there is no spare anywhere and "cover the shortfall from another
        wallet" has nothing to take. Halve the refund and teacher B is left
        holding spare credits that a wallet-blind second pass would happily
        use to settle teacher A's debt.
        """
        self._shared_payment_grant()
        self.wallet_a.consume_credits(
            BLOCK, feature="grading", task_id=str(uuid.uuid4())
        )

        # Half the money back: 500 of the 1000 credits granted should go.
        self.refund(payment_intent="pi_school", captured=PRICE * 2, refunded=PRICE)

        self.wallet_a.refresh_from_db()
        self.wallet_b.refresh_from_db()
        self.assertEqual(
            self.remaining(self.wallet_b),
            BLOCK - (BLOCK // 2),
            "teacher B lost more than their own share — their spare credits "
            "were used to settle teacher A's debt",
        )
        self.assertEqual(
            self.wallet_a.refund_deficit_credits,
            BLOCK // 2,
            "teacher A's debt was silently paid off with a colleague's credits",
        )
        self.assertEqual(self.wallet_b.refund_deficit_credits, 0)

    def test_a_won_chargeback_unblocks_every_teacher_it_blocked(self):
        """
        A school payment can leave SEVERAL teachers in debt, and the money
        row names at most one of them — for a licence payment, none at
        all. Clearing the block from `billing_transaction.user` alone
        would leave the rest permanently unable to spend after a win.
        """
        self._shared_payment_grant(payment_intent="pi_school_cb")
        for wallet in (self.wallet_a, self.wallet_b):
            wallet.consume_credits(BLOCK, feature="grading", task_id=str(uuid.uuid4()))

        self.chargeback(payment_intent="pi_school_cb", status="lost")
        self.wallet_a.refresh_from_db()
        self.wallet_b.refresh_from_db()
        self.assertTrue(self.wallet_a.is_consumption_blocked)
        self.assertTrue(self.wallet_b.is_consumption_blocked)

        self.chargeback(payment_intent="pi_school_cb", status="won")

        self.wallet_a.refresh_from_db()
        self.wallet_b.refresh_from_db()
        for wallet, name in ((self.wallet_a, "A"), (self.wallet_b, "B")):
            self.assertEqual(
                wallet.dispute_deficit_credits,
                0,
                f"teacher {name} still owes for a chargeback that was won",
            )
            self.assertFalse(
                wallet.is_consumption_blocked,
                f"teacher {name} is still locked out after the chargeback was won",
            )

    def test_only_the_spending_teacher_is_blocked(self):
        self._shared_payment_grant()
        self.wallet_a.consume_credits(
            BLOCK, feature="grading", task_id=str(uuid.uuid4())
        )

        self.refund(payment_intent="pi_school", captured=PRICE * 2, refunded=PRICE * 2)

        self.wallet_a.refresh_from_db()
        self.wallet_b.refresh_from_db()
        self.assertTrue(self.wallet_a.is_consumption_blocked)
        self.assertFalse(self.wallet_b.is_consumption_blocked)


class ReversalEngineGuardTests(TestCase, RefundFixture):
    """
    The shared engine's own guards, exercised directly.

    `RefundService` clamps its target before ever calling the engine (see
    `PaymentRefund.target_credits_reversed`), so a refund event cannot
    reach these no matter how absurd its payload. That makes them
    unfalsifiable through the webhook — a mutation removing the engine's
    ceiling passed the whole refund suite — and the engine is shared with
    the chargeback path and open to future callers. Tested at its own
    level rather than left as unverified defence in depth.
    """

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build()
        self.purchase(self.wallet, payment_intent="pi_engine")

    def _reverse(self, target):
        from billing.credit_reversal import reverse_credits_for_payment

        return reverse_credits_for_payment(
            "pi_engine",
            target_cumulative=target,
            ledger_type=CreditLedgerType.REFUND_REVERSAL,
            reference="engine guard test",
        )

    def test_a_target_beyond_what_was_granted_is_clamped(self):
        outcome = self._reverse(BLOCK * 10)

        self.assertEqual(outcome.reclaimed, BLOCK)
        self.assertEqual(
            outcome.deficit,
            0,
            "an inflated target invented a debt out of credits that were "
            "never granted",
        )

    def test_an_inflated_target_cannot_manufacture_a_debt_on_a_spent_purchase(
        self,
    ):
        self.wallet.consume_credits(BLOCK, feature="grading", task_id=str(uuid.uuid4()))

        outcome = self._reverse(BLOCK * 10)

        self.assertEqual(outcome.deficit, BLOCK)

    def test_a_negative_target_reverses_nothing(self):
        outcome = self._reverse(-500)

        self.assertEqual(outcome.reclaimed, 0)
        self.assertEqual(outcome.deficit, 0)
        self.assertEqual(self.remaining(self.wallet), BLOCK)

    def test_an_unknown_payment_reverses_nothing(self):
        from billing.credit_reversal import reverse_credits_for_payment

        outcome = reverse_credits_for_payment(
            "pi_never_seen",
            target_cumulative=BLOCK,
            ledger_type=CreditLedgerType.REFUND_REVERSAL,
            reference="engine guard test",
        )

        self.assertEqual(outcome.granted, 0)
        self.assertEqual(outcome.reclaimed, 0)
        self.assertEqual(self.remaining(self.wallet), BLOCK)

    def test_a_second_call_at_the_same_target_is_inert(self):
        self._reverse(BLOCK)
        second = self._reverse(BLOCK)

        self.assertEqual(second.reclaimed, 0)
        self.assertEqual(self.reversed_total(self.wallet), BLOCK)


class ConcurrentRefundTests(TransactionTestCase, RefundFixture):
    """
    REAL threads against Postgres.

    `TransactionTestCase` is required: the standard TestCase wraps each
    test in a transaction the worker threads cannot see, so the row locks
    would never contend and the race would not be reproduced.
    """

    reset_sequences = True

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build(email="concurrent.refund@billing.test")

    def _run(self, fn, count):
        barrier = threading.Barrier(count)
        errors = []

        def worker(i):
            try:
                barrier.wait(timeout=30)
                fn(i)
            except Exception as exc:  # noqa: BLE001 - asserted on below
                errors.append(exc)
            finally:
                connections.close_all()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        return errors

    def test_simultaneous_duplicate_refunds_reverse_exactly_once(self):
        """
        Both threads read the payment's settled total before either
        writes. Only the row lock stops them both reclaiming.
        """
        self.purchase(self.wallet)

        errors = self._run(lambda i: self.refund(), 4)

        self.assertEqual(errors, [])
        self.assertEqual(self.reversed_total(self.wallet), BLOCK)
        self.assertEqual(self.remaining(self.wallet), 0)

    def test_a_refund_racing_a_consumption_never_oversells(self):
        """
        The customer spends while the refund reclaims. Whichever wins, the
        credits must be accounted for exactly once: what was consumed plus
        what was reclaimed plus what remains equals what was granted.
        """
        self.purchase(self.wallet)

        def act(i):
            if i % 2 == 0:
                self.refund()
            else:
                try:
                    self.wallet.consume_credits(
                        100, feature="grading", task_id=str(uuid.uuid4())
                    )
                except InsufficientCreditsError:
                    pass

        errors = self._run(act, 6)

        self.assertEqual(errors, [], "the refund deadlocked against a consumption")
        self.wallet.refresh_from_db()
        bucket = CreditBucket.objects.get(
            wallet=self.wallet, bucket_type=CreditBucketType.OVERAGE
        )
        self.assertLessEqual(
            bucket.used_credits,
            bucket.total_credits,
            "the bucket was drawn past its balance",
        )

        # The accounting identity, whichever order the threads landed in:
        # every credit the payment granted is either still there, spent by
        # the customer, reclaimed by the refund, or owed as a debt.
        consumed = -sum(
            row.amount
            for row in CreditLedger.objects.filter(
                bucket__wallet=self.wallet, ledger_type=CreditLedgerType.CONSUME
            )
        )
        reclaimed = self.reversed_total(self.wallet)
        row = PaymentRefund.objects.get(stripe_payment_intent_id="pi_ref_1")
        self.assertEqual(
            self.remaining(self.wallet) + consumed + reclaimed,
            BLOCK,
            "credits were created or destroyed by the race",
        )
        self.assertEqual(
            reclaimed + row.credits_deficit,
            BLOCK,
            "the payment was not fully accounted for after the refund",
        )

    def test_a_refund_racing_a_chargeback_reverses_the_payment_once(self):
        self.purchase(self.wallet, payment_intent="pi_race")

        def act(i):
            if i % 2 == 0:
                self.refund(payment_intent="pi_race")
            else:
                self.chargeback(payment_intent="pi_race", dispute_id=f"dp_{i}")

        errors = self._run(act, 4)

        self.assertEqual(errors, [])
        self.assertEqual(
            self.reversed_total(self.wallet),
            BLOCK,
            "a refund and a chargeback arriving together reversed the same "
            "credits twice",
        )
