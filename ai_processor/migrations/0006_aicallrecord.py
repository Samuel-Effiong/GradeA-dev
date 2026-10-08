"""The per-call usage record (H-201): one new table, nothing else.

Additive: a new table with its own indexes and no foreign key, so no existing
table is touched or locked. Written after each AI call by
ai_processor/call_record.py (switch AI_CALL_RECORD_ENABLED).

To undo: go back to the previous version of the code and leave the table in
place, unused (older code never writes it). Reversing this migration drops the
table and the measurements in it, and nothing else references it.
"""

import uuid

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ai_processor", "0005_benchmarkrun_benchmarkquestionoutcome"),
    ]

    operations = [
        migrations.CreateModel(
            name="AICallRecord",
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
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "task_id",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        help_text="The id CreditUsageLog.task_id carries; NULL when never charged.",
                        max_length=200,
                        null=True,
                    ),
                ),
                (
                    "task_type",
                    models.CharField(
                        choices=[
                            ("extract_assignment", "extract_assignment"),
                            ("extract_answer", "extract_answer"),
                            ("grade_assignment", "grade_assignment"),
                            ("generate_assignment", "generate_assignment"),
                            ("formatted_grade", "formatted_grade"),
                            ("student_summary", "student_summary"),
                            ("weekly_course_summary", "weekly_course_summary"),
                            (
                                "weekly_school_admin_summary",
                                "weekly_school_admin_summary",
                            ),
                            ("other", "other"),
                        ],
                        max_length=40,
                    ),
                ),
                (
                    "feature",
                    models.CharField(
                        choices=[
                            ("Assignment Extraction", "Assignment Extraction"),
                            ("Answer Extraction", "Answer Extraction"),
                            ("Grading Assignment", "Grading Assignment"),
                            ("Assignment Generation", "Assignment Generation"),
                            ("Formatted Grade", "Formatted Grade"),
                            ("Student Summary", "Student Summary"),
                            ("Weekly Course Summary", "Weekly Course Summary"),
                            (
                                "Weekly School Admin Summary",
                                "Weekly School Admin Summary",
                            ),
                            ("other", "other"),
                        ],
                        max_length=40,
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("assignment_extraction", "Assignment extraction"),
                            ("assignment_generation", "Assignment generation"),
                            ("formatting", "Feedback wording"),
                            ("student_summary", "Student summary"),
                            ("weekly_course_summary", "Weekly course summary"),
                            (
                                "weekly_school_admin_summary",
                                "Weekly school admin summary",
                            ),
                        ],
                        help_text="Set only where the task type proves it; otherwise NULL.",
                        max_length=40,
                        null=True,
                    ),
                ),
                (
                    "outcome",
                    models.CharField(
                        choices=[
                            ("charged", "Charged"),
                            ("unmetered", "Unmetered (super admin)"),
                            ("refused_access", "Refused before the estimate"),
                            ("refused_gate", "Refused at the balance check"),
                            ("provider_error", "The provider call failed"),
                            ("cancelled_after_reply", "Cancelled after the reply"),
                            ("balance_changed", "Balance changed after the reply"),
                            ("charge_failed", "The charge failed after the reply"),
                            ("other_error", "Another error"),
                        ],
                        max_length=24,
                    ),
                ),
                (
                    "error_class",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("timeout", "timeout"),
                            ("rate_limit", "rate_limit"),
                            ("connection", "connection"),
                            ("api_status", "api_status"),
                            ("cancelled", "cancelled"),
                            ("insufficient_credits", "insufficient_credits"),
                            ("feature_not_available", "feature_not_available"),
                            ("value", "value"),
                            ("other", "other"),
                        ],
                        max_length=24,
                        null=True,
                    ),
                ),
                (
                    "model_asked",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                (
                    "model_answered",
                    models.CharField(blank=True, max_length=255, null=True),
                ),
                (
                    "finish_reason",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("stop", "stop"),
                            ("length", "length"),
                            ("tool_calls", "tool_calls"),
                            ("content_filter", "content_filter"),
                            ("function_call", "function_call"),
                            ("error", "error"),
                            ("other", "other"),
                        ],
                        max_length=16,
                        null=True,
                    ),
                ),
                (
                    "prompt_version",
                    models.CharField(
                        db_default="unknown",
                        default="unknown",
                        help_text="Not filled on the beta line (no prompt version reaches the call).",
                        max_length=128,
                    ),
                ),
                ("estimate", models.PositiveBigIntegerField(blank=True, null=True)),
                (
                    "prompt_tokens",
                    models.PositiveBigIntegerField(blank=True, null=True),
                ),
                (
                    "completion_tokens",
                    models.PositiveBigIntegerField(blank=True, null=True),
                ),
                (
                    "reasoning_tokens",
                    models.PositiveBigIntegerField(blank=True, null=True),
                ),
                (
                    "cached_tokens",
                    models.PositiveBigIntegerField(blank=True, null=True),
                ),
                ("total_tokens", models.PositiveBigIntegerField(blank=True, null=True)),
                ("charged_credits", models.PositiveBigIntegerField(default=0)),
                (
                    "cost_usd",
                    models.DecimalField(
                        blank=True,
                        decimal_places=8,
                        help_text="The provider's reported cost; NULL when the reply carries none.",
                        max_digits=14,
                        null=True,
                    ),
                ),
                ("images", models.PositiveIntegerField(blank=True, null=True)),
                ("pdf_items", models.PositiveIntegerField(blank=True, null=True)),
                ("questions", models.PositiveIntegerField(blank=True, null=True)),
                ("duration_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("billed_user_id", models.UUIDField(blank=True, null=True)),
                ("school_id", models.UUIDField(blank=True, null=True)),
                ("assignment_id", models.UUIDField(blank=True, null=True)),
                ("processing_task_id", models.UUIDField(blank=True, null=True)),
            ],
        ),
    ]
