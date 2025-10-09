# from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import UniqueConstraint
from django.utils.translation import gettext_lazy as _

# Create your models here.


class Session(models.Model):
    """Represents an Academic period (semester, year, quarter)."""

    id = models.UUIDField(primary_key=True, editable=False)
    name = models.CharField(max_length=100, db_index=True)
    created_at = models.DateFieled(auto_now_add=True)

    teacher = models.ForeignKey(
        "users.CustomUser",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="academic_terms",
    )

    class Meta:
        ordering = ("-start_date",)
        constraints = [
            UniqueConstraint(fields=["name", "teacher"], name="unique_academic_term"),
        ]


class Section(models.Model):
    """Represents different groups/periods within a classroom"""

    name = models.CharField(max_length=100, db_index=True)
    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="sections",
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
                fields=["name", "session"],
                name="unique_section_name_per_classroom",
            )
        ]

    def __str__(self):
        return f"{self.academic_term.name} - {self.name}"


class EnrollmentStatusType(models.TextChoices):
    ENROLLED = "ENROLLED", _("Enrolled")
    WITHDRAWN = "WITHDRAWN", _("Withdrawn")
    COMPLETED = "COMPLETED", _("Completed")


class StudentSection(models.Model):
    """Manages student enrollment in sections"""

    student = models.ForeignKey(
        "users.CustomUser", on_delete=models.CASCADE, related_name="enrollments"
    )
    section = models.ForeignKey(
        Section, on_delete=models.CASCADE, related_name="enrollments"
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
                fields=["student", "section"],
                name="unique_student_section_per_classroom",
            )
        ]
