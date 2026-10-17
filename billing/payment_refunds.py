"""
billing/payment_refunds.py
==========================
What happens to purchased credits when the money goes back.

THE DEFECT THIS CLOSES
----------------------
`charge.refunded` recorded the refund against the BillingTransaction and
stopped. Measured on a real handler run (2026-09-09):

    after purchase       credits 500, blocks used 1, txn PAID
    after FULL refund    credits 500, blocks used 1, txn REFUNDED

The customer got every cent back and kept every credit. Nothing about
that was a decision — no code had ever considered the question. Repeated,
it is a way to obtain unlimited AI credits for free: buy, use, refund,
repeat.

THE INVARIANT
-------------
    Financial records and customer entitlement must never silently
    diverge from the final payment state.

"Silently" is the operative word. There is a legitimate business case for
refunding money AND letting the customer keep what they bought — a
goodwill gesture, a support write-off. What there is no case for is that
happening by accident, unrecorded. So the default is to claw back, and
keeping the credits is available but must be asked for explicitly (see
RETAINING CREDITS DELIBERATELY below), whereupon it is recorded as a
decision on the PaymentRefund row.

THE RULES
---------
1.  FULL REFUND — reclaim every credit that payment bought.

2.  PARTIAL REFUND — reclaim the same proportion, floored:
        target = granted * amount_refunded // amount_captured
    Floored so rounding always favours the customer. A 50% refund of a
    501-credit grant reclaims 250.

3.  REFUND BEFORE CONSUMPTION — the credits are still in the bucket the
    payment created; they are taken back from that bucket and the wallet
    is left unblocked. Nothing is owed.

4.  REFUND AFTER PARTIAL CONSUMPTION — reclaim what is left, record the
    remainder as a debt. Usage history is NEVER rewritten: the work
    really happened and really cost us, and deleting the usage rows would
    make the ledger lie about it.

5.  REFUND AFTER FULL CONSUMPTION — nothing to reclaim; the entire amount
    becomes a debt and consumption is blocked until a human settles it.

6.  MULTIPLE REFUNDS — Stripe's `amount_refunded` is CUMULATIVE over the
    charge, so each delivery states a total, not an increment. Rule 2
    computes a cumulative target and only the shortfall is applied, so
    three 20% refunds land at exactly 60% reclaimed, not 20% three times.

7.  DUPLICATE AND OUT-OF-ORDER EVENTS — both stored amounts are
    monotonic: a delivery carrying a SMALLER cumulative refund than one
    already seen is a stale redelivery and is ignored for reversal
    purposes. A duplicate computes a shortfall of zero and does nothing.
    Reversal is never undone by a late-arriving older event.

8.  REFUND THEN A NEW PAYMENT — a new purchase is a new PaymentIntent
    with its own grant rows and its own PaymentRefund row; the refunded
    one cannot suppress it. A new purchase does NOT clear an outstanding
    debt: the debt is money owed for work already delivered, and paying
    for new credits is not paying that off. Settling is a human decision.

9.  PROCESSING FAILURE AND RETRY — the whole application runs in one
    transaction inside the webhook task, so a failure rolls back to
    before it and Celery retries. Because every quantity is a cumulative
    TARGET rather than a delta, a retry after a partial failure converges
    on the same state rather than compounding.

WHICH PAYMENTS THIS APPLIES TO
------------------------------
Only payments that can be shown to have granted credits — those with
grant rows carrying their PaymentIntent (see
credit_reversal.grant_rows_for_payment). In practice that is the three
overage purchase flows.

Refunds of SUBSCRIPTION invoices deliberately fall through to money-only
recording, unchanged. A subscription refund is entangled with the plan
change, proration and renewal machinery, and reclaiming a cycle's
allocation on a partial refund would be guesswork; the honest thing is to
leave it visible in the money ledger for a human rather than to invent a
rule here. Tracked as a separate piece of work.

RETAINING CREDITS DELIBERATELY
------------------------------
Set `retain_credits` to a truthy value in the Stripe metadata of either
the refund or the charge:

    stripe.Refund.create(charge="ch_...", metadata={"retain_credits": "true"})

The refund is then recorded, the decision is stamped on the PaymentRefund
row with a note, and entitlement is left alone. This is the supported way
to absorb the loss on purpose.

INTERACTION WITH CHARGEBACKS
----------------------------
A payment can be partly refunded and then disputed for the rest, so both
this module and billing/disputes.py can act on one payment. Neither owns
the accounting: both go through credit_reversal, which derives what has
already been settled from the append-only ledger plus the recorded debts
of BOTH causes. The second cause to arrive therefore sees the first one's
work and reclaims only the difference. A payment can never be reversed
past what it granted.
"""

import logging

from django.db import transaction

from .billing_transaction_service import BillingTransactionService
from .credit_reversal import (
    blocks_granted_by_payment,
    credits_granted_by_payment,
    reverse_credits_for_payment,
)
from .models import BillingTransaction, CreditLedgerType, CreditWallet, PaymentRefund

logger = logging.getLogger(__name__)

#: Stripe metadata key an operator sets to refund the money but leave the
#: credits in place. Accepted on the Refund or on the Charge.
RETAIN_CREDITS_METADATA_KEY = "retain_credits"

_TRUTHY = frozenset({"1", "true", "yes", "y", "on", "retain"})


def _is_truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUTHY


def _retention_requested(charge) -> bool:
    """
    Did a human deliberately ask for this refund to keep the credits?

    Checked on each Refund object first — that is where the decision is
    actually made, per refund — then on the Charge, which is the more
    convenient place to set it for a full refund from the dashboard.
    """
    refunds = (charge.get("refunds") or {}).get("data") or []
    for refund in refunds:
        meta = refund.get("metadata") or {}
        if _is_truthy(meta.get(RETAIN_CREDITS_METADATA_KEY)):
            return True
    charge_meta = charge.get("metadata") or {}
    return _is_truthy(charge_meta.get(RETAIN_CREDITS_METADATA_KEY))


#: Stamped by this system on a refund IT issued as a compensating
#: control, rather than one a human or a customer asked for. The value
#: names which control did it, so the log line can say so.
SYSTEM_REFUND_METADATA_KEY = "system_refund_reason"

#: Compensating control: a duplicate interval-change invoice Stripe raised
#: for a subscription the customer had already paid for through a separate
#: Checkout session. See StripeSubscriptionMutationService.
SYSTEM_REFUND_INTERVAL_CHANGE_DUPLICATE = "interval_change_duplicate_invoice"


def system_refund_reason(charge):
    """
    Which of our own compensating controls issued this refund, if any.

    Returns None for an ordinary refund — one a human issued from the
    dashboard, or a customer was granted.

    WHY THIS EXISTS
    ---------------
    `BillingTransactionService.handle_refund` flags any refund with no
    matching BillingTransaction as needing manual reconciliation, which is
    right for money moving that we cannot explain. But this system issues
    refunds of its own for invoices it deliberately never recorded as
    customer charges, so every one of those raised a false alarm — and an
    alarm that cries wolf on its own housekeeping is one nobody reads when
    a real unexplained refund appears.
    """
    refunds = (charge.get("refunds") or {}).get("data") or []
    for refund in refunds:
        reason = (refund.get("metadata") or {}).get(SYSTEM_REFUND_METADATA_KEY)
        if reason:
            return reason
    return (charge.get("metadata") or {}).get(SYSTEM_REFUND_METADATA_KEY) or None


def _resolve_payment_intent_id(charge):
    payment_intent = charge.get("payment_intent")
    if isinstance(payment_intent, dict):
        return payment_intent.get("id")
    return payment_intent


class RefundService:
    """Applies a Stripe `charge.refunded` event to money AND entitlement."""

    @staticmethod
    @transaction.atomic
    def apply(charge):
        """
        Record the refund and bring entitlement back in line with it.

        Safe to call repeatedly with the same charge, and safe to call
        with events that arrive out of order. Returns the PaymentRefund
        row, or None when the refund touched no credits.
        """
        # The money side is unchanged and runs first, so a refund is
        # recorded even if everything below decides there is nothing to
        # reclaim.
        BillingTransactionService.handle_refund(charge)

        payment_intent_id = _resolve_payment_intent_id(charge)
        if not payment_intent_id:
            logger.info(
                "charge.refunded for %s carries no payment_intent — money "
                "recorded, no credits to attribute.",
                charge.get("id"),
            )
            return None

        granted = credits_granted_by_payment(payment_intent_id)
        if granted <= 0:
            # A subscription invoice refund, or a payment that never
            # granted credits (e.g. one refused by the block cap). Money
            # recorded; nothing to reverse. See WHICH PAYMENTS THIS
            # APPLIES TO.
            logger.info(
                "Refund of payment %s granted no attributable credits — "
                "money recorded, entitlement untouched.",
                payment_intent_id,
            )
            return None

        return RefundService._apply_to_credits(charge, payment_intent_id, granted)

    @staticmethod
    def _apply_to_credits(charge, payment_intent_id, granted):
        amount_refunded = int(charge.get("amount_refunded") or 0)
        amount_captured = int(
            charge.get("amount_captured") or charge.get("amount") or 0
        )

        row, created = PaymentRefund.objects.select_for_update().get_or_create(
            stripe_payment_intent_id=payment_intent_id,
            defaults={
                "stripe_charge_id": charge.get("id") or "",
                "amount_captured_cents": max(0, amount_captured),
                "amount_refunded_cents": max(0, amount_refunded),
                "currency": charge.get("currency") or "usd",
                "credits_granted": granted,
            },
        )

        charge_id = charge.get("id") or ""
        if not created and charge_id and row.stripe_charge_id != charge_id:
            # Documented assumption broken — see PaymentRefund. Recorded
            # loudly rather than folded into the totals, because the
            # proportion below would be computed against the wrong base.
            logger.error(
                "Payment %s has refunds on more than one charge (%s and %s). "
                "Only the first is used for the reversal proportion; this "
                "needs manual review.",
                payment_intent_id,
                row.stripe_charge_id,
                charge_id,
            )

        # MONOTONIC. A delivery quoting a smaller cumulative refund than
        # one already applied is a stale redelivery — Stripe does not
        # guarantee ordering — and must not walk the reversal backwards.
        stale = amount_refunded < row.amount_refunded_cents
        if stale:
            logger.info(
                "Refund event for payment %s quotes %d cents refunded but "
                "%d is already recorded — stale redelivery, ignoring.",
                payment_intent_id,
                amount_refunded,
                row.amount_refunded_cents,
            )
        row.amount_refunded_cents = max(row.amount_refunded_cents, amount_refunded)
        row.amount_captured_cents = max(row.amount_captured_cents, amount_captured)
        # Grant rows can still be arriving on a retry; take the larger view.
        row.credits_granted = max(row.credits_granted, granted)
        if not row.billing_transaction_id:
            row.billing_transaction = BillingTransaction.objects.filter(
                stripe_payment_intent_id=payment_intent_id
            ).first()

        if _retention_requested(charge):
            row.retain_credits = True
            row.notes = (
                "Refund issued with retain_credits set — the loss is "
                "absorbed deliberately and the customer keeps the credits."
            )
            row.save()
            logger.warning(
                "Refund of payment %s carries retain_credits: %d cents "
                "returned and %d credit(s) deliberately LEFT with the "
                "customer.",
                payment_intent_id,
                row.amount_refunded_cents,
                row.credits_granted,
            )
            return row

        outcome = reverse_credits_for_payment(
            payment_intent_id,
            target_cumulative=row.target_credits_reversed,
            ledger_type=CreditLedgerType.REFUND_REVERSAL,
            reference=f"Refund of {charge.get('id') or payment_intent_id}",
            metadata={
                "stripe_charge_id": charge.get("id"),
                "amount_refunded_cents": row.amount_refunded_cents,
                "amount_captured_cents": row.amount_captured_cents,
            },
        )

        row.credits_reversed += outcome.reclaimed
        row.credits_deficit += outcome.deficit
        row.save()

        RefundService._record_deficits(outcome.deficit_by_wallet, payment_intent_id)
        RefundService._restore_block_allowance(row, payment_intent_id)

        if outcome.reclaimed or outcome.deficit:
            logger.warning(
                "Refund of payment %s (%d/%d cents): reclaimed %d credit(s), "
                "%d could not be reclaimed and are now a debt.",
                payment_intent_id,
                row.amount_refunded_cents,
                row.amount_captured_cents,
                outcome.reclaimed,
                outcome.deficit,
            )
        return row

    @staticmethod
    def _record_deficits(deficit_by_wallet, payment_intent_id):
        """
        Book the unreclaimable portion as debt on the wallet that spent
        it, and stop that wallet spending until a human settles up.
        """
        for wallet_id, amount in sorted(
            deficit_by_wallet.items(), key=lambda kv: str(kv[0])
        ):
            if amount <= 0:
                continue
            wallet = (
                CreditWallet.objects.select_for_update().filter(id=wallet_id).first()
            )
            if wallet is None:
                logger.error(
                    "Refund of payment %s left a %d credit debt against "
                    "wallet %s, which no longer exists.",
                    payment_intent_id,
                    amount,
                    wallet_id,
                )
                continue
            wallet.refund_deficit_credits += amount
            wallet.save(update_fields=["refund_deficit_credits", "updated_at"])
            wallet.sync_consumption_block()
            logger.error(
                "Refund of payment %s: %d credit(s) had already been spent "
                "and cannot be reclaimed. Recorded as a debt and "
                "consumption is now BLOCKED for wallet %s until settled.",
                payment_intent_id,
                amount,
                wallet.id,
            )

    @staticmethod
    def _restore_block_allowance(row, payment_intent_id):
        """
        Give back the overage block allowance a fully refunded purchase
        consumed, so the cap does not punish a customer for a purchase
        that was undone.

        Only on a FULL refund, and only once per wallet. A partially
        refunded block is still a block the customer partly holds, and
        handing back a fraction of a block is not a thing the cap can
        represent — so partial refunds deliberately leave the counter
        alone. The counter resets at renewal in any case, so the worst a
        partial refund costs is one block for the rest of the cycle.
        """
        if not row.is_fully_refunded:
            return

        blocks_by_wallet = blocks_granted_by_payment(payment_intent_id)
        already = dict(row.blocks_restored_by_wallet or {})
        changed = False

        for wallet_id, blocks in sorted(blocks_by_wallet.items()):
            if already.get(wallet_id) or blocks <= 0:
                continue
            wallet = (
                CreditWallet.objects.select_for_update().filter(id=wallet_id).first()
            )
            if wallet is None:
                continue
            # Clamped: the counter is reset to zero at every renewal, so a
            # refund arriving after the cycle turned over must not push it
            # negative.
            wallet.overage_blocks_used = max(0, wallet.overage_blocks_used - blocks)
            wallet.save(update_fields=["overage_blocks_used", "updated_at"])
            already[wallet_id] = blocks
            changed = True
            logger.info(
                "Refund of payment %s returned %d overage block(s) of "
                "allowance to wallet %s.",
                payment_intent_id,
                blocks,
                wallet_id,
            )

        if changed:
            row.blocks_restored_by_wallet = already
            row.save(update_fields=["blocks_restored_by_wallet", "updated_at"])
