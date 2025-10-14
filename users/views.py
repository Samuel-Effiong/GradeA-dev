# import secrets

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
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
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from users.models import CustomUser, PasswordChangeOTP, PasswordResetOTP
from users.serializers import (
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
        tags=["Users"],
        summary="Verify email and activate account",
        description="""Verify the email address of a user.""",
        request=VerifyCustomUserSerializer,
        responses={
            202: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="User successfully verified",
                examples=[
                    OpenApiExample(
                        "Successful Activation",
                        value={"access": "string", "refresh": "string"},
                    )
                ],
            ),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="User account activation failed.",
                examples=[
                    OpenApiExample(
                        "Unsuccessful Activation",
                        value={"Detail": "User account activation failed"},
                    )
                ],
            ),
        },
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
            return Response(
                {"detail": "Email and Token are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = CustomUser.objects.filter(email=email, activation_token=token)
        if not user.exists():
            return Response(
                {"detail": "Invalid email or token."},
                status=status.HTTP_404_NOT_FOUND,
            )

        user = user.first()

        if user.activation_expires and timezone.now() > user.activation_expires:
            return Response(
                {"detail": "Activation link has expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.email_verified_at = timezone.now()
        user.activation_token = None
        user.activation_expires = None
        user.is_active = True
        user.save()

        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "refresh": str(refresh),
                "access": str(refresh.access_token),
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @extend_schema(
        tags=["Users"],
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
            return Response(
                {
                    "detail": "Invalid OTP type, valid values are `VERIFY_EMAIL` and `RESET_PASSWORD`"
                },
                status=status.HTTP_400_BAD_REQUEST,
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
                return Response(
                    {"detail": "Email already verified."},
                    status=status.HTTP_404_NOT_FOUND,
                )

            send_user_activation_email(user)

        elif otp_type == "RESET_PASSWORD":
            if not user.email_verified_at:
                return Response(
                    {"detail": "Email not verified."}, status=status.HTTP_403_FORBIDDEN
                )

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
        tags=["Users"],
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
            return Response(
                {"detail": "Invalid email, OTP code, or new password."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not otp_obj.is_valid():
            otp_obj.delete()
            return Response(
                {"detail": "Invalid Email or OTP code has expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(new_password)
        user.save()

        otp_obj.delete()
        return Response(
            {"detail": "Password has been reset successfully."},
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Users"],
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
    @action(
        detail=False,
        methods=["post"],
    )
    def request_change_password(self, request, *args, **kwargs):
        user = request.user

        # Ensure the user's email is verified before allowing password changes
        if not user.email_verified_at and not user.is_active:
            return Response({"detail": "Your email address is not verified"})

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
