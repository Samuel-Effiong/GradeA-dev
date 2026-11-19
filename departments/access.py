"""Who may reach a department: the one place (plan section 5).

Everything about "may this person see or change this department" is decided
here and nowhere else. A membership row alone never grants anything: a
teacher must also be live in the department's school (`live_teachers`).

The liveness rule (owner Q1, plan 5.2): an active TEACHER whose `school_id`
is the school, the school is active, and who holds an active non-admin
allocation on the school's NEWEST license row, active or not. A lapse, a
cancel or a failed renewal changes none of that. A removal clears `school_id`
and the allocation; a replacement license is newer than the allocation of a
teacher left off it, so that teacher is cut off.
"""

from django.db.models import Exists, OuterRef, Subquery

from billing.models import LicenseSubscription, SchoolCreditAllocation
from users.models import CustomUser, UserTypes

from .models import Department, DepartmentMembership

SURFACES = ("lessons", "library")


def newest_license_id(school_ref):
    return Subquery(
        LicenseSubscription.objects.filter(school_id=school_ref)
        .order_by("-created_at", "-id")
        .values("id")[:1]
    )


def live_teachers(school_id):
    """Users who are live teachers of `school_id` right now."""
    if school_id is None:
        return CustomUser.objects.none()
    allocation = SchoolCreditAllocation.objects.filter(
        user_id=OuterRef("pk"),
        is_active=True,
        is_admin_allocation=False,
        license_subscription_id=newest_license_id(school_id),
    )
    return CustomUser.objects.filter(
        user_type=UserTypes.TEACHER,
        is_active=True,
        school_id=school_id,
        school__is_active=True,
    ).filter(Exists(allocation))


def is_licensed_teacher_of(user, school_id):
    """The liveness predicate, object form (one indexed query)."""
    if (
        school_id is None
        or user is None
        or not getattr(user, "is_authenticated", False)
        or user.user_type != UserTypes.TEACHER
        or user.school_id != school_id
    ):
        return False
    return live_teachers(school_id).filter(pk=user.pk).exists()


def admin_school_id(user):
    """The school a school admin manages, or None (no school, an inactive
    school, or not a school admin). `filter(school_id=None)` means IS NULL,
    so callers must never pass this value through without the None check."""
    if (
        user is None
        or not getattr(user, "is_authenticated", False)
        or user.user_type != UserTypes.SCHOOL_ADMIN
        or user.school_id is None
    ):
        return None
    school = user.school
    return school.id if school is not None and school.is_active else None


def visible_departments(user):
    """The departments `user` may see: an admin's own school's, a live
    teacher's own memberships, nothing for anyone else."""
    school_id = admin_school_id(user)
    if school_id is not None:
        return Department.objects.filter(school_id=school_id)
    if user is not None and is_licensed_teacher_of(user, user.school_id):
        return Department.objects.filter(
            school_id=user.school_id, memberships__teacher_id=user.pk
        )
    return Department.objects.none()


def admin_department(user, department_pk):
    """The department `department_pk` when `user` is an admin of its school,
    else None (one answer for unknown, malformed and foreign ids)."""
    if admin_school_id(user) is None:
        return None
    return visible_departments(user).filter(pk=department_pk).first()


def live_memberships(user):
    """The live teacher's memberships (empty for anyone else)."""
    if user is None or not is_licensed_teacher_of(user, user.school_id):
        return DepartmentMembership.objects.none()
    return DepartmentMembership.objects.filter(
        teacher_id=user.pk, department__school_id=user.school_id
    )


def member_department_ids(user):
    """Ids of the departments the user is a live member of. The function
    later epics (tags, lessons, library) must call."""
    return live_memberships(user).values_list("department_id", flat=True)


def can_member_edit(user, department, surface, lock=False):
    """May `user` edit `surface` ("lessons" or "library") in `department`?
    Never names a role: the admin's rights over the library are data, decided
    by the epic that builds the library (BE-F-08). `lock=True` takes the
    membership row's lock so a flag change cannot interleave with the edit;
    call it inside a transaction."""
    if surface not in SURFACES:
        raise ValueError(f"unknown surface: {surface!r}")
    memberships = live_memberships(user).filter(department_id=department.pk)
    if lock:
        memberships = memberships.select_for_update(of=("self",))
    membership = memberships.first()
    return bool(membership and membership.can_edit)
