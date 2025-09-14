from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from users.models import CustomUser

# Create your models here.


class AssignmentTypes(models.TextChoices):
    QUESTIONNAIRES = "QUESTIONNAIRE", _("Questionnaire")
    ESSAY = "ESSAY", _("Essay")
    SHORT_ANSWER = "SHORT_ANSWER", _("Short Answer")
    HYBRID = "HYBRID", _("Hybrid")


class Assignment(models.Model):
    title = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)
    assignment_type = models.CharField(
        max_length=20,
        choices=AssignmentTypes.choices,
        default=AssignmentTypes.QUESTIONNAIRES,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignments",
    )
    file = models.FileField(
        upload_to="assignments/original",
        validators=[
            FileExtensionValidator(allowed_extensions=["pdf", "png", "jpg", "jpeg"])
        ],
    )

    extracted_data = models.JSONField(null=True, blank=True)


class Rubric(models.Model):
    assignment = models.OneToOneField(
        Assignment, on_delete=models.CASCADE, related_name="rubric"
    )
    criteria = models.JSONField()  # Store rubric criteria as JSON
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Rubric for {self.assignment.title}"
