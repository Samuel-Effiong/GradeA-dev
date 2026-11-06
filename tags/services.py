"""Epic C1: the tag service, the only code that writes tags and their links
(plan 11 §3.3).

Every read and write of tags and links runs inside `zero_credit_scope`
(FR-B-10). Each change records its own audit event (FR-A-01), with ids,
counts and the type label only, never a tag's name (audit/metadata.py),
just after the scope closes and inside the same transaction (see _audit).

Races (plan 11 §5.3, Gate 3):
* Two callers creating the same tag: the database's unique key decides,
  and the loser re-reads the winner's row (one row, one id for everyone).
* A delete racing an attach: both lock the tag row first. If the delete
  goes first, the attach finds no tag ("not found"); if the attach goes
  first, the delete's count no longer matches the preview and is refused
  (TAG_CHANGED_SINCE_CHECK). A link to a deleted tag can't happen.
"""

import uuid

from django.db import IntegrityError, transaction
from django.http import Http404

from assignments.models import Assignment
from audit.emitter import emit
from audit.enums import AuditAction
from AutoGrader.reason_codes import CodedError
from billing.zero_credit import register_zero_credit_operations, zero_credit_scope

from .models import AssignmentTag, LinkSource, OwnerType, Tag, TagType
from .normalize import clean_tag_name

#: Django's own wording, which H-296 rewords for the client as "We couldn't
#: find that item. It may have been deleted." (users/exceptions.py): the same
#: text whether the tag never existed or is another teacher's.
NOT_FOUND = "No Tag matches the given query."

#: FR-C-04 allows several tags per assignment; this bounds one set.
MAX_TAGS_PER_ASSIGNMENT = 50

register_zero_credit_operations(
    "tag.get_or_create",
    "tag.rename",
    "tag.impact",
    "tag.delete",
    "tag.set_assignment_tags",
    "tag.resolve_in_scope",
)


def _audit(action, **fields):
    """Record the event after the operation's zero-credit scope has closed,
    inside the same transaction (a rollback removes both). Inside the scope
    the billing append-only guard, which AuditEvent shares with the credit
    ledger, refuses the insert as a ledger write and the emitter drops it.
    Reported to the SM; the guard is billing's (Lane 1)."""
    emit(action, **fields)


def _check_type(tag_type):
    if tag_type not in TagType.values:
        raise ValueError(f"not a C1 tag type: {tag_type!r}")
    return TagType(tag_type)


def _name_taken(existing):
    """D6, SM ruling (i): the existing tag's type label and stored spelling."""
    tag_type = TagType(existing.tag_type)
    return CodedError(
        "TAG_NAME_TAKEN",
        params={"tag_type": tag_type.value, "name": existing.name},
        display={"tag_type": tag_type.label},
    )


def get_or_create_tag(teacher, tag_type, name):
    """Return `(tag, created)`. A name that normalizes to one of the
    teacher's tags of this type reuses it (the first spelling wins)."""
    with zero_credit_scope("tag.get_or_create"):
        tag_type = _check_type(tag_type)
        display, key = clean_tag_name(name)
        scope = {
            "owner_type": OwnerType.TEACHER,
            "teacher": teacher,
            "tag_type": tag_type,
            "normalized_name": key,
        }
        existing = Tag.objects.filter(**scope).first()
    if existing is not None:
        return existing, False
    try:
        with transaction.atomic():
            with zero_credit_scope("tag.get_or_create"):
                tag = Tag.objects.create(
                    **scope, name=display, created_by_id=teacher.pk
                )
            _audit(
                AuditAction.TAG_CREATE,
                actor=teacher,
                target_type="Tag",
                target_id=tag.pk,
                metadata={"tag_type": tag_type.value},
            )
    except IntegrityError:
        # Another caller created it first: theirs is the tag.
        with zero_credit_scope("tag.get_or_create"):
            return Tag.objects.get(**scope), False
    return tag, True


def rename_tag(tag, name):
    """Rename the tag. A name that normalizes to another of the teacher's
    tags of this type is refused, never merged (TAG_NAME_TAKEN)."""
    display, key = clean_tag_name(name)
    with transaction.atomic():
        with zero_credit_scope("tag.rename"):
            locked = Tag.objects.select_for_update().get(pk=tag.pk)
            same_key = Tag.objects.filter(
                owner_type=locked.owner_type,
                teacher_id=locked.teacher_id,
                tag_type=locked.tag_type,
                normalized_name=key,
            ).exclude(pk=locked.pk)
            other = same_key.first()
            if other is not None:
                raise _name_taken(other)
            locked.name, locked.normalized_name = display, key
            try:
                with transaction.atomic():
                    locked.save(update_fields=["name", "normalized_name"])
            except IntegrityError:
                # Created under the same key since the check above.
                raise _name_taken(same_key.get()) from None
        _audit(
            AuditAction.TAG_RENAME,
            actor=locked.teacher,
            target_type="Tag",
            target_id=locked.pk,
            metadata={"tag_type": locked.tag_type},
        )
    tag.name, tag.normalized_name = display, key
    return tag


def tag_impact(tag):
    """How many assignments the tag is on: the delete preview (FR-C-05)."""
    with zero_credit_scope("tag.impact"):
        return AssignmentTag.objects.filter(tag_id=tag.pk).count()


def delete_tag(tag, expected_count):
    """Delete the tag and its links, never an assignment, if it is still on
    `expected_count` assignments (the count the teacher was shown).
    Otherwise refuse with TAG_CHANGED_SINCE_CHECK and change nothing.
    Returns the number of links removed."""
    with transaction.atomic():
        with zero_credit_scope("tag.delete"):
            locked = Tag.objects.select_for_update().filter(pk=tag.pk).first()
            if locked is None:
                raise Http404(NOT_FOUND)
            count = AssignmentTag.objects.filter(tag_id=locked.pk).count()
            if count != expected_count:
                raise CodedError("TAG_CHANGED_SINCE_CHECK")
            tag_id, actor = locked.pk, locked.teacher
            locked.delete()
        _audit(
            AuditAction.TAG_DELETE,
            actor=actor,
            target_type="Tag",
            target_id=tag_id,
            metadata={"tag_type": locked.tag_type, "affected_count": count},
        )
    return count


def _as_ids(tag_ids):
    ids = []
    for value in tag_ids:
        try:
            tag_id = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            raise Http404(NOT_FOUND) from None
        if tag_id not in ids:
            ids.append(tag_id)
    return ids


def set_assignment_tags(assignment, tag_ids, *, teacher):
    """Replace the assignment's tags with exactly `tag_ids` (duplicates
    count once) and return the tags in that order.

    Every id must be one of `teacher`'s own tags, or nothing changes and
    the answer is "not found" (an id from another teacher is not revealed).
    A link that stays keeps its source, so a backfilled link the teacher
    keeps is still removed by `backfill_topic_tags --reverse` (SM, C1 (ii));
    a link added here is USER. The caller checks access to the assignment.
    """
    if len(tag_ids) > MAX_TAGS_PER_ASSIGNMENT:
        # Before parsing: a body of 10,000 ids is refused cheaply.
        raise CodedError("TOO_MANY_TAGS")
    ids = _as_ids(tag_ids)
    with transaction.atomic():
        with zero_credit_scope("tag.set_assignment_tags"):
            # Lock the assignment (one set at a time per assignment) and the
            # tags (a racing delete either sees these links or makes these
            # tags disappear before we look).
            Assignment.objects.select_for_update().filter(pk=assignment.pk).exists()
            found = {
                tag.pk: tag
                for tag in Tag.objects.select_for_update()
                .filter(pk__in=ids, owner_type=OwnerType.TEACHER, teacher=teacher)
                .order_by("pk")
            }
            if len(found) != len(ids):
                raise Http404(NOT_FOUND)
            current = set(
                AssignmentTag.objects.filter(assignment_id=assignment.pk).values_list(
                    "tag_id", flat=True
                )
            )
            wanted = set(ids)
            AssignmentTag.objects.filter(
                assignment_id=assignment.pk, tag_id__in=current - wanted
            ).delete()
            AssignmentTag.objects.bulk_create(
                AssignmentTag(
                    assignment_id=assignment.pk, tag_id=tag_id, source=LinkSource.USER
                )
                for tag_id in ids
                if tag_id not in current
            )
        _audit(
            AuditAction.ASSIGNMENT_TAGS_SET,
            actor=teacher,
            target_type="Assignment",
            target_id=assignment.pk,
            before={"tag_ids": sorted(str(i) for i in current)},
            after={"tag_ids": sorted(str(i) for i in wanted)},
            metadata={"course_id": str(assignment.course_id)},
        )
    return [found[tag_id] for tag_id in ids]


def resolve_in_scope(tag, owner):
    """The same tag in `owner`'s scope, for copying (E2, plan 11 §0.3): the
    tag itself if `owner` has it, else `owner`'s tag of that type and name,
    made if needed (by get_or_create_tag, inside its own scope)."""
    with zero_credit_scope("tag.resolve_in_scope"):
        if tag.owner_type == OwnerType.TEACHER and tag.teacher_id == owner.pk:
            return tag
        tag_type, name = tag.tag_type, tag.name
    resolved, _ = get_or_create_tag(owner, tag_type, name)
    return resolved
