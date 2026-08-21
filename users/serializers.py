import logging

from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from AutoGrader.error_messages import describe_user_error
from billing.serializers import CreditWalletSerializer
from billing.services import AnalyticsService
from classrooms.models import School

# from students.task_context import get_session_context, get_task_context
from users.models import (
    BetaWhitelist,
    CustomUser,
    RegistrationMethod,
    Settings,
    UserTypes,
    Waitlist,
)
from users.services import send_user_activation_email

logger = logging.getLogger(__name__)


class SettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = Settings
        fields = [
            "id",
            "user",
            "theme",
            "notify_student_submission",
            "notify_weekly_summary",
            "notify_assignment_due_reminder",
            "notify_grading_complete",
            "notify_new_assignment_posted",
            "notify_teacher_activity_alerts",
            "notify_at_risk_student_alerts",
        ]
        read_only_fields = ["id", "user"]


class CustomUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, required=True, validators=[validate_password]
    )
    school = serializers.PrimaryKeyRelatedField(
        queryset=School.objects.all(),
        required=False,
        # The model column is nullable (users/models.py) and detaching a user
        # from a school is a real operation - it's also the documented remedy
        # for promoting a school member to SUPER_ADMIN, which validate()
        # refuses while a school is still attached.
        allow_null=True,
    )
    settings = SettingsSerializer(read_only=True)
    credit_wallet = CreditWalletSerializer(read_only=True)
    is_system_generated_email = serializers.SerializerMethodField()

    # Fields that decide what a user is allowed to do. They must never be
    # settable by the account holder: this serializer backs PATCH
    # /users/<id>, so a writable user_type is a self-service promotion to
    # SUPER_ADMIN, and a writable school is a jump into another tenant.
    # Read-only by default; re-opened in __init__ for a genuine super admin
    # so that POST /users (SuperAdmin-only) can still mint non-teacher
    # accounts. Server-side callers that legitimately set these -
    # register_student, register_school_admin, license_service - assign
    # them on the model directly and never go through this serializer.
    PRIVILEGED_FIELDS = ("user_type", "school")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        request = self.context.get("request")
        user = getattr(request, "user", None)
        is_super_admin = bool(
            user
            and user.is_authenticated
            and user.user_type == UserTypes.SUPER_ADMIN
            and user.is_superuser
        )

        # Set both directions explicitly rather than relying on
        # Meta.read_only_fields: DRF ignores read_only_fields for fields
        # declared on the class, and `school` is declared above, so listing
        # it in Meta alone would leave it writable.
        for field_name in self.PRIVILEGED_FIELDS:
            if field_name in self.fields:
                self.fields[field_name].read_only = not is_super_admin

    class Meta:
        model = CustomUser
        fields = [
            "id",
            "school",
            "email",
            "first_name",
            "middle_name",
            "last_name",
            "bio",
            "profile_image",
            "profile_image_url",
            "user_type",
            "password",
            "is_active",
            "date_joined",
            "settings",
            "credit_wallet",
            "is_system_generated_email",
        ]
        read_only_fields = ["id", "user_type", "school", "date_joined"]

        extra_kwargs = {
            "email": {"required": True},
            "password": {"write_only": True},
            "is_active": {"read_only": True},
            "date_joined": {"read_only": True},
            "profile_image_url": {"read_only": True},
        }

    def validate_email(self, value):
        """
        Normalise the email.

        Signup is open: this used to reject any address missing from
        `BetaWhitelist`, park it on `Waitlist` and raise NotInBetaException,
        which gated BOTH email and Google registration (GoogleUserSerializer
        subclasses this) plus any superadmin-created account. That gate is
        gone. `BetaWhitelist`/`Waitlist` and their superadmin endpoints are
        kept as records - nothing reads them to allow or deny access.

        Case-folding stays: the rest of the stack (login, uniqueness,
        invitation lookups) assumes stored emails are lowercase.
        """
        return value.lower().strip()

    def get_is_system_generated_email(self, obj) -> bool:
        return bool(obj.email and str(obj.email).endswith("@student.local"))

    def validate(self, attrs):
        from users.utils import (
            is_business_email,
            is_exempt_email_domain,
            is_personal_email,
        )

        # Determine user_type and email for this operation
        user_type = attrs.get("user_type")
        if not user_type and self.instance:
            user_type = self.instance.user_type
        elif not user_type:
            user_type = UserTypes.TEACHER

        # --- Platform staff are not tenant members ---
        # A superadmin belongs to no school, and nothing should be able to
        # give them one. Without this, a superadmin could PATCH themselves
        # (or be PATCHed) to user_type=SCHOOL_ADMIN with a school attached,
        # after which they show up as that school's admin on every school
        # screen -- all of which select on user_type=SCHOOL_ADMIN
        # (classrooms/views.py) -- while STILL holding is_superuser. It also
        # silently revokes their own access, since IsSuperAdmin checks
        # user_type: the account ends up able to administer neither the
        # platform nor, legitimately, the school. The same invariant is
        # enforced on the billing side by
        # LicenseSubscriptionService.validate_admin_user().
        tenant_user_types = {
            UserTypes.SCHOOL_ADMIN,
            UserTypes.TEACHER,
            UserTypes.STUDENT,
        }
        target_is_platform_staff = bool(
            self.instance
            and (
                self.instance.is_superuser
                or self.instance.user_type == UserTypes.SUPER_ADMIN
            )
        )
        requested_user_type = attrs.get("user_type")

        if target_is_platform_staff:
            if attrs.get("school") is not None:
                raise serializers.ValidationError(
                    {
                        "school": (
                            "A super admin cannot be assigned to a school. "
                            "Super admins administer the platform across all "
                            "schools; to make someone a school's admin, use a "
                            "separate non-superuser account."
                        )
                    }
                )
            if requested_user_type in tenant_user_types:
                raise serializers.ValidationError(
                    {
                        "user_type": (
                            f"A super admin cannot be changed into a "
                            f"{requested_user_type}. Demote the account's "
                            "superuser status first, or use a separate "
                            "account for that role."
                        )
                    }
                )

        # ...and the mirror image: promoting an account to SUPER_ADMIN must
        # not leave a stale school attached to it.
        if requested_user_type == UserTypes.SUPER_ADMIN:
            effective_school = attrs.get(
                "school", getattr(self.instance, "school", None)
            )
            if effective_school is not None:
                raise serializers.ValidationError(
                    {
                        "user_type": (
                            "This account belongs to a school and so cannot be "
                            "made a super admin. Clear its school first "
                            '(set "school": null in the same request).'
                        )
                    }
                )

        email = attrs.get("email")
        if not email and self.instance:
            email = self.instance.email

        is_creating = self.instance is None
        email_changed = self.instance and self.instance.email != email

        # Students are not allowed to change their names after registration
        if not is_creating and user_type == UserTypes.STUDENT:
            for field in ["first_name", "middle_name", "last_name"]:
                if field in attrs and attrs.get(field) != getattr(self.instance, field):
                    raise serializers.ValidationError(
                        {field: "Students are not allowed to edit their name."}
                    )

        # A user_type change re-decides which rule applies to an email that was
        # already accepted under the other one. Without this, PATCHing an
        # existing teacher on jane@gmail.com to SCHOOL_ADMIN sailed through --
        # the guard below only fired on creation or on an email change -- and
        # left a school admin sitting on a personal address, exactly the state
        # these rules exist to prevent. (PRIVILEGED_FIELDS means only a super
        # admin can make that request, but "only a super admin can do it" is
        # not the same as "it is allowed".)
        user_type_changed = bool(
            self.instance
            and requested_user_type
            and requested_user_type != self.instance.user_type
        )

        # System-generated student addresses (@student.local) are placeholders,
        # not mailboxes anyone owns, and they are never personal OR business.
        # They must not be dragged into this check by a user_type change.
        is_system_generated = bool(email and email.endswith("@student.local"))

        # Enforce email domain rules on account creation, on an email change,
        # or when the account is being moved between tracks.
        if (
            email
            and (is_creating or email_changed or user_type_changed)
            and not is_system_generated
            and not is_exempt_email_domain(email)
        ):
            if user_type == UserTypes.TEACHER:
                # Teachers (individual track) MUST use a personal email. This
                # is a positive test now: an unrecognised domain is not
                # personal, so it is refused here rather than treated as
                # "well, it isn't on the consumer list".
                if not is_personal_email(email):
                    raise serializers.ValidationError(
                        {
                            "email": (
                                "Business emails are not allowed for individual teacher accounts. "
                                "Please use a personal email address or use the License subscription track."
                            )
                        }
                    )
            elif user_type == UserTypes.SCHOOL_ADMIN:
                # School admins MUST use business email
                if not is_business_email(email):
                    raise serializers.ValidationError(
                        {
                            "email": (
                                "Personal emails are not allowed for school admin accounts. "
                                "Please use a business email address."
                            )
                        }
                    )

        # Attaching a school is the other half of the crossing. The rules
        # above police the email against the user_type, but `school` was
        # never checked against either: a super admin (the only actor for
        # whom `school` is writable - see PRIVILEGED_FIELDS) could attach a
        # school to a teacher sitting on a personal address, making an
        # individual-track account a member of a school tenant. It does not
        # by itself hand over a license seat - enrollment re-checks the
        # email in LicenseSubscriptionService._get_or_invite_teacher - but it
        # satisfies the school-membership half of that check and leaves the
        # account half-way across a line that is supposed to be absolute.
        incoming_school = attrs.get("school")
        if (
            incoming_school is not None
            and "school" in attrs
            and email
            and not is_system_generated
            and not is_exempt_email_domain(email)
            and user_type == UserTypes.TEACHER
            and not is_business_email(email)
        ):
            raise serializers.ValidationError(
                {
                    "school": (
                        "This account uses a personal email address and so "
                        "belongs to the individual track. A teacher can only "
                        "be attached to a school on a business email address "
                        "- invite them through the school's license instead."
                    )
                }
            )

        return attrs

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get("is_system_generated_email"):
            data["email"] = None
        return data

    def create(self, validated_data):
        try:
            with transaction.atomic():
                user = CustomUser.objects.create_user(**validated_data)

                if user.registration_method == RegistrationMethod.EMAIL:
                    try:
                        send_user_activation_email(user)
                    except Exception:
                        logger.exception(
                            "Registration email dispatch failed for user %s",
                            getattr(user, "email", None),
                        )

                return user

        except Exception as e:
            logger.error("User creation failed", exc_info=e)
            raise serializers.ValidationError(
                describe_user_error(
                    e,
                    fallback_message=(
                        "We couldn't create your account. Please try again."
                    ),
                ),
                code="user_creation_error",
            ) from e

    def update(self, instance, validated_data):
        try:
            with transaction.atomic():
                password = validated_data.pop("password", None)

                for attr, value in validated_data.items():
                    setattr(instance, attr, value)

                if password:
                    instance.set_password(password)

                instance.save()
                return instance
        except Exception as e:
            logger.error("User update failed", exc_info=e)
            raise serializers.ValidationError(
                describe_user_error(
                    e,
                    fallback_message=(
                        "We couldn't update your account. Please try again."
                    ),
                ),
                code="user_update_error",
            ) from e


class GoogleUserSerializer(CustomUserSerializer):
    password = serializers.CharField(write_only=True, required=False)

    class Meta(CustomUserSerializer.Meta):
        # Override the extra_kwargs to make password NOT required
        extra_kwargs = {
            **CustomUserSerializer.Meta.extra_kwargs,
            "password": {"required": False, "write_only": True},
        }


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs):
        if "email" in attrs:
            attrs["email"] = attrs["email"].lower().strip()

        data = super().validate(attrs)
        user_data = CustomUserSerializer(self.user).data

        data.update({"user": user_data})

        # Track user activity (distinct login days)
        AnalyticsService.track_activity(self.user)

        return data


class OTPSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    otp_type = serializers.CharField(required=True)

    def validate_otp_type(self, value):
        if value not in ("VERIFY_EMAIL", "RESET_PASSWORD"):
            raise serializers.ValidationError("Invalid OTP type")
        return value


class VerifyCustomUserSerializer(serializers.Serializer):
    token = serializers.CharField(required=True)
    email = serializers.EmailField(required=True)
    user = CustomUserSerializer(read_only=True)


class ResetPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    otp = serializers.CharField(required=True)
    new_password = serializers.CharField(
        required=True, write_only=True, validators=[validate_password]
    )


class ChangePasswordSerializer(serializers.Serializer):
    # otp = serializers.CharField(required=False)
    current_password = serializers.CharField(required=True, write_only=True)
    new_password = serializers.CharField(
        required=True, write_only=True, validators=[validate_password]
    )

    def validate(self, data):
        if data["current_password"] == data["new_password"]:
            raise serializers.ValidationError(
                "New password cannot be the same as the old one"
            )
        return data


class TaskContextSerializer(serializers.Serializer):
    """Serializer for the context of a background task."""

    resource_type = serializers.CharField(allow_null=True)
    resource_id = serializers.CharField(allow_null=True)
    action = serializers.CharField(allow_null=True)
    additional_ids = serializers.DictField(
        child=serializers.CharField(), allow_null=True
    )


class TaskStatusSerializer(serializers.Serializer):
    """
    Serializer for reflecting the status of a Celery task.
    """

    task_id = serializers.UUIDField()
    status = serializers.CharField()
    meta = serializers.CharField(allow_null=True)

    # Add context fields
    resource_type = serializers.CharField(allow_null=True)
    resource_id = serializers.CharField(allow_null=True)
    action = serializers.CharField(allow_null=True)
    additional_ids = serializers.DictField(
        child=serializers.CharField(), allow_null=True
    )


class BatchSessionResultTaskEntrySerializer(serializers.Serializer):
    """Per-task entry in a batch session results list."""

    status = serializers.CharField()
    file_name = serializers.CharField(allow_null=True)
    task_id = serializers.CharField(allow_null=True)
    error = serializers.CharField(allow_null=True)
    # Add context for each task
    context = TaskContextSerializer(allow_null=True)


class BatchSessionResultSerializer(serializers.Serializer):
    """Aggregated results for a batch session, now with context."""

    progress = serializers.CharField()
    percent = serializers.IntegerField()
    is_complete = serializers.BooleanField()
    success_count = serializers.IntegerField()
    failure_count = serializers.IntegerField()
    cancelled_count = serializers.IntegerField()
    pending_count = serializers.IntegerField()
    # Session-level context
    resource_type = serializers.CharField(allow_null=True)
    resource_id = serializers.CharField(allow_null=True)
    action = serializers.CharField(allow_null=True)
    additional_ids = serializers.DictField(
        child=serializers.CharField(), allow_null=True
    )
    # Lists of entries
    success_list = BatchSessionResultTaskEntrySerializer(many=True, allow_null=True)
    failure_list = BatchSessionResultTaskEntrySerializer(many=True, allow_null=True)
    cancelled_list = BatchSessionResultTaskEntrySerializer(many=True, allow_null=True)
    pending_list = BatchSessionResultTaskEntrySerializer(many=True, allow_null=True)


class TaskCancelSerializer(serializers.Serializer):
    task_id = serializers.UUIDField()
    status = serializers.CharField()
    message = serializers.CharField()


class BatchSessionCancelSerializer(serializers.Serializer):
    session_id = serializers.UUIDField()
    cancelled_count = serializers.IntegerField()
    message = serializers.CharField()


class BetaWhitelistSerializer(serializers.ModelSerializer):
    class Meta:
        model = BetaWhitelist
        fields = ["id", "email", "mode", "is_active", "created_at"]
        read_only_fields = ["id", "created_at"]


class WaitlistSerializer(serializers.ModelSerializer):
    class Meta:
        model = Waitlist
        fields = ["id", "email", "created_at"]
        read_only_fields = ["id", "created_at"]
