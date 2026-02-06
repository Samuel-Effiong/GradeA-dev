from django.contrib import admin

from .models import Course, CourseCategory, School, Session, StudentCourse


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "website", "created_at")
    search_fields = ("name", "address")
    readonly_fields = ("id", "created_at")


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("name", "teacher", "created_at")
    list_filter = ("created_at", "teacher")
    search_fields = ("name", "teacher__email")
    readonly_fields = ("id", "created_at")
    raw_id_fields = ("teacher",)


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("name", "teacher", "session", "is_active", "created_at")
    list_filter = ("is_active", "created_at", "session", "teacher")
    search_fields = ("name", "description", "teacher__email")
    readonly_fields = ("id", "created_at")
    raw_id_fields = ("teacher", "session")


@admin.register(CourseCategory)
class CourseCategoryAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)
    readonly_fields = ("id",)


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


class TopicAdmin(admin.ModelAdmin):
    list_display = ("name", "course")
    list_filter = ("course",)
    search_fields = ("name", "course__name")
    readonly_fields = ("id",)
    raw_id_fields = ("course",)
