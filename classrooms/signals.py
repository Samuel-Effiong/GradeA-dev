import logging
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

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

# ---------------------------------------------------------------------------
# Cache invalidation: generation bumps (H-1).
#
# These bumps are the ONLY cache invalidation. The legacy wildcard sweeps
# that ran alongside them were removed in H-1 step 4
# (docs/evidence/H1_STEP4_WILDCARD_REMOVAL_EVIDENCE.md), after every cache
# family was proven fresh on generations alone. A missing bump here is
# therefore stale data for the entry's whole TTL, with nothing to mask it,
# and AutoGrader/tests_no_wildcard_invalidation.py keeps a wildcard from
# coming back as a shortcut.
#
# Bumping is deliberately cheap and total: a bump nothing reads costs one
# INCR and invalidates nothing, whereas a MISSING bump serves stale data.
# When in doubt these bump more, not less.
# ---------------------------------------------------------------------------


def _course_owner_scopes(course):
    """The course itself, its teacher and the teacher's school: a fixed
    three scopes, whatever the size of the class."""
    if course is None:
        return []
    teacher = getattr(course, "teacher", None)
    return [
        (SCOPE_COURSE, course.pk),
        (SCOPE_USER, getattr(course, "teacher_id", None)),
        (SCOPE_SCHOOL, getattr(teacher, "school_id", None) if teacher else None),
    ]


def _course_scopes(course):
    """Entities whose cached responses a course-shaped change can affect.

    H-1 Stage 3 (gap G5): a course or topic change is also visible to every
    enrolled student - the course name and topics appear in their own
    user-keyed caches (course list, dashboards, submissions) - not only to
    the teacher who owns it. One query for the enrolled student ids, folded
    into the caller's own pipelined `bump_many`. That is O(class size) per
    call, which is fine for a rename or a topic edit, a single write.

    An ENROLMENT does not use this: see `clear_student_course_cache`.
    """
    if course is None:
        return []
    scopes = _course_owner_scopes(course)
    scopes.extend(
        (SCOPE_USER, student_id)
        for student_id in StudentCourse.objects.filter(course=course).values_list(
            "student_id", flat=True
        )
    )
    return scopes


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


@receiver([post_save, post_delete], sender=Session)
def clear_session_cache(sender, instance, **kwargs):
    # H-1 Stage 3 (gap G6): a SCHOOL-owned session has `teacher=None` (only
    # an INDIVIDUAL session sets it), so the `SCOPE_USER` bump above was a
    # no-op for exactly the sessions this branch exists to cover - and
    # `SessionViewSet` is a `UserCacheMixin` read keyed on the REQUESTING
    # user's own generation, so the `SCOPE_SCHOOL` bump never reached
    # anyone's cached list either, not even the acting school admin's own.
    # Reach everyone who can see a school session: its school's admins and
    # teachers, whoever created it, and every superadmin.
    from users.signals import school_admin_user_ids, superadmin_user_ids

    scopes = [
        (SCOPE_USER, instance.teacher_id),
        (SCOPE_USER, instance.created_by_id),
        (SCOPE_SCHOOL, instance.school_id),
        (SCOPE_GLOBAL, None),
    ]
    scopes.extend(
        (SCOPE_USER, admin_id)
        for admin_id in school_admin_user_ids([instance.school_id])
    )
    if instance.school_id:
        from users.models import CustomUser, UserTypes

        scopes.extend(
            (SCOPE_USER, teacher_id)
            for teacher_id in CustomUser.objects.filter(
                user_type=UserTypes.TEACHER, school_id=instance.school_id
            ).values_list("id", flat=True)
        )
    scopes.extend((SCOPE_USER, admin_id) for admin_id in superadmin_user_ids())
    bump_many(list(dict.fromkeys(scopes)))


@receiver([post_save, post_delete], sender=Course)
def clear_course_cache(sender, instance, **kwargs):
    bump_many(_course_scopes(instance) + [(SCOPE_GLOBAL, None)])


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
    # A fixed five scopes per enrolment write, NOT one per classmate. The
    # only thing a classmate sees change is the roster (CourseSerializer's
    # `students` and `student_count`), and every cached student payload
    # carrying it is keyed on this course's `crs` generation or on `global`
    # (CourseViewSet.extra_cache_scopes, my_courses), both bumped here. The
    # old per-classmate fan-out made a roster import of n rows cost O(n^2)
    # bumps. The sweep in classrooms/tests_course_roster_scope_sweep.py
    # keeps every roster-bearing student payload on one of those scopes.
    bump_many(
        [(SCOPE_USER, instance.student_id), (SCOPE_GLOBAL, None)]
        + _course_owner_scopes(getattr(instance, "course", None))
    )


@receiver([post_save, post_delete], sender=Topic)
def clear_topic_cache(sender, instance, **kwargs):
    bump_many(
        _course_scopes(getattr(instance, "course", None)) + [(SCOPE_GLOBAL, None)]
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
