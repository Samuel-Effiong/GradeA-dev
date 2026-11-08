"""Epic C1: the tag API (plan 11 §3.4).

Dark by default (SM ruling 1). While TAGS_ENABLED is off every tag route
answers exactly what an unknown URL answers (AutoGrader.handlers.json_404),
from `dispatch()`, before authentication, permissions or throttling run, so
no 401 or 403 reveals that the route exists.

With the flag on, only teachers are let in (403 otherwise; 401 without a
sign-in). Every lookup is the requesting teacher's own, so another
teacher's tag or assignment is a 404, never a 403 that confirms it exists.
Only the course's own teacher can tag its assignments
(`teacher_course_access_q`; SM ruling on D7).
"""

from typing import TYPE_CHECKING

from django.conf import settings
from django.db.models import Count
from django.shortcuts import get_object_or_404
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from assignments.models import Assignment
from AutoGrader.handlers import json_404
from AutoGrader.reason_codes import CodedError
from classrooms.models import teacher_course_access_q
from classrooms.permissions import IsTeacher

from . import services
from .models import OwnerType, Tag
from .normalize import MAX_NAME_LENGTH, normalize_tag_name
from .serializers import (
    AssignmentTagsSerializer,
    DeleteQuerySerializer,
    TagCreateSerializer,
    TagListQuerySerializer,
    TagListSerializer,
    TagSerializer,
)

UUID_PATTERN = "[0-9a-fA-F-]{36}"


if TYPE_CHECKING:
    _ViewBase = APIView
else:
    _ViewBase = object


class DarkUntilEnabled(_ViewBase):
    """While TAGS_ENABLED is off, answer as an unknown URL does."""

    def dispatch(self, request, *args, **kwargs):
        if not getattr(settings, "TAGS_ENABLED", False):
            return json_404(request)
        return super().dispatch(request, *args, **kwargs)


class TagViewSet(
    DarkUntilEnabled,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    permission_classes = (IsTeacher,)
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    lookup_value_regex = UUID_PATTERN

    def get_queryset(self):
        return Tag.objects.filter(
            owner_type=OwnerType.TEACHER, teacher_id=self.request.user.pk
        )

    def get_serializer_class(self):
        return TagListSerializer if self.action == "list" else TagSerializer

    def filter_queryset(self, queryset):
        if self.action != "list":
            return queryset
        query = TagListQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        tag_type = query.validated_data.get("tag_type")
        if tag_type:
            queryset = queryset.filter(tag_type=tag_type)
        typed = query.validated_data.get("q", "")
        if typed.strip():
            # The same rule as the names, so " CELL" finds "Cells".
            try:
                prefix = normalize_tag_name(typed)
            except CodedError:
                prefix = None
            if prefix is None or len(prefix) > MAX_NAME_LENGTH:
                return queryset.none()
            queryset = queryset.filter(normalized_name__startswith=prefix)
        # FE-C-03: most-used first, then by name; id breaks ties for paging.
        return queryset.annotate(usage_count=Count("assignment_links")).order_by(
            "-usage_count", "normalized_name", "id"
        )

    def create(self, request, *args, **kwargs):
        body = TagCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        name = request.data.get("name") if hasattr(request.data, "get") else None
        tag, created = services.get_or_create_tag(
            request.user, body.validated_data["tag_type"], name
        )
        return Response(
            TagSerializer(tag).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def partial_update(self, request, *args, **kwargs):
        tag = self.get_object()
        name = request.data.get("name") if hasattr(request.data, "get") else None
        services.rename_tag(tag, name)
        return Response(TagSerializer(tag).data)

    def destroy(self, request, *args, **kwargs):
        tag = self.get_object()
        query = DeleteQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        services.delete_tag(tag, query.validated_data["expected_count"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["get"])
    def impact(self, request, *args, **kwargs):
        return Response({"affected_count": services.tag_impact(self.get_object())})


class AssignmentTagsView(DarkUntilEnabled, APIView):
    """PUT /assignments/{id}/tags {tag_ids: [...]}: replace the set.

    Its own view (SM correction (b)): AssignmentViewSet has no PUT, and
    adding one would open PUT /assignments/{id} too."""

    permission_classes = (IsTeacher,)
    http_method_names = ["put", "options"]

    def put(self, request, pk):
        raw = request.data.get("tag_ids") if hasattr(request.data, "get") else None
        if isinstance(raw, list) and len(raw) > services.MAX_TAGS_PER_ASSIGNMENT:
            # Gate 4: before any parsing, so 10,000 ids cost nothing.
            raise CodedError("TOO_MANY_TAGS")
        body = AssignmentTagsSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        # Only the course's own teacher: anyone else gets the house
        # not-found answer for an assignment (H-296), never a 403.
        assignment = get_object_or_404(
            Assignment.objects.filter(
                teacher_course_access_q(request.user, prefix="course__")
            ).only("id", "course_id"),
            pk=pk,
        )
        tags = services.set_assignment_tags(
            assignment, body.validated_data["tag_ids"], teacher=request.user
        )
        return Response({"tags": TagSerializer(tags, many=True).data})
