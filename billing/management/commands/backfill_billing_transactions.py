"""
One-off backfill: populates BillingTransaction from historical data —
1. StripeEvent payloads already stored locally.
2. LicenseBillingRecord rows without a paired BillingTransaction.

Reads only — never re-runs business logic (no credits re-granted, no
subscriptions re-activated). Idempotent: safe to re-run.

Usage:
    python manage.py backfill_billing_transactions [--dry-run]
"""

import logging

from django.core.management.base import BaseCommand

from billing.billing_transaction_service import BillingTransactionService
from billing.models import (
    BillingTransactionMethod,
    BillingTransactionSource,
    BillingTransactionStatus,
    BillingTransactionType,
    LicenseBillingRecord,
    LicenseBillingRecordType,
    LicenseSubscription,
    StripeEvent,
    UserSubscription,
)
from users.models import CustomUser

logger = logging.getLogger(__name__)

LICENSE_RECORD_TYPE_TO_TXN_TYPE = {
    LicenseBillingRecordType.CREATED_OFFLINE: BillingTransactionType.LICENSE_OFFLINE_RENEWAL,
    LicenseBillingRecordType.RENEWED_OFFLINE: BillingTransactionType.LICENSE_OFFLINE_RENEWAL,
    LicenseBillingRecordType.PLAN_CHANGE_OFFLINE: BillingTransactionType.LICENSE_OFFLINE_PLAN_CHANGE,
    LicenseBillingRecordType.MANUAL_OVERAGE_GRANT: BillingTransactionType.LICENSE_OFFLINE_MANUAL_OVERAGE_GRANT,
    # SEATS_CHANGE_OFFLINE / CONVERTED_TO_STRIPE / CONVERTED_TO_OFFLINE carry
    # no charge of their own — deliberately not mapped, skipped below.
}


class Command(BaseCommand):
    help = "Backfill BillingTransaction rows from historical StripeEvent and LicenseBillingRecord data."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        stripe_count = self._backfill_from_stripe_events(dry_run)
        offline_count = self._backfill_from_license_billing_records(dry_run)
        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill complete{' (dry run)' if dry_run else ''}: "
                f"{stripe_count} Stripe-derived, {offline_count} offline transaction(s) processed."
            )
        )

    # -- StripeEvent -> BillingTransaction --------------------------------

    def _backfill_from_stripe_events(self, dry_run):
        events = StripeEvent.objects.filter(
            event_type__in=[
                "checkout.session.completed",
                "invoice.payment_succeeded",
                "invoice.payment_failed",
                "charge.refunded",
            ]
        ).order_by("processed_at")

        count = 0
        for event in events:
            try:
                if self._process_stripe_event(event, dry_run):
                    count += 1
            except Exception:
                logger.exception(
                    "Failed to backfill from StripeEvent %s (%s).",
                    event.id,
                    event.event_type,
                )
        return count

    def _process_stripe_event(self, event, dry_run):
        payload = event.payload or {}
        obj = payload.get("object", payload)

        if event.event_type == "checkout.session.completed":
            return self._backfill_checkout_session(obj, event, dry_run)
        if event.event_type == "invoice.payment_succeeded":
            return self._backfill_invoice(obj, event, dry_run, succeeded=True)
        if event.event_type == "invoice.payment_failed":
            return self._backfill_invoice(obj, event, dry_run, succeeded=False)
        if event.event_type == "charge.refunded":
            return self._backfill_refund(obj, dry_run)
        return False

    def _backfill_checkout_session(self, session, event, dry_run):
        metadata = session.get("metadata", {}) or {}
        flow = metadata.get("flow")

        flow_to_type = {
            "individual_checkout": BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE,
            "individual_subscribe": BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE,
            "trial_to_paid": BillingTransactionType.INDIVIDUAL_TRIAL_CONVERSION_CHARGE,
            "individual_upgrade_checkout": BillingTransactionType.INDIVIDUAL_UPGRADE_CHARGE,
            "overage_block_purchase_checkout": BillingTransactionType.INDIVIDUAL_OVERAGE_PURCHASE,
            "license_create": BillingTransactionType.LICENSE_INITIAL_CHARGE,
        }
        if flow not in flow_to_type:
            return False  # individual_trial / license_convert_to_stripe: no charge

        amount_total = session.get("amount_total")
        if amount_total is None:
            return False

        source = (
            BillingTransactionSource.LICENSE
            if flow == "license_create"
            else BillingTransactionSource.INDIVIDUAL
        )

        user = None
        user_id = metadata.get("user_id") or metadata.get("admin_user_id")
        if user_id:
            user = CustomUser.objects.filter(id=user_id).first()

        license_subscription = None
        if flow == "license_create":
            school_id = metadata.get("school_id")
            if school_id:
                license_subscription = (
                    LicenseSubscription.objects.filter(school_id=school_id)
                    .order_by("-created_at")
                    .first()
                )

        user_subscription = None
        if source == BillingTransactionSource.INDIVIDUAL and user:
            user_subscription = UserSubscription.objects.filter(
                user=user, is_active=True
            ).first()

        if dry_run:
            self.stdout.write(
                f"[dry-run] {flow_to_type[flow]} — session {session.get('id')}"
            )
            return True

        BillingTransactionService.record(
            source=source,
            transaction_type=flow_to_type[flow],
            status=BillingTransactionStatus.PAID,
            billing_method=BillingTransactionMethod.STRIPE,
            amount_cents=amount_total,
            currency=session.get("currency", "usd"),
            user=user,
            user_subscription=user_subscription,
            license_subscription=license_subscription,
            stripe_invoice_id=session.get("invoice"),
            stripe_payment_intent_id=session.get("payment_intent"),
            stripe_checkout_session_id=session.get("id"),
            stripe_subscription_id=session.get("subscription"),
            description=f"Backfilled from checkout.session.completed ({flow})",
            occurred_at=event.processed_at,
        )
        return True

    def _backfill_invoice(self, invoice, event, dry_run, succeeded):
        subscription_id = invoice.get("subscription")
        if not subscription_id:
            parent = invoice.get("parent") or {}
            subscription_id = (parent.get("subscription_details") or {}).get(
                "subscription"
            )
        if not subscription_id:
            return False

        user_sub = UserSubscription.objects.filter(
            stripe_subscription_id=subscription_id
        ).first()
        license_sub = None
        if not user_sub:
            license_sub = LicenseSubscription.objects.filter(
                stripe_subscription_id=subscription_id
            ).first()
        if not user_sub and not license_sub:
            return False

        billing_reason = invoice.get("billing_reason")
        amount = invoice.get("amount_paid") if succeeded else invoice.get("amount_due")

        if user_sub:
            source = BillingTransactionSource.INDIVIDUAL
            txn_type = (
                BillingTransactionType.INDIVIDUAL_UPGRADE_CHARGE
                if billing_reason == "subscription_update"
                else BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE
            )
            user = user_sub.user
            license_subscription = None
        else:
            source = BillingTransactionSource.LICENSE
            txn_type = (
                BillingTransactionType.LICENSE_PLAN_CHANGE_CHARGE
                if billing_reason == "subscription_update"
                else BillingTransactionType.LICENSE_SUBSCRIPTION_CHARGE
            )
            user = None
            license_subscription = license_sub
            user_sub = None

        if dry_run:
            self.stdout.write(f"[dry-run] {txn_type} — invoice {invoice.get('id')}")
            return True

        BillingTransactionService.record(
            source=source,
            transaction_type=txn_type,
            status=(
                BillingTransactionStatus.PAID
                if succeeded
                else BillingTransactionStatus.FAILED
            ),
            billing_method=BillingTransactionMethod.STRIPE,
            amount_cents=amount or 0,
            currency=invoice.get("currency", "usd"),
            user=user,
            user_subscription=user_sub,
            license_subscription=license_subscription,
            stripe_invoice_id=invoice.get("id"),
            stripe_subscription_id=subscription_id,
            description=f"Backfilled from invoice.payment_{'succeeded' if succeeded else 'failed'} ({billing_reason})",
            occurred_at=event.processed_at,
        )
        return True

    def _backfill_refund(self, charge, dry_run):
        if dry_run:
            self.stdout.write(f"[dry-run] refund for charge {charge.get('id')}")
            return True
        BillingTransactionService.handle_refund(charge)
        return True

    # -- LicenseBillingRecord -> BillingTransaction ------------------------

    def _backfill_from_license_billing_records(self, dry_run):
        records = LicenseBillingRecord.objects.filter(
            billing_transactions__isnull=True,
            record_type__in=LICENSE_RECORD_TYPE_TO_TXN_TYPE.keys(),
        ).select_related("license_subscription", "license_subscription__school")

        count = 0
        for record in records:
            txn_type = LICENSE_RECORD_TYPE_TO_TXN_TYPE[record.record_type]
            if dry_run:
                self.stdout.write(
                    f"[dry-run] {txn_type} — LicenseBillingRecord {record.id}"
                )
                count += 1
                continue

            BillingTransactionService.record(
                source=BillingTransactionSource.LICENSE,
                transaction_type=txn_type,
                status=BillingTransactionStatus.MANUAL,
                billing_method=BillingTransactionMethod.OFFLINE,
                amount_cents=record.amount_paid_cents or 0,
                license_subscription=record.license_subscription,
                license_billing_record=record,
                performed_by=record.performed_by,
                description=record.notes or record.get_record_type_display(),
                occurred_at=record.created_at,
            )
            count += 1
        return count
