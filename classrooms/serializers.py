from django.core.validators import MinLengthValidator
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from students.serializers import StudentSerializer
from users.models import CustomUser, UserTypes

from .models import Course, CourseCategory, School, Session, StudentCourse, Topic


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
    """Serializer for the Section model.
    I ask for open eyes and hears to every person using this software
    """

    teacher = serializers.HiddenField(default=serializers.CurrentUserDefault())

    student_count = serializers.SerializerMethodField(method_name="get_student_count")
    students = serializers.SerializerMethodField(method_name="get_students")

    # Deprecated field added for frontend compatibility
    categories = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )

    class Meta:
        model = Course
        fields = [
            "id",
            "name",
            "session",
            "teacher",
            "is_active",
            "created_at",
            "description",
            "student_count",
            "students",
            "categories",
        ]
        read_only_fields = ["id", "created_at", "teacher"]

        extra_kwargs = {"is_active": {"required": False}}

    def create(self, validated_data):
        """Pop categories before creating the course."""
        validated_data.pop("categories", None)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        """Pop categories before updating the course."""
        validated_data.pop("categories", None)
        return super().update(instance, validated_data)

    def get_student_count(self, obj) -> int:
        return (
            StudentCourse.objects.filter(course=obj)
            .exclude(enrollment_status__iexact="withdrawn")
            .distinct()
            .count()
        )

    @extend_schema_field(StudentSerializer(many=True))
    def get_students(self, obj):
        # TODO: Add users, to ensure that it is by the teacher
        enrolled_students = (
            CustomUser.objects.filter(enrollments__course=obj)
            .exclude(
                enrollments__course=obj,
                enrollments__enrollment_status__iexact="withdrawn",
            )
            .distinct()
        )

        serializer = StudentSerializer(
            enrolled_students, many=True, context={"course": obj}
        )

        return serializer.data


class StudentCourseSerializer(serializers.ModelSerializer):
    """Serializer for the StudentSection model."""

    class Meta:
        model = StudentCourse
        fields = [
            "id",
            "student",
            "course",
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

    def validate_email(self, value):
        """
        Validate that the email:
        1. Is not associated with a teacher account
        2. Is a valid email format (handled by EmailField)
        """
        teacher_exists = CustomUser.objects.filter(
            email=value,
            user_type=UserTypes.TEACHER,
        ).exists()

        if teacher_exists:
            raise serializers.ValidationError(
                "This email belongs to a teacher account and cannot be added as a student."
            )

        return value


class StudentRegistrationCompletionSerializer(serializers.Serializer):
    """Serializer for completing student registration."""

    first_name = serializers.CharField(
        max_length=150,
        validators=[MinLengthValidator(2)],
    )
    last_name = serializers.CharField(
        max_length=150,
        validators=[MinLengthValidator(2)],
    )
    password = serializers.CharField(
        write_only=True,
        min_length=8,
        validators=[MinLengthValidator(8)],
    )
    token = serializers.CharField(write_only=True)


class ExpiredTokenSerializer(serializers.Serializer):
    """Serializer for handling expired tokens."""

    token = serializers.CharField(required=True)


class SchoolSerializer(serializers.ModelSerializer):
    class Meta:
        model = School
        fields = ["id", "name", "address", "phone", "website", "created_at"]

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("School name cannot be empty.")
        return value


class CourseCategorySerializer(serializers.ModelSerializer):
    """Serializer for CourseCategory"""

    class Meta:
        model = CourseCategory
        fields = [
            "id",
            "name",
        ]
        read_only_fields = [
            "id",
        ]

    def validate_name(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError("Category name cannot be empty.")
        return value


class TopicSerializer(serializers.ModelSerializer):
    """Serializer for Topic"""

    class Meta:
        model = Topic
        fields = [
            "id",
            "name",
            "course",
        ]
        read_only_fields = [
            "id",
        ]

        validators = [
            UniqueTogetherValidator(
                queryset=Topic.objects.all(),
                fields=["name", "course"],
                message="This Course already has this topic",
            )
        ]
