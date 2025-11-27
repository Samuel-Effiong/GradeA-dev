"""Views for user management and API endpoints.

This module contains the Django REST Framework viewset for managing users
(CustomUserViewSet) and OpenAPI schema extensions for documenting the
users endpoints.

It exposes endpoints to list, create, retrieve, update, delete users,
and a convenience `me` action to fetch the currently authenticated user's
profile.
"""

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
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
from rest_framework.exceptions import ParseError, ValidationError
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

from classrooms.models import EnrollmentStatusType, StudentCourse
from classrooms.serializers import StudentRegistrationCompletionSerializer
from users.models import CustomUser, PasswordChangeOTP, PasswordResetOTP
from users.permissions import IsSuperUser
from users.serializers import (
    ChangePasswordSerializer,
    CustomTokenObtainPairSerializer,
    CustomUserSerializer,
    OTPSerializer,
    ResetPasswordSerializer,
    VerifyCustomUserSerializer,
)
from users.services import send_user_activation_email

# Create your views here.

USER_EXAMPLE = {
    "email": "teacher@example.com",
    "first_name": "John",
    "last_name": "Doe",
    "user_type": "TEACHER",
    "username": "john.doe",
}


class BaseUserViewSet(viewsets.ModelViewSet):
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

    def get_permissions(self):
        """
        Allow unauthenticated access only for POST endpoints (public actions).
        All other requests require authentication.
        """
        if self.action in [
            "create",
            "register",
            "register_student",
            "verify",
            "otp",
            "reset_password",
        ]:
            permission_classes = [AllowAny]
        elif self.action == "list":
            permission_classes = [IsAuthenticated, IsSuperUser]
        else:
            permission_classes = [IsAuthenticated]

        return [permission() for permission in permission_classes]

    @extend_schema(exclude=True)
    def create(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            return Response(
                {"detail": "You do not have permission to create users."},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().create(request, *args, **kwargs)

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


class AuthViewSet(viewsets.ViewSet):
    """
    Handles user authentication actions
    """

    http_method_names = ["post", "options"]

    @extend_schema(
        tags=["Authentication"],
        summary="Verify email and activate account",
        description="""Verify the email address of a user.""",
        request=VerifyCustomUserSerializer,
        responses=VerifyCustomUserSerializer,
    )
    @action(
        detail=False,
        methods=["post"],
        url_path="verify",
        url_name="verify",
        permission_classes=[AllowAny],
    )
    def verify(self, request, **kwargs):
        email = request.data.get("email").strip()
        token = request.data.get("token").strip()

        if not email or not token:
            raise ParseError("Email and Token are required.")

        user = CustomUser.objects.filter(email=email, activation_token=token)
        if not user.exists():
            raise ParseError("Invalid email or token.")

        user = user.first()

        if user.activation_expires and timezone.now() > user.activation_expires:
            raise ParseError("Activation link has expired.")

        user.email_verified_at = timezone.now()
        user.activation_token = None
        user.activation_expires = None
        user.is_active = True
        user.save()

        user_data = CustomUserSerializer(user).data

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
                "user": user_data,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @extend_schema(
        tags=["Authentication"],
        summary="Send verification email",
        description="""Send a verification email to the specified email address.""",
        request=OTPSerializer,
        responses={
            202: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Verification email sent successfully",
                examples=[
                    OpenApiExample(
                        "Verification Email Sent",
                        value={"Detail": "Verification email sent successfully"},
                    )
                ],
            ),
        },
    )
    @action(
        detail=False,
        methods=["post", "options"],
        url_path="otp",
        url_name="otp",
        permission_classes=[AllowAny],
    )
    def otp(self, request, **kwargs):
        serializer = OTPSerializer(data=request.data)
        result = serializer.is_valid(raise_exception=False)

        if not result:
            raise ParseError(
                "Invalid OTP type. Valid values are `VERIFY_EMAIL` and `RESET_PASSWORD`"
            )

        email = serializer.validated_data.get("email")
        otp_type = serializer.validated_data.get("otp_type")

        try:
            user = CustomUser.objects.get(email=email)
        except CustomUser.DoesNotExist:
            return Response(
                {
                    "detail": "If an account with that email exists, an OTP has been sent."
                },
                status=status.HTTP_202_ACCEPTED,
            )

        if otp_type == "VERIFY_EMAIL":

            if user.email_verified_at and user.is_active:
                raise ParseError("Email already verified. Please login.")

            send_user_activation_email(user)

        elif otp_type == "RESET_PASSWORD":
            if not user.email_verified_at:
                raise ParseError("Email not verified.")

            otp_obj, created = PasswordResetOTP.objects.get_or_create(user=user)
            otp_code = otp_obj.generate_code()

            send_mail(
                subject="Your Password Reset OTP",
                message=f"Your Password Reset OTP is: {otp_code}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=False,
            )

        return Response(
            {"detail": "An OTP has been sent if an account with that email exists."},
            status=status.HTTP_202_ACCEPTED,
        )

    @extend_schema(
        tags=["Authentication"],
        summary="Reset the password using an OTP",
        description="""
        Resets a user's password after a valid OTP has been provided via the `forgot-password` endpoint.
        This endpoint is public and does not require authentication.
        """,
        request=ResetPasswordSerializer,
        responses={
            200: {"description": "Password has been reset successfully."},
            400: {"description": "Invalid email, OTP code, or new password."},
        },
    )
    @action(
        detail=False,
        methods=["post", "options"],
        url_path="reset-password",
        url_name="reset-password",
        permission_classes=[AllowAny],
    )
    def reset_password(self, request, **kwargs):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data.get("email")
        otp = serializer.validated_data.get("otp")
        new_password = serializer.validated_data.get("new_password")

        try:
            user = CustomUser.objects.get(email=email)
            otp_obj = PasswordResetOTP.objects.get(user=user, code=otp)
        except (CustomUser.DoesNotExist, PasswordResetOTP.DoesNotExist):
            raise ParseError("Invalid email, OTP code, or new password.") from Exception

        if not otp_obj.is_valid():
            otp_obj.delete()
            raise ParseError("Invalid email, OTP code, or new password.")

        user.set_password(new_password)
        user.save()

        otp_obj.delete()
        return Response(
            {"detail": "Password has been reset successfully."},
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Authentication"],
        summary="Request a password change OTP",
        description="""
            Sends a one-time password (OTP) to the authenticated user's email address.
            This is a preliminary step for an authenticated user to change their password.
            The user's email must be verified to use this endpoint.
            """,
        request=None,
        responses={
            200: {"description": "OTP code sent successfully"},
            403: {"description": "User's email is not verified"},
        },
    )
    @action(detail=False, methods=["post"], url_path="request-change-password")
    def request_change_password(self, request, *args, **kwargs):
        user = request.user

        # Ensure the user's email is verified before allowing password changes
        if not user.email_verified_at and not user.is_active:
            raise ParseError("Your email address is not verified")

        with transaction.atomic():
            otp_obj, created = PasswordChangeOTP.objects.get_or_create(user=user)
            otp = otp_obj.generate_code()

        send_mail(
            subject="Your Password Change OTP",
            message=f"Your OTP for password change is: {otp}",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )

        return Response(
            {"detail": "An OTP code has been sent to your email"},
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Authentication"],
        summary="Change password using an OTP",
        description="""
        Changes the authenticated user's password after a valid OTP has been provided.
        """,
        request=ChangePasswordSerializer,
        responses={
            200: {"description": "Password changed successfully"},
            400: {"description": "Invalid OTP or expired OTP"},
        },
    )
    @action(detail=False, methods=["post", "options"], url_path="change-password")
    def change_password(self, request, **kwargs):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        otp = serializer.validated_data.get("otp")
        current_password = serializer.validated_data.get("current_password")
        new_password = serializer.validated_data.get("new_password")

        if not user.check_password(current_password):
            raise ParseError("Incorrect current password. Please try again.")

        try:
            otp_obj = PasswordChangeOTP.objects.get(user=user, code=otp)
        except PasswordChangeOTP.DoesNotExist:
            raise ParseError(
                "Invalid OTP or expired OTP. Please try again."
            ) from Exception

        if not otp_obj.is_valid():
            otp_obj.delete()
            raise ParseError("Invalid OTP or expired OTP. Please try again.")

        user.set_password(new_password)
        user.save()

        otp_obj.delete()

        return Response({"detail": "Password changed successfully"})

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
    @action(detail=False, methods=["post"], url_path="logout")
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
            raise ParseError("Refresh token is required.") from KeyError
        except TokenError:
            raise ParseError("Invalid or expired token") from TokenError

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
        url_path="register",
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
        request=StudentRegistrationCompletionSerializer,
        responses={
            200: OpenApiResponse(
                description="Student registration completed successfully",
            ),
            400: OpenApiResponse(description="Invalid or expired token"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[AllowAny],
        url_path="register/student",
    )
    def register_student(self, request, *args, **kwargs):
        try:
            with transaction.atomic():
                serializer = StudentRegistrationCompletionSerializer(data=request.data)

                if not serializer.is_valid():
                    raise ValidationError(serializer.errors)

                token = serializer.validated_data["token"]

                # Find user by activation token
                user = CustomUser.objects.filter(
                    activation_token=token, is_active=False
                ).first()

                if not user:
                    raise ParseError("Invalid or expired activation token")

                if user.activation_expires < timezone.now():
                    renewal_url = request.build_absolute_uri(
                        "/course/student/renew-student-token"
                    )

                    return Response(
                        {
                            "detail": "Activation token has expired.",
                            "renewal_url": renewal_url,
                            "expired_token": token,
                            "message": "Please request a new activation link",
                        }
                    )

                user.first_name = serializer.validated_data["first_name"]
                user.last_name = serializer.validated_data["last_name"]
                user.set_password(serializer.validated_data["password"])
                user.is_active = True
                user.activation_token = None
                user.activation_expire = None
                user.email_verified_at = timezone.now()
                user.save()

                StudentCourse.objects.filter(student=user, is_active=False).update(
                    is_active=True, enrollment_status=EnrollmentStatusType.ENROLLED
                )

                return Response(
                    {"detail": "Student registration completed successfully"},
                    status=status.HTTP_200_OK,
                )
        except Exception as e:
            return Response(
                {"detail": f"Internal Server Error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


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

    serializer_class = CustomTokenObtainPairSerializer


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
