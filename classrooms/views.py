import secrets

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from users.models import CustomUser, UserTypes

from .models import (  # , Classroom, ClassroomSettings
    Course,
    EnrollmentStatusType,
    Session,
    StudentCourse,
)
from .serializers import (  # ClassroomSerializer,; ClassroomSettingsSerializer,
    AddStudentToCourseSerializer,
    CourseSerializer,
    ExpiredTokenSerializer,
    SessionSerializer,
    StudentCourseSerializer,
)


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
class CourseViewSet(viewsets.ModelViewSet):
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
    permission_classes = (IsAuthenticated,)
    pagination_class = PageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]

    filterset_fields = ["session", "is_active", "categories"]
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

        if user.user_type == UserTypes.TEACHER:
            return Course.objects.filter(teacher=user)
        elif user.user_type == UserTypes.STUDENT:
            return Course.objects.filter(enrollments__student=user)
        else:
            return Course.objects.none()

    @extend_schema(
        tags=["02 Course"],
        summary="Add student to a particular course",
        description="Add student to a particular course.",
        request=AddStudentToCourseSerializer,
    )
    @action(detail=True, methods=["post"], url_path="students", url_name="students")
    def students(self, request, pk=None, *args, **kwargs):
        serializer = AddStudentToCourseSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        email = serializer.validated_data["email"]

        """Onboard students to a section."""
        course = self.get_object()

        try:
            with transaction.atomic():
                student = CustomUser.objects.filter(email=email).first()

                if student:
                    # Existing student flow
                    if StudentCourse.objects.filter(
                        student=student, course=course
                    ).exists():
                        return Response(
                            {"detail": "Student is already enrolled in this course."},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

                    StudentCourse.objects.create(
                        student=student,
                        course=course,
                        enrollment_status=EnrollmentStatusType.ENROLLED,
                    )

                    # Notify student about enrollment
                    context = {
                        "course": course,
                        "teacher": course.teacher,
                        "student": student,
                        "login_url": f"https://{settings.FRONTEND_DOMAIN}",
                    }

                    html_content = render_to_string(
                        "email/existing_student_course_enrollment.html", context=context
                    )

                    send_mail(
                        subject=f"New Course Enrollment: {course.name}",
                        message="",
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[student.email],
                        html_message=html_content,
                        fail_silently=False,
                    )
                else:
                    # New student flow
                    activation_token = secrets.token_urlsafe(16)

                    # Create inactive user account
                    student = CustomUser.objects.create(
                        email=email,
                        user_type=UserTypes.STUDENT,
                        is_active=False,
                        activation_token=activation_token,
                        activation_expires=timezone.now() + timezone.timedelta(days=7),
                    )

                    # Create pending enrollment
                    StudentCourse.objects.create(
                        student=student,
                        course=course,
                        enrollment_status=EnrollmentStatusType.ENROLLED,
                        is_active=False,  # Inactive until registration is complete
                    )

                    # Generate registration link
                    frontend_domain = settings.FRONTEND_DOMAIN
                    registration_link = (
                        f"https://{frontend_domain}/register/student/{activation_token}"
                    )

                    context = {
                        "course": course,
                        "teacher": course.teacher,
                        "registration_link": registration_link,
                    }

                    html_content = render_to_string(
                        "email/student_course_registration.html", context=context
                    )

                    send_mail(
                        subject="Complete Your Registration for the Course",
                        message="",
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[student.email],
                        html_message=html_content,
                        fail_silently=False,
                    )

                return Response(
                    {
                        "detail": "Student added to course successfully.",
                        "is_new_student": student.is_active is False,
                    },
                    status=status.HTTP_200_OK,
                )
        except Exception as e:
            return Response(
                {"detail": f"Failed to process student: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
        """Remove a student from a course"""
        try:
            with transaction.atomic():
                course = self.get_object()

                if request.user != course.teacher:
                    return Response(
                        {
                            "detail": "You do not have permission to remove students from this course. "
                            "Only course teacher can"
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )

                # Find the enrollment
                enrollment = StudentCourse.objects.filter(
                    course=course, student_id=student_id, is_active=True
                ).first()

                if not enrollment:
                    return Response(
                        {"detail": "Student is not enrolled in this course."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                # Deactivate enrollment instead
                enrollment.is_active = False
                enrollment.enrollment_status = EnrollmentStatusType.WITHDRAWN
                enrollment.save()

                # Send notification to student
                student = enrollment.student
                context = {
                    "course": course,
                    "teacher": course.teacher,
                    "student": student,
                }

                html_content = render_to_string(
                    "email/student_course_removal.html", context=context
                )

                send_mail(
                    subject=f"Removed from Course: {course.name}",
                    message="",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[student.email],
                    html_message=html_content,
                    fail_silently=False,
                )

                return Response(
                    {"detail": "Student removed from course successfully."},
                    status=status.HTTP_200_OK,
                )
        except Exception as e:
            return Response(
                {"detail": f"Failed to remove student: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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
        url_path=r"renew-student-token",
        url_name="renew-activation-token",
    )
    def handle_expired_token(self, request, token=None, *args, **kwargs):
        """Handle expired activation token scenario."""

        serializer = ExpiredTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            # Find user by expired token
            user = CustomUser.objects.filter(
                activation_token=serializer.validated_data["token"],
                is_active=False,
            ).first()

            if not user:
                return Response(
                    {"detail": "Invalid token or user not found."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            enrollment = StudentCourse.objects.filter(
                student=user, is_active=False
            ).first()

            if not enrollment:
                return Response(
                    {"detail": "No pending enrollment found for this user."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            new_token = user.renew_activation_token()

            # Generate new registration link
            registration_link = (
                f"https://{settings.FRONTEND_DOMAIN}/register/student/{new_token}"
            )
            expiry_date = timezone.now() + timezone.timedelta(days=7)

            student_context = {
                "course": enrollment.course,
                "teacher": enrollment.course.teacher,
                "registration_link": registration_link,
            }

            student_html = render_to_string(
                "email/student_token_renewal.html", context=student_context
            )

            # Notify student
            send_mail(
                subject="Course Registration Link Renewed",
                message="",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                html_message=student_html,
                fail_silently=False,
            )

            teacher_context = {
                "teacher": enrollment.course.teacher,
                "student_email": user.email,
                "course": enrollment.course,
                "expiry_date": expiry_date,
            }

            teacher_html = render_to_string(
                "email/teacher_token_renewal_notification.html", context=teacher_context
            )

            # Notify teacher
            send_mail(
                subject=f"Registration Link Renewed - {user.email}",
                message="",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[enrollment.course.teacher.email],
                html_message=teacher_html,
                fail_silently=False,
            )

            return Response(
                {
                    "detail": "A new activation link has been sent to the student's email. Expires in 7 days"
                },
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            return Response(
                {"detail": f"Failed to renew token: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


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
class SessionViewSet(viewsets.ModelViewSet):
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

    serializer_class = SessionSerializer
    permission_classes = (IsAuthenticated,)
    pagination_class = PageNumberPagination
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

        if user.user_type == UserTypes.TEACHER:
            return Session.objects.filter(teacher=user)
        elif user.user_type == UserTypes.STUDENT:
            return Session.objects.filter(courses__enrollment__student=user)
        else:
            return Session.objects.none()

    # @extend_schema(
    #     tags=["01 Session"],
    #     summary="Get courses for a session",
    #     description="Retrieve a list of courses associated with a specific session.",
    #     parameters=[
    #         OpenApiParameter(
    #             name="session_id",
    #             type=OpenApiTypes.UUID,
    #             location=OpenApiParameter.PATH,
    #             description="The ID of the session to retrieve courses for",
    #             required=True,
    #         ),
    #     ],
    #     responses={
    #         200: CourseSerializer(many=True),
    #         400: OpenApiResponse(description="Invalid session ID"),
    #         404: OpenApiResponse(description="Session not found"),
    #         500: OpenApiResponse(description="Internal Server Error"),
    #     },
    # )
    # @action(
    #     detail=False,
    #     methods=["get"],
    #     url_path=r"(?P<session_id>[-\w]+)/courses",
    #     url_name="session-courses",
    # )
    # def get_courses(self, request, session_id=None, **kwargs):
    #     """
    #     Retrieve a list of courses associated with a specific session.
    #     """
    #
    #     try:
    #         session = self.queryset.filter(id=session_id).first()
    #         if not session:
    #             return Response(
    #                 {"detail": "Session not found."}, status=status.HTTP_404_NOT_FOUND
    #             )
    #
    #         courses = session.courses.all()
    #
    #         serializer = CourseSerializer(courses, many=True)
    #         return Response(serializer.data)
    #
    #     except Exception as e:
    #         return Response(
    #             {"detail": "Internal Server Error", "traceback": f"{e}"},
    #             status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    #         )
    #


@extend_schema_view(
    list=extend_schema(
        tags=["06 Student Course"],
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
        tags=["06 Student Course"],
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
        tags=["06 Student Course"],
        summary="Retrieve a student Course",
        description="Retrieve detailed information about a specific student Course by its ID.",
        responses={
            200: StudentCourseSerializer,
            404: OpenApiResponse(description="Student Course not found "),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    partial_update=extend_schema(
        tags=["06 Student Course"],
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
        tags=["06 Student Course"],
        summary="Delete a student Course",
        description="Delete a student Course by ID. This action cannot be undone.",
        responses={
            204: OpenApiResponse(description="Student Course deleted successfully"),
            404: OpenApiResponse(description="Student Course not found"),
        },
    ),
)
class StudentCourseViewSet(viewsets.ModelViewSet):
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
    permission_classes = (IsAuthenticated,)
    pagination_class = PageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]

    def get_queryset(self):
        user = self.request.user

        if user.user_type == UserTypes.TEACHER:
            return StudentCourse.objects.filter(course__teacher=user)
        elif user.user_type == UserTypes.STUDENT:
            return StudentCourse.objects.filter(student=user)
        else:
            return StudentCourse.objects.none()
