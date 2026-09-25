import logging
from datetime import date, timedelta

from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.db.models import (
    Avg,
    Case,
    Count,
    DurationField,
    ExpressionWrapper,
    F,
    FloatField,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    Value,
    Variance,
    When,
)
from django.db.models.functions import Cast, Coalesce, ExtractMonth, TruncDay
from django.utils import timezone
from django.utils.text import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import filters, pagination, status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from ai_processor.models import AssistantType, ChatMessage, ChatSession, RoleType
from ai_processor.services import AI_CONFIDENCE_THRESHOLD, ai_processor
from assignments.models import Assignment, AssignmentStatus
from AutoGrader.cache_generation import (
    SCOPE_ANY_SCHOOL,
    SCOPE_ANY_USER,
    SCOPE_GLOBAL,
    SCOPE_SCHOOL,
    SCOPE_USER,
    versioned_key,
)
from AutoGrader.error_messages import describe_user_error
from AutoGrader.pagination import StandardPageNumberPagination
from billing.models import CONVERSION_FACTOR, CreditBucketType, CreditUsageLog
from billing.refusals import PERMANENT_AI_REFUSALS, log_refusal, refusal_response
from billing.services import FEATURE_TO_ANALYTICS_FIELD
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    SessionOwnerType,
    StudentCourse,
)
from classrooms.permissions import IsSchoolAdmin, IsStudent, IsSuperAdmin, IsTeacher
from dashboard.models import SchoolAtRiskSnapshot
from dashboard.risk import RiskInputs, StudentRiskEvaluator
from dashboard.serializers import (
    AssignmentActivityOverTimeChartSerializer,
    ConcurrencySerializer,
    CourseAnalyticsSerializer,
    CourseOverviewChartSerializer,
    CoursePerformanceDashboardPageSerializer,
    CoursePerformanceDashboardSerializer,
    CustomAIPrompt,
    CustomAIReply,
    DashboardChatSessionSerializer,
    PaginatedTeacherStudentAnalyticsSerializer,
    PlatformAdoptionSerializer,
    PlatformAIPerformanceSerializer,
    PlatformUsageSerializer,
    ScalingSignalsSerializer,
    SchoolAdminStudentPerformanceSerializer,
    SchoolAdminSummarySerializer,
    SchoolAnalyticsSerializer,
    SchoolAtRiskTrendSerializer,
    StudentAssignmentListSerializer,
    StudentAssignmentStatusSummarySerializer,
    StudentDashboardOverviewSerializer,
    SuperAdminStudentPerformanceSerializer,
    TeacherAssignmentAnalyticsSerializer,
    TeacherCourseAnalyticsSerializer,
    TeacherDashboardOverviewSerializer,
    TeacherDetailSerializer,
    TeacherPerformanceDashboardSerializer,
    TeacherPerformanceSerializer,
    TeacherStudentAnalyticsSerializer,
    UnitPerformanceSerializer,
)
from dashboard.services import (
    SchoolAdminAIContextService,
    SchoolAdminWeeklySummaryService,
    TeacherAIContextService,
    TeacherPerformanceStatsService,
    dashboard_context_json,
)
from dashboard.throttling import CustomAIPromptThrottle
from students.models import StudentSubmission
from students.services import get_grade_details, get_letter_grade_from_gpa
from users.models import CustomUser, UserTypes
from users.services import (
    get_peak_concurrent_users,
    get_peak_time_of_day,
    get_time_range,
)

logger = logging.getLogger(__name__)

#: TTL for the superadmin analytics dashboards (H-1 families 15-21).
#:
#: 24 hours, not the project's usual 15 minutes, and the reasoning is the
#: point: once a generation counter is embedded in the key, freshness is
#: guaranteed by the counter, so the TTL is no longer a staleness bound. It
#: becomes a pure memory/garbage-collection knob for superseded entries.
#:
#: Measured: these dashboards see ~6.5 relevant mutations/day in production,
#: while a 900s TTL expired them 96 times/day - so the TTL, not real change,
#: was causing ~94% of rebuilds. At 24h that falls to ~7.5/day, a ~14x
#: reduction, with NO staleness because versioning handles it.
SUPERADMIN_DASHBOARD_TTL_SECONDS = 60 * 60 * 24


def get_or_create_dashboard_chat_session(user, assistant_type):
    session, _ = ChatSession.objects.get_or_create(
        user=user,
        assistant_type=assistant_type,
    )
    return session


def append_dashboard_chat_message(session, role, content):
    return ChatMessage.objects.create(
        session=session,
        role=role,
        content=content,
    )


#: Told to the model in place of a section that failed to load, so it says
#: the data is unavailable instead of reasoning over an empty `{}`.
UNAVAILABLE_SECTION = (
    "UNAVAILABLE: this section could not be loaded because of an internal "
    "error. Do not guess its contents; tell the user this data is "
    "temporarily unavailable."
)


def dashboard_context_section(title, build, *, user, task_type):
    """One titled block of AI chat context.

    A section that fails is LOGGED with enough context to trace it, and the
    model is told explicitly that the data is missing. These sections used
    to be built inside `except Exception: section = {}`, which hid real
    defects: the school-admin chat called a view method that no longer
    existed, and every request for months sent the model an empty teachers
    section without a single log line.
    """
    try:
        body = dashboard_context_json(build())
    except Exception:
        logger.exception(
            "Dashboard AI context section failed to load",
            extra={"section": title, "user_id": str(user.id), "task_type": task_type},
        )
        body = UNAVAILABLE_SECTION
    return f"### {title}\n{body}"


def run_dashboard_ai_chat(
    request, prompt, *, assistant_type, role, context, feature, task_type
):
    """Ask the model, then record the exchange.

    THE PROVIDER CALL IS MADE OUTSIDE ANY DATABASE TRANSACTION. It used to run
    inside `transaction.atomic()`, holding a transaction - and, under
    retries, up to three provider round trips - open on the connection for
    the whole call. Now the chat turn is written afterwards, in one short
    transaction, and only when there is a reply: a failed or refused call
    still leaves no orphaned user message behind, which is what the atomic
    block was protecting.
    """
    user = request.user
    try:
        ai_feedback = ai_processor.custom_ai_prompt_retry(
            user,
            context,
            prompt,
            role,
            feature=feature,
            task_type=task_type,
        )
    except PERMANENT_AI_REFUSALS as e:
        log_refusal(logger, "Custom AI prompt", e, user_id=str(user.id))
        return refusal_response(e)
    except Exception as e:
        logger.error(
            "Custom AI prompt failed",
            exc_info=e,
            extra={"user_id": str(user.id), "task_type": task_type},
        )
        return Response(
            {
                "error": describe_user_error(
                    e,
                    fallback_message=(
                        "We couldn't generate a response right now. Please try again."
                    ),
                )
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    with transaction.atomic():
        chat_session = get_or_create_dashboard_chat_session(user, assistant_type)
        append_dashboard_chat_message(chat_session, RoleType.USER, prompt)
        append_dashboard_chat_message(chat_session, RoleType.ASSISTANT, ai_feedback)

    return Response(CustomAIReply({"response": ai_feedback}).data)


class SuperAdminDashboardView(viewsets.ViewSet):
    permission_classes = [IsSuperAdmin]

    @extend_schema(
        tags=["Super Admin"],
        summary="Platform Adoption Metrics",
        description="""
        Retrieve high-level platform adoption and growth metrics.

        Metrics include:
        - Total signup counts segmented by user role (Teachers, Students, School Admins).
        - New signup statistics for daily, weekly, and monthly intervals.
        - Teacher activation rate (percentage of teachers with at least one assignment).
        - Active user count for the last 30 days.
        - Average courses per teacher and average course size.
        """,
        responses={200: PlatformAdoptionSerializer},
    )
    @action(detail=False, methods=["get"], url_path="dashboard/adoption")
    def platform_adoption(self, request, *args, **kwargs):
        cache_key = versioned_key(
            f"superadmins:user_id__{request.user.id}:view__adoption",
            [(SCOPE_GLOBAL, None)],
        )
        data = cache.get(cache_key)

        if data is None:
            now = timezone.now()
            day_ago = now - timedelta(days=1)
            week_ago = now - timedelta(days=7)
            month_ago = now - timedelta(days=30)

            totals = CustomUser.objects.values("user_type").annotate(count=Count("id"))
            role_totals = {t["user_type"]: t["count"] for t in totals}

            new_signups = CustomUser.objects.aggregate(
                daily=Count("id", filter=Q(date_joined__gte=day_ago)),
                weekly=Count("id", filter=Q(date_joined__gte=week_ago)),
                monthly=Count("id", filter=Q(date_joined__gte=month_ago)),
            )

            teacher_queryset = CustomUser.objects.filter(
                user_type=UserTypes.TEACHER, is_active=True
            )
            total_teachers = teacher_queryset.count()

            # Activated teachers are those who have more than one assignment
            activated_teachers = Assignment.objects.values("teacher").distinct().count()
            activated_teacher_percent = round(
                (activated_teachers / max(total_teachers, 1)) * 100, 2
            )

            active_teachers_30d = teacher_queryset.filter(
                last_login__gte=month_ago
            ).count()

            avg_courses_per_teacher = (
                Course.objects.values("teacher")
                .annotate(course_count=Count("id"))
                .aggregate(avg=Avg("course_count"))
                .get("avg")
                or 0
            )

            avg_course_size = (
                Course.objects.annotate(
                    student_count=Count("enrollments", distinct=True)
                ).aggregate(Avg("student_count"))["student_count__avg"]
                or 0
            )

            data = {
                "total_signups": {
                    "teachers": role_totals.get(UserTypes.TEACHER, 0),
                    "students": role_totals.get(UserTypes.STUDENT, 0),
                    "school_admins": role_totals.get(UserTypes.SCHOOL_ADMIN, 0),
                },
                "new_signups": new_signups,
                "activated_percent": activated_teacher_percent,
                "active_last_30_days": active_teachers_30d,
                "average_course_per_teacher": round(float(avg_courses_per_teacher), 2),
                "average_course_size": round(float(avg_course_size), 2),
            }

            serializer = PlatformAdoptionSerializer(data)
            data = serializer.data

            cache.set(cache_key, data, SUPERADMIN_DASHBOARD_TTL_SECONDS)

        return Response(data)

    @extend_schema(
        tags=["Super Admin"],
        summary="Platform Usage Metrics",
        description="""
        Retrieve detailed platform-wide usage and efficiency metrics.

        Metrics include:
        - Total assignments created and graded across the entire platform.
        - Average number of assignments per course and per active teacher.
        - Percentage of assignments that are fully graded.
        - Grading turnaround time percentiles (P50 and P95) for student submissions.
        """,
        responses={200: PlatformUsageSerializer},
    )
    @action(detail=False, methods=["get"], url_path="dashboard/usage")
    def platform_usage(self, request, *args, **kwargs):
        cache_key = versioned_key(
            f"superadmins:user_id__{request.user.id}:view__usage",
            [(SCOPE_GLOBAL, None)],
        )
        data = cache.get(cache_key)

        if data is None:
            # BASE QUERYSETS
            assignments = Assignment.objects.all()
            submissions = StudentSubmission.objects.all()

            # TOTAL ASSIGNMENTS
            total_assignments_created = assignments.count()

            # TOTAL ASSIGNMENTS GRADED
            total_assignments_graded = (
                assignments.filter(submissions__graded_at__isnull=False)
                .distinct()
                .count()
            )

            avg_assignments_per_course = (
                Course.objects.annotate(assignment_count=Count("assignments"))
                .aggregate(avg=Avg("assignment_count"))
                .get("avg")
                or 0
            )

            avg_assignments_per_teacher = (
                CustomUser.objects.filter(user_type=UserTypes.TEACHER)
                .annotate(assignment_count=Count("assignments"))
                .aggregate(avg=Avg("assignment_count"))
                .get("avg")
                or 0
            )

            #  % OF ASSIGNMENTS FULLY GRADED
            fully_graded_assignments = (
                assignments.annotate(
                    total_submissions=Count("submissions"),
                    graded_submissions=Count(
                        "submissions", filter=Q(submissions__graded_at__isnull=False)
                    ),
                )
                .filter(
                    total_submissions__gt=0, total_submissions=F("graded_submissions")
                )
                .count()
            )
            percent_fully_graded = round(
                (fully_graded_assignments / max(total_assignments_created, 1)) * 100, 2
            )

            # GRADING TURNAROUND TIME (P50 / P95)
            turnaround_qs = (
                submissions.filter(graded_at__isnull=False)
                .annotate(
                    turnaround=ExpressionWrapper(
                        F("graded_at") - F("submission_date"),
                        output_field=DurationField(),
                    )
                )
                .values_list("turnaround", flat=True)
            )

            turnaround_values = sorted(turnaround_qs)

            def percentile(data, p):
                if not data:
                    return None

                k = int(len(data) * p)
                k = min(k, len(data) - 1)
                return data[k]

            p50 = percentile(turnaround_values, 0.50)
            p95 = percentile(turnaround_values, 0.95)

            # RESPONSE
            data = {
                "total_assignments_created": total_assignments_created,
                "total_assignments_graded": total_assignments_graded,
                "avg_assignments_per_course": round(
                    float(avg_assignments_per_course), 2
                ),
                "avg_assignments_per_active_teacher": round(
                    float(avg_assignments_per_teacher), 2
                ),
                "assignment_percent_fully_graded": percent_fully_graded,
                "grading_turnaround_time_p50": p50,
                "grading_turnaround_time_p95": p95,
            }

            serializer = PlatformUsageSerializer(data)
            data = serializer.data

            cache.set(cache_key, data, SUPERADMIN_DASHBOARD_TTL_SECONDS)
        return Response(data)

    @extend_schema(
        tags=["Super Admin"],
        summary="Platform AI Performance Metrics",
        description="""
        Retrieve detailed analytics on the performance and trust levels of the AI engine across the platform.

        Metrics include:
        - Confidence Levels: Average extraction and grading confidence scores, low-confidence rates, and score variance.
        - Trust & Risk: Manual override rates for AI-generated assignments and regrade rates for AI-graded submissions.
        - Efficiency: Average processing times for assignment extraction and automated grading.
        """,
        responses={200: PlatformAIPerformanceSerializer},
    )
    @action(detail=False, methods=["get"], url_path="dashboard/ai_performance")
    def platform_ai_performance(self, request, *args, **kwargs):
        cache_key = versioned_key(
            f"superadmins:user_id__{request.user.id}:view__ai_performance",
            [(SCOPE_GLOBAL, None)],
        )
        data = cache.get(cache_key)

        if data is None:
            assignments = Assignment.objects.all()
            submissions = StudentSubmission.objects.all()

            # EXTRACTION CONFIDENCE
            extraction_stats = assignments.aggregate(
                avg_extraction_confidence=Avg("extraction_confidence"),
                low_extraction_count=Count(
                    "id", filter=Q(extraction_confidence__lt=AI_CONFIDENCE_THRESHOLD)
                ),
                total_assignments=Count("id"),
                extraction_variance=Variance("extraction_confidence"),
            )

            # GRADING CONFIDENCE
            grading_stats = submissions.aggregate(
                avg_grading_confidence=Avg("grading_confidence"),
                low_grading_count=Count(
                    "id", filter=Q(grading_confidence__lt=AI_CONFIDENCE_THRESHOLD)
                ),
                total_submissions=Count("id"),
                grading_variance=Variance("grading_confidence"),
            )

            low_extraction_rate = (
                extraction_stats["low_extraction_count"]
                / max(extraction_stats["total_assignments"], 1)
            ) * 100
            low_grading_rate = (
                grading_stats["low_grading_count"]
                / max(grading_stats["total_submissions"], 1)
            ) * 100

            ai_assignments = Assignment.objects.filter(ai_generated=True)
            total_ai_assignments = ai_assignments.count()

            if total_ai_assignments > 0:
                overriden_count = ai_assignments.filter(was_overridden=True).count()
                manual_override_rate = round(
                    (overriden_count / total_ai_assignments) * 100, 2
                )
            else:
                manual_override_rate = 0

            total_ai_graded = StudentSubmission.objects.filter(
                ai_graded_at__isnull=False
            ).count()
            regraded = StudentSubmission.objects.filter(was_regraded=True).count()

            if total_ai_graded == 0:
                regrade_rate = 0
            else:
                regrade_rate = round((regraded / total_ai_graded) * 100, 2)

            avg_assignment_processing_time = (
                Assignment.objects.filter(extraction_completed_at__isnull=False)
                .annotate(
                    processing_time=ExpressionWrapper(
                        F("extraction_completed_at") - F("extraction_started_at"),
                        output_field=DurationField(),
                    )
                )
                .aggregate(avg_processing_time=Avg("processing_time"))[
                    "avg_processing_time"
                ]
            )

            avg_grading_time = (
                StudentSubmission.objects.filter(ai_graded_at__isnull=False)
                .annotate(
                    grading_time=ExpressionWrapper(
                        F("ai_grading_completed_at") - F("ai_graded_at"),
                        output_field=DurationField(),
                    )
                )
                .aggregate(avg_time=Avg("grading_time"))["avg_time"]
            )

            # RESPONSE
            data = {
                "confidence": {
                    "average_extraction": round(
                        extraction_stats["avg_extraction_confidence"] or 0, 2
                    ),
                    "average_grading": round(
                        grading_stats["avg_grading_confidence"] or 0, 2
                    ),
                    "low_confidence_extraction_rate": round(low_extraction_rate, 2),
                    "low_confidence_grading_rate": round(low_grading_rate, 2),
                    "confidence_variance_extraction": extraction_stats[
                        "extraction_variance"
                    ],
                    "confidence_variance_grading": grading_stats["grading_variance"],
                },
                "risk_indicators": {
                    "manual_override_rate": manual_override_rate,
                    "regrade_rate": regrade_rate,
                    "avg_assignment_processing_time": avg_assignment_processing_time,
                    "avg_grading_processing_time": avg_grading_time,
                },
            }

            serializer = PlatformAIPerformanceSerializer(data)
            data = serializer.data

            cache.set(cache_key, data, SUPERADMIN_DASHBOARD_TTL_SECONDS)
        return Response(data)

    @extend_schema(
        tags=["Super Admin"],
        summary="Institutional Scaling Signals",
        description="""
        Retrieve metrics tracking the transition from individual use to institutional adoption.

        Metrics include:
        - School Coverage: Total schools and schools with multiple active teachers.
        - Adoption Depth: Multi-teacher adoption rate and average teachers per school.
        - Operational Ratios: Admin-to-teacher ratio (indicator of managed growth).
        - Value Indicators: Average assignments and AI confidence scores aggregated at the school level.
        """,
        responses={200: ScalingSignalsSerializer},
    )
    @action(detail=False, methods=["get"], url_path="dashboard/scaling_signals")
    def scaling_signals(self, request, *args, **kwargs):
        """
        Institutional & Scaling Signals for Founder/Super Admin.
        Tracks how the platform is moving from individual use to institutional adoption.
        """

        cache_key = versioned_key(
            f"superadmins:user_id__{request.user.id}:view__scaling_signals",
            [(SCOPE_GLOBAL, None)],
        )
        data = cache.get(cache_key)

        if data is None:

            schools_query = School.objects.annotate(
                active_teacher_count=Count(
                    "users",
                    filter=Q(users__user_type=UserTypes.TEACHER, users__is_active=True),
                    distinct=True,
                )
            )
            total_schools = schools_query.count()
            schools_with_multiple_teachers = schools_query.filter(
                active_teacher_count__gt=1
            ).count()

            # Average Teacher per School
            avg_teachers_per_school = (
                schools_query.aggregate(avg=Avg("active_teacher_count"))["avg"] or 0
            )

            total_school_admins = CustomUser.objects.filter(
                user_type=UserTypes.SCHOOL_ADMIN, school__isnull=False, is_active=True
            ).count()

            total_teachers = CustomUser.objects.filter(
                user_type=UserTypes.TEACHER, school__isnull=False, is_active=True
            ).count()

            if total_school_admins == 0:
                admin_to_teacher_ratio = None
            else:
                admin_to_teacher_ratio = round(total_teachers / total_school_admins, 2)

            avg_assignments_per_school = (
                School.objects.annotate(
                    assignment_count=Count("users__assignments", distinct=True)
                ).aggregate(Avg("assignment_count"))["assignment_count__avg"]
                or 0
            )

            ai_confidence_by_school = School.objects.annotate(
                avg_grading_confidence=Avg(
                    "users__assignments__submissions__grading_confidence"
                ),
                avg_extraction_confidence=Avg(
                    "users__assignments__extraction_confidence"
                ),
            ).aggregate(
                total_avg_grading=Avg("avg_grading_confidence"),
                total_avg_extraction=Avg("avg_extraction_confidence"),
            )

            data = {
                "total_schools": total_schools,
                "schools_with_multiple_teachers": schools_with_multiple_teachers,
                "multi_teacher_adoption_rate": round(
                    (schools_with_multiple_teachers / max(total_schools, 1)) * 100, 2
                ),
                "avg_teachers_per_school": round(float(avg_teachers_per_school), 2),
                "admin_to_teacher_ratio": admin_to_teacher_ratio,
                "avg_assignments_per_school": round(
                    float(avg_assignments_per_school), 2
                ),
                "avg_grading_confidence_per_school": round(
                    float(ai_confidence_by_school["total_avg_grading"] or 0), 2
                ),
                "avg_extraction_confidence_per_school": round(
                    float(ai_confidence_by_school["total_avg_extraction"] or 0), 2
                ),
            }

            serializer = ScalingSignalsSerializer(data)
            data = serializer.data

            cache.set(cache_key, data, SUPERADMIN_DASHBOARD_TTL_SECONDS)

        return Response(data)

    @extend_schema(tags=["Super Admin"])
    def summary(self, request, *args, **kwargs):
        # Implementation for summary endpoint

        total_schools = School.objects.all()
        total_teachers = CustomUser.objects.filter(
            user_type=UserTypes.TEACHER, is_active=True
        )
        total_students = CustomUser.objects.filter(
            user_type=UserTypes.STUDENT, is_active=True
        )
        total_school_admin = CustomUser.objects.filter(
            user_type=UserTypes.SCHOOL_ADMIN, is_active=True
        )
        active_courses = Course.objects.filter(is_active=True)
        total_assignments = Assignment.objects.all()
        total_submissions = StudentSubmission.objects.all()

        # 1. Average course size (students per course)
        avg_course_size = (
            Course.objects.annotate(
                student_count=Count("enrollments", distinct=True)
            ).aggregate(Avg("student_count"))["student_count__avg"]
            or 0
        )

        # 2. Total assignment graded (submissions with a score)
        # Assuming score is not null means it's graded
        total_assignments_graded = StudentSubmission.objects.filter(
            score__isnull=False
        ).count()

        # 3. Average assignment per course
        avg_assignments_per_course = (
            Course.objects.annotate(
                assignment_count=Count("assignments", distinct=True)
            ).aggregate(Avg("assignment_count"))["assignment_count__avg"]
            or 0
        )

        # 4. Average course per teacher
        avg_courses_per_teacher = (
            CustomUser.objects.filter(user_type=UserTypes.TEACHER)
            .annotate(course_count=Count("courses", distinct=True))
            .aggregate(Avg("course_count"))["course_count__avg"]
            or 0
        )

        # 5. Average AI grading confidence level
        avg_grading_confidence = (
            StudentSubmission.objects.filter(
                feedback__grading_evaluation__grading_confidence_score__isnull=False
            )
            .annotate(
                conf_score=Cast(
                    "feedback__grading_evaluation__grading_confidence_score",
                    output_field=FloatField(),
                )
            )
            .aggregate(Avg("conf_score"))["conf_score__avg"]
            or 0
        )

        # 6. Average AI extraction confidence level
        avg_extraction_confidence = (
            Assignment.objects.filter(extraction_confidence__isnull=False).aggregate(
                Avg("extraction_confidence")
            )["extraction_confidence__avg"]
            or 0
        )

        return Response(
            {
                "total_schools": total_schools.count(),
                "teachers_signup": total_teachers.count(),
                "students_signup": total_students.count(),
                "school_admin_signup": total_school_admin.count(),
                "active_courses": active_courses.count(),
                "total_assignments": total_assignments.count(),
                "total_submissions": total_submissions.count(),
                "avg_course_size": round(avg_course_size, 2),
                "total_assignments_graded": total_assignments_graded,
                "avg_assignments_per_course": round(avg_assignments_per_course, 2),
                "avg_courses_per_teacher": round(avg_courses_per_teacher, 2),
                "avg_grading_confidence": round(avg_grading_confidence, 2),
                "avg_extraction_confidence": round(avg_extraction_confidence, 2),
            }
        )

    @extend_schema(
        tags=["Super Admin"],
        summary="Platform Schools Analytics",
        description="""
        Retrieve high-level performance and engagement metrics for all schools on the platform.

        Metrics for each school include:
        - Basic info (School ID, Name).
        - User Depth: Counts of active teachers and students.
        - Academic Activity: Total courses and assignments created.
        - Performance Indicators: Average student performance (final grades) within the school.
        """,
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
                description="Number of results per page (max 100)",
            ),
        ],
        responses={200: SchoolAnalyticsSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="dashboard/schools")
    def schools(self, request, *args, **kwargs):
        paginator = StandardPageNumberPagination()
        page_number = request.query_params.get(paginator.page_query_param, "1")
        page_size = request.query_params.get(paginator.page_size_query_param, "")
        cache_key = versioned_key(
            f"superadmins:user_id__{request.user.id}:view__schools"
            f":{page_number}:{page_size}",
            # Depends on the School table alone - NOT on global activity.
            [(SCOPE_ANY_SCHOOL, None)],
        )
        data = cache.get(cache_key)

        if data is None:

            # Explicit order: an unordered queryset pages non-deterministically.
            schools = paginator.paginate_queryset(
                School.objects.order_by("name", "id"), request, view=self
            )
            school_ids = [school.id for school in schools]

            # One grouped query per figure, each over a single relation. The
            # previous single annotate() joined users -> courses -> enrolments
            # and users -> courses -> assignments at once, so every enrolment
            # grade was repeated once per assignment and the average was
            # weighted by assignment count - measured: a school whose two
            # enrolments averaged 50.0 was reported as 83.33. Five queries per
            # page, however large the schools are.
            teacher_counts = _count_by(
                CustomUser.objects.filter(
                    school_id__in=school_ids,
                    user_type=UserTypes.TEACHER,
                    is_active=True,
                ),
                "school_id",
            )
            student_counts = _count_by(
                CustomUser.objects.filter(
                    school_id__in=school_ids,
                    user_type=UserTypes.STUDENT,
                    is_active=True,
                ),
                "school_id",
            )
            course_counts = _count_by(
                Course.objects.filter(teacher__school_id__in=school_ids),
                "teacher__school_id",
            )
            performance_by_school = {
                row["course__teacher__school_id"]: row["average"]
                for row in StudentCourse.objects.filter(
                    course__teacher__school_id__in=school_ids,
                    course__teacher__user_type=UserTypes.TEACHER,
                )
                .values("course__teacher__school_id")
                .annotate(average=Avg("final_grade"))
                .order_by()
            }

            result = [
                {
                    "school_id": school.id,
                    "school_name": school.name,
                    "teachers": teacher_counts.get(school.id, 0),
                    "students": student_counts.get(school.id, 0),
                    "courses": course_counts.get(school.id, 0),
                    "average_performance": round(
                        float(performance_by_school.get(school.id) or 0), 2
                    ),
                }
                for school in schools
            ]

            serializer = SchoolAnalyticsSerializer(result, many=True)
            data = paginator.get_paginated_response(serializer.data).data

            cache.set(cache_key, data, SUPERADMIN_DASHBOARD_TTL_SECONDS)

        return Response(data)

    @extend_schema(
        tags=["Super Admin"],
        summary="Platform Teachers Performance",
        description="""
        Retrieve high-level performance and engagement metrics for all teachers on the platform.

        Metrics for each teacher include:
        - Basic info (Teacher ID, Name).
        - Course Load: Total number of courses assigned.
        - Student Reach: Total number of unique students enrolled in their courses.
        - Academic Performance: Average student performance (final grades) across all their courses.
        - Engagement: Assignment completion rates (actual submissions vs. expected based on enrollments).
        """,
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
                description="Number of results per page (max 100)",
            ),
        ],
        responses={200: TeacherPerformanceSerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="dashboard/teachers")
    def teachers(self, request, *args, **kwargs):
        """
        Returns performance metrics for all teachers:
        - Number of courses per teacher
        - Number of students per teacher
        - Average student performance per teacher
        - Assignment completion rates per teacher
        """

        paginator = StandardPageNumberPagination()
        page_number = request.query_params.get(paginator.page_query_param, "1")
        page_size = request.query_params.get(paginator.page_size_query_param, "")
        cache_key = versioned_key(
            f"superadmins:user_id__{request.user.id}:view__teachers"
            f":{page_number}:{page_size}",
            # Depends on the CustomUser table alone.
            [(SCOPE_ANY_USER, None)],
        )
        data = cache.get(cache_key)

        if data is None:

            # Explicit order: an unordered queryset pages non-deterministically.
            teachers = paginator.paginate_queryset(
                CustomUser.objects.filter(
                    user_type=UserTypes.TEACHER, is_active=True
                ).order_by("first_name", "last_name", "id"),
                request,
                view=self,
            )
            teacher_ids = [teacher.id for teacher in teachers]

            # Grouped queries over single relations, replacing one annotate()
            # that joined courses to enrolments AND to assignments ->
            # submissions. That join repeated each course once per
            # (enrolment x submission), so a teacher with ONE course was
            # reported as having 18, and the average grade was weighted by
            # submission volume. It also ran two more aggregates per teacher.
            course_counts = _count_by(
                Course.objects.filter(teacher_id__in=teacher_ids), "teacher_id"
            )
            enrolment_stats = {
                row["course__teacher_id"]: row
                for row in StudentCourse.objects.filter(
                    course__teacher_id__in=teacher_ids
                )
                .values("course__teacher_id")
                .annotate(
                    students=Count("student", distinct=True),
                    average_grade=Avg("final_grade"),
                )
                .order_by()
            }
            submission_counts = _count_by(
                StudentSubmission.objects.filter(
                    assignment__course__teacher_id__in=teacher_ids
                ),
                "assignment__course__teacher_id",
            )

            # Expected submissions are summed PER COURSE. The previous
            # formula multiplied a teacher's total assignments by their total
            # enrolments across all courses, so a course with 10 assignments
            # and no students plus a course with 30 students and no
            # assignments "expected" 300 submissions instead of 0.
            expected_by_teacher: dict = {}
            for (
                teacher_id,
                assignment_total,
                enrolment_total,
            ) in _with_assignment_and_enrolment_counts(
                Course.objects.filter(teacher_id__in=teacher_ids)
            ).values_list(
                "teacher_id", "assignment_total", "enrolment_total"
            ):
                expected_by_teacher[teacher_id] = (
                    expected_by_teacher.get(teacher_id, 0)
                    + assignment_total * enrolment_total
                )

            performance_data = []
            for teacher in teachers:
                enrolments = enrolment_stats.get(teacher.id) or {}
                expected_submissions = expected_by_teacher.get(teacher.id, 0)
                completion_rate = (
                    (submission_counts.get(teacher.id, 0) / expected_submissions * 100)
                    if expected_submissions > 0
                    else 0
                )

                performance_data.append(
                    {
                        "teacher_id": teacher.id,
                        "teacher_name": f"{teacher.first_name} {teacher.last_name}",
                        "number_of_courses": course_counts.get(teacher.id, 0),
                        "number_of_students": enrolments.get("students") or 0,
                        "average_student_performance": round(
                            float(enrolments.get("average_grade") or 0), 2
                        ),
                        "assignment_completion_rate": round(
                            min(float(completion_rate), 100), 2
                        ),
                    }
                )

            serializer = TeacherPerformanceSerializer(performance_data, many=True)
            data = paginator.get_paginated_response(serializer.data).data

            cache.set(cache_key, data, SUPERADMIN_DASHBOARD_TTL_SECONDS)
        return Response(data)

    @extend_schema(
        tags=["Super Admin"],
        summary="Global Student Performance",
        description="""
        Retrieve platform-wide student performance and engagement metrics.

        Metrics include:
        - Average Grade: The mean final grade across all active student enrollments.
        - Global Completion Rate: Actual submissions vs. expected submissions based on active courses and enrollments.
        - Grade Distribution: Platform-wide count of students in each grade tier (A, B, C, D, F).
        - Active Enrollments: Total count of active student-course pairings.
        """,
        responses={200: SuperAdminStudentPerformanceSerializer},
    )
    @action(detail=False, methods=["get"], url_path="dashboard/students")
    def students(self, request, *args, **kwargs):
        cache_key = versioned_key(
            f"superadmins:user_id__{request.user.id}:view__students",
            [(SCOPE_GLOBAL, None)],
        )
        data = cache.get(cache_key)

        if data is None:
            stats = StudentCourse.objects.active().aggregate(
                avg_grade=Avg("final_grade"),
                total_enrollments=Count("id"),
                total_assignments=Count("course__assignments", distinct=True),
            )

            actual_submissions = StudentSubmission.objects.count()

            expected_submissions = _expected_submission_total(
                Course.objects.filter(is_active=True)
            )

            completion_rate = (
                (actual_submissions / expected_submissions * 100)
                if expected_submissions > 0
                else 0
            )

            distribution = StudentCourse.objects.active().aggregate(
                a=Count(Case(When(final_grade__gte=90, then=Value(1)))),
                b=Count(
                    Case(When(final_grade__gte=80, final_grade__lt=90, then=Value(1)))
                ),
                c=Count(
                    Case(When(final_grade__gte=70, final_grade__lt=80, then=Value(1)))
                ),
                d=Count(
                    Case(When(final_grade__gte=65, final_grade__lt=70, then=Value(1)))
                ),
                f=Count(Case(When(final_grade__lt=65, then=Value(1)))),
            )

            total_graded = sum(distribution.values())

            def get_entry(count):
                return {
                    "count": count,
                    "percentage": (
                        round((count / total_graded) * 100, 2)
                        if total_graded > 0
                        else 0
                    ),
                }

            data = {
                "average_grade": round(float(stats["avg_grade"] or 0), 2),
                "global_assignment_completion_rate": round(
                    min(float(completion_rate), 100), 2
                ),
                "grade_distribution": {
                    "A": get_entry(distribution["a"]),
                    "B": get_entry(distribution["b"]),
                    "C": get_entry(distribution["c"]),
                    "D": get_entry(distribution["d"]),
                    "E": get_entry(0),
                    "F": get_entry(distribution["f"]),
                },
                "total_active_enrollments": stats["total_enrollments"],
            }

            serializer = SuperAdminStudentPerformanceSerializer(data)
            data = serializer.data

            cache.set(cache_key, data, SUPERADMIN_DASHBOARD_TTL_SECONDS)

        return Response(data)

    @extend_schema(
        tags=["Super Admin"],
        summary="Get peak concurrent users and Peak time of day statistics",
        description="Retrieve peak concurrent user and peak activity times within a specified range",
        parameters=[
            OpenApiParameter(
                name="range",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="The time range for the statistics",
                required=False,
                enum=["daily", "weekly", "monthly"],
                default="daily",
            )
        ],
        responses={200: ConcurrencySerializer},
    )
    @action(detail=False, methods=["get"], url_path="dashboard/concurrency")
    def concurrency(self, request, *args, **kwargs):
        # DELIBERATELY UNCACHED (H-1 family 22).
        #
        # This endpoint reads the Redis presence set, not the database, so
        # there is no model mutation to hang a generation counter off - and
        # presence is defined over a 300-second window, so the previous
        # 900-second TTL could serve a "live" concurrency figure three
        # windows out of date.
        #
        # Measured before removing the cache: 18ms cold vs 6.9ms warm, a
        # 3x saving on an endpoint that already costs 5 queries and 4ms of
        # SQL. That does not justify a cache, and certainly not one that
        # makes a real-time number stale. Every other superadmin dashboard
        # measured 6x-199x, which is what a cache is for.
        range_key: str = request.query_params.get("range", "daily")

        start, end = get_time_range(range_key)
        pcu = get_peak_concurrent_users(start, end)
        peak_time = get_peak_time_of_day(start, end)

        serializer = ConcurrencySerializer(
            {
                "time_range": range_key,
                "start": start,
                "end": end,
                "peak_concurrent_users": pcu,
                "peak_time_of_day": peak_time,
            }
        )
        return Response(serializer.data)

    @extend_schema(
        tags=["Super Admin"],
        summary="Get AI detailed information about analytics ",
        request=CustomAIPrompt,
        responses={200: CustomAIReply},
    )
    @action(
        detail=False,
        methods=["POST"],
        url_path="dashboard/custom-ai-prompt",
        throttle_classes=[CustomAIPromptThrottle],
    )
    def custom_ai_prompt(self, request, *args, **kwargs):
        serializer = CustomAIPrompt(data=request.data)
        serializer.is_valid(raise_exception=True)

        prompt = serializer.validated_data["prompt"]
        task_type = "custom_ai_prompt:superadmin"

        def section(title, method):
            return dashboard_context_section(
                title,
                lambda: method(request, *args, **kwargs).data,
                user=request.user,
                task_type=task_type,
            )

        context = "\n\n".join(
            [
                section("PLATFORM ADOPTION METRICS", self.platform_adoption),
                section("PLATFORM USAGE METRICS", self.platform_usage),
                section(
                    "PLATFORM AI PERFORMANCE METRICS", self.platform_ai_performance
                ),
                section("SCALING SIGNALS METRICS", self.scaling_signals),
                section("SUMMARY METRICS", self.summary),
                section("SCHOOLS METRICS", self.schools),
                section("TEACHERS METRICS", self.teachers),
                section("STUDENTS METRICS", self.students),
                section("CONCURRENCY METRICS", self.concurrency),
            ]
        )

        return run_dashboard_ai_chat(
            request,
            prompt,
            assistant_type=AssistantType.SUPER_ADMIN_ANALYTICS,
            role=UserTypes.SUPER_ADMIN,
            context=context,
            feature="Superadmin Custom AI Prompt",
            task_type=task_type,
        )

    @extend_schema(
        tags=["Super Admin"],
        summary="Get custom AI prompt conversation history",
        responses={200: DashboardChatSessionSerializer},
    )
    @action(
        detail=False, methods=["GET"], url_path="dashboard/custom-ai-prompt/history"
    )
    def custom_ai_prompt_history(self, request, *args, **kwargs):
        session = get_or_create_dashboard_chat_session(
            request.user,
            AssistantType.SUPER_ADMIN_ANALYTICS,
        )
        session = (
            ChatSession.objects.filter(id=session.id)
            .prefetch_related("chatmessage_set")
            .get()
        )
        serializer = DashboardChatSessionSerializer(session)
        return Response(serializer.data)


#: Upper bound on any caller-supplied list length on the dashboards, matching
#: StandardPageNumberPagination.max_page_size.
MAX_DASHBOARD_LIMIT = 100


def _bounded_int_param(request, name, default):
    """A positive integer query parameter, capped at MAX_DASHBOARD_LIMIT."""
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a whole number.") from None
    if value < 1:
        raise ValueError(f"{name} must be at least 1.")
    return min(value, MAX_DASHBOARD_LIMIT)


def _percentage_param(request, name, default):
    """A 0-100 percentage query parameter."""
    raw = request.query_params.get(name)
    if raw in (None, ""):
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number.") from None
    # `value != value` rejects NaN, which float() accepts and every comparison
    # against it silently fails.
    if value != value or not 0 <= value <= 100:
        raise ValueError(f"{name} must be between 0 and 100.")
    return value


def _expected_assignments_q():
    """Assignments a student can fairly be expected to have submitted by now.

    The same definition SchoolAdminWeeklySummaryService._at_risk_students
    uses, and the one StudentRiskEvaluator's own wording assumes ("no
    submitted work for assignments due so far"). Drafts were never shown to
    students, and published work that is not yet due is not missing.
    """
    return Q(status=AssignmentStatus.PUBLISHED) & (
        Q(due_date__isnull=True) | Q(due_date__lte=timezone.now())
    )


def _count_by(queryset, key):
    """{key value: row count}, as one grouped query."""
    return {
        row[key]: row["n"]
        for row in queryset.values(key).annotate(n=Count("id")).order_by()
    }


def _with_assignment_and_enrolment_counts(courses):
    """Annotate each course with its own assignment and enrolment counts.

    As correlated subqueries rather than Count() over joins: joining a
    course to both its assignments and its enrolments at once multiplies
    the rows (every assignment repeated per enrolment), so the join is
    O(assignments x enrolments) per course before anything is counted.
    """
    assignment_total = (
        Assignment.objects.filter(course=OuterRef("pk"))
        .values("course")
        .annotate(n=Count("id"))
        .values("n")
    )
    enrolment_total = (
        StudentCourse.objects.filter(course=OuterRef("pk"))
        .values("course")
        .annotate(n=Count("id"))
        .values("n")
    )
    return courses.annotate(
        assignment_total=Coalesce(
            Subquery(assignment_total, output_field=IntegerField()), Value(0)
        ),
        enrolment_total=Coalesce(
            Subquery(enrolment_total, output_field=IntegerField()), Value(0)
        ),
    )


def _expected_submission_total(courses):
    """Sum over courses of (assignments x enrolments), in ONE query.

    Replaces `sum(c.assignments.count() * c.enrollments.count() for c in
    courses)`, which ran two queries per course - measured +20 queries for
    ten more courses. The population is unchanged: every assignment and
    every enrolment on each course, exactly as before.
    """
    return (
        _with_assignment_and_enrolment_counts(courses).aggregate(
            total=Sum(F("assignment_total") * F("enrolment_total"))
        )["total"]
        or 0
    )


#: Shared OpenAPI doc for the optional `session_id` param every session-aware
#: school-admin dashboard endpoint below accepts.
SESSION_ID_PARAMETER = OpenApiParameter(
    name="session_id",
    type=OpenApiTypes.STR,
    location=OpenApiParameter.QUERY,
    required=False,
    description=(
        "Restrict the results to one of the school's sessions (terms). "
        "Omit to see the school's whole history across every session, "
        "same as before this parameter existed."
    ),
)


def _resolve_school_session(request, school):
    """Validate an optional `?session_id=` against the requesting school.

    Returns `(session_or_none, error_response_or_none)`. `session` is
    `None` when the parameter was omitted, meaning "no scoping - report
    across the school's whole history", which is the behaviour every one
    of these endpoints had before this parameter existed. A `session_id`
    that isn't a real session, isn't a SCHOOL-owned session, or belongs to
    a different school is rejected rather than silently ignored or
    silently returning another school's data.
    """
    session_id = request.query_params.get("session_id")
    if not session_id:
        return None, None

    try:
        session = Session.objects.get(
            id=session_id, owner_type=SessionOwnerType.SCHOOL, school=school
        )
    except (Session.DoesNotExist, ValueError, ValidationError):
        return None, Response(
            {"detail": "No session with that ID exists for this school."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return session, None


class SchoolAdminDashboardView(viewsets.ViewSet):
    permission_classes = [IsSchoolAdmin]

    AT_RISK_TREND_WINDOW_WEEKS = 8
    AT_RISK_TREND_MAX_WINDOW_WEEKS = 52

    @extend_schema(
        tags=["School Admin"],
        summary="School Dashboard Summary",
        description="""
        Retrieve high-level metrics for the school admin's dashboard.

        Metrics include:
        - School identification (Name).
        - User Depth: Counts of active teachers and students in the school.
        - Institutional Activity: Total active courses, assignments, and submissions.

        Pass `session_id` to scope everything except `teachers` and
        `at_risk_students` to one of the school's sessions instead of its
        whole history. `teachers` (active teacher count) and
        `at_risk_students` (a live risk state, not a per-term figure) are
        always school-wide.
        """,
        parameters=[SESSION_ID_PARAMETER],
        responses={200: SchoolAdminSummarySerializer},
    )
    @action(detail=False, methods=["get"], url_path="dashboard/summary")
    def summary(self, request, *args, **kwargs):
        user = request.user
        school = request.user.school

        if not school:
            return Response(
                {"detail": "School admin must be associated with a school."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session, error = _resolve_school_session(request, school)
        if error:
            return error

        cache_key = versioned_key(
            f"schooladmins:user_id__{user.id}:view__summary"
            f":session__{session.id if session else 'all'}",
            [(SCOPE_USER, user.id), (SCOPE_SCHOOL, user.school_id)],
        )
        data = cache.get(cache_key)

        if data is None:

            school = user.school

            if not school:
                return Response(
                    {"detail": "User is not associated with any school"},
                )

            active_teachers_qs = CustomUser.objects.filter(
                school=school,
                user_type=UserTypes.TEACHER,
                is_active=True,
            )
            active_students_qs = CustomUser.objects.filter(
                enrollments__course__teacher__school=school,
                is_active=True,
                user_type=UserTypes.STUDENT,
            )
            if session is not None:
                # Scoping active_teachers to a session would mean "teachers
                # who taught in this session" - not what "active teachers"
                # means anywhere else in this endpoint, so it stays
                # school-wide; only the session's own activity is scoped.
                active_students_qs = active_students_qs.filter(
                    enrollments__course__session=session
                )
            active_teachers = active_teachers_qs.count()
            active_students = active_students_qs.distinct().count()

            courses = Course.objects.filter(teacher__school=school, is_active=True)
            if session is not None:
                courses = courses.filter(session=session)
            courses_count = courses.count()

            assignments = Assignment.objects.filter(course__teacher__school=school)
            if session is not None:
                assignments = assignments.filter(course__session=session)
            assignments_created = assignments.count()

            # Assignments graded: att least one submission with graded_at not null
            graded_assignments = (
                assignments.filter(submissions__graded_at__isnull=False)
                .distinct()
                .count()
            )
            assignments_graded_percentage = (
                (graded_assignments / assignments_created) * 100
                if assignments_created > 0
                else 0
            )

            avg_courses_per_teacher = round(
                (courses_count / active_teachers) if active_teachers > 0 else 0, 1
            )

            avg_class_size = round(
                (active_students / courses_count) if courses_count > 0 else 0, 1
            )

            assignments_per_course = round(
                (assignments_created / courses_count) if courses_count > 0 else 0, 1
            )

            # Submissions for this school's assignments
            submissions = StudentSubmission.objects.filter(
                assignment__course__teacher__school=school
            )
            if session is not None:
                submissions = submissions.filter(assignment__course__session=session)
            graded_submissions = submissions.filter(graded_at__isnull=False)
            total_graded_submissions = graded_submissions.count()

            # Average turnaround (days) for graded submissions
            # We compute average difference in days between submission_date and graded_at
            avg_turnaround = graded_submissions.aggregate(
                avg_days=Avg(
                    ExpressionWrapper(
                        F("graded_at") - F("submission_date"),
                        output_field=DurationField(),
                    )
                )
            )["avg_days"]

            avg_turnaround_days = (
                round(avg_turnaround.total_seconds() / 86400, 1)
                if avg_turnaround
                else None
            )

            # AI Extraction Confidence: average over assignments (or submissions). We'll average over assignments
            avg_extraction_confidence = (
                assignments.aggregate(avg_conf=Avg("extraction_confidence"))["avg_conf"]
                or 0.0
            )
            avg_extraction_confidence = round(avg_extraction_confidence, 1)

            avg_grading_confidence = (
                graded_submissions.aggregate(avg_conf=Avg("grading_confidence"))[
                    "avg_conf"
                ]
                or 0.0
            )
            avg_grading_confidence = round(avg_grading_confidence, 1)

            # Flagged for review: graded submissions below the canonical
            # AI_CONFIDENCE_THRESHOLD (80) - the same line the teacher overview,
            # course analytics and super-admin AI performance dashboards use.
            # This was a bare 70, so the school dashboard flagged fewer
            # submissions than every other view of the same data. Product
            # decision (§8 review): use the canonical threshold.
            flagged_submissions = graded_submissions.filter(
                grading_confidence__lt=AI_CONFIDENCE_THRESHOLD
            )

            flagged_count = flagged_submissions.count()
            flagged_percentage = round(
                (
                    (flagged_count / total_graded_submissions * 100)
                    if total_graded_submissions > 0
                    else 0.0
                ),
                1,
            )

            # At-risk student count: delegates to SchoolAdminWeeklySummaryService
            # (dashboard/services.py) so this endpoint, the weekly digest, and
            # the daily at-risk alert task all agree on one definition
            # (dashboard/risk.py) instead of maintaining separate, drifting
            # copies of the same query. It's a live risk state, not a
            # per-term figure, so it stays school-wide regardless of
            # `session_id` - same call as before this parameter existed.
            at_risk_students = (
                SchoolAdminWeeklySummaryService()._build_at_risk_students(school)[1]
            )

            # Student growth rate: % change in distinct enrolled students,
            # comparing courses created in the last 180 days ("current") to
            # courses created before that ("past"), school-wide. Same window
            # and formula as TeacherPerformanceStatsService's per-teacher
            # "growth" (dashboard/services.py), just aggregated across the
            # whole school instead of per-teacher.
            six_months_ago = timezone.now() - timedelta(days=180)

            current_student_enrollments = StudentCourse.objects.filter(
                course__teacher__school=school,
                course__created_at__gte=six_months_ago,
            ).exclude(enrollment_status=EnrollmentStatusType.WITHDRAWN)
            past_student_enrollments = StudentCourse.objects.filter(
                course__teacher__school=school,
                course__created_at__lt=six_months_ago,
            ).exclude(enrollment_status=EnrollmentStatusType.WITHDRAWN)
            if session is not None:
                current_student_enrollments = current_student_enrollments.filter(
                    course__session=session
                )
                past_student_enrollments = past_student_enrollments.filter(
                    course__session=session
                )
            current_growth_students = (
                current_student_enrollments.values("student").distinct().count()
            )
            past_growth_students = (
                past_student_enrollments.values("student").distinct().count()
            )

            if past_growth_students > 0:
                student_growth_rate = round(
                    (
                        (current_growth_students - past_growth_students)
                        / past_growth_students
                    )
                    * 100,
                    1,
                )
            elif current_growth_students > 0:
                student_growth_rate = 100.0  # started from zero
            else:
                student_growth_rate = None

            data = {
                "school_name": school.name,
                "teachers": active_teachers,
                "students": active_students,
                "assignments_created": assignments_created,
                "assignments_graded": graded_assignments,
                "assignments_graded_percentage": round(
                    assignments_graded_percentage, 1
                ),
                "avg_turnaround_days": avg_turnaround_days,
                "ai_extraction_confidence": avg_extraction_confidence,
                "ai_grading_confidence": avg_grading_confidence,
                "flagged_for_review_count": flagged_count,
                "flagged_for_review_percentage": flagged_percentage,
                "at_risk_students": at_risk_students,
                "avg_courses_per_teacher": avg_courses_per_teacher,
                "avg_class_size": avg_class_size,
                "avg_assignments_per_course": assignments_per_course,
                "student_growth_rate": student_growth_rate,
            }

            serializer = SchoolAdminSummarySerializer(data)
            data = serializer.data

            cache.set(cache_key, data, 60 * 15)
        return Response(data)

    @extend_schema(
        tags=["School Admin"],
        summary="At-Risk Student Trend",
        description="""
        Weekly at-risk student count for the school admin's school, bucketed
        into calendar (Mon-Sun) weeks.

        By default returns a rolling 8-week (~2 month) window ending with
        the current (partial) calendar week. Pass `start_date` and/or
        `end_date` (YYYY-MM-DD) to request a different window instead -
        each is snapped outward to the Monday/Sunday of its containing
        calendar week. Omitting one of the two falls back to the default
        relative to whichever was provided (or to today).

        Each week's value is the most recent daily snapshot recorded for
        that week (SchoolAtRiskSnapshot, written once per day by the
        at-risk alert task). Weeks with no snapshot yet are omitted
        entirely rather than filled with 0 or null.
        """,
        parameters=[
            OpenApiParameter(
                name="start_date",
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Start of the window (YYYY-MM-DD). Defaults to "
                    f"{AT_RISK_TREND_WINDOW_WEEKS} weeks before end_date."
                ),
            ),
            OpenApiParameter(
                name="end_date",
                type=OpenApiTypes.DATE,
                location=OpenApiParameter.QUERY,
                required=False,
                description="End of the window (YYYY-MM-DD). Defaults to today.",
            ),
        ],
        responses={
            200: SchoolAtRiskTrendSerializer,
            400: OpenApiResponse(description="Invalid window"),
        },
    )
    @action(detail=False, methods=["get"], url_path="dashboard/at-risk-trend")
    def at_risk_trend(self, request, *args, **kwargs):
        user = request.user
        school = user.school

        if not school:
            return Response(
                {"detail": "School admin must be associated with a school."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        start_param = request.query_params.get("start_date")
        end_param = request.query_params.get("end_date")

        try:
            end_date = date.fromisoformat(end_param) if end_param else None
        except ValueError:
            return Response(
                {"detail": "end_date must be in YYYY-MM-DD format."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            start_date = date.fromisoformat(start_param) if start_param else None
        except ValueError:
            return Response(
                {"detail": "start_date must be in YYYY-MM-DD format."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        end_date = end_date or timezone.localdate()
        window_end_week_start = end_date - timedelta(days=end_date.weekday())
        window_end = window_end_week_start + timedelta(days=6)

        if start_date:
            window_start = start_date - timedelta(days=start_date.weekday())
        else:
            window_start = window_end_week_start - timedelta(
                weeks=self.AT_RISK_TREND_WINDOW_WEEKS - 1
            )

        if window_start > window_end:
            return Response(
                {"detail": "start_date must not be after end_date."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        total_weeks = ((window_end - window_start).days + 1) // 7
        if total_weeks > self.AT_RISK_TREND_MAX_WINDOW_WEEKS:
            return Response(
                {
                    "detail": (
                        "Window too large: max "
                        f"{self.AT_RISK_TREND_MAX_WINDOW_WEEKS} weeks between "
                        "start_date and end_date."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        cache_key = versioned_key(
            f"schooladmins:user_id__{user.id}:view__at_risk_trend"
            f":{window_start.isoformat()}:{window_end.isoformat()}",
            [(SCOPE_USER, user.id), (SCOPE_SCHOOL, user.school_id)],
        )
        data = cache.get(cache_key)

        if data is None:
            snapshots = SchoolAtRiskSnapshot.objects.filter(
                school=school,
                snapshot_date__gte=window_start,
                snapshot_date__lte=window_end,
            ).order_by("snapshot_date")

            weeks = []
            for week_index in range(total_weeks):
                week_start = window_start + timedelta(weeks=week_index)
                week_end = week_start + timedelta(days=6)
                week_snapshots = [
                    snapshot
                    for snapshot in snapshots
                    if week_start <= snapshot.snapshot_date <= week_end
                ]
                if not week_snapshots:
                    continue

                latest_snapshot = week_snapshots[-1]
                weeks.append(
                    {
                        "week_start": week_start,
                        "week_end": week_end,
                        "at_risk_count": latest_snapshot.at_risk_count,
                    }
                )

            data = {
                "school_name": school.name,
                "window_start": window_start,
                "window_end": window_end,
                "weeks": weeks,
            }
            serializer = SchoolAtRiskTrendSerializer(data)
            data = serializer.data

            cache.set(cache_key, data, 60 * 60)
        return Response(data)

    @extend_schema(
        tags=["School Admin"],
        operation_id="teacherPerformanceDashboard",
        summary="Teacher Performance Dashboard",
        description="""
    Returns performance metrics for every teacher belonging to the authenticated
    School Admin's school.

    - **assignments_per_week** — Average number of assignments created per week
    since the teacher created their first assignment.

    - **turnaround** — Average grading turnaround time in **days** from
    student submission until grading.

    - **ai_confidence** — Average AI grading confidence score across all graded
    submissions.

    - **rigor** — Blended academic rigor on a **0–5 scale**. Same value as
    `rigor_breakdown.score`; kept as a flat field for convenience.

    - **rigor_breakdown** — The components behind that number, because the
    blend alone is not actionable:
        - `demand` — Cognitive demand, 0–5, from each question's Bloom's
        taxonomy level (Remember=0 … Create=5), weighted by question points.
        This is the definitional core of rigor: what level of thinking the
        work asks for.
        - `evidence` — Difficulty implied by results, `5 * (1 - avg score
        percentage / 100)`. Null until the teacher has at least 5 graded
        submissions, since it is a sample statistic.
        - `standards` — Share of open-ended questions carrying a rubric of
        3+ levels, scaled 0–5. Null when the teacher sets no open-ended
        questions (a multiple-choice quiz is not failing at rubric design).
        - `coverage` — Fraction of the teacher's non-draft assignments that
        carried usable Bloom's data. Low coverage means the score rests on a
        minority of their work.
        - `assignments_scored`, `submissions_scored` — Sample sizes behind
        `demand` and `evidence`.

    Weights are 0.6 demand / 0.25 evidence / 0.15 standards, renormalized
    over whichever components are present. `demand` is required: without it
    `rigor` is null, rather than silently reporting an outcome-only proxy
    under the same label. Draft assignments are excluded throughout.

    - **status**
        - `true` → Teacher account is active.
        - `false` → Teacher account is inactive.

    ### Notes

    - Only assignments that have been graded contribute to:
        - turnaround
        - ai_confidence

    - Metrics that cannot yet be calculated are returned as `null`.

    - Responses are cached for 5 minutes.
    """,
        parameters=[
            OpenApiParameter(
                "page",
                int,
                location=OpenApiParameter.QUERY,
                description="Page number to retrieve.",
            ),
            OpenApiParameter(
                "page_size",
                int,
                location=OpenApiParameter.QUERY,
                description="Number of results per page (max 100).",
            ),
            SESSION_ID_PARAMETER,
        ],
        responses={
            200: OpenApiResponse(
                response=TeacherPerformanceDashboardSerializer(many=True),
                description="Teacher performance metrics retrieved successfully.",
            ),
            400: OpenApiResponse(
                description="Authenticated School Admin is not associated with a school.",
                examples=[
                    OpenApiExample(
                        "No School",
                        value={
                            "detail": "School admin must be associated with a school."
                        },
                    )
                ],
            ),
            401: OpenApiResponse(
                description="Authentication credentials were not provided."
            ),
            403: OpenApiResponse(
                description="You do not have permission to access this resource."
            ),
        },
    )
    @action(detail=False, methods=["get"], url_path="dashboard/teachers")
    def teacher_performance(self, request, *args, **kwargs):
        school = request.user.school
        if not school:
            return Response(
                {"detail": "School admin must be associated with a school."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session, error = _resolve_school_session(request, school)
        if error:
            return error

        paginator = StandardPageNumberPagination()
        page_number = request.query_params.get(paginator.page_query_param, "1")
        page_size = request.query_params.get(paginator.page_size_query_param, "")
        cache_key = versioned_key(
            f"dashboards:school_id__{school.id}"
            f":view__teacher_performance:{page_number}:{page_size}"
            f":session__{session.id if session else 'all'}",
            [(SCOPE_SCHOOL, school.id)],
        )
        data = cache.get(cache_key)

        if data is not None:
            return Response(data)

        # Explicit order: an unordered queryset pages non-deterministically.
        teachers = paginator.paginate_queryset(
            CustomUser.objects.filter(
                school=school, user_type=UserTypes.TEACHER
            ).order_by("first_name", "last_name", "id"),
            request,
            view=self,
        )

        # A fixed number of queries for the whole page - see
        # TeacherPerformanceStatsService. Previously ~8 per teacher.
        stats = TeacherPerformanceStatsService().build(teachers, session=session)
        result = [stats[teacher.id] for teacher in teachers]

        serializer = TeacherPerformanceDashboardSerializer(result, many=True)
        response = paginator.get_paginated_response(serializer.data)
        data = response.data

        cache.set(cache_key, data, 300)  # 5 minutes
        return Response(data)

    @extend_schema(
        tags=["School Admin"],
        operation_id="teacherDetailDashboard",
        summary="Teacher Detail",
        description="""
    Retrieve the full detail view for a single teacher in the authenticated
    School Admin's school: everything `dashboard/teachers` returns for that
    teacher, plus credit usage.

    - **credits_used** — All-time credits consumed by this teacher, net of
    refunds.
    - **credits_used_percentage** — Percentage of the CURRENT plan
    allocation consumed (live non-overage buckets' used credits over
    used + remaining). Excludes OVERAGE buckets
    (purchased reactively after the plan is exhausted, not part of the
    fixed allocation), so buying overage doesn't make this percentage
    swing around.
    - **days_active** — Distinct calendar days with credit usage in the
    last 60 days.
    - **daily_usage** — Credits consumed per day for the last 60 days,
    zero-filled for days with no usage.
    - **grading / creation / feedback / other** — Feature-mix breakdown of
    `credits_used`, each with a raw `amount` and a `percent` of the total.
    "other" covers any AI feature not mapped to one of the first three
    (e.g. custom AI chat, weekly summaries).
    - **credits_remaining** — Live (unexpired) credits left, by source:
    `monthly` (current plan allocation), `carry_over` (rolled over from a
    prior cycle), `overage` (purchased overage blocks plus any manual
    grants), and `total` (the three summed). Excludes TRIAL, which a
    school-license teacher never has.

    Pass `session_id` to scope the performance/rigor figures (courses,
    students, assignments, turnaround, rigor) to one of the school's
    sessions instead of the teacher's whole history. Credit figures
    (`credits_used`, `credits_used_percentage`, `daily_usage`,
    `credits_remaining`, feature mix) are wallet-based, not tied to any
    one session, and are unaffected by this parameter.
        """,
        parameters=[SESSION_ID_PARAMETER],
        responses={
            200: OpenApiResponse(
                response=TeacherDetailSerializer,
                description="Teacher detail retrieved successfully.",
            ),
            400: OpenApiResponse(
                description="Authenticated School Admin is not associated with a school.",
                examples=[
                    OpenApiExample(
                        "No School",
                        value={
                            "detail": "School admin must be associated with a school."
                        },
                    )
                ],
            ),
            401: OpenApiResponse(
                description="Authentication credentials were not provided."
            ),
            403: OpenApiResponse(
                description="You do not have permission to access this resource."
            ),
            404: OpenApiResponse(
                description="No teacher with that ID exists in this school."
            ),
        },
    )
    @action(
        detail=False,
        methods=["get"],
        url_path=r"dashboard/teachers/(?P<teacher_id>[^/.]+)",
    )
    def teacher_detail(self, request, teacher_id=None, *args, **kwargs):
        school = request.user.school
        if not school:
            return Response(
                {"detail": "School admin must be associated with a school."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session, error = _resolve_school_session(request, school)
        if error:
            return error

        # Scoped by school on the lookup itself, so a school admin can't
        # pull another school's teacher by guessing a UUID.
        teacher = get_object_or_404(
            CustomUser, id=teacher_id, school=school, user_type=UserTypes.TEACHER
        )

        cache_key = versioned_key(
            f"dashboards:school_id__{school.id}"
            f":view__teacher_detail:{teacher.id}"
            f":session__{session.id if session else 'all'}",
            [(SCOPE_SCHOOL, school.id), (SCOPE_USER, teacher.id)],
        )
        data = cache.get(cache_key)
        if data is not None:
            return Response(data)

        now = timezone.now()
        result = TeacherPerformanceStatsService().build(
            [teacher], now=now, session=session
        )[teacher.id]

        # --- Feature mix, live from CreditUsageLog, net of refunds ---
        category_by_field = {
            "credits_used_grading": "grading",
            "credits_used_creation": "creation",
            "credits_used_feedback": "feedback",
        }
        feature_totals = (
            CreditUsageLog.objects.filter(wallet__user=teacher, is_refunded=False)
            .values("feature")
            .annotate(total=Sum("amount"))
        )
        category_totals = {"grading": 0, "creation": 0, "feedback": 0, "other": 0}
        for row in feature_totals:
            analytics_field = FEATURE_TO_ANALYTICS_FIELD.get(row["feature"])
            category = category_by_field.get(analytics_field or "", "other")
            category_totals[category] += row["total"] or 0

        credits_used = sum(category_totals.values())
        divisor = credits_used or 1
        for category, amount in category_totals.items():
            result[category] = {
                "amount": amount // CONVERSION_FACTOR,
                "percent": round((amount / divisor) * 100, 1),
            }

        # --- Credits used + % of plan consumed (excludes OVERAGE) ---
        # The percentage numerator is CURRENT-plan usage (live buckets'
        # used_credits), not the all-time `credits_used` shown above —
        # mixing all-time usage with a current-balance denominator made
        # this creep toward 100% forever regardless of the current cycle.
        # credit_wallet is a reverse OneToOne with no auto-creation — a
        # teacher who has never touched a credit-consuming feature has no
        # wallet row, and the reverse accessor raises
        # CreditWallet.DoesNotExist (not AttributeError, so getattr's
        # default can't catch it). Treat "no wallet" as zero usage instead
        # of a 500.
        try:
            wallet = teacher.credit_wallet
        except ObjectDoesNotExist:
            wallet = None
        remaining = wallet.plan_remaining_credits() if wallet else 0
        current_used = wallet.plan_used_credits() if wallet else 0
        denominator = current_used + remaining
        result["credits_used"] = credits_used // CONVERSION_FACTOR
        result["credits_used_percentage"] = (
            round((current_used / denominator) * 100, 1) if denominator else 0.0
        )

        # --- Remaining credits by source ---
        # TRIAL is deliberately excluded, not just zeroed: a teacher added
        # via a school license never gets one (see the license-invitation
        # guard in users/signals.py), so there is nothing to fold in for
        # the audience this view is for. MANUAL_GRANT is folded into
        # "overage" — both are credits outside the fixed plan allocation,
        # the same distinction plan_remaining_credits() already draws.
        if wallet:
            live_bucket_totals = {
                row["bucket_type"]: row["remaining"] or 0
                for row in wallet.buckets.filter(
                    Q(expires_at__isnull=True) | Q(expires_at__gt=now)
                )
                .values("bucket_type")
                .annotate(remaining=Sum(F("total_credits") - F("used_credits")))
            }
        else:
            live_bucket_totals = {}
        credits_remaining_monthly = live_bucket_totals.get(CreditBucketType.MONTHLY, 0)
        credits_remaining_carry_over = live_bucket_totals.get(
            CreditBucketType.CARRY_OVER, 0
        )
        credits_remaining_overage = live_bucket_totals.get(
            CreditBucketType.OVERAGE, 0
        ) + live_bucket_totals.get(CreditBucketType.MANUAL_GRANT, 0)
        result["credits_remaining"] = {
            "monthly": credits_remaining_monthly // CONVERSION_FACTOR,
            "carry_over": credits_remaining_carry_over // CONVERSION_FACTOR,
            "overage": credits_remaining_overage // CONVERSION_FACTOR,
            "total": (
                credits_remaining_monthly
                + credits_remaining_carry_over
                + credits_remaining_overage
            )
            // CONVERSION_FACTOR,
        }

        # --- Days active + daily usage (last 60 days) ---
        window_start = now.date() - timedelta(days=60)
        raw_usage = (
            CreditUsageLog.objects.filter(
                wallet__user=teacher,
                is_refunded=False,
                created_at__date__gte=window_start,
            )
            .annotate(day=TruncDay("created_at"))
            .values("day")
            .annotate(total=Sum("amount"))
            .order_by("day")
        )
        usage_dict = {row["day"].date(): row["total"] for row in raw_usage}
        daily_usage = [
            {
                "date": window_start + timedelta(days=i),
                "credits": usage_dict.get(window_start + timedelta(days=i), 0)
                // CONVERSION_FACTOR,
            }
            for i in range(61)
        ]
        result["daily_usage"] = daily_usage
        result["days_active"] = sum(1 for v in usage_dict.values() if v)

        serializer = TeacherDetailSerializer(result)
        data = serializer.data

        cache.set(cache_key, data, 300)  # 5 minutes
        return Response(data)

    @extend_schema(
        tags=["School Admin"],
        operation_id="coursePerformanceDashboard",
        summary="Course Performance Table",
        description="""
    Retrieve a paginated list of courses within the authenticated School Admin's
    school, along with performance metrics for each course.

    Each result contains:

    - **teacher** — Full name of the course's teacher.
    - **students** — Enrollment breakdown by status:
        - `enrolled` — Actively enrolled students.
        - `completed` — Students who completed the course.
        - `pending` — Invited students who haven't finished registration yet.
        - `total` — `enrolled + completed + pending` (everyone except
          withdrawn students).
    - **assignments** — Number of **published** assignments in the course.
    Draft/unpublished assignments (never shown to students) are excluded.
    - **avg_grade** — Points-weighted average final grade
    (`sum(score) / sum(max_points) * 100`) across `enrolled`/`completed`
    students, on a 0-100 scale. `0` if none of them has a graded
    submission yet (never `null` — see "students" to tell "no data" from
    "graded at 0%"). Pending/withdrawn students never contribute.
    - **distribution** — Grade-letter breakdown (`A`/`B`/`C`/`D`/`F`) of
    `enrolled`/`completed` students' final grades.

    ### Notes

    - `distribution` only includes keys for letters with at least one student —
    a missing letter means zero students received that grade, not an error.
    - `ordering` accepts either the response field name (`students`,
    `assignments`) or the raw metric name (`student_count`, `assignment_count`)
    interchangeably, and always falls back to a stable `name` + `id` order so
    paging through results is deterministic.
    """,
        parameters=[
            OpenApiParameter(
                name="search",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Case-insensitive search against course name, "
                "teacher first name, and teacher last name.",
                required=False,
            ),
            OpenApiParameter(
                name="ordering",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description=(
                    "Sort key: one of `name`, `students`, `assignments`, "
                    "`avg_grade`. Prefix with '-' for descending, "
                    "e.g. `-avg_grade`. Defaults to the model's natural "
                    "ordering (by `name`) when omitted."
                ),
                required=False,
            ),
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number (1-indexed). Defaults to 1.",
                required=False,
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of results per page. Defaults to 10, max 100.",
                required=False,
            ),
            SESSION_ID_PARAMETER,
        ],
        responses={
            200: OpenApiResponse(
                response=CoursePerformanceDashboardPageSerializer,
                description="Course performance metrics retrieved successfully.",
                examples=[
                    OpenApiExample(
                        "Course performance page",
                        value={
                            "count": 1,
                            "next": None,
                            "previous": None,
                            "results": [
                                {
                                    "id": "b3b3b3b3-1111-4b3b-8b3b-3b3b3b3b3b3b",
                                    "name": "Algebra I - Period 2",
                                    "teacher": "Jane Doe",
                                    "students": {
                                        "enrolled": 26,
                                        "completed": 0,
                                        "pending": 2,
                                        "total": 28,
                                    },
                                    "assignments": 12,
                                    "avg_grade": 84.5,
                                    "distribution": {
                                        "A": 6,
                                        "B": 10,
                                        "C": 8,
                                        "D": 3,
                                        "F": 1,
                                    },
                                }
                            ],
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                description="Authenticated School Admin is not associated with a school.",
                examples=[
                    OpenApiExample(
                        "No School",
                        value={
                            "detail": "School admin must be associated with a school."
                        },
                    )
                ],
            ),
            401: OpenApiResponse(
                description="Authentication credentials were not provided."
            ),
            403: OpenApiResponse(
                description="You do not have permission to access this resource."
            ),
        },
    )
    @action(detail=False, methods=["get"], url_path="dashboard/course-performance")
    def course_peformance(self, request, *args, **kwargs):
        school = request.user.school
        if not school:
            return Response(
                {"detail": "School admin must be associated with a school."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session, error = _resolve_school_session(request, school)
        if error:
            return error

        # Set up pagination
        paginator = pagination.PageNumberPagination()
        # A fixed integer default. This used to be the raw query-string value,
        # and DRF falls back to `page_size` when `?page_size=` fails its own
        # validation - so `?page_size=abc` reached Django's Paginator as the
        # string "abc" and crashed the request with a 500. DRF still honours
        # a valid `?page_size=` below, capped at max_page_size.
        paginator.page_size = 10
        paginator.page_size_query_param = "page_size"
        paginator.max_page_size = 100

        # Base queryset: courses taught by teachers in this school
        qs = Course.objects.filter(teacher__school=school).select_related("teacher")
        if session is not None:
            qs = qs.filter(session=session)

        # A course's grade average/distribution should reflect students who
        # actually engaged with the course - not enrollments still pending
        # activation. "students.total" (below) counts every non-withdrawn
        # enrollment, pending included, purely as a roster count.
        active_enrollment_statuses = [
            EnrollmentStatusType.ENROLLED,
            EnrollmentStatusType.COMPLETED,
        ]

        # Annotations. Every Count() here uses distinct=True: this queryset
        # also joins to `assignments`, and without distinct=True each of
        # these would be silently multiplied by the course's assignment
        # count (a classic Django multi-relation JOIN fan-out).
        qs = qs.annotate(
            enrolled_count=Count(
                "enrollments",
                filter=Q(enrollments__enrollment_status=EnrollmentStatusType.ENROLLED),
                distinct=True,
            ),
            completed_count=Count(
                "enrollments",
                filter=Q(enrollments__enrollment_status=EnrollmentStatusType.COMPLETED),
                distinct=True,
            ),
            pending_count=Count(
                "enrollments",
                filter=Q(enrollments__enrollment_status=EnrollmentStatusType.PENDING),
                distinct=True,
            ),
            student_count=Count(
                "enrollments",
                filter=~Q(
                    enrollments__enrollment_status=EnrollmentStatusType.WITHDRAWN
                ),
                distinct=True,
            ),
            assignment_count=Count(
                "assignments",
                filter=Q(assignments__status=AssignmentStatus.PUBLISHED),
                distinct=True,
            ),
            avg_grade=Coalesce(
                Avg(
                    "enrollments__final_grade",
                    filter=Q(
                        enrollments__enrollment_status__in=active_enrollment_statuses
                    ),
                ),
                Value(0.0),
                output_field=FloatField(),
            ),
        )

        # Grade distribution (active enrollments only - see comment above)
        grade_conditions = {
            "A": Q(enrollments__final_grade__gte=90, enrollments__final_grade__lte=100),
            "B": Q(enrollments__final_grade__gte=80, enrollments__final_grade__lt=90),
            "C": Q(enrollments__final_grade__gte=70, enrollments__final_grade__lt=80),
            "D": Q(enrollments__final_grade__gte=65, enrollments__final_grade__lt=70),
            "F": Q(enrollments__final_grade__lt=65),
        }

        for grade, condition in grade_conditions.items():
            qs = qs.annotate(
                **{
                    f"grade_{grade}": Count(
                        "enrollments",
                        filter=condition
                        & Q(
                            enrollments__enrollment_status__in=active_enrollment_statuses
                        ),
                        distinct=True,
                    )
                }
            )

        # Apply filter backends manually because we're in a ViewSet action
        search_backend = filters.SearchFilter()
        search_backend.search_fields = [
            "name",
            "teacher__first_name",
            "teacher__last_name",
        ]
        qs = search_backend.filter_queryset(request, qs, view=self)

        # Callers may order by either the response field names ("students",
        # "assignments") or the underlying annotated metric names.
        allowed_ordering_fields = {
            "name",
            "avg_grade",
            "student_count",
            "assignment_count",
        }
        ordering_aliases = {
            "students": "student_count",
            "assignments": "assignment_count",
        }
        raw_ordering = request.query_params.get("ordering")
        ordering = []
        seen_fields = set()
        if raw_ordering:
            for term in raw_ordering.split(","):
                term = term.strip()
                if not term:
                    continue
                prefix, field = ("-", term[1:]) if term.startswith("-") else ("", term)
                field = ordering_aliases.get(field, field)
                if field in allowed_ordering_fields and field not in seen_fields:
                    ordering.append(f"{prefix}{field}")
                    seen_fields.add(field)

        # Annotate/aggregate queries drop Course's default Meta.ordering,
        # leaving the queryset unordered - Django then paginates with a
        # non-deterministic row order, so the same course can appear on
        # two pages or be skipped entirely. Always fall back to `name`,
        # and always append `id` as a tiebreaker so paging is stable even
        # when ordering by a non-unique column like `avg_grade`.
        if not ordering:
            ordering = ["name"]
        if "id" not in seen_fields:
            ordering.append("id")
        qs = qs.order_by(*ordering)

        # Paginate
        page = paginator.paginate_queryset(qs, request, view=self)
        if page is not None:
            serializer = CoursePerformanceDashboardSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = CoursePerformanceDashboardSerializer(qs, many=True)
        return Response(serializer.data)

    @extend_schema(
        tags=["School Admin"],
        summary="Assignment Performance (Hardest & Reteach Recommended)",
        description="""
        Returns two lists of assignments with performance metrics.

        **Hardest Units**: Assignments with the lowest mastery rates (sorted ascending).
        **Reteach Recommended**: Assignments where mastery is below a threshold (default 75%).

        Mastery is defined as the percentage of students who scored >= 70% on the assignment.
        Both lists include assignment title, course name, mastery percentage, and average score.
        """,
        parameters=[
            OpenApiParameter(
                name="hardest_limit",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of hardest units to return (default: 5)",
                required=False,
            ),
            OpenApiParameter(
                name="reteach_limit",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of reteach-recommended units to return (default: 5)",
                required=False,
            ),
            OpenApiParameter(
                name="mastery_threshold",
                type=OpenApiTypes.NUMBER,
                location=OpenApiParameter.QUERY,
                description="Passing score percentage threshold (default: 70.0)",
                required=False,
            ),
            OpenApiParameter(
                name="reteach_threshold",
                type=OpenApiTypes.NUMBER,
                location=OpenApiParameter.QUERY,
                description="Mastery threshold below which reteach is recommended (default: 75.0)",
                required=False,
            ),
            SESSION_ID_PARAMETER,
        ],
        responses={200: UnitPerformanceSerializer},
    )
    @action(detail=False, methods=["get"], url_path="dashboard/unit-performance")
    def unit_performance(self, request, *args, **kwargs):
        school = request.user.school
        if not school:
            return Response(
                {"detail": "School admin must be associated with a school."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session, error = _resolve_school_session(request, school)
        if error:
            return error

        # Parse query parameters. Validated rather than cast blindly: a bare
        # int()/float() turned `?hardest_limit=abc` into a 500, a negative
        # limit into Django's "Negative indexing is not supported" 500, and
        # left the limits unbounded. Bad input is the caller's error, so it
        # is a 400 naming the parameter.
        try:
            hardest_limit = _bounded_int_param(request, "hardest_limit", 5)
            reteach_limit = _bounded_int_param(request, "reteach_limit", 5)
            mastery_threshold = _percentage_param(request, "mastery_threshold", 70.0)
            reteach_threshold = _percentage_param(request, "reteach_threshold", 75.0)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        # Base queryset: assignments belonging to courses in this school
        assignments = Assignment.objects.filter(course__teacher__school=school)
        if session is not None:
            assignments = assignments.filter(course__session=session)

        # Annotate performance metrics
        assignments = assignments.annotate(
            total_submissions=Count(
                "submissions",
                filter=Q(
                    submissions__graded_at__isnull=False, submissions__is_published=True
                ),
            ),
            avg_score=Avg(
                "submissions__score_percentage",
                filter=Q(
                    submissions__graded_at__isnull=False, submissions__is_published=True
                ),
            ),
            passing_submissions=Count(
                "submissions",
                filter=Q(
                    submissions__score_percentage__gte=mastery_threshold,
                    submissions__graded_at__isnull=False,
                    submissions__is_published=True,
                ),
            ),
        )

        # Compute mastery percentage (handle division by zero)
        assignments = assignments.annotate(
            mastery=ExpressionWrapper(
                Coalesce(
                    F("passing_submissions") * 100.0 / F("total_submissions"),
                    Value(0.0),
                ),
                output_field=FloatField(),
            )
        )

        # Filter to assignments with at least one graded submission
        assignments = assignments.filter(total_submissions__gt=0)

        # ---- Hardest Units ----
        hardest_qs = assignments.order_by("mastery")[:hardest_limit]

        # ---- Reteach Recommended ----
        reteach_qs = assignments.filter(mastery__lt=reteach_threshold).order_by(
            "mastery"
        )[:reteach_limit]

        # Serialize
        hardest_serializer = UnitPerformanceSerializer(hardest_qs, many=True)
        reteach_serializer = UnitPerformanceSerializer(reteach_qs, many=True)

        return Response(
            {
                "hardest_units": hardest_serializer.data,
                "reteach_recommended": reteach_serializer.data,
            }
        )

    @extend_schema(
        tags=["School Admin"],
        summary="School Student Performance",
        description="""
        Retrieve student performance and engagement metrics for the school.

        Metrics include:
        - Average Grade: The mean final grade across all active student enrollments in the school.
        - Global Completion Rate: Actual submissions vs. expected submissions based on school's
          active courses and enrollments.
        - Grade Distribution: School-wide count of students in each grade tier (A, B, C, D, F).
        - Active Enrollments: Total count of active student-course pairings in the school.
        """,
        parameters=[SESSION_ID_PARAMETER],
        responses={200: SchoolAdminStudentPerformanceSerializer},
    )
    @action(detail=False, methods=["get"], url_path="dashboard/students")
    def students(self, request, *args, **kwargs):
        user = request.user
        school = user.school

        if not school:
            return Response(
                {"detail": "User is not associated with any school"},
                status=400,
            )

        session, error = _resolve_school_session(request, school)
        if error:
            return error

        cache_key = versioned_key(
            f"schooladmins:user_id__{user.id}:view__students"
            f":session__{session.id if session else 'all'}",
            [(SCOPE_USER, user.id), (SCOPE_SCHOOL, user.school_id)],
        )
        data = cache.get(cache_key)

        if data is None:
            school_student_courses = StudentCourse.objects.filter(
                course__teacher__school=school
            )
            if session is not None:
                school_student_courses = school_student_courses.filter(
                    course__session=session
                )

            stats = school_student_courses.aggregate(
                avg_grade=Avg("final_grade"),
                total_enrollments=Count("id"),
                a=Count(Case(When(final_grade__gte=90, then=Value(1)))),
                b=Count(
                    Case(When(final_grade__gte=80, final_grade__lt=90, then=Value(1)))
                ),
                c=Count(
                    Case(When(final_grade__gte=70, final_grade__lt=80, then=Value(1)))
                ),
                d=Count(
                    Case(When(final_grade__gte=65, final_grade__lt=70, then=Value(1)))
                ),
                f=Count(Case(When(final_grade__lt=65, then=Value(1)))),
            )

            total_graded = sum(
                [stats["a"], stats["b"], stats["c"], stats["d"], stats["f"]]
            )

            def get_entry(count):
                return {
                    "count": count,
                    "percentage": (
                        round((count / total_graded) * 100, 2)
                        if total_graded > 0
                        else 0
                    ),
                }

            actual_submissions_qs = StudentSubmission.objects.filter(
                assignment__course__teacher__school=school
            )
            expected_submissions_courses = Course.objects.filter(
                teacher__school=school, is_active=True
            )
            if session is not None:
                actual_submissions_qs = actual_submissions_qs.filter(
                    assignment__course__session=session
                )
                expected_submissions_courses = expected_submissions_courses.filter(
                    session=session
                )
            actual_submissions = actual_submissions_qs.count()
            expected_submissions = _expected_submission_total(
                expected_submissions_courses
            )

            completion_rate = (
                (actual_submissions / expected_submissions * 100)
                if expected_submissions > 0
                else 0
            )

            data = {
                "school_name": school.name,
                "average_grade": round(float(stats["avg_grade"] or 0), 2),
                "assignment_completion_rate": round(
                    min(float(completion_rate), 100), 2
                ),
                "grade_distribution": {
                    "A": get_entry(stats["a"]),
                    "B": get_entry(stats["b"]),
                    "C": get_entry(stats["c"]),
                    "D": get_entry(stats["d"]),
                    "E": get_entry(0),
                    "F": get_entry(stats["f"]),
                },
                "total_active_enrollments": stats["total_enrollments"],
            }

            serializer = SchoolAdminStudentPerformanceSerializer(data)
            data = serializer.data

            cache.set(cache_key, data, 60 * 15)
        return Response(data)

    @extend_schema(
        tags=["School Admin Charts"],
        operation_id="assignment_activity_over_time_chart",
        summary="Assignment Activity Over Time",
        description=(
            "Returns monthly assignment activity for the authenticated school. "
            "The response contains two datasets:\n\n"
            "- **created**: Number of assignments created each month.\n"
            "- **graded**: Number of unique assignments that received at least one graded submission each month.\n\n"
            "If the optional **year** query parameter is supplied, only activity for "
            "that calendar year is returned. Otherwise, activity across all years is aggregated."
        ),
        parameters=[
            OpenApiParameter(
                name="year",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Optional calendar year used to filter assignment activity "
                    "(e.g. 2026). If omitted, activity from all years is aggregated."
                ),
            ),
            SESSION_ID_PARAMETER,
        ],
        responses={
            200: OpenApiResponse(
                response=AssignmentActivityOverTimeChartSerializer,
                description="Assignment activity chart retrieved successfully.",
            ),
            400: OpenApiResponse(
                description="The authenticated school administrator is not associated with a school.",
            ),
            401: OpenApiResponse(
                description="Authentication credentials were not provided or are invalid.",
            ),
            403: OpenApiResponse(
                description="You do not have permission to access this resource.",
            ),
        },
    )
    @action(
        detail=False,
        methods=["get"],
        url_path="dashboard/assignment-activity-over-time",
    )
    def assignment_activity_over_time_chart(self, request, *args, **kwargs):
        school = request.user.school

        if not school:
            return Response(
                {"detail": "School admin must be associated with a school."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session, error = _resolve_school_session(request, school)
        if error:
            return error

        year = request.query_params.get("year")

        # Cache per school, optional year, and optional session
        cache_key = versioned_key(
            f"dashboards:school_id__{school.id}"
            f":view__assignment_activity:{year or 'all'}"
            f":session__{session.id if session else 'all'}",
            [(SCOPE_SCHOOL, school.id)],
        )
        data = cache.get(cache_key)

        if data is None:
            # Base queryset for assignments belonging to the school
            base_qs = Assignment.objects.filter(course__teacher__school=school)
            if session is not None:
                base_qs = base_qs.filter(course__session=session)

            # CREATED ASSIGNMENTS PER MONTH

            created_qs = base_qs
            if year:
                created_qs = created_qs.filter(created_at__year=year)

            created_by_month = (
                created_qs.annotate(month=ExtractMonth("created_at"))
                .values("month")
                .annotate(count=Count("id"))
                .order_by("month")
            )

            created_dict = {item["month"]: item["count"] for item in created_by_month}

            # GRADED ASSIGNMENTS PER MONTH

            # An assignment is considered "graded" in a month if it has at least one
            # submission with a non-null `graded_at` in that month.
            graded_qs = base_qs.filter(submissions__graded_at__isnull=False)
            if year:
                graded_qs = graded_qs.filter(submissions__graded_at__year=year)

            graded_by_month = (
                graded_qs.annotate(month=ExtractMonth("submissions__graded_at"))
                .values("month")
                .annotate(
                    count=Count("id", distinct=True)
                )  # count distinct assignments
                .order_by("month")
            )
            graded_dict = {item["month"]: item["count"] for item in graded_by_month}

            # Build the full month list (1–12)
            months = list(range(1, 13))
            labels = [
                "Jan",
                "Feb",
                "Mar",
                "Apr",
                "May",
                "Jun",
                "Jul",
                "Aug",
                "Sep",
                "Oct",
                "Nov",
                "Dec",
            ]
            created_data = [created_dict.get(m, 0) for m in months]
            graded_data = [graded_dict.get(m, 0) for m in months]

            data = {
                "labels": labels,
                "created": created_data,
                "graded": graded_data,
            }

            cache.set(cache_key, data, 60 * 15)

        return Response(data)

    @extend_schema(
        tags=["School Admin Charts"],
        operation_id="course_overview_chart",
        summary="Course Overview Chart",
        description=(
            "Returns an overview of all courses within the school. "
            "For each course, the response includes the number of teachers "
            "assigned to the course and the average final grade of enrolled "
            "students (excluding withdrawn enrollments). "
            "This endpoint is intended for dashboard chart visualizations."
        ),
        responses={
            200: OpenApiResponse(
                response=CourseOverviewChartSerializer,
                description="Course overview data retrieved successfully.",
            ),
            400: OpenApiResponse(
                description="The authenticated school administrator is not associated with a school."
            ),
            401: OpenApiResponse(
                description="Authentication credentials were not provided or are invalid."
            ),
            403: OpenApiResponse(
                description="You do not have permission to access this resource."
            ),
        },
        parameters=[SESSION_ID_PARAMETER],
    )
    @action(detail=False, methods=["GET"], url_path="dashboard/course-overview-chart")
    def course_overview_chart(self, request, *args, **kwargs):
        school = request.user.school

        if not school:
            return Response(
                {"detail": "School admin must be associated with a school."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session, error = _resolve_school_session(request, school)
        if error:
            return error

        cache_key = versioned_key(
            f"dashboards:school_id__{school.id}:view__department_overview"
            f":session__{session.id if session else 'all'}",
            [(SCOPE_SCHOOL, school.id)],
        )
        data = cache.get(cache_key)

        if data is None:
            courses_qs = Course.objects.filter(teacher__school=school)
            if session is not None:
                courses_qs = courses_qs.filter(session=session)
            courses = (
                courses_qs.values("name")
                .annotate(
                    teacher_count=Count("teacher", distinct=True),
                    avg_grade=Coalesce(
                        Avg(
                            "enrollments__final_grade",
                            filter=~Q(enrollments__enrollment_status="WITHDRAWN"),
                        ),
                        Value(0.0),
                        output_field=FloatField(),
                    ),
                )
                .order_by("name")
            )

            data = {
                "courses": [
                    {
                        "name": item["name"],
                        "teachers": item["teacher_count"],
                        # 0, never null, when no non-withdrawn enrollment has
                        # a graded final_grade yet - see course-performance's
                        # avg_grade for why (dashboard/views.py course_peformance).
                        "avg_grade": round(item["avg_grade"], 1),
                    }
                    for item in courses
                ]
            }

            cache.set(cache_key, data, 300)
        return Response(data)

    @extend_schema(
        tags=["School Admin"],
        summary="Get AI detailed information about analytics ",
        request=CustomAIPrompt,
        responses={200: CustomAIReply},
    )
    @action(
        detail=False,
        methods=["POST"],
        url_path="dashboard/custom-ai-prompt",
        throttle_classes=[CustomAIPromptThrottle],
    )
    def custom_ai_prompt(self, request, *args, **kwargs):
        serializer = CustomAIPrompt(data=request.data)
        serializer.is_valid(raise_exception=True)

        prompt = serializer.validated_data["prompt"]
        task_type = "custom_ai_prompt:schooladmin"
        user = request.user

        # TEACHERS used to come from `self.teachers(...)`, a method that no
        # longer exists on this view (the endpoint became
        # `teacher_performance`). The AttributeError was swallowed, so the
        # model received `{}` for teachers on every request. It is now built
        # directly, for every teacher in the school - not one page of them.
        context = "\n\n".join(
            [
                dashboard_context_section(
                    "SUMMARY METRICS",
                    lambda: self.summary(request, *args, **kwargs).data,
                    user=user,
                    task_type=task_type,
                ),
                dashboard_context_section(
                    "TEACHERS METRICS",
                    lambda: SchoolAdminAIContextService().teachers(user.school),
                    user=user,
                    task_type=task_type,
                ),
                dashboard_context_section(
                    "STUDENTS METRICS",
                    lambda: self.students(request, *args, **kwargs).data,
                    user=user,
                    task_type=task_type,
                ),
            ]
        )

        return run_dashboard_ai_chat(
            request,
            prompt,
            assistant_type=AssistantType.SCHOOL_ADMIN_ANALYTICS,
            role=UserTypes.SCHOOL_ADMIN,
            context=context,
            feature="Schooladmin Custom AI Prompt",
            task_type=task_type,
        )

    @extend_schema(
        tags=["School Admin"],
        summary="Get custom AI prompt conversation history",
        responses={200: DashboardChatSessionSerializer},
    )
    @action(
        detail=False, methods=["GET"], url_path="dashboard/custom-ai-prompt/history"
    )
    def custom_ai_prompt_history(self, request, *args, **kwargs):
        session = get_or_create_dashboard_chat_session(
            request.user,
            AssistantType.SCHOOL_ADMIN_ANALYTICS,
        )
        session = (
            ChatSession.objects.filter(id=session.id)
            .prefetch_related("chatmessage_set")
            .get()
        )
        serializer = DashboardChatSessionSerializer(session)
        return Response(serializer.data)


class TeacherAdminDashboardView(viewsets.ViewSet):
    permission_classes = [IsTeacher]

    risk_evaluator = StudentRiskEvaluator()

    @extend_schema(
        tags=["Teacher Admin"],
        summary="Teacher Dashboard Overview",
        description="""
        Retrieve high-level grading and assignment metrics for a teacher within a specific academic session.

        Metrics include:
        - Total assignments created by the teacher in the session.
        - Total number of assignments that have at least one graded submission.
        - Total number of assignments with no graded submissions (pending).
        - Total number of unique active students in the session.
        - Percentage of assignments that have been partially or fully graded.
        - Grade distribution (A, B, C, D, F) for all students in the session.
        - Average turnaround time for grading submissions (from submission to grading).
        """,
        parameters=[
            OpenApiParameter(
                name="session_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description=_("The unique identifier (UUID) of the academic session"),
            )
        ],
        responses={200: TeacherDashboardOverviewSerializer},
    )
    @action(
        detail=False,
        methods=["get"],
        url_path=r"dashboard/overview/(?P<session_id>[-\w]+)",
    )
    def overview(self, request, session_id, *args, **kwargs):
        teacher = request.user
        cache_key = versioned_key(
            f"teacheradmins:user_id__{teacher.id}"
            f":instance__id__{session_id}:view__overview",
            [(SCOPE_USER, teacher.id)],
        )
        data = cache.get(cache_key)

        if data is None:
            session = get_object_or_404(Session, id=session_id, teacher=teacher)

            assignments = Assignment.objects.filter(
                course__teacher=teacher, course__session=session
            )
            total_assigned = assignments.count()

            graded_submissions = StudentSubmission.objects.filter(
                assignment__in=assignments,
                score__isnull=False,
                graded_at__isnull=False,
            )
            # assignments that have at least one graded submission
            graded_assignments = (
                graded_submissions.values("assignment").distinct().count()
            )
            percent_graded = (
                round((graded_assignments / total_assigned) * 100, 2)
                if total_assigned > 0
                else 0
            )

            turnaround = graded_submissions.annotate(
                turnaround_time=ExpressionWrapper(
                    F("graded_at") - F("submission_date"),
                    output_field=DurationField(),
                )
            ).aggregate(avg_turnaround=Avg("turnaround_time"))["avg_turnaround"]

            # Grade distribution for the session
            grade_stats = (
                StudentCourse.objects.filter(course__session=session)
                .active()
                .aggregate(
                    a=Count(Case(When(final_grade__gte=90, then=Value(1)))),
                    b=Count(
                        Case(
                            When(final_grade__gte=80, final_grade__lt=90, then=Value(1))
                        )
                    ),
                    c=Count(
                        Case(
                            When(final_grade__gte=70, final_grade__lt=80, then=Value(1))
                        )
                    ),
                    d=Count(
                        Case(
                            When(final_grade__gte=65, final_grade__lt=70, then=Value(1))
                        )
                    ),
                    f=Count(Case(When(final_grade__lt=65, then=Value(1)))),
                )
            )

            total_graded = sum(grade_stats.values())

            def get_entry(count):
                return {
                    "count": count,
                    "percentage": (
                        round((count / total_graded) * 100, 2)
                        if total_graded > 0
                        else 0
                    ),
                }

            # total student in that session that the teacher has
            total_students = (
                StudentCourse.objects.filter(course__session=session)
                .active()
                .values("student")
                .distinct()
                .count()
            )

            total_pending = total_assigned - graded_assignments

            # Course performance for all courses in the session
            course_performance_raw = (
                Course.objects.filter(teacher=teacher, session=session)
                .annotate(
                    avg_grade=Avg("assignments__submissions__score_percentage"),
                    actual_subs=Count("assignments__submissions", distinct=True),
                    enrollment_count=Count("enrollments", distinct=True),
                    assignment_count=Count("assignments", distinct=True),
                )
                .values(
                    "id",
                    "name",
                    "avg_grade",
                    "actual_subs",
                    "enrollment_count",
                    "assignment_count",
                )
            )

            course_performance = []
            for cp in course_performance_raw:
                expected_subs = cp["enrollment_count"] * cp["assignment_count"]
                sub_rate = (
                    round((cp["actual_subs"] / expected_subs) * 100, 2)
                    if expected_subs > 0
                    else 0
                )
                course_performance.append(
                    {
                        "id": cp["id"],
                        "name": cp["name"],
                        "average_grade": round(float(cp["avg_grade"] or 0), 2),
                        "submission_rate": sub_rate,
                    }
                )

            # Top 7 upcoming published assignments in the session
            upcoming_assignments = (
                Assignment.objects.filter(
                    course__teacher=teacher,
                    course__session=session,
                    status=AssignmentStatus.PUBLISHED,
                    due_date__gt=timezone.now(),
                )
                .select_related("course")
                .order_by("due_date")[:7]
            )

            # AI Extraction Confidence across all assignments in session
            avg_extraction_confidence = (
                assignments.aggregate(avg=Avg("extraction_confidence"))["avg"] or 0
            )
            low_extraction_count = assignments.filter(
                extraction_confidence__lt=AI_CONFIDENCE_THRESHOLD
            ).count()

            # AI Grading Confidence across all submissions in session
            grading_qs = graded_submissions.filter(grading_confidence__isnull=False)
            avg_grading_confidence = (
                grading_qs.aggregate(avg=Avg("grading_confidence"))["avg"] or 0
            )
            low_grading_count = grading_qs.filter(
                grading_confidence__lt=AI_CONFIDENCE_THRESHOLD
            ).count()

            total_confidence_records = assignments.count() + grading_qs.count()

            low_confidence_rate = (
                (low_extraction_count + low_grading_count)
                / total_confidence_records
                * 100
                if total_confidence_records > 0
                else 0
            )

            # At-risk students across all courses in the session
            course_submissions_qs = (
                StudentSubmission.objects.filter(assignment__course__session=session)
                .select_related("assignment", "assignment__course")
                .order_by("submission_date", "id")
            )

            enrollments = (
                StudentCourse.objects.filter(course__session=session)
                .active()
                .select_related("student", "course")
                .annotate(
                    avg_grade_val=Avg(
                        "student__submissions__score_percentage",
                        filter=Q(
                            student__submissions__assignment__course=F("course_id")
                        ),
                    ),
                    submitted_count_val=Count(
                        "student__submissions",
                        filter=Q(
                            student__submissions__assignment__course=F("course_id")
                        ),
                    ),
                )
                .prefetch_related(
                    Prefetch(
                        "student__submissions",
                        queryset=course_submissions_qs,
                        to_attr="course_submissions",
                    )
                )
            )

            at_risk_students = []
            # Map course to the assignments a student is expected to have
            # submitted by now. Previously every assignment in the course,
            # drafts and not-yet-due work included, which flagged strong
            # students at-risk for "missing" work they could not have done -
            # measured: a 95% student with one real assignment and two drafts
            # was reported at_risk=True.
            course_totals = {
                c["course_id"]: c["total_assigned"]
                for c in Assignment.objects.filter(course__session=session)
                .filter(_expected_assignments_q())
                .values("course_id")
                .annotate(total_assigned=Count("id"))
            }

            for enrollment in enrollments:
                student = enrollment.student
                course = enrollment.course

                student_course_subs = [
                    s
                    for s in student.course_submissions
                    if s.assignment.course_id == course.id
                ]

                submitted_count = enrollment.submitted_count_val
                course_total_assigned = course_totals.get(course.id, 0)

                graded = [
                    s for s in student_course_subs if s.score_percentage is not None
                ]
                dated_scores = [
                    (s.submission_date, float(s.score_percentage)) for s in graded
                ]

                risk_result = self.risk_evaluator.evaluate(
                    RiskInputs(
                        expected_assignment_count=course_total_assigned,
                        submitted_count=submitted_count,
                        graded_scores=dated_scores,
                    )
                )

                if risk_result.at_risk:
                    at_risk_students.append(
                        {
                            "student_id": student.id,
                            "student_name": student.get_full_name(),
                            "course_id": course.id,
                            "course_name": course.name,
                            "average_grade": risk_result.average_grade,
                            "grade_trend": risk_result.grade_trend,
                        }
                    )

            data = {
                "total_assignments_assigned": total_assigned,
                "total_assignments_graded": graded_assignments,
                "total_assignment_pending_grade": total_pending,
                "total_students": total_students,
                "percentage_graded": percent_graded,
                "grade_distribution": {
                    "A": get_entry(grade_stats["a"]),
                    "B": get_entry(grade_stats["b"]),
                    "C": get_entry(grade_stats["c"]),
                    "D": get_entry(grade_stats["d"]),
                    "E": get_entry(0),
                    "F": get_entry(grade_stats["f"]),
                },
                "course_performance": list(course_performance),
                "upcoming_assignments": upcoming_assignments,
                "at_risk_students": at_risk_students,
                "average_grading_turnaround": turnaround,
                "ai_trust": {
                    "average_ai_extraction_confidence": round(
                        float(avg_extraction_confidence), 2
                    ),
                    "average_ai_grading_confidence": round(
                        float(avg_grading_confidence), 2
                    ),
                    "low_confidence_rate": round(float(low_confidence_rate), 2),
                },
            }

            serializer = TeacherDashboardOverviewSerializer(data)
            data = serializer.data

            cache.set(cache_key, data, 60 * 15)
        return Response(data)

    @extend_schema(
        tags=["Teacher Admin"],
        summary="Teacher Course Performance Analytics",
        description="""
        Retrieve detailed performance, workflow, and AI trust analytics for a specific course.

        Analytics include:
        - Workflow: Counts of assigned, submitted, and graded assignments.
        - Performance: Average grades grouped by assignment type,
          the assignment with the lowest student mastery, and overall performance trend.
        - AI Trust: Average confidence scores for AI extraction and grading, and the rate of
          low-confidence records.
        """,
        parameters=[
            OpenApiParameter(
                name="course_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description=_("The unique identifier (UUID) of the course"),
            )
        ],
        responses={200: TeacherCourseAnalyticsSerializer},
    )
    @action(
        detail=False,
        methods=["get"],
        url_path=r"dashboard/courses/(?P<course_id>[-\w]+)",
    )
    def courses(self, request, course_id, *args, **kwargs):
        cache_key = versioned_key(
            f"teacheradmins:user_id__{request.user.id}"
            f":instance_id__{course_id}:view__courses",
            # `usr` alone. A `crs` scope was tried here and removed: every
            # SCOPE_COURSE bump in the project is accompanied by the
            # teacher's SCOPE_USER bump (verified across all three signal
            # modules), so adding `crs` to a key that already carries that
            # teacher's generation narrows nothing and costs one extra
            # Redis GET per request. Mutation-proved: removing `crs` broke
            # no test, because it was doing no work.
            [(SCOPE_USER, request.user.id)],
        )
        data = cache.get(cache_key)

        if data is None:
            course = get_object_or_404(Course, id=course_id, teacher=request.user)
            assignments = Assignment.objects.filter(course=course)
            total_assigned = assignments.count()
            submissions = StudentSubmission.objects.filter(assignment__course=course)
            total_submitted = submissions.values("assignment").distinct().count()
            graded_submissions = submissions.filter(score__isnull=False)

            total_graded = graded_submissions.values("assignment").distinct().count()

            # Average grade by Assignment Type
            avg_grade_by_type = graded_submissions.values(
                "assignment__assignment_type"
            ).annotate(avg_score=Avg("score_percentage"))

            avg_grade_by_topic = graded_submissions.values(
                "assignment__topic__name"
            ).annotate(avg_score=Avg("score_percentage"))

            # Lowest Mastery Assignment
            lowest_assignment = (
                graded_submissions.values("assignment__id", "assignment__title")
                .annotate(avg_score=Avg("score_percentage"))
                .order_by("avg_score")  # Fixed: order by ascending for lowest mastery
                .first()
            )

            # Course Performance trend
            trend_data = graded_submissions.order_by("graded_at").values_list(
                "graded_at", "score_percentage"
            )

            trend = "stable"
            if trend_data.count() >= 2:
                first = trend_data.first()[1]
                last = trend_data.last()[1]

                if last is not None and first is not None:
                    if last > first:
                        trend = "improving"
                    elif last < first:
                        trend = "declining"

            avg_extraction_confidence = (
                assignments.aggregate(avg=Avg("extraction_confidence"))["avg"] or 0
            )

            low_extraction_count = assignments.filter(
                extraction_confidence__lt=AI_CONFIDENCE_THRESHOLD
            ).count()

            # Grading Confidence
            grading_qs = submissions.filter(grading_confidence__isnull=False)
            avg_grading_confidence = (
                grading_qs.aggregate(avg=Avg("grading_confidence"))["avg"] or 0
            )
            low_grading_count = grading_qs.filter(
                grading_confidence__lt=AI_CONFIDENCE_THRESHOLD
            ).count()

            total_confidence_records = assignments.count() + grading_qs.count()

            low_confidence_rate = (
                (low_extraction_count + low_grading_count)
                / total_confidence_records
                * 100
                if total_confidence_records > 0
                else 0
            )

            data = {
                "workflow": {
                    "total_assignments_assigned": total_assigned,
                    "total_assignments_submitted": total_submitted,
                    "total_assignments_graded": total_graded,
                },
                "performance": {
                    "average_assignment_grade_by_type": avg_grade_by_type,
                    "average_assignment_grade_by_topic": avg_grade_by_topic,
                    "lowest_mastery_assignment": lowest_assignment,
                    "course_performance_trend": trend,
                },
                "ai_trust": {
                    "average_ai_extraction_confidence": round(
                        float(avg_extraction_confidence), 2
                    ),
                    "average_ai_grading_confidence": round(
                        float(avg_grading_confidence), 2
                    ),
                    "low_confidence_rate": round(float(low_confidence_rate), 2),
                },
            }

            serializer = TeacherCourseAnalyticsSerializer(data)
            data = serializer.data

            cache.set(cache_key, data, 60 * 15)

        return Response(data)

    @extend_schema(
        tags=["Teacher Admin"],
        summary="Teacher Assignment Performance Analytics",
        description="""
        Retrieve detailed performance metrics and AI trust indicators for a specific assignment.

        Analytics include:
        - Submission overview (total count, due date, type).
        - Performance metrics (average grade).
        - AI Trust indicators (extraction and grading confidence scores).
        """,
        parameters=[
            OpenApiParameter(
                name="assignment_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description=_("The unique identifier (UUID) of the assignment"),
            )
        ],
        responses={200: TeacherAssignmentAnalyticsSerializer},
    )
    @action(
        detail=False,
        methods=["get"],
        url_path=r"dashboard/assignments/(?P<assignment_id>[-\w]+)",
    )
    def assignments(self, request, assignment_id, *args, **kwargs):
        cache_key = versioned_key(
            f"teacheradmins:user_id__{request.user.id}"
            f":instance_id__{assignment_id}:view__assignments",
            [(SCOPE_USER, request.user.id)],
        )
        data = cache.get(cache_key)

        if data is None:
            assignment = get_object_or_404(
                Assignment, id=assignment_id, course__teacher=self.request.user
            )
            submissions = StudentSubmission.objects.filter(assignment=assignment)
            total_submissions = submissions.count()
            average_grade = (
                submissions.filter(score__isnull=False).aggregate(
                    avg=Avg("score_percentage")
                )["avg"]
                or 0
            )

            avg_grading_confidence = (
                submissions.aggregate(avg=Avg("grading_confidence"))["avg"] or 0
            )

            assignment_metrics = {
                "assignment": assignment.id,
                "title": assignment.title,
                "due_date": assignment.due_date,
                "assignment_type": assignment.assignment_type,
                "total_submissions": total_submissions,
                "unit": assignment.topic,
                "average_grade": round(float(average_grade), 2),
                "ai_extraction_confidence": assignment.extraction_confidence,
                "ai_grading_confidence": round(float(avg_grading_confidence), 2),
            }

            # Not implemented: hardest/easiest questions per assignment.

            serializer = TeacherAssignmentAnalyticsSerializer(assignment_metrics)
            data = serializer.data

            cache.set(cache_key, data, 60 * 15)
        return Response(data)

    @extend_schema(
        tags=["Teacher Admin"],
        summary="Teacher Students Course Analytics",
        description="""
        Retrieve detailed performance metrics, submission history,
        and risk analysis for all students in a specific course.

        Analytics per student include:
        - Basic info (Student ID, Name).
        - Submission metrics (Count submitted vs assigned).
        - Performance indicators (Average grade, best/worst assignments).
        - Predictive analysis (Grade trend, "At Risk" flags).
        - Full submission history for the course.

        Risk Analysis Logic:
        A student is flagged as "at_risk" if they meet ANY of:
        - Average grade below 60% (critical).
        - Submission rate below 50%, with at least 2 assignments due
          (critical missing work).
        - At least TWO of the following "Moderate" conditions:
            1. Average grade below 70%.
            2. Submission rate below 70%.
            3. Performance trend is "DECLINING".
        """,
        parameters=[
            OpenApiParameter(
                name="course_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description=_("The unique identifier (UUID) of the course"),
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
        responses={200: PaginatedTeacherStudentAnalyticsSerializer},
    )
    @action(
        detail=False,
        methods=["get"],
        url_path=r"dashboard/students/(?P<course_id>[-\w]+)",
    )
    def students(self, request, course_id, *args, **kwargs):
        paginator = StandardPageNumberPagination()
        page_number = request.query_params.get(paginator.page_query_param, "1")
        page_size = request.query_params.get(paginator.page_size_query_param, "")
        cache_key = versioned_key(
            f"teacheradmins:user_id__{request.user.id}:instance_id__{course_id}"
            f":view__students:{page_number}:{page_size}",
            # `usr` alone - same reasoning as view__courses above.
            [(SCOPE_USER, request.user.id)],
        )
        data = cache.get(cache_key)

        if data is None:
            teacher = request.user
            course = get_object_or_404(Course, id=course_id, teacher=teacher)

            assignments = Assignment.objects.filter(course=course)
            total_assigned = assignments.count()
            # Risk is judged against work a student could have submitted by
            # now - see _expected_assignments_q. `total_assigned` above is
            # still what the response reports as `assignment_assigned`.
            expected_assignment_count = assignments.filter(
                _expected_assignments_q()
            ).count()

            # Pre-fetch submissions for all students in this course to avoid N+1
            course_submissions_qs = (
                StudentSubmission.objects.filter(assignment__course=course)
                .select_related("assignment")
                .order_by("submission_date", "id")
            )

            enrollments = (
                StudentCourse.objects.filter(course=course)
                .select_related("student")
                .annotate(
                    avg_grade_val=Avg(
                        "student__submissions__score_percentage",
                        filter=Q(student__submissions__assignment__course=course),
                    ),
                    submitted_count_val=Count(
                        "student__submissions",
                        filter=Q(student__submissions__assignment__course=course),
                    ),
                )
                .prefetch_related(
                    Prefetch(
                        "student__submissions",
                        queryset=course_submissions_qs,
                        to_attr="course_submissions",
                    )
                )
                # Stable order, so a student can never appear on two pages.
                .order_by("student__first_name", "student__last_name", "id")
            )

            # PAGINATED. This endpoint documented `page` / `page_size` and put
            # them in its cache key, but returned every student in the course
            # regardless - so a 300-student course built and serialized 300
            # full submission histories per request. It now follows the
            # project's StandardPageNumberPagination contract: a
            # {count, next, previous, results} envelope, 20 per page by
            # default, `page_size` capped at 100, 404 for a page that does not
            # exist.
            page = paginator.paginate_queryset(enrollments, request, view=self)

            rows = []
            for enrollment in page:
                student = enrollment.student
                # Use prefetched submissions
                student_course_submissions = student.course_submissions
                submitted_count = enrollment.submitted_count_val

                # Filter specifically for graded submissions from the prefetched list
                graded = [
                    s
                    for s in student_course_submissions
                    if s.score_percentage is not None
                ]

                # Best/Worst (using score_percentage for normalized comparison)
                best = max(graded, key=lambda s: s.score_percentage or 0, default=None)
                worst = min(graded, key=lambda s: s.score_percentage or 0, default=None)

                dated_scores = [
                    (s.submission_date, float(s.score_percentage)) for s in graded
                ]
                risk_result = self.risk_evaluator.evaluate(
                    RiskInputs(
                        expected_assignment_count=expected_assignment_count,
                        submitted_count=submitted_count,
                        graded_scores=dated_scores,
                    )
                )
                average_grade = risk_result.average_grade
                trend = risk_result.grade_trend
                at_risk = risk_result.at_risk

                assignment_history = [
                    {
                        "assignment_id": s.assignment.id,
                        "assignment_title": s.assignment.title,
                        "submitted": True,
                        "score": s.score,
                        "score_percentage": s.score_percentage,
                        "graded_at": s.graded_at,
                    }
                    for s in student_course_submissions
                ]

                rows.append(
                    {
                        "student_id": student.id,
                        "student_name": student.get_full_name(),
                        "assignment_submitted": submitted_count,
                        "assignment_assigned": total_assigned,
                        "average_grade": average_grade,
                        "best_assignment": (
                            {
                                "id": best.assignment.id,
                                "title": best.assignment.title,
                                "score": best.score,
                                "score_percentage": best.score_percentage,
                            }
                            if best
                            else None
                        ),
                        "worst_assignment": (
                            {
                                "id": worst.assignment.id,
                                "title": worst.assignment.title,
                                "score": worst.score,
                                "score_percentage": worst.score_percentage,
                            }
                            if worst
                            else None
                        ),
                        "grade_trend": trend,
                        "assignment_history": assignment_history,
                        "ai_student_summary": enrollment.ai_summary,
                        "at_risk": at_risk,
                    }
                )

            serializer = TeacherStudentAnalyticsSerializer(rows, many=True)
            data = paginator.get_paginated_response(serializer.data).data

            cache.set(cache_key, data, 60 * 15)

        return Response(data)

    @extend_schema(
        tags=["Teacher Admin"],
        summary="Teacher AI Extraction and Grading Insights",
        request=CustomAIPrompt,
    )
    @action(
        detail=False,
        methods=["POST"],
        url_path=r"dashboard/custom-ai-prompt",
        throttle_classes=[CustomAIPromptThrottle],
    )
    def custom_ai_prompt(self, request, *args, **kwargs):
        serializer = CustomAIPrompt(data=request.data)
        serializer.is_valid(raise_exception=True)
        prompt = serializer.validated_data["prompt"]
        task_type = "custom_ai_prompt:teacher"

        # Built by TeacherAIContextService in a fixed number of queries with
        # stated limits, instead of calling four dashboard endpoints in loops
        # (one of them per assignment) and pasting every response in whole.
        context = dashboard_context_section(
            "TEACHING DATA",
            lambda: TeacherAIContextService().build(request.user),
            user=request.user,
            task_type=task_type,
        )

        return run_dashboard_ai_chat(
            request,
            prompt,
            assistant_type=AssistantType.TEACHER_ADMIN_ANALYTICS,
            role=UserTypes.TEACHER,
            context=context,
            feature="Teacher Custom AI Prompt",
            task_type=task_type,
        )

    @extend_schema(
        tags=["Teacher Admin"],
        summary="Get custom AI prompt conversation history",
        responses={200: DashboardChatSessionSerializer},
    )
    @action(
        detail=False, methods=["GET"], url_path="dashboard/custom-ai-prompt/history"
    )
    def custom_ai_prompt_history(self, request, *args, **kwargs):
        session = get_or_create_dashboard_chat_session(
            request.user,
            AssistantType.TEACHER_ADMIN_ANALYTICS,
        )
        session = (
            ChatSession.objects.filter(id=session.id)
            .prefetch_related("chatmessage_set")
            .get()
        )
        serializer = DashboardChatSessionSerializer(session)
        return Response(serializer.data)


def _assignment_status_counts(student, assignments, now):
    """The four assignment-status counts (Submitted / Not Submitted /
    Graded / Overdue) for `student` over `assignments` - an already
    course-scoped queryset, either every active course combined or a
    single one. Shared by StudentAdminDashboardView.overview (all courses)
    and .status_summary (all courses or one, via ?course=) so the two
    never compute this differently from each other."""
    submissions = StudentSubmission.objects.filter(
        student=student, assignment__in=assignments
    )
    assignments_submitted = submissions.count()

    submitted_assignment_ids = submissions.values_list("assignment_id", flat=True)
    pending_assignments = assignments.exclude(id__in=submitted_assignment_ids)

    assignments_not_submitted = pending_assignments.count()
    assignments_due_no_submission = pending_assignments.filter(due_date__lt=now).count()

    # Graded = released to the student, not merely scored - matches the
    # "released" pattern used for grade figures elsewhere in this view
    # (see StudentAdminDashboardView.summary).
    assignments_graded = submissions.filter(
        is_published=True, score_percentage__isnull=False
    ).count()

    return {
        "assignments_submitted": assignments_submitted,
        "assignments_not_submitted": assignments_not_submitted,
        "assignments_graded": assignments_graded,
        "assignments_due_no_submission": assignments_due_no_submission,
    }


class StudentAdminDashboardView(viewsets.ViewSet):
    permission_classes = [IsStudent]

    @extend_schema(
        tags=["Student Admin"],
        summary="Student Course Summary",
        description="""
        Retrieve a summary of a student's performance and activity in a specific course.
        Returns metrics including:
        - Total assignments assigned and submitted
        - Overall completion rate
        - Count of missing or overdue assignments
        - Average grade in the course
        - Performance trend (improving, stable, declining)
        - Top 3 best and worst performing assignments
        """,
        parameters=[
            OpenApiParameter(
                name="course_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description=_(
                    "The unique identifier (UUID) of the course to retrieve stats for"
                ),
            )
        ],
        responses={200: CourseAnalyticsSerializer},
    )
    @action(
        detail=False,
        methods=["get"],
        url_path=r"dashboard/summary/(?P<course_id>[-\w]+)",
    )
    def summary(self, request, course_id, *args, **kwargs):
        cache_key = f"studentadmins:user_id__{request.user.id}:instance_id__{course_id}:view__summary"
        data = cache.get(cache_key)

        if data is None:
            student = request.user

            # 1. Validate course
            course = get_object_or_404(
                Course.objects.select_related("session"), id=course_id, is_active=True
            )
            # 2. Validate enrollment
            get_object_or_404(
                StudentCourse.objects.active(), student=student, course=course
            )

            # 3. Assignments in this course. Published only: a student never
            # sees drafts, so counting them made work the student could not
            # see appear as assigned, missing and overdue.
            assignments = Assignment.objects.filter(
                course=course, status=AssignmentStatus.PUBLISHED
            )
            total_assigned = assignments.count()

            # 4. Student submissions for this course
            submissions = StudentSubmission.objects.filter(
                student=student, assignment__in=assignments
            )
            submitted_count = submissions.count()

            # GRADE VISIBILITY. Only grades the teacher has released. Every
            # grade-bearing figure below (average, trend, best, worst) used to
            # read `submissions` directly, so a score the teacher had not yet
            # published reached the student through this dashboard even
            # though students/serializers.py hides it everywhere else.
            released = submissions.filter(
                is_published=True, score_percentage__isnull=False
            )

            # 5. Completion rate
            completion_rate = (
                (submitted_count / total_assigned) * 100 if total_assigned > 0 else 0
            )

            # 6. Missing / Overdue assignments
            submitted_assignment_ids = submissions.values_list(
                "assignment_id", flat=True
            )
            missing_assignments = assignments.exclude(id__in=submitted_assignment_ids)

            not_submitted_count = missing_assignments.count()

            overdue_count = missing_assignments.filter(
                due_date__lt=timezone.now()
            ).count()

            # 7. Average grade (course)
            average_grade = released.aggregate(avg=Avg("score_percentage"))["avg"] or 0

            recent_scores = list(
                released.order_by("-submission_date").values_list(
                    "score_percentage", flat=True
                )[:5]
            )

            trend = "stable"
            if len(recent_scores) >= 3:
                # Use only valid scores for trend
                valid_scores = [float(s or 0) for s in recent_scores]
                first_half = valid_scores[: len(valid_scores) // 2]
                second_half = valid_scores[len(valid_scores) // 2 :]

                if sum(second_half) > sum(first_half):
                    trend = "improving"
                elif sum(second_half) < sum(first_half):
                    trend = "declining"

            # 9. Best & Worst assignments
            best_assignments = released.select_related("assignment").order_by(
                "-score_percentage"
            )[:3]
            worst_assignments = released.select_related("assignment").order_by(
                "score_percentage"
            )[:3]

            data = {
                "course": course.id,
                "assignment_submitted": submitted_count,
                "assignment_not_submitted": not_submitted_count,
                "assignment_graded": released.count(),
                "assignment_assigned": total_assigned,
                "completion_rate": completion_rate,
                "missing_or_overdue": overdue_count,
                "average_grade": round(float(average_grade), 2),
                "grade_trend": trend,
                "best_assignments": best_assignments,
                "worst_assignments": worst_assignments,
            }
            serializer = CourseAnalyticsSerializer(data)
            data = serializer.data

            cache.set(cache_key, data, 60 * 15)
        return Response(data)

    @extend_schema(
        tags=["Student Admin"],
        summary="Student Assignments List",
        description="""
        Retrieve a list of all assignments for a student, including their submission details, scores, and feedback.

        This endpoint returns:
        - Assignment identification (ID and Title)
        - Deadlines (Due Date)
        - Student performance (Score and Feedback)
        - Timestamps (Submission Date)
        - Status tracking (Submitted, Late, etc.)
        - etc
        """,
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
                description="Number of results per page (max 100)",
            ),
        ],
        responses={200: StudentAssignmentListSerializer(many=True)},
    )
    @action(
        detail=False,
        methods=["get"],
        url_path=r"dashboard/assignments",
    )
    def assignments(self, request, *args, **kwargs):
        paginator = StandardPageNumberPagination()
        page_number = request.query_params.get(paginator.page_query_param, "1")
        page_size = request.query_params.get(paginator.page_size_query_param, "")
        cache_key = (
            f"studentadmins:user_id__{request.user.id}:view__assignments"
            f":{page_number}:{page_size}"
        )
        data = cache.get(cache_key)

        if data is None:
            student = request.user

            # Published assignments in courses the student is actively
            # enrolled in. Previously any assignment in any course the student
            # had EVER been enrolled in, drafts included - so draft titles and
            # due dates reached students, and so did withdrawn courses' work.
            # A course-id subquery rather than a join through enrollments,
            # which also cannot produce duplicate rows.
            active_course_ids = (
                StudentCourse.objects.active()
                .filter(student=student)
                .values("course_id")
            )
            assignments = (
                Assignment.objects.filter(
                    course_id__in=active_course_ids,
                    status=AssignmentStatus.PUBLISHED,
                )
                # Every row reads course.name and course.teacher; without this
                # each row cost two extra queries (measured +30 for 10 rows).
                .select_related("course", "course__teacher").order_by("-created_at")
            )

            assignments = paginator.paginate_queryset(assignments, request, view=self)

            submissions = StudentSubmission.objects.filter(
                student=student, assignment__in=assignments
            )

            submissions_map = {s.assignment_id: s for s in submissions}

            data = []
            for a in assignments:
                s = submissions_map.get(a.id)
                # Mirrors students/serializers.py: a student sees a grade, and
                # the fact that grading happened, only once it is released.
                # The submission itself when its grade is released, else None,
                # so every grade read below is gated on the same object.
                released = s if s is not None and s.is_published else None

                if not s:
                    if a.due_date and a.due_date < timezone.now():
                        submission_status = "OVERDUE"
                    else:
                        submission_status = "NOT SUBMITTED"
                elif released and released.graded_at:
                    submission_status = "GRADED"
                else:
                    submission_status = "SUBMITTED"

                stats = {
                    "course": a.course.name,
                    "teacher": a.course.teacher.get_full_name(),
                    "assignment_id": str(a.id),
                    "title": a.title,
                    "due_date": a.due_date,
                    # THIS student's submission. `a.submissions.first()` took
                    # whichever student's submission sorted first, and cost a
                    # query per row.
                    "submission_date": s.submission_date if s else None,
                    "score": released.score if released else None,
                    "score_percentage": (
                        released.score_percentage if released else None
                    ),
                    "total_score": a.total_points,
                    "feedback": released.feedback if released else None,
                    "submission_status": submission_status,
                }

                data.append(stats)

            serializer = StudentAssignmentListSerializer(data, many=True)
            data = paginator.get_paginated_response(serializer.data).data

            cache.set(cache_key, data, 60 * 15)
        return Response(data)

    @extend_schema(
        tags=["Student Admin"],
        summary="Student Dashboard Overview",
        description="""
        Retrieve an overview of the student's academic metrics across all active courses they are enrolled in.
        Returns:
        - Total courses enrolled
        - Number of assignments submitted/completed
        - Number of assignments not yet due but pending submission
        - Number of assignments due but not yet submitted
        """,
        responses={200: StudentDashboardOverviewSerializer},
    )
    @action(
        detail=False,
        methods=["get"],
        url_path="dashboard/overview",
    )
    def overview(self, request, *args, **kwargs):
        cache_key = f"studentadmins:user_id__{request.user.id}:view__overview"
        data = cache.get(cache_key)

        if data is None:
            student = request.user
            now = timezone.now()

            # 1. Active courses the student is enrolled in
            active_courses = list(
                Course.objects.filter(
                    enrollments__student=student,
                    enrollments__in=StudentCourse.objects.active(),
                    is_active=True,
                ).distinct()
            )
            total_courses = len(active_courses)

            # 2. All published assignments across active courses
            assignments = Assignment.objects.filter(
                course__in=active_courses,
                status=AssignmentStatus.PUBLISHED,
            )

            # 3. All student submissions for those assignments
            submissions = StudentSubmission.objects.filter(
                student=student,
                assignment__in=assignments,
            ).select_related("assignment__course")

            status_counts = _assignment_status_counts(student, assignments, now)
            assignments_submitted = status_counts["assignments_submitted"]
            assignments_not_submitted = status_counts["assignments_not_submitted"]
            assignments_due_no_submission = status_counts[
                "assignments_due_no_submission"
            ]
            assignments_graded = status_counts["assignments_graded"]

            # 5. Per-course grade breakdown
            # Group graded submissions by course for efficient computation
            course_submissions: dict = {course.id: [] for course in active_courses}
            all_percentages = []
            all_scores = []
            graded_course_gpas = []

            for sub in submissions:
                course_id = sub.assignment.course_id
                # Released grades only - see StudentAdminDashboardView.summary.
                if course_id in course_submissions:
                    if sub.is_published and sub.score_percentage is not None:
                        course_submissions[course_id].append(sub)

            courses_grades = []
            for course in active_courses:
                course_subs = course_submissions.get(course.id, [])
                if course_subs:
                    avg_pct = sum(float(s.score_percentage) for s in course_subs) / len(
                        course_subs
                    )
                    avg_score = sum(
                        float(s.score) for s in course_subs if s.score is not None
                    ) / len(course_subs)
                    all_percentages.append(avg_pct)
                    all_scores.extend(
                        float(s.score) for s in course_subs if s.score is not None
                    )
                else:
                    avg_pct = 0.0
                    avg_score = 0.0

                grade_details = get_grade_details(avg_pct)
                courses_grades.append(
                    {
                        "course_id": course.id,
                        "course_name": course.name,
                        "score": round(avg_score, 2),
                        "percentage": round(avg_pct, 2),
                        "grade": grade_details["letter_grade"],
                        "gpa": grade_details["gpa"],
                    }
                )
                if course_subs:
                    graded_course_gpas.append(grade_details["gpa"])

            # 6. Overall grade standing. overall_percentage is a separate,
            # independent stat (the student's average percentage
            # performance, per graded course not per submission) - it does
            # NOT feed overall_grade/overall_remark. Those instead follow
            # the course percentage -> course letter grade -> course GPA ->
            # overall GPA -> overall letter grade -> overall remark chain,
            # so the letter grade and GPA shown together always agree with
            # each other and with the school's own GPA scale (see
            # get_letter_grade_from_gpa).
            if all_percentages:
                overall_percentage = round(
                    sum(all_percentages) / len(all_percentages), 2
                )
            else:
                overall_percentage = 0.0

            overall_gpa = (
                round(sum(graded_course_gpas) / len(graded_course_gpas), 2)
                if graded_course_gpas
                else 0.0
            )
            overall_grade_details = get_letter_grade_from_gpa(overall_gpa)
            overall_grade = overall_grade_details["letter_grade"]
            overall_remark = overall_grade_details["remark"]

            overview_data = {
                "total_courses": total_courses,
                "assignments_submitted": assignments_submitted,
                "assignments_not_submitted": assignments_not_submitted,
                "assignments_graded": assignments_graded,
                "assignments_due_no_submission": assignments_due_no_submission,
                # Grade standing
                "overall_percentage": overall_percentage,
                "overall_grade": overall_grade,
                "overall_gpa": overall_gpa,
                "overall_remark": overall_remark,
                # Per-course breakdowns
                "courses_grades": courses_grades,
            }

            serializer = StudentDashboardOverviewSerializer(overview_data)
            data = serializer.data
            cache.set(cache_key, data, 60 * 15)

        return Response(data)

    @extend_schema(
        tags=["Student Admin"],
        summary="Student Assignment Status Summary",
        description="""
        The four assignment-status counts alone - Submitted, Not
        Submitted, Graded, Overdue - the same values and the same
        computation as the dashboard overview, without the grade/GPA
        figures.

        Omit the `course` query param for the combined count across every
        active course (what the "All Assignments" page shows). Pass
        `?course=<course_id>` to scope the same four counts to just that
        one course (what a course's own page shows) - the student must be
        actively enrolled in it, or this returns 404.
        """,
        parameters=[
            OpenApiParameter(
                name="course",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=False,
                description=(
                    "Optional course id to scope the counts to a single "
                    "course. Omit for all active courses combined."
                ),
            ),
        ],
        responses={200: StudentAssignmentStatusSummarySerializer},
    )
    @action(
        detail=False,
        methods=["get"],
        url_path="dashboard/status-summary",
    )
    def status_summary(self, request, *args, **kwargs):
        student = request.user
        now = timezone.now()
        course_id = request.query_params.get("course")

        if course_id:
            course = get_object_or_404(
                Course.objects.filter(
                    enrollments__student=student,
                    enrollments__in=StudentCourse.objects.active(),
                    is_active=True,
                ),
                id=course_id,
            )
            cache_key = (
                f"studentadmins:user_id__{student.id}"
                f":view__status_summary:course__{course.id}"
            )
            assignments = Assignment.objects.filter(
                course=course, status=AssignmentStatus.PUBLISHED
            )
        else:
            cache_key = f"studentadmins:user_id__{student.id}:view__status_summary:all"
            active_courses = Course.objects.filter(
                enrollments__student=student,
                enrollments__in=StudentCourse.objects.active(),
                is_active=True,
            ).distinct()
            assignments = Assignment.objects.filter(
                course__in=active_courses, status=AssignmentStatus.PUBLISHED
            )

        data = cache.get(cache_key)
        if data is None:
            status_counts = _assignment_status_counts(student, assignments, now)
            serializer = StudentAssignmentStatusSummarySerializer(status_counts)
            data = serializer.data
            cache.set(cache_key, data, 60 * 15)

        return Response(data)
