"Eyes to see"

import logging

from django.conf import settings
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from AutoGrader.cache_generation import (
    SCOPE_ANY_USER,
    SCOPE_GLOBAL,
    SCOPE_SCHOOL,
    SCOPE_USER,
    bump_many,
)
from AutoGrader.cache_utils import delete_cache_patterns
from billing.context import get_license_invitation_context
from billing.models import BetaProfile, CreditWallet, PlanType, SubscriptionPlan
from billing.services import SubscriptionService
from users.models import CustomUser, Settings

logger = logging.getLogger(__name__)

# CustomUser fields that OTHER users' cached views display, count or filter
# on: the school admin's teacher list and summary, a teacher's roster, course
# and submission lists (H-1 Stage 3 item 7). A change to anything else -
# password, lockout counters, activation tokens, bio - is visible only to the
# user themself, so it moves only their own generation.
VIEWER_VISIBLE_USER_FIELDS = (
    "first_name",
    "middle_name",
    "last_name",
    "email",
    "is_active",
    "user_type",
    "school",
    "profile_image",
    "profile_image_url",
)
_VISIBLE_ATTNAMES = tuple(
    CustomUser._meta.get_field(name).attname for name in VIEWER_VISIBLE_USER_FIELDS
)
# `save(update_fields=...)` accepts either spelling (`school` / `school_id`).
_VISIBLE_UPDATE_NAMES = frozenset(VIEWER_VISIBLE_USER_FIELDS) | frozenset(
    _VISIBLE_ATTNAMES
)

_PRE_SAVE_STATE = "_cachegen_visible_state_before_save"


def _normalise(value):
    # An empty ImageField reads back as "" from `.values()` but as a FieldFile
    # whose str() is "" from the instance; treat both as "no value".
    return None if value in (None, "") else str(value)


def _visible_update(update_fields):
    return update_fields is None or bool(set(update_fields) & _VISIBLE_UPDATE_NAMES)


def viewer_scopes_for_users(user_ids, school_ids):
    """Generations of the OTHER users' views that display these users.

    * each school the users belong or belonged to - school-admin dashboards
      are keyed on the school;
    * the teacher of every course the users are enrolled in, and that
      teacher's school - a teacher's roster, course and submission lists are
      keyed on the teacher.

    One query regardless of how many users or courses, so the cost of a
    profile change does not grow with enrolments.
    """
    from classrooms.models import Course

    scopes = [(SCOPE_SCHOOL, school_id) for school_id in school_ids if school_id]
    teachers = (
        Course.objects.filter(enrollments__student_id__in=list(user_ids))
        .values_list("teacher_id", "teacher__school_id")
        .distinct()
    )
    for teacher_id, teacher_school_id in teachers:
        scopes.append((SCOPE_USER, teacher_id))
        if teacher_school_id:
            scopes.append((SCOPE_SCHOOL, teacher_school_id))
    return scopes


@receiver(pre_save, sender=CustomUser)
def remember_visible_state_before_save(
    sender, instance, raw=False, update_fields=None, **kwargs
):
    """Snapshot the viewer-visible fields, so post_save can tell a real
    change from a save that touched nothing anyone else sees, and knows the
    PREVIOUS school on a move."""
    setattr(instance, _PRE_SAVE_STATE, None)
    if raw or instance._state.adding or not _visible_update(update_fields):
        return
    setattr(
        instance,
        _PRE_SAVE_STATE,
        CustomUser.objects.filter(pk=instance.pk).values(*_VISIBLE_ATTNAMES).first(),
    )


def _viewer_scopes_for_signal(instance, signal_kwargs):
    if "created" not in signal_kwargs:  # post_delete
        # A deleted user's enrolments and courses are CASCADE-deleted first,
        # and those rows' own receivers already bump the teachers and schools
        # that displayed them. Mutation testing showed a separate pre_delete
        # teacher lookup broke nothing when removed, so it is not repeated
        # here. What no cascade covers is the user's own school listing them:
        # a teacher with no courses has nothing to cascade.
        return [(SCOPE_SCHOOL, instance.school_id)] if instance.school_id else []

    if signal_kwargs["created"]:
        # A brand-new user has no enrolments yet; only their school's
        # dashboards (which list and count its members) can change.
        return [(SCOPE_SCHOOL, instance.school_id)] if instance.school_id else []

    if not _visible_update(signal_kwargs.get("update_fields")):
        return []

    before = getattr(instance, _PRE_SAVE_STATE, None)
    if before is not None and all(
        _normalise(before[attname]) == _normalise(getattr(instance, attname))
        for attname in _VISIBLE_ATTNAMES
    ):
        return []

    # `before is None` means the prior state is unknown (the row vanished, or
    # a raw save): fail towards freshness rather than skip the fan-out.
    previous_school_id = before["school_id"] if before is not None else None
    return viewer_scopes_for_users(
        [instance.pk], [instance.school_id, previous_school_id]
    )


def invalidate_user_caches(users):
    """Invalidate what a change to these users' rows makes stale.

    For write paths that bypass post_save - a `QuerySet.update()` such as
    the admin's bulk activate/deactivate - and so would otherwise refresh
    nothing at all, under either mechanism. One query and one Redis round
    trip for any number of users.
    """
    users = list(users)
    if not users:
        return
    scopes = [(SCOPE_ANY_USER, None), (SCOPE_GLOBAL, None)]
    scopes.extend((SCOPE_USER, user.pk) for user in users)
    scopes.extend(
        viewer_scopes_for_users(
            [user.pk for user in users], [user.school_id for user in users]
        )
    )
    bump_many(list(dict.fromkeys(scopes)))


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
    scopes = [(SCOPE_USER, user_id), (SCOPE_ANY_USER, None), (SCOPE_GLOBAL, None)]
    if sender is CustomUser:
        # H-1 Stage 3 item 7: the user is also DISPLAYED to others - their
        # school's admins and their teachers - whose views are keyed on the
        # school or the teacher, not on this user.
        scopes.extend(_viewer_scopes_for_signal(instance, kwargs))
    # One pipelined round trip; duplicates (a student whose two teachers
    # share a school) are dropped so a count stays one bump per entity.
    bump_many(list(dict.fromkeys(scopes)))
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
