"""GET tasks/sessions: a teacher's batch sessions (owner, 2026-11-18).

A teacher who logs out or switches device finds a batch again here, then
reads session-results or retries by its session_id. Their own sessions only,
and (H-38, students.task_access) only those whose course they can still
reach: a session's course is its own, else its assignment's; a session with
neither is reachable, as teacher_may_reach says.

resumable and retryable_count use session-results' own rule
(students.item_results.is_retryable, with one kept-file read for every item
asked about), and every count is an annotation, so a page costs the same
number of queries whatever its size.
"""

from __future__ import annotations

from collections import Counter

from django.db.models import Count, Q

from classrooms.models import reachable_courses

from .item_results import is_retryable
from .item_retry import kept_item_ids
from .models import BackgroundProcessingTask, BackgroundTaskStatus, BatchUploadSession

_FINISHED = (
    BackgroundTaskStatus.SUCCESS,
    BackgroundTaskStatus.FAILURE,
    BackgroundTaskStatus.CANCELLED,
)


def reachable_sessions_q(user):
    """Q for the batch sessions whose course `user` can reach now (H-38)."""
    courses = reachable_courses(user)
    return (
        Q(course__isnull=False, course__in=courses)
        | Q(course__isnull=True, assignment__course__in=courses)
        | Q(course__isnull=True, assignment__isnull=True)
    )


def teacher_sessions(user, *, assignment_id=None, task_type=None):
    """`user`'s reachable batch sessions, filtered; not yet counted."""
    sessions = BatchUploadSession.objects.filter(teacher=user).filter(
        reachable_sessions_q(user)
    )
    if assignment_id is not None:
        sessions = sessions.filter(assignment_id=assignment_id)
    if task_type:
        sessions = sessions.filter(task_type=task_type)
    return sessions


def with_counts(sessions):
    """The sessions newest first, with their item counts and their course
    and assignment read in the same query."""

    def count(condition):
        return Count("processing_tasks", filter=condition)

    return (
        sessions.select_related("assignment", "course", "assignment__course")
        .annotate(
            success_count=count(
                Q(processing_tasks__status=BackgroundTaskStatus.SUCCESS)
            ),
            failure_count=count(
                Q(processing_tasks__status=BackgroundTaskStatus.FAILURE)
            ),
            cancelled_count=count(
                Q(processing_tasks__status=BackgroundTaskStatus.CANCELLED)
            ),
            dismissed_count=count(Q(processing_tasks__dismissed_at__isnull=False)),
            pending_count=count(
                Q(processing_tasks__isnull=False)
                & ~Q(processing_tasks__status__in=_FINISHED)
            ),
        )
        .order_by("-created_at", "-id")
    )


def retryable_counts(session_ids):
    """{session id: how many of its failed items a retry would retry}, for
    `session_ids` (a list or a subquery): two queries at most."""
    failed = list(
        BackgroundProcessingTask.objects.filter(
            batch_session_id__in=session_ids, status=BackgroundTaskStatus.FAILURE
        ).only(
            "id",
            "batch_session_id",
            "task_type",
            "status",
            "reason_code",
            "dismissed_at",
        )
    )
    kept_ids = kept_item_ids(failed)
    return Counter(
        task.batch_session_id for task in failed if is_retryable(task, kept_ids)
    )


def session_row(session, retryable):
    """One row of the list. `retryable`: the session's retryable_count."""
    assignment = session.assignment
    course = session.course or (assignment.course if assignment else None)
    return {
        "session_id": str(session.id),
        "task_type": session.task_type,
        "assignment_id": str(assignment.id) if assignment else None,
        "assignment_title": assignment.title if assignment else None,
        "course_id": str(course.id) if course else None,
        "course_name": course.name if course else None,
        "created_at": session.created_at,
        "success_count": session.success_count,
        "failure_count": session.failure_count,
        "cancelled_count": session.cancelled_count,
        "pending_count": session.pending_count,
        "dismissed_count": session.dismissed_count,
        "resumable": retryable > 0,
        "retryable_count": retryable,
    }
