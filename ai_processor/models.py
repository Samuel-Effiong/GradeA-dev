import uuid

from django.db import models
from django.db.models import Q

# Create your models here.


class AssistantType(models.TextChoices):
    SUPER_ADMIN_ANALYTICS = "SUPER_ADMIN_ANALYTICS", "Super Admin Analytics"
    SCHOOL_ADMIN_ANALYTICS = "SCHOOL_ADMIN_ANALYTICS", "School Admin Analytics"
    TEACHER_ADMIN_ANALYTICS = "TEACHER_ADMIN_ANALYTICS", "Teacher Admin Analytics"


class ChatSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        "users.CustomUser", on_delete=models.CASCADE, null=True, blank=True
    )
    course = models.ForeignKey(
        "classrooms.Course", null=True, blank=True, on_delete=models.CASCADE
    )
    assistant_type = models.CharField(
        max_length=50,
        choices=AssistantType.choices,
        null=True,
        blank=True,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "assistant_type"],
                condition=Q(user__isnull=False, assistant_type__isnull=False),
                name="unique_chat_session_per_user_assistant_type",
            )
        ]


class RoleType(models.TextChoices):
    USER = "user", "User"
    ASSISTANT = "assistant", "Assistant"
    SYSTEM = "system", "System"


class ChatMessage(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE)
    role = models.CharField(
        max_length=20, choices=RoleType.choices, default=RoleType.USER
    )
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("timestamp",)
