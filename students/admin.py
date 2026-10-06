from django.contrib import admin

from .grading_label import LABEL_FIELDS
from .models import StudentSubmission


@admin.register(StudentSubmission)
class StudentSubmissionAdmin(admin.ModelAdmin):
    list_display = ("student", "assignment", "score", "submission_date")
    list_filter = ("submission_date", "assignment")
    search_fields = ("student__email", "assignment__title")
    # The label describes the AI's marking (BE-I-04); only the grading
    # save writes it, so it is shown here and cannot be edited here.
    readonly_fields = ("id", "submission_date", *LABEL_FIELDS)
    raw_id_fields = ("student", "assignment")
    date_hierarchy = "submission_date"
