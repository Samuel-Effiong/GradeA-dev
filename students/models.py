import uuid

from django.db import models, transaction
from django.utils.translation import gettext_lazy as _

# from idlelib.pyparse import trans


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

    feedback = models.JSONField(
        null=True, blank=True, help_text=_("Feedback provided for each question")
    )
    graded_at = models.DateTimeField(
        null=True, blank=True, help_text=_("The time student submission was graded")
    )
    grading_confidence = models.IntegerField(null=False, blank=True, default=0)
    extraction_confidence = models.IntegerField(null=False, blank=True, default=0)

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


class BatchUploadSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    teacher = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.CASCADE,
        related_name="batch_upload_sessions",
    )
    assignment = models.ForeignKey(
        "assignments.Assignment",
        on_delete=models.CASCADE,
        related_name="batch_upload_sessions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    total_files = models.IntegerField(default=0)

    results = models.JSONField(default=list)

    def update_result(self, file_name, status, error=None, submission_id=None):
        new_entry = {
            "file_name": file_name,
            "status": status,
            "error": error,
            "submission_id": str(submission_id) if submission_id else None,
        }

        with transaction.atomic():
            session = BatchUploadSession.objects.select_related().get(id=self.id)
            session.results.append(new_entry)
            session.save(update_fields=["results"])
