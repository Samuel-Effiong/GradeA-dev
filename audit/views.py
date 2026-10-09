"""
audit/views.py
===============
Query API for AuditEvent (§8 of docs/phase2/architecture/
04_epic_a_implementation_plan.md).

Two read-only endpoints:
  * /api/v1/super-admin/audit/events - unrestricted, IsSuperAdmin only.
  * /api/v1/school-admin/audit/events - hard-scoped to the caller's own
    school_id at the queryset level. FR-A-09's acceptance criterion: a
    School Admin querying another school's data gets an empty, well-formed
    result - never the other school's rows, never an error revealing they
    exist. See filters.py's docstring for how the `school_id` query param
    is kept off this endpoint's filter surface entirely, not merely
    validated and overridden.

No aggregation - raw event log, paginated with the project-wide
StandardPageNumberPagination, filtered via DjangoFilterBackend so each
filter combination in §8.1 hits one of the indexes already defined on
AuditEvent (audit/models.py).
"""

from typing import TYPE_CHECKING, cast

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics

from classrooms.permissions import IsSchoolAdmin, IsSuperAdmin

from .filters import SchoolAdminAuditEventFilter, SuperAdminAuditEventFilter
from .models import AuditEvent
from .serializers import AuditEventSerializer

if TYPE_CHECKING:
    from users.models import CustomUser

_QUERY_PARAMETERS = [
    OpenApiParameter("actor_id", str, description="Filter by actor UUID."),
    OpenApiParameter("actor_role", str, description="Filter by actor role."),
    OpenApiParameter("action", str, description="Filter by AuditAction value."),
    OpenApiParameter("department_id", str, description="Filter by department UUID."),
    OpenApiParameter("outcome", str, description="Filter by outcome."),
    OpenApiParameter("reason_code", str, description="Filter by reason code."),
    OpenApiParameter(
        "time_from", str, description="ISO-8601 lower bound on occurred_at."
    ),
    OpenApiParameter(
        "time_to", str, description="ISO-8601 upper bound on occurred_at."
    ),
    OpenApiParameter("page", int, description="Page number to retrieve."),
    OpenApiParameter("page_size", int, description="Results per page (max 100)."),
]


@extend_schema_view(
    get=extend_schema(
        tags=["Audit"],
        summary="List audit events (Super Admin)",
        description="Unrestricted audit event query. Super Admin only.",
        parameters=[
            OpenApiParameter("school_id", str, description="Filter by school UUID."),
            *_QUERY_PARAMETERS,
        ],
    )
)
class SuperAdminAuditEventListView(generics.ListAPIView):
    serializer_class = AuditEventSerializer
    permission_classes = [IsSuperAdmin]
    queryset = AuditEvent.objects.all().order_by("-occurred_at")
    filter_backends = [DjangoFilterBackend]
    filterset_class = SuperAdminAuditEventFilter


@extend_schema_view(
    get=extend_schema(
        tags=["Audit"],
        summary="List audit events (School Admin)",
        description=(
            "Audit event query hard-scoped to the caller's own school. "
            "A school_id filter, even if supplied, has no effect - the "
            "queryset is always the authenticated admin's own school."
        ),
        parameters=_QUERY_PARAMETERS,
    )
)
class SchoolAdminAuditEventListView(generics.ListAPIView):
    serializer_class = AuditEventSerializer
    permission_classes = [IsSchoolAdmin]
    filter_backends = [DjangoFilterBackend]
    filterset_class = SchoolAdminAuditEventFilter

    def get_queryset(self):
        # IsSchoolAdmin has already rejected anonymous callers.
        school_id = cast("CustomUser", self.request.user).school_id
        if school_id is None:
            # AUDIT-NULL-SCHOOL: a user's school is nullable, and in Django
            # `filter(school_id=None)` means IS NULL: an admin with no school
            # would be shown every event that belongs to no school
            # (individual teachers, system, anonymous ones, failed sign-ins
            # with the address and the IP). They are shown nothing.
            return AuditEvent.objects.none()
        return AuditEvent.objects.filter(school_id=school_id).order_by("-occurred_at")
