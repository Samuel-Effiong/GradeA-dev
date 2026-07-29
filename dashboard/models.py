from django.db import models


class StudentRiskAlertState(models.Model):
    """
    Persisted cache of each student's school-wide at-risk status, used to
    detect a false->true transition ("newly at-risk") for the daily
    at-risk-student alert email sent to opted-in school admins.
    """

    student = models.ForeignKey(
        "users.CustomUser", on_delete=models.CASCADE, related_name="risk_alert_states"
    )
    school = models.ForeignKey(
        "classrooms.School", on_delete=models.CASCADE, related_name="risk_alert_states"
    )
    is_at_risk = models.BooleanField(default=False)
    average_score = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    last_checked_at = models.DateTimeField(auto_now=True)
    last_alerted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["student", "school"], name="unique_student_school_risk_state"
            )
        ]

    def __str__(self):
        return f"{self.student_id} @ {self.school_id}: at_risk={self.is_at_risk}"


class TeacherInactivityAlertState(models.Model):
    """
    Tracks whether a teacher is currently flagged as inactive (no login
    activity within the configured threshold), so the daily teacher-activity
    alert task sends exactly one email per inactivity episode instead of
    re-alerting every day the teacher remains inactive.
    """

    teacher = models.OneToOneField(
        "users.CustomUser",
        on_delete=models.CASCADE,
        related_name="inactivity_alert_state",
    )
    is_flagged_inactive = models.BooleanField(default=False)
    last_active_at = models.DateTimeField(null=True, blank=True)
    last_alerted_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.teacher_id}: inactive={self.is_flagged_inactive}"
