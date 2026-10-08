import uuid

from django.db import models
from django.db.models import Q

# Create your models here.


class AssistantType(models.TextChoices):
    SUPER_ADMIN_ANALYTICS = "SUPER_ADMIN_ANALYTICS", "Super Admin Analytics"
    SCHOOL_ADMIN_ANALYTICS = "SCHOOL_ADMIN_ANALYTICS", "School Admin Analytics"
    TEACHER_ADMIN_ANALYTICS = "TEACHER_ADMIN_ANALYTICS", "Teacher Admin Analytics"


class ChatSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "users.CustomUser", on_delete=models.CASCADE, null=True, blank=True
    )
    course = models.ForeignKey(
        "classrooms.Course", null=True, blank=True, on_delete=models.CASCADE
    )
    assistant_type = models.CharField(
        max_length=50,
        choices=AssistantType.choices,
        null=True,
        blank=True,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "assistant_type"],
                condition=Q(user__isnull=False, assistant_type__isnull=False),
                name="unique_chat_session_per_user_assistant_type",
            )
        ]


class RoleType(models.TextChoices):
    USER = "user", "User"
    ASSISTANT = "assistant", "Assistant"
    SYSTEM = "system", "System"


class ChatMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE)
    role = models.CharField(
        max_length=20, choices=RoleType.choices, default=RoleType.USER
    )
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("timestamp",)


class BenchmarkRun(models.Model):
    """
    One grading-benchmark run — the database mirror of a line in
    ai_processor/benchmark/history/runs.jsonl.

    The JSONL files are the shared, git-tracked source of truth (they travel
    with the code, so every developer sees the same history). This table
    mirrors them so the data is queryable — via the ORM, the admin, or a
    future dashboard chart — and so a run executed on the SERVER by Celery,
    which cannot commit to git, is still recorded somewhere durable.

    Deliberately denormalised and nullable: this is an analysis mirror, not
    the authority. An import must never fail because one metric is missing
    from an older row.
    """

    run_id = models.CharField(max_length=100, unique=True)
    recorded_at = models.DateTimeField()
    mode = models.CharField(max_length=20)
    source = models.CharField(max_length=30, default="benchmark")

    code_sha = models.CharField(max_length=40, null=True, blank=True)
    prompt_fingerprint = models.CharField(max_length=32, null=True, blank=True)
    dataset_fingerprint = models.CharField(max_length=32, null=True, blank=True)

    # Rows failing either check are excluded from variation statistics:
    # replay runs are deterministic (identical every night), and partial runs
    # grade a subset, so neither is comparable with a full paid run.
    is_full_run = models.BooleanField(default=True)

    submissions = models.PositiveIntegerField(default=0)
    submissions_failed = models.PositiveIntegerField(default=0)
    questions_graded = models.PositiveIntegerField(default=0)

    exact_rate = models.FloatField(null=True, blank=True)
    within_one_level_rate = models.FloatField(null=True, blank=True)
    mean_level_error = models.FloatField(null=True, blank=True)
    evidence_verified_rate = models.FloatField(null=True, blank=True)
    deterministic_accuracy = models.FloatField(null=True, blank=True)
    second_opinion_disagreement_rate = models.FloatField(null=True, blank=True)
    total_tokens = models.PositiveIntegerField(null=True, blank=True)

    archive_url = models.URLField(max_length=500, null=True, blank=True)

    # Everything else from the JSONL row, kept whole so a metric added later
    # is still recoverable from rows written before the column existed.
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-recorded_at"]
        indexes = [
            models.Index(fields=["mode", "recorded_at"]),
            models.Index(fields=["prompt_fingerprint"]),
        ]

    def __str__(self):
        return f"{self.run_id} ({self.mode}): exact={self.exact_rate}"


class BenchmarkQuestionOutcome(models.Model):
    """
    One question's result within one run — the mirror of a line in
    ai_processor/benchmark/history/questions.jsonl.

    This is what makes "has this question's grade changed between runs?"
    answerable. No aggregate metric can express that: the existing `twin`
    probe only checks that two identical answers agree WITHIN a single run,
    not that the same answer is graded the same way across runs.
    """

    run = models.ForeignKey(
        BenchmarkRun, on_delete=models.CASCADE, related_name="question_outcomes"
    )
    assignment_key = models.CharField(max_length=50)
    student_key = models.CharField(max_length=50)
    question_number = models.PositiveIntegerField()
    question_type = models.CharField(max_length=30)
    subject = models.CharField(max_length=100, blank=True)

    expected_points = models.FloatField(null=True, blank=True)
    awarded_points = models.FloatField(null=True, blank=True)
    expected_level = models.IntegerField(null=True, blank=True)
    awarded_level = models.IntegerField(null=True, blank=True)
    level_error = models.IntegerField(null=True, blank=True)
    verdict = models.CharField(max_length=20, blank=True)

    level_decision = models.CharField(max_length=20, null=True, blank=True)
    graded_by = models.CharField(max_length=100, null=True, blank=True)
    evidence_verified = models.BooleanField(null=True, blank=True)
    second_opinion_disagreed = models.BooleanField(null=True, blank=True)

    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["assignment_key", "student_key", "question_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "assignment_key", "student_key", "question_number"],
                name="unique_benchmark_question_outcome_per_run",
            )
        ]
        indexes = [
            models.Index(fields=["assignment_key", "student_key", "question_number"]),
        ]

    def __str__(self):
        return (
            f"{self.assignment_key}/{self.student_key}/Q{self.question_number} "
            f"= {self.awarded_points}"
        )


class CallOutcome(models.TextChoices):
    """What became of one AI call and its charge (AICallRecord.outcome)."""

    CHARGED = "charged", "Charged"
    UNMETERED = "unmetered", "Unmetered (super admin)"
    REFUSED_ACCESS = "refused_access", "Refused before the estimate"
    REFUSED_GATE = "refused_gate", "Refused at the balance check"
    PROVIDER_ERROR = "provider_error", "The provider call failed"
    CANCELLED_AFTER_REPLY = "cancelled_after_reply", "Cancelled after the reply"
    BALANCE_CHANGED = "balance_changed", "Balance changed after the reply"
    CHARGE_FAILED = "charge_failed", "The charge failed after the reply"
    OTHER_ERROR = "other_error", "Another error"


class CallKind(models.TextChoices):
    """Set only where the task type proves it (call_record.KIND_BY_TASK_TYPE)."""

    ASSIGNMENT_EXTRACTION = "assignment_extraction", "Assignment extraction"
    ASSIGNMENT_GENERATION = "assignment_generation", "Assignment generation"
    FORMATTING = "formatting", "Feedback wording"
    STUDENT_SUMMARY = "student_summary", "Student summary"
    WEEKLY_COURSE_SUMMARY = "weekly_course_summary", "Weekly course summary"
    WEEKLY_SCHOOL_ADMIN_SUMMARY = (
        "weekly_school_admin_summary",
        "Weekly school admin summary",
    )


class CallTaskType(models.TextChoices):
    EXTRACT_ASSIGNMENT = "extract_assignment", "extract_assignment"
    EXTRACT_ANSWER = "extract_answer", "extract_answer"
    GRADE_ASSIGNMENT = "grade_assignment", "grade_assignment"
    GENERATE_ASSIGNMENT = "generate_assignment", "generate_assignment"
    FORMATTED_GRADE = "formatted_grade", "formatted_grade"
    STUDENT_SUMMARY = "student_summary", "student_summary"
    WEEKLY_COURSE_SUMMARY = "weekly_course_summary", "weekly_course_summary"
    WEEKLY_SCHOOL_ADMIN_SUMMARY = (
        "weekly_school_admin_summary",
        "weekly_school_admin_summary",
    )
    OTHER = "other", "other"


class CallFeature(models.TextChoices):
    ASSIGNMENT_EXTRACTION = "Assignment Extraction", "Assignment Extraction"
    ANSWER_EXTRACTION = "Answer Extraction", "Answer Extraction"
    GRADING_ASSIGNMENT = "Grading Assignment", "Grading Assignment"
    ASSIGNMENT_GENERATION = "Assignment Generation", "Assignment Generation"
    FORMATTED_GRADE = "Formatted Grade", "Formatted Grade"
    STUDENT_SUMMARY = "Student Summary", "Student Summary"
    WEEKLY_COURSE_SUMMARY = "Weekly Course Summary", "Weekly Course Summary"
    WEEKLY_SCHOOL_ADMIN_SUMMARY = (
        "Weekly School Admin Summary",
        "Weekly School Admin Summary",
    )
    OTHER = "other", "other"


class CallErrorClass(models.TextChoices):
    TIMEOUT = "timeout", "timeout"
    RATE_LIMIT = "rate_limit", "rate_limit"
    CONNECTION = "connection", "connection"
    API_STATUS = "api_status", "api_status"
    CANCELLED = "cancelled", "cancelled"
    INSUFFICIENT_CREDITS = "insufficient_credits", "insufficient_credits"
    FEATURE_NOT_AVAILABLE = "feature_not_available", "feature_not_available"
    VALUE = "value", "value"
    OTHER = "other", "other"


class CallFinishReason(models.TextChoices):
    STOP = "stop", "stop"
    LENGTH = "length", "length"
    TOOL_CALLS = "tool_calls", "tool_calls"
    CONTENT_FILTER = "content_filter", "content_filter"
    FUNCTION_CALL = "function_call", "function_call"
    ERROR = "error", "error"
    OTHER = "other", "other"


class AICallRecord(models.Model):
    """One AI call through AIProcessor.execute_graded_task, written AFTER the
    call and its charge are finished (ai_processor/call_record.py).

    A MEASUREMENT table: ids and numbers only, never any prompt, student or
    answer text, never an error message. No foreign keys (the ids go stale
    when a teacher or assignment is deleted, which is correct here), never
    updated, no route, no serializer, not in the admin. Retention: 400 days,
    then a purge command that only the user runs on live (not in this
    slice). It is not the AI-call record of StepRun labels on the saved
    result (the Phase 2 follow-on): no table or column is shared.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    task_id = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        db_index=True,
        help_text="The id CreditUsageLog.task_id carries; NULL when never charged.",
    )
    task_type = models.CharField(max_length=40, choices=CallTaskType.choices)
    feature = models.CharField(max_length=40, choices=CallFeature.choices)
    kind = models.CharField(
        max_length=40,
        choices=CallKind.choices,
        null=True,
        blank=True,
        help_text="Set only where the task type proves it; otherwise NULL.",
    )
    outcome = models.CharField(max_length=24, choices=CallOutcome.choices)
    error_class = models.CharField(
        max_length=24, choices=CallErrorClass.choices, null=True, blank=True
    )
    model_asked = models.CharField(max_length=255, blank=True, default="")
    model_answered = models.CharField(max_length=255, null=True, blank=True)
    finish_reason = models.CharField(
        max_length=16, choices=CallFinishReason.choices, null=True, blank=True
    )
    prompt_version = models.CharField(
        max_length=128,
        default="unknown",
        db_default="unknown",
        help_text="Not filled on the beta line (no prompt version reaches the call).",
    )
    estimate = models.PositiveBigIntegerField(null=True, blank=True)
    prompt_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    completion_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    reasoning_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    cached_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    total_tokens = models.PositiveBigIntegerField(null=True, blank=True)
    charged_credits = models.PositiveBigIntegerField(default=0)
    cost_usd = models.DecimalField(
        max_digits=14,
        decimal_places=8,
        null=True,
        blank=True,
        help_text="The provider's reported cost; NULL when the reply carries none.",
    )
    images = models.PositiveIntegerField(null=True, blank=True)
    pdf_items = models.PositiveIntegerField(null=True, blank=True)
    questions = models.PositiveIntegerField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    billed_user_id = models.UUIDField(null=True, blank=True)
    school_id = models.UUIDField(null=True, blank=True)
    assignment_id = models.UUIDField(null=True, blank=True)
    processing_task_id = models.UUIDField(null=True, blank=True)
