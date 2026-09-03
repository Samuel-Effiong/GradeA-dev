"""
One-off backfill: populates BillingTransaction.receipt_url for existing
rows that have a Stripe reference but no receipt link yet — e.g. rows
created before the receipt_url field existed.

Reads existing BillingTransaction rows and calls the Stripe API to
resolve each one's receipt link; never touches business logic (no
credits re-granted, no subscriptions re-activated). Idempotent: safe to
re-run, only ever fills in rows still missing a receipt_url.

Usage:
    python manage.py backfill_receipt_urls [--dry-run]
"""

import logging

from django.core.management.base import BaseCommand
from django.db.models import Q

from billing.models import BillingTransaction
from billing.stripe_service import resolve_stripe_receipt_url

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Backfill BillingTransaction.receipt_url for rows missing it."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        transactions = BillingTransaction.objects.filter(
            receipt_url__isnull=True
        ).filter(
            Q(stripe_invoice_id__isnull=False)
            | Q(stripe_charge_id__isnull=False)
            | Q(stripe_payment_intent_id__isnull=False)
        )

        resolved_count = 0
        failed_count = 0

        for txn in transactions:
            receipt_url = resolve_stripe_receipt_url(
                invoice_id=txn.stripe_invoice_id,
                charge_id=txn.stripe_charge_id,
                payment_intent_id=txn.stripe_payment_intent_id,
            )

            if not receipt_url:
                failed_count += 1
                continue

            resolved_count += 1
            if not dry_run:
                txn.receipt_url = receipt_url
                txn.save(update_fields=["receipt_url", "updated_at"])

        self.stdout.write(
            self.style.SUCCESS(
                f"Backfill complete{' (dry run)' if dry_run else ''}: "
                f"{resolved_count} resolved, {failed_count} could not be resolved."
            )
        )
