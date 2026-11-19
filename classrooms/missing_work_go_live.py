"""Epic E2 go-live, automatic (owner 2026-11-19): the first time
GRADE_CATEGORIES_ENABLED is on in an environment, recompute the stored course
grades so missing work counts, once, by itself.

Why it is needed: a stored final grade is only rewritten by an event, and
the clocked due-date task is made only for due dates still ahead while the
flag is on. Grades stored before the flag went on do not yet count work that
was due earlier (see the `recalculate_missing_work` command's docstring).

How it runs once:
- Trigger: the hourly Beat task `classrooms.tasks.run_missing_work_go_live`
  (under the Beat lock, H-65). Not post_migrate (it runs inside the deploy's
  migrate step, on every test database and flush, and an earlier
  post_migrate auto-seed was removed for that reason, billing/checks.py),
  not AppConfig.ready (no DB access at import), not the request path. A
  worker or beat restart only means the next tick tries again.
- Marker: one GoLiveRun row (unique name) in the database, not a file, so
  every process sees the same state. A run claims it with a conditional
  UPDATE (only one process can win), holds a short lease renewed after
  each course, and sets `completed_at` at the end. Once completed, every
  later tick is one indexed read and nothing else.
- Interrupted: each course is its own transaction, so finished courses
  stay finished. On an error the claim is released and the error raised
  (the task fails visibly); a killed worker's lease lapses after LEASE.
  The next tick redoes the list: the recompute is idempotent, so courses
  already done change nothing and record nothing.
- Same recompute as the manual command: for each course that counts
  missing work, under the course lock, make the due-date tasks still ahead
  and recompute every enrolled student's grade; a GRADE_CHANGE audit only
  for a grade that moved, source "missing_work_go_live" (BE-E-06: idempotent,
  before and after values). No credits are spent.
- Flag off: returns at once, writes nothing (not even the marker).
"""

import logging
import uuid
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from classrooms import grade_categories, grade_service
from classrooms.models import Course, GoLiveRun

logger = logging.getLogger(__name__)

NAME = "missing_work_go_live"
SOURCE = NAME
#: How long a claim lives without renewal; renewed after every course.
LEASE = timedelta(minutes=10)

FLAG_OFF = "flag off"
ALREADY_DONE = "already done"
HELD_ELSEWHERE = "another run holds the claim"
LOST_CLAIM = "lost the claim"
COMPLETED = "completed"


def marker():
    return GoLiveRun.objects.filter(name=NAME).first()


def _claimable(now):
    return GoLiveRun.objects.filter(name=NAME, completed_at__isnull=True).filter(
        Q(claimed_until__isnull=True) | Q(claimed_until__lt=now)
    )


def _claim(token):
    GoLiveRun.objects.get_or_create(name=NAME)
    now = timezone.now()
    return (
        _claimable(now).update(
            claim_token=token, claimed_until=now + LEASE, started_at=now
        )
        == 1
    )


def _mine(token):
    return GoLiveRun.objects.filter(
        name=NAME, claim_token=token, completed_at__isnull=True
    )


def _renew(token):
    return _mine(token).update(claimed_until=timezone.now() + LEASE) == 1


def recompute_course(course_id):
    """One course, in its own transaction under the course lock: the
    due-date tasks still ahead, then the idempotent recompute. Returns
    `[(student_id, old, new)]` for the grades that moved."""
    with transaction.atomic():
        course = Course.objects.select_for_update().filter(pk=course_id).first()
        if course is None or not course.missing_counts_as_zero:
            return []
        grade_categories.sync_course_missing_work_tasks(course)
        return grade_categories.recalculate_course(course_id, source=SOURCE)


def course_ids():
    return (
        Course.objects.filter(missing_counts_as_zero=True)
        .order_by("created_at", "id")
        .values_list("id", flat=True)
    )


def run(*, on_change=None):
    """Run the go-live recompute if it has not completed here yet.
    `on_change(course_id, student_id, old, new)` is called for each grade
    that moved (the manual command prints them). Returns a dict with
    `status` (one of the constants above), `courses_checked` and
    `grades_changed`."""
    checked = changed = 0

    def outcome(status):
        return {"status": status, "courses_checked": checked, "grades_changed": changed}

    if not grade_service.categories_enabled():
        return outcome(FLAG_OFF)
    current = marker()
    if current is not None and current.completed_at is not None:
        return outcome(ALREADY_DONE)
    token = uuid.uuid4().hex
    if not _claim(token):
        current = marker()
        done = current is not None and current.completed_at is not None
        return outcome(ALREADY_DONE if done else HELD_ELSEWHERE)

    try:
        for course_id in course_ids().iterator():
            for student_id, old, new in recompute_course(course_id):
                changed += 1
                if on_change is not None:
                    on_change(course_id, student_id, old, new)
            checked += 1
            if not _renew(token):
                logger.warning(
                    "missing work go-live stopped: the claim was taken over "
                    "(courses checked %d, grades changed %d)",
                    checked,
                    changed,
                )
                return outcome(LOST_CLAIM)
    except BaseException:
        _mine(token).update(claimed_until=None)
        raise

    _mine(token).update(
        completed_at=timezone.now(),
        claimed_until=None,
        courses_checked=checked,
        grades_changed=changed,
    )
    logger.info(
        "missing work go-live completed: courses checked %d, grades changed %d",
        checked,
        changed,
    )
    return outcome(COMPLETED)
