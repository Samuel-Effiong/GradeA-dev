"""
billing/disputes.py
===================
The chargeback lifecycle.

WHY THIS EXISTS
---------------
Nothing in billing reacted to disputes. A chargeback removed the money and
the dispute fee from the Stripe balance while the local BillingTransaction
still read PAID and the customer kept the credits that payment bought. The
evidence deadline passed unanswered, which loses by default.

THE LIFECYCLE, VERIFIED AGAINST REAL STRIPE (test mode, 2026-09-08)
-------------------------------------------------------------------
Driving one real dispute end to end produced, in this order:

    charge.dispute.created          status=needs_response   -2499, fee 1500
    charge.dispute.funds_withdrawn  status=needs_response
    charge.dispute.updated          status=under_review
    charge.dispute.closed           status=won
    charge.dispute.funds_reinstated status=won              +2499, fee 0

Three facts from that run shape everything below:

1. **The money leaves at CREATION, not at closure.** By the time we hear
   about a dispute the balance has already been debited.
2. **The fee is separate and is not returned on a win.** Winning still
   costs the dispute fee, so `amount_cents` alone understates the loss.
3. **`warning_*` statuses are INQUIRIES.** No money moves. They must never
   touch credits or entitlement.

And from Stripe's documentation: a `lost` dispute can later become `won`
(a "late win", when an issuer corrects itself outside the normal flow). So
`lost` is NOT terminal, and the state machine has to allow that one
backwards step while rejecting every other.

STATE MACHINE
-------------
    warning_needs_response ─→ warning_under_review ─→ warning_closed
        (inquiry: recorded, no financial or entitlement effect)

    needs_response ─→ under_review ─→ won
                                  └─→ lost ──→ won   (late win)

Applied by RANK (see DISPUTE_STATUS_RANK), not by event type. Every dispute
webhook carries the full Dispute object with its current status, so each
delivery is an idempotent "the state is now X" rather than a step that must
arrive in order.

IDEMPOTENCY AND ORDERING RULES
------------------------------
* Keyed on `stripe_dispute_id` — a repeated `created` is a no-op update.
* A delivery whose status ranks LOWER than what we already recorded is
  ignored. Stripe does not guarantee ordering, so a delayed `created` can
  arrive after `closed`; without this it would drag a settled dispute back
  to needs_response.
* `lost -> won` is the one permitted rank decrease, because Stripe
  documents it.
* Financial side effects are gated on ROW FLAGS (`credits_reversed`), not
  on the event, and are performed inside `transaction.atomic` with the
  wallet row locked. A duplicate or replayed `lost` therefore cannot
  reverse twice.
* One payment can carry more than one dispute (Stripe documents this as
  rare but real), which is why nothing here keys on the charge.

BUSINESS RULES (as specified by the owner)
------------------------------------------
* created  -> record it, mark the transaction DISPUTED, revoke NOTHING.
* won      -> restore the normal state; no credits were taken, so none
              are returned.
* lost     -> reflect the loss, reverse the credits that payment bought.
* already spent -> never delete usage history. Record the shortfall as a
              deficit on the wallet and block further consumption until a
              human settles it.
"""

import logging

from django.db import transaction
from django.utils import timezone

from .credit_reversal import (
    credits_granted_by_payment,
    restore_credits_for_payment,
    reverse_credits_for_payment,
)
from .models import (
    DISPUTE_STATUS_RANK,
    BillingTransaction,
    BillingTransactionStatus,
    CreditBucket,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    DisputeStatus,
    PaymentDispute,
)

logger = logging.getLogger(__name__)


def _coerce_timestamp(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        return None
    from datetime import datetime
    from datetime import timezone as dt_timezone

    return datetime.fromtimestamp(int(value), tz=dt_timezone.utc)


def _dispute_fee_cents(dispute) -> int:
    """
    Stripe's fee for this dispute, summed from its balance transactions.

    Read rather than assumed: the fee varies by country and by whether the
    dispute was countered, and on a win the reinstating balance transaction
    carries fee=0 while the original still carries the fee.
    """
    total = 0
    for entry in dispute.get("balance_transactions") or []:
        fee = entry.get("fee")
        if isinstance(fee, int):
            total += fee
    return total


def _match_transaction(dispute):
    """
    The local money row this dispute is about.

    PaymentIntent first, then charge — the same specificity order
    BillingTransactionService uses. Returns None when we have no record of
    the payment, which is itself worth surfacing rather than dropping.
    """
    pi = dispute.get("payment_intent")
    if isinstance(pi, dict):
        pi = pi.get("id")
    charge = dispute.get("charge")
    if isinstance(charge, dict):
        charge = charge.get("id")

    txn = None
    if pi:
        txn = BillingTransaction.objects.filter(stripe_payment_intent_id=pi).first()
    if txn is None and charge:
        txn = BillingTransaction.objects.filter(stripe_charge_id=charge).first()
    return txn, pi or "", charge or ""


def _should_apply(existing_status, incoming_status) -> bool:
    """
    Is this delivery newer than what we already have?

    True for a first sighting, for a forward step, and for the documented
    `lost -> won` late win. False for a stale redelivery, which is what
    keeps out-of-order events harmless.
    """
    if existing_status is None:
        return True
    if existing_status == incoming_status:
        return True  # same state, refresh the payload/audit fields
    old = DISPUTE_STATUS_RANK.get(existing_status, -1)
    new = DISPUTE_STATUS_RANK.get(incoming_status, -1)
    if new > old:
        return True
    # Stripe's documented "late win": an issuer may overturn a loss after
    # the fact and return the funds.
    return (
        existing_status == DisputeStatus.LOST and incoming_status == DisputeStatus.WON
    )


class DisputeService:
    """Applies a Stripe Dispute object to local state, idempotently."""

    @staticmethod
    @transaction.atomic
    def apply(dispute):
        """
        Record `dispute` and run whatever entitlement change its status
        implies. Safe to call repeatedly with the same object, and safe to
        call with events that arrive out of order.

        Returns the PaymentDispute row.
        """
        dispute_id = dispute.get("id")
        if not dispute_id:
            logger.warning("Dispute event carried no id; ignoring: %r", dispute)
            return None

        status = dispute.get("status") or ""
        txn, pi_id, charge_id = _match_transaction(dispute)

        row, created = PaymentDispute.objects.select_for_update().get_or_create(
            stripe_dispute_id=dispute_id,
            defaults={
                "status": status,
                "stripe_charge_id": charge_id,
                "stripe_payment_intent_id": pi_id,
                "billing_transaction": txn,
                "user": getattr(txn, "user", None),
                "reason": dispute.get("reason") or "",
                "amount_cents": dispute.get("amount") or 0,
                "fee_cents": _dispute_fee_cents(dispute),
                "currency": dispute.get("currency") or "usd",
                "opened_at": _coerce_timestamp(dispute.get("created")),
            },
        )

        if created and txn is None:
            logger.error(
                "DISPUTE WITH NO MATCHING PAYMENT: %s (%s %s) has no local "
                "BillingTransaction for payment_intent=%r charge=%r. The "
                "money has already left the Stripe balance. This needs "
                "manual reconciliation.",
                dispute_id,
                dispute.get("amount"),
                dispute.get("currency"),
                pi_id,
                charge_id,
            )

        if not _should_apply(None if created else row.status, status):
            logger.info(
                "Dispute %s: ignoring stale delivery status=%r; already at %r.",
                dispute_id,
                status,
                row.status,
            )
            return row

        row.status = status
        row.raw_payload = dict(dispute)
        row.reason = dispute.get("reason") or row.reason
        row.amount_cents = dispute.get("amount") or row.amount_cents
        fee = _dispute_fee_cents(dispute)
        if fee:
            row.fee_cents = fee
        evidence = dispute.get("evidence_details") or {}
        row.evidence_due_by = _coerce_timestamp(evidence.get("due_by"))
        if txn is not None and row.billing_transaction_id is None:
            row.billing_transaction = txn
            row.user = txn.user
        row.save()

        if row.is_inquiry:
            # An inquiry moves no money. Recorded for the audit trail and
            # for the response deadline, but entitlement is untouched.
            logger.info(
                "Dispute %s is an INQUIRY (%s) on %s %s — recorded, no "
                "entitlement change. Evidence due %s.",
                dispute_id,
                status,
                row.amount_cents,
                row.currency,
                row.evidence_due_by,
            )
            return row

        if status in (DisputeStatus.NEEDS_RESPONSE, DisputeStatus.UNDER_REVIEW):
            DisputeService._on_opened(row)
        elif status == DisputeStatus.WON:
            DisputeService._on_won(row)
        elif status == DisputeStatus.LOST:
            DisputeService._on_lost(row)
        return row

    # -- outcomes ---------------------------------------------------------

    @staticmethod
    def _on_opened(row):
        """
        A chargeback is open. Mark the money row, change nothing else.

        Deliberately no credit revocation: most disputes are resolved, and
        cutting a paying customer off on an accusation is worse than
        carrying the risk for the weeks the decision takes.
        """
        row.funds_withdrawn_at = row.funds_withdrawn_at or timezone.now()
        row.save(update_fields=["funds_withdrawn_at", "updated_at"])

        txn = row.billing_transaction
        if txn is not None and txn.status != BillingTransactionStatus.DISPUTED:
            txn.status = BillingTransactionStatus.DISPUTED
            txn.save(update_fields=["status", "updated_at"])

        logger.error(
            "CHARGEBACK OPENED: dispute %s for %s %s on transaction %s "
            "(reason=%s). Stripe has already debited the amount plus a "
            "%s fee. Evidence is due by %s — after that it is lost by "
            "default.",
            row.stripe_dispute_id,
            row.amount_cents,
            row.currency,
            getattr(txn, "id", None),
            row.reason,
            row.fee_cents,
            row.evidence_due_by,
        )

    @staticmethod
    def _on_won(row):
        """
        Overturned in our favour. Restore normal state.

        If a previous `lost` had already reversed credits (a late win),
        give them back — otherwise the customer is punished for a decision
        that went their way.
        """
        row.funds_reinstated_at = row.funds_reinstated_at or timezone.now()
        row.closed_at = row.closed_at or timezone.now()
        row.save(update_fields=["funds_reinstated_at", "closed_at", "updated_at"])

        txn = row.billing_transaction
        if txn is not None and txn.status in (
            BillingTransactionStatus.DISPUTED,
            BillingTransactionStatus.DISPUTE_LOST,
        ):
            txn.status = BillingTransactionStatus.PAID
            txn.save(update_fields=["status", "updated_at"])

        if row.credits_reversed:
            DisputeService._restore_reversed_credits(row)

        logger.info(
            "Dispute %s WON. Funds reinstated; the %s %s dispute fee is "
            "not returned.",
            row.stripe_dispute_id,
            row.fee_cents,
            row.currency,
        )

    @staticmethod
    def _on_lost(row):
        """
        Upheld against us. The money is gone; reclaim what it bought.

        Guarded by `credits_reversed` so a duplicate or replayed `lost`
        cannot reverse twice.
        """
        row.closed_at = row.closed_at or timezone.now()
        row.save(update_fields=["closed_at", "updated_at"])

        txn = row.billing_transaction
        if txn is not None:
            txn.status = BillingTransactionStatus.DISPUTE_LOST
            txn.save(update_fields=["status", "updated_at"])

        if not row.credits_reversed:
            DisputeService._reverse_credits(row)

        logger.error(
            "CHARGEBACK LOST: dispute %s. Total loss %s %s (amount %s + fee "
            "%s). Credits reversed: %s, unreclaimable deficit: %s.",
            row.stripe_dispute_id,
            row.total_loss_cents,
            row.currency,
            row.amount_cents,
            row.fee_cents,
            row.credits_reversed_amount,
            row.credits_deficit_amount,
        )

    # -- credit reversal ---------------------------------------------------

    @staticmethod
    def _reverse_credits(row):
        """
        Take back the credits the charged-back payment bought.

        Reclaims from the buckets that payment created, newest first, and
        clamps at what is actually left. Whatever cannot be reclaimed —
        because the customer already spent it — becomes a deficit on the
        wallet and blocks further consumption. Usage history is never
        touched: the work really happened and really cost us.
        """
        # PRECISE ATTRIBUTION FIRST. If this payment left grant rows — an
        # overage purchase, individual or school — we know exactly which
        # buckets it filled and can reclaim from those. Measured before
        # this existed: a chargeback on a 500-credit overage purchase
        # reclaimed NOTHING, because the plan-allocation fallback below
        # only understands subscription payments and returned zero.
        #
        # Routed through credit_reversal so a refund on the same payment
        # and this chargeback share one view of what has been settled and
        # cannot both reclaim it.
        if DisputeService._reverse_via_payment_attribution(row):
            return

        txn = row.billing_transaction
        if txn is None or txn.user_id is None:
            logger.warning(
                "Dispute %s lost but no local payment to reverse credits "
                "against; nothing reclaimed.",
                row.stripe_dispute_id,
            )
            return

        wallet = (
            CreditWallet.objects.select_for_update().filter(user_id=txn.user_id).first()
        )
        if wallet is None:
            return

        # The credits this payment bought. Subscription invoices leave no
        # per-payment grant rows, so attribute by the subscription's plan
        # allocation — the amount the payment entitled them to.
        owed = DisputeService._credits_bought_by(txn)
        if owed <= 0:
            row.credits_reversed = True
            row.save(update_fields=["credits_reversed", "updated_at"])
            return

        reclaimed = 0
        buckets = (
            CreditBucket.objects.select_for_update()
            .filter(wallet=wallet)
            .order_by("-created_at")
        )
        for bucket in buckets:
            if reclaimed >= owed:
                break
            available = max(0, bucket.total_credits - bucket.used_credits)
            if available <= 0:
                continue
            take = min(available, owed - reclaimed)
            bucket.used_credits += take
            bucket.save(update_fields=["used_credits", "updated_at"])
            CreditLedger.record(
                user=txn.user,
                bucket=bucket,
                ledger_type=CreditLedgerType.DISPUTE_REVERSAL,
                amount=-take,
                reference=f"Chargeback {row.stripe_dispute_id} reversal",
                metadata={
                    "stripe_dispute_id": row.stripe_dispute_id,
                    "billing_transaction_id": str(txn.id),
                },
            )
            reclaimed += take

        deficit = owed - reclaimed
        row.credits_reversed = True
        row.credits_reversed_amount = reclaimed
        row.credits_deficit_amount = deficit
        row.save(
            update_fields=[
                "credits_reversed",
                "credits_reversed_amount",
                "credits_deficit_amount",
                "updated_at",
            ]
        )

        if deficit > 0:
            wallet.dispute_deficit_credits += deficit
            wallet.save(update_fields=["dispute_deficit_credits", "updated_at"])
            wallet.sync_consumption_block()
            logger.error(
                "Dispute %s: %s credits were already spent and cannot be "
                "reclaimed. Recorded as a deficit and consumption is now "
                "BLOCKED for user %s until this is settled.",
                row.stripe_dispute_id,
                deficit,
                txn.user_id,
            )

    @staticmethod
    def _reverse_via_payment_attribution(row) -> bool:
        """
        Reclaim using the payment's own grant rows, when it has any.

        Returns True when this payment is one we can attribute precisely
        (an overage purchase), in which case it has been fully handled.
        False means "no grant rows for this PaymentIntent" — a
        subscription invoice — and the caller falls back to the plan
        allocation heuristic.
        """
        payment_intent_id = row.stripe_payment_intent_id
        granted = credits_granted_by_payment(payment_intent_id)
        if granted <= 0:
            return False

        outcome = reverse_credits_for_payment(
            payment_intent_id,
            target_cumulative=granted,
            ledger_type=CreditLedgerType.DISPUTE_REVERSAL,
            reference=f"Chargeback {row.stripe_dispute_id} reversal",
            metadata={
                "stripe_dispute_id": row.stripe_dispute_id,
                "billing_transaction_id": str(row.billing_transaction_id or ""),
            },
        )

        row.credits_reversed = True
        row.credits_reversed_amount = outcome.reclaimed
        row.credits_deficit_amount = outcome.deficit
        # Recorded per wallet, because a school payment can leave several
        # teachers in debt and a late win has to lift the block from each.
        row.deficit_by_wallet = {
            str(wallet_id): amount
            for wallet_id, amount in outcome.deficit_by_wallet.items()
            if amount > 0
        }
        row.save(
            update_fields=[
                "credits_reversed",
                "credits_reversed_amount",
                "credits_deficit_amount",
                "deficit_by_wallet",
                "updated_at",
            ]
        )

        for wallet_id, amount in sorted(
            outcome.deficit_by_wallet.items(), key=lambda kv: str(kv[0])
        ):
            if amount <= 0:
                continue
            wallet = (
                CreditWallet.objects.select_for_update().filter(id=wallet_id).first()
            )
            if wallet is None:
                continue
            wallet.dispute_deficit_credits += amount
            wallet.save(update_fields=["dispute_deficit_credits", "updated_at"])
            wallet.sync_consumption_block()
            logger.error(
                "Dispute %s: %s credit(s) bought by payment %s had already "
                "been spent from wallet %s. Recorded as a deficit; "
                "consumption is now BLOCKED until settled.",
                row.stripe_dispute_id,
                amount,
                payment_intent_id,
                wallet.id,
            )

        logger.info(
            "Dispute %s reversed against payment %s: %s reclaimed, %s "
            "unreclaimable (of %s granted).",
            row.stripe_dispute_id,
            payment_intent_id,
            outcome.reclaimed,
            outcome.deficit,
            granted,
        )
        return True

    @staticmethod
    def _restore_reversed_credits(row):
        """Undo a reversal after a late win."""
        txn = row.billing_transaction

        # Precisely-attributed reversals are restored to the very buckets
        # they were taken from, read back off the reversal ledger rows.
        restored = restore_credits_for_payment(
            row.stripe_payment_intent_id,
            ledger_type=CreditLedgerType.DISPUTE_REVERSAL,
            reference=f"Chargeback {row.stripe_dispute_id} late win — restored",
            metadata={"stripe_dispute_id": row.stripe_dispute_id},
        )

        DisputeService._clear_deficits(row, txn)

        if restored:
            row.credits_reversed = False
            row.credits_reversed_amount = 0
            row.credits_deficit_amount = 0
            row.deficit_by_wallet = {}
            row.save(
                update_fields=[
                    "credits_reversed",
                    "credits_reversed_amount",
                    "credits_deficit_amount",
                    "deficit_by_wallet",
                    "updated_at",
                ]
            )
            return

        wallet = (
            CreditWallet.objects.select_for_update()
            .filter(user_id=getattr(txn, "user_id", None))
            .first()
        )

        if txn is not None and row.credits_reversed_amount:
            bucket = (
                CreditBucket.objects.filter(wallet=wallet)
                .order_by("-created_at")
                .first()
            )
            if bucket is not None:
                bucket.used_credits = max(
                    0, bucket.used_credits - row.credits_reversed_amount
                )
                bucket.save(update_fields=["used_credits", "updated_at"])
                CreditLedger.record(
                    user=txn.user,
                    bucket=bucket,
                    ledger_type=CreditLedgerType.DISPUTE_REVERSAL,
                    amount=row.credits_reversed_amount,
                    reference=f"Chargeback {row.stripe_dispute_id} late win — restored",
                    metadata={"stripe_dispute_id": row.stripe_dispute_id},
                )

        row.credits_reversed = False
        row.credits_reversed_amount = 0
        row.credits_deficit_amount = 0
        row.deficit_by_wallet = {}
        row.save(
            update_fields=[
                "credits_reversed",
                "credits_reversed_amount",
                "credits_deficit_amount",
                "deficit_by_wallet",
                "updated_at",
            ]
        )

    @staticmethod
    def _clear_deficits(row, txn):
        """
        Lift this chargeback's debt from every wallet carrying it.

        Uses the per-wallet map recorded at reversal time where there is
        one. A school overage payment leaves several teachers in debt, and
        `billing_transaction.user` names at most one of them — for a
        license payment, usually none — so clearing by that alone would
        leave teachers permanently blocked after a win.

        Falls back to the transaction's own user for disputes recorded
        before the map existed, and for the subscription path, where the
        debt only ever belongs to one wallet.
        """
        entries = dict(row.deficit_by_wallet or {})
        if not entries and row.credits_deficit_amount and txn is not None:
            wallet_id = (
                CreditWallet.objects.filter(user_id=txn.user_id)
                .values_list("id", flat=True)
                .first()
            )
            if wallet_id:
                entries = {str(wallet_id): row.credits_deficit_amount}

        for wallet_id, amount in sorted(entries.items()):
            if not amount:
                continue
            wallet = (
                CreditWallet.objects.select_for_update().filter(id=wallet_id).first()
            )
            if wallet is None:
                continue
            wallet.dispute_deficit_credits = max(
                0, wallet.dispute_deficit_credits - int(amount)
            )
            wallet.save(update_fields=["dispute_deficit_credits", "updated_at"])
            # Recomputed from BOTH counters, never assigned from one. An
            # unrelated refund debt on the same wallet must keep the block
            # on after this dispute clears its own.
            wallet.sync_consumption_block()

    @staticmethod
    def _credits_bought_by(txn) -> int:
        """
        How many credits the disputed payment entitled the customer to.

        The plan's monthly allocation, since that is what one paid cycle
        buys. Falls back to zero rather than guessing when the payment has
        no subscription attached — over-reclaiming would take credits the
        customer paid for separately.
        """
        sub = txn.user_subscription
        if sub is None or sub.plan_id is None:
            return 0
        return int(sub.plan.monthly_credits or 0)
