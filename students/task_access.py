"""H-38 for background work: a task, a batch session or a grading run is
reachable only while its course is (v2's finding on the tasks/ namespace).

Ownership alone - `requested_by == user` for a task, `teacher == user` for a
batch session, `course.teacher` for the auto-grade beat - never stops being
true, so a teacher removed from a school kept reading their past jobs'
results (which name the school's students), cancelling the school's grading,
and having the school's students graded - and billed - in their name.

The rule is `classrooms.models.teacher_can_reach_course`: the teacher must
be the course's CURRENT owner and the course must still be reachable by
H-38. For a teacher it decides even when they are not the owner, because
the work's own ownership (`requested_by`) outlives a course reassignment: a
removed teacher whose old course a super admin gives to a colleague must
not regain it (1a's N1). Work with no course (nothing to leak or act on),
or a user who is not a teacher (an admin's own tasks; their ownership check
still decides, unchanged), is left as it was.

One place, so the tasks/ routes, the grading dispatches and later callers
(Epic A's per-item retry, S7b) all ask the same question.
"""

from __future__ import annotations

from typing import Any, Optional

from django.http import Http404

from classrooms.models import Course, teacher_can_reach_course
from users.models import UserTypes


def course_of(work: Any) -> Optional[Course]:
    """The course a BackgroundProcessingTask, a BatchUploadSession or a
    StudentSubmission belongs to, or None if it has none."""
    for path in (
        ("course",),
        ("submission", "assignment", "course"),
        ("assignment", "course"),
        ("batch_session", "course"),
        ("batch_session", "assignment", "course"),
    ):
        value: Any = work
        for attr in path:
            value = getattr(value, attr, None)
            if value is None:
                break
        if isinstance(value, Course):
            return value
    return None


def teacher_may_reach(user, work) -> bool:
    """False when `user` is a teacher who can't reach the work's course now:
    not its current owner, or no longer in its school (H-38)."""
    course = course_of(work)
    if course is None or user is None or user.user_type != UserTypes.TEACHER:
        return True
    return teacher_can_reach_course(user, course)


def ensure_reachable(user, work) -> None:
    """For a route: an unreachable task or session answers exactly like a
    missing one (404), so the answer reveals nothing either."""
    if not teacher_may_reach(user, work):
        raise Http404("Not found.")
