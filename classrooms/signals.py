from decimal import ROUND_HALF_UP, Decimal

from django.core.cache import cache
from django.db import transaction
from django.db.models import Sum
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from assignments.models import Assignment
from classrooms.models import Course, School, Session, StudentCourse, Topic
from students.models import StudentSubmission


def delete_cache_patterns(*patterns):
    if not hasattr(cache, "delete_pattern"):
        return

    for pattern in patterns:
        cache.delete_pattern(pattern)


@receiver([post_save, post_delete], sender=School)
def clear_school_cache(sender, instance, **kwargs):
    delete_cache_patterns(
        "*superadmin*",
        "*schooladmin*",
        "schools:*",
        "courses:*",
        "sessions:*",
    )


@receiver([post_save, post_delete], sender=Session)
def clear_session_cache(sender, instance, **kwargs):
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


def _recalculate_final_grade(student_id, course_id):
    """
    Recomputes a student's final grade for a course as a points-weighted
    average across all graded submissions: sum(score) / sum(max_points) * 100.

    Weighted (not a plain mean of percentages) so a 100-point exam counts
    more than a 5-point quiz. Runs after every submission save *and*
    delete so `final_grade` can't drift from submissions that were
    resubmitted, ungraded, or removed after an earlier grade was recorded.

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
            return

        totals = StudentSubmission.objects.filter(
            student_id=student_id,
            assignment__course_id=course_id,
            graded_at__isnull=False,
            score__isnull=False,
            max_points__gt=0,
        ).aggregate(total_score=Sum("score"), total_max_points=Sum("max_points"))

        total_score = totals["total_score"]
        total_max_points = totals["total_max_points"]

        if not total_max_points:
            new_final_grade = None
        else:
            raw_grade = (total_score / total_max_points) * 100
            # Clamp to the documented 0-100 scale so bad upstream data (extra
            # credit pushing a score over 100%, a negative adjustment) can't
            # silently fall outside every grade band in grade-distribution
            # reporting.
            clamped = max(Decimal("0"), min(Decimal("100"), raw_grade))
            new_final_grade = clamped.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if enrollment.final_grade != new_final_grade:
            enrollment.final_grade = new_final_grade
            enrollment.save(update_fields=["final_grade"])


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
