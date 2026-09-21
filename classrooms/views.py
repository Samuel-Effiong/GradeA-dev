import logging
import uuid

from dateutil.relativedelta import relativedelta
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import (
    CharField,
    Count,
    Exists,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, Concat, TruncMonth
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import filters, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import (
    NotFound,
    ParseError,
    PermissionDenied,
    ValidationError,
)
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from assignments.models import Assignment
from assignments.serializers import TaskInfoSerializer
from AutoGrader.cache_generation import SCOPE_GLOBAL, SCOPE_USER, versioned_key
from AutoGrader.error_messages import describe_user_error
from AutoGrader.pagination import StandardPageNumberPagination
from billing.models import CreditUsageLog
from classrooms.permissions import CanManageSession
from students.models import BackgroundTaskType, StudentSubmission
from students.serializers import StudentListSerializer
from students.task_tracking import create_processing_task, launch_processing_task
from users.mixins import UserCacheMixin
from users.models import CustomUser, UserTypes
from users.permissions import HasCreditBalance
from users.serializers import CustomUserSerializer
from users.throttling import RegisterThrottle

from . import services
from .filters import MyStudentsFilter
from .models import (  # , Classroom, ClassroomSettings
    COURSE_ACCESS_ENROLLMENT_STATUSES,
    Course,
    CourseCategory,
    EnrollmentStatusType,
    School,
    Session,
    SessionOwnerType,
    StudentCourse,
    Topic,
)
from .permissions import IsSuperAdmin, IsTeacher, IsTeacherOrReadOnly
from .serializers import (  # ClassroomSerializer,; ClassroomSettingsSerializer,
    AddStudentToCourseSerializer,
    BulkAddStudentSerializer,
    CourseCategorySerializer,
    CourseSerializer,
    DirectAddStudentSerializer,
    ExpiredTokenSerializer,
    MonthlyTokenUsageSerializer,
    SchoolAdminSummarySerializer,
    SchoolDetailSerializer,
    SchoolSerializer,
    SchoolSummarySerializer,
    SchoolWithAdminResponseSerializer,
    SchoolWithAdminSerializer,
    SessionSerializer,
    StudentCourseDetailSerializer,
    StudentCourseSerializer,
    TeacherSummarySerializer,
    TopicSerializer,
)
from .tasks import student_summary_async

logger = logging.getLogger(__name__)


def _validate_uuid_query_param(value, param_name):
    """Validate a query-param string is a well-formed UUID.

    Filtering directly on a malformed UUID (e.g. `.filter(school_id=value)`)
    raises Django's `django.core.exceptions.ValidationError` deep inside the
    ORM, which DRF's exception handler doesn't translate to a clean 400 —
    it surfaces as an unhandled 500. Validate up front instead and raise
    DRF's own `ValidationError` so it's a normal 400 response.
    """
    try:
        uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError(
            {param_name: [f"{value!r} is not a valid UUID."]}
        ) from exc


@extend_schema_view(
    list=extend_schema(
        tags=["School"],
        summary="School summary",
        description="""
            Returns a paginated list of all schools with aggregate stats and
            admin info (if any).
        """,
        parameters=[
            OpenApiParameter(
                name="search",
                type=str,
                location="query",
                description="Search by school name or admin name/email.",
            ),
            OpenApiParameter(
                name="ordering",
                type=str,
                location="query",
                description="""
                    Order by field (prefix with "-" for descending).
                    Allowed: admin_name, admin_email.
                """,
            ),
            OpenApiParameter(
                name="include_archived",
                type=bool,
                location="query",
                description=(
                    "If true, also include archived (soft-deleted) schools. "
                    "Defaults to false — archived schools are hidden."
                ),
            ),
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number for pagination",
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of results per page (max 100)",
            ),
        ],
        responses={200: SchoolSummarySerializer(many=True)},
    ),
    create=extend_schema(
        tags=["School"],
        summary="Create a new School",
        description="Create a new School with the provided details.",
        request=SchoolSerializer,
        responses={
            201: OpenApiResponse(
                response=SchoolSerializer,
                description="School created successfully",
            ),
        },
    ),
    retrieve=extend_schema(
        tags=["School"],
        summary="School detail summary",
        description=(
            "Returns detailed stats for a specific school, including a "
            "per-session breakdown with a nested per-teacher breakdown. "
            "Includes SCHOOL-owned sessions (shared across every teacher "
            "in the school), not just INDIVIDUAL ones — each contributing "
            "teacher gets their own slice under session_breakdown[].teachers, "
            "and the session's own assignments/students/tokens are the sum "
            "of that list. "
            "tokens_used is every token consumed by the school's teachers "
            "and school admins; only the portion tied to a course/session "
            "is broken out per-session — the rest is reported separately "
            "as tokens_unattributed, so sum(session_breakdown[].tokens) + "
            "tokens_unattributed == tokens_used."
        ),
        responses={200: SchoolDetailSerializer},
    ),
    partial_update=extend_schema(
        tags=["School"],
        summary="Update an existing School",
        description="Update an existing School with the provided details.",
        request=SchoolSerializer,
        responses={
            200: OpenApiResponse(
                response=SchoolSerializer,
                description="School updated successfully",
            )
        },
    ),
    destroy=extend_schema(
        tags=["School"],
        summary="Archive a School",
        description=(
            "Archives a School by ID (soft-delete). The school is hidden from "
            "the default list/detail views but the row and all its history "
            "(sessions, license/billing records, analytics) are preserved. "
            'Restore it with PATCH {"is_active": true}.'
        ),
        responses={
            204: OpenApiResponse(description="School archived successfully"),
            403: OpenApiResponse(
                description="You do not have permission to archive this School"
            ),
            404: OpenApiResponse(description="School not found"),
        },
    ),
)
class SchoolViewSet(UserCacheMixin, viewsets.ModelViewSet):
    queryset = School.objects.all()
    serializer_class = SchoolSerializer
    permission_classes = (IsSuperAdmin,)
    pagination_class = StandardPageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]

    def get_queryset(self):
        if self.request.query_params.get("include_archived", "").lower() == "true":
            return School.objects.all()
        return School.objects.filter(is_active=True)

    def destroy(self, request, *args, **kwargs):
        school = self.get_object()
        school.is_active = False
        school.save(update_fields=["is_active"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        tags=["School"],
        summary="Create a School and its Admin in one request",
        description="Create both a School and a School Admin user atomically.",
        request=SchoolWithAdminSerializer,
        responses={201: SchoolWithAdminResponseSerializer},
    )
    @action(detail=False, methods=["post"])
    def create_with_admin(self, request, *args, **kwargs):
        serializer = SchoolWithAdminSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = serializer.save()

        response_data = {
            "school": result["school"],
            "admin": result["admin"],
            "message": "School and admin created successfully",
        }

        response_serializer = SchoolWithAdminResponseSerializer(response_data)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["School"],
        summary="School admin summary",
        description="Returns a paginated list of school administrators with aggregate stats for their schools.",
        parameters=[
            OpenApiParameter(
                name="search",
                type=str,
                location="query",
                description="Search by admin name, email, or school name (case‑insensitive partial match).",
            ),
            OpenApiParameter(
                name="ordering",
                type=str,
                location="query",
                description='Order by field (prefix with "-" for descending). Allowed: name, email, organization, '
                "teachers, students, tokens_used, sessions.",  # noqa: E501
            ),
        ],
        responses={200: SchoolAdminSummarySerializer(many=True)},
    )
    @action(
        detail=False,
        methods=["get"],
        url_path="admin-summary",
        # Explicit, to avoid a url-name collision: DRF's default url_name
        # comes from the method name ("admin-summary"), which combined with
        # this viewset's "school" basename produces "school-admin-summary"
        # - the exact same reverse() name that dashboard.SchoolAdminDashboardView's
        # `summary` action (basename "school-admin") also produces by
        # coincidence, even though the two endpoints share nothing (paths
        # are /schools/admin-summary vs /school-admin/dashboard/summary).
        # Whichever URL Django registered last silently wins on reverse().
        url_name="admins-summary",
    )
    def admin_summary(self, request, *args, **kwargs):
        """
        Returns a paginated list of school administrators with aggregated school stats.

        Query parameters:
        - search: (string) filter by admin name, email, or school name (case‑insensitive partial match)
        - ordering: (string) field to order by (prefix with '-' for descending). Defaults to 'name'.
                    Allowed fields: name, email, organization, teachers, students, tokens_used, sessions.
        """

        # Base queryset: all school admins, with school prefetched
        admins = CustomUser.objects.filter(user_type="SCHOOL_ADMIN").select_related(
            "school"
        )

        # --- Subqueries for school‑level aggregates ---
        # 1. Teachers count
        teachers_sub = Subquery(
            CustomUser.objects.filter(school=OuterRef("school"), user_type="TEACHER")
            .values("school")
            .annotate(count=Count("id"))
            .values("count"),
            output_field=IntegerField(),
        )

        # 2. Students count
        students_sub = Subquery(
            CustomUser.objects.filter(
                school=OuterRef("enrollments__course__teacher__school"),
                user_type="STUDENT",
            )
            .values("school")
            .annotate(count=Count("id"))
            .values("count"),
            output_field=IntegerField(),
        )

        # 3. Tokens used (sum of raw credits consumed by teachers and school
        # admins in the school — school admins can also directly trigger
        # credit-consuming AI features, billed to their own wallet).
        # Scoped by CreditUsageLog.school — a snapshot of the billed user's
        # school taken at consumption time — not wallet__user__school (the
        # user's CURRENT school), so a teacher who later transfers to a
        # different school doesn't retroactively drag their historical
        # usage along with them.
        tokens_sub = Subquery(
            CreditUsageLog.objects.filter(
                school=OuterRef("school"),
                wallet__user__user_type__in=["TEACHER", "SCHOOL_ADMIN"],
                is_refunded=False,
            )
            .values("school")
            .annotate(total=Sum("amount"))
            .values("total"),
            output_field=IntegerField(),
        )
        # 4. Sessions (count of ChatSession records created by teachers in the school)
        sessions_sub = Subquery(
            Session.objects.filter(teacher__school=OuterRef("school"))
            .values("teacher__school")
            .annotate(count=Count("id"))
            .values("count"),
            output_field=IntegerField(),
        )

        # Annotate each admin with the school aggregates
        admins = admins.annotate(
            teachers=Coalesce(teachers_sub, Value(0)),
            students=Coalesce(students_sub, Value(0)),
            tokens_used=Coalesce(tokens_sub, Value(0)),
            academic_sessions=Coalesce(sessions_sub, Value(0)),
        )

        # --- Search ---
        search = request.query_params.get("search", "").strip()
        if search:
            admins = admins.filter(
                Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(email__icontains=search)
                | Q(school__name__icontains=search)
            )

        # --- Ordering ---
        ordering = request.query_params.get("ordering", "name")
        # Map frontend field names to DB column names / annotated names
        order_map = {
            "name": "first_name",
            "email": "email",
            "school": "school__name",
            "teachers": "teachers",
            "students": "students",
            "tokens_used": "tokens_used",
            "sessions": "academic_sessions",
        }

        # Handle descending order (prefix '-')
        if ordering.startswith("-"):
            order_field = order_map.get(ordering[1:], "first_name")
            descending = True
        else:
            order_field = order_map.get(ordering, "first_name")
            descending = False

        # Apply ordering with tie-breaker for name
        if order_field == "first_name":
            admins = (
                admins.order_by("first_name", "last_name")
                if not descending
                else admins.order_by("-first_name", "-last_name")
            )
        else:
            admins = admins.order_by(
                order_field if not descending else f"-{order_field}"
            )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(admins, request, view=self)

        data = []
        for admin in page:
            data.append(
                {
                    "id": str(admin.id),
                    "name": admin.get_full_name(),
                    "email": admin.email,
                    "organization": admin.school.name if admin.school else "",
                    "teachers": admin.teachers,
                    "students": admin.students,
                    "tokens_used": admin.tokens_used,
                    "sessions": admin.academic_sessions,
                }
            )

        return paginator.get_paginated_response(data)

    def list(self, request):
        # Base queryset: active schools by default; ?include_archived=true
        # to also see archived ones (see get_queryset()).
        schools = self.get_queryset()

        # ---- Subqueries for aggregates ----
        # Teachers count (direct school FK, works because teachers have school set)
        teachers_sub = Subquery(
            CustomUser.objects.filter(school=OuterRef("id"), user_type="TEACHER")
            .values("school")
            .annotate(count=Count("id"))
            .values("count"),
            output_field=IntegerField(),
        )

        # Students count via StudentCourse (since student.school is null)
        students_sub = Subquery(
            StudentCourse.objects.filter(course__teacher__school=OuterRef("id"))
            .values("course__teacher__school")
            .annotate(total=Count("student", distinct=True))
            .values("total"),
            output_field=IntegerField(),
        )

        # Tokens used (sum of raw credits consumed by teachers and school
        # admins in the school). Scoped by CreditUsageLog.school (a
        # snapshot at consumption time), not wallet__user__school (the
        # user's current school) — see the tokens_sub comment in
        # admin_summary() above for why that distinction matters.
        tokens_sub = Subquery(
            CreditUsageLog.objects.filter(
                school=OuterRef("id"),
                wallet__user__user_type__in=["TEACHER", "SCHOOL_ADMIN"],
                is_refunded=False,
            )
            .values("school")
            .annotate(total=Sum("amount"))
            .values("total"),
            output_field=IntegerField(),
        )

        # Academic sessions (count of Session records created by teachers in the school)
        sessions_sub = Subquery(
            Session.objects.filter(teacher__school=OuterRef("id"))
            .values("teacher__school")
            .annotate(count=Count("id"))
            .values("count"),
            output_field=IntegerField(),
        )

        # Annotate schools
        schools = schools.annotate(
            teachers=Coalesce(teachers_sub, Value(0)),
            students=Coalesce(students_sub, Value(0)),
            tokens_used=Coalesce(tokens_sub, Value(0)),
            school_sessions=Coalesce(sessions_sub, Value(0)),
        )

        # ---- Prefetch the first admin for each school ----
        admin_queryset = CustomUser.objects.filter(user_type="SCHOOL_ADMIN").order_by(
            "date_joined"
        )
        schools = schools.prefetch_related(
            Prefetch("users", queryset=admin_queryset, to_attr="school_admins")
        )

        # ---- Search ----
        search = request.query_params.get("search", "").strip()
        if search:
            # Get school IDs where an admin matches the search
            admin_school_ids = (
                CustomUser.objects.filter(user_type="SCHOOL_ADMIN")
                .filter(
                    Q(first_name__icontains=search)
                    | Q(last_name__icontains=search)
                    | Q(email__icontains=search)
                )
                .exclude(school__isnull=True)
                .values("school")
            )  # exclude admins without school

            schools = schools.filter(
                Q(name__icontains=search) | Q(id__in=admin_school_ids)
            )

        # ---- Ordering ----
        ordering = request.query_params.get("ordering", "school_name")
        order_map = {
            "school_name": "name",
            "teachers": "teachers",
            "students": "students",
            "tokens_used": "tokens_used",
            "sessions": "sessions",
            "admin_name": "admin_name",
            "admin_email": "admin_email",
        }

        # Handle ordering by admin_name or admin_email using subqueries
        if ordering.lstrip("-") in ["admin_name", "admin_email"]:
            admin_name_ord = Subquery(
                CustomUser.objects.filter(
                    school=OuterRef("id"), user_type="SCHOOL_ADMIN"
                )
                .order_by("date_joined")
                .annotate(full_name=Concat("first_name", Value(" "), "last_name"))
                .values("full_name")[:1],
                output_field=CharField(),
            )
            admin_email_ord = Subquery(
                CustomUser.objects.filter(
                    school=OuterRef("id"), user_type="SCHOOL_ADMIN"
                )
                .order_by("date_joined")
                .values("email")[:1],
                output_field=CharField(),
            )
            schools = schools.annotate(
                admin_name_ord=admin_name_ord, admin_email_ord=admin_email_ord
            )

            if ordering.startswith("-"):
                if ordering[1:] == "admin_name":
                    schools = schools.order_by("-admin_name_ord")
                else:
                    schools = schools.order_by("-admin_email_ord")
            else:
                if ordering == "admin_name":
                    schools = schools.order_by("admin_name_ord")
                else:
                    schools = schools.order_by("admin_email_ord")
        else:
            order_field = order_map.get(ordering, "name")
            if ordering.startswith("-"):
                order_field = f"-{order_field}"
            schools = schools.order_by(order_field)

        # ---- Pagination ----
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(schools, request, view=self)

        # ---- Build response ----
        data = []
        for school in page:
            admin = school.school_admins[0] if school.school_admins else None
            data.append(
                {
                    "id": str(school.id),
                    "school_name": school.name,
                    "address": school.address,
                    "phone": school.phone,
                    "website": school.website,
                    "admin_id": str(admin.id) if admin else None,
                    "admin_name": admin.get_full_name() if admin else None,
                    "admin_email": admin.email if admin else None,
                    "teachers": school.teachers,
                    "students": school.students,
                    "tokens_used": school.tokens_used,
                    "sessions": school.school_sessions,
                    "is_active": school.is_active,
                }
            )

        return paginator.get_paginated_response(data)

    def retrieve(self, request, pk=None):
        school = self.get_object()

        # ---- Basic aggregates ----
        teachers_count = CustomUser.objects.filter(
            school=school, user_type="TEACHER"
        ).count()
        students_count = (
            StudentCourse.objects.filter(course__teacher__school=school)
            .values("student")
            .distinct()
            .count()
        )
        # Every token consumed by the school's teachers and school admins —
        # school admins can also directly trigger credit-consuming AI
        # features (weekly summaries, custom prompts), billed to their own
        # wallet, so they're included here too. Scoped by
        # CreditUsageLog.school — a snapshot of the billed user's school
        # taken at consumption time — not wallet__user__school (the user's
        # CURRENT school): a teacher who later transfers to a different
        # school must not retroactively drag their historical usage along
        # with them, nor vanish from the school they earned it under.
        school_wallet_filter = Q(
            school=school,
            wallet__user__user_type__in=["TEACHER", "SCHOOL_ADMIN"],
        )
        tokens_total = (
            CreditUsageLog.objects.filter(
                school_wallet_filter, is_refunded=False
            ).aggregate(total=Sum("amount"))["total"]
            or 0
        )
        # tokens_unattributed is computed further down, as tokens_total
        # minus whatever ends up actually displayed in session_breakdown —
        # not as a separate "course__isnull=False" query. That residual
        # approach guarantees sum(session tokens) + tokens_unattributed ==
        # tokens_used by construction, which a separate query can't: a
        # usage log can have a real course attached and still not be
        # displayable here, e.g. if the course's session no longer belongs
        # to this school by current roster (a teacher transferred schools
        # after the fact - assignments/students/sessions below are scoped
        # by the teacher's CURRENT school, while tokens are scoped by the
        # snapshot on CreditUsageLog.school, so the two can legitimately
        # diverge for a transferred teacher's old activity).
        # SCHOOL-owned sessions have no `teacher` (see Session.clean()) -
        # they're shared across every teacher in the school - so counting
        # via teacher__school alone misses them entirely.
        sessions_total = Session.objects.filter(
            Q(teacher__school=school) | Q(school=school)
        ).count()
        courses_total = Course.objects.filter(teacher__school=school).count()

        # ---- Admin info ----
        admin = (
            CustomUser.objects.filter(school=school, user_type="SCHOOL_ADMIN")
            .order_by("date_joined")
            .first()
        )

        # ---- Per‑session breakdown, with a nested per‑teacher breakdown ----
        # A Session's `teacher` FK is only ever set for owner_type=INDIVIDUAL
        # (see Session.clean()) - for owner_type=SCHOOL it's null, and the
        # session is shared: any teacher in the school can attach their own
        # Course to it. So attribution has to come from Course (which has
        # both `teacher` and `session`), not from Session.teacher.
        #
        # Each metric is computed as its own grouped-by-(session, teacher)
        # aggregate query and merged in Python, rather than combined into
        # one annotate() call - combining a Sum with multiple joined
        # one-to-many relations (assignments, enrollments, usage logs) in a
        # single query silently inflates every total via join fan-out; only
        # Count(distinct=True) has an escape hatch for that, Sum doesn't.
        assignments_by_key = {
            (row["course__session"], row["course__teacher"]): row["count"]
            for row in Assignment.objects.filter(course__teacher__school=school)
            .values("course__session", "course__teacher")
            .annotate(count=Count("id"))
        }
        students_by_key = {
            (row["course__session"], row["course__teacher"]): row["count"]
            for row in StudentCourse.objects.filter(course__teacher__school=school)
            .values("course__session", "course__teacher")
            .annotate(count=Count("student", distinct=True))
        }
        # course__isnull=False here is now load-bearing, not incidental:
        # school_wallet_filter no longer implies a non-null course via an
        # INNER JOIN the way `course__teacher__school=school` used to (that
        # clause is gone — it was current-state-biased in the same way
        # wallet__user__school was, via the course's teacher's CURRENT
        # school rather than a snapshot). Without this filter, usage with
        # no course context would group under a (None, None) key that's
        # simply never looked up below — harmless, but wasteful and
        # unclear; excluding it up front is both cheaper and more explicit.
        tokens_by_key = {
            (row["course__session"], row["course__teacher"]): row["total"]
            for row in CreditUsageLog.objects.filter(
                school_wallet_filter, course__isnull=False, is_refunded=False
            )
            .values("course__session", "course__teacher")
            .annotate(total=Sum("amount"))
        }

        sessions = Session.objects.filter(
            Q(owner_type=SessionOwnerType.INDIVIDUAL, teacher__school=school)
            | Q(owner_type=SessionOwnerType.SCHOOL, school=school)
        )

        # For SCHOOL sessions, the contributing teachers are whoever has a
        # Course there - grouped up front to avoid an N+1 query per session.
        school_session_teachers = {}
        for row in (
            Course.objects.filter(
                session__owner_type=SessionOwnerType.SCHOOL, session__school=school
            )
            .values_list("session_id", "teacher_id")
            .distinct()
        ):
            session_id, teacher_id = row
            school_session_teachers.setdefault(session_id, set()).add(teacher_id)

        all_teacher_ids = {
            tid for tids in school_session_teachers.values() for tid in tids
        }
        all_teacher_ids.update(
            s.teacher_id
            for s in sessions
            if s.owner_type == SessionOwnerType.INDIVIDUAL
        )
        teacher_names = {
            u.id: u.get_full_name()
            for u in CustomUser.objects.filter(id__in=all_teacher_ids)
        }

        session_breakdown = []
        for session in sessions:
            if session.owner_type == SessionOwnerType.INDIVIDUAL:
                teacher_ids = [session.teacher_id]
            else:
                teacher_ids = sorted(
                    school_session_teachers.get(session.id, set()),
                    key=lambda tid: teacher_names.get(tid, ""),
                )

            teachers = [
                {
                    "teacher_id": str(teacher_id),
                    "teacher_name": teacher_names.get(teacher_id, ""),
                    "assignments": assignments_by_key.get((session.id, teacher_id), 0),
                    "students": students_by_key.get((session.id, teacher_id), 0),
                    "tokens": tokens_by_key.get((session.id, teacher_id), 0),
                }
                for teacher_id in teacher_ids
            ]

            session_breakdown.append(
                {
                    "session_id": str(session.id),
                    "session_name": session.name,
                    "owner_type": session.owner_type,
                    "assignments": sum(t["assignments"] for t in teachers),
                    "students": sum(t["students"] for t in teachers),
                    "tokens": sum(t["tokens"] for t in teachers),
                    "teachers": teachers,
                }
            )

        # See the NOTE above tokens_total: computed as a residual so the
        # invariant sum(session_breakdown[].tokens) + tokens_unattributed
        # == tokens_used holds by construction, no matter what caused a
        # given usage log to not show up in session_breakdown above.
        tokens_unattributed = tokens_total - sum(
            row["tokens"] for row in session_breakdown
        )

        # ---- Assemble response ----
        data = {
            "id": str(school.id),
            "school_name": school.name,
            "address": school.address,
            "phone": school.phone,
            "website": school.website,
            "admin_id": str(admin.id) if admin else None,
            "admin_name": admin.get_full_name() if admin else None,
            "admin_email": admin.email if admin else None,
            "teachers": teachers_count,
            "students": students_count,
            "tokens_used": tokens_total,
            "tokens_unattributed": tokens_unattributed,
            "sessions": sessions_total,
            "courses": courses_total,
            "is_active": school.is_active,
            "session_breakdown": session_breakdown,
        }

        serializer = SchoolDetailSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data)

    @extend_schema(
        tags=["School"],
        summary="Teacher summary",
        description="""
            Returns a paginated list of teachers with aggregate stats
            (assignments, students, tokens used). Optionally filter by school
            and academic session.

            Without session_id, tokens_used is the teacher's full personal
            token total (every AI feature they've used, not just teaching
            activity). With session_id, tokens_used is scoped to that
            session's courses like assignments/students are, and the rest
            of the teacher's total (other sessions, or usage with no course
            context at all — custom AI chat, pre-Assignment extraction) is
            reported separately as tokens_used_outside_session so it isn't
            silently dropped.
        """,
        parameters=[
            OpenApiParameter(
                name="school_id",
                type=str,
                location="query",
                description="Filter by school UUID.",
            ),
            OpenApiParameter(
                name="session_id",
                type=str,
                location="query",
                description=(
                    "Filter by academic session UUID (limits assignments, "
                    "students, and tokens_used to that session; the "
                    "remainder of the teacher's token usage outside this "
                    "session is reported as tokens_used_outside_session)."
                ),
            ),
            OpenApiParameter(
                name="search",
                type=str,
                location="query",
                description="Search by teacher name or email.",
            ),
            OpenApiParameter(
                name="ordering",
                type=str,
                location="query",
                description=(
                    'Order by field (prefix with "-" for descending). '
                    "Allowed: name, email, organization, assignments, "
                    "students, tokens_used."
                ),
            ),
        ],
        responses={200: TeacherSummarySerializer(many=True)},
    )
    @action(
        detail=False,
        methods=["get"],
        url_path="teacher-summary",
    )
    def teacher_summary(self, request):
        # Base queryset: all teachers, prefetch school
        teachers = CustomUser.objects.filter(user_type="TEACHER").select_related(
            "school"
        )

        # Optional filters
        school_id = request.query_params.get("school_id")
        if school_id:
            _validate_uuid_query_param(school_id, "school_id")
            teachers = teachers.filter(school_id=school_id)

        session_id = request.query_params.get("session_id")
        session_filter = Q()
        if session_id:
            _validate_uuid_query_param(session_id, "session_id")
            session_filter = Q(course__session_id=session_id)

        # --- Subqueries for teacher-level aggregates ---
        # 1. Assignments count (filtered by session if provided)
        assignments_count_sub = Subquery(
            Assignment.objects.filter(course__teacher=OuterRef("id"))
            .filter(session_filter)
            .values("course__teacher")
            .annotate(count=Count("id"))
            .values("count"),
            output_field=IntegerField(),
        )
        # 2. Students count (distinct students enrolled in courses taught
        # by this teacher, filtered by session if provided)
        students_count_sub = Subquery(
            StudentCourse.objects.filter(course__teacher=OuterRef("id"))
            .filter(session_filter)
            .values("course__teacher")
            .annotate(distinct_students=Count("student", distinct=True))
            .values("distinct_students"),
            output_field=IntegerField(),
        )
        # 3. Tokens used (sum of CreditUsageLog amounts for this teacher,
        # filtered by session if provided — same course__session_id path as
        # assignments/students above, now possible since CreditUsageLog has
        # a `course` FK). Also compute the teacher's unfiltered lifetime
        # total so the portion excluded by the session filter (other
        # sessions, or usage with no course context at all) can be reported
        # rather than silently dropped.
        total_tokens_sub = Subquery(
            CreditUsageLog.objects.filter(
                wallet__user=OuterRef("id"), is_refunded=False
            )
            .filter(session_filter)
            .values("wallet__user")
            .annotate(total=Sum("amount"))
            .values("total"),
            output_field=IntegerField(),
        )
        full_tokens_sub = Subquery(
            CreditUsageLog.objects.filter(
                wallet__user=OuterRef("id"), is_refunded=False
            )
            .values("wallet__user")
            .annotate(total=Sum("amount"))
            .values("total"),
            output_field=IntegerField(),
        )

        # Annotate teachers
        teachers = teachers.annotate(
            assignment_count=Coalesce(assignments_count_sub, Value(0)),
            student_count=Coalesce(students_count_sub, Value(0)),
            total_tokens=Coalesce(total_tokens_sub, Value(0)),
            full_tokens=Coalesce(full_tokens_sub, Value(0)),
        )

        # --- Search ---
        search = request.query_params.get("search", "").strip()
        if search:
            teachers = teachers.filter(
                Q(first_name__icontains=search)
                | Q(last_name__icontains=search)
                | Q(email__icontains=search)
            )

        # --- Ordering ---
        ordering = request.query_params.get("ordering", "name")
        order_map = {
            "name": "first_name",  # order by first_name, then last_name
            "email": "email",
            "school": "school__name",
            "assignments": "assignment_count",
            "students": "student_count",
            "tokens_used": "total_tokens",
        }
        if ordering.startswith("-"):
            order_field = order_map.get(ordering[1:], "first_name")
            descending = True
        else:
            order_field = order_map.get(ordering, "first_name")
            descending = False

        if order_field == "first_name":
            teachers = (
                teachers.order_by("first_name", "last_name")
                if not descending
                else teachers.order_by("-first_name", "-last_name")
            )
        else:
            teachers = teachers.order_by(
                order_field if not descending else f"-{order_field}"
            )

        # --- Pagination ---
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(teachers, request, view=self)

        # Build response
        data = []
        for teacher in page:
            data.append(
                {
                    "id": str(teacher.id),
                    "name": teacher.get_full_name(),
                    "email": teacher.email,
                    "school": teacher.school.name if teacher.school else "",
                    "assignments": teacher.assignment_count,
                    "students": teacher.student_count,
                    "tokens_used": teacher.total_tokens,
                    "tokens_used_outside_session": teacher.full_tokens
                    - teacher.total_tokens,
                }
            )

        return paginator.get_paginated_response(data)

    @extend_schema(
        tags=["School"],
        summary="Monthly token usage",
        description=(
            "Returns monthly token consumption for a school over the past "
            "12 months (or custom range). Scoped by CreditUsageLog.school "
            "— a snapshot of each billed user's school taken at "
            "consumption time — so a teacher transferring schools doesn't "
            "retroactively move their historical usage, and a school's "
            "history stays visible even if every teacher who generated it "
            "has since left."
        ),
        parameters=[
            OpenApiParameter(
                name="school_id",
                type=str,
                location="query",
                description="School UUID. If not provided, uses the authenticated user's school (if school admin).",
            ),
            OpenApiParameter(
                name="months",
                type=int,
                location="query",
                description="Number of past months to include (default 12).",
            ),
        ],
        responses={200: MonthlyTokenUsageSerializer(many=True)},
    )
    @action(
        detail=False,
        methods=["get"],
        url_path="monthly-token-usage",
        # Deliberate exception to the viewset's superadmin-only default: this
        # is the only endpoint that lets a SCHOOL_ADMIN see their own
        # school's usage, so it stays open to any authenticated user and
        # enforces the school-scoping itself below.
        permission_classes=[IsAuthenticated],
    )
    def monthly_token_usage(self, request):
        # Determine school
        school_id = request.query_params.get("school_id")
        if school_id:
            _validate_uuid_query_param(school_id, "school_id")
            # Superadmin can specify any school. Both flags, as IsSuperAdmin
            # requires (H-19): `or` let a createsuperuser account (user_type
            # TEACHER) read any school's usage.
            if not (
                request.user.is_superuser
                and request.user.user_type == UserTypes.SUPER_ADMIN
            ):
                return Response(
                    {"detail": "You do not have permission to view this school."},
                    status=403,
                )
        else:
            # For school admins, use their own school
            if request.user.user_type == "SCHOOL_ADMIN":
                school_id = request.user.school_id
            else:
                return Response(
                    {"detail": "school_id is required for superadmins."}, status=400
                )

        if not school_id:
            return Response({"detail": "School not found for this user."}, status=404)

        if not School.objects.filter(id=school_id).exists():
            return Response({"detail": "School not found."}, status=404)

        # Number of months to look back
        months = int(request.query_params.get("months", 12))
        if months < 1 or months > 36:
            months = 12

        # Date range: `months` months, ending with (and including) the
        # current month. Regression note: this used to be
        # `end_date - relativedelta(months=months)`, which — combined with
        # the loop below starting at that month and only ever stepping
        # forward `months - 1` more times — silently excluded the current
        # month from every response. A "past 12 months" chart that never
        # shows the current month is missing exactly the data point most
        # likely to be looked at.
        end_date = timezone.now()
        start_date = end_date - relativedelta(months=months - 1)

        # Aggregate monthly usage. Scoped directly by CreditUsageLog.school
        # (the snapshot) rather than first resolving "which teachers/admins
        # currently belong to this school" and filtering by wallet owner —
        # that current-roster approach both misattributes historical usage
        # after a transfer AND would 404 a school whose usage-generating
        # teachers have since all left, even though its history is still
        # perfectly real and worth showing.
        monthly_usage = (
            CreditUsageLog.objects.filter(
                school_id=school_id,
                wallet__user__user_type__in=["TEACHER", "SCHOOL_ADMIN"],
                is_refunded=False,
            )
            .annotate(month=TruncMonth("created_at"))
            .values("month")
            .annotate(total=Sum("amount"))
            .order_by("month")
        )

        # Build response, filling missing months with 0.
        # NOTE: TruncMonth("created_at") always truncates to midnight on
        # the 1st (confirmed directly against the DB: hour=minute=second=0)
        # — `current` must match that exactly, or every lookup below misses
        # and this endpoint silently returns all-zero tokens for every
        # month regardless of actual usage. `start_date.replace(day=1)`
        # alone only replaces the day, leaving today's current hour/minute/
        # second/microsecond in place, so it practically never equalled a
        # TruncMonth key. Explicitly zero the whole time-of-day too.
        result = []
        current = start_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        usage_dict = {item["month"]: item["total"] for item in monthly_usage}

        for _ in range(months):
            month_str = current.strftime("%b %Y")  # e.g. "Apr 2025"
            tokens = usage_dict.get(current, 0)
            result.append(
                {
                    "month": month_str,
                    "tokens": tokens,
                }
            )
            current += relativedelta(months=1)

        serializer = MonthlyTokenUsageSerializer(result, many=True)
        return Response(serializer.data)


@extend_schema_view(
    list=extend_schema(
        tags=["02 Course"],
        summary="List all Course",
        description="Retrieve a paginated list of all Course in the system.",
        parameters=[
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number for pagination",
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of results per page",
            ),
        ],
        responses={
            200: CourseSerializer(many=True),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    create=extend_schema(
        tags=["02 Course"],
        summary="Create a new Course",
        description="Create a new Course with the provided details.",
        request=CourseSerializer,
        responses={
            201: OpenApiResponse(
                response=CourseSerializer,
                description="Course created successfully",
            ),
            400: OpenApiResponse(
                description="Invalid input. Missing required fields or invalid data format"
            ),
        },
    ),
    retrieve=extend_schema(
        tags=["02 Course"],
        summary="Retrieve a Course",
        description="Retrieve detailed information about a specific Course by its ID.",
        responses={
            200: CourseSerializer,
            404: OpenApiResponse(description="Course not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    partial_update=extend_schema(
        tags=["02 Course"],
        summary="Partially update a Course",
        description="Update one or more fields of an existing section.",
        request=CourseSerializer(partial=True),
        responses={
            200: CourseSerializer,
            400: OpenApiResponse(description="Invalid input"),
            404: OpenApiResponse(description="Course not found"),
        },
    ),
    destroy=extend_schema(
        tags=["02 Course"],
        summary="Delete a Course",
        description="Delete a Course by ID. This action cannot be undone.",
        responses={
            204: OpenApiResponse(description="Course deleted successfully"),
            404: OpenApiResponse(description="Course not found"),
        },
    ),
)
class CourseViewSet(UserCacheMixin, viewsets.ModelViewSet):
    """
    API endpoint for managing sections.

    Provides CRUD operations for sections including:
    - List all sections
    - Create new sections
    - Retrieve specific sections
    - Update sections
    - Delete sections

    Sections represent different groups or periods within a classroom.
    """

    queryset = Course.objects.all()
    serializer_class = CourseSerializer
    permission_classes = (IsAuthenticated, IsTeacherOrReadOnly)
    pagination_class = StandardPageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]

    filterset_fields = [
        "session",
        "is_active",
    ]
    ordering_fields = ["name", "created_at"]
    search_fields = [
        "name",
        "description",
    ]
    ordering = (
        "name",
        "created_at",
    )

    def get_queryset(self):
        user = self.request.user
        course = (
            Course.objects.select_related("session", "teacher")
            .prefetch_related(
                "topics",
                # CourseSerializer nests AssignmentListSerializer and counts
                # assignments; without this each course re-queried them.
                "assignments",
                # AssignmentListSerializer.get_submission_count calls
                # .count() on the reverse relation, which reads a prefetch
                # cache when one exists and issues a COUNT per assignment
                # when it doesn't - 80 queries for a 3-course page. Only
                # the two columns needed to count are loaded, so this
                # doesn't drag whole submission rows into memory.
                Prefetch(
                    "assignments__submissions",
                    queryset=StudentSubmission.objects.only("id", "assignment_id"),
                ),
                Prefetch(
                    "enrollments",
                    queryset=StudentCourse.objects.exclude(
                        enrollment_status=EnrollmentStatusType.WITHDRAWN
                    ).select_related("student"),
                    to_attr="active_enrollments",
                ),
            )
            .annotate(
                student_count=Count(
                    "enrollments",
                    filter=~Q(
                        enrollments__enrollment_status=EnrollmentStatusType.WITHDRAWN
                    ),
                    distinct=True,
                )
            )
        )

        if user.user_type == UserTypes.TEACHER:
            return course.filter(teacher=user)
        elif user.user_type == UserTypes.STUDENT:
            # One rule, shared with assignments and topics - see
            # COURSE_ACCESS_ENROLLMENT_STATUSES in classrooms.models.
            return course.filter(
                enrollments__student=user,
                enrollments__enrollment_status__in=(COURSE_ACCESS_ENROLLMENT_STATUSES),
            )
        else:
            return Course.objects.none()

    @extend_schema(
        tags=["02 Course"],
        summary="Add student to a particular course",
        description="Add student to a particular course.",
        request=AddStudentToCourseSerializer,
    )
    @action(detail=True, methods=["post"], url_path="students", url_name="students")
    def students(self, request, *args, **kwargs):
        """Onboard a student to a course by email address."""
        serializer = AddStudentToCourseSerializer(data=request.data)
        if not serializer.is_valid():
            raise ValidationError(serializer.errors)

        # Scoped through get_queryset() (teacher=user for a teacher, .none()
        # for anyone else) so a teacher can't enroll a student into another
        # teacher's course by guessing a course id - this custom @action
        # never calls self.get_object(), so a bare Course.objects.get() here
        # would bypass the scoping entirely.
        course = get_object_or_404(self.get_queryset(), pk=self.kwargs["pk"])

        try:
            student, is_new_student = services.enroll_student_by_email(
                course=course, email=serializer.validated_data["email"]
            )
        except services.EnrollmentError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except DjangoValidationError as exc:
            detail = exc.message_dict if hasattr(exc, "message_dict") else exc.messages
            raise ValidationError(detail) from exc
        except Exception as exc:
            logger.error("Failed to add student to course", exc_info=exc)
            return Response(
                {
                    "detail": describe_user_error(
                        exc,
                        fallback_message=(
                            "We couldn't add this student to the course. "
                            "Please try again."
                        ),
                    )
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "detail": "Student added to course successfully.",
                "is_new_student": is_new_student,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["02 Course"],
        summary="Add student directly to a course without email verification",
        description="Allows teachers to manually create and enroll students (like toddlers) instantly.",
        request=DirectAddStudentSerializer,
    )
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsTeacher],
        url_path="direct-add-student",
        url_name="direct-add-student",
    )
    def direct_add_student(self, request, *args, **kwargs):
        course = self.get_object()
        serializer = DirectAddStudentSerializer(
            data=request.data, context={"course": course}
        )

        if not serializer.is_valid():
            raise ValidationError(serializer.errors)

        try:
            student = serializer.save()
            serializer = CustomUserSerializer(student)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except DjangoValidationError as e:
            detail = e.message_dict if hasattr(e, "message_dict") else e.messages
            raise ValidationError(detail) from e
        except Exception as e:
            logger.error("Failed to direct-add student to course", exc_info=e)
            return Response(
                {
                    "message": "Unable to add student to course",
                    "detail": describe_user_error(
                        e,
                        fallback_message=(
                            "We couldn't add this student to the course. "
                            "Please try again."
                        ),
                    ),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        tags=["02 Course"],
        summary="Bulk add students to a course",
        description="Allows teachers to upload a CSV or paste roster data from Excel (TSV).",
        request=BulkAddStudentSerializer,
    )
    @action(
        detail=True,
        methods=["post"],
        permission_classes=[IsTeacher],
        url_path="bulk-add-students",
        url_name="bulk-add-students",
    )
    def bulk_add_students(self, request, pk=None):
        course = self.get_object()
        serializer = BulkAddStudentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            rows, total_processed = services.parse_roster(
                input_file=serializer.validated_data.get("file"),
                raw_data=serializer.validated_data.get("raw_data"),
            )
        except services.RosterImportError as exc:
            if exc.field == "detail" and "No valid student data" in exc.message:
                raise ParseError(exc.message) from exc
            raise ValidationError({exc.field: [exc.message]}) from exc

        return Response(
            services.import_roster(
                course=course, rows=rows, total_processed=total_processed
            ),
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["02 Course"],
        summary="Remove student from course",
        description="Remove a student from a course.",
        parameters=[
            OpenApiParameter(
                name="student_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description="UUID of the student to be removed from the course",
                required=True,
            ),
        ],
        request=None,
        responses={
            200: OpenApiResponse(
                description="Student removed from course successfully"
            ),
            400: OpenApiResponse(
                description="Invalid student ID or student not enrolled in course"
            ),
            404: OpenApiResponse(description="Student not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    @action(
        detail=True,
        methods=["delete"],
        url_path=r"student/(?P<student_id>[-\w]+)",
        url_name="remove-student",
    )
    def remove_student(self, request, pk=None, student_id=None, *args, **kwargs):
        """Remove a student from a course."""
        # Kept outside the try/except below: get_object() already scopes to
        # the requesting teacher's own courses via get_queryset(), so a
        # different teacher's course id raises Http404 here - inside the
        # try, that got caught by the blanket `except Exception` and
        # downgraded to a 500 instead of DRF's normal 404.
        course = self.get_object()

        if request.user != course.teacher:
            raise PermissionDenied(
                "You do not have permission to remove students from this "
                "course. Only course teacher can"
            )

        try:
            services.remove_student_from_course(course=course, student_id=student_id)
        except services.EnrollmentError as exc:
            # A 400, not a 404: the course exists and is theirs, the
            # student simply isn't on its roster.
            raise ParseError(str(exc)) from exc
        except Exception as exc:
            logger.error("Failed to remove student from course", exc_info=exc)
            return Response(
                {
                    "detail": describe_user_error(
                        exc,
                        fallback_message=(
                            "We couldn't remove this student from the course."
                        ),
                    )
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {"detail": "Student removed from course successfully."},
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["02 Course"],
        summary="Renew expired activation token for student",
        description="Renew the activation token for a student whose token has expired",
        request=ExpiredTokenSerializer,
        responses={
            200: OpenApiResponse(description="Activation token renewed successfully"),
            400: OpenApiResponse(description="Invalid or expired token"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    @action(
        detail=False,
        methods=["POST"],
        permission_classes=[AllowAny],
        # Unauthenticated, guesses at an activation token, and sends an
        # email on success - so it is both a token-guessing oracle and a
        # free outbound-mail trigger. Shares the registration bucket.
        throttle_classes=[RegisterThrottle],
        url_path=r"renew-student-token",
        url_name="renew-activation-token",
    )
    def handle_expired_token(self, request, token=None, *args, **kwargs):
        """Reissue an expired student activation link."""
        serializer = ExpiredTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            services.renew_student_activation(token=serializer.validated_data["token"])
        except services.EnrollmentError as exc:
            raise ParseError(str(exc)) from exc
        except Exception as exc:
            logger.error("Failed to renew activation token", exc_info=exc)
            return Response(
                {
                    "detail": describe_user_error(
                        exc,
                        fallback_message=(
                            "We couldn't renew the activation link. Please "
                            "try again."
                        ),
                    )
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "detail": (
                    "A new activation link has been sent to the student's "
                    "email. Expires in 24 hours"
                )
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["02 Course"],
        summary="List courses a student is enrolled in",
        responses=CourseSerializer(many=True),
    )
    @action(detail=False, methods=["get"], url_name="my-courses", url_path="my-courses")
    def my_courses(self, request, *args, **kwargs):
        """List courses the authenticated student is enrolled in, exclude withdrawn"""
        user = request.user

        if user.user_type != UserTypes.STUDENT:
            raise ParseError("Only students can access their enrolled courses.")

        # `usr` AND `global`. The student's own enrollments bump `usr`, but
        # this payload also serializes course names, topics and assignments
        # owned by the TEACHER - and a teacher's course edit bumps their own
        # generation, not their students'. `global` closes that gap.
        #
        # The precise alternative was to bump every enrolled student on a
        # course/topic/assignment change (~30 INCRs per edit, pipelined).
        # Rejected on measurement: `global` moves ~6.5 times/day in
        # production while this key's 5-minute TTL expires 288 times/day, so
        # the extra invalidation is ~2% of misses - not worth a per-edit
        # fan-out query.
        cache_key = versioned_key(
            f"courses:user_id__{request.user.id}",
            [(SCOPE_USER, request.user.id), (SCOPE_GLOBAL, None)],
        )
        cached_data = cache.get(cache_key)

        if cached_data is not None:
            return Response(cached_data)

        # get_queryset() already scopes a student to their own non-withdrawn
        # enrollments, and carries the prefetches/annotations CourseSerializer
        # needs. Rebuilding the list by hand from bare Course rows meant
        # student_count, topics, assignments and the roster were each fetched
        # per course.
        courses = self.get_queryset()

        serializer = self.get_serializer(courses, many=True)
        data = serializer.data

        cache.set(cache_key, data, 60 * 5)
        return Response(data)

    @extend_schema(
        tags=["Courses"],
        summary="Generate an AI summary for a student in this course",
        description="""Generates a short, personalised AI narrative about a specific student's
        performance across all assignments in this course.

        The summary is cached on the enrollment record and only regenerated when explicitly
        requested by passing `?refresh=true`. Credits are charged to the requesting teacher.

        **Caching behaviour:**
        - First call: generates and stores the summary, charges credits.
        - Subsequent calls: returns the cached summary instantly at no credit cost.
        - `?refresh=true`: forces regeneration and charges credits again.
        """,
        parameters=[
            OpenApiParameter(
                name="student_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description="UUID of the student to summarise",
                required=True,
            ),
            OpenApiParameter(
                name="refresh",
                type=OpenApiTypes.BOOL,
                location=OpenApiParameter.QUERY,
                description="Set to true to force regeneration of the summary",
                required=False,
            ),
        ],
        responses={
            200: OpenApiResponse(description="Student AI summary"),
            400: OpenApiResponse(description="student_id is required"),
            403: OpenApiResponse(description="You do not have permission to do this"),
            404: OpenApiResponse(description="Student not enrolled in this course"),
        },
    )
    @action(
        detail=True,
        methods=["get"],
        url_path="student-summary",
        url_name="student-summary",
        permission_classes=[IsAuthenticated, IsTeacher, HasCreditBalance],
    )
    def student_summary(self, request, pk=None):
        """Return a cached or freshly generated AI summary for a student in this course."""
        course = self.get_object()

        student_id = request.query_params.get("student_id")
        if not student_id:
            raise ParseError("student_id query parameter is required.")

        enrollment = (
            StudentCourse.objects.filter(course=course, student__id=student_id)
            .select_related("student")
            .first()
        )

        if not enrollment:
            raise NotFound("This student is not enrolled in the course.")

        force_refresh = request.query_params.get("refresh", "false").lower() == "true"

        # Return cached summary unless refresh is forced
        if enrollment.ai_summary and not force_refresh:
            return Response(
                {
                    "student_id": enrollment.student.id,
                    "student_name": enrollment.student.get_full_name(),
                    "course": course.name,
                    "summary": enrollment.ai_summary,
                    "generated_at": enrollment.ai_summary_generated_at,
                    "cached": True,
                },
                status=status.HTTP_200_OK,
            )

        # Tracked like every other user-facing async endpoint, rather than a
        # bare .delay(). Three things follow from the tracking row that did
        # not hold before: the poller in users.views.TaskViewSet can verify
        # this task belongs to the caller (a bare Celery id has no owner, so
        # status for it had to be served unauthenticated-by-ownership), the
        # teacher can cancel a summary that is still running, and a failure
        # is recorded with a readable message instead of only existing as a
        # Celery traceback.
        processing_task = create_processing_task(
            requested_by=request.user,
            task_type=BackgroundTaskType.STUDENT_SUMMARY,
            file_name=f"Summary for {enrollment.student.get_full_name()}",
            # BackgroundProcessingTask has no student/course FK, so these ids
            # live here - students.task_context.get_task_context reads them
            # back out to build this task's context.
            meta={
                "step": "Queued for student summary",
                "student_id": str(enrollment.student.id),
                "course_id": str(course.id),
            },
        )
        task = launch_processing_task(
            student_summary_async,
            processing_task,
            student_id,
            str(request.user.id),
            str(course.id),
        )

        data = {
            "file_name": "Student summary generation started",
            "task_id": task.id,
        }

        serializer = TaskInfoSerializer(data)
        return Response(serializer.data)

    @extend_schema(
        tags=["02 Course"],
        summary="Create topics for a course",
        description="Create multiple topics for a specific course. Accepts a list of topic names or topic objects.",
        request=TopicSerializer(many=True),
        examples=[
            OpenApiExample(
                "List of Objects",
                summary="Create topics using objects",
                description="Send a list of topic objects with the 'name' field.",
                value={"name": "Introduction to Algebra"},
                request_only=True,
            ),
            OpenApiExample(
                "List of Strings",
                summary="Create topics using strings",
                description="Send a simple list of topic names as strings.",
                value="Introduction to Algebra",
                request_only=True,
            ),
        ],
        responses={
            201: TopicSerializer(many=True),
            400: OpenApiResponse(description="Invalid input"),
        },
    )
    @action(detail=True, methods=["post"], url_path="topics", url_name="create-topics")
    def create_topics(self, request, pk=None):
        """Create topics for a specific course."""
        course = self.get_object()

        # Check if request data is a list
        if not isinstance(request.data, list):
            return Response(
                {"detail": "Expected a list of topics."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        topics_data = []
        for item in request.data:
            if isinstance(item, str):
                topics_data.append({"name": item, "course": course.id})
            elif isinstance(item, dict):
                item["course"] = course.id
                topics_data.append(item)

        serializer = TopicSerializer(
            data=topics_data, many=True, context=self.get_serializer_context()
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(serializer.data, status=status.HTTP_201_CREATED)


@extend_schema_view(
    list=extend_schema(
        tags=["01 Session"],
        summary="List all Session",
        description="Retrieve a paginated list of all Session in the system.",
        parameters=[
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number for pagination",
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of results per page",
            ),
        ],
        responses={
            200: SessionSerializer(many=True),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    create=extend_schema(
        tags=["01 Session"],
        summary="Create a new Session",
        description="Create a new Session with the provided details.",
        request=SessionSerializer,
        responses={
            201: OpenApiResponse(
                response=SessionSerializer,
                description="Session created successfully",
            ),
            400: OpenApiResponse(
                description="Invalid input. Missing required fields or invalid data format"
            ),
        },
    ),
    retrieve=extend_schema(
        tags=["01 Session"],
        summary="Retrieve an Session",
        description="Retrieve detailed information about a specific Session by its ID.",
        responses={
            200: SessionSerializer,
            404: OpenApiResponse(description="Session not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    partial_update=extend_schema(
        tags=["01 Session"],
        summary="Partially update an Session",
        description="Update one or more fields of an existing Session.",
        request=SessionSerializer(partial=True),
        responses={
            200: SessionSerializer,
            400: OpenApiResponse(description="Invalid input"),
            404: OpenApiResponse(description="Session not found"),
        },
    ),
    destroy=extend_schema(
        tags=["01 Session"],
        summary="Delete an Session",
        description="Delete an Session by ID. This action cannot be undone.",
        responses={
            204: OpenApiResponse(description="Session deleted successfully"),
            404: OpenApiResponse(description="Session not found"),
        },
    ),
)
class SessionViewSet(UserCacheMixin, viewsets.ModelViewSet):
    """
    API endpoint for managing academic terms.

    Provides CRUD operations for academic terms including:
    - List all academic terms
    - Create new academic terms
    - Retrieve specific academic terms
    - Update academic terms
    - Delete academic terms

    Academic terms represent a period of time such as a semester, year, or quarter.
    """

    queryset = Session.objects.all()
    serializer_class = SessionSerializer
    permission_classes = (IsAuthenticated, CanManageSession)
    pagination_class = StandardPageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]

    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    ordering_fields = [
        "name",
        "created_at",
    ]
    search_fields = [
        "name",
    ]
    ordering = (
        "name",
        "created_at",
    )

    def get_queryset(self):
        user = self.request.user

        if user.is_superuser and user.user_type == UserTypes.SUPER_ADMIN:
            return Session.objects.all()

        if user.user_type == UserTypes.SCHOOL_ADMIN:
            admin_schools = School.objects.filter(users=user)
            return Session.objects.filter(
                owner_type=SessionOwnerType.SCHOOL, school__in=admin_schools
            )

        if user.user_type == UserTypes.TEACHER:
            if user.is_under_license():
                # School-managed teacher: read-only view of their school's sessions.
                return Session.objects.filter(
                    owner_type=SessionOwnerType.SCHOOL, school=user.school
                )
            # Individual-track teacher: unchanged legacy behavior. Keyed off
            # is_under_license() rather than school_id so a teacher who was
            # removed from a license (or whose license lapsed) isn't stuck
            # unable to see their own individual sessions.
            return Session.objects.filter(
                owner_type=SessionOwnerType.INDIVIDUAL, teacher=user
            )

        if user.user_type == UserTypes.STUDENT:
            # COMPLETED included, unlike before: a student who finished the
            # course could still open its assignments (assignments/views.py)
            # but the session containing them vanished from their sidebar.
            return Session.objects.filter(
                courses__enrollments__student=user,
                courses__enrollments__enrollment_status__in=(
                    COURSE_ACCESS_ENROLLMENT_STATUSES
                ),
            ).distinct()

        return Session.objects.none()

    def perform_create(self, serializer):
        user = self.request.user

        if user.user_type == UserTypes.SCHOOL_ADMIN:
            admin_schools = School.objects.filter(users=user)
            school = admin_schools.first()
            if not school:
                raise ParseError("You are not assigned as an admin for any school.")
            serializer.save(
                owner_type=SessionOwnerType.SCHOOL,
                school=school,
                teacher=None,
                created_by=user,
            )
            return

        if user.user_type == UserTypes.TEACHER:
            if user.is_under_license():
                raise PermissionDenied(
                    "Sessions for your school are managed by your school "
                    "admin. Contact them to add or update academic sessions."
                )
            serializer.save(
                owner_type=SessionOwnerType.INDIVIDUAL,
                teacher=user,
                school=None,
                created_by=user,
            )
            return

        if user.is_superuser and user.user_type == UserTypes.SUPER_ADMIN:
            school_id = self.request.data.get("school")
            if not school_id:
                raise ParseError(
                    "Superadmin session creation requires an explicit "
                    "'school' field (to create a SCHOOL session) — there's "
                    "no superadmin path for creating an INDIVIDUAL session "
                    "on a teacher's behalf."
                )
            school = get_object_or_404(School, pk=school_id)
            serializer.save(
                owner_type=SessionOwnerType.SCHOOL,
                school=school,
                teacher=None,
                created_by=user,
            )
            return

        raise PermissionDenied("You do not have permission to create sessions.")


@extend_schema_view(
    list=extend_schema(
        tags=["Student Course"],
        summary="List all student Course",
        description="Retrieve a paginated list of all student Course in the system.",
        parameters=[
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number for pagination",
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of results per page",
            ),
        ],
        responses={
            200: StudentCourseSerializer(many=True),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    create=extend_schema(
        tags=["Student Course"],
        summary="Create a new student Course",
        description="Create a new student Course with the provided details.",
        request=StudentCourseSerializer,
        responses={
            201: OpenApiResponse(
                response=StudentCourseSerializer,
                description="Student Course created successfully",
            ),
            400: OpenApiResponse(
                description="Invalid input. Missing required fields or invalid data format"
            ),
        },
    ),
    retrieve=extend_schema(
        tags=["Student Course"],
        summary="Retrieve a student Course",
        description="Retrieve detailed information about a specific student Course by its ID.",
        responses={
            200: StudentCourseSerializer,
            404: OpenApiResponse(description="Student Course not found "),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    partial_update=extend_schema(
        tags=["Student Course"],
        summary="Partially update a student Course",
        description="Update one or more fields of an existing student Course.",
        request=StudentCourseSerializer(partial=True),
        responses={
            200: StudentCourseSerializer,
            400: OpenApiResponse(description="Invalid input"),
            404: OpenApiResponse(description="Student Course not found"),
        },
    ),
    destroy=extend_schema(
        tags=["Student Course"],
        summary="Delete a student Course",
        description="Delete a student Course by ID. This action cannot be undone.",
        responses={
            204: OpenApiResponse(description="Student Course deleted successfully"),
            404: OpenApiResponse(description="Student Course not found"),
        },
    ),
)
class StudentCourseViewSet(UserCacheMixin, viewsets.ModelViewSet):
    """
    API endpoint for managing student sections.

    Provides CRUD operations for student sections including:
    - List all student sections
    - Create new student sections
    - Retrieve specific student sections
    - Update student sections
    - Delete student sections

    Student sections represent a student's enrollment in a specific section.
    """

    queryset = StudentCourse.objects.all()
    serializer_class = StudentCourseSerializer
    permission_classes = (IsAuthenticated, IsTeacherOrReadOnly)
    pagination_class = StandardPageNumberPagination
    http_method_names = ["get", "head", "delete", "patch", "options"]

    def get_queryset(self):
        user = self.request.user

        if self.action == "my_students":
            if user.user_type == UserTypes.TEACHER:
                active_enrollment = StudentCourse.objects.filter(
                    student=OuterRef("pk"), course__teacher=user
                ).exclude(enrollment_status=EnrollmentStatusType.WITHDRAWN)
                # StudentListSerializer walks enrollments -> course ->
                # teacher and assignments, and submissions -> assignment,
                # for every row. Unprefetched that was ~140 queries PER
                # STUDENT (425 for a 3-row page, measured).
                #
                # Both prefetches are scoped to THIS teacher's courses. A
                # student is routinely enrolled with several unrelated
                # teachers, and the serializer reports whatever the cache
                # holds: unscoped, `enrolled_courses` listed other teachers'
                # course names, and `?enrollments__course=<their course>`
                # made that foreign course the row's subject - its
                # description, its teacher's name and the student's grade.
                return CustomUser.objects.filter(
                    Exists(active_enrollment)
                ).prefetch_related(
                    Prefetch(
                        "enrollments",
                        queryset=StudentCourse.objects.filter(course__teacher=user)
                        .select_related("course", "course__teacher")
                        .prefetch_related("course__assignments"),
                    ),
                    Prefetch(
                        "submissions",
                        queryset=StudentSubmission.objects.filter(
                            assignment__course__teacher=user
                        ).select_related("assignment"),
                    ),
                )
            return CustomUser.objects.none()

        # Scope the submissions prefetch to what the serializer can
        # legitimately show. Unfiltered, it loaded EVERY submission each
        # student had ever made — across all courses, including other
        # teachers' — into memory for the serializer to then discard in
        # Python. For a teacher, only submissions in that teacher's own
        # courses are relevant (and visible); a student only ever sees
        # their own rows, which student__submissions already guarantees.
        submissions_qs = StudentSubmission.objects.select_related("assignment")
        if user.user_type == UserTypes.TEACHER:
            submissions_qs = submissions_qs.filter(assignment__course__teacher=user)

        queryset = (
            StudentCourse.objects
            # course__teacher is walked by StudentCourseSerializer.get_teacher
            # on every row; without it that is one extra query per row.
            .select_related("student", "course", "course__teacher").prefetch_related(
                "course__assignments",
                Prefetch("student__submissions", queryset=submissions_qs),
            )
            # StudentCourse has no Meta.ordering, so paginating this
            # unordered queryset gave Postgres licence to return rows in any
            # order per page - a row could appear on two pages or on none.
            .order_by("-created_at", "id")
        )

        if user.user_type == UserTypes.TEACHER:
            return queryset.filter(course__teacher=user).distinct()
        elif user.user_type == UserTypes.STUDENT:
            return queryset.filter(student=user)
        return StudentCourse.objects.none()

    def get_serializer_class(self):
        if self.action == "my_students":
            return StudentListSerializer
        if self.action in ["retrieve"]:
            return StudentCourseDetailSerializer
        return super().get_serializer_class()

    def filter_queryset(self, queryset):
        if self.action == "my_students":
            # Not filterset_fields: those join every enrollment the student
            # has, including other teachers' (see MyStudentsFilter).
            self.filterset_class = MyStudentsFilter
            self.search_fields = ["first_name", "last_name", "email"]

        return super().filter_queryset(queryset)

    @extend_schema(
        summary="List Teacher's Students",
        description=(
            "Retrieves a unique list of students enrolled in any course taught by the currently authenticated teacher. "
            "Supports filtering by specific course or session and searching by name/email."
        ),
        parameters=[
            OpenApiParameter(
                name="enrollments__course",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description="Filter students by a specific Course UUID.",
            ),
            OpenApiParameter(
                name="enrollments__course__session",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description="Filter students by a specific Session (Academic Period) UUID.",
            ),
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Search students by first name, last name, or email (case-insensitive).",
            ),
        ],
        responses={200: StudentListSerializer(many=True)},
        tags=["Student Course"],
    )
    @action(
        detail=False,
        methods=["GET"],
        url_path="my-students",
        serializer_class=StudentListSerializer,
        filter_backends=[DjangoFilterBackend, filters.SearchFilter],
        permission_classes=[IsTeacher],
    )
    def my_students(self, request):
        """
        Returns a list of unique students enrolled in courses
        taught by the authenticated teacher
        """
        queryset = self.filter_queryset(self.get_queryset())

        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


@extend_schema_view(
    list=extend_schema(
        tags=["Course Categories"],
        summary="List all course categories",
        description="Retrieve a paginated list of all course categories in the system.",
        parameters=[
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number for pagination",
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of results per page",
            ),
        ],
        responses={
            200: CourseCategorySerializer(many=True),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    create=extend_schema(
        tags=["Course Categories"],
        summary="Create a new course category",
        description="Create a new course category with the provided details.",
        request=CourseCategorySerializer,
        responses={
            201: OpenApiResponse(
                response=CourseCategorySerializer,
                description="Course category created successfully",
            ),
            400: OpenApiResponse(
                description="Invalid input. Missing required fields or invalid data format"
            ),
        },
    ),
    retrieve=extend_schema(
        tags=["Course Categories"],
        summary="Retrieve a course category",
        description="Retrieve detailed information about a specific course category by its ID.",
        responses={
            200: CourseCategorySerializer,
            404: OpenApiResponse(description="Course category not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    partial_update=extend_schema(
        tags=["Course Categories"],
        summary="Partially update a course category",
        description="Update one or more fields of an existing course category.",
        request=CourseCategorySerializer(partial=True),
        responses={
            200: CourseCategorySerializer,
            400: OpenApiResponse(description="Invalid input data"),
            401: OpenApiResponse(
                description="Authentication credentials were not provided"
            ),
            403: OpenApiResponse(
                description="User does not have permission to perform this action"
            ),
            404: OpenApiResponse(description="Category not found"),
        },
    ),
    destroy=extend_schema(
        tags=["Course Categories"],
        summary="Delete a course category",
        description="Delete a course category by ID. This action cannot be undone.",
        responses={
            204: OpenApiResponse(description="Course category deleted successfully"),
            404: OpenApiResponse(description="Course category not found"),
        },
    ),
)
class CourseCategoryViewSet(UserCacheMixin, viewsets.ModelViewSet):
    """
    API endpoint that allows course categories to be viewed or edited.
    """

    queryset = CourseCategory.objects.all()
    serializer_class = CourseCategorySerializer
    permission_classes = (IsAuthenticated, IsTeacherOrReadOnly)
    pagination_class = StandardPageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = {
        "name": ["exact", "icontains"],
    }
    search_fields = ["name"]
    ordering_fields = ["name"]

    def get_queryset(self):
        queryset = super().get_queryset()

        # Filter by search query if provided
        search_query = self.request.query_params.get("search", None)
        if search_query:
            queryset = queryset.filter(name__icontains=search_query)
        return queryset

    @extend_schema(
        tags=["Course Categories"],
        summary="Get courses in a category",
        description="List all courses associated with a specific category.",
        parameters=[
            OpenApiParameter(
                name="id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description="ID of the course category",
                required=True,
            ),
        ],
        responses={
            200: CourseSerializer(many=True),
            404: OpenApiResponse(description="Category not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    @action(detail=True, methods=["get"], url_path="courses")
    def category_courses(self, request, pk=None, *args, **kwargs):
        """
        List all courses associated with a specific category.
        """
        category = self.get_object()
        courses = category.courses.filter(is_active=True)
        page = self.paginate_queryset(courses)
        if page is not None:
            serializer = CourseSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = CourseSerializer(courses, many=True, context={"request": request})
        return Response(serializer.data)


@extend_schema_view(
    list=extend_schema(
        tags=["Topic"],
        summary="List all Topic",
        parameters=[
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number for pagination",
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of results per page",
            ),
        ],
    ),
    retrieve=extend_schema(
        tags=["Topic"],
        summary="Retrieve a Topic",
        responses={
            200: TopicSerializer,
            404: OpenApiResponse(description="Topic not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    create=extend_schema(
        tags=["Topic"],
        summary="Create a new Topic",
        description="Create a new Topic withe the required details",
        request=TopicSerializer,
        responses={
            201: TopicSerializer,
            400: OpenApiResponse(description="Invalid data provided"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    partial_update=extend_schema(
        tags=["Topic"],
        summary="Partially update a Topic",
        request=TopicSerializer,
        responses={
            200: TopicSerializer,
            400: OpenApiResponse(description="Invalid data provided"),
            404: OpenApiResponse(description="Topic not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    destroy=extend_schema(
        tags=["Topic"],
        summary="Delete a Topic",
        description="Delete a Topic by ID. This action cannot be undone.",
        responses={
            204: OpenApiResponse(description="Topic deleted successfully"),
            404: OpenApiResponse(description="Topic not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
)
class TopicViewSet(UserCacheMixin, viewsets.ModelViewSet):
    queryset = Topic.objects.all()
    serializer_class = TopicSerializer
    # `permission_classes`, not `permission_class`: the misspelling was
    # silently ignored by DRF, which then fell back to the project-wide
    # default of IsAuthenticated alone - so any authenticated user,
    # students included, could create/edit/delete topics.
    permission_classes = (IsAuthenticated, IsTeacherOrReadOnly)
    pagination_class = StandardPageNumberPagination
    # "options", not "option": the typo made DRF reject every OPTIONS
    # request with 405, which breaks CORS preflight for this endpoint.
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ("name", "course__name")
    search_fields = ("name", "course__name")
    ordering_fields = ("name",)

    def get_queryset(self):
        user = self.request.user

        if user.user_type == UserTypes.TEACHER:
            return Topic.objects.filter(course__teacher=user)
        elif user.user_type == UserTypes.STUDENT:
            # Previously matched on "an enrollment row exists", with no
            # status condition - so a WITHDRAWN student kept reading the
            # topic list of a course they had been removed from.
            return Topic.objects.filter(
                course__enrollments__student=user,
                course__enrollments__enrollment_status__in=(
                    COURSE_ACCESS_ENROLLMENT_STATUSES
                ),
            ).distinct()
        else:
            return Topic.objects.none()
