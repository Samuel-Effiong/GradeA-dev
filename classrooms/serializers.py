from rest_framework import serializers

from .models import Section, Session, StudentSection  # , Classroom, ClassroomSettings,

# from rest_framework.validators import UniqueTogetherValidator

# from users.models import CustomUser


class SessionSerializer(serializers.ModelSerializer):
    """Serializer for the AcademicTerm model."""

    # teacher = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = Session
        fields = [
            "id",
            "name",
        ]
        read_only_fields = ["id"]

        # validators = [
        #     UniqueTogetherValidator(
        #         queryset=Session.objects.all(),
        #         fields=['name', 'teacher'],
        #         message="This Faculty already exists in this Tenant"
        #     )
        # ]


class SectionSerializer(serializers.ModelSerializer):
    """Serializer for the Section model."""

    class Meta:
        model = Section
        fields = [
            "id",
            "name",
            "session",
            "is_active",
            "created_at",
            "description",
        ]
        read_only_fields = ["id", "created_at"]


class StudentSectionSerializer(serializers.ModelSerializer):
    """Serializer for the StudentSection model."""

    class Meta:
        model = StudentSection
        fields = [
            "id",
            "student",
            "section",
            "is_active",
            "created_at",
            "enrollment_status",
            "withdrawal_date",
            "final_grade",
        ]
        read_only_fields = ["id", "created_at"]

    def validate_final_grade(self, value):
        """Validate that final_grade is between 0 and 100."""
        if value is not None and (value < 0 or value > 100):
            raise serializers.ValidationError("Final grade must be between 0 and 100.")
        return value

    def validate_participation_score(self, value):
        """Validate that participation_score is between 0 and 100."""
        if value < 0 or value > 100:
            raise serializers.ValidationError(
                "Participation score must be between 0 and 100."
            )
        return value
