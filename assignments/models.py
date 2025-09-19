from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from users.models import CustomUser

# Create your models here.


class AssignmentTypes(models.TextChoices):
    QUESTIONNAIRES = "QUESTIONNAIRE", _("Questionnaire")
    ESSAY = "ESSAY", _("Essay")
    SHORT_ANSWER = "SHORT-ANSWER", _("Short Answer")
    HYBRID = "HYBRID", _("Hybrid")


class Assignment(models.Model):
    section = models.ForeignKey(
        "classrooms.Section", on_delete=models.CASCADE, related_name="assignments"
    )
    title = models.CharField(max_length=255, unique=True)
    subject_name = models.CharField(max_length=255)
    instructions = models.TextField()
    total_points = models.IntegerField()
    question_count = models.IntegerField()
    assignment_type = models.CharField(
        max_length=20,
        choices=AssignmentTypes.choices,
        default=AssignmentTypes.OBJECTIVE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignments",
    )

    questions = models.JSONField(null=True, blank=True)


class Rubric(models.Model):
    assignment = models.OneToOneField(
        Assignment, on_delete=models.CASCADE, related_name="rubric"
    )
    criteria = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Rubric for {self.assignment.title}"

    def has_criteria(self):
        return True if self.criteria else False

    def get_rubric_criteria_json(self):
        return self.criteria
