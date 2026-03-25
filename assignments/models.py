import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils.translation import gettext_lazy as _

from users.models import CustomUser

# Create your models here.


class AssignmentTypes(models.TextChoices):
    OBJECTIVE = "OBJECTIVE", _("OBJECTIVE")
    ESSAY = "ESSAY", _("ESSAY")
    SHORT_ANSWER = "SHORT-ANSWER", _("SHORT ANSWER")
    HYBRID = "HYBRID", _("HYBRID")


class AssignmentStatus(models.TextChoices):
    DRAFT = "DRAFT", _("DRAFT")
    PUBLISHED = "PUBLISHED", _("PUBLISHED")


class Assignment(models.Model):

    # REQUIRED FIELD NEEDED TO CREATE MODEL
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course = models.ForeignKey(
        "classrooms.Course", on_delete=models.CASCADE, related_name="assignments"
    )

    topic = models.ForeignKey(
        "classrooms.Topic",
        on_delete=models.CASCADE,
        related_name="assignments",
        null=True,
        blank=True,
    )

    title = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    raw_input = models.TextField(null=True, blank=True)
    raw_input_hash = models.CharField(max_length=64, editable=False, null=True)

    created_at = models.DateTimeField(auto_now_add=True)

    # AI GENERATED FIELDS
    instructions = models.TextField(null=True, blank=True, default="")
    total_points = models.IntegerField(null=True, blank=True)
    question_count = models.IntegerField(null=True, blank=True)
    assignment_type = models.CharField(
        max_length=20,
        choices=AssignmentTypes.choices,
        default=AssignmentTypes.OBJECTIVE,
    )
    questions = models.JSONField(null=True, blank=True)
    due_date = models.DateTimeField(null=True, blank=True)
    extraction_confidence = models.IntegerField(null=True, blank=True, default=0)
    potential_issues = ArrayField(
        models.CharField(max_length=1000), null=True, blank=True
    )
    self_assessment = models.TextField(null=True, blank=True)

    custom_ai_prompt = models.TextField(null=True, blank=True)

    # ASSESSMENT FIELDS

    ai_generated = models.BooleanField(default=True)
    ai_raw_payload = models.JSONField(null=True, blank=True)
    ai_generated_at = models.DateTimeField(null=True, blank=True)

    was_overridden = models.BooleanField(default=False)
    overridden_at = models.DateTimeField(null=True, blank=True)

    extraction_started_at = models.DateTimeField(null=True, blank=True)
    extraction_completed_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(
        max_length=20, choices=AssignmentStatus.choices, default=AssignmentStatus.DRAFT
    )

    # grading_status = models.CharField(
    #     max_length=20,
    #     choices=[("NOT_STARTED", "NOT STARTED"), ("COMPLETED", "COMPLETED")],
    #     default="NOT_STARTED",
    # )

    # IN REVIEW FOR REMOVAL
    teacher = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignments",
    )

    # def save(self, *args, **kwargs):
    #     self.raw_input_hash = hashlib.sha256(
    #         self.raw_input.encode("utf-8")
    #     ).hexdigest()
    #
    #     super().save(*args, **kwargs)

    class Meta:
        ordering = ["title"]

        constraints = [
            models.UniqueConstraint(
                fields=["course", "title", "raw_input_hash"],
                name="unique_assignment_per_course",
            )
        ]
