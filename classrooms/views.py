from django.conf import settings
from django.contrib.auth.models import AnonymousUser

# from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

# from django.utils.decorators import method_decorator
# from django.views.decorators.cache import cache_page
# from django.views.decorators.vary import vary_on_headers
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ParseError, PermissionDenied, ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from AutoGrader.tasks import send_email_task
from users.models import CustomUser, UserTypes
from users.serializers import CustomUserSerializer
from users.services import otp_manager

from .models import (  # , Classroom, ClassroomSettings
    Course,
    CourseCategory,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
    Topic,
)
from .permissions import IsSuperAdmin, IsTeacherOrReadOnly
from .serializers import (  # ClassroomSerializer,; ClassroomSettingsSerializer,
    AddStudentToCourseSerializer,
    CourseCategorySerializer,
    CourseSerializer,
    ExpiredTokenSerializer,
    SchoolSerializer,
    SessionSerializer,
    StudentCourseSerializer,
    TopicSerializer,
)


@extend_schema_view(
    list=extend_schema(
        tags=["School"],
        summary="List all Schools",
        description="Retrieve a paginated list of all Schools in the system.",
        parameters=[
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number for pagination",
            )
        ],
        responses={200: SchoolSerializer(many=True)},
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
        summary="Retrieve a School",
        description="Retrieve detailed information about a specific School by its ID.",
        responses={
            200: SchoolSerializer,
            404: OpenApiResponse(description="School not found"),
        },
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
        summary="Delete a School",
        description="Delete a School by ID. This action cannot be undone.",
        responses={
            204: OpenApiResponse(description="School deleted successfully"),
            403: OpenApiResponse(
                description="You do not have permission to delete this School"
            ),
            404: OpenApiResponse(description="School not found"),
        },
    ),
)
class SchoolViewSet(viewsets.ModelViewSet):
    queryset = School.objects.all()
    serializer_class = SchoolSerializer
    permission_classes = (IsSuperAdmin,)
    pagination_class = PageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    ordering_fields = ["name", "created_at"]
    search_fields = ["name"]

    # @method_decorator(cache_page(60 * 3, key_prefix="schools:list"))
    # @method_decorator(vary_on_headers("Authorization"))
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    # @method_decorator(cache_page(60 * 3, key_prefix="schools:retrieve"))
    # @method_decorator(vary_on_headers("Authorization"))
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        tags=["School"],
        summary="Create the School admin of a school",
        description="""

        """,
        request=CustomUserSerializer,
        responses={
            201: OpenApiResponse(
                response=CustomUserSerializer,
                description="School admin created successfully",
            ),
            400: OpenApiResponse(
                description="Invalid input. Missing required fields or invalid data format"
            ),
        },
    )
    @action(detail=False, methods=["post"], url_path="admin", url_name="admin")
    def admin(self, request, *args, **kwargs):
        """Make a User the admin of a school"""
        serializer = CustomUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(serializer.data, status=status.HTTP_200_OK)


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
    permission_classes = (IsAuthenticated, IsTeacherOrReadOnly)
    pagination_class = PageNumberPagination
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

    # @method_decorator(cache_page(60 * 3, key_prefix="courses:list"))
    # @method_decorator(vary_on_headers("Authorization"))
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    # @method_decorator(cache_page(60 * 3, key_prefix="courses:detail"))
    # @method_decorator(vary_on_headers("Authorization"))
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

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
    def students(self, request, *args, **kwargs):
        serializer = AddStudentToCourseSerializer(data=request.data)
        if not serializer.is_valid():
            raise ValidationError(serializer.errors)

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
                        raise ParseError("Student is already enrolled in this course.")

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

                    send_email_task.delay(
                        subject=f"New Course Enrollment: {course.name}",
                        message="",
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[student.email],
                        html_message=html_content,
                    )
                else:
                    # New student flow
                    activation_token = otp_manager.generate_otp()

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
                        enrollment_status=EnrollmentStatusType.PENDING,
                    )

                    # Generate registration link
                    frontend_domain = settings.FRONTEND_DOMAIN
                    registration_link = f"https://{frontend_domain}/register/student/{activation_token}?email={email}"

                    context = {
                        "course": course,
                        "teacher": course.teacher,
                        "registration_link": registration_link,
                    }

                    html_content = render_to_string(
                        "email/student_course_registration.html", context=context
                    )

                    send_email_task.delay(
                        subject="Complete Your Registration for the Course",
                        message="",
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        recipient_list=[student.email],
                        html_message=html_content,
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
                    raise PermissionDenied(
                        "You do not have permission to remove students from this course. "
                        "Only course teacher can"
                    )

                # Find the enrollment
                enrollment = StudentCourse.objects.filter(
                    course=course, student_id=student_id
                ).first()

                if not enrollment:
                    raise ParseError("Student is not enrolled in this course.")

                # Deactivate enrollment instead
                # enrollment.withdrawn()
                # enrollment.enrollment_status = EnrollmentStatusType.WITHDRAWN
                #
                # enrollment.save()

                # Send notification to student
                student = enrollment.student
                context = {
                    "course": course,
                    "teacher": course.teacher,
                    "student": student,
                }

                enrollment.delete()
                student.delete()

                html_content = render_to_string(
                    "email/student_course_removal.html", context=context
                )

                send_email_task.delay(
                    subject=f"Removed from Course: {course.name}",
                    message="",
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[student.email],
                    html_message=html_content,
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
                raise ParseError("Invalid token or user not found.")

            enrollment = StudentCourse.objects.filter(
                student=user, is_active=False
            ).first()

            if not enrollment:
                raise ParseError("No pending enrollment found for this user.")

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
            send_email_task.delay(
                subject="Course Registration Link Renewed",
                message="",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                html_message=student_html,
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
            send_email_task.delay(
                subject=f"Registration Link Renewed - {user.email}",
                message="",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[enrollment.course.teacher.email],
                html_message=teacher_html,
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

    @extend_schema(
        tags=["02 Course"],
        summary="List courses a student is enrolled in",
        # description="",
        responses=CourseSerializer(many=True),
    )
    # @method_decorator(cache_page(60 * 5, key_prefix="courses:my_list"))
    # @method_decorator(vary_on_headers("Authorization"))
    @action(detail=False, methods=["get"], url_name="my-courses", url_path="my-courses")
    def my_courses(self, request, *args, **kwargs):
        """List courses the authenticated student is enrolled in, exclude withdrawn"""
        user = request.user

        if user.user_type != UserTypes.STUDENT:
            raise ParseError("Only students can access their enrolled courses.")

        student_courses = StudentCourse.objects.filter(student=user)
        courses = [sc.course for sc in student_courses]

        serializer = CourseSerializer(courses, many=True)
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

        serializer = TopicSerializer(data=topics_data, many=True)
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

    queryset = Session.objects.all()
    serializer_class = SessionSerializer
    permission_classes = (IsAuthenticated, IsTeacherOrReadOnly)
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

    # @method_decorator(cache_page(60 * 3, key_prefix="sessions:list"))
    # @method_decorator(vary_on_headers("Authorization"))
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    # @method_decorator(cache_page(60 * 3, key_prefix="sessions:detail"))
    # @method_decorator(vary_on_headers("Authorization"))
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user

        if isinstance(user, AnonymousUser):
            return Session.objects.none()

        if user.user_type == UserTypes.TEACHER:
            return Session.objects.filter(teacher=user)
        elif user.user_type == UserTypes.STUDENT:
            return Session.objects.filter(
                courses__enrollments__student=user,
                courses__enrollments__enrollment_status=EnrollmentStatusType.ENROLLED,
            )
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
    permission_classes = (IsAuthenticated, IsTeacherOrReadOnly)
    pagination_class = PageNumberPagination
    http_method_names = ["get", "head", "delete", "patch", "options"]

    # @method_decorator(cache_page(60 * 3, key_prefix="studentcourses:list"))
    # @method_decorator(vary_on_headers("Authorization"))
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    # @method_decorator(cache_page(60 * 3, key_prefix="studentcourses:detail"))
    # @method_decorator(vary_on_headers("Authorization"))
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    def get_queryset(self):
        user = self.request.user

        if user.user_type == UserTypes.TEACHER:
            return StudentCourse.objects.filter(course__teacher=user)
        elif user.user_type == UserTypes.STUDENT:
            return StudentCourse.objects.filter(student=user)
        else:
            return StudentCourse.objects.none()


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
class CourseCategoryViewSet(viewsets.ModelViewSet):
    """
    API endpoint that allows course categories to be viewed or edited.
    """

    queryset = CourseCategory.objects.all()
    serializer_class = CourseCategorySerializer
    permission_classes = (IsAuthenticated, IsTeacherOrReadOnly)
    pagination_class = PageNumberPagination
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

    # @method_decorator(cache_page(60 * 3, key_prefix="coursecategories:list"))
    # @method_decorator(vary_on_headers("Authorization"))
    def list(self, request, *args, **kwargs):
        """
        List all course categories with optional search and pagination.
        """
        return super().list(request, *args, **kwargs)

    # @method_decorator(cache_page(60 * 3, key_prefix="coursecategories:detail"))
    # @method_decorator(vary_on_headers("Authorization"))
    def retrieve(self, request, *args, **kwargs):
        """
        Retrieve a specific course category by ID.
        """
        return super().retrieve(request, *args, **kwargs)

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
    def category_courses(self, request, pk=None):
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
class TopicViewSet(viewsets.ModelViewSet):
    queryset = Topic.objects.all()
    serializer_class = TopicSerializer
    permission_class = (IsAuthenticated, IsTeacherOrReadOnly)
    pagination_class = PageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "option"]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ("name", "course__name")
    search_fields = ("name", "course__name")
    ordering_fields = ("name",)

    def get_queryset(self):
        user = self.request.user

        if user.user_type == UserTypes.TEACHER:
            return Topic.objects.filter(course__teacher=user)
        elif user.user_type == UserTypes.STUDENT:
            return Topic.objects.filter(course__enrollments__student=user)
        else:
            return Topic.objects.none()

    # @method_decorator(cache_page(60 * 3, key_prefix="topics:list"))
    # @method_decorator(vary_on_headers("Authorization"))
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    # @method_decorator(cache_page(60 * 3, key_prefix="topics:detail"))
    # @method_decorator(vary_on_headers("Authorization"))
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)
