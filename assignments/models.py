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
    UNPUBLISHED = "UNPUBLISHED", _("UNPUBLISHED")


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
    auto_grade_on_due_date = models.BooleanField(
        default=False,
        help_text="If True, all ungraded submissions will be automatically graded when the due date passes.",
    )
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
    scheduled_grading_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_(
            "The time all submissions for this assignment are scheduled to be graded"
        ),
    )
    grading_task_name = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text=_("The name of the Celery task handling the batch grading"),
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


class AssignmentGenerationHistory(models.Model):
    """
    Stores the history of assignment generation requests.

    This model maintains a record of:
    - User prompts sent to the AI
    - Assignments generated in response to those prompts

    Used for the chat-like history UI where users can browse
    and reuse previously generated assignments without re-running AI.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="assignment_generation_history",
    )

    # The original prompt/input the user provided
    prompt = models.TextField(
        help_text="The original prompt sent to generate or extract the assignment"
    )

    # The resulting assignment that was generated
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.SET_NULL,
        null=True,
        related_name="generation_history",
        help_text="The assignment generated from this prompt",
    )

    assignment_snapshot = models.JSONField(null=True, blank=True)

    # Timestamp for when this generation occurred
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class AssignmentGenerationSession(models.Model):
    """
    Groups a teacher's assignment-generation conversation for a single course.
    The frontend can treat this as a chat thread and fetch its messages in order.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        CustomUser,
        on_delete=models.CASCADE,
        related_name="assignment_generation_sessions",
    )
    course = models.ForeignKey(
        "classrooms.Course",
        on_delete=models.CASCADE,
        related_name="assignment_generation_sessions",
    )
    title = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        ordering = ["-updated_at", "-created_at"]
        indexes = [
            models.Index(fields=["user", "course", "-updated_at"]),
        ]

    def __str__(self):
        return self.title or f"{self.course.name} generation session"


class AssignmentGenerationRole(models.TextChoices):
    USER = "USER", _("User")
    ASSISTANT = "ASSISTANT", _("Assistant")


class AssignmentGenerationMessage(models.Model):
    """
    Stores individual prompt/response items inside an assignment-generation session.

    Assistant messages may optionally link to a saved Assignment when one is created
    from that AI response.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        AssignmentGenerationSession,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(
        max_length=20,
        choices=AssignmentGenerationRole.choices,
        db_index=True,
    )
    content = models.TextField()
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generation_messages",
    )
    assignment_snapshot = models.JSONField(null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["session", "created_at"]),
            models.Index(fields=["session", "role", "created_at"]),
        ]

    def __str__(self):
        return f"{self.role} message in {self.session_id}"
