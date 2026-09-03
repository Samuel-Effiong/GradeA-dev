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
    # Bumped on every save. Read by assignments/pdf_cache.py, which builds
    # its cache key from this timestamp: an edit changes the key, so the
    # next download is a natural miss and the stale entry simply ages out
    # under its TTL - there is no invalidation hook to keep in sync with
    # future write paths.
    updated_at = models.DateTimeField(auto_now=True)

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

    # --- Denormalized rigor components (see assignments/rigor.py) ---
    # Derived from `questions` and kept in sync by the pre_save hook in
    # assignments/signals.py. Stored rather than computed on read because the
    # school-admin dashboard and weekly digest aggregate these across every
    # assignment of every teacher in a school, and re-parsing the questions
    # JSON per request made that an N+1 over JSON blobs. Both are null when
    # the source data cannot support a score.
    rigor_demand = models.FloatField(
        null=True,
        blank=True,
        # Deliberately unindexed: the dashboard roll-up filters by
        # course__teacher_id and status (both already indexed) and only
        # aggregates this column, so a standalone index on it would never be
        # chosen -- while its non-concurrent CREATE INDEX would hold an
        # exclusive write lock over the whole table at deploy time.
        help_text=(
            "Points-weighted mean Bloom's taxonomy level across this "
            "assignment's questions, 0-5. Null when Bloom's coverage is below "
            "assignments.rigor.MIN_BLOOMS_COVERAGE."
        ),
    )
    rigor_standards = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "Share of open-ended questions carrying a usable rubric, scaled "
            "0-5. Null when the assignment has no open-ended questions."
        ),
    )
    rigor_blooms_coverage = models.FloatField(
        null=True,
        blank=True,
        help_text=(
            "Fraction of question points that carried a recognised "
            "blooms_level, 0.0-1.0. Reported alongside the score so a "
            "confident-looking number drawn from sparse data is visible."
        ),
    )

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
    admin_grading_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_(
            "When school admins were notified that grading finished for this "
            "assignment. Also acts as an idempotency guard so the notification "
            "is only ever sent once per assignment."
        ),
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

        indexes = [
            # Backs course__teacher=user (assignments/views.py) sorted by
            # the default ordering above - Meta.ordering alone doesn't
            # create a DB index.
            models.Index(
                fields=["course", "title"], name="assignment_course_title_idx"
            ),
        ]


class AssignmentGenerationHistory(models.Model):
    """
        Stores the history of assignment generation requests.

        This model maintains a record of:
        - User prompts sent to the AI
        - Assignments generated in response to those prompts
    f
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
