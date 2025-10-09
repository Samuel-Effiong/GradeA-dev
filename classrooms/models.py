# from django.core.exceptions import ValidationError
import uuid

from django.db import models
from django.db.models import UniqueConstraint
from django.utils.translation import gettext_lazy as _

# Create your models here.


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
    categories = models.ManyToManyField(
        "CourseCategory",
        related_name="courses",
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            UniqueConstraint(
                fields=["name", "session"],
                name="unique_section_name_per_session",
            )
        ]

    def __str__(self):
        return f"{self.session.name} - {self.name}"


class CourseCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, db_index=True)

    def __str__(self):
        return self.name


class EnrollmentStatusType(models.TextChoices):
    ENROLLED = "ENROLLED", _("Enrolled")
    WITHDRAWN = "WITHDRAWN", _("Withdrawn")
    COMPLETED = "COMPLETED", _("Completed")


class StudentCourse(models.Model):
    """Manages student enrollment in sections"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    student = models.ForeignKey(
        "users.CustomUser", on_delete=models.CASCADE, related_name="enrollments"
    )
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, related_name="enrollments"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    enrollment_status = models.CharField(
        max_length=20,
        choices=EnrollmentStatusType.choices,
        default=EnrollmentStatusType.ENROLLED,
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

    class Meta:
        constraints = [
            UniqueConstraint(
                fields=["student", "course"],
                name="unique_student_section_per_classroom",
            )
        ]
