from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from students.serializers import StudentSerializer
from users.models import CustomUser

from .models import (  # , Classroom, ClassroomSettings,
    Course,
    CourseCategory,
    Session,
    StudentCourse,
)

# from rest_framework.validators import UniqueTogetherValidator

# from users.models import CustomUser


class SessionSerializer(serializers.ModelSerializer):
    """Serializer for the AcademicTerm model."""

    teacher = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = Session
        fields = ["id", "name", "created_at", "teacher"]
        read_only_fields = ["id", "created_at", "teacher"]

        validators = [
            UniqueTogetherValidator(
                queryset=Session.objects.all(),
                fields=["name", "teacher"],
                message="This Teacher already has this session",
            )
        ]


class CourseSerializer(serializers.ModelSerializer):
    """Serializer for the Section model."""

    categories = serializers.PrimaryKeyRelatedField(
        many=True, queryset=CourseCategory.objects.all()
    )
    teacher = serializers.HiddenField(default=serializers.CurrentUserDefault())

    student_count = serializers.SerializerMethodField(method_name="get_student_count")
    students = serializers.SerializerMethodField(method_name="get_students")

    class Meta:
        model = Course
        fields = [
            "id",
            "name",
            "session",
            "teacher",
            "categories",
            "is_active",
            "created_at",
            "description",
            "student_count",
            "students",
        ]
        read_only_fields = ["id", "created_at", "teacher"]

    def get_student_count(self, obj) -> int:
        return StudentCourse.objects.filter(course=obj).count()

    def get_students(self, obj):
        # TODO: Add users, to ensure that it is by the teacher
        enrolled_students = CustomUser.objects.filter(
            enrollments__course=obj
        ).distinct()

        serializer = StudentSerializer(enrolled_students, many=True)

        return serializer.data


class StudentCourseSerializer(serializers.ModelSerializer):
    """Serializer for the StudentSection model."""

    class Meta:
        model = StudentCourse
        fields = [
            "id",
            "student",
            "course",
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


class AddStudentToCourseSerializer(serializers.Serializer):
    """Serializer for adding students to a course."""

    email = serializers.EmailField(required=True)
