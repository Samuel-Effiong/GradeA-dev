import uuid

from django.db import models
from django.db.models import UniqueConstraint, UUIDField
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


# Create your models here.
class School(models.Model):
    id = UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True, db_index=True)
    address = models.TextField(blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    website = models.URLField(max_length=500, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Session(models.Model):
    """Represents an Academic period (semester, year, quarter)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, db_index=True)
    created_at = models.DateField(auto_now_add=True)

    teacher = models.ForeignKey(
        "users.CustomUser",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="sessions",
    )

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            UniqueConstraint(
                fields=["name", "teacher"], name="unique_session_name_per_teacher"
            ),
        ]


class Course(models.Model):
    """Represents different groups/periods within a classroom"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, db_index=True)
    teacher = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.CASCADE,
        related_name="courses",
        blank=True,
        null=True,
    )
    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="courses",
        blank=True,
        null=True,
    )
    description = models.TextField(blank=True, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            UniqueConstraint(
                fields=["name", "teacher", "session"],
                name="unique_section_name_per_session",
            ),
        ]

    def __str__(self):
        return f"{self.session.name} - {self.name}"


class Topic(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, db_index=True)
    course = models.ForeignKey(
        Course,
        on_delete=models.CASCADE,
        related_name="topics",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            UniqueConstraint(
                fields=["name", "course"],
                name="unique_topic_name_per_course",
            ),
        ]

    def __str__(self):
        return f"{self.course.name} - {self.name}"


class CourseCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, db_index=True)

    def __str__(self):
        return self.name


class EnrollmentStatusType(models.TextChoices):
    ENROLLED = "ENROLLED", _("Enrolled")
    WITHDRAWN = "WITHDRAWN", _("Withdrawn")
    COMPLETED = "COMPLETED", _("Completed")
    PENDING = "PENDING", _("Pending")


class StudentCourseQuerySet(models.QuerySet):
    def active(self):
        return self.exclude(enrollment_status=EnrollmentStatusType.WITHDRAWN)


class StudentCourse(models.Model):
    """Manages student enrollment in sections"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey(
        "users.CustomUser", on_delete=models.CASCADE, related_name="enrollments"
    )
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="enrollments"
    )
    auto_added = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    enrollment_status = models.CharField(
        max_length=20,
        choices=EnrollmentStatusType.choices,
        default=EnrollmentStatusType.PENDING,
        db_index=True,
    )
    withdrawal_date = models.DateTimeField(null=True, blank=True)

    final_grade = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    # attendance_record = models.JSONField(default=dict)
    # participation_score = models.DecimalField(
    #     max_digits=5,
    #     decimal_places=2,
    #     default=0.00
    # )

    objects = StudentCourseQuerySet.as_manager()
    all_objects = models.Manager()

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["student", "course"],
                name="unique_student_section_per_classroom",
            )
        ]

    def withdrawn(self, when=None):
        if self.enrollment_status == EnrollmentStatusType.WITHDRAWN:
            return
        when = when or timezone.now()
        self.enrollment_status = EnrollmentStatusType.WITHDRAWN
        self.withdrawal_date = when
        self.save(update_fields=["enrollment_status", "withdrawal_date"])

    def reactivate(self):
        self.enrollment_status = EnrollmentStatusType.ENROLLED
        self.withdrawal_date = None
        self.save(update_fields=["enrollment_status", "withdrawal_date"])

    def clean(self):
        if self.student_id or not self.course_id:
            return

        student = self.student
        first_name = student.first_name
        last_name = student.last_name
        middle_name = student.middle_name

        existing_enrollments = StudentCourse.objects.filter(
            course=self.course,
            student__firstname=first_name,
            student__lastname=last_name,
            student__middlename=middle_name,
        ).exclude(id=self.id)

        if existing_enrollments.exists():
            raise ValueError(
                f"Student with the name '{first_name} {middle_name} {last_name} "
                f"is already enrolled in this course."
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
