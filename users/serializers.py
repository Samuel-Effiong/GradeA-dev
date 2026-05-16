from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from billing.serializers import CreditWalletSummarySerializer
from billing.services import AnalyticsService
from classrooms.models import School
from users.exceptions import NotInBetaException
from users.models import BetaWhitelist, CustomUser, Settings, Waitlist
from users.services import send_user_activation_email


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
        ]


class CustomUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, required=True, validators=[validate_password]
    )
    school = serializers.PrimaryKeyRelatedField(
        queryset=School.objects.all(), required=False
    )
    settings = SettingsSerializer(read_only=True)
    credit_wallet = CreditWalletSummarySerializer(read_only=True)
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

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get("is_system_generated_email"):
            data["email"] = None
        return data

    def create(self, validated_data):
        try:
            with transaction.atomic():
                user = CustomUser.objects.create_user(**validated_data)
                send_user_activation_email(user)

                return user

        except Exception as e:
            raise serializers.ValidationError(
                str(e), code="User creation error"
            ) from Exception

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
            raise serializers.ValidationError(
                str(e), code="User update error"
            ) from Exception


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


class TaskStatusSerializer(serializers.Serializer):
    """
    Serializer for reflecting the status of a Celery task.
    """

    task_id = serializers.UUIDField()
    status = serializers.CharField()
    meta = serializers.CharField(allow_null=True)


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
