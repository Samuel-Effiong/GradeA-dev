import logging
import secrets

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.validators import MinLengthValidator
from django.db import IntegrityError, transaction
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.validators import UniqueTogetherValidator

from assignments.models import AssignmentStatus
from assignments.serializers import AssignmentListSerializer  # , AssignmentSerializer
from AutoGrader.tasks import send_email_task
from billing.context import (
    clear_license_invitation_context,
    set_license_invitation_context,
)
from students.serializers import StudentSerializer
from students.services import get_grade_details
from users.models import CustomUser, UserTypes
from users.serializers import CustomUserSerializer

from .models import (
    Course,
    CourseCategory,
    EnrollmentStatusType,
    School,
    Session,
    SessionOwnerType,
    StudentCourse,
    Topic,
)


class SessionSerializer(serializers.ModelSerializer):
    """Serializer for the AcademicTerm model."""

    teacher = serializers.HiddenField(default=serializers.CurrentUserDefault())
    school = serializers.PrimaryKeyRelatedField(read_only=True)  # added
    owner_type = serializers.ChoiceField(
        choices=SessionOwnerType.choices, read_only=True
    )

    class Meta:
        model = Session
        fields = [
            "id",
            "name",
            "owner_type",
            "teacher",
            "school",
            "created_by",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "owner_type",
            "teacher",
            "school",
            "created_by",
            "created_at",
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
    assignment_count = serializers.SerializerMethodField()
    assignments = AssignmentListSerializer(many=True, read_only=True)

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
            "assignment_count",
            "students",
            "topics",
            "topic_names",
            "assignments",
        ]
        read_only_fields = ["id", "created_at", "teacher"]

        extra_kwargs = {"is_active": {"required": False}}

    def validate_session(self, value):
        """Ensure a course can only be attached to a session the requesting
        teacher actually owns/has access to — otherwise a teacher could
        point a course at another teacher's individual session or another
        school's session by guessing/enumerating the UUID."""
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        if user is None or not user.is_authenticated:
            return value

        if value.owner_type == SessionOwnerType.INDIVIDUAL:
            if user.is_under_license() or value.teacher_id != user.id:
                raise serializers.ValidationError(
                    "You do not have access to this session."
                )
        elif value.owner_type == SessionOwnerType.SCHOOL:
            if not user.is_under_license() or value.school_id != user.school_id:
                raise serializers.ValidationError(
                    "You do not have access to this session."
                )
        return value

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

    def get_assignment_count(self, obj):
        if hasattr(obj, "assignment_count"):
            return obj.assignment_count

        return obj.assignments.distinct().count()

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

    course_description = serializers.CharField(
        source="course.description", read_only=True
    )
    course_title = serializers.CharField(source="course.name", read_only=True)
    teacher = serializers.SerializerMethodField()
    total_no_of_assignment = serializers.SerializerMethodField()
    total_assignment_submitted = serializers.SerializerMethodField()
    submitted_assignment_percentage = serializers.SerializerMethodField()
    grade_letter = serializers.SerializerMethodField()

    class Meta:
        model = StudentCourse
        fields = [
            "id",
            "student",
            "course",
            "course_title",
            "course_description",
            "teacher",
            "total_no_of_assignment",
            "total_assignment_submitted",
            "submitted_assignment_percentage",
            "created_at",
            "enrollment_status",
            "withdrawal_date",
            "final_grade",
            "grade_letter",
            "auto_added",
        ]
        read_only_fields = ["id", "created_at", "auto_added"]

    def get_teacher(self, obj):
        return obj.course.teacher.get_full_name()

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

    def get_total_no_of_assignment(self, obj):
        return obj.course.assignments.count()

    def get_total_assignment_submitted(self, obj):
        return obj.course.assignments.filter(submissions__student=obj.student).count()

    def get_submitted_assignment_percentage(self, obj):
        if obj.course.assignments.count():
            return (
                obj.course.assignments.filter(submissions__student=obj.student).count()
                / obj.course.assignments.count()
            ) * 100
        return 0

    def get_grade_letter(self, obj):
        return get_grade_details(obj.final_grade) if obj.final_grade else None


class StudentCourseDetailSerializer(StudentCourseSerializer):
    assignments = serializers.SerializerMethodField()

    class Meta(StudentCourseSerializer.Meta):
        fields = StudentCourseSerializer.Meta.fields + ["assignments"]

    def get_assignments(self, obj):
        # Access pre-fetched assignments from the course
        # from assignment.models import AssignmentStatus

        assignments = obj.course.assignments.all().filter(
            status=AssignmentStatus.PUBLISHED
        )

        # Filter pre-fetched submissions for this specific student

        submissions = {
            s.assignment_id: s
            for s in obj.student.submissions.all()
            if s.assignment.course_id == obj.course_id
        }

        result = []

        for assignment in assignments:
            submission = submissions.get(assignment.id)

            # Logic: status and score
            if not submission:
                now = timezone.now()

                if assignment.due_date < now:
                    status = "OVERDUE"
                else:
                    status = "PENDING"
                score = None
            elif submission.graded_at and submission.is_published:
                status = "GRADED"
                score = submission.score
            else:
                status = "SUBMITTED"
                score = None

            result.append(
                {
                    "id": assignment.id,
                    "title": assignment.title,
                    "instructions": assignment.instructions,
                    "total_points": assignment.total_points,
                    "due_date": assignment.due_date,
                    "status": status,
                    "score": score,
                }
            )

        return result


class AddStudentToCourseSerializer(serializers.Serializer):
    """Serializer for adding students to a course."""

    email = serializers.EmailField(required=True)

    def validate_email(self, value):
        """
        Validate that the email:
        1. Is not associated with a teacher account
        2. Is a valid email format (handled by EmailField)
        """
        existing_user = CustomUser.objects.filter(email=value).first()

        if existing_user and existing_user.user_type == UserTypes.TEACHER:
            raise serializers.ValidationError(
                "This email belongs to a teacher account and cannot be added as a student."
            )

        if existing_user and existing_user.user_type != UserTypes.STUDENT:
            raise serializers.ValidationError(
                "This email already exists in the system and cannot be added as a "
                f"{existing_user.get_user_type_display().lower()}."
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
        allow_blank=True,
    )
    last_name = serializers.CharField(
        max_length=150, validators=[MinLengthValidator(2)], required=True
    )
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    profile_image = serializers.ImageField(required=False, allow_null=True)

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

    def validate(self, attrs):
        first_name = attrs.get("first_name")
        last_name = attrs.get("last_name")
        middle_name = attrs.get("middle_name", "")
        course = self.context.get("course")

        if course:
            existing_enrollments = StudentCourse.find_name_conflicts(
                course=course,
                first_name=first_name,
                last_name=last_name,
                middle_name=middle_name,
            )

            if existing_enrollments.exists():
                full_name = f"{first_name} {middle_name} {last_name}".replace(
                    "  ", " "
                ).strip()
                raise serializers.ValidationError(
                    f"A student with the exact name {full_name!r} is already enrolled in this course."
                )

        return attrs

    def create(self, validated_data):

        first_name = validated_data["first_name"]
        middle_name = validated_data.get("middle_name", "")
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
                    middle_name=middle_name,
                    last_name=last_name,
                    profile_image=validated_data.get("profile_image"),
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
    profile_image = serializers.ImageField(required=False, allow_null=True)


class ExpiredTokenSerializer(serializers.Serializer):
    """Serializer for handling expired tokens."""

    token = serializers.CharField(required=True)


logger = logging.getLogger(__name__)


class SchoolSerializer(serializers.ModelSerializer):
    class Meta:
        model = School
        fields = [
            "id",
            "name",
            "address",
            "phone",
            "website",
            "is_active",
            "created_at",
        ]

    def validate_name(self, value):
        if not value.strip():
            raise serializers.ValidationError("School name cannot be empty.")
        return value

    def create(self, validated_data):
        # DRF's BooleanField treats a missing field in HTML/multipart form
        # data as an explicit False on non-partial requests, which would
        # silently override the model's default=True whenever a caller
        # creates a school without passing is_active. New schools should
        # always start active — archiving only ever happens via destroy()
        # or an explicit PATCH.
        validated_data.pop("is_active", None)
        return super().create(validated_data)


def _send_school_admin_invitation_email(user, school):
    """Queue the invitation email for a newly created school admin.

    The admin has no usable password yet; the email links to a registration
    page where they set their own password using the activation token.
    """
    frontend_domain = settings.FRONTEND_DOMAIN
    activation_url = (
        f"https://{frontend_domain}/register/school-admin"
        f"?email={user.email}&token={user.activation_token}"
    )

    merge_data = {
        "title": f"You've been added as the admin for {school.name}",
        "name": user.get_full_name() or user.first_name,
        "top_content": (
            f"You have been set up as the school administrator for {school.name} on Grade A+.<br><br>"
            "Complete your registration to set up your password and start managing your school."
        ),
        "bottom_content": "This invitation link expires in 7 days.",
        "activation_url": activation_url,
        "current_year": timezone.now().year,
        "support_email": settings.SUPPORT_EMAIL,
    }

    user_email = user.email
    school_name = school.name

    def _dispatch():
        try:
            send_email_task.delay(
                subject=f"You've been added as the admin for {school_name}",
                message="",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user_email],
                html_message=None,
                template_id="ynrw7gy0ye2l2k8e",
                merge_data=merge_data,
            )
        except Exception:
            logger.exception(
                "Failed to queue school admin invitation email to %s for school %s.",
                user_email,
                school_name,
            )

    transaction.on_commit(_dispatch)


class SchoolWithAdminSerializer(serializers.Serializer):
    # School Fields
    school_name = serializers.CharField(max_length=255)
    school_address = serializers.CharField(required=False, allow_blank=True)
    school_phone = serializers.CharField(required=False, allow_blank=True)
    school_website = serializers.URLField(required=False, allow_blank=True)

    # Admin Fields
    admin_email = serializers.EmailField()
    admin_first_name = serializers.CharField(
        max_length=150, validators=[MinLengthValidator(2)]
    )
    admin_last_name = serializers.CharField(
        max_length=150, validators=[MinLengthValidator(2)]
    )
    admin_middle_name = serializers.CharField(
        max_length=150, required=False, allow_blank=True
    )

    admin_profile_image = serializers.ImageField(required=False, allow_null=True)

    def validate_admin_email(self, value):
        from users.utils import is_business_email, is_exempt_email_domain

        value = value.lower().strip()

        # School admins are onboarded here by a superadmin, not self-registering,
        # so the beta whitelist/waitlist gate doesn't apply. We do still enforce
        # the same business-email requirement as the plain /schools/admin path.
        if not is_exempt_email_domain(value) and not is_business_email(value):
            raise serializers.ValidationError(
                "Personal emails are not allowed for school admin accounts. "
                "Please use a business email address."
            )

        return value

    def validate(self, attrs):
        # Ensure school name is unique (case-insensitive)
        school_name = attrs.get("school_name")
        if school_name and School.objects.filter(name__iexact=school_name).exists():
            raise serializers.ValidationError("A school with this name already exists.")

        # Ensure admin email is not already used by any user
        if CustomUser.objects.filter(email__iexact=attrs.get("admin_email")).exists():
            raise serializers.ValidationError(
                "A user with this email address already exists."
            )

        return attrs

    def create(self, validated_data):
        try:
            with transaction.atomic():
                # 1. Create School
                school = School.objects.create(
                    name=validated_data["school_name"],
                    address=validated_data.get("school_address", ""),
                    phone=validated_data.get("school_phone", ""),
                    website=validated_data.get("school_website", ""),
                )

                # 2. Create Admin User with an invitation token and no usable
                # password. The admin sets their own password when they
                # complete registration via the emailed invite link — no
                # secret ever has to travel through an email template.
                admin_data = {
                    "email": validated_data["admin_email"],
                    "first_name": validated_data["admin_first_name"],
                    "last_name": validated_data["admin_last_name"],
                    "middle_name": validated_data.get("admin_middle_name", ""),
                    "profile_image": validated_data.get("admin_profile_image"),
                    "user_type": UserTypes.SCHOOL_ADMIN,
                    "school": school,
                    "is_active": False,
                    # A high-entropy token, not the 6-digit OTP used for the
                    # short-lived (15 min) email-verification flow — this link
                    # stays valid for 7 days and needs a much bigger keyspace
                    # to resist brute-forcing over that window.
                    "activation_token": secrets.token_urlsafe(32),
                    "activation_expires": timezone.now() + timezone.timedelta(days=7),
                }

                try:
                    # Set license context so the post_save signal skips trial activation
                    set_license_invitation_context(True)
                    # No password kwarg is passed, so CustomUser.objects.create_user()
                    # calls set_password(None), which Django resolves to an unusable
                    # password — equivalent to calling set_unusable_password().
                    user = CustomUser.objects.create_user(**admin_data)
                finally:
                    clear_license_invitation_context()

                # 3. Send the invitation email only after the transaction commits,
                # so a rollback can never leave a queued email referencing a
                # school/admin that doesn't exist.
                _send_school_admin_invitation_email(user, school)

                return {
                    "school": school,
                    "admin": user,
                }
        except IntegrityError as e:
            raise serializers.ValidationError(
                "A school or user with these details already exists."
            ) from e


class SchoolWithAdminResponseSerializer(serializers.Serializer):
    """Serializer for returning School and Admin after creation."""

    school = SchoolSerializer()
    admin = CustomUserSerializer()
    message = serializers.CharField(
        default="School and admin created successfully", read_only=True
    )


class SchoolAdminRegistrationCompletionSerializer(serializers.Serializer):
    """Serializer for a school admin completing registration via invite link."""

    email = serializers.EmailField(required=True)
    token = serializers.CharField(required=True, write_only=True)
    password = serializers.CharField(
        write_only=True, required=True, validators=[validate_password]
    )


class SchoolAdminSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    email = serializers.EmailField()
    school = serializers.CharField()
    teachers = serializers.IntegerField()
    students = serializers.IntegerField()
    tokens_used = serializers.IntegerField()
    sessions = serializers.IntegerField()


class SchoolSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    school_name = serializers.CharField()
    address = serializers.CharField(required=False, allow_null=True)
    phone = serializers.CharField(required=False, allow_null=True)
    website = serializers.URLField(required=False, allow_null=True)
    admin_id = serializers.UUIDField(required=False, allow_null=True)
    admin_name = serializers.CharField(required=False, allow_null=True)
    admin_email = serializers.EmailField(required=False, allow_null=True)
    teachers = serializers.IntegerField()
    students = serializers.IntegerField()
    tokens_used = serializers.IntegerField()
    sessions = serializers.IntegerField()
    is_active = serializers.BooleanField()


class SessionTeacherSerializer(serializers.Serializer):
    teacher_id = serializers.UUIDField()
    teacher_name = serializers.CharField()
    assignments = serializers.IntegerField()
    students = serializers.IntegerField()
    tokens = serializers.IntegerField()


class SessionBreakdownSerializer(serializers.Serializer):
    session_id = serializers.UUIDField()
    session_name = serializers.CharField()
    owner_type = serializers.CharField(
        help_text="INDIVIDUAL (one teacher) or SCHOOL (shared, may have multiple contributing teachers)."
    )
    assignments = serializers.IntegerField(help_text="Sum of teachers[].assignments.")
    students = serializers.IntegerField(help_text="Sum of teachers[].students.")
    tokens = serializers.IntegerField(help_text="Sum of teachers[].tokens.")
    teachers = SessionTeacherSerializer(
        many=True,
        help_text="Per-teacher breakdown within this session. Empty for a "
        "SCHOOL session with no courses yet.",
    )


class SchoolDetailSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    school_name = serializers.CharField()
    address = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    phone = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    website = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    admin_id = serializers.UUIDField(required=False, allow_null=True)
    admin_name = serializers.CharField(required=False, allow_null=True)
    admin_email = serializers.EmailField(required=False, allow_null=True)
    teachers = serializers.IntegerField()
    students = serializers.IntegerField()
    tokens_used = serializers.IntegerField()
    tokens_unattributed = serializers.IntegerField(
        help_text=(
            "Portion of tokens_used that couldn't be tied to any session "
            "(no course context — e.g. school-admin actions, custom AI "
            "chat, pre-Assignment extraction). "
            "sum(session_breakdown[].tokens) + tokens_unattributed == "
            "tokens_used."
        )
    )
    sessions = serializers.IntegerField()
    courses = serializers.IntegerField(required=False)
    is_active = serializers.BooleanField()
    session_breakdown = SessionBreakdownSerializer(many=True, required=False)


class TeacherSummarySerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    email = serializers.EmailField()
    school = serializers.CharField()
    students = serializers.IntegerField()
    assignments = serializers.IntegerField()
    tokens_used = serializers.IntegerField()


class MonthlyTokenUsageSerializer(serializers.Serializer):
    month = serializers.CharField()
    tokens = serializers.IntegerField()


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


class BulkAddStudentSerializer(serializers.Serializer):
    """Serializer for bulk adding students via CSV or Excel paste."""

    file = serializers.FileField(required=False)
    raw_data = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        if not attrs.get("file") and not attrs.get("raw_data"):
            raise serializers.ValidationError(
                "Either a CSV file or raw text data must be provided."
            )
        return attrs
