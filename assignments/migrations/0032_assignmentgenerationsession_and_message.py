import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("classrooms", "0013_studentcourse_ai_summary_and_more"),
        ("assignments", "0031_assignment_grading_task_name_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="AssignmentGenerationSession",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("title", models.CharField(blank=True, max_length=255, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True, db_index=True)),
                (
                    "course",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="assignment_generation_sessions",
                        to="classrooms.course",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="assignment_generation_sessions",
                        to="users.customuser",
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at", "-created_at"],
                "indexes": [
                    models.Index(
                        fields=["user", "course", "-updated_at"],
                        name="assignmentg_user_id_b13b45_idx",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="AssignmentGenerationMessage",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "role",
                    models.CharField(
                        choices=[("USER", "User"), ("ASSISTANT", "Assistant")],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                ("content", models.TextField()),
                ("assignment_snapshot", models.JSONField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "assignment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="generation_messages",
                        to="assignments.assignment",
                    ),
                ),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="messages",
                        to="assignments.assignmentgenerationsession",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at"],
                "indexes": [
                    models.Index(
                        fields=["session", "created_at"],
                        name="assignmentg_session_b8f3ec_idx",
                    ),
                    models.Index(
                        fields=["session", "role", "created_at"],
                        name="assignmentg_session_3ce73e_idx",
                    ),
                ],
            },
        ),
    ]
