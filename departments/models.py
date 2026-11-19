"""Epic F1: departments and their members (plan docs/evidence/epic-f1).

A department belongs to one school. A membership puts one licensed teacher
in it, with one per-member edit switch (`can_edit`, owner Q2). Who may see
or change either is decided in one place, `departments.access`; nothing here
grants access. A membership alone never grants access: the liveness rule in
`departments.access` must also hold (plan section 5.2).
"""

import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from users.models import UserTypes

from .normalize import MAX_NAME_LENGTH, clean_department_name


class Department(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    #: Departments live with their school: a school delete removes them
    #: (pinned by a test; the audit rows are values, not foreign keys).
    school = models.ForeignKey(
        "classrooms.School",
        on_delete=models.CASCADE,
        related_name="departments",
    )
    #: As the admin typed it, cleaned. Never a session field: departments
    #: outlive terms (FR-F-01).
    name = models.CharField(max_length=MAX_NAME_LENGTH)
    #: The uniqueness key: the display name in lower case.
    name_key = models.CharField(max_length=MAX_NAME_LENGTH)
    description = models.CharField(max_length=500, blank=True, default="")
    #: Captured, not a foreign key: removing an admin must not null or
    #: cascade it.
    created_by_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name_key", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["school", "name_key"], name="departments_name_unique"
            ),
            models.CheckConstraint(
                condition=~Q(name_key=""), name="departments_name_key_not_empty"
            ),
        ]

    def __str__(self):
        return self.name

    def _normalize_name(self):
        self.name, self.name_key = clean_department_name(self.name)

    def clean(self):
        self._normalize_name()
        if not self.name:
            raise ValidationError({"name": "Name is required."})
        if len(self.name) > MAX_NAME_LENGTH:
            raise ValidationError({"name": "Name is too long."})

    def save(self, *args, **kwargs):
        self._normalize_name()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "name" in update_fields:
            kwargs["update_fields"] = {*update_fields, "name_key"}
        super().save(*args, **kwargs)


class DepartmentMembership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, related_name="memberships"
    )
    #: A deleted teacher takes their rows with them (H-298 stays valid).
    teacher = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.CASCADE,
        related_name="department_memberships",
    )
    #: One switch for department lessons and the shared library (owner Q2).
    can_edit = models.BooleanField(default=True)
    #: Captured, not a foreign key.
    added_by_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["department", "teacher"], name="departments_member_unique"
            ),
        ]

    def __str__(self):
        return f"{self.teacher_id} in {self.department_id}"

    def clean(self):
        teacher = self.teacher
        department = self.department
        if teacher.user_type != UserTypes.TEACHER:
            raise ValidationError("Only teachers can be department members.")
        if teacher.school_id is None or teacher.school_id != department.school_id:
            raise ValidationError("The teacher is not on this department's school.")

    def save(self, *args, **kwargs):
        # The model refuses a cross-school or non-teacher row by itself, so
        # Django admin, scripts and the API all hit the same wall (T12).
        self.clean()
        super().save(*args, **kwargs)
