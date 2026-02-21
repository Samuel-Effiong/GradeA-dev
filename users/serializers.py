from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

from classrooms.models import School
from users.models import CustomUser
from users.services import send_user_activation_email


class CustomUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, required=True, validators=[validate_password]
    )
    school = serializers.PrimaryKeyRelatedField(
        queryset=School.objects.all(), required=False
    )

    class Meta:
        model = CustomUser
        fields = [
            "id",
            "school",
            "email",
            "first_name",
            "last_name",
            "user_type",
            "password",
            "is_active",
        ]
        # read_only_fields = ['id', 'user_type']
        extra_kwargs = {
            "email": {"required": True},
            "password": {"write_only": True},
            "is_active": {"read_only": True},
        }

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
        data = super().validate(attrs)
        user_data = CustomUserSerializer(self.user).data

        data.update({"user": user_data})
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
    otp = serializers.CharField(required=True)
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

    task_id = serializers.CharField()
    status = serializers.CharField()
    meta = serializers.CharField(allow_null=True)
