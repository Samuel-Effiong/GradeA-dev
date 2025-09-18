from django.db import models
from django.utils.translation import gettext_lazy as _


# Create your models here.
class StudentSubmission(models.Model):
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
