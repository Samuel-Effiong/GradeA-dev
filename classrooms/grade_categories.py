"""Epic E2: grade categories, excused work and recalculation (plan
docs/evidence/epic-e2/STEP0_PLAN.md §3-4; FR-E-03/04/07/08).

The maths is `classrooms.grade_service`'s; this module changes what it reads
and stores the results:

* `set_course_categories` replaces a course's categories as one set, under
  a lock on the course row (the weights add up to 100, or there are none).
* `excuse_work` / `remove_excuse` (FR-E-08).
* `recalculate_course` recomputes every enrolled student's stored final
  grade, each under its enrollment row lock (as the submission receivers
  do), and records one GRADE_CHANGE with before and after for each grade
  that actually moved (FR-E-07). It runs in the request, never on the hot
  path of a submission save, and spends no credits.
* The missing-work task (SM ruling 2026-11-18, the one exception to D2's
  "no background task"): a missing assignment starts counting as 0 when
  its due date passes, and nothing is saved at that moment, so a one-off
  clocked task recomputes the course then. It exists only while the flag is
  on, the assignment is published with a due date still ahead, and the
  course counts missing work; `sync_missing_work_task` keeps it so, the way
  due reminders are kept.

User-facing text is the owner-approved wording (OWNER_SHEET.md W1-W13),
used verbatim.
"""

import json
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone
from django_celery_beat.models import ClockedSchedule, PeriodicTask
from rest_framework.exceptions import ValidationError

from audit.emitter import emit
from audit.enums import AuditAction, AuditOutcome
from classrooms import grade_service
from classrooms.models import Course, CourseCategory, StudentCourse

# --- owner-approved wording (W1-W6, W11, W13) --------------------------------

W1_TOTAL = "Category weights must add up to 100%. Right now they add up to {total}%."
W2_NAME_TAKEN = 'This course already has a category called "{name}".'
W3_WEIGHT_RANGE = "Each category weight must be between 0% and 100%."
W4_RELATIVE_WEIGHT = "An assignment's weight in its category must be more than 0."
W5_UNCATEGORIZED = (
    "{n} assignments have no category, so they don't count toward the grade. "
    "Choose a category for each one."
)
W6_CATEGORY_REQUIRED = (
    "Choose a category for this assignment. This course uses grade categories."
)
W11_NOT_IN_COURSE = "This student isn't in this course."
W13_EXCUSED = "Excused work doesn't count toward the grade."
W13_MISSING_ZERO = "Missing work counts as 0."
W13_MISSING_WAITS = "Missing work doesn't count until it's handed in."
W13_UNGRADED = "Work that isn't graded yet doesn't count."

HUNDRED = Decimal("100")
MAX_CATEGORIES = 20


def _clean_name(value):
    name = " ".join(str(value or "").split())
    if not name:
        raise ValidationError({"name": ["This field may not be blank."]})
    if len(name) > 100:
        raise ValidationError(
            {"name": ["Ensure this field has no more than 100 characters."]}
        )
    return name


def _weight(value):
    try:
        weight = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError({"weight_percent": [W3_WEIGHT_RANGE]}) from None
    if not weight.is_finite() or weight < 0 or weight > HUNDRED:
        raise ValidationError({"weight_percent": [W3_WEIGHT_RANGE]})
    if weight != weight.quantize(Decimal("0.01")):
        raise ValidationError({"weight_percent": [W3_WEIGHT_RANGE]})
    return weight.quantize(Decimal("0.01"))


def _total_label(total):
    """`total` as W1 shows it: 90 not 90.00, 99.5 not 99.50."""
    return format(total.normalize(), "f")


def validate_category_items(items):
    """The cleaned `[(id or None, name, weight)]` of a whole set, or a
    ValidationError naming the first problem (W1-W3)."""
    if not isinstance(items, list):
        raise ValidationError({"categories": ["Expected a list of items."]})
    if len(items) > MAX_CATEGORIES:
        raise ValidationError(
            {
                "categories": [
                    f"Ensure this field has no more than {MAX_CATEGORIES} elements."
                ]
            }
        )
    cleaned = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValidationError({"categories": ["Expected a list of items."]})
        name = _clean_name(item.get("name"))
        key = name.casefold()
        if key in seen:
            raise ValidationError({"name": [W2_NAME_TAKEN.format(name=name)]})
        seen.add(key)
        cleaned.append((item.get("id"), name, _weight(item.get("weight_percent"))))
    total = sum((weight for _, _, weight in cleaned), Decimal("0"))
    if cleaned and total != HUNDRED:
        raise ValidationError(
            {"weight_percent": [W1_TOTAL.format(total=_total_label(total))]}
        )
    return cleaned


def _snapshot(course_id):
    """(what the audit event may show, the full set). Names are compared
    but never written to the audit trail (FR-A-04: ids and numbers only)."""
    rows = list(
        CourseCategory.objects.filter(course_id=course_id)
        .order_by("position", "name", "id")
        .values_list("id", "weight_percent", "name")
    )
    shown = {
        "category_ids": [str(cid) for cid, _, _ in rows],
        "weight_percents": [str(weight) for _, weight, _ in rows],
    }
    return shown, rows


def set_course_categories(course, items, *, actor, request=None):
    """Replace `course`'s categories with `items` (`[{"id"?, "name",
    "weight_percent"}]`): an item whose id is one of the course's categories
    updates it, any other item is new, and a category left out is deleted
    (its assignments become uncategorized). Then the course's stored grades
    are recomputed. Returns the categories in order."""
    cleaned = validate_category_items(items)
    with transaction.atomic():
        locked = Course.objects.select_for_update().get(pk=course.pk)
        existing = {
            str(category.id): category
            for category in CourseCategory.objects.filter(course=locked)
        }
        before, before_rows = _snapshot(locked.pk)
        keep = {str(cid) for cid, _, _ in cleaned if str(cid) in existing}
        # Delete first, then rename through a free name, so a set that swaps
        # two names never trips UNIQUE(course, name) on the way.
        CourseCategory.objects.filter(course=locked).exclude(
            id__in=[existing[key].id for key in keep]
        ).delete()
        for key in keep:
            CourseCategory.objects.filter(pk=existing[key].pk).update(
                name=f"__renaming__{existing[key].pk}"
            )
        for position, (cid, name, weight) in enumerate(cleaned):
            if str(cid) in keep:
                CourseCategory.objects.filter(pk=existing[str(cid)].pk).update(
                    name=name, weight_percent=weight, position=position
                )
            else:
                CourseCategory.objects.create(
                    course=locked, name=name, weight_percent=weight, position=position
                )
        after, after_rows = _snapshot(locked.pk)
        if before_rows != after_rows:
            emit(
                AuditAction.GRADE_CATEGORIES_CHANGE,
                actor=actor,
                request=request,
                target_type="Course",
                target_id=locked.pk,
                outcome=AuditOutcome.SUCCESS,
                before=before,
                after=after,
                metadata={"course_id": str(locked.pk), "item_count": len(cleaned)},
            )
            recalculate_course(locked.pk, source="grade_categories")
    return list(CourseCategory.objects.filter(course=course).order_by("position"))


def recalculate_course(course_id, *, source="recalculation", student_ids=None):
    """Recompute the stored final grade of every enrolled student of the
    course (or of `student_ids`), each under its enrollment row lock, and
    record a GRADE_CHANGE for each one that changed. Idempotent: a second
    run finds nothing to change and records nothing. Returns
    `[(student_id, old, new)]` for the grades that changed."""
    from classrooms.signals import _recalculate_final_grade

    enrollments = StudentCourse.objects.filter(course_id=course_id)
    if student_ids is not None:
        enrollments = enrollments.filter(student_id__in=list(student_ids))
    changed = []
    with transaction.atomic():
        for enrollment_id, student_id in enrollments.order_by("id").values_list(
            "id", "student_id"
        ):
            result = _recalculate_final_grade(student_id, course_id)
            if result is None or not result[2]:
                continue
            old, new, _ = result
            changed.append((student_id, old, new))
            emit(
                AuditAction.GRADE_CHANGE,
                target_type="StudentCourse",
                target_id=enrollment_id,
                outcome=AuditOutcome.SUCCESS,
                before={"final_grade": None if old is None else str(old)},
                after={"final_grade": None if new is None else str(new)},
                metadata={
                    "course_id": str(course_id),
                    "student_id": str(student_id),
                    "source": source,
                },
                touches_student_record=True,
            )
    return changed


# --- excused work (FR-E-08) --------------------------------------------------


def _require_enrolled(assignment, student):
    if not StudentCourse.objects.filter(
        course_id=assignment.course_id, student=student
    ).exists():
        raise ValidationError({"student": [W11_NOT_IN_COURSE]})


def excuse_work(assignment, student, *, actor, request=None):
    """Excuse `student` from `assignment`; recompute their grade. Returns
    (row, created)."""
    from students.models import ExcusedWork

    _require_enrolled(assignment, student)
    with transaction.atomic():
        row, created = ExcusedWork.objects.get_or_create(
            student=student,
            assignment=assignment,
            defaults={"excused_by": actor, "excused_at": timezone.now()},
        )
        if created:
            emit(
                AuditAction.WORK_EXCUSED,
                actor=actor,
                request=request,
                target_type="Assignment",
                target_id=assignment.pk,
                outcome=AuditOutcome.SUCCESS,
                metadata={
                    "assignment_id": str(assignment.pk),
                    "student_id": str(student.pk),
                    "course_id": str(assignment.course_id),
                },
                touches_student_record=True,
            )
            recalculate_course(
                assignment.course_id, source="excused", student_ids=[student.pk]
            )
    return row, created


def remove_excuse(assignment, student, *, actor, request=None):
    """Remove the excuse, if any; recompute the grade. Returns whether one
    was removed."""
    from students.models import ExcusedWork

    _require_enrolled(assignment, student)
    with transaction.atomic():
        row = (
            ExcusedWork.objects.select_for_update()
            .filter(student=student, assignment=assignment)
            .first()
        )
        if row is None:
            return False
        row.delete()
        emit(
            AuditAction.EXCUSE_REMOVED,
            actor=actor,
            request=request,
            target_type="Assignment",
            target_id=assignment.pk,
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "assignment_id": str(assignment.pk),
                "student_id": str(student.pk),
                "course_id": str(assignment.course_id),
            },
            touches_student_record=True,
        )
        recalculate_course(
            assignment.course_id, source="excuse_removed", student_ids=[student.pk]
        )
    return True


# --- the grading rules a teacher reads (FR-E-04/08, W5, W13) -----------------


def grading_policy(course):
    """The treatments, in the approved words, for the course's settings."""
    policy = grade_service.course_policy(course.pk)
    rules = [
        W13_EXCUSED,
        W13_MISSING_ZERO if policy.missing_counts_as_zero else W13_MISSING_WAITS,
        W13_UNGRADED,
    ]
    uncategorized = 0
    if policy.category_weights:
        uncategorized = course.assignments.filter(category__isnull=True).count()
    return {
        "uses_categories": bool(policy.category_weights),
        "missing_counts_as_zero": policy.missing_counts_as_zero,
        "uncategorized_count": uncategorized,
        "rules": rules,
        "warnings": (
            [W5_UNCATEGORIZED.format(n=uncategorized)] if uncategorized else []
        ),
    }


# --- the missing-work task (SM ruling 2026-11-18) ----------------------------

MISSING_WORK_TASK = "classrooms.tasks.recalculate_missing_work"


def missing_work_task_name(assignment_id):
    return f"missing-work-recalc-{assignment_id}"


def _qualifies(assignment, course_counts_missing, now=None):
    from assignments.models import AssignmentStatus

    now = now or timezone.now()
    return (
        grade_service.categories_enabled()
        and course_counts_missing
        and assignment.status == AssignmentStatus.PUBLISHED
        and assignment.due_date is not None
        and assignment.due_date > now
    )


def sync_missing_work_task(assignment, *, course_counts_missing=None):
    """Make, re-time or delete the assignment's task so it exists exactly
    while it qualifies."""
    name = missing_work_task_name(assignment.pk)
    if course_counts_missing is None:
        course_counts_missing = bool(
            Course.objects.filter(pk=assignment.course_id)
            .values_list("missing_counts_as_zero", flat=True)
            .first()
        )
    if not _qualifies(assignment, course_counts_missing):
        PeriodicTask.objects.filter(name=name).delete()
        return None
    clocked, _ = ClockedSchedule.objects.get_or_create(clocked_time=assignment.due_date)
    task, _ = PeriodicTask.objects.update_or_create(
        name=name,
        defaults={
            "task": MISSING_WORK_TASK,
            "clocked": clocked,
            "one_off": True,
            "enabled": True,
            "args": json.dumps([str(assignment.pk)]),
        },
    )
    return task


def sync_course_missing_work_tasks(course):
    """After the course's setting changes: every assignment's task."""
    for assignment in course.assignments.only("id", "course_id", "status", "due_date"):
        sync_missing_work_task(
            assignment, course_counts_missing=course.missing_counts_as_zero
        )


def delete_missing_work_task(assignment_id):
    PeriodicTask.objects.filter(name=missing_work_task_name(assignment_id)).delete()


def run_missing_work_task(assignment_id):
    """The task body: recompute the course if it still counts missing work
    (the flag on, the course's setting on, the assignment published with a
    due date that has passed); otherwise nothing. Idempotent."""
    from assignments.models import Assignment, AssignmentStatus

    if not grade_service.categories_enabled():
        return []
    assignment = (
        Assignment.objects.filter(pk=assignment_id)
        .select_related("course")
        .only(
            "id", "status", "due_date", "course__id", "course__missing_counts_as_zero"
        )
        .first()
    )
    if (
        assignment is None
        or not assignment.course.missing_counts_as_zero
        or assignment.status != AssignmentStatus.PUBLISHED
        or assignment.due_date is None
        or assignment.due_date > timezone.now()
    ):
        return []
    with transaction.atomic():
        Course.objects.select_for_update().filter(pk=assignment.course_id).first()
        return recalculate_course(assignment.course_id, source="missing_work_due")
