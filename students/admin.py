from django.contrib import admin

from .models import StudentSubmission


@admin.register(StudentSubmission)
class StudentSubmissionAdmin(admin.ModelAdmin):
    list_display = ("student", "assignment", "score", "submission_date")
    list_filter = ("submission_date", "assignment")
    search_fields = ("student__email", "assignment__title")
    readonly_fields = ("id", "submission_date")
    raw_id_fields = ("student", "assignment")
    date_hierarchy = "submission_date"
