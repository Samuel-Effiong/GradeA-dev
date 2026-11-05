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

from typing import TYPE_CHECKING, Any, cast

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics

from classrooms.models import School
from classrooms.permissions import IsSchoolAdmin, IsSuperAdmin

from .enums import ErrorClass
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
    OpenApiParameter(
        "error_class",
        str,
        enum=ErrorClass.values,
        description="Filter by error class (SYSTEM = server errors, also in Sentry).",
    ),
    OpenApiParameter("reason_code", str, description="Filter by reason code."),
    OpenApiParameter(
        "trace_id",
        str,
        description="Filter by trace UUID (dashed or undashed, any case).",
    ),
    OpenApiParameter("target_id", str, description="Filter by target UUID."),
    OpenApiParameter(
        "time_from", str, description="ISO-8601 lower bound on occurred_at."
    ),
    OpenApiParameter(
        "time_to", str, description="ISO-8601 upper bound on occurred_at."
    ),
    OpenApiParameter("page", int, description="Page number to retrieve."),
    OpenApiParameter("page_size", int, description="Results per page (max 100)."),
]


def _school_names_for_page(page, *, allowed_school_ids=None):
    """One query for School names referenced by a page of audit events.

    When *allowed_school_ids* is set, only those schools are resolved (school-
    admin scope: never leak another school's name).
    """
    school_ids = {
        e.target_id for e in page if e.target_type == "School" and e.target_id
    }
    if allowed_school_ids is not None:
        school_ids &= set(allowed_school_ids)
    if not school_ids:
        return {}
    return dict(School.objects.filter(pk__in=school_ids).values_list("pk", "name"))


class _SchoolNamesMixin:
    # Mypy cannot see that this is always mixed with ListAPIView.
    filter_queryset: Any
    get_queryset: Any
    paginate_queryset: Any
    get_serializer_context: Any
    get_serializer: Any
    get_paginated_response: Any

    _allowed_school_ids: frozenset | None = None

    def _school_names_ctx(self, events):
        return _school_names_for_page(
            events, allowed_school_ids=self._allowed_school_ids
        )

    def list(self, request, *args, **kwargs):
        from rest_framework.response import Response

        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            school_names = self._school_names_ctx(page)
            ctx = {**self.get_serializer_context(), "school_names": school_names}
            serializer = self.get_serializer(page, many=True, context=ctx)
            return self.get_paginated_response(serializer.data)
        # Unreachable: DEFAULT_PAGINATION_CLASS is set project-wide.
        school_names = self._school_names_ctx(queryset)
        ctx = {**self.get_serializer_context(), "school_names": school_names}
        serializer = self.get_serializer(queryset, many=True, context=ctx)
        return Response(serializer.data)


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
class SuperAdminAuditEventListView(_SchoolNamesMixin, generics.ListAPIView):
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
class SchoolAdminAuditEventListView(_SchoolNamesMixin, generics.ListAPIView):
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
        self._allowed_school_ids = frozenset({school_id})
        return AuditEvent.objects.filter(school_id=school_id).order_by("-occurred_at")
