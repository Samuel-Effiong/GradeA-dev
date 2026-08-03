"""
billing/billing_transaction_service.py
=======================================
Central point for recording BillingTransaction rows. Every call site that
confirms a charge — whether via a Stripe webhook or a synchronous
invoice/PaymentIntent-paid check already present elsewhere in this
codebase — calls BillingTransactionService.record() rather than creating
BillingTransaction rows directly, so the idempotent upsert below is never
bypassed.
"""

import logging

from django.db import transaction
from django.utils import timezone

from .models import (
    BillingTransaction,
    BillingTransactionSource,
    BillingTransactionStatus,
    BillingTransactionType,
)

logger = logging.getLogger(__name__)


class BillingTransactionService:

    @staticmethod
    def _resolve_lookup(
        stripe_invoice_id, stripe_payment_intent_id, license_billing_record
    ):
        """
        Picks the single most specific identifier to upsert on: the Stripe
        invoice (subscription-mode charges) over the payment_intent
        (one-time/payment-mode charges) over the offline
        LicenseBillingRecord link. Exactly one is relevant per call site.
        """
        if stripe_invoice_id:
            return {"stripe_invoice_id": stripe_invoice_id}
        if stripe_payment_intent_id:
            return {"stripe_payment_intent_id": stripe_payment_intent_id}
        if license_billing_record:
            return {"license_billing_record": license_billing_record}
        return None

    @staticmethod
    @transaction.atomic
    def record(
        *,
        source,
        transaction_type,
        status,
        billing_method,
        amount_cents=0,
        currency="usd",
        user=None,
        user_subscription=None,
        license_subscription=None,
        school=None,
        stripe_invoice_id=None,
        stripe_payment_intent_id=None,
        stripe_checkout_session_id=None,
        stripe_charge_id=None,
        stripe_subscription_id=None,
        receipt_url=None,
        license_billing_record=None,
        description="",
        metadata=None,
        performed_by=None,
        occurred_at=None,
    ) -> BillingTransaction:
        """
        Idempotently create-or-update a BillingTransaction.

        First call for a given invoice/payment_intent/LicenseBillingRecord
        creates the row. Any later call with the SAME identifier (e.g.
        checkout.session.completed firing before invoice.payment_succeeded
        for the same invoice) only updates fields that legitimately change
        post-creation (status, amount, previously-missing references) — it
        never overwrites transaction_type, since whichever call site
        created the row usually had the most precise knowledge of what
        kind of charge this was (e.g. a synchronous seat-increase call
        knows it's LICENSE_SEAT_CHANGE_CHARGE; the generic
        invoice.payment_succeeded webhook that fires moments later only
        knows "a subscription invoice was paid").

        Never lets a later call downgrade a terminal REFUNDED /
        PARTIALLY_REFUNDED status — refunds are only ever applied via
        handle_refund().
        """
        occurred_at = occurred_at or timezone.now()
        school = school or (
            license_subscription.school if license_subscription else None
        )

        lookup = BillingTransactionService._resolve_lookup(
            stripe_invoice_id, stripe_payment_intent_id, license_billing_record
        )

        defaults = dict(
            source=source,
            transaction_type=transaction_type,
            status=status,
            billing_method=billing_method,
            amount_cents=amount_cents or 0,
            currency=currency or "usd",
            user=user,
            user_subscription=user_subscription,
            license_subscription=license_subscription,
            school=school,
            stripe_invoice_id=stripe_invoice_id,
            stripe_payment_intent_id=stripe_payment_intent_id,
            stripe_checkout_session_id=stripe_checkout_session_id,
            stripe_charge_id=stripe_charge_id,
            stripe_subscription_id=stripe_subscription_id,
            receipt_url=receipt_url,
            license_billing_record=license_billing_record,
            description=description,
            metadata=metadata or {},
            performed_by=performed_by,
            occurred_at=occurred_at,
        )

        if lookup is None:
            logger.warning(
                "BillingTransactionService.record called with no "
                "stripe_invoice_id, stripe_payment_intent_id, or "
                "license_billing_record (type=%s) — creating an "
                "un-deduplicated row.",
                transaction_type,
            )
            return BillingTransaction.objects.create(**defaults)

        obj, created = BillingTransaction.objects.select_for_update().get_or_create(
            defaults=defaults, **lookup
        )

        if created:
            logger.info(
                "Recorded BillingTransaction %s (type=%s, status=%s, amount=%d %s).",
                obj.id,
                transaction_type,
                status,
                amount_cents or 0,
                currency,
            )
            return obj

        update_fields = []

        if status and obj.status != status:
            if obj.status not in (
                BillingTransactionStatus.REFUNDED,
                BillingTransactionStatus.PARTIALLY_REFUNDED,
            ):
                obj.status = status
                update_fields.append("status")

        if amount_cents and obj.amount_cents != amount_cents:
            obj.amount_cents = amount_cents
            update_fields.append("amount_cents")

        if stripe_charge_id and obj.stripe_charge_id != stripe_charge_id:
            obj.stripe_charge_id = stripe_charge_id
            update_fields.append("stripe_charge_id")

        if stripe_payment_intent_id and not obj.stripe_payment_intent_id:
            obj.stripe_payment_intent_id = stripe_payment_intent_id
            update_fields.append("stripe_payment_intent_id")

        if stripe_checkout_session_id and not obj.stripe_checkout_session_id:
            obj.stripe_checkout_session_id = stripe_checkout_session_id
            update_fields.append("stripe_checkout_session_id")

        if stripe_subscription_id and not obj.stripe_subscription_id:
            obj.stripe_subscription_id = stripe_subscription_id
            update_fields.append("stripe_subscription_id")

        if receipt_url and not obj.receipt_url:
            obj.receipt_url = receipt_url
            update_fields.append("receipt_url")

        if description and not obj.description:
            obj.description = description
            update_fields.append("description")

        if user_subscription and not obj.user_subscription_id:
            obj.user_subscription = user_subscription
            update_fields.append("user_subscription")

        if license_subscription and not obj.license_subscription_id:
            obj.license_subscription = license_subscription
            update_fields.append("license_subscription")

        if update_fields:
            update_fields.append("updated_at")
            obj.save(update_fields=update_fields)
            logger.info(
                "Updated existing BillingTransaction %s (fields: %s).",
                obj.id,
                ", ".join(update_fields),
            )

        return obj

    @staticmethod
    @transaction.atomic
    def handle_refund(charge):
        """
        Applies a Stripe `charge.refunded` event to the matching
        BillingTransaction (matched by invoice id, then payment_intent
        id). If no match is found, creates a standalone record flagged
        for manual review rather than silently dropping the refund.
        """
        payment_intent_id = charge.get("payment_intent")
        invoice_id = charge.get("invoice")
        amount_refunded = charge.get("amount_refunded") or 0
        amount_captured = charge.get("amount_captured") or charge.get("amount") or 0

        txn = None
        if invoice_id:
            txn = (
                BillingTransaction.objects.select_for_update()
                .filter(stripe_invoice_id=invoice_id)
                .first()
            )
        if not txn and payment_intent_id:
            txn = (
                BillingTransaction.objects.select_for_update()
                .filter(stripe_payment_intent_id=payment_intent_id)
                .first()
            )

        if not txn:
            logger.warning(
                "charge.refunded for charge %s (invoice=%s, payment_intent=%s) "
                "has no matching BillingTransaction — creating a standalone "
                "record for manual review.",
                charge.get("id"),
                invoice_id,
                payment_intent_id,
            )
            BillingTransaction.objects.create(
                source=BillingTransactionSource.INDIVIDUAL,
                transaction_type=BillingTransactionType.OTHER,
                status=(
                    BillingTransactionStatus.REFUNDED
                    if amount_refunded >= amount_captured
                    else BillingTransactionStatus.PARTIALLY_REFUNDED
                ),
                billing_method="STRIPE",
                amount_cents=amount_captured,
                refunded_amount_cents=amount_refunded,
                currency=charge.get("currency", "usd"),
                stripe_charge_id=charge.get("id"),
                stripe_payment_intent_id=payment_intent_id,
                stripe_invoice_id=invoice_id,
                receipt_url=charge.get("receipt_url"),
                description=(
                    "Refund received with no matching local billing "
                    "transaction — needs manual reconciliation."
                ),
                occurred_at=timezone.now(),
            )
            return

        txn.stripe_charge_id = charge.get("id") or txn.stripe_charge_id
        txn.refunded_amount_cents = amount_refunded
        reference_amount = txn.amount_cents or amount_captured
        txn.status = (
            BillingTransactionStatus.REFUNDED
            if amount_refunded >= reference_amount
            else BillingTransactionStatus.PARTIALLY_REFUNDED
        )
        update_fields = [
            "stripe_charge_id",
            "refunded_amount_cents",
            "status",
            "updated_at",
        ]
        if not txn.receipt_url and charge.get("receipt_url"):
            txn.receipt_url = charge.get("receipt_url")
            update_fields.append("receipt_url")
        txn.save(update_fields=update_fields)
        logger.info(
            "BillingTransaction %s refunded: %d/%d cents (status=%s).",
            txn.id,
            amount_refunded,
            reference_amount,
            txn.status,
        )
