"Eyes to see"

import logging

from django.conf import settings
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from AutoGrader.cache_generation import (
    SCOPE_ANY_USER,
    SCOPE_GLOBAL,
    SCOPE_USER,
    bump_many,
)
from AutoGrader.cache_utils import delete_cache_patterns
from billing.context import get_license_invitation_context
from billing.models import BetaProfile, CreditWallet, PlanType, SubscriptionPlan
from billing.services import SubscriptionService
from users.models import CustomUser, Settings

logger = logging.getLogger(__name__)


@receiver([post_save, post_delete], sender=CustomUser)
@receiver([post_save, post_delete], sender=Settings)
def clear_user_cache(sender, instance, **kwargs):
    # H-1 stage 2: bump only THIS user's generation. The wildcard "*user*"
    # below matches 29 of the project's 35 cache families, which is why
    # every CustomUser/Settings save is currently a de-facto full flush.
    # The bump is the targeted replacement; both run until stage 3.
    user_id = getattr(instance, "user_id", None) or instance.pk
    # `anyusr` backs super-admin/dashboard/teachers, whose dependency is the
    # CustomUser table and nothing else; `global` backs the superadmin
    # dashboards that aggregate across everything.
    bump_many([(SCOPE_USER, user_id), (SCOPE_ANY_USER, None), (SCOPE_GLOBAL, None)])
    # Routed through the project's shared helper rather than calling
    # `cache.delete_pattern` directly. This is a post_save/post_delete
    # receiver, so Django runs it inside the caller's transaction: an
    # unguarded call meant a Redis blip failed the user save itself, even
    # though saving a user needs nothing from Redis. The helper treats
    # invalidation as best-effort and logs rather than raising.
    delete_cache_patterns(
        "*superadmin*",
        "*schooladmin*",
        "*teacheradmin*",
        "*studentadmin*",
        "*user*",
        "*school*",
        "*course*",
        "*studentcourse*",
        "*settings*",
    )


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
            "(user type '%s' is not eligible for individual trials).",
            user.email,
            user.user_type,
        )
        return

    if get_license_invitation_context():
        logger.info(
            "Skipping automatic trial for user %s (created during license invitation).",
            user.email,
        )
        return

    # Check environment variable for which plan to use
    use_beta_plan = settings.USE_BETA_PLAN_ON_SIGNUP

    if use_beta_plan:
        logger.debug(
            "Activating Beta plan for user %s based on env variable", user.email
        )
        beta_plan = SubscriptionPlan.objects.filter(name=PlanType.BETA).first()

        if beta_plan:
            initial_credits = beta_plan.monthly_credits
            BetaProfile.objects.get_or_create(
                user=user, defaults={"initial_beta_credits": initial_credits}
            )
            try:
                SubscriptionService.activate_subscription(user, beta_plan)
                logger.info(
                    "✓ Beta plan successfully activated for user %s.", user.email
                )
            except Exception as exc:
                logger.error(
                    "Failed to activate beta plan for user %s: %s",
                    user.email,
                    str(exc),
                    exc_info=True,
                )
        else:
            logger.warning(
                "BETA plan not found in database for user %s. Skipping activation.",
                user.email,
            )

    else:
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
