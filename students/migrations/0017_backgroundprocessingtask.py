import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("students", "0016_studentsubmission_grading_task_name_and_more"),
        ("users", "0025_customuser_profile_image"),
        ("assignments", "0033_assignmentgenerationhistory_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="BackgroundProcessingTask",
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
                    "celery_task_id",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        help_text="The Celery task id for the running background process.",
                        max_length=255,
                        null=True,
                        unique=True,
                    ),
                ),
                (
                    "task_type",
                    models.CharField(
                        choices=[
                            ("assignment_extraction", "Assignment Extraction"),
                            ("assignment_reextraction", "Assignment Re-extraction"),
                            ("batch_assignment_upload", "Batch Assignment Upload"),
                            ("answer_extraction", "Answer Extraction"),
                            ("batch_answer_upload", "Batch Answer Upload"),
                            ("submission_grading", "Submission Grading"),
                            ("batch_submission_grading", "Batch Submission Grading"),
                            ("formatted_grade", "Formatted Grade"),
                        ],
                        max_length=64,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "Pending"),
                            ("STARTED", "Started"),
                            ("CANCELLED", "Cancelled"),
                            ("SUCCESS", "Success"),
                            ("FAILURE", "Failure"),
                        ],
                        db_index=True,
                        default="PENDING",
                        max_length=20,
                    ),
                ),
                ("file_name", models.CharField(blank=True, max_length=255, null=True)),
                ("meta", models.JSONField(blank=True, default=dict)),
                ("error", models.TextField(blank=True, null=True)),
                ("cancel_requested_at", models.DateTimeField(blank=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "assignment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.CASCADE,
                        related_name="processing_tasks",
                        to="assignments.assignment",
                    ),
                ),
                (
                    "batch_session",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.CASCADE,
                        related_name="processing_tasks",
                        to="students.batchuploadsession",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="background_processing_tasks",
                        to="users.customuser",
                    ),
                ),
                (
                    "submission",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.CASCADE,
                        related_name="processing_tasks",
                        to="students.studentsubmission",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
