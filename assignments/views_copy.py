"""Epic E2: single and bulk assignment copy (FR-E-01/02; plan §4-5).

Dark by default: while ASSIGNMENT_COPY_ENABLED is off both routes answer
exactly what an unknown URL answers (`json_404`), before authentication.
With it on, only teachers are let in; a source or destination the teacher
cannot reach is "not found" (single copy: 404; bulk: that item's result).
"""

from typing import TYPE_CHECKING

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from AutoGrader.handlers import json_404
from classrooms.permissions import IsTeacher

from . import copy as copying
from .serializers import AssignmentDetailSerializer

if TYPE_CHECKING:
    _ViewBase = APIView
else:
    _ViewBase = object


class DarkUntilCopy(_ViewBase):
    def dispatch(self, request, *args, **kwargs):
        if not getattr(settings, "ASSIGNMENT_COPY_ENABLED", False):
            return json_404(request)
        return super().dispatch(request, *args, **kwargs)


class CopyRequestSerializer(serializers.Serializer):
    course_id = serializers.UUIDField()


class BulkCopyRequestSerializer(serializers.Serializer):
    course_id = serializers.UUIDField()
    assignment_ids = serializers.ListField(
        child=serializers.UUIDField(), allow_empty=False
    )

    def validate_assignment_ids(self, value):
        if len(dict.fromkeys(value)) > copying.BULK_LIMIT:
            raise ValidationError(copying.W10_BULK_LIMIT)
        return value


@extend_schema(tags=["Assignments"])
class AssignmentCopyView(DarkUntilCopy, APIView):
    """POST assignments/{id}/copy {course_id}: one copy, as a draft."""

    permission_classes = (IsAuthenticated, IsTeacher)
    http_method_names = ["post", "options"]

    def post(self, request, pk):
        body = CopyRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        course_id = body.validated_data["course_id"]
        try:
            source = copying.reachable_source(request.user, pk)
            destination = copying.reachable_destination(request.user, course_id)
            new = copying.copy_assignment(
                source, destination, actor=request.user, request=request
            )
        except copying.CopyRefused as refusal:
            copying.refuse_audit(
                request.user, request, refusal, source_id=pk, course_id=course_id
            )
            raise NotFound(refusal.message) from None
        return Response(
            AssignmentDetailSerializer(new, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


@extend_schema(tags=["Assignments"])
class AssignmentBulkCopyView(DarkUntilCopy, APIView):
    """POST assignments/bulk-copy {assignment_ids, course_id}: up to 50
    (Q9); each item succeeds or fails on its own, with a reason code."""

    permission_classes = (IsAuthenticated, IsTeacher)
    http_method_names = ["post", "options"]

    def post(self, request):
        body = BulkCopyRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        results = copying.bulk_copy(
            body.validated_data["assignment_ids"],
            body.validated_data["course_id"],
            actor=request.user,
            request=request,
        )
        succeeded = sum(1 for item in results if item["ok"])
        return Response(
            {
                "results": results,
                "succeeded_count": succeeded,
                "failed_count": len(results) - succeeded,
            },
            status=status.HTTP_200_OK,
        )
