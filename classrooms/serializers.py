import secrets

from django.core.validators import MinLengthValidator
from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from students.serializers import StudentSerializer
from users.models import CustomUser, UserTypes

from .models import (
    Course,
    CourseCategory,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
    Topic,
)


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

        extra_kwargs = {
            "course": {"write_only": True},
        }

        def validate_name(self, value):
            """Validate that name is not empty."""
            if not value.strip():
                raise serializers.ValidationError("Name cannot be empty.")
            return value

        validators = [
            UniqueTogetherValidator(
                queryset=Topic.objects.all(),
                fields=["name", "course"],
                message="This Course already has this topic",
            )
        ]


class CourseSerializer(serializers.ModelSerializer):
    """Serializer for the Section model.
    I ask for open eyes and hears to every person using this software
    """

    teacher = serializers.HiddenField(default=serializers.CurrentUserDefault())

    student_count = serializers.SerializerMethodField(method_name="get_student_count")
    students = serializers.SerializerMethodField(method_name="get_students")

    topics = TopicSerializer(many=True, read_only=True)
    topic_names = serializers.ListField(
        child=serializers.CharField(max_length=100),
        write_only=True,
        required=False,
        allow_empty=True,
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
            "topics",
            "topic_names",
        ]
        read_only_fields = ["id", "created_at", "teacher"]

        extra_kwargs = {"is_active": {"required": False}}

    def create(self, validated_data):
        """Create course and associated topics from topic_names."""
        topic_names = validated_data.pop("topic_names", [])
        course = super().create(validated_data)

        # Create topics from the list of names
        for topic_name in topic_names:
            Topic.objects.get_or_create(name=topic_name.strip(), course=course)

        return course

    def update(self, instance, validated_data):
        """Update course and replace topics if topic_names is provided."""
        topic_names = validated_data.pop("topic_names", None)
        course = super().update(instance, validated_data)

        # If topic_names is provided, replace existing topics
        if topic_names is not None:
            # Delete existing topics
            instance.topics.all().delete()

            # Create new topics from the list of names
            for topic_name in topic_names:
                Topic.objects.get_or_create(name=topic_name.strip(), course=course)

        return course

    def get_student_count(self, obj) -> int:
        # return (
        #     StudentCourse.objects.filter(course=obj)
        #     .exclude(enrollment_status__iexact="withdrawn")
        #     .distinct()
        #     .count()
        # )

        if hasattr(obj, "student_count"):
            return obj.student_count

        return (
            obj.enrollments.exclude(enrollment_status=EnrollmentStatusType.WITHDRAWN)
            .distinct()
            .count()
        )

        # return getattr(obj, "student_count", 0)

    @extend_schema_field(StudentSerializer(many=True))
    def get_students(self, obj):
        # # TODO: Add users, to ensure that it is by the teacher
        # enrolled_students = (
        #     CustomUser.objects.filter(enrollments__course=obj)
        #     .exclude(
        #         enrollments__course=obj,
        #         enrollments__enrollment_status__iexact="withdrawn",
        #     )
        #     .distinct()
        # )

        if hasattr(obj, "active_enrollments"):
            enrolled_students = [
                enrollment.student for enrollment in obj.active_enrollments
            ]
        else:
            enrolled_students = [
                enrollment.student
                for enrollment in obj.enrollments.exclude(
                    enrollment_status=EnrollmentStatusType.WITHDRAWN
                ).select_related("student")
            ]

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


class DirectAddStudentSerializer(serializers.Serializer):
    """Serializer for directly adding and activating a student in a course."""

    first_name = serializers.CharField(
        max_length=150, validators=[MinLengthValidator(2)], required=True
    )
    middle_name = serializers.CharField(
        max_length=150,
        default="",
    )
    last_name = serializers.CharField(
        max_length=150, validators=[MinLengthValidator(2)], required=True
    )
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)

    def validate_email(self, value):
        if not value:
            return value

        if CustomUser.objects.filter(
            email=value,
            user_type=UserTypes.TEACHER,
        ).exists():
            raise serializers.ValidationError(
                "This email belongs to a teacher account and cannot be added as a student."
            )

        return value

    def create(self, validated_data):

        first_name = validated_data["first_name"]
        last_name = validated_data["last_name"]
        email = validated_data.get("email")
        course = self.context.get("course")

        if not course:
            raise serializers.ValidationError("Course context is required.")

        # Generate a tracked backend email if not provided
        if not email:
            unique_suffix = secrets.randbelow(10000)
            safe_first = "".join(c for c in first_name.lower() if c.isalnum())
            safe_last = "".join(c for c in last_name.lower() if c.isalnum())
            email = f"{safe_first}.{safe_last}{unique_suffix}@student.local"

        with transaction.atomic():
            student = CustomUser.objects.filter(email=email).first()

            if student:
                # Check if already enrolled
                if StudentCourse.objects.filter(
                    student=student, course=course
                ).exists():
                    raise serializers.ValidationError(
                        "Student is already enrolled in this course."
                    )

                StudentCourse.objects.create(
                    student=student,
                    course=course,
                    enrollment_status=EnrollmentStatusType.ENROLLED,
                    auto_added=True,
                )
            else:
                student = CustomUser.objects.create(
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    user_type=UserTypes.STUDENT,
                    school=course.teacher.school,
                    is_active=True,
                )
                student.set_password("student123!")
                student.save()

                StudentCourse.objects.create(
                    student=student,
                    course=course,
                    enrollment_status=EnrollmentStatusType.ENROLLED,
                    auto_added=True,
                )

        return student


class StudentRegistrationCompletionSerializer(serializers.Serializer):
    """Serializer for completing student registration."""

    first_name = serializers.CharField(
        max_length=150,
        validators=[MinLengthValidator(2)],
    )
    middle_name = serializers.CharField(max_length=150, default="")
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
