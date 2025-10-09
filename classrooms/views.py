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
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import Course, Session, StudentSection  # , Classroom, ClassroomSettings
from .serializers import (  # ClassroomSerializer,; ClassroomSettingsSerializer,
    CourseSerializer,
    SessionSerializer,
    StudentSectionSerializer,
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
    permission_classes = (AllowAny,)
    pagination_class = PageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]

    filterset_fields = ["session", "is_active"]
    ordering_fields = ["name", "start_date"]
    search_fields = [
        "name",
        "description",
    ]
    ordering = (
        "name",
        "created_at",
    )

    @extend_schema(tags=["02 Course"])
    @action(
        detail=False, methods=["post"], url_path="add_student", url_name="add_student"
    )
    def add_student(self, request):
        """Onboard students to a section."""
        return Response({}, status=status.HTTP_200_OK)


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
    permission_classes = (AllowAny,)
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

    @extend_schema(
        tags=["01 Session"],
        summary="Get courses for a session",
        description="Retrieve a list of courses associated with a specific session.",
        parameters=[
            OpenApiParameter(
                name="session_id",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.PATH,
                description="The ID of the session to retrieve courses for",
                required=True,
            ),
        ],
        responses={
            200: CourseSerializer(many=True),
            400: OpenApiResponse(description="Invalid session ID"),
            404: OpenApiResponse(description="Session not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    @action(
        detail=False,
        methods=["get"],
        url_path=r"(?P<session_id>[-\w]+)/courses",
        url_name="session-courses",
    )
    def get_courses(self, request, session_id=None, **kwargs):
        """
        Retrieve a list of courses associated with a specific session.
        """

        try:
            session = self.queryset.filter(id=session_id).first()
            if not session:
                return Response(
                    {"detail": "Session not found."}, status=status.HTTP_404_NOT_FOUND
                )

            courses = session.courses.all()

            serializer = CourseSerializer(courses, many=True)
            return Response(serializer.data)

        except Exception as e:
            return Response(
                {"detail": "Internal Server Error"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


@extend_schema_view(
    list=extend_schema(
        tags=["06 Student Sections"],
        summary="List all student sections",
        description="Retrieve a paginated list of all student sections in the system.",
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
            200: StudentSectionSerializer(many=True),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    create=extend_schema(
        tags=["06 Student Sections"],
        summary="Create a new student section",
        description="Create a new student section with the provided details.",
        request=StudentSectionSerializer,
        responses={
            201: OpenApiResponse(
                response=StudentSectionSerializer,
                description="Student section created successfully",
            ),
            400: OpenApiResponse(
                description="Invalid input. Missing required fields or invalid data format"
            ),
        },
    ),
    retrieve=extend_schema(
        tags=["06 Student Sections"],
        summary="Retrieve a student section",
        description="Retrieve detailed information about a specific student section by its ID.",
        responses={
            200: StudentSectionSerializer,
            404: OpenApiResponse(description="Student section not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    partial_update=extend_schema(
        tags=["06 Student Sections"],
        summary="Partially update a student section",
        description="Update one or more fields of an existing student section.",
        request=StudentSectionSerializer(partial=True),
        responses={
            200: StudentSectionSerializer,
            400: OpenApiResponse(description="Invalid input"),
            404: OpenApiResponse(description="Student section not found"),
        },
    ),
    destroy=extend_schema(
        tags=["06 Student Sections"],
        summary="Delete a student section",
        description="Delete a student section by ID. This action cannot be undone.",
        responses={
            204: OpenApiResponse(description="Student section deleted successfully"),
            404: OpenApiResponse(description="Student section not found"),
        },
    ),
)
class StudentSectionViewSet(viewsets.ModelViewSet):
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

    queryset = StudentSection.objects.all()
    serializer_class = StudentSectionSerializer
    permission_classes = (AllowAny,)
    pagination_class = PageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]
