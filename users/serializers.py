from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers

from users.models import CustomUser


class CustomUserSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True, required=True, validators=[validate_password]
    )

    class Meta:
        model = CustomUser
        fields = ["id", "email", "first_name", "last_name", "user_type", "password"]
        read_only_fields = ["id", "user_type"]
        extra_kwargs = {
            "email": {"required": True},
            "user_type": {"required": False},
        }

    def create(self, validated_data):
        try:
            with transaction.atomic():
                user = CustomUser.objects.create_user(**validated_data)
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
