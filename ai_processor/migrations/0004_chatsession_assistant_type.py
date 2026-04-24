from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("ai_processor", "0003_chatsession_user"),
    ]

    operations = [
        migrations.AddField(
            model_name="chatsession",
            name="assistant_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("SUPER_ADMIN_ANALYTICS", "Super Admin Analytics"),
                    ("SCHOOL_ADMIN_ANALYTICS", "School Admin Analytics"),
                    ("TEACHER_ADMIN_ANALYTICS", "Teacher Admin Analytics"),
                ],
                db_index=True,
                max_length=50,
                null=True,
            ),
        ),
        migrations.AddConstraint(
            model_name="chatsession",
            constraint=models.UniqueConstraint(
                condition=Q(assistant_type__isnull=False, user__isnull=False),
                fields=("user", "assistant_type"),
                name="unique_chat_session_per_user_assistant_type",
            ),
        ),
    ]
