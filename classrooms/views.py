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

from .models import (  # , Classroom, ClassroomSettings
    AcademicTerm,
    Section,
    StudentSection,
)
from .serializers import (  # ClassroomSerializer,; ClassroomSettingsSerializer,
    AcademicTermSerializer,
    SectionSerializer,
    StudentSectionSerializer,
)


@extend_schema_view(
    list=extend_schema(
        tags=["01 Academic Term"],
        summary="List all academic terms",
        description="Retrieve a paginated list of all academic terms in the system.",
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
            200: AcademicTermSerializer(many=True),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    create=extend_schema(
        tags=["01 Academic Term"],
        summary="Create a new academic term",
        description="Create a new academic term with the provided details.",
        request=AcademicTermSerializer,
        responses={
            201: OpenApiResponse(
                response=AcademicTermSerializer,
                description="Academic term created successfully",
            ),
            400: OpenApiResponse(
                description="Invalid input. Missing required fields or invalid data format"
            ),
        },
    ),
    retrieve=extend_schema(
        tags=["01 Academic Term"],
        summary="Retrieve an academic term",
        description="Retrieve detailed information about a specific academic term by its ID.",
        responses={
            200: AcademicTermSerializer,
            404: OpenApiResponse(description="Academic term not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    partial_update=extend_schema(
        tags=["01 Academic Term"],
        summary="Partially update an academic term",
        description="Update one or more fields of an existing academic term.",
        request=AcademicTermSerializer(partial=True),
        responses={
            200: AcademicTermSerializer,
            400: OpenApiResponse(description="Invalid input"),
            404: OpenApiResponse(description="Academic term not found"),
        },
    ),
    destroy=extend_schema(
        tags=["01 Academic Term"],
        summary="Delete an academic term",
        description="Delete an academic term by ID. This action cannot be undone.",
        responses={
            204: OpenApiResponse(description="Academic term deleted successfully"),
            404: OpenApiResponse(description="Academic term not found"),
        },
    ),
)
class AcademicTermViewSet(viewsets.ModelViewSet):
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

    queryset = AcademicTerm.objects.all()
    serializer_class = AcademicTermSerializer
    permission_classes = (AllowAny,)
    pagination_class = PageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]

    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    ordering_fields = [
        "name",
        "start_date",
        "end_date",
    ]
    search_fields = [
        "name",
    ]
    ordering = (
        "name",
        "start_date",
    )


# @extend_schema_view(
#     list=extend_schema(
#         tags=["02 Classroom"],
#         summary="List all classrooms",
#         description="Retrieve a paginated list of all classrooms in the system.",
#         parameters=[
#             OpenApiParameter(
#                 name="page",
#                 type=OpenApiTypes.INT,
#                 location=OpenApiParameter.QUERY,
#                 description="Page number for pagination",
#             ),
#             OpenApiParameter(
#                 name="page_size",
#                 type=OpenApiTypes.INT,
#                 location=OpenApiParameter.QUERY,
#                 description="Number of results per page",
#             ),
#         ],
#         responses={
#             200: ClassroomSerializer(many=True),
#             500: OpenApiResponse(description="Internal Server Error"),
#         },
#     ),
#     create=extend_schema(
#         tags=["02 Classroom"],
#         summary="Create a new classroom",
#         description="Create a new classroom with the provided details.",
#         request=ClassroomSerializer,
#         responses={
#             201: OpenApiResponse(
#                 response=ClassroomSerializer,
#                 description="Classroom created successfully",
#             ),
#             400: OpenApiResponse(
#                 description="Invalid input. Missing required fields or invalid data format"
#             ),
#         },
#     ),
#     retrieve=extend_schema(
#         tags=["02 Classroom"],
#         summary="Retrieve a classroom",
#         description="Retrieve detailed information about a specific classroom by its ID.",
#         responses={
#             200: ClassroomSerializer,
#             404: OpenApiResponse(description="Classroom not found"),
#             500: OpenApiResponse(description="Internal Server Error"),
#         },
#     ),
#     partial_update=extend_schema(
#         tags=["02 Classroom"],
#         summary="Partially update a classroom",
#         description="Update one or more fields of an existing classroom.",
#         request=ClassroomSerializer(partial=True),
#         responses={
#             200: ClassroomSerializer,
#             400: OpenApiResponse(description="Invalid input"),
#             404: OpenApiResponse(description="Classroom not found"),
#         },
#     ),
#     destroy=extend_schema(
#         tags=["02 Classroom"],
#         summary="Delete a classroom",
#         description="Delete a classroom by ID. This action cannot be undone.",
#         responses={
#             204: OpenApiResponse(description="Classroom deleted successfully"),
#             404: OpenApiResponse(description="Classroom not found"),
#         },
#     ),
# )
# class ClassroomViewSet(viewsets.ModelViewSet):
#     """
#     API endpoint for managing classrooms.
#
#     Provides CRUD operations for classrooms including:
#     - List all classrooms
#     - Create new classrooms
#     - Retrieve specific classrooms
#     - Update classrooms
#     - Delete classrooms
#
#     Classrooms represent a teacher's class for a specific academic term.
#     """
#
#     queryset = Classroom.objects.all()
#     serializer_class = ClassroomSerializer
#     permission_classes = (AllowAny,)
#     pagination_class = PageNumberPagination
#     http_method_names = ["get", "head", "post", "delete", "patch", "options"]
#

# @extend_schema_view(
#     list=extend_schema(
#         tags=["02 Classroom"],
#         summary="List all classroom settings",
#         description="Retrieve a paginated list of all classroom settings in the system.",
#         parameters=[
#             OpenApiParameter(
#                 name="page",
#                 type=OpenApiTypes.INT,
#                 location=OpenApiParameter.QUERY,
#                 description="Page number for pagination",
#             ),
#             OpenApiParameter(
#                 name="page_size",
#                 type=OpenApiTypes.INT,
#                 location=OpenApiParameter.QUERY,
#                 description="Number of results per page",
#             ),
#         ],
#         responses={
#             200: ClassroomSettingsSerializer(many=True),
#             500: OpenApiResponse(description="Internal Server Error"),
#         },
#     ),
#     create=extend_schema(
#         tags=["02 Classroom"],
#         summary="Create new classroom settings",
#         description="Create new classroom settings with the provided details.",
#         request=ClassroomSettingsSerializer,
#         responses={
#             201: OpenApiResponse(
#                 response=ClassroomSettingsSerializer,
#                 description="Classroom settings created successfully",
#             ),
#             400: OpenApiResponse(
#                 description="Invalid input. Missing required fields or invalid data format"
#             ),
#         },
#     ),
#     retrieve=extend_schema(
#         tags=["02 Classroom"],
#         summary="Retrieve classroom settings",
#         description="Retrieve detailed information about specific classroom settings by its ID.",
#         responses={
#             200: ClassroomSettingsSerializer,
#             404: OpenApiResponse(description="Classroom settings not found"),
#             500: OpenApiResponse(description="Internal Server Error"),
#         },
#     ),
#     partial_update=extend_schema(
#         tags=["02 Classroom"],
#         summary="Partially update classroom settings",
#         description="Update one or more fields of existing classroom settings.",
#         request=ClassroomSettingsSerializer(partial=True),
#         responses={
#             200: ClassroomSettingsSerializer,
#             400: OpenApiResponse(description="Invalid input"),
#             404: OpenApiResponse(description="Classroom settings not found"),
#         },
#     ),
#     destroy=extend_schema(
#         tags=["02 Classroom"],
#         summary="Delete classroom settings",
#         description="Delete classroom settings by ID. This action cannot be undone.",
#         responses={
#             204: OpenApiResponse(description="Classroom settings deleted successfully"),
#             404: OpenApiResponse(description="Classroom settings not found"),
#         },
#     ),
# )
# class ClassroomSettingsViewSet(viewsets.ModelViewSet):
#     """
#     API endpoint for managing classroom settings.
#
#     Provides CRUD operations for classroom settings including:
#     - List all classroom settings
#     - Create new classroom settings
#     - Retrieve specific classroom settings
#     - Update classroom settings
#     - Delete classroom settings
#
#     Classroom settings store configuration options for a classroom.
#     """
#
#     queryset = ClassroomSettings.objects.all()
#     serializer_class = ClassroomSettingsSerializer
#     permission_classes = (AllowAny,)
#     pagination_class = PageNumberPagination
#     http_method_names = ["get", "head", "post", "delete", "patch", "options"]


@extend_schema_view(
    list=extend_schema(
        tags=["03 Sections"],
        summary="List all sections",
        description="Retrieve a paginated list of all sections in the system.",
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
            200: SectionSerializer(many=True),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    create=extend_schema(
        tags=["03 Sections"],
        summary="Create a new section",
        description="Create a new section with the provided details.",
        request=SectionSerializer,
        responses={
            201: OpenApiResponse(
                response=SectionSerializer,
                description="Section created successfully",
            ),
            400: OpenApiResponse(
                description="Invalid input. Missing required fields or invalid data format"
            ),
        },
    ),
    retrieve=extend_schema(
        tags=["03 Sections"],
        summary="Retrieve a section",
        description="Retrieve detailed information about a specific section by its ID.",
        responses={
            200: SectionSerializer,
            404: OpenApiResponse(description="Section not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    partial_update=extend_schema(
        tags=["03 Sections"],
        summary="Partially update a section",
        description="Update one or more fields of an existing section.",
        request=SectionSerializer(partial=True),
        responses={
            200: SectionSerializer,
            400: OpenApiResponse(description="Invalid input"),
            404: OpenApiResponse(description="Section not found"),
        },
    ),
    destroy=extend_schema(
        tags=["03 Sections"],
        summary="Delete a section",
        description="Delete a section by ID. This action cannot be undone.",
        responses={
            204: OpenApiResponse(description="Section deleted successfully"),
            404: OpenApiResponse(description="Section not found"),
        },
    ),
)
class SectionViewSet(viewsets.ModelViewSet):
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

    queryset = Section.objects.all()
    serializer_class = SectionSerializer
    permission_classes = (AllowAny,)
    pagination_class = PageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]

    filterset_fields = ["academic_term", "is_active"]
    ordering_fields = ["name", "start_date"]
    search_fields = [
        "name",
        "description",
    ]
    ordering = (
        "name",
        "created_at",
    )

    @extend_schema(tags=["03 Sections"])
    @action(
        detail=False, methods=["post"], url_path="add_student", url_name="add_student"
    )
    def add_student(self, request):
        """Onboard students to a section."""
        return Response({}, status=status.HTTP_200_OK)


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
