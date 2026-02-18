from datetime import timedelta

from django.db.models import (
    Avg,
    Case,
    Count,
    DurationField,
    ExpressionWrapper,
    F,
    FloatField,
    Q,
    Value,
    Variance,
    When,
)
from django.db.models.functions import Cast
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.text import gettext_lazy as _
from django.views.decorators.cache import cache_page
from django.views.decorators.vary import vary_on_headers
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from ai_processor.services import AI_CONFIDENCE_THRESHOLD
from assignments.models import Assignment
from classrooms.models import Course, School, Session, StudentCourse
from classrooms.permissions import IsSchoolAdmin, IsStudent, IsSuperAdmin, IsTeacher
from dashboard.serializers import (
    ConcurrencySerializer,
    CourseAnalyticsSerializer,
    PlatformAdoptionSerializer,
    PlatformAIPerformanceSerializer,
    PlatformUsageSerializer,
    ScalingSignalsSerializer,
    SchoolAdminStudentPerformanceSerializer,
    SchoolAdminSummarySerializer,
    SchoolAdminTeacherPerformanceSerializer,
    SchoolAnalyticsSerializer,
    StudentAssignmentListSerializer,
    SuperAdminStudentPerformanceSerializer,
    TeacherAssignmentAnalyticsSerializer,
    TeacherCourseAnalyticsSerializer,
    TeacherDashboardOverviewSerializer,
    TeacherPerformanceSerializer,
    TeacherStudentAnalyticsSerializer,
)

# from dashboard.services import analyze_question_difficulty
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes
from users.services import (
    get_peak_concurrent_users,
    get_peak_time_of_day,
    get_time_range,
)


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
    @method_decorator(cache_page(60 * 30, key_prefix="superadmin:dashboard:adoption"))
    @method_decorator(vary_on_headers("Authorization"))
    @action(detail=False, methods=["get"], url_path="dashboard/adoption")
    def platform_adoption(self, request, *args, **kwargs):
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

        active_teachers_30d = teacher_queryset.filter(last_login__gte=month_ago).count()

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

        return Response(serializer.data)

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
    @method_decorator(cache_page(60 * 30, key_prefix="superadmin:dashboard:usage"))
    @method_decorator(vary_on_headers("Authorization"))
    @action(detail=False, methods=["get"], url_path="dashboard/usage")
    def platform_usage(self, request, *args, **kwargs):
        # BASE QUERYSETS
        assignments = Assignment.objects.all()
        submissions = StudentSubmission.objects.all()

        # TOTAL ASSIGNMENTS
        total_assignments_created = assignments.count()

        # TOTAL ASSIGNMENTS GRADED
        total_assignments_graded = (
            assignments.filter(submissions__graded_at__isnull=False).distinct().count()
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
            .filter(total_submissions__gt=0, total_submissions=F("graded_submissions"))
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
                    F("graded_at") - F("submission_date"), output_field=DurationField()
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
            "avg_assignments_per_course": round(float(avg_assignments_per_course), 2),
            "avg_assignments_per_active_teacher": round(
                float(avg_assignments_per_teacher), 2
            ),
            "assignment_percent_fully_graded": percent_fully_graded,
            "grading_turnaround_time_p50": p50,
            "grading_turnaround_time_p95": p95,
        }

        serializer = PlatformUsageSerializer(data)

        return Response(serializer.data)

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
    @method_decorator(
        cache_page(60 * 30, key_prefix="superadmin:dashboard:performance")
    )
    @method_decorator(vary_on_headers("Authorization"))
    @action(detail=False, methods=["get"], url_path="dashboard/ai_performance")
    def platform_ai_performance(self, request, *args, **kwargs):
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
                # "queue_backlog": None,
                # "error_rate": None,
            },
        }

        serializer = PlatformAIPerformanceSerializer(data)

        return Response(serializer.data)

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
    @method_decorator(
        cache_page(60 * 30, key_prefix="superadmin:dashboard:scaling_signals")
    )
    @method_decorator(vary_on_headers("Authorization"))
    @action(detail=False, methods=["get"], url_path="dashboard/scaling_signals")
    def scaling_signals(self, request, *args, **kwargs):
        """
        Institutional & Scaling Signals for Founder/Super Admin.
        Tracks how the platform is moving from individual use to institutional adoption.
        """

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
            avg_extraction_confidence=Avg("users__assignments__extraction_confidence"),
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
            "avg_assignments_per_school": round(float(avg_assignments_per_school), 2),
            "avg_grading_confidence_per_school": round(
                float(ai_confidence_by_school["total_avg_grading"] or 0), 2
            ),
            "avg_extraction_confidence_per_school": round(
                float(ai_confidence_by_school["total_avg_extraction"] or 0), 2
            ),
        }

        serializer = ScalingSignalsSerializer(data)

        return Response(serializer.data)

    @extend_schema(tags=["Super Admin"])
    @method_decorator(cache_page(60 * 3, key_prefix="superadmin:dashboard:summary"))
    @method_decorator(vary_on_headers("Authorization"))
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
        responses={200: SchoolAnalyticsSerializer(many=True)},
    )
    @method_decorator(cache_page(60 * 3, key_prefix="superadmin:dashboard:schools"))
    @method_decorator(vary_on_headers("Authorization"))
    @action(detail=False, methods=["get"], url_path="dashboard/schools")
    def schools(self, request, *args, **kwargs):

        schools = School.objects.all().annotate(
            teacher_count=Count(
                "users",
                filter=Q(users__user_type=UserTypes.TEACHER, users__is_active=True),
                distinct=True,
            ),
            student_count=Count(
                "users",
                filter=Q(users__user_type=UserTypes.STUDENT, users__is_active=True),
                distinct=True,
            ),
            course_count=Count("users__courses", distinct=True),
            performance=Avg(
                "users__courses__enrollments__final_grade",
                filter=Q(users__user_type=UserTypes.TEACHER),
            ),
            total_assignments=Count("users__courses__assignments", distinct=True),
            # total_completed_assignments=Count("users__courses__assignments__submissions",
            #                                   filter=Q(users__courses__assignments__submission__is_completed=True),
            #                                   distinct=True),
        )

        result = []
        for school in schools:
            # completion_rate = (
            #     (school.total_completed_assignments / school.total_assignments) * 100
            #     if school.total_assignments else 0
            # )

            result.append(
                {
                    "school_id": school.id,
                    "school_name": school.name,
                    "teachers": school.teacher_count,
                    "students": school.student_count,
                    "courses": school.course_count,
                    "average_performance": round(float(school.performance or 0), 2),
                    # "assignment_completion_rate": round(completion_rate, 2),
                }
            )

        serializer = SchoolAnalyticsSerializer(result, many=True)

        return Response(serializer.data)

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
        responses={200: TeacherPerformanceSerializer(many=True)},
    )
    @method_decorator(cache_page(60 * 3, key_prefix="superadmin:dashboard:teachers"))
    @method_decorator(vary_on_headers("Authorization"))
    @action(detail=False, methods=["get"], url_path="dashboard/teachers")
    def teachers(self, request, *args, **kwargs):
        """
        Returns performance metrics for all teachers:
        - Number of courses per teacher
        - Number of students per teacher
        - Average student performance per teacher
        - Assignment completion rates per teacher
        """

        teacher_queryset = CustomUser.objects.filter(
            user_type=UserTypes.TEACHER, is_active=True
        )

        teachers = teacher_queryset.annotate(
            course_count=Count("courses"),
            student_count=Count("courses__enrollments__student", distinct=True),
            average_grade=Avg("courses__enrollments__final_grade"),
            total_possible_submissions=Count("courses__enrollments", distinct=True)
            * Count("courses__assignments", distinct=True),
            actual_submissions=Count(
                "courses__assignments__submissions", distinct=True
            ),
        )

        performance_data = []
        for teacher in teachers:
            total_assignments = (
                teacher.courses.aggregate(count=Count("assignments"))["count"] or 0
            )
            total_enrollments = (
                teacher.courses.aggregate(count=Count("enrollments"))["count"] or 0
            )
            expected_submissions = total_assignments * total_enrollments

            completion_rate = (
                (teacher.actual_submissions / expected_submissions * 100)
                if expected_submissions > 0
                else 0
            )

            performance_data.append(
                {
                    "teacher_id": teacher.id,
                    "teacher_name": f"{teacher.first_name} {teacher.last_name}",
                    "number_of_courses": teacher.course_count,
                    "number_of_students": teacher.student_count,
                    "average_student_performance": round(
                        float(teacher.average_grade or 0), 2
                    ),
                    "assignment_completion_rate": round(
                        min(float(completion_rate), 100), 2
                    ),
                }
            )

        serializer = TeacherPerformanceSerializer(performance_data, many=True)
        return Response(serializer.data)

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
    @method_decorator(cache_page(60 * 3, key_prefix="superadmin:dashboard:students"))
    @method_decorator(vary_on_headers("Authorization"))
    @action(detail=False, methods=["get"], url_path="dashboard/students")
    def students(self, request, *args, **kwargs):
        stats = StudentCourse.objects.active().aggregate(
            avg_grade=Avg("final_grade"),
            total_enrollments=Count("id"),
            total_assignments=Count("course__assignments", distinct=True),
        )

        # total_students = CustomUser.objects.filter(user_type=UserTypes.STUDENT, is_active=True).count()
        actual_submissions = StudentSubmission.objects.count()

        expected_submissions = sum(
            c.assignments.count() * c.enrollments.count()
            for c in Course.objects.filter(is_active=True)
        )

        completion_rate = (
            (actual_submissions / expected_submissions * 100)
            if expected_submissions > 0
            else 0
        )

        distribution = StudentCourse.objects.active().aggregate(
            a=Count(Case(When(final_grade__gte=90, then=Value(1)))),
            b=Count(Case(When(final_grade__gte=80, final_grade__lt=90, then=Value(1)))),
            c=Count(Case(When(final_grade__gte=70, final_grade__lt=80, then=Value(1)))),
            d=Count(Case(When(final_grade__gte=60, final_grade__lt=70, then=Value(1)))),
            f=Count(Case(When(final_grade__lt=60, then=Value(1)))),
        )

        data = {
            "average_grade": round(float(stats["avg_grade"] or 0), 2),
            "global_assignment_completion_rate": round(
                min(float(completion_rate), 100), 2
            ),
            "grade_distribution": {
                "A": distribution["a"],
                "B": distribution["b"],
                "C": distribution["c"],
                "D": distribution["d"],
                "F": distribution["f"],
            },
            "total_active_enrollments": stats["total_enrollments"],
        }

        serializer = SuperAdminStudentPerformanceSerializer(data)

        return Response(serializer.data)

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
    @method_decorator(cache_page(60 * 3, key_prefix="superadmin:dashboard:concurrency"))
    @method_decorator(vary_on_headers("Authorization"))
    @action(detail=False, methods=["get"], url_path="dashboard/concurrency")
    def concurrency(self, request, *args, **kwargs):
        range_key: str = request.query_params.get("range", "daily")

        start, end = get_time_range(range_key)
        pcu = get_peak_concurrent_users(start, end)
        peak_time = get_peak_time_of_day(start, end)

        payload = {
            "time_range": range_key,
            "start": start,
            "end": end,
            "peak_concurrent_users": pcu,
            "peak_time_of_day": peak_time,
        }

        serializer = ConcurrencySerializer(payload)

        return Response(serializer.data)


class SchoolAdminDashboardView(viewsets.ViewSet):
    permission_classes = [IsSchoolAdmin]
    http_method_names = ["get", "head", "options"]

    @extend_schema(
        tags=["School Admin"],
        summary="School Dashboard Summary",
        description="""
        Retrieve high-level metrics for the school admin's dashboard.

        Metrics include:
        - School identification (Name).
        - User Depth: Counts of active teachers and students in the school.
        - Institutional Activity: Total active courses, assignments, and submissions.
        """,
        responses={200: SchoolAdminSummarySerializer},
    )
    @method_decorator(cache_page(60 * 3, key_prefix="schooladmin:dashboard:summary"))
    @method_decorator(vary_on_headers("Authorization"))
    @action(detail=False, methods=["get"], url_path="dashboard/summary")
    def summary(self, request, *args, **kwargs):
        user = request.user
        school = user.school

        if not school:
            return Response(
                {"detail": "User is not associated with any school"},
            )

        active_teachers = CustomUser.objects.filter(
            school=school,
            user_type=UserTypes.TEACHER,
            is_active=True,
        ).count()

        active_students = CustomUser.objects.filter(
            school=school,
            user_type=UserTypes.STUDENT,
            is_active=True,
        ).count()

        active_courses = Course.objects.filter(
            teacher__school=school,
            is_active=True,
        ).count()

        total_assignments = Assignment.objects.filter(
            course__teacher__school=school
        ).count()

        total_submissions = StudentSubmission.objects.filter(
            assignment__course__teacher__school=school
        ).count()

        data = {
            "school_name": school.name,
            "active_teachers": active_teachers,
            "active_students": active_students,
            "active_courses": active_courses,
            "total_assignments": total_assignments,
            "total_submissions": total_submissions,
        }

        serializer = SchoolAdminSummarySerializer(data)
        return Response(serializer.data)

    @extend_schema(
        tags=["School Admin"],
        summary="School Teachers Performance",
        description="""
        Retrieve performance and engagement metrics for all teachers within the school.

        Metrics for each teacher include:
        - Basic identification (Teacher ID, Name).
        - Course Load: Total number of courses assigned.
        - Student Reach: Total number of unique students enrolled in their courses.
        - Academic Performance: Average student performance (final grades) across all their courses.
        - Engagement: Assignment completion rates (actual submissions vs. expected based on enrollments).
        """,
        responses={200: SchoolAdminTeacherPerformanceSerializer(many=True)},
    )
    @method_decorator(cache_page(60 * 3, key_prefix="schooladmin:dashboard:teachers"))
    @method_decorator(vary_on_headers("Authorization"))
    @action(detail=False, methods=["get"], url_path="dashboard/teachers")
    def teachers(self, request, *args, **kwargs):
        """
        Returns performance metrics for all teachers in the admin's school:
        - Number of courses per teacher
        - Number of students per teacher
        - Average student performance per teacher
        - Assignment completion rates per teacher
        """

        user = request.user
        school = user.school

        if not school:
            return Response(
                {
                    "detail": "User is not associated with any school",
                },
                status=400,
            )

        teacher_queryset = CustomUser.objects.filter(
            school=school, user_type=UserTypes.TEACHER, is_active=True
        ).annotate(
            course_count=Count("courses", distinct=True),
            student_count=Count("courses__enrollments__student", distinct=True),
            average_grade=Avg("courses__enrollments__final_grade"),
            actual_submissions=Count(
                "courses__assignments__submissions", distinct=True
            ),
        )

        performance_data = []
        for teacher in teacher_queryset:
            stats = teacher.courses.aggregate(
                total_assignments=Count("assignments", distinct=True),
                total_enrollments=Count("enrollments", distinct=True),
            )

            expected_submissions = (stats["total_assignments"] or 0) * (
                stats["total_enrollments"] or 0
            )

            completion_rate = (
                (teacher.actual_submissions / expected_submissions * 100)
                if expected_submissions > 0
                else 0
            )

            performance_data.append(
                {
                    "teacher_id": teacher.id,
                    "teacher_name": f"{teacher.first_name} {teacher.last_name}",
                    "number_of_courses": teacher.course_count,
                    "number_of_students": teacher.student_count,
                    "average_student_performance": round(
                        float(teacher.average_grade or 0), 2
                    ),
                    "assignment_completion_rate": round(
                        min(float(completion_rate), 100), 2
                    ),
                }
            )

        serializer = SchoolAdminTeacherPerformanceSerializer(
            performance_data, many=True
        )
        return Response(serializer.data)

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
        responses={200: SchoolAdminStudentPerformanceSerializer},
    )
    @method_decorator(cache_page(60 * 3, key_prefix="schooladmin:dashboard:students"))
    @method_decorator(vary_on_headers("Authorization"))
    @action(detail=False, methods=["get"], url_path="dashboard/students")
    def students(self, request, *args, **kwargs):
        user = request.user
        school = user.school

        if not school:
            return Response(
                {
                    "detail": "User is not associated with any school",
                },
                status=400,
            )

        school_student_courses = StudentCourse.objects.filter(
            course__teacher__school=school
        )

        stats = school_student_courses.aggregate(
            avg_grade=Avg("final_grade"),
            total_enrollments=Count("id"),
            a=Count(Case(When(final_grade__gte=90, then=Value(1)))),
            b=Count(Case(When(final_grade__gte=80, final_grade__lt=90, then=Value(1)))),
            c=Count(Case(When(final_grade__gte=70, final_grade__lt=80, then=Value(1)))),
            d=Count(Case(When(final_grade__gte=60, final_grade__lt=70, then=Value(1)))),
            f=Count(Case(When(final_grade__lt=60, then=Value(1)))),
        )

        actual_submissions = StudentSubmission.objects.filter(
            assignment__course__teacher__school=school
        ).count()
        active_courses = Course.objects.filter(teacher__school=school, is_active=True)
        expected_submissions = sum(
            c.assignments.count() * c.enrollments.count() for c in active_courses
        )

        completion_rate = (
            (actual_submissions / expected_submissions * 100)
            if expected_submissions > 0
            else 0
        )

        data = {
            "school_name": school.name,
            "average_grade": round(float(stats["avg_grade"] or 0), 2),
            "assignment_completion_rate": round(min(float(completion_rate), 100), 2),
            "grade_distribution": {
                "A": stats["a"],
                "B": stats["b"],
                "C": stats["c"],
                "D": stats["d"],
                "F": stats["f"],
            },
            "total_active_enrollments": stats["total_enrollments"],
        }

        serializer = SchoolAdminStudentPerformanceSerializer(data)
        return Response(serializer.data)


class TeacherAdminDashboardView(viewsets.ViewSet):
    permission_classes = [IsTeacher]
    http_method_names = ["get", "options", "head"]

    @extend_schema(
        tags=["Teacher Admin"],
        summary="Teacher Dashboard Overview",
        description="""
        Retrieve high-level grading and assignment metrics for a teacher within a specific academic session.

        Metrics include:
        - Total assignments created by the teacher in the session.
        - Total number of assignments that have at least one graded submission.
        - Percentage of assignments that have been partially or fully graded.
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
    @method_decorator(cache_page(60 * 3, key_prefix="teacheradmin:dashboard:overview"))
    @method_decorator(vary_on_headers("Authorization"))
    @action(
        detail=False,
        methods=["get"],
        url_path=r"dashboard/overview/(?P<session_id>[-\w]+)",
    )
    def overview(self, request, session_id, *args, **kwargs):
        teacher = request.user
        session = get_object_or_404(Session, id=session_id, teacher=teacher)

        assignments = Assignment.objects.filter(
            teacher=teacher, course__session=session
        )
        total_assigned = assignments.count()

        graded_submissions = StudentSubmission.objects.filter(
            assignment__in=assignments,
            score__isnull=False,
            graded_at__isnull=False,
        )
        # assignments that have at least one graded submission
        graded_assignments = graded_submissions.values("assignment").distinct().count()
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

        data = {
            "total_assignments_assigned": total_assigned,
            "total_assignments_graded": graded_assignments,
            "percentage_graded": percent_graded,
            "average_grading_turnaround": turnaround,
        }

        serializer = TeacherDashboardOverviewSerializer(data)

        return Response(serializer.data)

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
    @method_decorator(cache_page(60 * 3, key_prefix="teacheradmin:dashboard:courses"))
    @method_decorator(vary_on_headers("Authorization"))
    @action(
        detail=False,
        methods=["get"],
        url_path=r"dashboard/courses/(?P<course_id>[-\w]+)",
    )
    def courses(self, request, course_id, *args, **kwargs):
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
        ).annotate(avg_score=Avg("score"))

        avg_grade_by_topic = graded_submissions.values(
            "assignment__topic__name"
        ).annotate(avg_score=Avg("score"))

        # Lowest Mastery Assignment
        lowest_assignment = (
            graded_submissions.values("assignment__id", "assignment__title")
            .annotate(avg_score=Avg("score"))
            .order_by("avg_score")  # Fixed: order by ascending for lowest mastery
            .first()
        )

        # Course Performance trend
        trend_data = graded_submissions.order_by("graded_at").values_list(
            "graded_at", "score"
        )

        trend = "stable"
        if trend_data.count() >= 2:
            first = trend_data.first()[1]
            last = trend_data.last()[1]

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
            (low_extraction_count + low_grading_count) / total_confidence_records * 100
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

        return Response(serializer.data)

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
    @method_decorator(
        cache_page(60 * 3, key_prefix="teacheradmin:dashboard:assignments")
    )
    @method_decorator(vary_on_headers("Authorization"))
    @action(
        detail=False,
        methods=["get"],
        url_path=r"dashboard/assignments/(?P<assignment_id>[-\w]+)",
    )
    def assignments(self, request, assignment_id, *args, **kwargs):
        assignment = get_object_or_404(
            Assignment, id=assignment_id, course__teacher=self.request.user
        )
        submissions = StudentSubmission.objects.filter(assignment=assignment)
        total_submissions = submissions.count()
        average_grade = (
            submissions.filter(score__isnull=False).aggregate(avg=Avg("score"))["avg"]
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

        # FIXME: Implement the hardest and easiest questions
        # hardest, easiest = analyze_question_difficulty(submissions)

        # assignment_metrics.update(
        #     {
        #         # "hardest_questions": hardest,
        #         # "easiest_questions": easiest,
        #         "custom_ai_prompt": {
        #             "enabled": False,
        #             "scope": "assignment",
        #             "prompt": assignment.custom_ai_prompt,
        #         }
        #     }
        # )

        serializer = TeacherAssignmentAnalyticsSerializer(assignment_metrics)

        return Response(serializer.data)

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
        A student is flagged as "at_risk" if they meet at least two of the following criteria:
        1. Average grade below 50%.
        2. Submission rate below 70%.
        3. Performance trend is "DECLINING".
        """,
        parameters=[
            OpenApiParameter(
                name="course_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description=_("The unique identifier (UUID) of the course"),
            )
        ],
        responses={200: TeacherStudentAnalyticsSerializer(many=True)},
    )
    @method_decorator(cache_page(60 * 3, key_prefix="teacheradmin:dashboard:students"))
    @method_decorator(vary_on_headers("Authorization"))
    @action(
        detail=False,
        methods=["get"],
        url_path=r"dashboard/students/(?P<course_id>[-\w]+)",
    )
    def students(self, request, course_id, *args, **kwargs):
        teacher = request.user
        course = get_object_or_404(Course, id=course_id, teacher=teacher)

        assignments = Assignment.objects.filter(course=course)
        total_assigned = assignments.count()

        enrollments = StudentCourse.objects.filter(course=course).select_related(
            "student"
        )

        data = []
        for enrollment in enrollments:
            student = enrollment.student
            submissions = StudentSubmission.objects.filter(
                student=student, assignment__course=course
            ).select_related("assignment")
            submitted_count = submissions.count()
            graded = submissions.filter(score__isnull=False)
            avg_grade = graded.aggregate(avg=Avg("score"))["avg"] or 0

            best = graded.order_by("-score").first()
            worst = graded.order_by("score").first()

            recent = graded.order_by("-graded_at")[:3]
            scores = [s.score for s in recent if s.score is not None]

            if len(scores) < 2:
                trend = "INSUFFICIENT_DATA"
            elif scores[0] > scores[-1]:
                trend = "IMPROVING"
            elif scores[0] < scores[-1]:
                trend = "DECLINING"
            else:
                trend = "STABLE"

            risk_flags = 0
            if avg_grade is not None and avg_grade < 50:
                risk_flags += 1
            if total_assigned > 0 and (submitted_count / total_assigned) < 0.7:
                risk_flags += 1
            if trend == "DECLINING":
                risk_flags += 1

            at_risk = risk_flags >= 2
            assignment_history = [
                {
                    "assignment_id": s.assignment.id,
                    "assignment_title": s.assignment.title,
                    "submitted": True,
                    "score": s.score,
                    "graded_at": s.graded_at,
                }
                for s in submissions
            ]
            data.append(
                {
                    "student_id": student.id,
                    "student_name": student.get_full_name(),
                    "assignment_submitted": submitted_count,
                    "assignment_assigned": total_assigned,
                    "average_grade": (
                        round(avg_grade, 2) if avg_grade is not None else None
                    ),
                    "best_assignment": (
                        {
                            "id": best.assignment.id,
                            "title": best.assignment.title,
                            "score": best.score,
                        }
                        if best
                        else None
                    ),
                    "worst_assignment": (
                        {
                            "id": worst.assignment.id,
                            "title": worst.assignment.title,
                            "score": worst.score,
                        }
                        if worst
                        else None
                    ),
                    "grade_trend": trend,
                    "assignment_history": assignment_history,
                    "ai_student_summary": "Available in Power Tier",
                    "at_risk": at_risk,
                }
            )

        serializer = TeacherStudentAnalyticsSerializer(data, many=True)

        return Response(serializer.data)


class StudentAdminDashboardView(viewsets.ViewSet):
    permission_classes = [IsStudent]
    http_method_names = ["get", "options", "head"]

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
    @method_decorator(cache_page(60 * 5, key_prefix="studentadmin:dashboard:summary"))
    @method_decorator(vary_on_headers("Authorization"))
    @action(
        detail=False,
        methods=["get"],
        url_path=r"dashboard/summary/(?P<course_id>[-\w]+)",
    )
    def summary(self, request, course_id, *args, **kwargs):
        student = request.user

        # 1. Validate course
        course = get_object_or_404(
            Course.objects.select_related("session"), id=course_id, is_active=True
        )

        # 2. Validate enrollment
        get_object_or_404(
            StudentCourse.objects.active(), student=student, course=course
        )

        # 3. Assignments in this course
        assignments = Assignment.objects.filter(course=course)
        total_assigned = assignments.count()

        # 4. Student submissions for this course
        submissions = StudentSubmission.objects.filter(
            student=student, assignment__course=course
        )
        submitted_count = submissions.count()

        # 5. Completion rate
        completion_rate = (
            (submitted_count / total_assigned) * 100 if total_assigned > 0 else 0
        )

        # 6. Missing / Overdue assignments
        submitted_assignment_ids = submissions.values_list("assignment_id", flat=True)
        missing_assignments = assignments.exclude(id__in=submitted_assignment_ids)

        overdue_count = missing_assignments.filter(due_date__lt=timezone.now()).count()

        # 7. Average grade (course)
        average_grade = submissions.aggregate(avg=Avg("score"))["avg"] or 0

        recent_scores = list(
            submissions.order_by("-submission_date").values_list("score", flat=True)[:5]
        )

        trend = "stable"
        if len(recent_scores) >= 3:
            first_half = recent_scores[: len(recent_scores) // 2]
            second_half = recent_scores[len(recent_scores) // 2 :]

            if sum(second_half) > sum(first_half):
                trend = "improving"
            elif sum(second_half) < sum(first_half):
                trend = "declining"

        # 9. Best & Worst assignments
        best_assignments = submissions.order_by("-score")[:3]
        worst_assignments = submissions.order_by("score")[:3]

        data = {
            "course": course.id,
            "assignment_submitted": submitted_count,
            "assignment_assigned": total_assigned,
            "completion_rate": completion_rate,
            "missing_or_overdue": overdue_count,
            "average_grade": round(float(average_grade), 2),
            "grade_trend": trend,
            "best_assignments": best_assignments,
            "worst_assignments": worst_assignments,
        }
        serializer = CourseAnalyticsSerializer(data)
        return Response(serializer.data)

    @extend_schema(
        tags=["Student Admin"],
        summary="Student Course Assignments List",
        description="""
        Retrieve a list of all assignments for a student within a specific course,
        including their submission details, scores, and feedback.

        This endpoint returns:
        - Assignment identification (ID and Title)
        - Deadlines (Due Date)
        - Student performance (Score and Feedback)
        - Timestamps (Submission Date)
        - Status tracking (Submitted, Late, etc.)
        """,
        parameters=[
            OpenApiParameter(
                name="course_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description=_(
                    "The unique identifier (UUID) of the course to retrieve assignments for"
                ),
            )
        ],
        responses={200: StudentAssignmentListSerializer(many=True)},
    )
    @method_decorator(
        cache_page(60 * 5, key_prefix="studentadmin:dashboard:assignments")
    )
    @method_decorator(vary_on_headers("Authorization"))
    @action(
        detail=False,
        methods=["get"],
        url_path=r"dashboard/assignments/(?P<course_id>[-\w]+)",
    )
    def assignments(self, request, course_id, *args, **kwargs):
        student = request.user

        # Filter assignments for student's course
        assignments = Assignment.objects.filter(course__id=course_id)
        submissions = StudentSubmission.objects.filter(
            student=student, assignment__in=assignments
        ).select_related("assignment")

        submissions_map = {s.assignment_id: s for s in submissions}

        data = []
        for a in assignments:
            s = submissions_map.get(a.id)
            if s:
                submission_status = (
                    "late"
                    if a.due_date and s.submission_date > a.due_date
                    else "submitted"
                )
                score = s.score
                submission_date = a.submissions.first().submission_date
                feedback = s.feedback
                data.append(
                    {
                        "assignment": a,
                        "title": a.title,
                        "due_date": a.due_date,
                        "submission_date": submission_date,
                        "score": score,
                        "feedback": feedback,
                        "submission_status": submission_status,
                    }
                )

        serializer = StudentAssignmentListSerializer(data, many=True)
        return Response(serializer.data)

    # @extend_schema(tags=["Student Admin"])
    # @action(
    #     detail=False,
    #     methods=["get"],
    #     url_path=r"dashboard/strength/(?P<course_id>[-\w]+)",
    # )
    # def strengths(self, request, *args, **kwargs):
    #     pass

    # @extend_schema(tags=["Student Admin"])
    # @action(
    #     detail=False,
    #     methods=["get"],
    #     url_path=r"dashboard/ai_summary/(?P<course_id>[-\w]+)",
    # )
    # def ai_summary(self, request, *args, **kwargs):
    #     pass
