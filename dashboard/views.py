from django.db.models import Avg, Case, Count, FloatField, Q, Value, When
from django.db.models.functions import Cast, ExtractHour
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.views.decorators.vary import vary_on_headers
from drf_spectacular.utils import extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from assignments.models import Assignment
from classrooms.models import Course, School, StudentCourse
from classrooms.permissions import IsSchoolAdmin, IsSuperAdmin
from students.models import StudentSubmission
from users.models import CustomUser, UserActivity, UserTypes


class SuperAdminDashboardView(viewsets.ViewSet):
    permission_classes = [IsSuperAdmin]

    @extend_schema(tags=["Super Admin"])
    @method_decorator(cache_page(60 * 3, key_prefix="superadmin:dashboard:summary"))
    @method_decorator(vary_on_headers("Authorization"))
    @action(detail=False, methods=["get"], url_path="dashboard/summary")
    def summary(self, request, *args, **kwargs):
        # Implementation for summary endpoint

        total_schools = School.objects.all()
        total_teachers = CustomUser.objects.filter(
            user_type=UserTypes.TEACHER, is_active=True
        )
        total_students = CustomUser.objects.filter(
            user_type=UserTypes.STUDENT, is_active=True
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

        # 7. Peak Concurrent Users (PCU)
        # We find the window with the most unique users.
        # This is an approximation as it finds the timestamp with the highest 'concurrency'
        # within a theoretical 5-min window around each activity point.
        pcu_data = (
            UserActivity.objects.values("timestamp")
            .annotate(user_count=Count("user", distinct=True))
            .order_by("-user_count")
            .first()
        )
        pcu = pcu_data["user_count"] if pcu_data else 0

        # 8. Peak Time of Day
        peak_hour_data = (
            UserActivity.objects.annotate(hour=ExtractHour("timestamp"))
            .values("hour")
            .annotate(activity_count=Count("id"))
            .order_by("-activity_count")
            .first()
        )
        peak_time_of_day = f"{peak_hour_data['hour']}:00" if peak_hour_data else "N/A"

        return Response(
            {
                "total_schools": total_schools.count(),
                "active_teachers": total_teachers.count(),
                "total_students": total_students.count(),
                "active_courses": active_courses.count(),
                "total_assignments": total_assignments.count(),
                "total_submissions": total_submissions.count(),
                "avg_course_size": round(avg_course_size, 2),
                "total_assignments_graded": total_assignments_graded,
                "avg_assignments_per_course": round(avg_assignments_per_course, 2),
                "avg_courses_per_teacher": round(avg_courses_per_teacher, 2),
                "avg_grading_confidence": round(avg_grading_confidence, 2),
                "avg_extraction_confidence": round(avg_extraction_confidence, 2),
                "peak_concurrent_users": pcu,
                "peak_time_of_day": peak_time_of_day,
            }
        )

    @extend_schema(tags=["Super Admin"])
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
                    "average_performance": round(school.performance or 0, 2),
                    # "assignment_completion_rate": round(completion_rate, 2),
                }
            )

        return Response(result)

    @extend_schema(tags=["Super Admin"])
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
                    "metrics": {
                        "number_of_courses": teacher.course_count,
                        "number_of_students": teacher.student_count,
                        "average_student_performance": round(
                            teacher.average_grade or 0, 2
                        ),
                        "assignment_completion_rate": round(
                            min(completion_rate, 100), 2
                        ),
                    },
                }
            )
        return Response(performance_data)

    @extend_schema(tags=["Super Admin"])
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

        return Response(
            {
                "average_grade": round(stats["avg_grade"] or 0, 2),
                "global_assignment_completion_rate": round(
                    min(completion_rate, 100), 2
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
        )


class SchoolAdminDashboardView(viewsets.ViewSet):
    permission_classes = [IsSchoolAdmin]
    http_method_names = ["get", "head", "options"]

    @extend_schema(tags=["School Admin"])
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

        # 1. Active Teachers in the school
        active_teachers = CustomUser.objects.filter(
            school=school,
            user_type=UserTypes.TEACHER,
            is_active=True,
        ).count()

        # 2. Active Students in the school
        active_students = CustomUser.objects.filter(
            school=school,
            user_type=UserTypes.STUDENT,
            is_active=True,
        ).count()

        # 3. Active Courses in the school
        # (Linked via teachers who belong to the school)
        active_courses = Course.objects.filter(
            teacher__school=school,
            is_active=True,
        ).count()

        # 4. Total Assignments in the school
        total_assignments = Assignment.objects.filter(
            course__teacher__school=school
        ).count()

        # 5. Total Submissions in the school
        total_submissions = StudentSubmission.objects.filter(
            assignment__course__teacher__school=school
        ).count()

        return Response(
            {
                "school_name": school.name,
                "active_teachers": active_teachers,
                "active_students": active_students,
                "active_courses": active_courses,
                "total_assignments": total_assignments,
                "total_submissions": total_submissions,
            }
        )

    @extend_schema(tags=["School Admin"])
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
            # total_assignments = teacher.courses.aggregate(count=Count('assignments'))['count'] or 0
            # total_enrollments = teacher.courses.aggregate(count=Count('enrollments'))['count'] or 0
            # expected_submissions = total_assignments * total_enrollments

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
                    "metrics": {
                        "number_of_courses": teacher.course_count,
                        "number_of_students": teacher.student_count,
                        "average_student_performance": round(
                            teacher.average_grade or 0, 2
                        ),
                        "assignment_completion_rate": round(
                            min(completion_rate, 100), 2
                        ),
                    },
                }
            )

        return Response(performance_data)

    @extend_schema(tags=["School Admin"])
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

        # 1. Filter StudentCourses to only thoses in this school's courses
        school_student_courses = StudentCourse.objects.filter(
            course__teacher__school=school
        )

        # 2. Global Metrics (Average and Distribution)
        stats = school_student_courses.aggregate(
            avg_grade=Avg("final_grade"),
            total_enrollments=Count("id"),
            a=Count(Case(When(final_grade__gte=90, then=Value(1)))),
            b=Count(Case(When(final_grade__gte=80, final_grade__lt=90, then=Value(1)))),
            c=Count(Case(When(final_grade__gte=70, final_grade__lt=80, then=Value(1)))),
            d=Count(Case(When(final_grade__gte=60, final_grade__lt=70, then=Value(1)))),
            f=Count(Case(When(final_grade__lt=60, then=Value(1)))),
        )

        # 3. Completion Rate logic for the School
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

        return Response(
            {
                "school_name": school.name,
                "average_grade": round(stats["avg_grade"] or 0, 2),
                "assignment_completion_rate": round(min(completion_rate, 100), 2),
                "grade_distribution": {
                    "A": stats["a"],
                    "B": stats["b"],
                    "C": stats["c"],
                    "D": stats["d"],
                    "F": stats["f"],
                },
                "total_active_enrollments": stats["total_enrollments"],
            }
        )
