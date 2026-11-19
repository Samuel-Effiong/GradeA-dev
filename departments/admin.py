"""Django admin for departments (superusers only; plan section 5.5).

Read-oriented: id-only columns, no email, balance, tag or student data.
"""

from django.contrib import admin

from .models import Department, DepartmentMembership


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "school", "created_at")
    search_fields = ("name",)
    raw_id_fields = ("school",)
    readonly_fields = ("id", "name_key", "created_by_id", "created_at", "updated_at")


@admin.register(DepartmentMembership)
class DepartmentMembershipAdmin(admin.ModelAdmin):
    list_display = ("department", "teacher_id", "can_edit", "created_at")
    raw_id_fields = ("department", "teacher")
    readonly_fields = ("id", "added_by_id", "created_at", "updated_at")
