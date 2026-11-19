"""Epic E2: the grade-category API (plan docs/evidence/epic-e2/STEP0_PLAN.md
§4; FR-E-03/04/08/12/13).

Dark by default: while GRADE_CATEGORIES_ENABLED is off every route here
answers exactly what an unknown URL answers (`json_404`), from `dispatch()`,
before authentication, so no 401 or 403 reveals that it exists.

With the flag on:

* a course's categories, preview, breakdown, grading policy and excuses are
  for the teacher who can reach the course (H-38); any other course is a
  404, never a 403 that confirms it exists;
* category templates: a teacher manages their own and reads their school's;
  a school admin manages their school's (Q5);
* students get nothing here.
"""

import uuid
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from assignments.models import Assignment
from audit.emitter import emit
from audit.enums import AuditAction, AuditOutcome
from AutoGrader.handlers import json_404
from classrooms import docs_grade_categories as docs
from classrooms import grade_categories, grade_service
from classrooms.models import (
    CategoryTemplate,
    CategoryTemplateOwner,
    CourseCategory,
    StudentCourse,
    reachable_courses,
)
from classrooms.permissions import IsTeacher
from users.models import UserTypes

W12_TEMPLATE_NAME_TAKEN = 'You already have a template called "{name}".'

if TYPE_CHECKING:
    _ViewBase = APIView
else:
    _ViewBase = object


class DarkUntilCategories(_ViewBase):
    """While GRADE_CATEGORIES_ENABLED is off, answer as an unknown URL does."""

    def dispatch(self, request, *args, **kwargs):
        if not getattr(settings, "GRADE_CATEGORIES_ENABLED", False):
            return json_404(request)
        return super().dispatch(request, *args, **kwargs)


def _decimal(value):
    return None if value is None else str(value)


class CategoryItemSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False, allow_null=True)
    name = serializers.CharField(max_length=100)
    weight_percent = serializers.DecimalField(max_digits=5, decimal_places=2)


class CategorySetSerializer(serializers.Serializer):
    categories = CategoryItemSerializer(many=True, allow_empty=True)


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseCategory
        fields = ["id", "name", "weight_percent", "position"]


class _CourseView(DarkUntilCategories, APIView):
    permission_classes = (IsAuthenticated, IsTeacher)

    def course(self, pk):
        return get_object_or_404(
            reachable_courses(self.request.user).select_related("session"), pk=pk
        )


def _items(request):
    body = CategorySetSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    return [
        {
            "id": item.get("id"),
            "name": item["name"],
            "weight_percent": item["weight_percent"],
        }
        for item in body.validated_data["categories"]
    ]


@extend_schema(tags=["Grade Categories"])
class CourseCategoriesView(_CourseView):
    """GET / PUT course/{id}/categories: the whole set, replaced as one.
    The weights add up to 100, or there are none (FR-E-04)."""

    http_method_names = ["get", "put", "options"]

    def _payload(self, course):
        return {
            "categories": CategorySerializer(
                course.grade_categories.order_by("position", "name"), many=True
            ).data,
            "policy": grade_categories.grading_policy(course),
        }

    @docs.get_categories
    def get(self, request, pk):
        return Response(self._payload(self.course(pk)))

    @docs.put_categories
    def put(self, request, pk):
        course = self.course(pk)
        grade_categories.set_course_categories(
            course, _items(request), actor=request.user, request=request
        )
        return Response(self._payload(course))


@extend_schema(tags=["Grade Categories"])
class CategoryPreviewView(_CourseView):
    """POST course/{id}/categories/preview: the grades a proposed set would
    give, now and proposed. Writes nothing (FR-E-12)."""

    http_method_names = ["post", "options"]

    @docs.preview_categories
    def post(self, request, pk):
        course = self.course(pk)
        items = _items(request)
        grade_categories.validate_category_items(items)
        preview = grade_service.preview_course_grades(course.pk, items)
        return Response(
            {
                "students": [
                    {
                        "student_id": row["student_id"],
                        "current": _decimal(row["current"]),
                        "proposed": _decimal(row["proposed"]),
                    }
                    for row in preview["students"]
                ],
                "class_average": {
                    key: _decimal(value)
                    for key, value in preview["class_average"].items()
                },
            }
        )


@extend_schema(tags=["Grade Categories"])
class GradeBreakdownView(_CourseView):
    """GET course/{id}/grade-breakdown?student={id}: one student's grade by
    category; the contributions add up to the final grade (FR-E-13)."""

    http_method_names = ["get", "options"]

    @docs.grade_breakdown
    def get(self, request, pk):
        course = self.course(pk)
        try:
            student_id = uuid.UUID(str(request.query_params.get("student")))
        except ValueError:
            student_id = None
        if (
            student_id is None
            or not StudentCourse.objects.filter(
                course=course, student_id=student_id
            ).exists()
        ):
            raise ValidationError({"student": [grade_categories.W11_NOT_IN_COURSE]})
        breakdown = grade_service.grade_breakdown(student_id, course.pk)
        return Response(
            {
                "final_grade": _decimal(breakdown["final_grade"]),
                "categories": [
                    {
                        **row,
                        "weight_percent": _decimal(row["weight_percent"]),
                        "grade": _decimal(row["grade"]),
                        "contribution": _decimal(row["contribution"]),
                    }
                    for row in breakdown["categories"]
                ],
                "uncategorized": breakdown["uncategorized"],
                "excused_count": breakdown["excused_count"],
                "missing_count": breakdown["missing_count"],
                "missing_counts_as_zero": breakdown["missing_counts_as_zero"],
            }
        )


class GradingPolicySerializer(serializers.Serializer):
    missing_counts_as_zero = serializers.BooleanField()


@extend_schema(tags=["Grade Categories"])
class GradingPolicyView(_CourseView):
    """GET / PATCH course/{id}/grading-policy: how excused, missing,
    ungraded and uncategorized work count, in the approved words
    (FR-E-04/08, W5, W13). PATCH sets the course's missing-work rule (Q2);
    the course's grades are recomputed when it changes."""

    http_method_names = ["get", "patch", "options"]

    @docs.get_policy
    def get(self, request, pk):
        return Response(grade_categories.grading_policy(self.course(pk)))

    @docs.patch_policy
    def patch(self, request, pk):
        course = self.course(pk)
        body = GradingPolicySerializer(data=request.data)
        body.is_valid(raise_exception=True)
        value = body.validated_data["missing_counts_as_zero"]
        if course.missing_counts_as_zero != value:
            with transaction.atomic():
                course.missing_counts_as_zero = value
                course.save(update_fields=["missing_counts_as_zero"])
        return Response(grade_categories.grading_policy(course))


class ExcuseSerializer(serializers.Serializer):
    student = serializers.UUIDField()


@extend_schema(tags=["Assignments"])
class ExcusedWorkView(DarkUntilCategories, APIView):
    """POST / DELETE assignments/{id}/excused (student in the body, or
    ?student= on DELETE): excuse a student from the assignment, or take the
    excuse back (FR-E-08). The student's grade is recomputed."""

    permission_classes = (IsAuthenticated, IsTeacher)
    http_method_names = ["post", "delete", "options"]

    def _target(self, request, pk, data):
        assignment = get_object_or_404(
            Assignment.objects.filter(
                course__in=reachable_courses(request.user)
            ).select_related("course"),
            pk=pk,
        )
        body = ExcuseSerializer(data=data)
        body.is_valid(raise_exception=True)
        enrollment = (
            StudentCourse.objects.filter(
                course_id=assignment.course_id,
                student_id=body.validated_data["student"],
            )
            .select_related("student")
            .first()
        )
        if enrollment is None:
            raise ValidationError({"student": [grade_categories.W11_NOT_IN_COURSE]})
        return assignment, enrollment.student

    @docs.excuse
    def post(self, request, pk):
        assignment, student = self._target(request, pk, request.data)
        row, created = grade_categories.excuse_work(
            assignment, student, actor=request.user, request=request
        )
        return Response(
            {
                "assignment_id": str(assignment.pk),
                "student_id": str(student.pk),
                "excused": True,
                "excused_at": row.excused_at,
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @docs.unexcuse
    def delete(self, request, pk):
        data = request.data if request.data else request.query_params
        assignment, student = self._target(request, pk, data)
        grade_categories.remove_excuse(
            assignment, student, actor=request.user, request=request
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


# --- category templates (FR-E-03, Q5) ----------------------------------------


class TemplateItemSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    weight_percent = serializers.DecimalField(max_digits=5, decimal_places=2)


class CategoryTemplateSerializer(serializers.ModelSerializer):
    items = TemplateItemSerializer(many=True, allow_empty=False)

    class Meta:
        model = CategoryTemplate
        fields = ["id", "name", "owner_type", "items", "created_at", "updated_at"]
        read_only_fields = ["id", "owner_type", "created_at", "updated_at"]

    def validate_name(self, value):
        return grade_categories._clean_name(value)

    def validate_items(self, value):
        cleaned = grade_categories.validate_category_items(
            [dict(item) for item in value]
        )
        return [
            {"name": name, "weight_percent": str(weight)} for _, name, weight in cleaned
        ]


def _templates_visible_to(user):
    if user.user_type == UserTypes.TEACHER:
        visible = Q(owner_type=CategoryTemplateOwner.TEACHER, teacher=user)
        if user.school_id:
            visible |= Q(owner_type=CategoryTemplateOwner.SCHOOL, school=user.school_id)
        return CategoryTemplate.objects.filter(visible)
    if user.user_type == UserTypes.SCHOOL_ADMIN and user.school_id:
        return CategoryTemplate.objects.filter(
            owner_type=CategoryTemplateOwner.SCHOOL, school=user.school_id
        )
    return CategoryTemplate.objects.none()


def _templates_managed_by(user):
    if user.user_type == UserTypes.TEACHER:
        return CategoryTemplate.objects.filter(
            owner_type=CategoryTemplateOwner.TEACHER, teacher=user
        )
    return _templates_visible_to(user)


def templates_visible_to(user):
    """The templates `user` may make a course from (course create)."""
    return _templates_visible_to(user)


def _can_use_templates(user):
    return user.user_type == UserTypes.TEACHER or (
        user.user_type == UserTypes.SCHOOL_ADMIN and bool(user.school_id)
    )


class _TemplateBase(DarkUntilCategories, APIView):
    permission_classes = (IsAuthenticated,)

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _can_use_templates(request.user):
            raise PermissionDenied()

    def _audit(self, template, change, *, template_id=None):
        emit(
            AuditAction.CATEGORY_TEMPLATE_CHANGE,
            actor=self.request.user,
            request=self.request,
            target_type="CategoryTemplate",
            target_id=template_id or template.pk,
            outcome=AuditOutcome.SUCCESS,
            school_id=template.school_id,
            metadata={
                "template_id": str(template_id or template.pk),
                "owner_type": template.owner_type,
                "item_count": len(template.items or []),
                "change": change,
            },
        )

    def _save(self, serializer, **extra):
        name = serializer.validated_data.get("name")
        try:
            with transaction.atomic():
                return serializer.save(**extra)
        except IntegrityError:
            raise ValidationError(
                {"name": [W12_TEMPLATE_NAME_TAKEN.format(name=name)]}
            ) from None


@extend_schema(tags=["Grade Categories"])
class CategoryTemplateListView(_TemplateBase):
    """GET / POST category-templates."""

    http_method_names = ["get", "post", "options"]

    @docs.list_templates
    def get(self, request):
        templates = _templates_visible_to(request.user).order_by(
            "owner_type", "name", "id"
        )
        return Response(CategoryTemplateSerializer(templates, many=True).data)

    @docs.create_template
    def post(self, request):
        serializer = CategoryTemplateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if user.user_type == UserTypes.TEACHER:
            owner = {"owner_type": CategoryTemplateOwner.TEACHER, "teacher": user}
        else:
            owner = {
                "owner_type": CategoryTemplateOwner.SCHOOL,
                "school_id": user.school_id,
            }
        template = self._save(serializer, created_by_id=user.pk, **owner)
        self._audit(template, "create")
        return Response(
            CategoryTemplateSerializer(template).data, status=status.HTTP_201_CREATED
        )


@extend_schema(tags=["Grade Categories"])
class CategoryTemplateDetailView(_TemplateBase):
    """GET / PATCH / DELETE category-templates/{id}. A teacher reads their
    school's templates but changes only their own."""

    http_method_names = ["get", "patch", "delete", "options"]

    @docs.get_template
    def get(self, request, pk):
        template = get_object_or_404(_templates_visible_to(request.user), pk=pk)
        return Response(CategoryTemplateSerializer(template).data)

    def _managed(self, request, pk):
        template = get_object_or_404(_templates_visible_to(request.user), pk=pk)
        if not _templates_managed_by(request.user).filter(pk=template.pk).exists():
            raise PermissionDenied()
        return template

    @docs.patch_template
    def patch(self, request, pk):
        template = self._managed(request, pk)
        serializer = CategoryTemplateSerializer(
            template, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        if "name" not in serializer.validated_data:
            serializer.validated_data["name"] = template.name
        template = self._save(serializer)
        self._audit(template, "update")
        return Response(CategoryTemplateSerializer(template).data)

    @docs.delete_template
    def delete(self, request, pk):
        template = self._managed(request, pk)
        template_id = template.pk
        with transaction.atomic():
            template.delete()
            self._audit(template, "delete", template_id=template_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


def apply_template(course, template_id, *, actor, request=None):
    """Seed a new course's categories from a template the actor may use."""
    template = templates_visible_to(actor).filter(pk=template_id).first()
    if template is None:
        raise NotFound()
    grade_categories.set_course_categories(
        course, list(template.items), actor=actor, request=request
    )
