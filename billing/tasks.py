from celery import shared_task
from django.utils import timezone

from .models import UserSubscription
from .services import SubscriptionService


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
