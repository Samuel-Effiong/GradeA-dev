import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from users.models import CustomUser

# Create your models here.


class AssignmentTypes(models.TextChoices):
    OBJECTIVE = "OBJECTIVE", _("OBJECTIVE")
    ESSAY = "ESSAY", _("ESSAY")
    SHORT_ANSWER = "SHORT-ANSWER", _("SHORT ANSWER")
    HYBRID = "HYBRID", _("HYBRID")


class Assignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course = models.ForeignKey(
        "classrooms.Course", on_delete=models.CASCADE, related_name="assignments"
    )
    title = models.CharField(max_length=255, unique=True)
    instructions = models.TextField()
    total_points = models.IntegerField()
    question_count = models.IntegerField()
    assignment_type = models.CharField(
        max_length=20,
        choices=AssignmentTypes.choices,
        default=AssignmentTypes.OBJECTIVE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    due_date = models.DateTimeField(null=True, blank=True)
    teacher = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignments",
    )

    questions = models.JSONField(null=True, blank=True)
    extraction_confidence = models.IntegerField(null=True, blank=True, default=0)
    potential_issues = models.TextField(null=True, blank=True)
    # raw_content = models.TextField()

    class Meta:
        ordering = ["title"]


class Rubric(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
