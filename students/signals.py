from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from AutoGrader.cache_generation import (
    SCOPE_COURSE,
    SCOPE_GLOBAL,
    SCOPE_SCHOOL,
    SCOPE_USER,
    bump_many,
)
from students.models import StudentSubmission


def _bump_submission_scopes(submission):
    """Entities a submission change can affect.

    Every relation is resolved defensively: `post_delete` may fire after the
    assignment or course row is gone, and a bump that raises inside a
    receiver would fail the delete that triggered it.
    """
    assignment = getattr(submission, "assignment", None)
    course = getattr(assignment, "course", None) if assignment else None
    teacher = getattr(course, "teacher", None) if course else None

    bump_many(
        [
            (SCOPE_USER, getattr(submission, "student_id", None)),
            (SCOPE_USER, getattr(course, "teacher_id", None) if course else None),
            (
                SCOPE_COURSE,
                getattr(assignment, "course_id", None) if assignment else None,
            ),
            (SCOPE_SCHOOL, getattr(teacher, "school_id", None) if teacher else None),
            (SCOPE_GLOBAL, None),
        ]
    )


def invalidate_submission_caches(submission):
    """Everything a submission change makes stale. Public so service code
    that bypasses save() (a QuerySet.update() on the grading claim, the
    single grade publish) invalidates exactly what the receiver would
    have."""
    # H-1 stage 2. `teacher_performance_<school>_*` and
    # `teacher_detail_<school>_<teacher>` (dashboard families 30-31) are
    # built from grading statistics, so a submission has to move the school
    # and teacher generations or those dashboards keep serving pre-grading
    # numbers for their whole TTL.
    _bump_submission_scopes(submission)


def invalidate_submission_caches_bulk(submissions):
    """Same as `invalidate_submission_caches`, for many submissions at once.

    H-1 Stage 3 (gap G3): `publish_all_grades` (assignments/views.py) does
    one bulk `.update()` for a whole batch, which bypasses `post_save` for
    every row in it. Invalidating only `submissions[0]` bumped one
    student's generation; every OTHER student's cached list was refreshed
    only by the legacy `studentsubmissions:*` wildcard (removed in H-1
    step 4), and without it would keep the pre-publish result. Every student's `usr` is bumped in a single
    pipelined `bump_many` call, so the round-trip cost stays O(1) rather
    than O(batch size); course/teacher/school/global are shared across the
    batch and so are bumped once each, not once per submission.
    """
    submissions = [s for s in submissions if s is not None]
    if not submissions:
        return

    scopes = [(SCOPE_USER, s.student_id) for s in submissions]

    first = submissions[0]
    assignment = getattr(first, "assignment", None)
    course = getattr(assignment, "course", None) if assignment else None
    teacher = getattr(course, "teacher", None) if course else None

    scopes.append((SCOPE_USER, getattr(course, "teacher_id", None) if course else None))
    scopes.append(
        (SCOPE_COURSE, getattr(assignment, "course_id", None) if assignment else None)
    )
    scopes.append(
        (SCOPE_SCHOOL, getattr(teacher, "school_id", None) if teacher else None)
    )
    scopes.append((SCOPE_GLOBAL, None))

    bump_many(scopes)


@receiver([post_save, post_delete], sender=StudentSubmission)
def clear_student_submission_cache(sender, instance, **kwargs):
    invalidate_submission_caches(instance)
