import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _


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
