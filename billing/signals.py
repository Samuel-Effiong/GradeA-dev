# billing/signals.py

import logging

from django.db import transaction
from django.db.models import F
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import CreditUsageLog, LicenseSubscription, SchoolCreditAllocation

logger = logging.getLogger(__name__)


@receiver(post_save, sender=CreditUsageLog)
def update_license_consumption(sender, instance, created, **kwargs):
    """
    When a credit usage log is created, check if the user is under a license
    and update the license's total_credits_consumed accordingly.
    """
    if not created:
        return

    user = instance.wallet.user

    # Check if the user has an active license allocation
    allocation = (
        SchoolCreditAllocation.objects.filter(
            user=user,
            is_active=True,
            is_admin_allocation=False,
            license_subscription__is_active=True,
        )
        .select_related("license_subscription")
        .first()
    )

    if not allocation:
        return

    license_sub = allocation.license_subscription
    amount = instance.amount

    # Atomically increment the consumption counter
    with transaction.atomic():
        # Lock the license row
        license_sub = LicenseSubscription.objects.select_for_update().get(
            pk=license_sub.pk
        )
        license_sub.total_credits_consumed = F("total_credits_consumed") + amount
        license_sub.save(update_fields=["total_credits_consumed", "updated_at"])

    logger.debug(
        "Updated license %s total_credits_consumed by %d (now %d)",
        license_sub.id,
        amount,
        license_sub.total_credits_consumed,
    )
