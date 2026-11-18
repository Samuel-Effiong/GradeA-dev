"""Epic E2: copy one assignment, or up to 50, into a course (FR-E-01/02;
plan docs/evidence/epic-e2/STEP0_PLAN.md §5).

* Tenancy: a teacher, and both the source's course and the destination pass
  `teacher_can_reach_course` (H-38). Anything else fails that item as not
  found, never as forbidden.
* Carried: title, instructions, `raw_input` and its hash, questions (the
  answer key and rubric live inside them), points, question count, type,
  self-assessment, custom AI prompt, relative weight; the category by
  normalized name in the destination, else none (X-6); tags through
  `tags.services.resolve_in_scope`.
* Never: submissions, grades, feedback, excuses, generation history, the
  PDF cache, upload fingerprints, `topic`, the AI payload.
* The copy lands as a DRAFT with no due date and auto-grade off (Q6), so no
  post_save side effect fires: no new-assignment email, no reminders, no
  auto-grade task (which would spend credits).
* When the destination already holds the same title and content (UNIQUE
  course, title, raw_input_hash), the title gets "(copy)", then
  "(copy 2)" ... (W7).
* Zero credits (`zero_credit_scope`), and one ASSIGNMENT_COPY per copy.
* Images stay shared by address: there is no foreign key to them and
  deleting an assignment deletes no file (NFR-REL-07).
"""

import copy as _copy

from django.db import IntegrityError, transaction

from assignments.models import Assignment, AssignmentStatus
from audit.emitter import emit
from audit.enums import AuditAction, AuditOutcome, ErrorClass, ReasonCode
from billing.zero_credit import register_zero_credit_operations, zero_credit_scope
from classrooms.models import Course, CourseCategory, teacher_can_reach_course

COPY_OPERATION = "assignment.copy"
BULK_COPY_OPERATION = "assignment.bulk_copy"
register_zero_credit_operations(COPY_OPERATION, BULK_COPY_OPERATION)

BULK_LIMIT = 50  # Q9

# Owner-approved wording (OWNER_SHEET W7-W10).
W7_FIRST = "{title} (copy)"
W7_NEXT = "{title} (copy {n})"
W8_SOURCE_UNAVAILABLE = "This assignment can't be copied. It may have been deleted."
W9_DESTINATION_REFUSED = "You can't copy into this course."
W10_BULK_LIMIT = "You can copy up to 50 assignments at a time."

# Per-item reason codes (FR-E-02: actionable and stable).
SOURCE_UNAVAILABLE = "ASSIGNMENT_NOT_AVAILABLE"
DESTINATION_REFUSED = ReasonCode.COURSE_NOT_REACHABLE.value

TITLE_MAX = Assignment._meta.get_field("title").max_length or 255
MAX_TITLE_TRIES = 50


class CopyRefused(Exception):
    def __init__(self, reason_code, message):
        super().__init__(message)
        self.reason_code = reason_code
        self.message = message


def reachable_destination(user, course_id):
    course = Course.objects.select_related("session").filter(pk=course_id).first()
    if course is None or not teacher_can_reach_course(user, course):
        raise CopyRefused(DESTINATION_REFUSED, W9_DESTINATION_REFUSED)
    return course


def reachable_source(user, assignment_id):
    source = (
        Assignment.objects.select_related("course__session", "category")
        .filter(pk=assignment_id)
        .first()
    )
    if source is None or not teacher_can_reach_course(user, source.course):
        raise CopyRefused(SOURCE_UNAVAILABLE, W8_SOURCE_UNAVAILABLE)
    return source


def _normalized(name):
    return " ".join((name or "").split()).casefold()


def _matching_category(source, destination):
    if source.category is None:
        return None
    wanted = _normalized(source.category.name)
    for category in CourseCategory.objects.filter(course=destination):
        if _normalized(category.name) == wanted:
            return category
    return None


def _titles(title):
    """The source title, then W7's suffixes, each kept within the column."""
    if title is None:
        yield None
        return
    yield title
    for n in range(1, MAX_TITLE_TRIES + 1):
        suffix_form = W7_FIRST if n == 1 else W7_NEXT
        tail = suffix_form.format(title="", n=n)
        base = title[: TITLE_MAX - len(tail)]
        yield suffix_form.format(title=base, n=n)


def _free_title(destination, source):
    taken = set(
        Assignment.objects.filter(
            course=destination, raw_input_hash=source.raw_input_hash
        ).values_list("title", flat=True)
    )
    for title in _titles(source.title):
        if title is None or title not in taken:
            return title
    raise CopyRefused(SOURCE_UNAVAILABLE, W8_SOURCE_UNAVAILABLE)


def _copy_tags(source, new, actor):
    from tags.models import AssignmentTag
    from tags.services import resolve_in_scope

    for link in AssignmentTag.objects.filter(assignment=source).select_related("tag"):
        AssignmentTag.objects.get_or_create(
            assignment=new, tag=resolve_in_scope(link.tag, actor)
        )


def copy_assignment(source, destination, *, actor, request=None):
    """Copy `source` into `destination` (both already checked reachable).
    Returns the new assignment."""
    with zero_credit_scope(COPY_OPERATION):
        for _ in range(3):
            try:
                with transaction.atomic():
                    new = Assignment.objects.create(
                        course=destination,
                        title=_free_title(destination, source),
                        instructions=source.instructions,
                        raw_input=source.raw_input,
                        raw_input_hash=source.raw_input_hash,
                        total_points=source.total_points,
                        question_count=source.question_count,
                        assignment_type=source.assignment_type,
                        questions=_copy.deepcopy(source.questions),
                        self_assessment=source.self_assessment,
                        custom_ai_prompt=source.custom_ai_prompt,
                        ai_generated=source.ai_generated,
                        category=_matching_category(source, destination),
                        relative_weight=source.relative_weight,
                        status=AssignmentStatus.DRAFT,
                        due_date=None,
                        auto_grade_on_due_date=False,
                    )
                    _copy_tags(source, new, actor)
                    emit(
                        AuditAction.ASSIGNMENT_COPY,
                        actor=actor,
                        request=request,
                        target_type="Assignment",
                        target_id=new.pk,
                        outcome=AuditOutcome.SUCCESS,
                        metadata={
                            "assignment_id": str(source.pk),
                            "course_id": str(destination.pk),
                            "session_id": (
                                str(destination.session_id)
                                if destination.session_id
                                else None
                            ),
                        },
                    )
                    return new
            except IntegrityError:
                # A concurrent copy took the same title; pick the next one.
                continue
        raise CopyRefused(SOURCE_UNAVAILABLE, W8_SOURCE_UNAVAILABLE)


def refuse_audit(actor, request, refusal, *, source_id, course_id):
    """A refused copy into a course the teacher cannot reach is recorded
    (as H-38's refusals are); a missing source records nothing."""
    if refusal.reason_code != DESTINATION_REFUSED:
        return
    emit(
        AuditAction.ASSIGNMENT_COPY,
        actor=actor,
        request=request,
        target_type="Assignment",
        target_id=source_id,
        outcome=AuditOutcome.FAILURE,
        error_class=ErrorClass.USER,
        reason_code=ReasonCode.COURSE_NOT_REACHABLE,
        metadata={"assignment_id": str(source_id), "course_id": str(course_id)},
    )


def bulk_copy(assignment_ids, course_id, *, actor, request=None):
    """Copy each assignment into the course; one failure never stops the
    rest (FR-E-02). Returns a result per distinct id, in order."""
    results = []
    with zero_credit_scope(BULK_COPY_OPERATION):
        try:
            destination = reachable_destination(actor, course_id)
            refusal = None
        except CopyRefused as exc:
            destination, refusal = None, exc
        for source_id in dict.fromkeys(assignment_ids):
            try:
                if refusal is not None:
                    raise refusal
                source = reachable_source(actor, source_id)
                new = copy_assignment(source, destination, actor=actor, request=request)
                results.append(
                    {
                        "source_id": str(source_id),
                        "ok": True,
                        "assignment_id": str(new.pk),
                        "title": new.title,
                    }
                )
            except CopyRefused as exc:
                refuse_audit(
                    actor, request, exc, source_id=source_id, course_id=course_id
                )
                results.append(
                    {
                        "source_id": str(source_id),
                        "ok": False,
                        "reason_code": exc.reason_code,
                        "error": exc.message,
                    }
                )
    return results
