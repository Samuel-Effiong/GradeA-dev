"""The department operations (plan sections 4, 6, 8, 9). Every write records
exactly one audit event inside its own transaction, so a rollback removes
both. Callers have already decided WHO may act (departments.access)."""

import uuid

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from audit.emitter import emit
from audit.enums import AuditAction
from AutoGrader.reason_codes import CodedError
from billing.models import SchoolCreditAllocation
from users.models import CustomUser, UserTypes

from . import notifications, registry
from .access import live_teachers, newest_license_id
from .models import Department, DepartmentMembership
from .normalize import MAX_NAME_LENGTH, clean_department_name
from .wording import MAX_MEMBERS_PER_DEPARTMENT, MAX_TEACHERS_PER_ADD

MEMBER_IDS_CAP = 50

SOURCE_ADMIN = "admin"
SOURCE_LEFT_LICENSE = "left_license"
SOURCE_ACCOUNT_CHANGE = "account_change"


def _emit(action, actor, department, *, target_type, target_id, request=None, **extra):
    return emit(
        action,
        actor=actor,
        request=request,
        target_type=target_type,
        target_id=target_id,
        school_id=department.school_id,
        department_id=department.id,
        **extra,
    )


def _clean_name(typed):
    name, key = clean_department_name(typed)
    if not name or len(name) > MAX_NAME_LENGTH:
        raise CodedError("DEPARTMENT_NAME_INVALID")
    return name, key


def _taken(school_id, key, exclude_id=None):
    existing = Department.objects.filter(school_id=school_id, name_key=key)
    if exclude_id is not None:
        existing = existing.exclude(pk=exclude_id)
    return existing.first()


def _name_taken(existing):
    return CodedError("DEPARTMENT_NAME_TAKEN", params={"name": existing.name})


def create_department(actor, name, description="", request=None):
    name, key = _clean_name(name)
    school_id = actor.school_id
    with transaction.atomic():
        existing = _taken(school_id, key)
        if existing is not None:
            raise _name_taken(existing)
        try:
            with transaction.atomic():
                department = Department.objects.create(
                    school_id=school_id,
                    name=name,
                    description=(description or "")[:500],
                    created_by_id=actor.pk,
                )
        except IntegrityError:
            existing = _taken(school_id, key)
            if existing is None:
                raise
            raise _name_taken(existing) from None
        _emit(
            AuditAction.DEPARTMENT_CREATE,
            actor,
            department,
            target_type="Department",
            target_id=department.id,
            request=request,
        )
    return department


def update_department(actor, department, name=None, description=None, request=None):
    changed = []
    with transaction.atomic():
        department = Department.objects.select_for_update().get(pk=department.pk)
        if name is not None:
            new_name, key = _clean_name(name)
            if key != department.name_key:
                existing = _taken(department.school_id, key, exclude_id=department.pk)
                if existing is not None:
                    raise _name_taken(existing)
            if new_name != department.name:
                department.name = new_name
                changed.append("name")
        if description is not None and description[:500] != department.description:
            department.description = description[:500]
            changed.append("description")
        if changed:
            try:
                with transaction.atomic():
                    department.save()
            except IntegrityError:
                existing = _taken(department.school_id, department.name_key)
                if existing is None:
                    raise
                raise _name_taken(existing) from None
            _emit(
                AuditAction.DEPARTMENT_UPDATE,
                actor,
                department,
                target_type="Department",
                target_id=department.id,
                request=request,
                metadata={"changed_fields": changed},
            )
    return department


def _lock_delete_targets(department, target_id):
    """Lock the department (and the reassign target) in one pk-ordered
    statement so two crossing reassigns cannot deadlock. Returns
    (department, target or None); a bad target is one error whatever the
    reason (unknown, malformed, itself, other school)."""
    target_uuid = _parse_uuid(target_id) if target_id is not None else None
    wanted = [department.pk] + ([target_uuid] if target_uuid else [])
    rows = {
        d.pk: d
        for d in Department.objects.select_for_update()
        .filter(pk__in=wanted)
        .order_by("pk")
    }
    locked = rows[department.pk]
    if target_id is None:
        return locked, None
    target = rows.get(target_uuid) if target_uuid else None
    if target is None or target.pk == locked.pk or target.school_id != locked.school_id:
        raise CodedError("DEPARTMENT_REASSIGN_TARGET_INVALID")
    return locked, target


def delete_department(actor, department, content=None, target_id=None, request=None):
    """Remove the department and, with it, its memberships. One event.
    Content (registered kinds) needs an explicit choice: `content` is
    "delete" or "reassign" (with `target_id`); anything else with content
    present is a 409 and nothing changes. All or nothing."""
    with transaction.atomic():
        department, target = _lock_delete_targets(
            department, target_id if content == "reassign" else None
        )
        if content == "reassign" and target is None:
            raise CodedError("DEPARTMENT_REASSIGN_TARGET_INVALID")
        item_count = sum(registry.counts(department).values())
        if item_count and content not in ("delete", "reassign"):
            raise CodedError(
                "DEPARTMENT_CONTENT_CHOICE_REQUIRED",
                params={"item_count": item_count},
            )
        choice = content if item_count else "none"
        if content == "reassign" and not item_count:
            choice = "reassign"
        if content == "delete" and not item_count:
            choice = "delete"
        if item_count:
            for kind in registry.kinds():
                if content == "reassign":
                    kind.move(department, target)
                else:
                    kind.delete(department)
        member_ids = [
            str(pk)
            for pk in DepartmentMembership.objects.filter(department=department)
            .select_for_update()
            .order_by("pk")
            .values_list("teacher_id", flat=True)
        ]
        metadata = {
            "member_count": len(member_ids),
            "member_ids": member_ids[:MEMBER_IDS_CAP],
            "member_ids_truncated": len(member_ids) > MEMBER_IDS_CAP,
            "item_count": item_count,
            "delete_choice": choice,
        }
        if content == "reassign" and target is not None:
            metadata["target_department_id"] = str(target.id)
        department_id = department.id
        school_id = department.school_id
        department.delete()
        emit(
            AuditAction.DEPARTMENT_DELETE,
            actor=actor,
            request=request,
            target_type="Department",
            target_id=department_id,
            school_id=school_id,
            department_id=department_id,
            metadata=metadata,
        )
    return {"deleted": True, "member_count": len(member_ids)}


# --- members ---------------------------------------------------------------


def _parse_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _add_one(actor, department, raw_id, request):
    """None when added, else the CodedError for that teacher. An unknown,
    malformed, student, admin, other-school or deactivated id all answer
    TEACHER_NOT_ON_LICENCE alike, so the route is no oracle."""
    not_on_license = CodedError("TEACHER_NOT_ON_LICENCE")
    teacher_id = _parse_uuid(raw_id)
    if teacher_id is None:
        return not_on_license
    try:
        with transaction.atomic():
            # Lock order (plan 6.2): allocation, teacher, department, membership.
            school_id = department.school_id
            allocation = (
                SchoolCreditAllocation.objects.select_for_update()
                .filter(
                    user_id=teacher_id,
                    is_active=True,
                    is_admin_allocation=False,
                    license_subscription_id=newest_license_id(school_id),
                )
                .first()
            )
            if allocation is None:
                return not_on_license
            teacher = CustomUser.objects.select_for_update().get(pk=teacher_id)
            if not live_teachers(school_id).filter(pk=teacher.pk).exists():
                return not_on_license
            Department.objects.select_for_update(no_key=True).get(pk=department.pk)
            if DepartmentMembership.objects.filter(
                department=department, teacher=teacher
            ).exists():
                return CodedError("DEPARTMENT_ALREADY_MEMBER")
            if (
                DepartmentMembership.objects.filter(department=department).count()
                >= MAX_MEMBERS_PER_DEPARTMENT
            ):
                return CodedError("DEPARTMENT_TOO_MANY_TEACHERS")
            membership = DepartmentMembership(
                department=department, teacher=teacher, added_by_id=actor.pk
            )
            try:
                membership.save()
            except ValidationError:
                return not_on_license
            _emit(
                AuditAction.DEPARTMENT_MEMBER_ADD,
                actor,
                department,
                target_type="DepartmentMembership",
                target_id=membership.id,
                request=request,
                metadata={"teacher_id": str(teacher.id), "source": SOURCE_ADMIN},
            )
            notifications.send_added_to_department_email(teacher, department)
    except CustomUser.DoesNotExist:
        return not_on_license
    return None


def add_members(actor, department, teacher_ids, request=None):
    """Add up to 50 teachers; partial success, per-item coded failures."""
    if len(teacher_ids) > MAX_TEACHERS_PER_ADD:
        raise CodedError("DEPARTMENT_BATCH_TOO_LARGE")
    current = DepartmentMembership.objects.filter(department=department).count()
    if current + len(set(map(str, teacher_ids))) > MAX_MEMBERS_PER_DEPARTMENT:
        raise CodedError("DEPARTMENT_TOO_MANY_TEACHERS")
    added = []
    errors = []
    for raw_id in teacher_ids:
        error = _add_one(actor, department, raw_id, request)
        if error is None:
            added.append(str(raw_id))
        else:
            errors.append((str(raw_id), error))
    return added, errors


def set_member_edit(actor, department, membership, can_edit, request=None):
    with transaction.atomic():
        membership = DepartmentMembership.objects.select_for_update().get(
            pk=membership.pk
        )
        if membership.can_edit != can_edit:
            before = {"can_edit": membership.can_edit}
            membership.can_edit = can_edit
            membership.save(update_fields=["can_edit", "updated_at"])
            _emit(
                AuditAction.PERMISSION_CHANGE,
                actor,
                department,
                target_type="DepartmentMembership",
                target_id=membership.id,
                request=request,
                before=before,
                after={"can_edit": can_edit},
                metadata={"source": "department"},
            )
    return membership


def remove_member(actor, department, membership, request=None):
    with transaction.atomic():
        membership = (
            DepartmentMembership.objects.select_for_update()
            .filter(pk=membership.pk)
            .first()
        )
        if membership is None:
            return
        teacher = membership.teacher
        teacher_id = membership.teacher_id
        membership_id = membership.id
        membership.delete()
        _emit(
            AuditAction.DEPARTMENT_MEMBER_REMOVE,
            actor,
            department,
            target_type="DepartmentMembership",
            target_id=membership_id,
            request=request,
            metadata={"teacher_id": str(teacher_id), "source": SOURCE_ADMIN},
        )
        notifications.send_removed_from_department_email(teacher, department)


#: "do not judge this dimension" for `end_memberships`.
KEEP = object()


def end_memberships(
    teacher_id,
    new_school_id=KEEP,
    new_user_type=KEEP,
    *,
    source,
    actor=None,
    request=None,
):
    """End the memberships that would be invalid once the teacher has
    `new_school_id` and `new_user_type` (plan 6.2, R1: by state, never by a
    diff, so a stale caller can at worst look and find nothing). Idempotent:
    a second call finds nothing and writes nothing. One REMOVE event per row.
    Not swallowed: a failure here must abort the caller's change (fail
    closed). Returns the number of memberships ended."""
    rows = list(
        DepartmentMembership.objects.filter(teacher_id=teacher_id)
        .select_related("department")
        .select_for_update(of=("self",))
        .order_by("pk")
    )
    leaves_teaching = new_user_type is not KEEP and new_user_type != UserTypes.TEACHER
    ended = 0
    for membership in rows:
        department = membership.department
        wrong_school = new_school_id is not KEEP and (
            department.school_id != new_school_id
        )
        if not (leaves_teaching or wrong_school):
            continue
        membership_id = membership.id
        membership.delete()
        _emit(
            AuditAction.DEPARTMENT_MEMBER_REMOVE,
            actor,
            department,
            target_type="DepartmentMembership",
            target_id=membership_id,
            request=request,
            metadata={"teacher_id": str(teacher_id), "source": source},
        )
        ended += 1
    return ended
