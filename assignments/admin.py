from django.contrib import admin

from .models import Assignment, Rubric


@admin.register(Assignment)
class AssignmentAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "course",
        "teacher",
        "assignment_type",
        "total_points",
        "due_date",
        "created_at",
    )
    list_filter = ("assignment_type", "created_at", "due_date", "course")
    search_fields = ("title", "instructions", "teacher__email", "course__name")
    readonly_fields = ("id", "created_at")
    raw_id_fields = ("course", "teacher")
    date_hierarchy = "created_at"


@admin.register(Rubric)
class RubricAdmin(admin.ModelAdmin):
    list_display = ("assignment", "created_at", "updated_at")
    search_fields = ("assignment__title",)
    readonly_fields = ("id", "created_at", "updated_at")
    raw_id_fields = ("assignment",)
