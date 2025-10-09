from rest_framework import serializers

from users.models import CustomUser

from .models import StudentSubmission


class StudentSerializer(serializers.ModelSerializer):

    class Meta:
        model = CustomUser
        fields = ["id", "first_name", "last_name", "email"]


class StudentSubmissionSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    assignment_title = serializers.CharField(source="assignment.title", read_only=True)

    class Meta:
        model = StudentSubmission
        fields = [
            "id",
            "student",
            "student_name",
            "assignment",
            "assignment_title",
            "answers",
            "score",
            "feedback",
            "submission_date",
        ]

        read_only_fields = [
            "score",
            "feedback",
            "submission_date",
            "student_name",
            "assignment_title",
        ]

    def get_student_name(self, obj) -> str:
        return f"{obj.student.first_name} {obj.student.last_name}"
