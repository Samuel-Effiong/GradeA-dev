from django.db import models

# Create your models here.


class ChatSession(models.Model):
    # user = models.ForeignKey("users.CustomUser", on_delete=models.CASCADE, null=True, blank=True)
    section = models.ForeignKey("classrooms.Section", on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class RoleType(models.TextChoices):
    USER = "user", "User"
    ASSISTANT = "assistant", "Assistant"
    SYSTEM = "system", "System"


class ChatMessage(models.Model):
    session = models.ForeignKey(ChatSession, on_delete=models.CASCADE)
    role = models.CharField(
        max_length=20, choices=RoleType.choices, default=RoleType.USER
    )
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("timestamp",)
