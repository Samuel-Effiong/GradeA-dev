"""Epic F1 routes. While DEPARTMENTS_ENABLED is off every route answers as an
unknown URL does, before sign-in, permissions or throttling run (the C1 rule),
so no 401 or 403 reveals that the route exists.

Two prefixes and two small view sets, never one view that picks behavior by
role: `/school-admin/departments` (school admins only) and `/departments`
(licensed teachers). Every lookup goes through `departments.access`.
"""

import uuid

from django.conf import settings
from django.db.models import Count
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from audit.admin_action import emit_denied
from AutoGrader.handlers import json_404
from AutoGrader.reason_codes import coded_entry
from classrooms.permissions import IsSchoolAdmin, IsTeacher
from users.models import CustomUser, UserTypes

from . import service, wording
from .access import admin_school_id, live_teachers, visible_departments
from .models import Department, DepartmentMembership
from .serializers import (
    AddMembersSerializer,
    AdminMemberRowSerializer,
    DepartmentRenameSerializer,
    DepartmentRowSerializer,
    DepartmentWriteSerializer,
    MemberEditSerializer,
    MyDepartmentSerializer,
    RosterRowSerializer,
)


class DarkUntilEnabled:
    def dispatch(self, request, *args, **kwargs):
        if not getattr(settings, "DEPARTMENTS_ENABLED", False):
            return json_404(request)
        return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]


class DepartmentAdminPermission(IsSchoolAdmin):
    """IsSchoolAdmin, and a refusal of a signed-in person leaves one DENIED
    event (the route-coverage rule: a write leaves exactly one)."""

    def has_permission(self, request, view):
        allowed = super().has_permission(request, view)
        if not allowed and request.user and request.user.is_authenticated:
            emit_denied(request, view)
        return allowed


def _uuid_or_404(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise NotFound() from None


def _full_name(first, last):
    return f"{first or ''} {last or ''}".strip()


def _admin_department(user, department_id):
    school_id = admin_school_id(user)
    if school_id is None:
        raise NotFound()
    department = Department.objects.filter(
        pk=_uuid_or_404(department_id), school_id=school_id
    ).first()
    if department is None:
        raise NotFound()
    return department


def _row(department, member_count):
    return {
        "id": department.id,
        "name": department.name,
        "description": department.description,
        "member_count": member_count,
        "created_at": department.created_at,
    }


class AdminBase(DarkUntilEnabled, APIView):
    permission_classes = [DepartmentAdminPermission]

    def get_audit_target(self, request):
        return "Department", self.kwargs.get("department_id")


@extend_schema(tags=["School Admin Departments"])
class AdminDepartmentListView(AdminBase):
    def get(self, request):
        school_id = admin_school_id(request.user)
        if school_id is None:
            return Response([])
        rows = (
            visible_departments(request.user)
            .annotate(n=Count("memberships"))
            .order_by("name_key", "id")
        )
        data = [_row(d, d.n) for d in rows]
        return Response(DepartmentRowSerializer(data, many=True).data)

    def post(self, request):
        if admin_school_id(request.user) is None:
            raise NotFound()
        body = DepartmentWriteSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        department = service.create_department(
            request.user,
            body.validated_data["name"],
            body.validated_data.get("description") or "",
            request=request,
        )
        return Response(
            DepartmentRowSerializer(_row(department, 0)).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["School Admin Departments"])
class AdminDepartmentDetailView(AdminBase):
    def get(self, request, department_id):
        department = _admin_department(request.user, department_id)
        members = (
            DepartmentMembership.objects.filter(department=department)
            .select_related("teacher")
            .order_by("teacher__last_name", "teacher__first_name", "id")
        )
        rows = [
            {
                "membership_id": m.id,
                "teacher_id": m.teacher_id,
                "name": _full_name(m.teacher.first_name, m.teacher.last_name),
                "email": m.teacher.email,
                "can_edit": m.can_edit,
                "added_at": m.created_at,
            }
            for m in members
        ]
        data = DepartmentRowSerializer(_row(department, len(rows))).data
        data["members"] = AdminMemberRowSerializer(rows, many=True).data
        return Response(data)

    def patch(self, request, department_id):
        department = _admin_department(request.user, department_id)
        body = DepartmentRenameSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        department = service.update_department(
            request.user,
            department,
            name=body.validated_data.get("name"),
            description=body.validated_data.get("description"),
            request=request,
        )
        count = DepartmentMembership.objects.filter(department=department).count()
        return Response(DepartmentRowSerializer(_row(department, count)).data)

    def delete(self, request, department_id):
        department = _admin_department(request.user, department_id)
        summary = service.delete_department(request.user, department, request=request)
        return Response(summary)


@extend_schema(tags=["School Admin Departments"])
class AdminMembersView(AdminBase):
    def post(self, request, department_id):
        department = _admin_department(request.user, department_id)
        body = AddMembersSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        added, errors = service.add_members(
            request.user,
            department,
            body.validated_data["teacher_ids"],
            request=request,
        )
        return Response(
            {
                "successful": len(added),
                "failed": len(errors),
                "added": [{"teacher_id": t, "status": "added"} for t in added],
                "errors": [
                    coded_entry(error, teacher_id=teacher_id, status="failed")
                    for teacher_id, error in errors
                ],
            }
        )


@extend_schema(tags=["School Admin Departments"])
class AdminMemberDetailView(AdminBase):
    def _membership(self, request, department_id, membership_id):
        department = _admin_department(request.user, department_id)
        membership = DepartmentMembership.objects.filter(
            pk=_uuid_or_404(membership_id), department=department
        ).first()
        if membership is None:
            raise NotFound()
        return department, membership

    def patch(self, request, department_id, membership_id):
        department, membership = self._membership(request, department_id, membership_id)
        body = MemberEditSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        membership = service.set_member_edit(
            request.user,
            department,
            membership,
            body.validated_data["can_edit"],
            request=request,
        )
        return Response(
            {"membership_id": membership.id, "can_edit": membership.can_edit}
        )

    def delete(self, request, department_id, membership_id):
        department, membership = self._membership(request, department_id, membership_id)
        service.remove_member(request.user, department, membership, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- licensed teachers -----------------------------------------------------


class TeacherBase(DarkUntilEnabled, APIView):
    permission_classes = [IsTeacher]


@extend_schema(tags=["Departments"])
class MyDepartmentsView(TeacherBase):
    def get(self, request):
        rows = list(
            DepartmentMembership.objects.filter(
                department__in=visible_departments(request.user),
                teacher_id=request.user.pk,
            )
            .select_related("department")
            .order_by("department__name_key", "department_id")
        )
        data = [
            {"id": m.department_id, "name": m.department.name, "can_edit": m.can_edit}
            for m in rows
        ]
        return Response(MyDepartmentSerializer(data, many=True).data)


@extend_schema(tags=["Departments"])
class RosterView(TeacherBase):
    def get(self, request, department_id):
        department = (
            visible_departments(request.user)
            .filter(pk=_uuid_or_404(department_id))
            .first()
        )
        if department is None:
            raise NotFound()
        admins = (
            CustomUser.objects.filter(
                user_type=UserTypes.SCHOOL_ADMIN,
                school_id=department.school_id,
                is_active=True,
            )
            .order_by("last_name", "first_name", "id")
            .values_list("first_name", "last_name")
        )
        member_ids = DepartmentMembership.objects.filter(department=department).values(
            "teacher_id"
        )
        teachers = (
            live_teachers(department.school_id)
            .filter(pk__in=member_ids)
            .order_by("last_name", "first_name", "id")
            .values_list("first_name", "last_name")
        )
        rows = [
            {"name": _full_name(*pair), "role": wording.ROLE_SCHOOL_ADMIN}
            for pair in admins
        ] + [
            {"name": _full_name(*pair), "role": wording.ROLE_TEACHER}
            for pair in teachers
        ]
        return Response(RosterRowSerializer(rows, many=True).data)
