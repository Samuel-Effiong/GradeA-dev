"""API documentation for the assignment copy routes (drf-spectacular).
Docs only: nothing here is read at request time."""

from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers

from .serializers import AssignmentDetailSerializer


def many(serializer, **kwargs):
    """The same inline serializer as a list."""
    return type(serializer)(many=True, **kwargs)


TAG = "Assignments"

COPY_BODY = inline_serializer(
    "AssignmentCopyBody",
    {"course_id": serializers.UUIDField(help_text="The destination course.")},
)
BULK_COPY_BODY = inline_serializer(
    "AssignmentBulkCopyBody",
    {
        "course_id": serializers.UUIDField(help_text="The destination course."),
        "assignment_ids": serializers.ListField(
            child=serializers.UUIDField(),
            help_text="1 to 50 distinct assignment ids.",
        ),
    },
)

ERROR_BODY = inline_serializer(
    "AssignmentCopyError",
    {"detail": serializers.CharField(help_text="The message to show.")},
)

FLAG_OFF = (
    "While the copy switch is off every route answers as an unknown URL (404), "
    "before sign-in."
)
WHO = (
    "Teachers only. The source assignment and the destination course must both be "
    "reachable by the caller; otherwise the house 404 comes back, never a 403."
)

BULK_ITEM = inline_serializer(
    "BulkCopyItem",
    {
        "source_id": serializers.UUIDField(),
        "ok": serializers.BooleanField(),
        "assignment_id": serializers.UUIDField(
            required=False, help_text="The new draft, when `ok`."
        ),
        "title": serializers.CharField(required=False, help_text="When `ok`."),
        "reason_code": serializers.CharField(
            required=False, help_text="Why this one was refused, when not `ok`."
        ),
        "error": serializers.CharField(required=False, help_text="When not `ok`."),
    },
)
BULK_RESULT = inline_serializer(
    "BulkCopyResult",
    {
        "results": many(BULK_ITEM),
        "succeeded_count": serializers.IntegerField(),
        "failed_count": serializers.IntegerField(),
    },
)

copy_assignment = extend_schema(
    tags=[TAG],
    operation_id="assignments_copy_create",
    summary="Copy an assignment into a course",
    description=f"Makes one copy of the assignment in the destination course, as a "
    f"draft. {WHO} {FLAG_OFF}",
    request=COPY_BODY,
    responses={
        201: AssignmentDetailSerializer,
        400: OpenApiResponse(description="`course_id` missing or invalid."),
        403: OpenApiResponse(ERROR_BODY, description="Not a teacher."),
        404: OpenApiResponse(
            ERROR_BODY,
            description="Source or destination not reachable; flag off.",
        ),
    },
)
bulk_copy_assignments = extend_schema(
    tags=[TAG],
    operation_id="assignments_bulk_copy_create",
    summary="Copy several assignments into a course",
    description=f"Up to 50 distinct assignments in one request. Each one succeeds or "
    f"fails on its own, with a reason code, and one failure never stops the rest. "
    f"No credits are spent. {WHO} {FLAG_OFF}",
    request=BULK_COPY_BODY,
    responses={
        200: BULK_RESULT,
        400: OpenApiResponse(description="Empty list, more than 50 ids, or a bad id."),
        403: OpenApiResponse(ERROR_BODY, description="Not a teacher."),
        404: OpenApiResponse(ERROR_BODY, description="Flag off."),
    },
)
