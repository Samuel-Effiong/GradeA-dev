from django.contrib import admin

from .models import StudentCourse


# Register your models here.
@admin.register(StudentCourse)
class StudentCourseAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "student",
        "course",
        "enrollment_status",
        "created_at",
        "final_grade",
    ]
    list_filter = ["enrollment_status", "created_at", "course"]
    search_fields = [
        "student__email",
        "student__first_name",
        "student__last_name",
        "course__name",
    ]
    readonly_fields = ["id", "created_at"]
    raw_id_fields = ["student", "course"]
    date_hierarchy = "created_at"
