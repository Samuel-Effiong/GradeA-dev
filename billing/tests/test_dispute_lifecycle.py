"""
billing/tests/test_dispute_lifecycle.py
=======================================
Chargebacks: recording, entitlement, and safety against duplicate,
delayed and out-of-order Stripe events.

Before this, nothing in billing reacted to a dispute at all. The money and
the dispute fee left the Stripe balance, the local BillingTransaction still
read PAID, the customer kept the credits the payment bought, and the
evidence deadline passed unanswered — which loses by default.

FIXTURES ARE BUILT FROM A REAL DISPUTE
--------------------------------------
Driving one through Stripe's test API on 2026-09-08 produced exactly:

    charge.dispute.created          status=needs_response   -2499, fee 1500
    charge.dispute.funds_withdrawn  status=needs_response
    charge.dispute.updated          status=under_review
    charge.dispute.closed           status=won
    charge.dispute.funds_reinstated status=won              +2499, fee 0

so the payloads below carry `balance_transactions` with a separate `fee`,
both `charge` and `payment_intent`, and `evidence_details.due_by` — the
shape the handler actually meets.

THE RULES BEING PINNED
----------------------
  opened  -> record, mark DISPUTED, revoke NOTHING (most disputes resolve;
             cutting off a paying customer on an accusation is worse than
             carrying the risk)
  won     -> restore normal state
  lost    -> reflect the loss and reverse the credits that payment bought
  spent   -> never delete usage history; record a deficit and block further
             consumption
  inquiry -> `warning_*` moves no money and must not touch entitlement
"""

import time
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.disputes import DisputeService
from billing.models import (
    BillingInterval,
    BillingTransaction,
    BillingTransactionSource,
    BillingTransactionStatus,
    BillingTransactionType,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    DisputeStatus,
    PaymentDispute,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
    UserSubscription,
)
from billing.stripe_service import StripeWebhookHandler
from users.models import UserTypes

CustomUser = get_user_model()

DISPUTE_ID = "du_1UDKB1Aq8voEV6EaEbwNIxnl"
CHARGE_ID = "ch_3UDKB0Aq8voEV6Ea0210ALFJ"
PI_ID = "pi_3UDKB0Aq8voEV6Ea0jCSFFO3"
AMOUNT = 2499
FEE = 1500
MONTHLY_CREDITS = 10_000


def dispute(status, *, reinstated=False, amount=AMOUNT, dispute_id=DISPUTE_ID):
    """A Dispute object shaped like the live one."""
    bts = [
        {"amount": -amount, "fee": FEE, "net": -(amount + FEE), "type": "adjustment"}
    ]
    if reinstated:
        bts.append({"amount": amount, "fee": 0, "net": amount, "type": "adjustment"})
    return {
        "id": dispute_id,
        "object": "dispute",
        "status": status,
        "amount": amount,
        "currency": "usd",
        "reason": "fraudulent",
        "charge": CHARGE_ID,
        "payment_intent": PI_ID,
        "is_charge_refundable": False,
        "created": int(time.time()),
        "balance_transactions": bts,
        "evidence_details": {
            "due_by": int(time.time()) + 14 * 86400,
            "has_evidence": False,
            "submission_count": 0,
        },
    }


class DisputeLifecycleTests(TestCase):
    def setUp(self):
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Pro",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.PRO,
            interval=BillingInterval.MONTHLY,
            monthly_credits=MONTHLY_CREDITS,
            overage_block_size=500,
            is_active=True,
        )
        self.user = CustomUser.objects.create_user(
            email="disputed@billing.test",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        CreditBucket.objects.filter(wallet=self.wallet).delete()
        now = timezone.now()
        self.sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - timedelta(days=2),
            billing_cycle_end=now + timedelta(days=28),
            next_credit_grant_at=now + timedelta(days=28),
        )
        self.bucket = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=MONTHLY_CREDITS,
            used_credits=0,
            expires_at=now + timedelta(days=28),
        )
        self.txn = BillingTransaction.objects.create(
            source=BillingTransactionSource.INDIVIDUAL,
            transaction_type=BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE,
            status=BillingTransactionStatus.PAID,
            billing_method="STRIPE",
            user=self.user,
            user_subscription=self.sub,
            amount_cents=AMOUNT,
            currency="usd",
            stripe_charge_id=CHARGE_ID,
            stripe_payment_intent_id=PI_ID,
            occurred_at=now,
        )

    def _apply(self, status, **kw):
        return DisputeService.apply(dispute(status, **kw))

    def _wallet(self):
        return CreditWallet.objects.get(pk=self.wallet.pk)

    def _txn(self):
        return BillingTransaction.objects.get(pk=self.txn.pk)

    # --- opened -----------------------------------------------------------

    def test_a_new_chargeback_is_recorded_against_the_payment(self):
        row = self._apply(DisputeStatus.NEEDS_RESPONSE)

        self.assertEqual(row.stripe_dispute_id, DISPUTE_ID)
        self.assertEqual(row.billing_transaction_id, self.txn.id)
        self.assertEqual(row.user_id, self.user.id)
        self.assertEqual(row.amount_cents, AMOUNT)
        self.assertEqual(row.reason, "fraudulent")

    def test_the_dispute_fee_is_recorded_separately_from_the_amount(self):
        """Winning still costs the fee, so the amount alone understates it."""
        row = self._apply(DisputeStatus.NEEDS_RESPONSE)

        self.assertEqual(row.fee_cents, FEE)
        self.assertEqual(row.total_loss_cents, AMOUNT + FEE)

    def test_the_evidence_deadline_is_captured(self):
        """Miss it and the dispute is lost by default."""
        row = self._apply(DisputeStatus.NEEDS_RESPONSE)
        self.assertIsNotNone(row.evidence_due_by)

    def test_opening_marks_the_transaction_disputed(self):
        self._apply(DisputeStatus.NEEDS_RESPONSE)
        self.assertEqual(self._txn().status, BillingTransactionStatus.DISPUTED)

    def test_opening_does_NOT_revoke_credits(self):
        """The rule: an accusation is not a verdict."""
        self._apply(DisputeStatus.NEEDS_RESPONSE)

        self.bucket.refresh_from_db()
        self.assertEqual(self.bucket.used_credits, 0)
        self.assertFalse(self._wallet().is_consumption_blocked)

    # --- won --------------------------------------------------------------

    def test_a_won_dispute_restores_the_transaction(self):
        self._apply(DisputeStatus.NEEDS_RESPONSE)
        self._apply(DisputeStatus.WON, reinstated=True)

        self.assertEqual(self._txn().status, BillingTransactionStatus.PAID)

    def test_a_won_dispute_leaves_credits_alone(self):
        self._apply(DisputeStatus.NEEDS_RESPONSE)
        self._apply(DisputeStatus.WON, reinstated=True)

        self.bucket.refresh_from_db()
        self.assertEqual(self.bucket.used_credits, 0)
        self.assertFalse(self._wallet().is_consumption_blocked)

    # --- lost -------------------------------------------------------------

    def test_a_lost_dispute_marks_the_transaction_lost(self):
        self._apply(DisputeStatus.NEEDS_RESPONSE)
        self._apply(DisputeStatus.LOST)

        self.assertEqual(self._txn().status, BillingTransactionStatus.DISPUTE_LOST)

    def test_a_lost_dispute_reverses_the_credits_it_bought(self):
        self._apply(DisputeStatus.NEEDS_RESPONSE)
        row = self._apply(DisputeStatus.LOST)

        self.bucket.refresh_from_db()
        self.assertEqual(self.bucket.used_credits, MONTHLY_CREDITS)
        self.assertEqual(row.credits_reversed_amount, MONTHLY_CREDITS)
        self.assertEqual(row.credits_deficit_amount, 0)

    def test_the_reversal_is_appended_to_the_ledger_not_hidden(self):
        self._apply(DisputeStatus.NEEDS_RESPONSE)
        self._apply(DisputeStatus.LOST)

        rows = CreditLedger.objects.filter(
            ledger_type=CreditLedgerType.DISPUTE_REVERSAL
        )
        self.assertEqual(rows.count(), 1)
        entry = rows.first()
        self.assertEqual(entry.amount, -MONTHLY_CREDITS)
        self.assertIn(DISPUTE_ID, entry.reference)

    def test_credits_already_spent_become_a_deficit_not_deleted_history(self):
        """
        The rule: never silently delete usage. The work happened and cost
        us; record the shortfall as a debt.
        """
        self.bucket.used_credits = 6_000  # already spent
        self.bucket.save(update_fields=["used_credits"])
        usage_before = CreditLedger.objects.count()

        self._apply(DisputeStatus.NEEDS_RESPONSE)
        row = self._apply(DisputeStatus.LOST)

        self.bucket.refresh_from_db()
        self.assertEqual(self.bucket.used_credits, MONTHLY_CREDITS)  # clamped
        self.assertEqual(row.credits_reversed_amount, 4_000)  # what was left
        self.assertEqual(row.credits_deficit_amount, 6_000)  # what was spent
        self.assertGreaterEqual(
            CreditLedger.objects.count(), usage_before, "history was removed"
        )

    def test_a_deficit_blocks_further_consumption(self):
        self.bucket.used_credits = 6_000
        self.bucket.save(update_fields=["used_credits"])

        self._apply(DisputeStatus.NEEDS_RESPONSE)
        self._apply(DisputeStatus.LOST)

        wallet = self._wallet()
        self.assertTrue(wallet.is_consumption_blocked)
        self.assertEqual(wallet.dispute_deficit_credits, 6_000)

    def test_the_blocked_wallet_actually_refuses_to_spend(self):
        from billing.models import InsufficientCreditsError

        self.bucket.used_credits = 6_000
        self.bucket.save(update_fields=["used_credits"])
        self._apply(DisputeStatus.NEEDS_RESPONSE)
        self._apply(DisputeStatus.LOST)

        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MANUAL_GRANT,
            total_credits=5_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=10),
        )
        with self.assertRaises(InsufficientCreditsError):
            self._wallet().consume_credits(
                10, feature="Grading Assignment", task_type="GRADING", task_id="t1"
            )

    # --- idempotency and ordering -----------------------------------------

    def test_a_duplicate_lost_does_not_reverse_twice(self):
        """
        A SECOND bucket on purpose. With only the one bucket the first
        reversal exhausts it, so a second reversal finds nothing to take
        and the test passes even when the idempotency guard is removed —
        it would prove nothing. With spare credits available, a double
        reversal takes twice as much and is visible.
        """
        spare = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MANUAL_GRANT,
            total_credits=MONTHLY_CREDITS,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=28),
        )

        self._apply(DisputeStatus.NEEDS_RESPONSE)
        self._apply(DisputeStatus.LOST)
        self._apply(DisputeStatus.LOST)
        self._apply(DisputeStatus.LOST)

        self.bucket.refresh_from_db()
        spare.refresh_from_db()
        total_taken = self.bucket.used_credits + spare.used_credits
        self.assertEqual(
            total_taken,
            MONTHLY_CREDITS,
            f"the reversal ran more than once: {total_taken} taken for a "
            f"{MONTHLY_CREDITS}-credit payment",
        )
        self.assertEqual(
            CreditLedger.objects.filter(
                ledger_type=CreditLedgerType.DISPUTE_REVERSAL
            ).count(),
            1,
        )

    def test_only_one_row_per_dispute_however_many_events_arrive(self):
        for status in (
            DisputeStatus.NEEDS_RESPONSE,
            DisputeStatus.NEEDS_RESPONSE,
            DisputeStatus.UNDER_REVIEW,
            DisputeStatus.LOST,
        ):
            self._apply(status)

        self.assertEqual(PaymentDispute.objects.count(), 1)

    def test_a_late_created_event_cannot_reopen_a_settled_dispute(self):
        """
        Stripe does not guarantee ordering. A delayed `created` arriving
        after `closed` must not drag the dispute back to needs_response.
        """
        self._apply(DisputeStatus.NEEDS_RESPONSE)
        self._apply(DisputeStatus.WON, reinstated=True)

        self._apply(DisputeStatus.NEEDS_RESPONSE)  # the straggler

        row = PaymentDispute.objects.get()
        self.assertEqual(row.status, DisputeStatus.WON)
        self.assertEqual(self._txn().status, BillingTransactionStatus.PAID)

    def test_an_out_of_order_lost_after_won_is_ignored(self):
        self._apply(DisputeStatus.NEEDS_RESPONSE)
        self._apply(DisputeStatus.WON, reinstated=True)
        self._apply(DisputeStatus.LOST)  # stale

        self.bucket.refresh_from_db()
        self.assertEqual(self.bucket.used_credits, 0, "a stale LOST reversed credits")
        self.assertEqual(self._txn().status, BillingTransactionStatus.PAID)

    def test_a_late_win_after_a_loss_IS_applied(self):
        """
        Stripe documents this one: an issuer can overturn a loss after the
        fact. It is the single permitted backwards step, and the customer
        must get their credits back.
        """
        self._apply(DisputeStatus.NEEDS_RESPONSE)
        self._apply(DisputeStatus.LOST)
        self.bucket.refresh_from_db()
        self.assertEqual(self.bucket.used_credits, MONTHLY_CREDITS)

        self._apply(DisputeStatus.WON, reinstated=True)

        row = PaymentDispute.objects.get()
        self.assertEqual(row.status, DisputeStatus.WON)
        self.bucket.refresh_from_db()
        self.assertEqual(self.bucket.used_credits, 0, "a late win did not restore")
        self.assertEqual(self._txn().status, BillingTransactionStatus.PAID)
        self.assertFalse(self._wallet().is_consumption_blocked)

    # --- inquiries --------------------------------------------------------

    def test_an_inquiry_is_recorded_but_moves_nothing(self):
        """
        `warning_*` withdraws no funds — entitlement must be untouched.

        NOTE ON WHAT PROTECTS THIS: the `is_inquiry` early return in
        DisputeService.apply is belt-and-braces, not the load-bearing
        guard. Removing it changes nothing, because the status dispatch
        below it only matches the four chargeback statuses and no
        `warning_*` value. Verified by mutation. The guard is kept because
        it states the intent where a reader will look for it, and it would
        become load-bearing the moment someone adds a catch-all branch.
        """
        row = self._apply(DisputeStatus.WARNING_NEEDS_RESPONSE)

        self.assertTrue(row.is_inquiry)
        self.bucket.refresh_from_db()
        self.assertEqual(self.bucket.used_credits, 0)
        self.assertEqual(self._txn().status, BillingTransactionStatus.PAID)

    def test_a_closed_inquiry_does_not_touch_entitlement_either(self):
        self._apply(DisputeStatus.WARNING_NEEDS_RESPONSE)
        self._apply(DisputeStatus.WARNING_CLOSED)

        self.assertEqual(self._txn().status, BillingTransactionStatus.PAID)
        self.bucket.refresh_from_db()
        self.assertEqual(self.bucket.used_credits, 0)

    # --- unmatched --------------------------------------------------------

    def test_a_dispute_for_an_unknown_payment_is_still_recorded(self):
        """
        Money has already left the balance. A dispute we cannot match must
        surface for a human, never be dropped.
        """
        payload = dispute(DisputeStatus.NEEDS_RESPONSE, dispute_id="du_orphan")
        payload["charge"] = "ch_unknown"
        payload["payment_intent"] = "pi_unknown"

        with self.assertLogs("billing.disputes", level="ERROR") as logs:
            row = DisputeService.apply(payload)

        self.assertIsNotNone(row)
        self.assertIsNone(row.billing_transaction_id)
        self.assertTrue(
            any("NO MATCHING PAYMENT" in line for line in logs.output), logs.output
        )

    # --- multiple disputes per payment ------------------------------------

    def test_two_disputes_on_one_payment_are_tracked_separately(self):
        """Stripe documents this as rare but real."""
        self._apply(DisputeStatus.NEEDS_RESPONSE)
        DisputeService.apply(
            dispute(DisputeStatus.NEEDS_RESPONSE, dispute_id="du_second")
        )

        self.assertEqual(PaymentDispute.objects.count(), 2)


class DisputeWebhookRoutingTests(TestCase):
    """All five dispute event types must reach the handler."""

    def test_every_dispute_event_is_routed(self):
        from billing.webhooks import _EVENT_HANDLERS

        for event_type in (
            "charge.dispute.created",
            "charge.dispute.updated",
            "charge.dispute.closed",
            "charge.dispute.funds_withdrawn",
            "charge.dispute.funds_reinstated",
        ):
            self.assertIn(event_type, _EVENT_HANDLERS)
            self.assertIs(
                _EVENT_HANDLERS[event_type],
                StripeWebhookHandler.handle_dispute_event,
            )

    def test_the_handler_records_a_dispute_end_to_end(self):
        StripeWebhookHandler.handle_dispute_event(
            dispute(DisputeStatus.NEEDS_RESPONSE, dispute_id="du_routed")
        )
        self.assertTrue(
            PaymentDispute.objects.filter(stripe_dispute_id="du_routed").exists()
        )
