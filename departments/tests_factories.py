"""Builders for the departments tests (the interface the matrix tests rely on).

Department rows come from `make_department` and `add_member`. Everything
that is not a department (schools, licenses, licensed teachers, replacing a
license, removing a teacher) goes through the project's own code so the
state is the state the app really produces.
"""

import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    LicenseBillingMethod,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
)
from classrooms.models import School
from departments.models import Department, DepartmentMembership
from users.models import CustomUser, UserTypes

PASSWORD = "dept-pass-123"  # nosec B105 # pragma: allowlist secret


def _tag():
    return uuid.uuid4().hex[:8]


def make_school(name=None):
    return School.objects.create(name=name or f"School {_tag()}")


def make_plan():
    plan, _ = SubscriptionPlan.objects.get_or_create(
        name=PlanType.PRO_LICENSE,
        defaults={
            "display_name": "Department tests license plan",
            "category": PlanCategory.LICENSE,
            "tier": PlanTier.PRO,
            "monthly_credits": 20_000,
            "overage_block_size": 5_000,
            "overage_block_price": 299,
        },
    )
    return plan


def make_user(user_type, *, school=None, first_name="Dept", last_name=None):
    tag = _tag()
    user = CustomUser.objects.create_user(
        email=f"dept-{user_type.lower()}-{tag}@dept.test", password=PASSWORD
    )
    user.user_type = user_type
    user.is_active = True
    user.first_name = first_name
    user.last_name = last_name or user_type.title()
    user.school = school
    user.save()
    return user


def make_admin(school):
    return make_user(UserTypes.SCHOOL_ADMIN, school=school)


def make_license(school, admin, *, is_active=True, **extra):
    now = timezone.now()
    return LicenseSubscription.objects.create(
        school=school,
        admin_user=admin,
        plan=make_plan(),
        billing_cycle_start=now,
        billing_cycle_end=now + timedelta(days=30),
        billing_method=LicenseBillingMethod.OFFLINE,
        is_active=is_active,
        auto_renew=False,
        **{"max_seats": 0, **extra},
    )


def make_licensed_teacher(school, license_sub, **kwargs):
    """A teacher on `school` holding a real allocation on `license_sub`."""
    teacher = make_user(UserTypes.TEACHER, school=school, **kwargs)
    with transaction.atomic():
        LicenseSubscriptionService._enroll_teacher_internal(license_sub, teacher)
    return teacher


def make_school_with_license(*, teachers=2):
    """(school, admin, license, [teachers]) with an active license."""
    school = make_school()
    admin = make_admin(school)
    license_sub = make_license(school, admin)
    staff = [make_licensed_teacher(school, license_sub) for _ in range(teachers)]
    return school, admin, license_sub, staff


def make_department(school, name=None, **fields):
    return Department.objects.create(
        school=school, name=name or f"Dept {_tag()}", **fields
    )


def add_member(department, teacher, *, can_edit=True, added_by=None):
    return DepartmentMembership.objects.create(
        department=department,
        teacher=teacher,
        can_edit=can_edit,
        added_by_id=(added_by.id if added_by else None),
    )
