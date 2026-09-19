import logging
from decimal import ROUND_HALF_UP, Decimal

from django.core.cache import cache
from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from assignments.models import Assignment
from AutoGrader.cache_generation import (
    SCOPE_ANY_SCHOOL,
    SCOPE_COURSE,
    SCOPE_GLOBAL,
    SCOPE_SCHOOL,
    SCOPE_USER,
    bump_many,
)
from classrooms.models import Course, School, Session, StudentCourse, Topic
from students.models import StudentSubmission

logger = logging.getLogger(__name__)

#: What "the cache is unreachable" looks like coming out of django-redis.
#: Mirrors AutoGrader.dispatch.BROKER_UNAVAILABLE_ERRORS, which classifies
#: the same failure for Celery's broker: redis-py's own errors at the
#: bottom, plus the socket-level builtins either layer may surface a raw
#: connection failure as. Deliberately NOT `except Exception` - a bug in an
#: invalidation call (a bad pattern, a typo) should still fail loudly in
#: tests rather than be swallowed as if it were an outage.
#: Set once the process has reported an invalidation-incapable backend.
_warned_backend_lacks_delete_pattern = False

CACHE_UNAVAILABLE_ERRORS = (
    RedisConnectionError,
    RedisTimeoutError,
    ConnectionError,
    TimeoutError,
)


def delete_cache_patterns(*patterns):
    """Invalidate cached list/detail responses by key pattern.

    These calls are what revokes a withdrawn student's cached course list,
    so both ways they can fail need handling and neither may be silent:

    * The backend has no `delete_pattern` at all. It is a django-redis
      extension, not part of Django's cache API, so a backend swap or a
      misconfigured environment would quietly turn a security boundary into
      a stale-cache window. Warn ONCE - this is a static property of the
      configured backend, not a per-event condition, so warning on every
      save would bury it in its own noise (and floods the test log, where
      LocMem is the backend).
    * The call raises because Redis is unreachable. Receivers run inside
      the caller's transaction, so an escaping exception fails the write
      itself - see the comment on the except clause.
    """
    if not hasattr(cache, "delete_pattern"):
        global _warned_backend_lacks_delete_pattern
        if not _warned_backend_lacks_delete_pattern:
            _warned_backend_lacks_delete_pattern = True
            logger.warning(
                "Cache backend %s has no delete_pattern(); wildcard cache "
                "invalidation is disabled for this process. Cached responses "
                "will serve stale data until they expire, including for users "
                "whose access was just revoked.",
                type(cache).__name__,
            )
        return

    for pattern in patterns:
        try:
            cache.delete_pattern(pattern)
        except CACHE_UNAVAILABLE_ERRORS:
            # Every caller here is a post_save/post_delete receiver, which
            # Django runs INSIDE the caller's transaction - so an exception
            # escaping this loop doesn't just skip an invalidation, it
            # fails the write that triggered it. A Redis blip would have
            # made enrolling a student impossible, even though enrollment
            # needs nothing from Redis.
            #
            # The trade is deliberate and one-directional: a missed
            # invalidation serves stale reads until the entry expires
            # (CACHE_TTL, 5 minutes), which for a revocation is a bounded
            # window; letting it raise loses the write permanently. Logged
            # at ERROR because the stale window includes users whose access
            # was just revoked, so it needs to be alertable, not merely
            # visible.
            logger.error(
                "Cache invalidation failed for pattern %s; entries matching "
                "it will serve stale data until they expire. Access changes "
                "made now may not take effect immediately.",
                pattern,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# H-1 stage 2: generation bumps.
#
# These run ALONGSIDE the wildcard `delete_cache_patterns` calls above, not
# instead of them. Both mechanisms are live during the migration so that a
# read site can be moved to versioned keys one at a time, and so that
# reverting a read site restores working invalidation without a deploy of
# this file. Removing the wildcard receivers is stage 3, gated on proving
# every family is covered - see docs/H1_CACHE_INVALIDATION_DESIGN.md.
#
# Bumping is deliberately cheap and total: a bump that is not yet read by
# anything costs one INCR and invalidates nothing, whereas a MISSING bump
# after a read site migrates would serve permanently stale data. When in
# doubt these bump more, not less.
# ---------------------------------------------------------------------------


def _course_scopes(course):
    """Entities whose cached responses a course-shaped change can affect."""
    if course is None:
        return []
    teacher = getattr(course, "teacher", None)
    return [
        (SCOPE_COURSE, course.pk),
        (SCOPE_USER, getattr(course, "teacher_id", None)),
        (SCOPE_SCHOOL, getattr(teacher, "school_id", None) if teacher else None),
    ]


@receiver([post_save, post_delete], sender=School)
def clear_school_cache(sender, instance, **kwargs):
    # `anysch` backs super-admin/dashboard/schools, whose only dependency
    # is the School table.
    bump_many(
        [
            (SCOPE_SCHOOL, instance.pk),
            (SCOPE_ANY_SCHOOL, None),
            (SCOPE_GLOBAL, None),
        ]
    )
    delete_cache_patterns(
        "*superadmin*",
        "*schooladmin*",
        "schools:*",
        "courses:*",
        "sessions:*",
    )


@receiver([post_save, post_delete], sender=Session)
def clear_session_cache(sender, instance, **kwargs):
    bump_many(
        [
            (SCOPE_USER, instance.teacher_id),
            (SCOPE_SCHOOL, instance.school_id),
            (SCOPE_GLOBAL, None),
        ]
    )
    delete_cache_patterns(
        "*superadmin*",
        "*schooladmin*",
        "*school*",
        "sessions:*",
        "courses:*",
        "assignments:*",
        "studentsubmissions:*",
    )


@receiver([post_save, post_delete], sender=Course)
def clear_course_cache(sender, instance, **kwargs):
    bump_many(_course_scopes(instance) + [(SCOPE_GLOBAL, None)])
    delete_cache_patterns(
        "*superadmin*",
        "*schooladmin*",
        "*teacheradmin*",
        "*studentadmin*",
        "*user*",
        "*school*",
        "sessions:*",
        "courses:*",
        "assignments:*",
        "studentsubmissions:*",
        "studentcourses:*",
        "topics:*",
    )


@receiver(post_save, sender=Course)
def notify_admins_of_teacher_first_course(sender, instance, created, **kwargs):
    """When a teacher creates their first-ever course, queue a best-effort
    milestone notification to their school's opted-in admins."""
    if not created:
        return

    teacher = instance.teacher
    if not teacher or not teacher.school_id:
        return

    if Course.objects.filter(teacher=teacher).count() != 1:
        return

    course_id = str(instance.id)

    def enqueue():
        from AutoGrader.dispatch import safe_delay
        from dashboard.tasks import send_teacher_first_course_milestone_alert

        safe_delay(send_teacher_first_course_milestone_alert, course_id)

    transaction.on_commit(enqueue)


@receiver([post_save, post_delete], sender=StudentCourse)
def clear_student_course_cache(sender, instance, **kwargs):
    bump_many(
        [(SCOPE_USER, instance.student_id), (SCOPE_GLOBAL, None)]
        + _course_scopes(getattr(instance, "course", None))
    )
    delete_cache_patterns(
        "*superadmin*",
        "*schooladmin*",
        "*teacheradmin*",
        "*studentadmin*",
        "*user*",
        "*school*",
        "sessions:*",
        "courses:*",
        "studentcourses:*",
        "assignments:*",
        "studentsubmissions:*",
    )


@receiver([post_save, post_delete], sender=Topic)
def clear_topic_cache(sender, instance, **kwargs):
    bump_many(
        _course_scopes(getattr(instance, "course", None)) + [(SCOPE_GLOBAL, None)]
    )
    delete_cache_patterns(
        "*superadmin*",
        "*schooladmin*",
        "*teacheradmin*",
        "*studentadmin*",
        "*user*",
        "topics:*",
        "courses:*",
        "assignments:*",
    )


def _course_id_for_assignment(assignment_id):
    return (
        Assignment.objects.filter(id=assignment_id)
        .values_list("course_id", flat=True)
        .first()
    )


def compute_final_grade(student_id, course_id):
    """The final grade the student's graded work implies, or None.

    A points-weighted average across all graded submissions:
    sum(score) / sum(points) * 100, clamped to 0-100 and rounded to 2dp.
    Weighted (not a plain mean of percentages) so a 100-point exam counts
    more than a 5-point quiz.

    Pure read: `_recalculate_final_grade` writes the result, and the
    `recalculate_final_grades` command previews it for existing rows. Both
    must agree, so this is the only place the formula lives.
    """
    # Submissions graded before `max_points` was stored have it NULL.
    # Weight them by the assignment's total_points - the same fallback the
    # submission serializers display - rather than dropping them: filtering
    # on `max_points > 0` alone silently left a graded 4/5 out of a
    # student's final grade (H-33). A stored max_points always wins; a row
    # with no maximum anywhere still can't be weighted.
    totals = (
        StudentSubmission.objects.filter(
            student_id=student_id,
            assignment__course_id=course_id,
            graded_at__isnull=False,
            score__isnull=False,
        )
        .annotate(points=Coalesce("max_points", "assignment__total_points"))
        .filter(points__gt=0)
        .aggregate(total_score=Sum("score"), total_max_points=Sum("points"))
    )

    total_score = totals["total_score"]
    total_max_points = totals["total_max_points"]

    if not total_max_points:
        return None
    raw_grade = (total_score / total_max_points) * 100
    # Clamp to the documented 0-100 scale so bad upstream data (extra
    # credit pushing a score over 100%, a negative adjustment) can't
    # silently fall outside every grade band in grade-distribution
    # reporting.
    clamped = max(Decimal("0"), min(Decimal("100"), raw_grade))
    return clamped.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _recalculate_final_grade(student_id, course_id, *, allow_clear=True):
    """
    Recomputes a student's final grade for a course (see
    `compute_final_grade`) and stores it if it changed. Returns
    (old, new, written) when the value differs, None when it doesn't.

    `allow_clear=False` refuses to turn an existing grade into no grade:
    the row is left as it is and reported with written=False. The
    receivers keep the default (a deleted or un-graded submission really
    does remove the grade); the repair command passes False so a grade a
    student and teacher have already seen never silently disappears.

    Runs after every submission save *and* delete so `final_grade` can't
    drift from submissions that were resubmitted, ungraded, or removed
    after an earlier grade was recorded. Rows last written before the
    current formula existed are NOT revisited by these receivers; the
    `recalculate_final_grades` management command exists for them.

    Locks the enrollment row for the duration of the aggregate + write.
    Batch grading (grade-all) finishes several submissions for the same
    (student, course) on different workers at nearly the same moment; an
    unlocked read-aggregate-write here let a worker holding a stale
    aggregate win the last write, permanently understating final_grade
    (nothing re-triggers the recalc afterwards). Under the lock, the
    second worker blocks until the first commits and then aggregates
    fresh data, so the last write always reflects every graded
    submission.
    """
    with transaction.atomic():
        enrollment = (
            StudentCourse.objects.select_for_update()
            .filter(student_id=student_id, course_id=course_id)
            .first()
        )
        if enrollment is None:
            return None

        new_final_grade = compute_final_grade(student_id, course_id)
        old = enrollment.final_grade

        if old == new_final_grade:
            return None
        if new_final_grade is None and old is not None and not allow_clear:
            return old, new_final_grade, False
        enrollment.final_grade = new_final_grade
        enrollment.save(update_fields=["final_grade"])
        return old, new_final_grade, True


@receiver(post_save, sender=StudentSubmission)
def update_student_course_final_grade(sender, instance, **kwargs):
    """When a submission is saved, recalculate the student's final grade."""
    course_id = _course_id_for_assignment(instance.assignment_id)
    if course_id is None:
        return
    _recalculate_final_grade(instance.student_id, course_id)


@receiver(post_delete, sender=StudentSubmission)
def recalculate_final_grade_on_submission_delete(sender, instance, **kwargs):
    """
    A deleted submission's contribution to the final grade must be removed
    too, otherwise final_grade keeps counting work that no longer exists.
    """
    course_id = _course_id_for_assignment(instance.assignment_id)
    if course_id is None:
        return
    _recalculate_final_grade(instance.student_id, course_id)
