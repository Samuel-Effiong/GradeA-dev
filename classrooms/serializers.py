from rest_framework import serializers

from .models import AcademicTerm, Classroom, ClassroomSettings, Section, StudentSection


class AcademicTermSerializer(serializers.ModelSerializer):
    """Serializer for the AcademicTerm model."""

    class Meta:
        model = AcademicTerm
        fields = ["id", "name", "type", "start_date", "end_date"]
        read_only_fields = ["id"]

    def validate(self, data):
        """Validate that start_date is before end_date."""
        if (
            data.get("start_date")
            and data.get("end_date")
            and data["start_date"] > data["end_date"]
        ):
            raise serializers.ValidationError("Start date must be before end date.")
        return data


class ClassroomSerializer(serializers.ModelSerializer):
    """Serializer for the Classroom model."""

    class Meta:
        model = Classroom
        fields = [
            "id",
            "name",
            "teacher",
            "academic_term",
            "created_at",
            "updated_at",
            "is_active",
            "description",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class ClassroomSettingsSerializer(serializers.ModelSerializer):
    """Serializer for the ClassroomSettings model."""

    class Meta:
        model = ClassroomSettings
        fields = ["id", "classroom", "allow_late_submission"]
        read_only_fields = ["id"]


class SectionSerializer(serializers.ModelSerializer):
    """Serializer for the Section model."""

    class Meta:
        model = Section
        fields = ["id", "name", "classroom", "is_active", "created_at"]
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
