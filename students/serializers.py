from django.utils import timezone
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

    def get_enrollment_status(self, obj) -> str | None:
        """Returns the enrollment status for this student in the course provided via serializer context.
        If no course is provided or a student is not enrolled in that course, returns None.
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
            "graded_at",
            "grading_confidence",
        ]

        read_only_fields = [
            "score",
            "feedback",
            "submission_date",
            "student_name",
            "assignment_title",
            "grade_at",
            "grading_confidence",
        ]

    def get_student_name(self, obj) -> str:
        return f"{obj.student.first_name} {obj.student.last_name}"

    def update(self, instance, validated_data):
        # request = self.context.get("request")
        # user = request.user if request else None

        # ONly track regardes AFTER AI has graded
        if instance.ai_graded_at:
            score_changed = (
                "score" in validated_data
                and validated_data["score"] != instance.ai_score
            )

            feedback_changed = (
                "feedback" in validated_data
                and validated_data["feedback"] != instance.ai_feedback
            )

            if score_changed or feedback_changed:
                instance.was_regraded = True
                instance.regraded_at = timezone.now()

        return super().update(instance, validated_data)


class StudentSubmissionUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentSubmission
        fields = [
            "id",
            "raw_input",
        ]

        read_only_fields = [
            "id",
        ]


class StudentSubmissionListSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    assignment_title = serializers.CharField(source="assignment.title", read_only=True)
    course = serializers.CharField(source="assignment.course.id", read_only=True)
    score = serializers.SerializerMethodField()
    score_percentage = serializers.SerializerMethodField()

    class Meta:
        model = StudentSubmission
        fields = [
            "id",
            "student",
            "student_name",
            "assignment",
            "assignment_title",
            "course",
            "submission_date",
            "score",
            "score_percentage",
            "max_points",
            "graded_at",
            "is_published",
            "grading_confidence",
        ]

        read_only_fields = [
            "submission_date",
            "student_name",
            "assignment_title",
            "course",
            "score",
            "score_percentage",
            "max_points",
            "graded_at",
            "is_published",
            "grading_confidence",
        ]

    def get_student_name(self, obj) -> str:
        return f"{obj.student.first_name} {obj.student.last_name}"

    def get_score(self, obj):
        request = self.context.get("request")
        if request and request.user.user_type == "STUDENT" and not obj.is_published:
            return None
        return obj.score

    def get_score_percentage(self, obj):
        request = self.context.get("request")
        if request and request.user.user_type == "STUDENT" and not obj.is_published:
            return None
        return obj.score_percentage


class StudentSubmissionDetailSerializer(serializers.ModelSerializer):
    score = serializers.SerializerMethodField()
    score_percentage = serializers.SerializerMethodField()
    # feedback = serializers.SerializerMethodField()
    formatted_grade = serializers.SerializerMethodField()

    class Meta:
        model = StudentSubmission
        fields = [
            "id",
            "assignment",
            "submission_date",
            "raw_input",
            "score",
            "score_percentage",
            "formatted_grade",
            "is_published",
        ]

        read_only_fields = [
            "assignment",
            "submission_date",
            "raw_input",
            "score",
            "score_percentage",
            "formatted_grade",
            "is_published",
        ]

    def get_score(self, obj):
        request = self.context.get("request")
        if request and request.user.user_type == "STUDENT" and not obj.is_published:
            return None
        return obj.score

    def get_score_percentage(self, obj):
        request = self.context.get("request")
        if request and request.user.user_type == "STUDENT" and not obj.is_published:
            return None
        return obj.score_percentage

    # def get_feedback(self, obj):
    #     request = self.context.get("request")
    #     if (
    #         request
    #         and request.user.user_type == "STUDENT"
    #         and not obj.is_published
    #     ):
    #         return None
    #     return obj.feedback

    def get_formatted_grade(self, obj):
        request = self.context.get("request")
        if request and request.user.user_type == "STUDENT" and not obj.is_published:
            return None
        return obj.formatted_grade


class StudentSubmissionGradeUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentSubmission
        fields = [
            "id",
            "score",
        ]


class StudentSubmissionTeacherFeedbackSerializer(serializers.ModelSerializer):
    class Meta:
        model = StudentSubmission
        fields = [
            "id",
            "formatted_grade",
        ]


class StudentSubmissionGradeAsyncSerializer(serializers.Serializer):
    """Serializer for async grade engine task ID"""

    submission_id = serializers.UUIDField(read_only=True)
    task_id = serializers.UUIDField(read_only=True)
    message = serializers.CharField(read_only=True)


class StudentSubmissionUploadAsyncSerializer(serializers.Serializer):
    """Serializer for async upload answers task ID"""

    task_id = serializers.UUIDField(read_only=True)
    message = serializers.CharField(read_only=True)


class StudentSubmissionFormattedGradeAsyncSerializer(serializers.Serializer):
    """Serializer for async formatted grade task ID"""

    submission_id = serializers.UUIDField(read_only=True)
    task_id = serializers.UUIDField(read_only=True)
    message = serializers.CharField(read_only=True)
