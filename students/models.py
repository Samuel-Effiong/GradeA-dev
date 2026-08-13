import uuid

from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

# from idlelib.pyparse import trans


class GradingState(models.TextChoices):
    """
    Idempotency guard for the grading pipeline (students.services.grade_engine).

    Celery is configured with acks_late=True and a Redis broker visibility
    timeout — if a grading run (several sequential AI calls, each with its
    own retries) takes longer than that timeout, Redis will redeliver the
    same task message to a second worker while the first is still running.
    Without a claim, both workers run the full (billed) pipeline
    concurrently on the same submission. RUNNING with a fresh
    grading_started_at is the claim; a second worker/request sees RUNNING
    and backs off instead of re-running the pipeline. See
    students.services._claim_submission_for_grading.
    """

    IDLE = "IDLE", _("Idle")
    RUNNING = "RUNNING", _("Running")
    DONE = "DONE", _("Done")
    FAILED = "FAILED", _("Failed")


# Create your models here.
class StudentSubmission(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    assignment = models.ForeignKey(
        "assignments.Assignment", on_delete=models.CASCADE, related_name="submissions"
    )
    student = models.ForeignKey(
        "users.CustomUser", on_delete=models.CASCADE, related_name="submissions"
    )
    submission_date = models.DateTimeField(auto_now_add=True)

    answers = models.JSONField(
        help_text=_(
            "Student answers in JSON format, structured to match assignment questions"
        )
    )

    raw_input = models.TextField(
        null=True,
        blank=True,
        help_text=_("Raw student input, as submitted in the form"),
    )

    score = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0.00,
        null=True,
        blank=True,
        help_text=_("Final score awarded to the submission"),
    )
    score_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        db_index=True,
        help_text=_(
            "Score as a percentage of total possible points (calculated from score and assignment max_points)"
        ),
    )

    max_points = models.IntegerField(
        null=True,
        blank=True,
        help_text=_("Maximum points available for this assignment"),
    )

    feedback = models.JSONField(
        null=True, blank=True, help_text=_("Feedback provided for each question")
    )
    graded_at = models.DateTimeField(
        null=True, blank=True, help_text=_("The time student submission was graded")
    )
    grading_state = models.CharField(
        max_length=20,
        choices=GradingState.choices,
        default=GradingState.IDLE,
        db_index=True,
        help_text=_(
            "Idempotency claim for the grading pipeline. RUNNING means a "
            "worker currently holds the claim; see GradingState's docstring."
        ),
    )
    grading_started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_(
            "When the current (or most recent) grading claim was acquired. "
            "Used to detect and reclaim a stale RUNNING claim left behind "
            "by a crashed worker."
        ),
    )
    grading_confidence = models.IntegerField(null=False, blank=True, default=0)
    extraction_confidence = models.IntegerField(null=False, blank=True, default=0)

    needs_review = models.BooleanField(
        default=False,
        db_index=True,
        help_text=_(
            "The two independent AI graders disagreed on at least one "
            "question (see feedback['second_opinion']) — the teacher's "
            "review queue filters on this. Cleared by mark-reviewed or a "
            "manual grade override."
        ),
    )
    review_reasons = models.JSONField(
        null=True,
        blank=True,
        help_text=_(
            "Why this submission needs (or needed) review — per-question "
            "grader-disagreement entries, and the teacher's resolution "
            "once reviewed."
        ),
    )
    review_severity = models.FloatField(
        null=True,
        blank=True,
        db_index=True,
        help_text=_(
            "0-1 tier-weighted sort key for the review queue: the tier "
            "picks the band (critical 0.67-1.0, moderate 0.33-0.67, "
            "borderline 0-0.33) and the disagreement's point gap orders "
            "within it. Weighted rather than the raw gap fraction because "
            "a disagreement is also 'critical' when the graders are 2+ "
            "rubric levels apart, which can happen at a small gap - "
            "ordering on the raw fraction buried those below milder ones. "
            "?needs_review=true&ordering=-review_severity."
        ),
    )
    review_tier = models.CharField(
        max_length=16,
        null=True,
        blank=True,
        db_index=True,
        help_text=_(
            "Worst disagreement severity on this submission (critical / "
            "moderate / borderline), denormalised from review_reasons so "
            "the queue can filter on it - review_reasons is a JSONField "
            "and cannot be filtered. ?needs_review=true&review_tier=critical."
        ),
    )

    ai_score = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0.00,
        null=True,
        blank=True,
        help_text=_("AI score awarded to the submission"),
    )

    ai_feedback = models.JSONField(
        null=True, blank=True, help_text=_("AI feedback provided for each question")
    )

    ai_graded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("The time student submission was graded by AI"),
    )
    ai_grading_completed_at = models.DateField(
        null=True,
        blank=True,
        help_text=_("The time the ai finished grading the student submission"),
    )

    was_regraded = models.BooleanField(
        default=False,
        db_index=True,
        help_text=_("Whether the AI grade was modified by a human"),
    )
    regraded_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("The time the AI grade was modified by a human"),
    )

    formatted_grade = models.TextField(null=True, blank=True)
    is_published = models.BooleanField(
        default=False, help_text=_("Whether the grade has been released to the student")
    )
    scheduled_grading_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("The time this submission is scheduled to be graded"),
    )
    grading_task_name = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text=_("The name of the Celery task handling the scheduled grading"),
    )

    attempt_count = models.PositiveSmallIntegerField(
        default=0,
        null=True,
        blank=True,
        help_text="Number of times the student has submitted this assignment.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "assignment"],
                name="unique_student_submission_per_assignment",
            )
        ]

        ordering = ["-submission_date"]

    def get_answer(self):
        return self.answers


class BatchUploadType(models.TextChoices):
    SUBMISSION = "submission"
    ASSIGNMENT = "assignment"
    GRADE = "grade"


class BatchUploadSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    teacher = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.CASCADE,
        related_name="batch_upload_sessions",
    )
    task_type = models.CharField(
        max_length=255,
        choices=BatchUploadType.choices,
        default=BatchUploadType.SUBMISSION,
    )

    assignment = models.ForeignKey(
        "assignments.Assignment",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="batch_upload_sessions",
    )

    course = models.ForeignKey(
        "classrooms.Course",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="batch_upload_sessions",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    total_files = models.IntegerField(default=0)

    results = models.JSONField(default=list)

    def update_result(
        self,
        file_name,
        status,
        error=None,
        batch_type=None,
        submission_id=None,
        assignment_id=None,
    ):
        new_entry = {
            "file_name": file_name,
            "status": status,
            "error": error,
        }
        if batch_type == BatchUploadType.SUBMISSION:
            new_entry.update(
                {
                    "submission_id": str(submission_id) if submission_id else None,
                }
            )

        elif batch_type == BatchUploadType.ASSIGNMENT:
            new_entry.update(
                {
                    "assignment_id": str(assignment_id) if assignment_id else None,
                }
            )

        with transaction.atomic():
            session = BatchUploadSession.objects.select_related().get(id=self.id)
            session.results.append(new_entry)
            session.save(update_fields=["results"])


class BackgroundTaskType(models.TextChoices):
    ASSIGNMENT_EXTRACTION = "assignment_extraction", _("Assignment Extraction")
    ASSIGNMENT_REEXTRACTION = "assignment_reextraction", _("Assignment Re-extraction")
    BATCH_ASSIGNMENT_UPLOAD = "batch_assignment_upload", _("Batch Assignment Upload")
    ANSWER_EXTRACTION = "answer_extraction", _("Answer Extraction")
    BATCH_ANSWER_UPLOAD = "batch_answer_upload", _("Batch Answer Upload")
    SUBMISSION_GRADING = "submission_grading", _("Submission Grading")
    BATCH_SUBMISSION_GRADING = "batch_submission_grading", _("Batch Submission Grading")
    FORMATTED_GRADE = "formatted_grade", _("Formatted Grade")


class BackgroundTaskStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    STARTED = "STARTED", _("Started")
    CANCELLED = "CANCELLED", _("Cancelled")
    SUCCESS = "SUCCESS", _("Success")
    FAILURE = "FAILURE", _("Failure")


class BackgroundProcessingTask(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    celery_task_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text=_("The Celery task id for the running background process."),
    )
    requested_by = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.CASCADE,
        related_name="background_processing_tasks",
    )
    batch_session = models.ForeignKey(
        "students.BatchUploadSession",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="processing_tasks",
    )
    assignment = models.ForeignKey(
        "assignments.Assignment",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="processing_tasks",
    )
    submission = models.ForeignKey(
        "students.StudentSubmission",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="processing_tasks",
    )
    task_type = models.CharField(max_length=64, choices=BackgroundTaskType.choices)
    status = models.CharField(
        max_length=20,
        choices=BackgroundTaskStatus.choices,
        default=BackgroundTaskStatus.PENDING,
        db_index=True,
    )
    file_name = models.CharField(max_length=255, null=True, blank=True)
    meta = models.JSONField(default=dict, blank=True)
    error = models.TextField(null=True, blank=True)
    cancel_requested_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        label = self.file_name or self.celery_task_id or str(self.id)
        return f"{self.task_type}::{label}"
