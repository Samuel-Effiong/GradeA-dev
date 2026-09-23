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

        validators = [
            UniqueTogetherValidator(
                queryset=Topic.objects.all(),
                fields=["name", "course"],
                message="This Course already has this topic",
            )
        ]

    def validate_name(self, value):
        """Validate that name is not empty."""
        if not value.strip():
            raise serializers.ValidationError("Name cannot be empty.")
        return value

    def validate_course(self, value):
        """Reject a course the requesting teacher doesn't own.

        `course` is a plain writable PK field, so without this a caller
        could attach a topic to any course in the system just by knowing
        (or enumerating) its UUID - the viewset's get_queryset() only
        scopes reads and edits of existing rows, never the course a NEW
        topic points at.
        """
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        if user is None or not user.is_authenticated:
            # Fail closed (H-18 hardening): a caller that builds this
            # serializer without the request must not skip the check.
            raise serializers.ValidationError("You do not have access to this course.")

        if user.is_superuser and user.user_type == UserTypes.SUPER_ADMIN:
            return value

        if value.teacher_id != user.id:
            raise serializers.ValidationError("You do not have access to this course.")
        return value


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
    # A method field rather than a nested serializer so a student's payload
    # can be filtered to published work - see get_assignments. The schema
    # and every teacher's output are unchanged.
    assignments = serializers.SerializerMethodField()

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
            # Fail closed (H-18 hardening): a caller that builds this
            # serializer without the request must not skip the check.
            raise serializers.ValidationError("You do not have access to this session.")

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
        if hasattr(obj, "student_count"):
            return obj.student_count

        return (
            obj.enrollments.exclude(enrollment_status=EnrollmentStatusType.WITHDRAWN)
            .distinct()
            .count()
        )

    def _requesting_student(self):
        """The requester when they are a student, otherwise None.

        Only a student's course payload is filtered. Teachers, school
        admins and superadmins keep exactly what they have always received.
        """
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if getattr(user, "user_type", None) == UserTypes.STUDENT:
            return user
        return None

    def _visible_assignments(self, obj):
        """Every assignment for staff; only PUBLISHED ones for a student.

        Drafts and unpublished work are the teacher's, and the assignments
        endpoints already hide them from students by the same rule. Filtered
        in Python: calling .filter() would discard the view's prefetch and
        issue a query per course.
        """
        assignments = obj.assignments.all()
        if self._requesting_student() is None:
            return assignments
        return [a for a in assignments if a.status == AssignmentStatus.PUBLISHED]

    @extend_schema_field(AssignmentListSerializer(many=True))
    def get_assignments(self, obj):
        return AssignmentListSerializer(
            many=True, context=self.context
        ).to_representation(self._visible_assignments(obj))

    def get_assignment_count(self, obj):
        if self._requesting_student() is not None:
            # Must agree with the filtered `assignments` list. An annotated
            # count, if one is ever added, would include drafts.
            return len(self._visible_assignments(obj))

        if hasattr(obj, "assignment_count"):
            return obj.assignment_count

        # len() of the prefetch cache. `.distinct().count()` discarded it and
        # issued a fresh COUNT for every course on the page; distinct() was
        # pointless anyway, since assignments is a plain reverse FK and the
        # query has no join that could duplicate a row.
        return len(obj.assignments.all())

    @extend_schema_field(StudentSerializer(many=True))
    def get_students(self, obj):
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

        # The enrollment status each student holds in THIS course, passed
        # down so StudentSerializer.get_enrollment_status can read it
        # instead of issuing its own query per student.
        if hasattr(obj, "active_enrollments"):
            status_by_student = {
                enrollment.student_id: enrollment.enrollment_status
                for enrollment in obj.active_enrollments
            }
        else:
            status_by_student = None

        serializer = StudentSerializer(
            enrolled_students,
            many=True,
            context={"course": obj, "enrollment_status_by_student": status_by_student},
        )

        data = serializer.data
        viewer = self._requesting_student()
        if viewer is not None:
            # A student may see who their classmates are, never how to
            # email them. Their own address stays: it is their own data.
            for student, entry in zip(enrolled_students, data, strict=True):
                if student.pk != viewer.pk:
                    entry["email"] = None

        return data


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
        # `student` and `course` are read-only. The viewset exposes no POST
        # (see StudentCourseViewSet.http_method_names), so the only thing
        # their writability ever achieved was letting a PATCH re-point an
        # enrollment the teacher legitimately owns at ANOTHER teacher's
        # course, or at a different student - get_queryset() scopes which
        # row you may edit, not what you may write into it.
        read_only_fields = ["id", "created_at", "auto_added", "student", "course"]

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

    # The three counts below all read from the caches the viewset already
    # populates (prefetch_related("course__assignments") and the scoped
    # "student__submissions" Prefetch), rather than issuing their own
    # queries. `.count()` and `.filter()` both bypass a prefetch cache, so
    # the previous versions cost four extra round trips PER ROW - a 100-row
    # page of /student-course was ~400 avoidable queries.

    def _course_assignments(self, obj):
        # This viewset serves both the owning teacher and the enrolled
        # student (see StudentCourseViewSet.get_queryset). A teacher sees
        # every assignment they authored, drafts included - see
        # test_the_counts_are_still_correct. A student must not learn a
        # draft exists at all, so their counts have to agree with the
        # PUBLISHED-only filter get_assignments applies below, or the
        # stats and the assignment table disagree (the bug this fixes).
        assignments = obj.course.assignments.all()
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        if getattr(user, "user_type", None) != UserTypes.STUDENT:
            return list(assignments)
        return [a for a in assignments if a.status == AssignmentStatus.PUBLISHED]

    def _submitted_assignment_ids(self, obj):
        return {
            submission.assignment_id
            for submission in obj.student.submissions.all()
            if submission.assignment.course_id == obj.course_id
        }

    def get_total_no_of_assignment(self, obj):
        return len(self._course_assignments(obj))

    def get_total_assignment_submitted(self, obj):
        submitted = self._submitted_assignment_ids(obj)
        return sum(1 for a in self._course_assignments(obj) if a.id in submitted)

    def get_submitted_assignment_percentage(self, obj):
        total = self.get_total_no_of_assignment(obj)
        if not total:
            return 0
        return (self.get_total_assignment_submitted(obj) / total) * 100

    def get_grade_letter(self, obj):
        # `is not None`, not truthiness: a genuine 0.00 is a grade (an F),
        # not the absence of one.
        if obj.final_grade is None:
            return None
        return get_grade_details(obj.final_grade)


class StudentCourseDetailSerializer(StudentCourseSerializer):
    assignments = serializers.SerializerMethodField()

    class Meta(StudentCourseSerializer.Meta):
        fields = StudentCourseSerializer.Meta.fields + ["assignments"]

    def get_assignments(self, obj):
        # Filter the prefetched assignments in Python: calling .filter() on
        # the prefetched .all() would discard the prefetch cache and issue
        # a fresh query per enrollment row — the exact N+1 the view's
        # prefetch_related("course__assignments") exists to prevent.
        # Shares _course_assignments with the stats fields above so the
        # table and the header counts can never disagree again.
        assignments = self._course_assignments(obj)

        # Filter pre-fetched submissions for this specific student

        submissions = {
            s.assignment_id: s
            for s in obj.student.submissions.all()
            if s.assignment.course_id == obj.course_id
        }

        result = []

        for assignment in assignments:
            submission = submissions.get(assignment.id)

            # Status and score for this assignment
            if not submission:
                now = timezone.now()

                if assignment.due_date and assignment.due_date < now:
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
        from .services import find_account_by_email, normalize_email

        value = normalize_email(value)
        existing_user = find_account_by_email(value)

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

        from .services import normalize_email

        value = normalize_email(value)

        if CustomUser.objects.filter(
            email__iexact=value,
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
            from .services import find_account_by_email

            student = find_account_by_email(email)

            if student:
                # Check if already enrolled
                if StudentCourse.objects.filter(
                    student=student, course=course
                ).exists():
                    raise serializers.ValidationError(
                        "Student is already enrolled in this course."
                    )

                # This path attaches an EXISTING account, so it needs the
                # same gate as single-add and bulk import. It is reachable
                # with a caller-supplied email, so without this a teacher
                # could pull another school's student in through the
                # "direct add" form even after the other two routes were
                # closed.
                # Imported here, not at module scope: services.roster_import
                # imports this module for DirectAddStudentSerializer, so a
                # top-level import the other way is a genuine cycle.
                from .services import EnrollmentError, check_existing_account_may_join

                try:
                    check_existing_account_may_join(student, course)
                except EnrollmentError as exc:
                    raise serializers.ValidationError(str(exc)) from exc

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
                # No password, rather than a shared literal. Every student
                # created this way used to get the SAME known password, on
                # an active account whose address follows a guessable
                # pattern (first.last<0-9999>@student.local) - so anyone
                # who learned the literal could sign in as any of them.
                # These are teacher-managed roster entries that are never
                # meant to be signed into directly; a student who later
                # needs real access goes through the invitation flow, which
                # sets a password of their own.
                student.set_unusable_password()
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
    frontend_domain = settings.SCHOOL_ADMIN_FRONTEND_DOMAIN
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


def resend_school_admin_invitation(user):
    """Issue a fresh 7-day invitation token and re-send the school-admin
    invite email for a still-pending admin.

    Exists so a pending school admin (`is_active=False`, no usable
    password - see `SchoolWithAdminSerializer.create()`) can always be
    reached with a working invite, rather than being routed through the
    generic self-registration activation flow, which has no password step
    and would silently overwrite this same `activation_token` field with
    one that leads nowhere useful (H-42).
    """
    if not user.school:
        logger.error(
            "Cannot resend school admin invitation for %s: no school attached.",
            user.email,
        )
        return

    user.activation_token = secrets.token_urlsafe(32)
    user.activation_expires = timezone.now() + timezone.timedelta(days=7)
    user.save(update_fields=["activation_token", "activation_expires"])
    _send_school_admin_invitation_email(user, user.school)


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
    tokens_used = serializers.IntegerField(
        help_text=(
            "Without session_id: the teacher's full personal token total "
            "(every AI feature they've used). With session_id: scoped to "
            "that session's courses, same as assignments/students."
        )
    )
    tokens_used_outside_session = serializers.IntegerField(
        help_text=(
            "The rest of the teacher's token total not counted in "
            "tokens_used — other sessions, or usage with no course context "
            "at all (custom AI chat, pre-Assignment extraction). Always 0 "
            "when no session_id filter is applied. tokens_used + "
            "tokens_used_outside_session == the teacher's full personal "
            "total."
        )
    )


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
