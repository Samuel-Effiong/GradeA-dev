from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from AutoGrader.cache_generation import (
    SCOPE_COURSE,
    SCOPE_GLOBAL,
    SCOPE_SCHOOL,
    SCOPE_USER,
    bump_many,
)
from AutoGrader.cache_utils import delete_cache_patterns
from students.models import BatchUploadSession, StudentSubmission

# `delete_cache_patterns` is the project's shared helper rather than a local
# copy. The local copy called `cache.delete_pattern` unguarded, and these are
# post_save/post_delete receivers, which Django runs INSIDE the caller's
# transaction - so a Redis blip did not just skip an invalidation, it failed
# the submission save that triggered it. The shared helper treats
# invalidation as best-effort (stale for at most CACHE_TTL beats losing a
# committed write) and coalesces patterns inside a batched block.

# Every wildcard family a submission change can make stale. One definition,
# shared with the service-layer paths that write a submission via
# QuerySet.update() (which never fires post_save), so those paths can't
# drift from what the receiver clears.
SUBMISSION_CACHE_PATTERNS = (
    "*superadmin*",
    "*schooladmin*",
    "*teacheradmin*",
    "*studentadmin*",
    "courses:*",
    "assignments:*",
    "studentsubmissions:*",
)


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
    """Everything a submission change makes stale: generation counters and
    wildcard families. Public so service code that bypasses save() (a
    QuerySet.update() on the grading claim) invalidates exactly what the
    receiver would have."""
    # H-1 stage 2. `teacher_performance_<school>_*` and
    # `teacher_detail_<school>_<teacher>` (dashboard families 30-31) are
    # built from grading statistics, so a submission has to move the school
    # and teacher generations or those dashboards keep serving pre-grading
    # numbers for their whole TTL.
    _bump_submission_scopes(submission)
    delete_cache_patterns(*SUBMISSION_CACHE_PATTERNS)


def invalidate_submission_caches_bulk(submissions):
    """Same as `invalidate_submission_caches`, for many submissions at once.

    H-1 Stage 3 (gap G3): `publish_all_grades` (assignments/views.py) does
    one bulk `.update()` for a whole batch, which bypasses `post_save` for
    every row in it - so invalidating only `submissions[0]` (the previous
    shape of this fix) left every OTHER student in the batch reading their
    pre-publish result. Every student's `usr` is bumped in a single
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
    delete_cache_patterns(*SUBMISSION_CACHE_PATTERNS)


@receiver([post_save, post_delete], sender=StudentSubmission)
def clear_student_submission_cache(sender, instance, **kwargs):
    invalidate_submission_caches(instance)


@receiver([post_save, post_delete], sender=BatchUploadSession)
def clear_batch_upload_session_cache(sender, instance, **kwargs):
    delete_cache_patterns(
        "studentsubmissions:*",
        "assignments:*",
    )
