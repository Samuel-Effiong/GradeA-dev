from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import UniqueConstraint
from django.utils.translation import gettext_lazy as _

# Create your models here.


class TermTypes(models.TextChoices):
    SEMESTER = "SEMESTER", "Semester"
    YEAR = "YEAR", "Academic Year"
    QUARTER = "QUARTER", "Quarter"


class AcademicTerm(models.Model):
    """Represents an Academic period (semester, year, quarter)."""

    name = models.CharField(max_length=100)
    # type = models.CharField(max_length=20, choices=TermTypes.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    teacher = models.ForeignKey(
        "users.CustomUser",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="academic_terms",
    )

    class Meta:
        ordering = ("-start_date",)

    def clean(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError(_("Start date must be before end date."))


class Classroom(models.Model):
    """Main classroom model that represents a teacher's class"""

    name = models.CharField(max_length=255)
    teacher = models.ForeignKey(
        "users.CustomUser",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="classrooms",
    )

    academic_term = models.ForeignKey(
        AcademicTerm, on_delete=models.CASCADE, related_name="classrooms"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            UniqueConstraint(
                fields=["teacher", "name", "academic_term"],
                name="unique_classroom_name_per_teacher_per_term",
            )
        ]

    def __str__(self):
        return f"{self.name} ({self.academic_term})"


class ClassroomSettings(models.Model):
    """Stores classsroom-specific settings"""

    classroom = models.OneToOneField(
        Classroom, on_delete=models.CASCADE, related_name="settings"
    )
    allow_late_submission = models.BooleanField(default=False)

    def __str__(self):
        return f"Settings for {self.classroom.name}"


class Section(models.Model):
    """Represents different groups/periods within a classroom"""

    name = models.CharField(max_length=100)
    academic_term = models.ForeignKey(
        AcademicTerm,
        on_delete=models.CASCADE,
        related_name="sections",
        blank=True,
        null=True,
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            UniqueConstraint(
                fields=["name", "academic_term"],
                name="unique_section_name_per_classroom",
            )
        ]

    def __str__(self):
        return f"{self.academic_term.name} - {self.name}"


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
        choices=[
            ("ENROLLED", "Enrolled"),
            ("WITHDRAWN", "Withdrawn"),
            ("COMPLETED", "Completed"),
        ],
        default="ENROLLED",
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
