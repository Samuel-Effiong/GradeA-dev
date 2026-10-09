"""Views for user management and API endpoints.
Help o father
This module contains the Django REST Framework viewset for managing users
(CustomUserViewSet) and OpenAPI schema extensions for documenting the
users endpoints.

It exposes endpoints to list, create, retrieve, update, delete users,
and a convenience `me` action to fetch the currently authenticated user's
profile.
"""

import logging
import math
import time
import uuid
from datetime import timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiRequest,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action  # , api_view
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotFound,
    ParseError,
    PermissionDenied,
    ValidationError,
)
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import (
    TokenObtainPairView as BaseTokenObtainPairView,
)
from rest_framework_simplejwt.views import TokenRefreshView as BaseTokenRefreshView

from audit import history
from audit.emitter import emit
from audit.enums import AuditAction, AuditOutcome, ErrorClass
from AutoGrader.cache_generation import SCOPE_USER, versioned_key
from AutoGrader.dispatch import safe_delay
from AutoGrader.error_messages import describe_user_error
from AutoGrader.pagination import StandardPageNumberPagination
from AutoGrader.reason_codes import add_coded_envelope
from AutoGrader.tasks import send_email_task
from billing.services import AnalyticsService
from classrooms.models import (
    EnrollmentStatusType,
    StudentCourse,
    teacher_course_access_q,
)
from classrooms.permissions import IsSuperAdmin, IsTeacher
from classrooms.serializers import SchoolAdminRegistrationCompletionSerializer
from classrooms.services import OLD_INVITATION_CLOSED_MESSAGE
from students import item_retry
from students.item_results import UNCLASSIFIED, failure_summary, item_result
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BatchUploadSession,
)
from students.task_access import teacher_may_reach
from students.task_context import get_session_context, get_task_context
from students.task_tracking import (
    TERMINAL_TASK_STATUSES,
    cancel_processing_task,
    get_processing_task,
    normalize_processing_task_status,
)
from users.admin_power import holds_admin_power as _holds_admin_power
from users.auth_audit import account_for_email, sign_in_failed, sign_in_succeeded
from users.exceptions import EnvelopedThrottled
from users.filters import UserEnrollmentFilter
from users.mixins import UserCacheMixin
from users.models import (
    BetaWhitelist,
    CustomUser,
    PasswordChangeOTP,
    PasswordResetOTP,
    RegistrationMethod,
    Settings,
    UserGoogleCredentials,
    UserTypes,
    Waitlist,
)
from users.permissions import HasCreditBalance
from users.serializers import (  # BatchSessionResultTaskEntrySerializer,; TaskContextSerializer,
    BatchSessionCancelSerializer,
    BatchSessionResultSerializer,
    BetaWhitelistSerializer,
    ChangePasswordSerializer,
    CustomTokenObtainPairSerializer,
    CustomUserSerializer,
    EpochTokenRefreshSerializer,
    GoogleUserSerializer,
    OTPSerializer,
    ResetPasswordSerializer,
    SettingsSerializer,
    StudentNameSerializer,
    TaskCancelSerializer,
    TaskStatusSerializer,
    VerifyCustomUserSerializer,
    WaitlistSerializer,
)
from users.services import send_user_activation_email, stamp_last_login
from users.tasks import sync_user_to_mailerlite
from users.throttling import (
    GoogleAuthThrottle,
    LoginThrottle,
    OTPRequestThrottle,
    PasswordResetThrottle,
    RegisterThrottle,
    VerifyEmailThrottle,
    clear_verify_failures,
    lock_verify_address,
    reserve_verify_attempt,
    verify_attempt_over_budget,
    verify_budget_spent,
    verify_lock_until,
)
from users.tokens import EpochRefreshToken

logger = logging.getLogger(__name__)

#: H-153: one line per rename of a student, with ids only (who, whom,
#: through which courses). Never a name and never an address. On this line
#: it is the record of a rename; the audit event follows on the Phase 2 line.
student_names_logger = logging.getLogger("users.student_names")

#: H-153: what a caller is told when the new name is already held in a
#: course the caller does not teach. The refusal that quotes the name is
#: only ever sent to a teacher of the course where the clash is.
NAME_HELD_ELSEWHERE_MESSAGE = "This name cannot be used for this student."

# H-43: the ONE reply /auth/otp gives for every 202 - an unknown address, a
# sent code, and a locked reset alike - so its text says nothing about
# whether an account exists.
OTP_SENT_DETAIL = "An OTP has been sent if an account with that email exists."
GOOGLE_SIGN_IN_FAILED = "Google sign-in failed. Please try again."

# Founder-approved wording (2026-09-28) for the password-reset email.
# Wording only: no link.
RESET_EMAIL_WARNING = (
    "Didn't ask for this? Someone may be trying to get into your account. "
    "Don't share this code with anyone, including Grade A+ staff. Your "
    "password hasn't been changed. If you didn't request this, you can ignore "
    "this email."
)

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
class CustomUserViewSet(UserCacheMixin, viewsets.ModelViewSet):
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
    pagination_class = StandardPageNumberPagination
    http_method_names = ["get", "head", "post", "delete", "patch", "options"]

    # Not filterset_fields: the enrollments__* lookups joined every
    # enrollment an account had, other teachers' included, and get_object()
    # applies filters too - a yes/no oracle on other tenants' enrollments.
    filterset_class = UserEnrollmentFilter
    search_fields = ["first_name", "last_name", "email"]
    ordering_fields = ["first_name", "last_name", "email", "username"]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]

    def get_permissions(self):
        """
        Allow unauthenticated access only for POST endpoints (public actions).
        All other requests require authentication.
        """
        if self.action in ["list", "create", "destroy"]:
            permission_classes = [IsAuthenticated, IsSuperAdmin]
        else:
            permission_classes = [IsAuthenticated]

        return [permission() for permission in permission_classes]

    def get_queryset(self):
        """
        Limit which users a request may see or act on.

        Without this the viewset exposed `CustomUser.objects.all()` behind a
        bare `IsAuthenticated`, so any logged-in user could read, edit or
        delete any other user by guessing a UUID.

        Roles get the narrowest set that keeps their existing flows working.
        Students carry `school=NULL` (they are attached to a course, not a
        school), so a school admin has to reach them through the course's
        teacher rather than through `school_id`.

        Never raise from here: `UserCacheMixin.get_cache_key` calls
        `get_queryset()` for the model name before permissions run, so an
        exception would surface as a 500 instead of a 401/403.
        """
        queryset = CustomUser.objects.all()
        user = self.request.user

        if not user or not user.is_authenticated:
            return CustomUser.objects.none()

        # Mirrors classrooms.permissions.IsSuperAdmin, which requires both
        # flags. Checking only is_superuser would let the two disagree.
        if user.is_superuser and user.user_type == UserTypes.SUPER_ADMIN:
            return queryset

        if user.user_type == UserTypes.SCHOOL_ADMIN and user.school_id:
            return queryset.filter(
                Q(pk=user.pk)
                | Q(school_id=user.school_id)
                | Q(enrollments__course__teacher__school_id=user.school_id)
            ).distinct()

        if user.user_type == UserTypes.TEACHER:
            return queryset.filter(
                Q(pk=user.pk)
                | teacher_course_access_q(user, prefix="enrollments__course__")
            ).distinct()

        return queryset.filter(pk=user.pk)

    def partial_update(self, request, *args, **kwargs):
        """
        Being able to *see* a user is not permission to *edit* them.

        The queryset above deliberately lets teachers and school admins read
        their students, so without this check that read access would also
        grant write access over those accounts.
        """
        instance = self.get_object()
        is_super_admin = (
            request.user.is_superuser
            and request.user.user_type == UserTypes.SUPER_ADMIN
        )

        if instance.pk != request.user.pk and not is_super_admin:
            raise PermissionDenied("You can only modify your own account.")

        return super().partial_update(request, *args, **kwargs)

    @extend_schema(
        tags=["Users"],
        summary="Rename a student (their teacher, or a super admin)",
        request=StudentNameSerializer,
        responses={200: StudentNameSerializer},
    )
    @action(
        detail=True,
        methods=["patch"],
        url_path="student-name",
        url_name="student-name",
    )
    def student_name(self, request, pk=None):
        """H-153: a teacher names or renames a student.

        A student does not name themselves, and the account edit above
        refuses any change to a student's name whoever asks. This is the
        one way a student's name changes after the account exists.

        Who may: a teacher who can reach a course the student is CURRENTLY
        in (enrolled or pending), so either of two teachers who share a
        student; and a super admin, who is the only one for a student with
        no current teacher. A school admin may not; nor the student.

        What the answers tell: `get_object` answers 404 for every account
        the caller cannot already read, existing or not, so 403 is only
        ever said about an account the caller can see anyway.
        """
        student = self.get_object()
        actor = request.user
        is_super_admin = actor.is_superuser and actor.user_type == UserTypes.SUPER_ADMIN
        if not is_super_admin and actor.user_type != UserTypes.TEACHER:
            raise PermissionDenied(
                "Only a student's teacher can change the student's name."
            )
        if student.user_type != UserTypes.STUDENT:
            raise ValidationError({"detail": "Only a student can be renamed here."})

        # The courses through which this teacher currently has the student.
        through = []
        if not is_super_admin:
            through = list(
                StudentCourse.objects.filter(
                    teacher_course_access_q(actor, prefix="course__"),
                    student=student,
                    enrollment_status__in=(
                        EnrollmentStatusType.ENROLLED,
                        EnrollmentStatusType.PENDING,
                    ),
                ).values_list("course_id", flat=True)
            )
            if not through:
                raise PermissionDenied(
                    "Only a student's current teacher can change the student's name."
                )

        serializer = StudentNameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        names = {
            "first_name": serializer.validated_data["first_name"],
            "middle_name": serializer.validated_data.get("middle_name", ""),
            "last_name": serializer.validated_data["last_name"],
        }

        with transaction.atomic():
            # One exact name per course, in EVERY course the student has a
            # place in, whatever its status: the enrolment's own check
            # (StudentCourse.clean, run on every save) counts every row, so
            # anything narrower here would leave rows that can no longer
            # be saved.
            # The lock is on the enrolment rows only (`of`), and nothing
            # that may be empty is joined: PostgreSQL refuses FOR UPDATE
            # through a link that can be null, and a course's session can
            # (docs/evidence/h38_part2/select_for_update_outer_join_regression.md;
            # this route's first version joined the session and answered
            # 500 on every rename).
            enrolments = list(
                StudentCourse.objects.select_for_update(of=("self",))
                .filter(student=student)
                .select_related("course")
            )
            held_elsewhere = False
            for enrolment in enrolments:
                clash = StudentCourse.find_name_conflicts(
                    course=enrolment.course,
                    exclude_student_id=student.pk,
                    **names,
                ).exists()
                if not clash:
                    continue
                if is_super_admin or enrolment.course_id in through:
                    full_name = " ".join(part for part in names.values() if part)
                    raise ValidationError(
                        {
                            "detail": (
                                f"A student with the exact name {full_name!r} "
                                "is already enrolled in this course."
                            )
                        }
                    )
                held_elsewhere = True
            if held_elsewhere:
                raise ValidationError({"detail": NAME_HELD_ELSEWHERE_MESSAGE})

            for field, value in names.items():
                setattr(student, field, value)
            student.save(update_fields=list(names))

        student_names_logger.info(
            "student_renamed actor=%s student=%s courses=%s",
            actor.pk,
            student.pk,
            ",".join(str(course_id) for course_id in through) or "none",
        )
        return Response({"id": str(student.pk), **names}, status=status.HTTP_200_OK)

    # @extend_schema(exclude=True)
    def create(self, request, *args, **kwargs):
        if (
            not request.user.is_superuser
            and request.user.user_type != UserTypes.SUPER_ADMIN
        ):
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
    @action(detail=False, methods=["GET"])
    def me(self, request, *args, **kwargs):
        """
        Retrieve the currently authenticated user's information.

        This endpoint returns the complete user profile of the currently logged-in user.
        The user must be authenticated to access this endpoint.

        ## Response
        - 200: Success - Returns the user's profile information
        - 401: Unauthorized - If user is not authenticated
        """

        # `usr` alone: this payload is the user's own row and nothing else,
        # and a CustomUser save bumps exactly that generation.
        cache_key = versioned_key(
            f"user:user_id__{request.user.id}", [(SCOPE_USER, request.user.id)]
        )
        data = cache.get(cache_key)

        if data is None:
            serializer = self.get_serializer(request.user)
            data = serializer.data
            cache.set(cache_key, data, getattr(settings, "CACHE_TTL", 60 * 5))

        return Response(data)


@extend_schema_view(
    list=extend_schema(
        tags=["Settings"],
        summary="List all user settings",
        description="""
        Retrieve a paginated list of all user settings in the system.

        This endpoint is restricted to SuperAdmin users only. Regular users should use
        the 'my_settings' endpoint to retrieve their own settings.

        ## Permissions
        - SuperAdmin only

        ## Response
        - 200: Success - Returns paginated list of user settings
        - 403: Forbidden - If user is not a SuperAdmin
        """,
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
                name="user",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                description="Filter by user ID",
            ),
        ],
        responses={
            200: SettingsSerializer(many=True),
            403: OpenApiResponse(description="Permission denied - SuperAdmin only"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    retrieve=extend_schema(
        tags=["Settings"],
        summary="Retrieve user settings",
        description="""
        Retrieve detailed settings for a specific user by settings ID.

        ## Permissions
        - Users can only retrieve their own settings
        - SuperAdmin can retrieve any user's settings

        ## Response
        - 200: Success - Returns the settings object
        - 403: Forbidden - If user tries to access another user's settings
        - 404: Not Found - If settings with the given ID don't exist
        """,
        responses={
            200: SettingsSerializer,
            403: OpenApiResponse(
                description="Permission denied - can only access own settings"
            ),
            404: OpenApiResponse(description="Settings not found"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    ),
    partial_update=extend_schema(
        tags=["Settings"],
        summary="Update user settings",
        description="""
        Partially update settings for the authenticated user.

        Users can update their notification preferences, display preferences, and other
        configurable settings. All fields are optional.

        ## Available Settings
        - Notification preferences (email, in-app, push notifications)
        - Display preferences (theme, language, timezone)
        - Privacy settings
        - Default values for various features

        ## Permissions
        - Users can only update their own settings
        - SuperAdmin can update any user's settings

        ## Response
        - 200: Success - Returns updated settings
        - 400: Bad Request - Invalid input data
        - 403: Forbidden - If user tries to update another user's settings
        - 404: Not Found - If settings don't exist
        """,
        request=SettingsSerializer(partial=True),
        responses={
            200: SettingsSerializer,
            400: OpenApiResponse(description="Invalid input data"),
            403: OpenApiResponse(
                description="Permission denied - can only update own settings"
            ),
            404: OpenApiResponse(description="Settings not found"),
        },
    ),
)
class SettingsViewSet(UserCacheMixin, viewsets.ModelViewSet):
    """
    API endpoint for managing user settings.

    Provides operations for managing user-specific settings including:
    - List all settings (SuperAdmin only)
    - Retrieve specific user settings
    - Update user settings
    - Retrieve authenticated user's settings (my_settings action)

    Settings control user preferences such as notifications, display options,
    privacy settings, and default values for various features.

    ## Notes
    - Settings are automatically created for each user
    - Direct creation and deletion via API is disabled
    - Use 'my_settings' endpoint for convenient access to own settings
    """

    queryset = Settings.objects.all()
    serializer_class = SettingsSerializer
    permission_classes = (IsAuthenticated,)
    pagination_class = StandardPageNumberPagination
    # No "post"/"delete": a Settings row is created by the post_save signal
    # on CustomUser and lives as long as the account, so both are refused
    # here with a 405. This list is what performs that refusal - overriding
    # create()/destroy() to return 405 as well was unreachable code, since
    # DRF rejects the method before dispatch ever reaches them.
    http_method_names = ["get", "head", "patch", "options"]

    filterset_fields = {
        "user": ["exact"],
        "user__user_type": ["exact"],
        "user__school": ["exact"],
    }
    search_fields = [
        "user__email",
        "user__first_name",
        "user__last_name",
    ]
    ordering_fields = ["user__username", "user__email"]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]

    def get_permissions(self):
        """
        Allow only SuperAdmin users to list all settings.
        Other actions require standard authentication.
        """
        if self.action == "list":
            permission_classes = [IsAuthenticated, IsSuperAdmin]
        else:
            permission_classes = [IsAuthenticated]

        return [permission() for permission in permission_classes]

    def get_queryset(self):
        """
        Filter queryset based on user permissions.

        - SuperAdmin: Can access all settings
        - Regular users: Can only access their own settings
        """
        user = self.request.user

        # Both flags, as IsSuperAdmin requires (H-19). `or` let a
        # createsuperuser account - is_superuser but user_type TEACHER -
        # read and edit every user's settings.
        if user.is_superuser and user.user_type == UserTypes.SUPER_ADMIN:
            return Settings.objects.all()

        return Settings.objects.filter(user=user)

    @extend_schema(
        tags=["Settings"],
        summary="Get current user's settings",
        description="""
        Retrieve the settings for the currently authenticated user.

        This is a convenience endpoint that returns the authenticated user's settings
        without requiring the settings ID. It uses caching for improved performance.

        ## Response
        - 200: Success - Returns the user's settings
        - 401: Unauthorized - If user is not authenticated
        - 404: Not Found - If settings don't exist (should never happen as they're auto-created)

        ## Caching
        Results are cached for 5 minutes to improve performance.
        """,
        responses={
            200: SettingsSerializer,
            401: OpenApiResponse(
                description="Unauthorized",
                examples=[
                    OpenApiExample(name="unauthorized", value={"error": "Unauthorized"})
                ],
            ),
            404: OpenApiResponse(
                description="Settings not found",
                examples=[
                    OpenApiExample(
                        name="not_found",
                        value={"detail": "Settings not found for this user"},
                    )
                ],
            ),
        },
    )
    @action(detail=False, methods=["GET"])
    def my_settings(self, request, *args, **kwargs):
        """
        Retrieve the currently authenticated user's settings.
        Returns the complete settings profile for the logged-in user with caching support.
        """
        # `usr` alone: a Settings save bumps its owner's generation (see
        # users.signals.clear_user_cache, which reads instance.user_id).
        cache_key = versioned_key(
            f"settings:user_id__{request.user.id}:view__my_settings",
            [(SCOPE_USER, request.user.id)],
        )
        data = cache.get(cache_key)

        if data is None:
            # Settings are normally auto-created on user registration (see
            # users.signals.create_default_settings_and_wallet), but that
            # creation can silently fail. Self-heal here instead of 404ing,
            # since the user has no other way to discover/create their row.
            settings_obj, _ = Settings.objects.get_or_create(user=request.user)
            serializer = self.get_serializer(settings_obj)
            data = serializer.data
            cache.set(cache_key, data, getattr(settings, "CACHE_TTL", 60 * 5))

        return Response(data)


def _reset_locked_response(otp_obj):
    """429 for POST /auth/reset-password while the reset code is locked.

    Informative by founder decision (2026-09-28): the person resetting is
    usually the owner, so they're told why, when and that nothing changed.
    /auth/otp stays generic while locked (anti-enumeration).
    """
    locked_until = otp_obj.locked_until.astimezone(dt_timezone.utc)
    retry_after = max(1, math.ceil((locked_until - timezone.now()).total_seconds()))
    minutes = max(1, math.ceil(retry_after / 60))
    message = (
        "For your security, password reset is paused on this account because "
        f"the code was entered incorrectly {PasswordResetOTP.MAX_ATTEMPTS} times. "
        f"You can request a new code after {locked_until:%H:%M} UTC "
        f"(in {minutes} minute{'' if minutes == 1 else 's'}). "
        "Your password has not been changed, and you can still sign in with "
        "your current password. If you didn't try to reset your password, "
        "someone else may have. Your account is still safe."
    )
    # v2's S6a N3 (SM ruling): every documented field exactly as before,
    # with the coded envelope added beside them.
    body = {
        "code": "RESET_LOCKED",
        "message": message,
        "locked_until": locked_until.isoformat(),
        "retry_after_seconds": retry_after,
    }
    response = Response(
        add_coded_envelope(body, "RESET_LOCKED", message),
        status=status.HTTP_429_TOO_MANY_REQUESTS,
    )
    response["Retry-After"] = str(retry_after)
    return response


# --- OpenAPI documentation for the public auth code flows -------------------
# Every response goes through users.renderers.APIJSONRenderer, so the frontend
# sees {"success", "message", "data"} on success and {"success": false,
# "message", "error": {"field_errors": {...}}} on failure. The examples below
# show the rendered (on-the-wire) bodies, not the raw view payloads.


def _auth_error_example(name, message, summary=None):
    return OpenApiExample(
        name,
        summary=summary or message,
        value={
            "success": False,
            "message": message,
            "error": {"field_errors": {"detail": message}},
        },
        response_only=True,
    )


_VERIFY_LOCKED_TEXT = (
    "Too many incorrect codes for this email address. Please wait, then "
    "request a new verification email. Expected available in 1800 seconds."
)

_THROTTLED_EXAMPLE = _auth_error_example(
    "Throttled",
    "Request was throttled. Expected available in 3599 seconds.",
    summary="Per-IP rate limit hit (also sets the Retry-After header)",
)


class AuthViewSet(viewsets.ViewSet):
    """
    Handles user authentication actions
    """

    http_method_names = ["post", "options"]

    @extend_schema(
        tags=["Authentication"],
        summary="Verify email and activate account (signs the user in)",
        description="""
Activates an account with the 6-digit code from the activation email and
returns a JWT pair, so the user is signed in straight away.

**Frontend flow**
1. The activation email links to `<frontend>/verify-email?email=<email>&token=<6 digits>`.
   Read both query parameters and POST them here unchanged.
2. On **202**, store `data.access` / `data.refresh` and treat the user as signed in
   (`data.user` is the full user object, same shape as `GET /users/me`).
3. On **400** show `message`. If it is "Activation link has expired.", offer
   "Send a new link", which calls `POST /auth/otp` with `otp_type: "VERIFY_EMAIL"`.

**Values**
- `email`: the address from the link (string, required).
- `token`: the 6-digit code from the link, sent as a **string** (keep leading zeros).
- The code from a self-registration email is valid for **15 minutes**; requesting a
  new one (`/auth/otp`) replaces the old code.
- Rate limit: **5 requests per hour per IP** → 429 with a `Retry-After` header.

**Two different 429s.** After **5 wrong codes for one address** (from any number
of IPs) the address is locked for 30 minutes, and every attempt, a correct code
included, answers **429** with `error.field_errors.code == "VERIFY_LOCKED"` and a
`Retry-After` header. Show `message`, then offer "Send a new link" once the wait
is over. The per-IP rate limit has **no** `code`. The lock's body also carries
`reason_code`, `error_class`, `remediation`, `retryable`, `params` and `reference`
(quote `reference` when contacting support); the per-IP limit's does not. An
address with no account is locked and answered exactly the same way.
""",
        request=VerifyCustomUserSerializer,
        examples=[
            OpenApiExample(
                "Verify request",
                value={"email": "teacher@example.com", "token": "048213"},
                request_only=True,
            ),
        ],
        responses={
            202: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Account activated; JWT pair issued (user is signed in).",
                examples=[
                    OpenApiExample(
                        "Activated",
                        value={
                            "success": True,
                            "message": "Request Successful",
                            "data": {
                                "refresh": "<jwt refresh token>",
                                "access": "<jwt access token>",
                                "user": {
                                    "id": "<uuid>",
                                    "email": "teacher@example.com",
                                    "...": "...",
                                },
                            },
                        },
                        response_only=True,
                    )
                ],
            ),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Missing fields, wrong code or email, or an expired code. Show `message`.",
                examples=[
                    _auth_error_example(
                        "Missing fields", "Email and Token are required."
                    ),
                    _auth_error_example(
                        "Wrong email or code", "Invalid email or token."
                    ),
                    _auth_error_example(
                        "Expired code",
                        "Activation link has expired.",
                        summary="Expired: offer a resend via POST /auth/otp (VERIFY_EMAIL)",
                    ),
                ],
            ),
            429: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description=(
                    "VERIFY_LOCKED (5 wrong codes for this address, 30 min; has "
                    "`error.field_errors.code`), or the per-IP rate limit (5/hour; "
                    "no `code`). Wait for `Retry-After` seconds either way."
                ),
                examples=[
                    OpenApiExample(
                        "Address locked",
                        summary="VERIFY_LOCKED: 5 wrong codes for this address",
                        value={
                            "success": False,
                            "message": _VERIFY_LOCKED_TEXT,
                            "error": {
                                "field_errors": {
                                    "detail": _VERIFY_LOCKED_TEXT,
                                    "code": "VERIFY_LOCKED",
                                    "reason_code": "VERIFY_LOCKED",
                                    "error_class": "USER",
                                    "remediation": (
                                        "Wait, then request a new verification email."
                                    ),
                                    "retryable": True,
                                    "params": {},
                                    "reference": "<request id>",
                                }
                            },
                        },
                        response_only=True,
                    ),
                    _THROTTLED_EXAMPLE,
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
        # The token being checked is a 6-digit numeric code (by design, see
        # users.models.ACTIVATION_TOKEN_VALIDITY) - without a dedicated,
        # tight throttle this falls back to the generic 60/min anon rate,
        # which does not meaningfully slow down guessing it for a known
        # email.
        throttle_classes=[VerifyEmailThrottle],
    )
    def verify(self, request, **kwargs):
        email = (request.data.get("email") or "").strip()
        token = (request.data.get("token") or "").strip()

        if not email or not token:
            sign_in_failed(
                request, account_for_email(email), "email_verification", "CODE_MISSING"
            )
            raise ParseError("Email and Token are required.")

        # H-53: a per-address budget of attempts. While locked, every
        # attempt is refused - a correct code and a re-sent one included - and
        # the answer is the same whether or not the address has an account.
        # The attempt is spent before the code is checked, so simultaneous
        # guesses from many IPs cannot all slip in before the lock.
        lock_until = verify_lock_until(email)
        attempt = None if lock_until else reserve_verify_attempt(email)
        if lock_until or verify_attempt_over_budget(attempt):
            wait = (
                lock_until - time.time()
                if lock_until
                else settings.VERIFY_EMAIL_LOCK_SECONDS
            )
            # Merge of beta (H-53) into Epic A (SM ruling): a locked attempt
            # is recorded as refused, one event per attempt.
            sign_in_failed(
                request,
                account_for_email(email),
                "email_verification",
                "VERIFY_LOCKED",
                denied=True,
            )
            # v2's S6a N3 (SM ruling): the same 429, text and Retry-After,
            # plus `code` VERIFY_LOCKED and the coded envelope, so a client
            # can tell this lock from the per-IP rate limit.
            raise EnvelopedThrottled(
                wait=max(1, int(wait)),
                detail=(
                    "Too many incorrect codes for this email address. Please "
                    "wait, then request a new verification email."
                ),
                reason_code="VERIFY_LOCKED",
                code_value="VERIFY_LOCKED",
            )

        def refuse(message, account, reason_code):
            # Audit records what happened to THIS attempt; the guess that
            # spends the budget also set the lock (as reset_password's L2).
            lock_triggered = verify_budget_spent(attempt)
            if lock_triggered:
                # The stored code is left alone: activation_token also holds
                # student (24 h) and school-admin (7 d) invitations, and
                # clearing it would let anyone destroy an invitation with a
                # few wrong guesses. A sign-up code (15 min) expires during
                # the lock anyway.
                lock_verify_address(email)
            sign_in_failed(
                request,
                account,
                "email_verification",
                reason_code,
                extra_metadata={"lock_triggered": True} if lock_triggered else None,
            )
            raise ParseError(message)

        user = CustomUser.objects.filter(email=email, activation_token=token)
        if not user.exists():
            refuse("Invalid email or token.", account_for_email(email), "INVALID_CODE")

        user = user.first()

        # H-164: a never-verified account with admin power is not activated
        # or signed in by a code: the wrong-code refusal, the attempt spent,
        # nothing written.
        if not user.email_verified_at and _holds_admin_power(user):
            refuse("Invalid email or token.", user, "INVALID_CODE")

        # H-202: a verified account that was switched off is not switched back
        # on or signed in by a code: the same refusal, nothing written.
        if user.email_verified_at and not user.is_active:
            refuse("Invalid email or token.", user, "INVALID_CODE")

        if user.activation_expires and timezone.now() > user.activation_expires:
            refuse("Activation link has expired.", user, "CODE_EXPIRED")

        user.email_verified_at = timezone.now()
        user.activation_token = None
        user.activation_expires = None
        user.is_active = True
        # Epic A S4 (SM ruling): the activation's PERMISSION_CHANGE names the
        # account that just proved the code - their own action, not SYSTEM.
        with history.acting_as(user):
            user.save()
        clear_verify_failures(email)

        safe_delay(sync_user_to_mailerlite, str(user.id))

        user_data = CustomUserSerializer(user).data

        refresh = EpochRefreshToken.for_user(user)

        # Track activity
        AnalyticsService.track_activity(user)
        sign_in_succeeded(request, user, "email_verification")

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
        summary="Send a verification link or a password-reset code",
        description="""
Sends one of two emails, chosen by `otp_type`:

| `otp_type` | Sends | Next call |
|---|---|---|
| `VERIFY_EMAIL` | a new activation link, valid 15 min (replaces the old code) | `POST /auth/verify` |
| `RESET_PASSWORD` | a 6-digit password-reset code (valid 15 min) | `POST /auth/reset-password` |

**An unknown address always gets 202.** Show the same neutral confirmation (e.g.
"If an account exists for that address, we've sent an email") for every 202 and
never branch on `message`: its wording can differ between cases. The two 400s
below only happen for existing accounts in the wrong state.

**Values**
- `email`: string, required, must be a valid email address.
- `otp_type`: exactly `"VERIFY_EMAIL"` or `"RESET_PASSWORD"` (upper case).

**400 cases** (show `message`):
- invalid `otp_type` or email → "Invalid OTP type. Valid values are `VERIFY_EMAIL` and `RESET_PASSWORD`"
- `VERIFY_EMAIL` for an account that is already active → "Email already verified. Please login."
  (send the user to sign in)
- `RESET_PASSWORD` for an account that never verified its email → "Email not verified."
  (offer `VERIFY_EMAIL` instead)

While a password reset is locked after 5 wrong codes, this endpoint still answers
202 but sends no code; the lock is reported by `POST /auth/reset-password` (429
`RESET_LOCKED`).

Rate limit: **5 requests per hour per IP** → 429 with a `Retry-After` header.
""",
        request=OTPSerializer,
        examples=[
            OpenApiExample(
                "Resend verification link",
                value={"email": "teacher@example.com", "otp_type": "VERIFY_EMAIL"},
                request_only=True,
            ),
            OpenApiExample(
                "Request password-reset code",
                value={"email": "teacher@example.com", "otp_type": "RESET_PASSWORD"},
                request_only=True,
            ),
        ],
        responses={
            202: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Accepted. Show one neutral confirmation for every 202; don't branch on `message`.",
                examples=[
                    OpenApiExample(
                        "Accepted",
                        value={
                            "success": True,
                            "message": OTP_SENT_DETAIL,
                            "data": {"detail": OTP_SENT_DETAIL},
                        },
                        response_only=True,
                    )
                ],
            ),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Bad input, or the account is in the wrong state for this otp_type.",
                examples=[
                    _auth_error_example(
                        "Invalid otp_type or email",
                        "Invalid OTP type. Valid values are `VERIFY_EMAIL` and `RESET_PASSWORD`",
                    ),
                    _auth_error_example(
                        "Already verified", "Email already verified. Please login."
                    ),
                    _auth_error_example("Email not verified", "Email not verified."),
                ],
            ),
            429: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Too many requests from this IP (5/hour). Wait for `Retry-After` seconds.",
                examples=[_THROTTLED_EXAMPLE],
            ),
        },
    )
    @action(
        detail=False,
        methods=["post", "options"],
        url_path="otp",
        url_name="otp",
        permission_classes=[AllowAny],
        throttle_classes=[OTPRequestThrottle],
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
                {"detail": OTP_SENT_DETAIL}, status=status.HTTP_202_ACCEPTED
            )

        if otp_type == "VERIFY_EMAIL":

            if user.email_verified_at and user.is_active:
                raise ParseError("Email already verified. Please login.")

            # H-202: a verified account that was switched off is not mailed a
            # code (verify would switch it back on): the neutral reply of an
            # unknown address, nothing made or sent.
            if user.email_verified_at and not user.is_active:
                return Response(
                    {"detail": OTP_SENT_DETAIL}, status=status.HTTP_202_ACCEPTED
                )

            # H-164: a never-verified account with admin power gets no
            # activation code (/auth/verify would make it active and sign it
            # in): nothing is made or sent, and the reply below is the one an
            # unknown address gets.
            if user.email_verified_at or not _holds_admin_power(user):
                # H-53: a locked address gets no new code (it could not be
                # used until the lock ends), and the same reply as a send.
                if not verify_lock_until(user.email):
                    send_user_activation_email(user)

        elif otp_type == "RESET_PASSWORD":
            # H-164: refused only when the account is also INACTIVE (a
            # self-registered row, or an old-scheme pending student). An
            # ACTIVE account that never verified its email is a student a
            # teacher invited: it was created active with an emailed
            # temporary password, and a student who lost that email needs
            # this road. The reset itself proves the mailbox (see
            # reset_password).
            # An account with admin power that never verified its email gets no
            # new first road by a mailbox code: refused as the inactive case is.
            if not user.email_verified_at and (
                not user.is_active or _holds_admin_power(user)
            ):
                raise ParseError("Email not verified.")

            # H-202: a switched-off (verified) account makes and gets no reset
            # code: the neutral reply of an unknown address.
            if not user.is_active:
                return Response(
                    {"detail": OTP_SENT_DETAIL}, status=status.HTTP_202_ACCEPTED
                )

            otp_obj, created = PasswordResetOTP.objects.get_or_create(user=user)
            otp_code = otp_obj.generate_code()
            if otp_code is None:
                # Locked out (AUTHZ-L2): no new code and no email, but the
                # same reply as a send, so this is not an enumeration signal.
                return Response(
                    {"detail": OTP_SENT_DETAIL}, status=status.HTTP_202_ACCEPTED
                )

            message = f"""
Hello {user.first_name},

We received a request to reset your Grade A+ password

Your Password reset code is: {otp_code}

Enter this code in the app to continue.

{RESET_EMAIL_WARNING}

The Grade A+ Team

Need help? Contact us at {settings.SUPPORT_EMAIL}
            """

            safe_delay(
                send_email_task,
                subject="Your Grade A+ password reset code",
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
            )

        return Response(
            {"detail": OTP_SENT_DETAIL},
            status=status.HTTP_202_ACCEPTED,
        )

    @extend_schema(
        tags=["Authentication"],
        summary="Reset the password with the emailed code (signs the user in)",
        description="""
Sets a new password using the 6-digit code from `POST /auth/otp`
(`otp_type: "RESET_PASSWORD"`), signs the user out of every other device and
returns a fresh JWT pair.

**Values**
- `email`: string, required.
- `otp`: the 6-digit code from the email, sent as a **string** (keep leading zeros).
- `new_password`: string, must pass the password rules (errors come back under
  `error.field_errors.new_password`).
- The code is valid for **15 minutes**.

**Wrong code handling.** Every wrong email / code / expired code gives the same
400 "Invalid email, OTP code, or new password." After **5 wrong codes** the reset
is locked for **30 minutes**: the answer becomes **429** with
`error.field_errors.code == "RESET_LOCKED"`, plus `locked_until` (UTC ISO time),
`retry_after_seconds` and a `Retry-After` header. Show `message` as is; it tells
the user their password was not changed. Requesting a new code during the lock
sends nothing. The body also carries `reason_code` ("RESET_LOCKED"),
`error_class`, `remediation`, `retryable`, `params` and `reference` (quote
`reference` when contacting support).

**Telling the two 429s apart:** `RESET_LOCKED` has `error.field_errors.code`;
the plain rate limit (10 requests/hour per IP) does not.
""",
        request=ResetPasswordSerializer,
        examples=[
            OpenApiExample(
                "Reset request",
                value={
                    "email": "teacher@example.com",
                    "otp": "731904",
                    "new_password": "a-new-strong-passphrase",  # pragma: allowlist secret
                },
                request_only=True,
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Password changed; other sessions revoked; JWT pair issued.",
                examples=[
                    OpenApiExample(
                        "Reset done",
                        value={
                            "success": True,
                            "message": "Password has been reset successfully. You are now logged in.",
                            "data": {
                                "detail": "Password has been reset successfully. You are now logged in.",
                                "access": "<jwt access token>",
                                "refresh": "<jwt refresh token>",
                            },
                        },
                        response_only=True,
                    )
                ],
            ),
            400: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="Wrong or expired code (one generic message), or a password-rule failure.",
                examples=[
                    _auth_error_example(
                        "Wrong or expired code",
                        "Invalid email, OTP code, or new password.",
                    ),
                    OpenApiExample(
                        "Weak password",
                        value={
                            "success": False,
                            "message": "New password: This password is too common.",
                            "error": {
                                "field_errors": {
                                    "new_password": ["This password is too common."]
                                }
                            },
                        },
                        response_only=True,
                    ),
                ],
            ),
            429: OpenApiResponse(
                response=OpenApiTypes.OBJECT,
                description="RESET_LOCKED after 5 wrong codes (30 min), or the plain per-IP rate limit.",
                examples=[
                    OpenApiExample(
                        "Reset locked",
                        value={
                            "success": False,
                            "message": (
                                "For your security, password reset is paused on this account because "
                                "the code was entered incorrectly 5 times. You can request a new code "
                                "after 14:32 UTC (in 30 minutes). Your password has not been changed, "
                                "and you can still sign in with your current password. If you didn't "
                                "try to reset your password, someone else may have. Your account is "
                                "still safe."
                            ),
                            "error": {
                                "field_errors": {
                                    "code": "RESET_LOCKED",
                                    "message": "For your security, password reset is paused … (same text)",
                                    "locked_until": "2026-09-30T14:32:05+00:00",
                                    "retry_after_seconds": 1800,
                                }
                            },
                        },
                        response_only=True,
                    ),
                    _THROTTLED_EXAMPLE,
                ],
            ),
        },
    )
    @action(
        detail=False,
        methods=["post", "options"],
        url_path="reset-password",
        url_name="reset-password",
        permission_classes=[AllowAny],
        throttle_classes=[PasswordResetThrottle],
    )
    def reset_password(self, request, **kwargs):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data.get("email")
        otp = serializer.validated_data.get("otp")
        new_password = serializer.validated_data.get("new_password")

        # Look the OTP up by user rather than by (user, code) so that a
        # wrong guess is attributable to a row and can be counted. The old
        # (user, code) lookup made every failure indistinguishable from
        # "no OTP exists", which is why the attempts budget could not be
        # enforced.
        try:
            user = CustomUser.objects.get(email=email)
            otp_obj = PasswordResetOTP.objects.get(user=user)
        except (CustomUser.DoesNotExist, PasswordResetOTP.DoesNotExist):
            sign_in_failed(
                request, account_for_email(email), "password_reset", "INVALID_CODE"
            )
            raise ParseError("Invalid email, OTP code, or new password.") from Exception

        # H-164: a switched-off account that never verified its email is not
        # reset or stamped, whatever code exists (one issued while it was
        # active), and neither is a never-verified account with admin power.
        # The same generic refusal as an unknown address; nothing is
        # written, so the code is left to expire by itself.
        if not user.email_verified_at and (
            not user.is_active or _holds_admin_power(user)
        ):
            raise ParseError("Invalid email, OTP code, or new password.")

        # H-202: what is left inactive here is verified. A switched-off person
        # sets no password and is not told it worked: the generic refusal,
        # the attempt spent like a wrong guess (unless already locked).
        if not user.is_active:
            if not otp_obj.is_locked():
                otp_obj.register_failure()
            raise ParseError("Invalid email, OTP code, or new password.")

        if otp_obj.is_locked():
            # Merge of batch-2a (L2) into Epic A: L2's 429 answer, and the
            # attempt is recorded as refused because the reset is locked.
            sign_in_failed(request, user, "password_reset", "RESET_LOCKED", denied=True)
            return _reset_locked_response(otp_obj)

        if not otp_obj.is_valid():
            otp_obj.delete()
            sign_in_failed(request, user, "password_reset", "CODE_EXPIRED")
            raise ParseError("Invalid email, OTP code, or new password.")

        # Constant-time compare so the response latency does not leak how
        # much of the code was correct. Every non-lockout failure returns
        # the identical message, to avoid confirming which of email / code
        # / password was the wrong one.
        if not constant_time_compare(str(otp_obj.code), str(otp)):
            otp_obj.register_failure()
            # The guess that spends the budget gets the lockout answer
            # straight away, not one more generic 400. Audit records what
            # happened to THIS attempt: a wrong code, which also set the lock.
            if otp_obj.is_locked():
                sign_in_failed(
                    request,
                    user,
                    "password_reset",
                    "INVALID_CODE",
                    extra_metadata={"lock_triggered": True},
                )
                return _reset_locked_response(otp_obj)
            sign_in_failed(request, user, "password_reset", "INVALID_CODE")
            raise ParseError("Invalid email, OTP code, or new password.")

        user.set_password(new_password)
        # H-164: the code proves control of the mailbox exactly as the verify
        # link does, so a successful reset stamps the email in the SAME save
        # as the new password (never on the request, never on a wrong code).
        # The new password replaces the temporary one an invited student was
        # sent, so the flag that stood for it is cleared, as change_password
        # does. The reset signs the student in, so it stamps last_login like
        # every sign-in: otherwise a later add to another course would see
        # "never signed in" and overwrite the password just chosen.
        if not user.email_verified_at:
            user.email_verified_at = timezone.now()
        user.must_change_password = False
        user.save()
        stamp_last_login(user)

        otp_obj.delete()

        tokens = OutstandingToken.objects.filter(user=user)
        for token in tokens:
            BlacklistedToken.objects.get_or_create(token=token)

        refresh = EpochRefreshToken.for_user(user)

        # Track activity
        AnalyticsService.track_activity(user)
        sign_in_succeeded(request, user, "password_reset")

        return Response(
            {
                "detail": "Password has been reset successfully. You are now logged in.",
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            },
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

        message = f"""
Hello {user.first_name},

You are one step away from updating your Grade A+ password.

Your password change code is: {otp}


Enter this code to complete the update. If you did not request this change, please secure your account immediately.

The Grade A+ Team
Need help? Contact us at {settings.SUPPORT_EMAIL}
"""

        send_mail(
            subject="Your Grade A+ password change code",
            message=message,
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
        summary="Change password (optionally with an OTP)",
        description="""
        Changes the authenticated user's password.

        `current_password` is always required. `otp` is OPTIONAL: if the
        client sends one, it must be the code issued by
        `auth/request-change-password` and still be within its validity
        window, or the request is rejected. If the client omits it, the
        change proceeds on `current_password` alone.
        """,
        request=ChangePasswordSerializer,
        responses={
            200: {"description": "Password changed successfully"},
            400: {"description": "Incorrect current password, or invalid/expired OTP"},
        },
    )
    @action(detail=False, methods=["post", "options"], url_path="change-password")
    def change_password(self, request, **kwargs):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        current_password = serializer.validated_data.get("current_password")
        new_password = serializer.validated_data.get("new_password")
        otp = (serializer.validated_data.get("otp") or "").strip()

        if not user.check_password(current_password):
            sign_in_failed(request, user, "password_change", "WRONG_PASSWORD")
            raise ParseError("Incorrect current password. Please try again.")

        # Dual-mode by design: the frontend does not send `otp` yet, so a
        # request without one must keep working exactly as it did. When one
        # IS sent it is fully verified, so the day the frontend starts
        # collecting the code from request-change-password, no backend
        # deploy is needed to make it count.
        #
        # This is deliberately NOT an enforcement point: a caller can still
        # omit `otp` entirely. Making the second factor mandatory is a
        # separate, breaking change (drop this `if otp:` guard and require
        # the field on the serializer) that has to land together with the
        # frontend sending it.
        if otp:
            otp_obj = PasswordChangeOTP.objects.filter(user=user).first()

            if otp_obj is None:
                sign_in_failed(request, user, "password_change", "CODE_NOT_REQUESTED")
                raise ParseError(
                    "No password change code has been requested for this "
                    "account. Request one and try again."
                )

            if not otp_obj.is_valid():
                otp_obj.delete()
                sign_in_failed(request, user, "password_change", "CODE_EXPIRED")
                raise ParseError(
                    "This password change code has expired. Request a new one "
                    "and try again."
                )

            # Constant-time, for the same reason reset_password does it: a
            # plain == leaks how much of the code was correct via timing.
            if not constant_time_compare(str(otp_obj.code), str(otp)):
                sign_in_failed(request, user, "password_change", "INVALID_CODE")
                raise ParseError("Invalid password change code. Please try again.")

            # Single-use: a code that has completed a change must not be
            # replayable for a second one.
            otp_obj.delete()

        user.set_password(new_password)
        user.must_change_password = False
        user.save()

        tokens = OutstandingToken.objects.filter(user=user)
        for token in tokens:
            BlacklistedToken.objects.get_or_create(token=token)

        # 2. Generate new tokens for the current device
        refresh = EpochRefreshToken.for_user(user)

        # Track activity
        AnalyticsService.track_activity(user)
        sign_in_succeeded(request, user, "password_change")

        return Response(
            {
                "detail": "Password changed successfully.",
                "access": str(refresh.access_token),
                "refresh": str(refresh),
            }
        )

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
    def logout(self, request, *args, **kwargs):
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
            emit(
                AuditAction.AUTH_LOGOUT,
                actor=request.user,
                request=request,
                target_type="CustomUser",
                target_id=request.user.id,
                outcome=AuditOutcome.FAILURE,
                error_class=ErrorClass.VALIDATION,
                reason_code="REFRESH_TOKEN_MISSING",
            )
            raise ParseError("Refresh token is required.") from KeyError
        except TokenError:
            emit(
                AuditAction.AUTH_LOGOUT,
                actor=request.user,
                request=request,
                target_type="CustomUser",
                target_id=request.user.id,
                outcome=AuditOutcome.FAILURE,
                error_class=ErrorClass.USER,
                reason_code="REFRESH_TOKEN_INVALID",
            )
            raise ParseError("Invalid or expired token") from TokenError

        # AUTHZ-T1: blacklisting the refresh token never touched the access
        # token, which stayed valid for up to a day. Bumping the session epoch
        # kills the access token AND every other device's tokens at once
        # (logging out anywhere signs the user out everywhere).
        try:
            request.user.revoke_all_sessions()
        except Exception:
            emit(
                AuditAction.AUTH_LOGOUT,
                actor=request.user,
                request=request,
                target_type="CustomUser",
                target_id=request.user.id,
                outcome=AuditOutcome.FAILURE,
                error_class=ErrorClass.SYSTEM,
                reason_code="SESSION_REVOKE_FAILED",
            )
            raise

        emit(
            AuditAction.AUTH_LOGOUT,
            actor=request.user,
            request=request,
            target_type="CustomUser",
            target_id=request.user.id,
            outcome=AuditOutcome.SUCCESS,
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
        throttle_classes=[RegisterThrottle],
        url_path="register",
    )
    def register(self, request, *args, **kwargs):
        """
        Register a new Teacher user

        This endpoint allows registration of new users with TEACHER role only.
        """
        # No need to strip user_type from the payload: CustomUserSerializer
        # forces its PRIVILEGED_FIELDS read-only unless the serializer is
        # built with a super admin in its context, and this one is built
        # without any context at all - so a client-sent user_type is dropped
        # on the floor either way. The strip that used to live here did it by
        # mutating request.data, which raises AttributeError on the immutable
        # QueryDict that a form-encoded or multipart body produces: every
        # non-JSON registration 500'd, whether or not it mentioned user_type.
        serializer = CustomUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.save()
        # Epic A S2: the new account is traceable. The requester was not
        # signed in, so the actor is ANONYMOUS and the account is the target;
        # a refused (malformed) registration is recorded by AuditMiddleware.
        emit(
            AuditAction.ACCOUNT_REGISTER,
            actor=request.user,
            request=request,
            target_type="CustomUser",
            target_id=user.pk,
            metadata={"auth_method": "self_registration"},
        )

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
        request=None,
        responses={
            410: OpenApiResponse(
                description="Closed (H-152): this sign-up is no longer used.",
            ),
        },
    )
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[AllowAny],
        throttle_classes=[RegisterThrottle],
        url_path="register/student",
    )
    def register_student(self, request, *args, **kwargs):
        """H-152: closed. Every request is answered the same way: a valid
        code, an expired one, a wrong one and none at all cannot be told
        apart, nothing is read from the request, nothing is written and
        nobody is mailed. The per-address rate limit above still applies."""
        return Response(
            {"detail": OLD_INVITATION_CLOSED_MESSAGE},
            status=status.HTTP_410_GONE,
        )

    @extend_schema(
        tags=["Authentication"],
        summary="Complete school admin registration",
        description="""
        Complete registration for a school admin invited via the school's
        `create_with_admin` endpoint.

        Required fields:
        - email: The invited admin's email address
        - token: The activation token from the invitation email
        - password: The password the admin wants to set for their account
        """,
        request=SchoolAdminRegistrationCompletionSerializer,
        responses={
            200: OpenApiResponse(
                description="School admin registration completed successfully",
            ),
            400: OpenApiResponse(description="Invalid or expired token"),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[AllowAny],
        throttle_classes=[RegisterThrottle],
        url_path="register/school-admin",
        url_name="register-school-admin",
    )
    def register_school_admin(self, request, *args, **kwargs):
        # A refused code is recorded after the atomic block has rolled back,
        # never inside it (the event would roll back too).
        audit_failure = None
        try:
            with transaction.atomic():
                serializer = SchoolAdminRegistrationCompletionSerializer(
                    data=request.data
                )

                if not serializer.is_valid():
                    raise ValidationError(serializer.errors)

                email = serializer.validated_data["email"].strip().lower()
                token = serializer.validated_data["token"].strip()

                # select_for_update prevents two concurrent submissions of the
                # same invite link from both racing past the is_active check.
                user = (
                    CustomUser.objects.select_for_update()
                    .filter(
                        email__iexact=email,
                        activation_token=token,
                        user_type=UserTypes.SCHOOL_ADMIN,
                        is_active=False,
                    )
                    .first()
                )

                # H-203, THE PRINCIPLE: the invitation token proves only the
                # mailbox. A pending row that has admin power (the Django admin
                # can give a SCHOOL_ADMIN row is_staff or is_superuser) is not
                # activated or signed in: the answer for a wrong token, and the
                # same audit event as one (Epic A's failed-sign-in record).
                if not user or _holds_admin_power(user):
                    audit_failure = (account_for_email(email), "INVALID_CODE")
                    raise ParseError("Invalid or expired activation token.")

                if user.activation_expires and timezone.now() > user.activation_expires:
                    audit_failure = (user, "CODE_EXPIRED")
                    raise ParseError(
                        "This invitation link has expired. Please contact your "
                        "superadmin for a new invitation."
                    )

                user.set_password(serializer.validated_data["password"])
                user.is_active = True
                user.email_verified_at = timezone.now()
                user.activation_token = None
                user.activation_expires = None
                # Epic A S4: the invited school admin who just proved the
                # invitation code is the actor of their own activation.
                with history.acting_as(user):
                    user.save()

            safe_delay(sync_user_to_mailerlite, str(user.id))

            refresh = EpochRefreshToken.for_user(user)

            # Track activity
            AnalyticsService.track_activity(user)
            sign_in_succeeded(request, user, "school_admin_invitation")

            return Response(
                {
                    "refresh": str(refresh),
                    "access": str(refresh.access_token),
                    "user": CustomUserSerializer(user).data,
                },
                status=status.HTTP_200_OK,
            )
        except (ParseError, ValidationError):
            if audit_failure is not None:
                sign_in_failed(
                    request,
                    audit_failure[0],
                    "school_admin_invitation",
                    audit_failure[1],
                )
            raise
        except Exception as e:
            logger.error("School admin registration failed", exc_info=e)
            return Response(
                {
                    "detail": describe_user_error(
                        e,
                        fallback_message=(
                            "Registration could not be completed. Please " "try again."
                        ),
                    )
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @extend_schema(
        tags=["Authentication"],
        summary="Authenticate with Google OAuth2",
        description="""
        Verifies a Google ID token (credential) sent from the frontend.

        - If the user does not exist: Creates a new user, assigns an unusable
        password, marks their email as verified, and logs them in.
        - If the user exists: Authenticates them if their registration method is `GOOGLE`.
        - Returns a pair of JWT (JSON Web Tokens) access and refresh tokens, along with user details.
        """,
        request=inline_serializer(
            name="GoogleAuthRequest",
            fields={
                "code": serializers.CharField(
                    required=True,
                    help_text="The authorization code received from Google's credential service on the frontend.",
                ),
            },
        ),
        responses={
            200: OpenApiResponse(
                description="Successfully authenticated. Returns JWT tokens and user profile.",
                response=inline_serializer(
                    name="GoogleAuthResponse",
                    fields={
                        "access": serializers.CharField(help_text="JWT Access Token"),
                        "refresh": serializers.CharField(help_text="JWT Refresh Token"),
                        "user": CustomUserSerializer(
                            help_text="The authenticated user's profile details."
                        ),
                    },
                ),
            ),
            400: OpenApiResponse(
                description="Bad Request. Possible causes:\n"
                "- Missing code\n"
                "- Invalid or expired authorization code\n"
                "- Email address has not been verified by Google\n"
                "- User already registered via standard Email instead of Google"
            ),
            500: OpenApiResponse(description="Internal Server Error"),
        },
    )
    @action(
        detail=False,
        methods=["post"],
        permission_classes=[AllowAny],
        throttle_classes=[GoogleAuthThrottle],
        url_path="google-auth",
    )
    def google_auth(self, request, *args, **kwargs):
        # Every refusal is recorded here, after the view's own atomic block has
        # unwound (an event written inside it would roll back with it). Each
        # raise site in _google_auth tags its reason, and the refused account
        # when one is known (a deactivated account).
        self._audit_reason = "GOOGLE_SIGN_IN_REFUSED"
        self._audit_account = None
        try:
            return self._google_auth(request)
        except (ParseError, ValidationError, AuthenticationFailed):
            reason = self._audit_reason
            sign_in_failed(
                request,
                self._audit_account,
                "google",
                reason,
                denied=reason == "ACCOUNT_DEACTIVATED",
                error_class=(
                    ErrorClass.PROVIDER
                    if reason == "GOOGLE_EXCHANGE_FAILED"
                    else ErrorClass.USER
                ),
            )
            raise

    def _google_auth(self, request):
        code = request.data.get("code")
        redirect_uri = settings.GOOGLE_REDIRECT_URI

        if not code:
            self._audit_reason = "GOOGLE_CODE_MISSING"
            raise ParseError("Authorization code is required")

        try:
            import requests as http_requests

            token_url = "https://oauth2.googleapis.com/token"
            payload = {
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            }

            try:
                response = http_requests.post(token_url, data=payload)
                response.raise_for_status()
                token_data = response.json()
            except http_requests.exceptions.RequestException as e:
                logger.error("Google OAuth token exchange failed", exc_info=e)
                self._audit_reason = "GOOGLE_EXCHANGE_FAILED"
                raise ParseError(
                    describe_user_error(
                        e,
                        fallback_message=GOOGLE_SIGN_IN_FAILED,
                    )
                ) from e

            id_token_str = token_data.get("id_token")
            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")
            expires_in = token_data.get("expires_in", 500)

            if not id_token_str:
                self._audit_reason = "GOOGLE_TOKEN_INVALID"
                raise ParseError("Google did not return an ID token")

            id_info = id_token.verify_oauth2_token(
                id_token_str,
                google_requests.Request(),
                settings.GOOGLE_OAUTH_CLIENT_ID,
            )

            if not id_info.get("email_verified"):
                self._audit_reason = "GOOGLE_EMAIL_UNVERIFIED"
                raise ParseError("Google has not verified your email")

            with transaction.atomic():
                # Lowercased to match how every other path stores and looks up
                # emails (CustomUserSerializer.validate_email). Without it, a
                # mixed-case Google address misses the lookup below, falls into
                # the create branch, and dies on the unique constraint.
                email = id_info["email"].strip().lower()
                first_name = id_info.get("given_name", "")
                last_name = id_info.get("family_name", "")
                middle_name = id_info.get("middle_name", "")
                profile_image_url = id_info.get(
                    "picture",
                )

                user = CustomUser.objects.filter(email=email).first()

                if not user:
                    # create the user with
                    # registration_method / email_verified_at / is_active are
                    # deliberately NOT in this dict. None of them are in
                    # CustomUserSerializer.Meta.fields, so DRF silently
                    # dropped them - which meant every Google signup was
                    # stored as registration_method=EMAIL with a null
                    # email_verified_at, and (because
                    # CustomUserSerializer.create() sends an activation email
                    # whenever registration_method is EMAIL) was mailed a
                    # "verify your email" link it had no reason to receive.
                    # They are passed through save() below instead.
                    data = {
                        "email": email,
                        "first_name": first_name,
                        "last_name": last_name,
                        "middle_name": middle_name,
                        "profile_image_url": profile_image_url,
                    }

                    serializer = GoogleUserSerializer(data=data)
                    if serializer.is_valid():
                        # Server-controlled values, injected via save() rather
                        # than declared on the serializer: this serializer also
                        # backs /auth/register, so a writable email_verified_at
                        # would let any caller mark their own address verified.
                        # They must be set HERE rather than patched on after
                        # save, because create() reads registration_method to
                        # decide whether to send the activation email.
                        user = serializer.save(
                            registration_method=RegistrationMethod.GOOGLE,
                            email_verified_at=timezone.now(),
                        )

                        user.is_active = True
                        user.set_unusable_password()
                        user.save(update_fields=["password", "is_active"])

                        safe_delay(sync_user_to_mailerlite, str(user.id))
                    else:
                        # A Google account on a business domain lands here:
                        # signing in with Google creates an individual TEACHER
                        # account, and those require a personal address. The
                        # raw serializer error dict reads as a bug to the
                        # user, so say what actually happened - their school
                        # account is created by their school admin, not by
                        # this button.
                        email_errors = serializer.errors.get("email")
                        if email_errors:
                            self._audit_reason = "GOOGLE_SIGN_IN_REFUSED"
                            raise ParseError(
                                f"{email_errors[0]} If you are joining a "
                                "school, ask your school admin to invite you "
                                "and use the link in that invitation instead."
                            )
                        self._audit_reason = "GOOGLE_SIGN_IN_REFUSED"
                        raise ValidationError(serializer.errors)

                else:
                    # An account already exists for this address. Google has
                    # just proven the person controls that mailbox, which is
                    # strictly STRONGER evidence than the 6-digit code the
                    # email flow sends - so an account that simply never
                    # finished email verification is completed here instead
                    # of dead-ending. (It used to dead-end: this endpoint
                    # answered 200 with tokens, but the account stayed
                    # is_active=False, so SimpleJWT rejected those very
                    # tokens with "User is inactive" on the next request.)
                    #
                    # The carve-out: an account that WAS verified and is now
                    # inactive was switched off deliberately. Proving mailbox
                    # ownership says nothing about whether that decision
                    # should be reversed, so this must NOT reverse it -
                    # activating there would turn Google sign-in into a way
                    # round a deactivation.
                    if not user.is_active and user.email_verified_at is not None:
                        self._audit_reason = "ACCOUNT_DEACTIVATED"
                        self._audit_account = user
                        raise AuthenticationFailed(
                            "This account has been deactivated. Please "
                            "contact support.",
                            "account_deactivated",
                        )

                    # H-203, THE PRINCIPLE: a mailbox-only road (a Google
                    # identity proves only the mailbox) never signs in, verifies
                    # or activates a never-verified account with admin power.
                    # Answered like a failed Google sign-in; nothing written.
                    if user.email_verified_at is None and _holds_admin_power(user):
                        # Epic A: a refused sign-in is audited, naming the
                        # account it was tried on.
                        self._audit_reason = "GOOGLE_SIGN_IN_REFUSED"
                        self._audit_account = user
                        raise ParseError(GOOGLE_SIGN_IN_FAILED)

                    resurrected_fields = []
                    if user.email_verified_at is None:
                        user.email_verified_at = timezone.now()
                        resurrected_fields.append("email_verified_at")
                    if not user.is_active:
                        user.is_active = True
                        resurrected_fields.append("is_active")

                        # This row may carry a password an attacker chose
                        # while it sat dormant (POST /auth/register creates
                        # is_active=False rows with a real, caller-supplied
                        # password). Google has only proven mailbox
                        # ownership here, not which password belongs to the
                        # rightful owner, so activating the row must not
                        # leave any existing password usable - the
                        # rightful owner can always get a fresh one through
                        # the reset-password flow.
                        user.set_unusable_password()
                        resurrected_fields.append("password")

                    if resurrected_fields:
                        # Epic A S4 (SM ruling): the account Google's token
                        # check just established is the actor of its own
                        # activation.
                        with history.acting_as(user):
                            user.save(update_fields=resurrected_fields)
                        # Only now does this account become a real, usable
                        # one, so this is the first point it should reach
                        # the mailing list (queue_sync no-ops on inactive).
                        safe_delay(sync_user_to_mailerlite, str(user.id))

                        # Finishing registration is what promotes a
                        # teacher's invitation from PENDING to ENROLLED,
                        # and register_student (the emailed-link flow)
                        # does exactly this. A student invited to a course
                        # who signs in with Google instead of using that
                        # link completed registration by a different door,
                        # so the same promotion has to happen here or
                        # their enrollments stay PENDING forever - which,
                        # now that PENDING is not an access-granting state
                        # (see classrooms.models
                        # COURSE_ACCESS_ENROLLMENT_STATUSES), would lock
                        # them out of the very course they were invited to.
                        #
                        # Gated on `resurrected_fields` deliberately: this
                        # only fires for an account that was still
                        # unverified/inactive, i.e. one that had genuinely
                        # not finished registering. It never touches the
                        # PENDING rows of an already-established account,
                        # and Google has just proven mailbox ownership of
                        # the exact address the teacher invited - strictly
                        # stronger evidence than the emailed code.
                        # Epic A S4 (SM R3): one ROSTER_CHANGE per promoted
                        # enrolment, naming `user` - the account Google's
                        # token check just established, not request input.
                        promoted = history.record_bulk(
                            StudentCourse.objects.filter(
                                student=user,
                                enrollment_status=EnrollmentStatusType.PENDING,
                            ),
                            actor=user,
                            enrollment_status=EnrollmentStatusType.ENROLLED,
                        )
                        if promoted:
                            logger.info(
                                "Promoted %s pending enrollment(s) to ENROLLED "
                                "after Google sign-in completed registration "
                                "for user %s",
                                promoted,
                                user.id,
                            )

                expiry = timezone.now() + timedelta(seconds=expires_in)
                credentials, _ = UserGoogleCredentials.objects.update_or_create(
                    user=user,
                    defaults={
                        "access_token": access_token,
                        "token_expiry": expiry,
                        "scopes": id_info.get("scope", ""),
                        **({"refresh_token": refresh_token} if refresh_token else {}),
                    },
                )

            # A Google sign-in is a sign-in too (has_signed_in), and it mints
            # tokens here rather than through the login serializer.
            stamp_last_login(user)
            refresh = EpochRefreshToken.for_user(user)
            sign_in_succeeded(request, user, "google")

            return Response(
                {
                    "access": str(refresh.access_token),
                    "refresh": str(refresh),
                    "user": CustomUserSerializer(user).data,
                },
                status=status.HTTP_200_OK,
            )

        except ValueError as e:
            self._audit_reason = "GOOGLE_TOKEN_INVALID"
            raise ParseError("Invalid Google token signature") from e


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
        401: OpenApiResponse(
            description=(
                "Invalid credentials, or the account is locked after too many "
                "failed attempts. The lock's body has `error.field_errors.code` "
                '"account_locked" and `reason_code` "ACCOUNT_LOCKED", plus '
                "`error_class`, `remediation`, `retryable`, `params` and "
                "`reference`; show `message` either way."
            )
        ),
    },
)
class TokenObtainPairView(BaseTokenObtainPairView):
    """
    Custom view for obtaining JWT token pairs
    """

    serializer_class = CustomTokenObtainPairSerializer
    throttle_classes = [LoginThrottle]


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
    serializer_class = EpochTokenRefreshSerializer


def _uuid_or_404(value):
    """A path id as a UUID; a malformed one is simply not found (a 404, not
    a 500 from the UUIDField lookup)."""
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise NotFound() from exc


class TaskViewSet(viewsets.ViewSet):
    """
    ViewSet for managing background task status endpoints.

    This viewset handles endpoints that are not associated with any specific model
    but provides utility functionality for task management.
    """

    http_method_names = ["get", "post", "options"]
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Tasks"],
        summary="Get status of a background task",
        description="Retrieve the current status and metadata for a Celery background task by its ID.",
        responses={
            200: OpenApiResponse(
                response=TaskStatusSerializer,
                examples=[
                    OpenApiExample(
                        "Task Completed Example",
                        value={
                            "task_id": "9f7e4a19-b299-41b4-9829-b5490e93c523",
                            "status": "completed",
                            "meta": "{'status': 'Completed'}",
                            "resource_type": "assignment",
                            "resource_id": "055eb99a-d9af-4671-ac94-38133376e942",
                            "action": "grade",
                            "additional_ids": {},
                        },
                    ),
                    OpenApiExample(
                        "Task Processing Example",
                        value={
                            "task_id": "9f7e4a19-b299-41b4-9829-b5490e93c523",
                            "status": "processing",
                            "meta": "{'current': 0, 'total': 10, 'percent': 0, 'step': 'Initializing'}",
                            "resource_type": "assignment",
                            "resource_id": "055eb99a-d9af-4671-ac94-38133376e942",
                            "action": "grade",
                            "additional_ids": {},
                        },
                    ),
                ],
            ),
            404: OpenApiResponse(description="Task not found"),
        },
    )
    @action(detail=False, methods=["get"], url_path="status/(?P<task_id>[^/.]+)")
    def task_status(self, request, task_id=None):
        """
        Retrieve the status of a background task by its ID, enriched with context.

        Scoped to tasks the caller actually started. This used to fall back
        to a bare `AsyncResult(task_id)` whenever no tracked task matched -
        which, because `get_processing_task` filters on `requested_by`, is
        exactly what happens when the task belongs to somebody else. That
        made another user's task state and return value readable to anyone
        holding the id (`send_email_task`, for instance, returns a string
        containing the recipient's address). A Celery id has no owner to
        check, so there is no way to serve that fallback safely; every
        user-facing async endpoint now creates a tracked task, so nothing
        legitimate needs it.
        """
        processing_task = get_processing_task(task_id, requested_by=request.user)
        # H-38: a task whose course its owner can no longer reach answers
        # exactly like a missing one.
        if not processing_task or not teacher_may_reach(request.user, processing_task):
            raise NotFound("Tracked task not found for this user.")

        normalize_processing_task_status(processing_task)
        processing_task.refresh_from_db()
        meta = dict(processing_task.meta or {})
        if processing_task.error:
            meta.setdefault("error", processing_task.error)
        status_value = self._map_status(processing_task.status)

        context = get_task_context(processing_task)

        data = {
            "task_id": task_id,
            "status": status_value,
            "meta": str(meta) if meta else None,
            "resource_type": context["resource_type"],
            "resource_id": context["resource_id"],
            "action": context["action"],
            "additional_ids": context["additional_ids"],
        }

        serializer = TaskStatusSerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)

    # Helper methods (add inside TaskViewSet)
    def _map_status(self, db_status):
        """Map BackgroundTaskStatus to frontend-friendly status string."""
        mapping = {
            "PENDING": "processing",
            "STARTED": "processing",
            "SUCCESS": "completed",
            "FAILURE": "failed",
            "CANCELLED": "cancelled",
        }
        return mapping.get(db_status, "processing")

    @extend_schema(
        tags=["Tasks"],
        summary="Cancel a background task",
        description=(
            "Cancel a background task by its Celery task id. "
            "If the task is already running, the worker is terminated "
            "and any remaining pipeline steps will refuse to save results. "
            "If the task had already reached a final state (completed, "
            "failed, or already cancelled) before this request arrived, "
            "the response reports that real final status instead of "
            "claiming cancellation succeeded."
        ),
        responses={
            200: OpenApiResponse(
                response=TaskCancelSerializer,
                examples=[
                    OpenApiExample(
                        "Task Cancellation",
                        value={
                            "task_id": "9f7e4a19-b299-41b4-9829-b5490e93c523",
                            "status": "cancelled",
                            "message": "Background task cancellation requested successfully.",
                        },
                    )
                ],
            ),
            404: OpenApiResponse(description="Tracked task not found"),
        },
    )
    @action(detail=False, methods=["post"], url_path="cancel/(?P<task_id>[^/.]+)")
    def cancel(self, request, task_id=None):
        processing_task = get_processing_task(task_id, requested_by=request.user)
        # H-38: a task whose course its owner can no longer reach answers
        # exactly like a missing one.
        if not processing_task or not teacher_may_reach(request.user, processing_task):
            raise NotFound("Tracked task not found for this user.")

        already_terminal = processing_task.status in TERMINAL_TASK_STATUSES
        processing_task = cancel_processing_task(processing_task)
        status_value = self._map_status(processing_task.status)

        if already_terminal:
            message = (
                "This task had already finished before the cancellation "
                f"request reached it — its final status is {status_value!r}."
            )
        else:
            message = "Background task cancellation requested successfully."

        serializer = TaskCancelSerializer(
            {
                "task_id": task_id,
                "status": status_value,
                "message": message,
            }
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Tasks"],
        summary="Cancel all remaining tasks in a batch session",
        description=(
            "Cancel all pending or running tracked tasks in a batch upload session "
            "owned by the authenticated teacher."
        ),
        responses={
            200: OpenApiResponse(
                response=BatchSessionCancelSerializer,
                examples=[
                    OpenApiExample(
                        "Session Cancellation",
                        value={
                            "session_id": "550e8400-e29b-41d4-a716-446655440000",
                            "cancelled_count": 5,
                            "message": "Batch session cancellation requested successfully.",
                        },
                    )
                ],
            ),
            404: OpenApiResponse(description="Session not found"),
        },
    )
    @action(
        detail=False,
        methods=["post"],
        url_path="cancel-session/(?P<session_id>[^/.]+)",
    )
    def cancel_session(self, request, session_id=None):
        session = get_object_or_404(
            BatchUploadSession, id=session_id, teacher=request.user
        )
        # H-38: a session whose course its teacher can no longer reach
        # answers exactly like a missing one.
        if not teacher_may_reach(request.user, session):
            raise Http404("No BatchUploadSession matches the given query.")

        cancellable_tasks = list(
            session.processing_tasks.exclude(
                status__in=[
                    BackgroundTaskStatus.SUCCESS,
                    BackgroundTaskStatus.FAILURE,
                    BackgroundTaskStatus.CANCELLED,
                ]
            )
        )

        for processing_task in cancellable_tasks:
            cancel_processing_task(processing_task)

        serializer = BatchSessionCancelSerializer(
            {
                "session_id": session.id,
                "cancelled_count": len(cancellable_tasks),
                "message": "Batch session cancellation requested successfully.",
            }
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Tasks"],
        summary="Retry one failed batch item in place (FR-A-07 S7b)",
        description=(
            "Retries a FAILED grade item whose reason code is retryable as it "
            "is (PROVIDER_FAILURE, INSUFFICIENT_CREDITS_MID_BATCH): the same "
            "item_id, retry_count + 1, relaunched. 202 with the item in "
            "session-results' shape. Anything else is 409 NOT_RETRYABLE; an "
            "upload item answers 'upload the file again' with "
            "params.resolution = 'replace_file' (its file isn't kept)."
        ),
    )
    @action(
        detail=False,
        methods=["post"],
        url_path=r"session/(?P<session_id>[^/.]+)/items/(?P<item_id>[^/.]+)/retry",
        url_name="retry-item",
        permission_classes=[IsAuthenticated, IsTeacher, HasCreditBalance],
    )
    def retry_item(self, request, session_id=None, item_id=None):
        session = self._own_session(request, session_id)
        # H-38 (F6.2's rule for every tasks/ route; SM ruling at the bundle 4
        # merge-down): a session whose course its teacher can no longer reach
        # answers exactly like a missing one, as status and session-results
        # do. item_retry's own per-item and in-claim checks stay behind it.
        if not teacher_may_reach(request.user, session):
            raise Http404("No BatchUploadSession matches the given query.")
        item = get_object_or_404(
            BackgroundProcessingTask, id=_uuid_or_404(item_id), batch_session=session
        )
        item = item_retry.retry_item(item, request.user, request=request)
        return Response(
            item_result(item, get_task_context(item)), status=status.HTTP_202_ACCEPTED
        )

    @extend_schema(
        tags=["Tasks"],
        summary="Retry every retryable failed item of a batch (FR-A-07 S7b)",
        description=(
            'Body (optional): {"reason_codes": [...]} to retry only those codes. '
            "202 with {retried: [item_id], skipped: [{item_id, reason_code}]}."
        ),
    )
    @action(
        detail=False,
        methods=["post"],
        url_path=r"session/(?P<session_id>[^/.]+)/retry-failed",
        url_name="retry-failed",
        permission_classes=[IsAuthenticated, IsTeacher, HasCreditBalance],
    )
    def retry_failed(self, request, session_id=None):
        session = self._own_session(request, session_id)
        # H-38: the same session-level rule as the other tasks/ routes.
        if not teacher_may_reach(request.user, session):
            raise Http404("No BatchUploadSession matches the given query.")
        reason_codes = request.data.get("reason_codes")
        if reason_codes is not None and (
            not isinstance(reason_codes, list)
            or not all(isinstance(code, str) for code in reason_codes)
        ):
            raise ParseError("reason_codes must be a list of reason codes.")
        retried, skipped = item_retry.retry_failed(
            session, request.user, reason_codes=reason_codes, request=request
        )
        return Response(
            {"retried": retried, "skipped": skipped}, status=status.HTTP_202_ACCEPTED
        )

    @staticmethod
    def _own_session(request, session_id):
        return get_object_or_404(
            BatchUploadSession, id=_uuid_or_404(session_id), teacher=request.user
        )

    @extend_schema(
        tags=["Tasks"],
        summary="Retrieve batch upload session results",
        description="""
            Retrieve the processing status and results of a batch upload session.

            This endpoint returns the progress of the background tasks, indicating how many
            files have been processed and the overall completion status. It provides lists of
            successfully processed submissions, failures, cancellations, and pending tasks.
            """,
        responses={
            200: OpenApiResponse(
                description="Session results retrieved successfully.",
                response=BatchSessionResultSerializer,
                examples=[
                    OpenApiExample(
                        "In Progress",
                        value={
                            "progress": "2 / 4",
                            "percent": 50,
                            "is_complete": False,
                            "success_count": 2,
                            "failure_count": 0,
                            "cancelled_count": 0,
                            "pending_count": 2,
                            "resource_type": "assignment",
                            "resource_id": "055eb99a-d9af-4671-ac94-38133376e942",
                            "action": "grade",
                            "additional_ids": {},
                            "success_list": [
                                {
                                    "status": "SUCCESS",
                                    "file_name": "student_a.pdf",
                                    "task_id": "b2c3d4e5",
                                    "error": None,
                                    "context": {
                                        "resource_type": "submission",
                                        "resource_id": "4321",
                                        "action": "grade",
                                        "additional_ids": {},
                                    },
                                },
                            ],
                            "failure_list": [],
                            "cancelled_list": [],
                            "pending_list": [
                                {
                                    "status": "PENDING",
                                    "file_name": "student_b.pdf",
                                    "task_id": "b2c3d4e6",
                                    "error": None,
                                    "context": {
                                        "resource_type": "submission",
                                        "resource_id": "4322",
                                        "action": "grade",
                                        "additional_ids": {},
                                    },
                                }
                            ],
                        },
                    ),
                    OpenApiExample(
                        "Completed with failures",
                        value={
                            "progress": "3 / 3",
                            "percent": 100,
                            "is_complete": True,
                            "success_count": 2,
                            "failure_count": 1,
                            "cancelled_count": 0,
                            "pending_count": 0,
                            "resource_type": "assignment",
                            "resource_id": "055eb99a-d9af-4671-ac94-38133376e942",
                            "action": "grade",
                            "additional_ids": {},
                            "success_list": [
                                {
                                    "status": "SUCCESS",
                                    "file_name": "student_a.pdf",
                                    "task_id": "b2c3d4e5",
                                    "error": None,
                                    "context": {
                                        "resource_type": "submission",
                                        "resource_id": "4321",
                                        "action": "grade",
                                        "additional_ids": {},
                                    },
                                },
                            ],
                            "failure_list": [
                                {
                                    "status": "FAILURE",
                                    "file_name": "unknown_file.pdf",
                                    "task_id": "b2c3d4e7",
                                    "error": "Could not identify or associate a student with this paper",
                                    "context": {
                                        "resource_type": "submission",
                                        "resource_id": None,
                                        "action": "grade",
                                        "additional_ids": {},
                                    },
                                }
                            ],
                            "cancelled_list": [],
                            "pending_list": [],
                        },
                    ),
                ],
            ),
            404: OpenApiResponse(
                description="Session not found.",
            ),
        },
    )
    @action(
        detail=False, methods=["GET"], url_path="session-results/(?P<session_id>[^/.]+)"
    )
    def session_results(self, request, session_id=None):
        session = get_object_or_404(
            BatchUploadSession, id=session_id, teacher=request.user
        )
        # H-38: a session whose course its teacher can no longer reach
        # answers exactly like a missing one.
        if not teacher_may_reach(request.user, session):
            raise Http404("No BatchUploadSession matches the given query.")

        tracked_tasks = list(
            session.processing_tasks.select_related(
                "assignment",
                "submission",
                "submission__assignment",
                "assignment__course",
            )
            # FR-A-07 (S7a, v2's N1): every per-item list is in item_index
            # order (upload order), whatever order the items finished in;
            # items with no index (older rows) come last, oldest first.
            .order_by(F("item_index").asc(nulls_last=True), "created_at")
        )

        if tracked_tasks:
            success = []
            failures = []
            cancelled = []
            pending = []

            for processing_task in tracked_tasks:
                normalize_processing_task_status(processing_task)
                processing_task.refresh_from_db()

                # Get context for this specific task
                task_context = get_task_context(processing_task)

                # FR-A-07 (S7a): the old keys, plus the item's own result.
                task_entry = item_result(processing_task, task_context)

                if processing_task.status == "SUCCESS":
                    success.append(task_entry)
                elif processing_task.status == "FAILURE":
                    failures.append(task_entry)
                elif processing_task.status == "CANCELLED":
                    cancelled.append(task_entry)
                else:
                    pending.append(task_entry)

            completed = len(success) + len(failures) + len(cancelled)
            total = session.total_files or len(tracked_tasks)
            percentage = (completed / total) * 100 if total > 0 else 0

            # Get session-level context
            session_context = get_session_context(session)

            data = {
                "progress": f"{completed} / {total}",
                "percent": round(percentage),
                "is_complete": completed == total,
                "success_count": len(success),
                "failure_count": len(failures),
                "cancelled_count": len(cancelled),
                "pending_count": len(pending),
                # Session-level context
                "resource_type": session_context["resource_type"],
                "resource_id": session_context["resource_id"],
                "action": session_context["action"],
                "additional_ids": session_context["additional_ids"],
                # Lists
                "success_list": success,
                "failure_list": failures,
                "cancelled_list": cancelled,
                "pending_list": pending,
                **failure_summary(failures),
            }
        else:
            # Fallback to session.results (legacy, for sessions without tracked tasks)
            success = [r for r in session.results if r["status"] == "SUCCESS"]
            failures = [r for r in session.results if r["status"] == "FAILED"]
            # For legacy entries, we don't have context, so we set context to None
            # We'll map them to a minimal entry
            success_entries = [
                {
                    "status": r["status"],
                    "file_name": r.get("file_name"),
                    "task_id": None,
                    "error": r.get("error"),
                    "context": None,
                }
                for r in success
            ]
            failure_entries = [
                {
                    "status": r["status"],
                    "file_name": r.get("file_name"),
                    "task_id": None,
                    "error": r.get("error"),
                    "context": None,
                }
                for r in failures
            ]

            completed = len(session.results)
            total = session.total_files
            percentage = (completed / total) * 100 if total > 0 else 0

            # Still compute session context using the helper (which may use assignment/course)
            session_context = get_session_context(session)

            data = {
                "progress": f"{completed} / {total}",
                "percent": round(percentage),
                "is_complete": completed == total,
                "success_count": len(success),
                "failure_count": len(failures),
                "cancelled_count": 0,
                "pending_count": max(total - completed, 0),
                # Session-level context
                "resource_type": session_context["resource_type"],
                "resource_id": session_context["resource_id"],
                "action": session_context["action"],
                "additional_ids": session_context["additional_ids"],
                "success_list": success_entries,
                "failure_list": failure_entries,
                "cancelled_list": [],
                "pending_list": [],
                # FR-A-07: a legacy session has no codes; its failures count
                # as unclassified.
                "failure_codes": (
                    {UNCLASSIFIED: len(failure_entries)} if failure_entries else {}
                ),
                "stopped_at_item": None,
                "resumable": False,
            }

        # Use the new serializer
        serializer = BatchSessionResultSerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema_view(
    list=extend_schema(
        tags=["Beta Whitelist"],
        summary="List all beta whitelisted emails",
        description="Retrieve a paginated list of all emails that are whitelisted for the private beta.",
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
                description="Number of results per page (max 100)",
            ),
        ],
        responses={200: BetaWhitelistSerializer(many=True)},
    ),
    create=extend_schema(
        tags=["Beta Whitelist"],
        summary="Add an email to the beta whitelist",
        description="Add a new email to the private beta whitelist.",
        request=BetaWhitelistSerializer,
        responses={201: BetaWhitelistSerializer},
    ),
    retrieve=extend_schema(
        tags=["Beta Whitelist"],
        summary="Retrieve a beta whitelist entry",
        description="Retrieve detailed information about a specific entry in the beta whitelist.",
        responses={200: BetaWhitelistSerializer},
    ),
    partial_update=extend_schema(
        tags=["Beta Whitelist"],
        summary="Update a beta whitelist entry",
        description="Update an existing entry in the beta whitelist.",
        request=BetaWhitelistSerializer,
        responses={200: BetaWhitelistSerializer},
    ),
    destroy=extend_schema(
        tags=["Beta Whitelist"],
        summary="Remove an email from the beta whitelist",
        description="Delete an entry from the private beta whitelist.",
        responses={204: OpenApiResponse(description="Entry deleted successfully")},
    ),
)
class BetaWhitelistViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing the private beta whitelist.

    Only SuperAdmins are allowed to access these endpoints.
    """

    queryset = BetaWhitelist.objects.all()
    serializer_class = BetaWhitelistSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    pagination_class = StandardPageNumberPagination
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    search_fields = ["email", "mode"]
    ordering_fields = ["email", "mode", "created_at"]


@extend_schema_view(
    list=extend_schema(
        tags=["Waitlist"],
        summary="List all waitlist entries",
        description="Retrieve a paginated list of all users currently on the waiting list.",
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
                description="Number of results per page (max 100)",
            ),
        ],
        responses={200: WaitlistSerializer(many=True)},
    ),
    retrieve=extend_schema(
        tags=["Waitlist"],
        summary="Retrieve a waitlist entry",
        description="Retrieve detailed information about a specific entry in the waitlist.",
        responses={200: WaitlistSerializer},
    ),
    destroy=extend_schema(
        tags=["Waitlist"],
        summary="Remove a user from the waitlist",
        description="Delete a user from the waiting list.",
        responses={204: OpenApiResponse(description="User removed from waitlist")},
    ),
)
class WaitlistViewSet(viewsets.ModelViewSet):
    """
    API endpoint for managing the waiting list.

    Only SuperAdmins are allowed to access these endpoints.
    """

    queryset = Waitlist.objects.all()
    serializer_class = WaitlistSerializer
    permission_classes = [IsAuthenticated, IsSuperAdmin]
    pagination_class = StandardPageNumberPagination
    http_method_names = ["get", "post", "delete", "head", "options"]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    search_fields = ["email"]
    ordering_fields = ["email", "created_at"]

    @extend_schema(
        tags=["Waitlist"],
        summary="Transfer user to whitelist",
        description="""
        Transfers a user from the waitlist to the beta whitelist.
        This action creates a new BetaWhitelist entry with mode='WAITLIST'
        and removes the original Waitlist entry.
        """,
        request=None,
        responses={201: BetaWhitelistSerializer},
    )
    @action(detail=True, methods=["post"])
    def transfer(self, request, pk=None):
        waitlist_user = self.get_object()
        whitelist_user = waitlist_user.transfer_to_whitelist()
        serializer = BetaWhitelistSerializer(whitelist_user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
