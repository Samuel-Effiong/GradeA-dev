from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from billing.models import BetaProfile, CreditWallet, PlanType, SubscriptionPlan
from billing.services import SubscriptionService
from users.models import CustomUser, Settings


@receiver([post_save, post_delete], sender=CustomUser)
@receiver([post_save, post_delete], sender=Settings)
def clear_user_cache(sender, instance, **kwargs):
    if hasattr(cache, "delete_pattern"):
        cache.delete_pattern("*superadmin*")
        cache.delete_pattern("*schooladmin*")
        cache.delete_pattern("*teacheradmin*")
        cache.delete_pattern("*studentadmin*")
        cache.delete_pattern("*user*")
        cache.delete_pattern("*school*")
        cache.delete_pattern("*course*")
        cache.delete_pattern("*studentcourse*")
        cache.delete_pattern("*settings*")


@receiver(post_save, sender=CustomUser)
def create_settings(sender, instance, created, **kwargs):
    # Create default settings
    Settings.objects.get_or_create(user=instance)

    # Create empty wallet
    CreditWallet.objects.get_or_create(user=instance)

    if created:
        beta_plan = SubscriptionPlan.objects.filter(name=PlanType.BETA).first()

        if beta_plan:
            initial_credits = beta_plan.monthly_credits
            BetaProfile.objects.get_or_create(
                user=instance, defaults={"initial_beta_credits": initial_credits}
            )
            SubscriptionService.activate_subscription(instance, beta_plan)
