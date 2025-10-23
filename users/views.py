"""Views for user management and API endpoints.

This module contains the Django REST Framework viewset for managing users
(CustomUserViewSet) and OpenAPI schema extensions for documenting the
users endpoints.

It exposes endpoints to list, create, retrieve, update, delete users,
and a convenience `me` action to fetch the currently authenticated user's
profile.
"""

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiRequest,
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
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import (
    TokenObtainPairView as BaseTokenObtainPairView,
)
from rest_framework_simplejwt.views import TokenRefreshView as BaseTokenRefreshView

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
    permission_classes = (IsAuthenticated,)
    pagination_class = PageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]

    filterset_fields = {
        "user_type": ["exact"],
        "enrollments__course": ["exact", "isnull"],
        "enrollments__course__session": ["exact"],
        "enrollments__enrollment_status": ["exact", "in"],
    }
    search_fields = ["username", "first_name", "last_name", "email"]
    ordering_fields = ["first_name", "last_name", "email", "username"]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]

    class Meta:
        """Meta configuration describing model and exposed fields for the viewset.

        Although DRF's ModelViewSet does not require an inner Meta normally,
        this inner class documents which model and fields are intended to be
        surfaced by this viewset for clarity and tooling.
        """

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

    @extend_schema(
        tags=["Authentication"],
        summary="Log out the current user",
        description="""
        "This endpoint logs out the currently authenticated user by **blacklisting their refresh token**. "
        "Once the refresh token is blacklisted, it can no longer be used to obtain new access tokens.\n\n"
        "**Note:** The access token will naturally expire and does not need to be explicitly invalidated."
        """,
        request=OpenApiRequest(
            request={
                "type": "object",
                "properties": {
                    "refresh": {
                        "type": "string",
                        "description": "The refresh token to be blacklisted.",
                        "example": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                    },
                },
                "required": ["refresh"],
            }
        ),
        responses={
            205: OpenApiResponse(
                response={"type": "null"},
                description="Successfully logged out. Refresh token has been blacklisted.",
            ),
            400: OpenApiResponse(
                response={
                    "type": "object",
                    "properties": {
                        "detail": {
                            "type": "string",
                            "example": "Refresh token is required.",
                        }
                    },
                },
                description="Bad request. Missing or invalid refresh token.",
            ),
            401: OpenApiResponse(
                response={
                    "type": "object",
                    "properties": {
                        "detail": {
                            "type": "string",
                            "example": "Authentication credentials were not provided.",
                        }
                    },
                },
                description="User is not authenticated.",
            ),
        },
        examples=[
            OpenApiExample(
                "Valid logout request",
                value={"refresh": "eyJ0eXAiOiJKV1QiLCJh..."},
                request_only=True,
            ),
            OpenApiExample(
                "Missing refresh token",
                value={"detail": "Refresh token is required."},
                response_only=True,
                status_codes=["400"],
            ),
        ],
    )
    @action(detail=False, methods=["post"], url_path="auth/logout")
    def logout(self, request):
        """
        Log out the currently authenticated user.

        This endpoint logs out the user by invalidating their authentication token.
        The user must be authenticated to access this endpoint.

        ## Response
        - 204: No Content - Successfully logged out
        - 401: Unauthorized - If user is not authenticated
        """
        try:
            refresh_token = request.data["refresh"]
            token = RefreshToken(refresh_token)
            token.blacklist()
        except KeyError:
            return Response(
                {"detail": "Refresh token is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except TokenError:
            return Response(
                {"detail": "Invalid or expired token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(status=status.HTTP_205_RESET_CONTENT)

    @extend_schema(
        tags=["Authentication"],
        summary="Register a new Teacher user",
        description="""
        Register a new user with TEACHER role.

        Required fields:
        - email: User's email address (must be unique)
        - password: User's password
        - first_name: User's first name
        - last_name: User's last name

        Note: The user_type will be automatically set to TEACHER.
        """,
        request=CustomUserSerializer,
        responses={
            201: OpenApiResponse(
                response=CustomUserSerializer,
                description="Teacher user created successfully",
            ),
            400: OpenApiResponse(description="Invalid input data"),
            500: OpenApiResponse(description="Internal server error"),
        },
    )
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[AllowAny],
        url_path="auth/register",
    )
    def register(self, request, *args, **kwargs):
        """
        Register a new Teacher user

        This endpoint allows registration of new users with TEACHER role only.
        """
        request.data.pop("user_type", None)
        serializer = CustomUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        serializer.save()

        return Response(serializer.data)

    @extend_schema(
        tags=["Authentication"],
        summary="Register a new Student user",
        description="""
        Register a new user with STUDENT role.

        Required fields:
        - email: User's email address (must be unique)
        - password: User's password
        - first_name: User's first name
        """,
    )
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[AllowAny],
        url_path="auth/register/student",
    )
    def register_student(self, request, *args, **kwargs):
        pass


@extend_schema(
    tags=["Authentication"],
    summary="Obtain JWT token pair",
    description="""
    Authenticate a user and return a JWT token pair.

    Returns:
    - access: Access token for API authentication
    - refresh: Refresh token to obtain new access tokens
    """,
    responses={
        200: OpenApiResponse(
            response={
                "type": "object",
                "properties": {
                    "access": {"type": "string"},
                    "refresh": {"type": "string"},
                },
            },
            description="Successfully authenticated",
        ),
        401: OpenApiResponse(description="Invalid credentials"),
    },
)
class TokenObtainPairView(BaseTokenObtainPairView):
    """
    Custom view for obtaining JWT token pairs
    """

    pass


@extend_schema(
    tags=["Authentication"],
    summary="Refresh JWT token pair",
    description="""

    """,
    request=OpenApiRequest(
        request={
            "type": "object",
            "properties": {
                "refresh": {
                    "type": "string",
                    "description": "The refresh token to be blacklisted.",
                    "example": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                },
            },
            "required": ["refresh"],
        }
    ),
    responses={
        200: OpenApiResponse(
            response={
                "type": "object",
                "properties": {
                    "access": {"type": "string"},
                    "refresh": {"type": "string"},
                },
            },
            description="Successfully authenticated",
        ),
        401: OpenApiResponse(description="Invalid credentials"),
    },
)
class TokenRefreshView(BaseTokenRefreshView):
    pass
