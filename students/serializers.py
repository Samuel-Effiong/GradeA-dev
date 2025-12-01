from rest_framework import serializers

from users.models import CustomUser

from .models import StudentSubmission


class StudentSerializer(serializers.ModelSerializer):
    enrollment_status = serializers.SerializerMethodField(
        method_name="get_enrollment_status"
    )

    class Meta:
        model = CustomUser
        fields = [
            "id",
            "first_name",
            "last_name",
            "email",
            "is_active",
            "enrollment_status",
        ]

    def get_enrollment_status(self, obj):
        """Returns the enrollment status for this student in the course provided via serializer context.
        If no course is provided or student is not enrolled in that course, returns None.
        """
        course = self.context.get("course")

        if not course:
            return None

        enrollment = obj.enrollments.filter(course=course).first()
        return enrollment.enrollment_status if enrollment else None


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
