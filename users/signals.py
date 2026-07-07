import logging

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from billing.context import get_license_invitation_context
from billing.models import CreditWallet

# from billing.models import BetaProfile, CreditWallet # PlanType, # SubscriptionPlan
from billing.services import SubscriptionService
from users.models import CustomUser, Settings

logger = logging.getLogger(__name__)


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


# @receiver(post_save, sender=CustomUser)
# def create_settings(sender, instance, created, **kwargs):
#     # Create default settings
#     Settings.objects.get_or_create(user=instance)

#     # Create empty wallet
#     CreditWallet.objects.get_or_create(user=instance)

#     if created and instance.is_beta_eligible():
#         if get_license_invitation_context():
#             return

#         beta_plan = SubscriptionPlan.objects.filter(name=PlanType.BETA).first()

#         if beta_plan:
#             initial_credits = beta_plan.monthly_credits
#             BetaProfile.objects.get_or_create(
#                 user=instance, defaults={"initial_beta_credits": initial_credits}
#             )
#             SubscriptionService.activate_subscription(instance, beta_plan)


@receiver(post_save, sender=CustomUser)
def create_default_settings_and_wallet(sender, instance, created, **kwargs):
    """
    Signal handler for CustomUser post_save.

    On user CREATION (created=True):
    1. Creates default Settings row
    2. Creates empty CreditWallet
    3. AUTO-ACTIVATES FREE TRIAL for teachers (unless license-invited)

    On user UPDATE (created=False):
    - Does nothing (settings/wallet already exist)

    This is the entrypoint for automatic trial activation.

    Args:
        sender: CustomUser model
        instance: The user instance being saved
        created: Boolean, True if this is a new user

    Design notes:
    - Uses atomic operation (trial activation is @transaction.atomic)
    - Catches all exceptions to prevent registration failure
    - Skips trial for non-teacher users (is_beta_eligible check)
    - Skips trial for license-invited users (context check)
    - Logs all actions for debugging
    """

    if not created:
        return

    user = instance

    logger.info(
        "Post-save signal fired for new user %s (ID: %s, type: %s).",
        user.email,
        user.id,
        user.user_type,
    )

    # CREATE SETTINGS & WALLET (always)

    try:
        Settings.objects.get_or_create(user=user)
        logger.debug("Created Settings for user %s", user.email)
    except Exception as exc:
        logger.error(
            "Failed to create Settings for user %s: %s",
            user.email,
            str(exc),
            exc_info=True,
        )

    # Continue even if settings creation fails

    try:
        CreditWallet.objects.get_or_create(user=user)
        logger.debug("Created CreditWallet for user %s", user.email)
    except Exception as exc:
        logger.error(
            "Failed to create CreditWallet for user %s: %s",
            user.email,
            str(exc),
            exc_info=True,
        )
        # Continue even if wallet creation fails

    # AUTO-ACTIVATE FREE TRIAL (teacher-only, non-license users)

    logger.debug("Checking if user %s needs trial activation", user.email)

    # Check if user is a teacher (beta-eligible)
    if not user.is_beta_eligible():
        logger.info(
            "Skipping automatic trial for user %s "
            "(created via license invitation context).",
            user.email,
        )
        return

    if get_license_invitation_context():
        logger.info(
            "Skipping automatic trial for user %s (created during license invitation).",
            user.email,
        )
        return

    # Attempt Trial Activation

    try:
        SubscriptionService.activate_automatic_free_trial(user)
        logger.info(
            "✓ Automatic free trial successfully activated for user %s.",
            user.email,
        )
    except ValueError as exc:
        # Validation error — log but don't fail registration
        # This could happen if STANDARD plan doesn't exist
        logger.warning(
            "Cannot activate automatic trial for user %s (validation): %s",
            user.email,
            str(exc),
        )
    except Exception as exc:
        # Unexpected error — log but don't fail registration
        # Registration should succeed even if trial activation fails
        logger.error(
            "Failed to activate automatic trial for user %s: %s",
            user.email,
            str(exc),
            exc_info=True,
        )
