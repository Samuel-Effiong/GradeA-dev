"""
billing/departed_share_refund.py
================================
The share of a school's overage purchase that belonged to a teacher who
left before the payment went through is refunded (B1a plan step 3b, plan
§9 item 3; owner ruling 2026-10-29).

At fulfillment the purchase handlers re-check every teacher under lock and
skip anyone no longer on the license. Today that skipped share is left for
a human ("Needs manual review"). With the switch on:

  * card payments: one Stripe partial refund for the skipped share, issued
    by `refund_departed_teacher_share` after the webhook's transaction
    commits (a refund cannot be rolled back with the database);
  * offline payments: an OFFLINE_OVERAGE_REFUND_DUE billing record for the
    super admin, who pays it back by hand.

THE ROUND TRIP
--------------
Our refund comes back as `charge.refunded`. By default RefundService would
then reclaim `granted * refunded // captured` from the teachers who WERE
granted, taking credits from people who are owed them. The refund
therefore carries `retain_credits=true` (payment_refunds.py), and its
`system_refund_reason` marks it as this system's own.

NEVER TWICE
-----------
The Stripe idempotency key covers a retry within 24 hours. Beyond that,
before refunding, the payment's existing refunds are read and any refund
carrying our reason and this intent's id ends the run (guard 2). The
amount is capped at what is still refundable on the payment.

THE SWITCH
----------
On since the owner approved the texts (2026-11-08). With it off, today's
behavior and today's texts are unchanged: a refund running while the purchase still
says "Needs manual review (partial refund ...)" invites a second, manual
refund of the same money.
"""

import logging

from .imports import stripe

logger = logging.getLogger(__name__)

#: On: the owner approved DEPARTED_SHARE_REFUND_TEXTS on 2026-11-08
#: (relayed by the SM). Off restores today's behavior and texts exactly.
DEPARTED_SHARE_REFUND_APPROVED = True

#: Approved by the owner 2026-11-08, verbatim
#: (team/approved_wording_departed_share_2026-11-08.md). Two SM rulings on
#: that file: `offline_record_notes` is the bare amount (no prose), and
#: `approval_email_not_credited` is W-6 without its "Not credited — "
#: heading, which the template keeps. The history suffixes omit their
#: leading space; the callers add it.
DEPARTED_SHARE_REFUND_TEXTS = {
    "purchase_note": (
        "Paid for {blocks} block(s). {n} teacher(s) had left before the "
        "purchase went through, so they weren't given credits: {skipped}. "
        "Their share, {amount}, is being refunded automatically to the card "
        "used."
    ),
    "history_suffix_card": "({n} teacher(s) had left; their share is being refunded)",
    "history_suffix_offline": "({n} teacher(s) had left; their share is owed back)",
    "email_subject": "Part of your credit purchase is being refunded",
    "email_body_card": (
        "{n} teacher(s) in your credit purchase had left your school before it "
        "went through, so they weren't given credits. The other teachers "
        "received theirs. We're refunding {amount} for the credits that "
        "weren't given to the card you paid with."
    ),
    "offline_record_notes": "{amount}",
    "approval_email_not_credited": (
        "these teachers had left your school before the purchase went "
        "through, so they weren't given credits. Our team will refund "
        "{amount} to you directly."
    ),
}

#: Stamped on the refund so the inbound charge.refunded is recognisable.
SYSTEM_REFUND_DEPARTED_TEACHER_SHARE = "departed_teacher_share"

#: Stripe errors worth retrying: the request may simply not have arrived.
RETRYABLE_STRIPE_ERRORS = (
    stripe.error.APIConnectionError,
    stripe.error.RateLimitError,
    stripe.error.APIError,
)

#: Refund statuses that returned no money and so leave it refundable.
_UNSPENT_REFUND_STATUSES = frozenset({"failed", "canceled"})


class RetryableRefundError(Exception):
    """Stripe could not be reached; the refund may be retried safely."""


def enabled():
    return DEPARTED_SHARE_REFUND_APPROVED


def text(key, **fields):
    return DEPARTED_SHARE_REFUND_TEXTS[key].format(**fields)


def format_amount(cents):
    return f"${cents / 100:,.2f}"


def share_cents(skipped, unit_price_cents):
    """What the skipped teachers' blocks cost."""
    return sum(int(row["blocks"]) for row in skipped) * int(unit_price_cents)


def idempotency_key(intent_id):
    return f"departed-share-refund-{intent_id}"


def can_refund_automatically(skipped, payment_intent, currency):
    """The skipped share is refunded automatically only when the switch is
    on, there is a PaymentIntent to refund, and it is in dollars (no other
    currency's symbol is guessed). Anything else stays as today, for a
    human, and is reported."""
    if not (skipped and enabled()):
        return False
    if not payment_intent:
        _manual(
            "a departed teacher's share is owed but the checkout carries no "
            "PaymentIntent to refund."
        )
        return False
    if (currency or "").lower() != "usd":
        _manual(
            "a departed teacher's share is owed on PaymentIntent %s, paid in "
            "%r; only usd is refunded automatically.",
            payment_intent,
            currency,
        )
        return False
    return True


def _paid_cents(intent):
    """What the payment captured: the purchase's BillingTransaction records
    the session's amount_total; the intent's quote is the fallback."""
    from .models import BillingTransaction, BillingTransactionType

    paid = (
        BillingTransaction.objects.filter(
            stripe_payment_intent_id=intent.stripe_payment_intent_id,
            transaction_type=BillingTransactionType.LICENSE_OVERAGE_PURCHASE,
        )
        .values_list("amount_cents", flat=True)
        .first()
    )
    return int(paid if paid is not None else intent.amount_cents)


def _manual(message, *args):
    logger.error("MANUAL RECONCILIATION NEEDED: " + message, *args)


def issue_share_refund(intent_id, share):
    """Refund `share` cents of the intent's payment, at most once.

    Raises RetryableRefundError when Stripe could not be reached. Anything
    else that stops the refund is logged for manual reconciliation; the
    grants, the intent and its BillingTransaction are never touched here.
    """
    from .models import LicenseOveragePurchaseIntent

    intent = LicenseOveragePurchaseIntent.objects.get(pk=intent_id)
    payment_intent = intent.stripe_payment_intent_id
    if not payment_intent:
        _manual(
            "overage intent %s owes a departed teacher's share of %s cents "
            "but has no PaymentIntent to refund.",
            intent_id,
            share,
        )
        return None
    if share <= 0:
        return None

    try:
        existing = stripe.Refund.list(payment_intent=payment_intent, limit=100)
        refunds = list(existing.get("data") or [])
        for refund in refunds:
            meta = refund.get("metadata") or {}
            if meta.get(
                "system_refund_reason"
            ) == SYSTEM_REFUND_DEPARTED_TEACHER_SHARE and meta.get("intent_id") == str(
                intent_id
            ):
                logger.info(
                    "Departed-teacher share of intent %s already refunded (%s).",
                    intent_id,
                    refund.get("id"),
                )
                return None

        already = sum(
            int(r.get("amount") or 0)
            for r in refunds
            if r.get("status") not in _UNSPENT_REFUND_STATUSES
        )
        refundable = _paid_cents(intent) - already
        amount = min(int(share), refundable)
        if amount <= 0:
            _manual(
                "overage intent %s owes a departed teacher's share of %s cents "
                "but nothing is left to refund on PaymentIntent %s.",
                intent_id,
                share,
                payment_intent,
            )
            return None

        refund = stripe.Refund.create(
            payment_intent=payment_intent,
            amount=amount,
            idempotency_key=idempotency_key(intent_id),
            metadata={
                "system_refund_reason": SYSTEM_REFUND_DEPARTED_TEACHER_SHARE,
                "retain_credits": "true",
                "intent_id": str(intent_id),
            },
        )
    except RETRYABLE_STRIPE_ERRORS as exc:
        raise RetryableRefundError(str(type(exc).__name__)) from exc
    except stripe.error.StripeError as exc:
        _manual(
            "the departed-teacher share refund for overage intent %s was "
            "refused by Stripe (%s).",
            intent_id,
            type(exc).__name__,
        )
        return None

    logger.warning(
        "Refunded %s cents of PaymentIntent %s: the share of teacher(s) who "
        "left before overage intent %s was fulfilled.",
        amount,
        payment_intent,
        intent_id,
    )
    return refund
