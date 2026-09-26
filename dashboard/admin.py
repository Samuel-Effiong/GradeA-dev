from django.contrib import admin

from .models import StudentRiskAlertState, TeacherInactivityAlertState


@admin.register(StudentRiskAlertState)
class StudentRiskAlertStateAdmin(admin.ModelAdmin):
    list_display = (
        "student",
        "school",
        "is_at_risk",
        "average_score",
        "last_checked_at",
        "last_alerted_at",
    )
    list_filter = ("is_at_risk", "school")
    search_fields = ("student__email", "school__name")
    raw_id_fields = ("student", "school")


@admin.register(TeacherInactivityAlertState)
class TeacherInactivityAlertStateAdmin(admin.ModelAdmin):
    list_display = (
        "teacher",
        "is_flagged_inactive",
        "last_active_at",
        "last_alerted_at",
    )
    list_filter = ("is_flagged_inactive",)
    search_fields = ("teacher__email",)
    raw_id_fields = ("teacher",)
