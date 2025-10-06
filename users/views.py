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
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from users.models import CustomUser
from users.serializers import CustomUserSerializer

# Create your views here.

USER_EXAMPLE = {
    "email": "teacher@example.com",
    "first_name": "John",
    "last_name": "Doe",
    "user_type": "TEACHER",
    "username": "john.doe",
}


@extend_schema_view(
    list=extend_schema(
        tags=["Users"],
        summary="List all users",
        description="Retrieve a paginated list of all users in the system.",
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
            OpenApiParameter(
                name="user_type",
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description="Filter users by type (TEACHER or STUDENT)",
                enum=["TEACHER", "STUDENT"],
            ),
        ],
        responses={
            200: CustomUserSerializer(many=True),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    create=extend_schema(
        tags=["Users"],
        summary="Create a new user",
        description="""Create a new user with the provided details.
        Email is used as the primary identifier for authentication.
        Required fields include email, password, first_name, and last_name.
        """,
        request=CustomUserSerializer,
        responses={
            201: OpenApiResponse(
                response=CustomUserSerializer,
                description="User created successfully",
            ),
            400: OpenApiResponse(
                description="Invalid input. Missing required fields or invalid data format"
            ),
        },
    ),
    retrieve=extend_schema(
        tags=["Users"],
        summary="Retrieve a user",
        description="Retrieve detailed information about a specific user by their ID.",
        responses={
            200: CustomUserSerializer,
            404: OpenApiResponse(description="User not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    partial_update=extend_schema(
        tags=["Users"],
        summary="Partially update a user",
        description="Update one or more fields of an existing user. Password updates require the current password.",
        request=CustomUserSerializer(partial=True),
        responses={
            200: CustomUserSerializer,
            400: OpenApiResponse(description="Invalid input"),
            403: OpenApiResponse(
                description="Permission denied - can only modify own account unless admin"
            ),
            404: OpenApiResponse(description="User not found"),
        },
    ),
    destroy=extend_schema(
        tags=["Users"],
        summary="Delete a user",
        description="Delete a user by ID. This action cannot be undone and requires admin privileges.",
        responses={
            204: OpenApiResponse(description="User deleted successfully"),
            403: OpenApiResponse(
                description="Permission denied - requires admin privileges"
            ),
            404: OpenApiResponse(description="User not found"),
        },
    ),
)
class CustomUserViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing users.

    Provides CRUD operations for users including:
    - List all users
    - Create new users
    - Retrieve specific users
    - Update users
    - Delete users

    Users can be either teachers or students with different permissions
    and access levels in the system.
    """

    queryset = CustomUser.objects.all()
    serializer_class = CustomUserSerializer
    permission_classes = (AllowAny,)
    pagination_class = PageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]

    filterset_fields = {
        "user_type": ["exact"],
        "enrollments__section": ["exact", "isnull"],
        "enrollments__section__academic_term": ["exact"],
        "enrollments__enrollment_status": ["exact", "in"],
    }
    search_fields = ["username", "first_name", "last_name", "email"]
    ordering_fields = ["first_name", "last_name", "email", "username"]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]

    class Meta:
        model = CustomUser
        fields = ["id", "username", "email", "user_type"]

    @extend_schema(
        tags=["Users"],
        summary="Get current authenticated user",
        description="""
        Retrieve the currently authenticated user's information.

        This endpoint returns the complete user profile of the currently logged-in user.
        The user must be authenticated to access this endpoint.

        ## Response
        - 200: Success - Returns the user's profile information
        - 401: Unauthorized - If user is not authenticated
        """,
        responses={
            200: CustomUserSerializer,
            401: OpenApiResponse(
                description="Unauthorized",
                examples=[
                    OpenApiExample(name="unauthorized", value={"error": "Unauthorized"})
                ],
            ),
        },
    )
    @action(detail=False, methods=["get"])
    def me(self, request):
        """
        Retrieve the currently authenticated user's information.

        This endpoint returns the complete user profile of the currently logged-in user.
        The user must be authenticated to access this endpoint.

        ## Response
        - 200: Success - Returns the user's profile information
        - 401: Unauthorized - If user is not authenticated
        """
        if not request.user.is_authenticated:
            return Response(
                {"detail": "Authentication credentials were not provided."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        serializer = self.get_serializer(request.user)
        return Response(serializer.data)
