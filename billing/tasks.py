import logging

from celery import shared_task
from django.utils import timezone

from .models import CreditBucket, UserSubscription
from .services import SubscriptionService

logger = logging.getLogger(__name__)


@shared_task
def process_subscription_renewals():
    """Process subscription renewals for all active subscriptions."""

    now = timezone.now()
    expired_subs = UserSubscription.objects.filter(
        is_active=True, billing_cycle_end__lte=now
    )

    for sub in expired_subs:
        if sub.auto_renew or sub.pending_plan:
            SubscriptionService.process_rollover_and_renewal(sub)

        else:
            # If no renewal and no pending plan, deactivate the subscription
            sub.is_active = False
            sub.save()


@shared_task
def cleanup_expired_credit_buckets():
    """
    Finds all buckets that have expired but haven't been processed,
    and formalizes their expiration in the ledger.
    """

    now = timezone.now()

    # Look for buckets were expires_at is in the past AND they aren't marked as processed
    # use filter for total_credits > used_credits to avoid unnecessary logs for empty buckets
    expired_buckets = CreditBucket.objects.filter(
        expires_at__lte=now,
        is_processed=False,
    ).select_related("wallet__user")

    total_expired_count = 0
    total_value_lost = 0

    for bucket in expired_buckets:
        try:
            value_lost = SubscriptionService.expire_bucket(bucket)
            total_expired_count += 1
            total_value_lost += value_lost
        except Exception as e:
            logger.error(f"Failed to reconcile expired bucket {bucket.id}: {str(e)}")
            continue

    summary = f"Processed {total_expired_count} buckets. Total credits expired: {total_value_lost}"
    logger.info(summary)

    return summary
