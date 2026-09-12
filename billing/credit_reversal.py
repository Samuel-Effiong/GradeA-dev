"""
billing/credit_reversal.py
==========================
Taking back the credits a specific payment bought.

WHY THIS IS SHARED
------------------
Two different events mean "that money is no longer ours": a refund
(billing/payment_refunds.py) and a lost chargeback (billing/disputes.py).
Both have to reduce entitlement by the same amount, against the same
buckets, without ever reducing it twice — and a payment can genuinely
attract both, since a partial refund does not stop the customer disputing
the rest. If each path kept its own idea of "how much have we already
reclaimed", the overlap would double-reverse.

So the accounting lives here, and it is DERIVED rather than kept in a
counter each caller increments:

    granted        = sum of the payment's POSITIVE ledger rows
    settled        = net of its reversal rows, PLUS debt already recorded
                     against it by either cause
    reversible now = clamp(target - settled, 0, granted - settled)

The ledger half is append-only, so it cannot drift and reads the same to
whichever caller asks. Every caller states a cumulative TARGET rather than
a delta, so a duplicate delivery computes zero on its own and a retry
after a partial failure converges instead of compounding.

ATTRIBUTION
-----------
Reclaim comes from the buckets THAT PAYMENT created, never from the
wallet at large. A customer who bought two blocks and is refunded for one
must lose one block's worth, not whatever happens to be sitting newest in
their wallet — and a school payment that spread credits across six
teachers must take back from those six, in proportion to what each
received.

That proportional split is also why a shortfall is attributed per wallet.
If teacher A has spent their share and teacher B has not, the deficit
belongs to A. Covering A's shortfall out of B's balance would take
credits from a teacher who did nothing wrong.

WHAT IS NEVER TOUCHED
---------------------
Usage history. If the credits were already spent, the work really
happened and really cost us; deleting the CreditUsageLog rows would make
the ledger lie about it. The shortfall is recorded as a debt on the
wallet instead, which blocks further consumption until a human settles
it.
"""

import logging
from collections import defaultdict

from django.db import transaction

from .models import CreditBucket, CreditLedger, CreditLedgerType, CreditWallet

logger = logging.getLogger(__name__)

#: Ledger types that represent credits being taken back off a payment.
#: Summed together deliberately: a refund and a chargeback on the same
#: payment must see each other's work, or they will reverse it twice.
REVERSAL_LEDGER_TYPES = (
    CreditLedgerType.DISPUTE_REVERSAL,
    CreditLedgerType.REFUND_REVERSAL,
)


class ReversalOutcome:
    """What one reversal call actually did."""

    def __init__(self):
        self.reclaimed = 0
        self.deficit_by_wallet = {}
        self.granted = 0
        self.already_reversed = 0
        self.attempted = 0

    @property
    def deficit(self) -> int:
        return sum(self.deficit_by_wallet.values())

    @property
    def total_reversed(self) -> int:
        """Cumulative for the payment, including this call."""
        return self.already_reversed + self.reclaimed + self.deficit

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"<ReversalOutcome reclaimed={self.reclaimed} "
            f"deficit={self.deficit} of granted={self.granted}>"
        )


def grant_rows_for_payment(payment_intent_id):
    """The ledger rows in which this payment handed credits over."""
    if not payment_intent_id:
        return CreditLedger.objects.none()
    return CreditLedger.objects.filter(
        stripe_payment_intent_id=payment_intent_id, amount__gt=0
    )


def credits_granted_by_payment(payment_intent_id) -> int:
    return sum(
        row.amount for row in grant_rows_for_payment(payment_intent_id).only("amount")
    )


def credits_already_reversed(payment_intent_id) -> int:
    """
    How much of this payment has already been clawed back, by any cause.

    Positive number. Reads reversal rows written by BOTH the refund and
    the dispute path — that shared view is what makes a double reversal
    arithmetically impossible rather than merely unlikely.

    NET of restores. A reversal that was later undone (a chargeback won
    late) leaves a matching POSITIVE row of the same type, and the two
    cancel. Counting only the negative rows would leave the payment
    looking permanently reversed, so a refund arriving afterwards would
    reclaim nothing.
    """
    if not payment_intent_id:
        return 0
    rows = CreditLedger.objects.filter(
        stripe_payment_intent_id=payment_intent_id,
        ledger_type__in=REVERSAL_LEDGER_TYPES,
    ).only("amount")
    return max(0, sum(-row.amount for row in rows))


def credits_settled_for_payment(payment_intent_id) -> int:
    """
    Everything already accounted for on this payment: credits actually
    reclaimed PLUS debt already recorded for credits that were spent
    before we could reclaim them.

    The deficit half matters and is easy to miss. A reclaim leaves a
    negative ledger row; a deficit leaves nothing in the ledger, because
    no bucket moved. Counting only the ledger would therefore make an
    entirely-spent payment look untouched, and the second delivery of the
    same refund would record the debt a second time — measured: a 500
    credit purchase, fully spent, fully refunded twice, produced a 1000
    credit deficit.

    It also spans BOTH causes, so a chargeback on an already-refunded
    payment cannot re-charge the customer for a debt the refund already
    recorded.
    """
    if not payment_intent_id:
        return 0

    from django.db.models import Sum

    from .models import PaymentDispute, PaymentRefund

    refunded = (
        PaymentRefund.objects.filter(
            stripe_payment_intent_id=payment_intent_id
        ).aggregate(total=Sum("credits_deficit"))["total"]
        or 0
    )
    disputed = (
        PaymentDispute.objects.filter(
            stripe_payment_intent_id=payment_intent_id
        ).aggregate(total=Sum("credits_deficit_amount"))["total"]
        or 0
    )
    return credits_already_reversed(payment_intent_id) + refunded + disputed


def blocks_granted_by_payment(payment_intent_id) -> dict:
    """
    Overage BLOCKS this payment granted, per wallet id (as a string).

    Read from the grant rows' metadata, which the two purchase flows
    happen to spell differently — `quantity` on the individual flow,
    `blocks_purchased` on the license one. Absent on non-overage grants,
    which simply means there is no block allowance to give back.
    """
    blocks = defaultdict(int)
    rows = grant_rows_for_payment(payment_intent_id).select_related("bucket")
    for row in rows:
        if row.bucket is None or row.bucket.wallet_id is None:
            continue
        meta = row.metadata or {}
        raw = meta.get("quantity", meta.get("blocks_purchased"))
        if raw is None:
            continue
        try:
            count = int(raw)
        except (TypeError, ValueError):
            continue
        if count > 0:
            blocks[str(row.bucket.wallet_id)] += count
    return dict(blocks)


def _allocate_proportionally(need, weights):
    """
    Split `need` across `weights` (a list of (key, weight)) in proportion,
    giving the rounding remainder to the largest weight.

    Deterministic — ties broken by key — so two concurrent callers reading
    the same inputs compute the same split.
    """
    total_weight = sum(w for _, w in weights)
    if need <= 0 or total_weight <= 0:
        return {}
    shares = {}
    assigned = 0
    for key, weight in weights:
        share = (need * weight) // total_weight
        shares[key] = share
        assigned += share
    remainder = need - assigned
    if remainder:
        biggest = max(weights, key=lambda kw: (kw[1], str(kw[0])))[0]
        shares[biggest] += remainder
    return shares


@transaction.atomic
def reverse_credits_for_payment(
    payment_intent_id,
    *,
    target_cumulative,
    ledger_type,
    reference,
    metadata=None,
) -> ReversalOutcome:
    """
    Bring the total credits reclaimed for `payment_intent_id` up to
    `target_cumulative`, and no further.

    Expressed as a target rather than an amount so that repeated
    deliveries, out-of-order deliveries and a second cause acting on the
    same payment all converge on the same state instead of stacking.

    Returns a ReversalOutcome. Never raises on "nothing to do".
    """
    outcome = ReversalOutcome()
    if not payment_intent_id:
        return outcome

    grant_rows = list(
        grant_rows_for_payment(payment_intent_id).select_related("bucket")
    )
    outcome.granted = sum(row.amount for row in grant_rows)
    # Reclaimed AND already-recorded debt, across refunds and chargebacks
    # alike — see credits_settled_for_payment.
    outcome.already_reversed = credits_settled_for_payment(payment_intent_id)

    if outcome.granted <= 0:
        return outcome

    ceiling = outcome.granted - outcome.already_reversed
    need = min(target_cumulative, outcome.granted) - outcome.already_reversed
    need = max(0, min(need, ceiling))
    outcome.attempted = need
    if need <= 0:
        return outcome

    # How much this payment put into each bucket. A bucket can appear on
    # more than one grant row (a retried grant, a multi-row school
    # allocation), so sum rather than assume one row each.
    granted_by_bucket = defaultdict(int)
    bucket_ids = []
    for row in grant_rows:
        if row.bucket_id is None:
            continue
        if row.bucket_id not in granted_by_bucket:
            bucket_ids.append(row.bucket_id)
        granted_by_bucket[row.bucket_id] += row.amount

    if not bucket_ids:
        # The payment granted credits but we can no longer see into which
        # bucket. Nothing to reclaim from; the whole amount is a debt, but
        # we have no wallet to attach it to either.
        logger.error(
            "Cannot reverse %s credits for payment %s: its grant rows have "
            "no surviving bucket reference. Needs manual reconciliation.",
            need,
            payment_intent_id,
        )
        return outcome

    # WALLETS FIRST, THEN BUCKETS, each by primary key.
    #
    # The order is not arbitrary and is not merely tidy:
    # `CreditWallet.consume_credits` takes the wallet lock and then draws
    # down buckets. Taking them the other way round here is a lock-order
    # inversion, and a refund arriving while the customer is spending
    # deadlocks — reproduced by
    # ConcurrentRefundTests.test_a_refund_racing_a_consumption_never_oversells,
    # which failed with "deadlock detected ... while locking tuple in
    # relation billing_creditwallet" until this matched consumption's
    # order.
    #
    # Within each table the ids are sorted so two reversals racing on the
    # same payment queue rather than interleave.
    wallet_ids = sorted(
        {
            wallet_id
            for wallet_id in CreditBucket.objects.filter(id__in=bucket_ids).values_list(
                "wallet_id", flat=True
            )
            if wallet_id
        }
    )
    wallets = {
        w.id: w
        for w in CreditWallet.objects.select_for_update()
        .filter(id__in=wallet_ids)
        .order_by("id")
    }
    buckets = {
        bucket.id: bucket
        for bucket in CreditBucket.objects.select_for_update()
        .filter(id__in=bucket_ids)
        .order_by("id")
    }

    # Pass 1 — proportional to what each bucket received, so a refund
    # spread over several teachers lands on them in the same shape the
    # purchase did.
    weights = [(bid, granted_by_bucket[bid]) for bid in bucket_ids if bid in buckets]
    shares = _allocate_proportionally(need, weights)

    shortfall_by_wallet = defaultdict(int)
    taken_by_bucket = {}
    for bucket_id, share in shares.items():
        bucket = buckets[bucket_id]
        available = max(0, bucket.total_credits - bucket.used_credits)
        take = min(available, share)
        taken_by_bucket[bucket_id] = take
        if take < share:
            shortfall_by_wallet[bucket.wallet_id] += share - take

    # Pass 2 — a wallet's own shortfall may be coverable from another
    # bucket THIS SAME PAYMENT gave that same wallet. Strictly within the
    # wallet: one teacher's unspent credits never cover another's debt.
    for wallet_id, shortfall in list(shortfall_by_wallet.items()):
        remaining = shortfall
        for bucket_id in bucket_ids:
            if remaining <= 0:
                break
            bucket = buckets.get(bucket_id)
            if bucket is None or bucket.wallet_id != wallet_id:
                continue
            already_taken = taken_by_bucket.get(bucket_id, 0)
            spare = max(0, bucket.total_credits - bucket.used_credits - already_taken)
            if spare <= 0:
                continue
            extra = min(spare, remaining)
            taken_by_bucket[bucket_id] = already_taken + extra
            remaining -= extra
        shortfall_by_wallet[wallet_id] = remaining

    # Apply. One negative ledger row per bucket touched — the original
    # grant row is never edited, so the history reads "given 500" then
    # "taken back 500" rather than pretending the grant was smaller.
    for bucket_id, take in taken_by_bucket.items():
        if take <= 0:
            continue
        bucket = buckets[bucket_id]
        bucket.used_credits += take
        bucket.save(update_fields=["used_credits", "updated_at"])
        wallet = wallets.get(bucket.wallet_id)
        CreditLedger.record(
            user=getattr(wallet, "user", None),
            bucket=bucket,
            ledger_type=ledger_type,
            amount=-take,
            reference=reference,
            stripe_payment_intent_id=payment_intent_id,
            metadata={
                **(metadata or {}),
                "stripe_payment_intent_id": payment_intent_id,
                "reversed_from_bucket": str(bucket_id),
            },
        )
        outcome.reclaimed += take

    outcome.deficit_by_wallet = {
        wid: amount for wid, amount in shortfall_by_wallet.items() if amount > 0
    }
    return outcome


@transaction.atomic
def restore_credits_for_payment(
    payment_intent_id,
    *,
    ledger_type,
    reference,
    metadata=None,
) -> int:
    """
    Undo the reversals of ONE CAUSE recorded against `payment_intent_id`.

    For the case where the reversal turns out to have been wrong — Stripe's
    documented "late win", where an issuer overturns a chargeback we had
    already settled. Restores each bucket by exactly what was taken from
    it, read back off the reversal ledger rows rather than recomputed, so a
    partial reclaim is restored partially.

    SCOPED TO `ledger_type` deliberately. A payment can be partly refunded
    and then disputed for the rest; a late win on that dispute must give
    back what the CHARGEBACK took and leave what the refund took alone.
    Restoring both would hand the customer credits for money we really did
    return to them.

    Returns the number of credits handed back.
    """
    if not payment_intent_id:
        return 0

    reversal_rows = list(
        CreditLedger.objects.filter(
            stripe_payment_intent_id=payment_intent_id,
            ledger_type=ledger_type,
        ).select_related("bucket")
    )

    net_by_bucket = defaultdict(int)
    for row in reversal_rows:
        if row.bucket_id is None:
            continue
        # Negative rows took credits; positive rows are earlier restores.
        net_by_bucket[row.bucket_id] += -row.amount

    bucket_ids = sorted(
        (bid for bid, amount in net_by_bucket.items() if amount > 0), key=str
    )
    if not bucket_ids:
        return 0

    # Wallets before buckets, for the same reason as the reversal above:
    # `consume_credits` takes them in that order, and inverting it
    # deadlocks against a customer spending at the same moment.
    wallet_ids = sorted(
        {
            wallet_id
            for wallet_id in CreditBucket.objects.filter(id__in=bucket_ids).values_list(
                "wallet_id", flat=True
            )
            if wallet_id
        }
    )
    wallets = {
        w.id: w
        for w in CreditWallet.objects.select_for_update()
        .filter(id__in=wallet_ids)
        .order_by("id")
    }
    buckets = {
        b.id: b
        for b in CreditBucket.objects.select_for_update()
        .filter(id__in=bucket_ids)
        .order_by("id")
    }

    restored_total = 0
    for bucket_id in bucket_ids:
        bucket = buckets.get(bucket_id)
        if bucket is None:
            continue
        give_back = min(net_by_bucket[bucket_id], bucket.used_credits)
        if give_back <= 0:
            continue
        bucket.used_credits -= give_back
        bucket.save(update_fields=["used_credits", "updated_at"])
        wallet = wallets.get(bucket.wallet_id)
        CreditLedger.record(
            user=getattr(wallet, "user", None),
            bucket=bucket,
            ledger_type=ledger_type,
            amount=give_back,
            reference=reference,
            stripe_payment_intent_id=payment_intent_id,
            metadata={
                **(metadata or {}),
                "stripe_payment_intent_id": payment_intent_id,
                "restored_to_bucket": str(bucket_id),
            },
        )
        restored_total += give_back

    return restored_total
