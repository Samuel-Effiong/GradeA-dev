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
from users.exceptions import NotInBetaException
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
        queryset=School.objects.all(), required=False
    )
    settings = SettingsSerializer(read_only=True)
    credit_wallet = CreditWalletSerializer(read_only=True)
    is_system_generated_email = serializers.SerializerMethodField()

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
        # read_only_fields = ['id', 'user_type']

        extra_kwargs = {
            "email": {"required": True},
            "password": {"write_only": True},
            "is_active": {"read_only": True},
            "date_joined": {"read_only": True},
            "profile_image_url": {"read_only": True},
        }

    def validate_email(self, value):
        """
        Check if the email is in the BetaWhitelist.
        If not, add to Waitlist and block registration.
        """
        # We only enforce this for new registrations.
        # Update logic usually won't trigger this unless the email changes,
        # but for safety let's focus on the check itself.

        email = value.lower().strip()

        # Check if the email exists in BetaWhitelist and is active
        allowed = BetaWhitelist.objects.filter(
            email__iexact=email, is_active=True
        ).exists()

        if not allowed:
            # Record user in Waitlist
            Waitlist.objects.get_or_create(email=email)

            # Raise the exception that informs the user they are now on the waitlist
            raise NotInBetaException()

        return email

    def get_is_system_generated_email(self, obj) -> bool:
        return bool(obj.email and str(obj.email).endswith("@student.local"))

    def validate(self, attrs):
        from users.utils import is_business_email

        # Determine user_type and email for this operation
        user_type = attrs.get("user_type")
        if not user_type and self.instance:
            user_type = self.instance.user_type
        elif not user_type:
            user_type = UserTypes.TEACHER

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

        # Enforce email domain rules on account creation or when changing their email
        if email and (is_creating or email_changed):
            if user_type == UserTypes.TEACHER:
                # Teachers (individual track) MUST use personal email
                if is_business_email(email):
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
