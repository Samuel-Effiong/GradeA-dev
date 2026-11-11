"""
H-34: a super admin locks and unlocks a user's sign-in.

The lock is its own state, `CustomUser.admin_locked_at`, not `is_active`:
the mailbox roads (verify, Google) switch a pending account on, rosters show
is_active, and an unlock must never switch on a pending account. Locking
stamps the column and raises `token_epoch` (every live session ends) in one
UPDATE; the sign-in doors then refuse the account until a super admin
unlocks it. Unlock clears the admin lock and the 5-try login lock
(`failed_login_attempts`, `locked_until`) and leaves the epoch alone, so the
sessions the lock ended stay ended.

Nobody locks themselves or a super admin (`is_superuser` OR user_type
SUPER_ADMIN). Both functions change only the rows that change ("only rows
that change", as the deactivate action counts) and return them, so each
caller writes one ACCOUNT_LOCK / ACCOUNT_UNLOCK per changed user.
"""

from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from audit.enums import ReasonCode
from AutoGrader.reason_codes import REASON_CODES

from .models import CustomUser, UserTypes
from .signals import invalidate_user_caches

NO_SCHOOL = "no_school"


def is_super_admin_account(user):
    """Either flag is enough to make an account one nobody may lock."""
    return bool(user.is_superuser or user.user_type == UserTypes.SUPER_ADMIN)


def may_lock(actor, target):
    return target.pk != actor.pk and not is_super_admin_account(target)


def _lockable(queryset, actor):
    """The rows of `queryset` a lock or unlock may touch, locked for update."""
    return (
        CustomUser.objects.select_for_update()
        .filter(pk__in=queryset.values("pk"))
        .exclude(pk=actor.pk)
        .exclude(is_superuser=True)
        .exclude(user_type=UserTypes.SUPER_ADMIN)
        .order_by("pk")
    )


def lock_users(queryset, *, actor):
    """Lock every lockable, not-yet-locked user in `queryset`. Returns the
    users that changed."""
    with transaction.atomic():
        changed = list(
            _lockable(queryset, actor)
            .filter(admin_locked_at__isnull=True)
            .only("pk", "school_id")
        )
        if changed:
            CustomUser.objects.filter(pk__in=[u.pk for u in changed]).update(
                admin_locked_at=timezone.now(),
                token_epoch=F("token_epoch") + 1,
            )
    invalidate_user_caches(changed)
    return changed


def unlock_users(queryset, *, actor):
    """Clear both locks and the failed count on every lockable user in
    `queryset` that has something to clear. Never touches is_active or the
    token epoch. Returns [(user, metadata)] for the users that changed."""
    now = timezone.now()
    with transaction.atomic():
        rows = list(
            _lockable(queryset, actor)
            .filter(
                Q(admin_locked_at__isnull=False)
                | Q(failed_login_attempts__gt=0)
                | Q(locked_until__isnull=False)
            )
            .only(
                "pk",
                "school_id",
                "admin_locked_at",
                "failed_login_attempts",
                "locked_until",
            )
        )
        if rows:
            CustomUser.objects.filter(pk__in=[u.pk for u in rows]).update(
                admin_locked_at=None,
                failed_login_attempts=0,
                locked_until=None,
            )
    invalidate_user_caches(rows)
    return [
        (
            user,
            {
                "had_admin_lock": user.admin_locked_at is not None,
                "had_login_lock": bool(user.locked_until and user.locked_until > now),
                "cleared_failed_attempts": user.failed_login_attempts,
            },
        )
        for user in rows
    ]


def _reaches_a_school(user):
    """SM R1: a user with a school, or a student with a non-WITHDRAWN
    enrollment in a course whose teacher has a school."""
    if user.school_id:
        return True
    if user.user_type != UserTypes.STUDENT:
        return False
    # Local import: classrooms imports users.serializers, which imports this.
    from classrooms.models import EnrollmentStatusType, StudentCourse

    return (
        StudentCourse.objects.filter(
            student=user, course__teacher__school__isnull=False
        )
        .exclude(enrollment_status=EnrollmentStatusType.WITHDRAWN)
        .exists()
    )


def lock_message(user):
    """The approved sentence for this user (owner texts #1 and #2)."""
    spec = REASON_CODES[ReasonCode.ACCOUNT_LOCKED_BY_ADMIN]
    return spec.template(None if _reaches_a_school(user) else NO_SCHOOL)
