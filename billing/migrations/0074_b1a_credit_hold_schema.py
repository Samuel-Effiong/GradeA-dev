"""Epic B1a step 1 (plan 08 §4): the credit-hold schema.

Class: ADDITIVE (docs/MIGRATIONS.md §2).
- Three new tables: AIJob, SettlementRecord, BillingSettings. Their indexes
  and constraints are created with the table, so no existing row is read.
- New CreditWallet columns: reserved_credits has a real default and
  db_default=0 (H-56: rolled-back code still inserts); the other two are
  nullable.
- New SubscriptionPlan columns with db_default (14, 48).
The CreditWallet CHECK (reserved_credits >= 0) is added NOT VALID in 0075 and
validated in 0076.

Reversing it: allowed only with BILLING_HOLDS_ENABLED off and every
CreditWallet.reserved_credits at 0 (refuse_reverse_with_open_holds), because
reversing with holds open throws away hold state.
"""

import uuid

import django.db.models.deletion
import django.db.models.expressions
from django.conf import settings
from django.db import migrations, models


def refuse_reverse_with_open_holds(apps, schema_editor):
    """Plan 08 §4: reversing reserved_credits is allowed only with the flag
    off and no hold open; otherwise the hold state would be thrown away."""
    if getattr(settings, "BILLING_HOLDS_ENABLED", False):
        raise RuntimeError(
            "Refusing to reverse billing 0074: BILLING_HOLDS_ENABLED is on."
        )
    wallet_model = apps.get_model("billing", "CreditWallet")
    open_holds = wallet_model.objects.exclude(reserved_credits=0).count()
    if open_holds:
        raise RuntimeError(
            "Refusing to reverse billing 0074: "
            f"{open_holds} wallet(s) have reserved_credits other than 0."
        )


class Migration(migrations.Migration):

    dependencies = [
        ("assignments", "0040_db_defaults_for_rollback"),
        ("billing", "0073_schoolcreditallocation_grant_anchor_at"),
        ("classrooms", "0018_alter_session_is_active"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AIJob",
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
                    "job_type",
                    models.CharField(
                        choices=[
                            ("BATCH_GRADING", "Batch Grading"),
                            ("LESSON_GENERATION", "Lesson Generation"),
                            ("ASSIGNMENT_GENERATION", "Assignment Generation"),
                            ("INSIGHT_GENERATION", "Insight Generation"),
                            ("SINGLE_CALL", "Single Call"),
                            ("SINGLE_ITEM", "Single Item"),
                            ("UPLOAD", "Upload"),
                        ],
                        db_index=True,
                        max_length=32,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("ESTIMATED", "Estimated"),
                            ("RESERVED", "Reserved"),
                            ("RUNNING", "Running"),
                            ("SETTLED", "Settled"),
                            ("FAILED", "Failed"),
                            ("QUEUED_PENDING_CREDITS", "Queued Pending Credits"),
                            ("EXPIRED", "Expired"),
                            ("CANCELLED", "Cancelled"),
                        ],
                        db_index=True,
                        default="ESTIMATED",
                        max_length=32,
                    ),
                ),
                ("school_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("estimate_p50", models.IntegerField(blank=True, null=True)),
                ("estimate_p90", models.IntegerField(blank=True, null=True)),
                (
                    "sufficiency_verdict",
                    models.CharField(blank=True, max_length=16, null=True),
                ),
                ("reserved_credits", models.IntegerField(db_default=0, default=0)),
                ("reservation_claimed_at", models.DateTimeField(blank=True, null=True)),
                ("consumed_credits", models.IntegerField(db_default=0, default=0)),
                ("released_credits", models.IntegerField(db_default=0, default=0)),
                ("hold_drawn", models.IntegerField(db_default=0, default=0)),
                ("unbilled_credits", models.IntegerField(db_default=0, default=0)),
                ("overrun_charged", models.IntegerField(db_default=0, default=0)),
                ("held_per_item", models.IntegerField(db_default=0, default=0)),
                ("total_items", models.IntegerField(db_default=0, default=0)),
                ("completed_items", models.IntegerField(db_default=0, default=0)),
                ("failed_items", models.IntegerField(db_default=0, default=0)),
                ("stopped_at_item", models.IntegerField(blank=True, null=True)),
                ("queued_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("warned_expiry_at", models.DateTimeField(blank=True, null=True)),
                ("cancel_requested_at", models.DateTimeField(blank=True, null=True)),
                ("trace_id", models.UUIDField(db_index=True)),
                ("meta", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "assignment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="ai_jobs",
                        to="assignments.assignment",
                    ),
                ),
                (
                    "course",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="ai_jobs",
                        to="classrooms.course",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ai_jobs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "wallet",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ai_jobs",
                        to="billing.creditwallet",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        condition=models.Q(("status__in", ["RESERVED", "RUNNING"])),
                        fields=["wallet", "status"],
                        name="aijob_wallet_open_ix",
                    ),
                    models.Index(
                        condition=models.Q(("status", "QUEUED_PENDING_CREDITS")),
                        fields=["status", "expires_at"],
                        name="aijob_queued_expiry_ix",
                    ),
                    models.Index(
                        fields=["requested_by", "-created_at"],
                        name="aijob_requester_created_ix",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            ("reserved_credits__gte", 0),
                            ("consumed_credits__gte", 0),
                            ("released_credits__gte", 0),
                        ),
                        name="aijob_credits_nonneg",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "total_items__gte",
                                django.db.models.expressions.CombinedExpression(
                                    models.F("completed_items"),
                                    "+",
                                    models.F("failed_items"),
                                ),
                            )
                        ),
                        name="aijob_items_within_total",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="BillingSettings",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "cold_start_cost_factor",
                    models.FloatField(
                        default=3.0,
                        help_text="Multiplier for the cold-start estimate fallback.",
                    ),
                ),
                (
                    "unstarted_backstop_hours",
                    models.PositiveSmallIntegerField(
                        default=36,
                        help_text="Hours before unstarted items in a job are failed by the backstop.",
                    ),
                ),
            ],
            options={
                "verbose_name": "Billing settings",
                "verbose_name_plural": "Billing settings",
            },
        ),
        migrations.CreateModel(
            name="SettlementRecord",
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
                    "item_id",
                    models.UUIDField(
                        help_text="BackgroundProcessingTask id, or the job id for E-4/E-5/SINGLE_CALL."
                    ),
                ),
                ("retry_count", models.PositiveIntegerField(default=0)),
                ("credits_charged", models.IntegerField(default=0)),
                ("settled_at", models.DateTimeField(auto_now_add=True)),
                (
                    "job",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="settlements",
                        to="billing.aijob",
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("item_id", "retry_count"),
                        name="unique_settlement_per_item_retry",
                    )
                ],
            },
        ),
        migrations.AddField(
            model_name="creditwallet",
            name="low_balance_warned_cycle_start",
            field=models.DateField(
                blank=True,
                help_text="Billing-cycle start date the last warning was for.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="creditwallet",
            name="low_balance_warned_level",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="Last threshold-warning level sent this cycle (25, 10 or 0).",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="creditwallet",
            name="reserved_credits",
            field=models.IntegerField(
                db_default=0,
                default=0,
                help_text="Credits currently reserved by open AIJob holds. Spendable = total_remaining_credits() − reserved_credits.",
            ),
        ),
        # Runs on reverse before the column is dropped (operations reverse in
        # reverse order).
        migrations.RunPython(migrations.RunPython.noop, refuse_reverse_with_open_holds),
        migrations.AddField(
            model_name="subscriptionplan",
            name="queue_expiry_days",
            field=models.PositiveSmallIntegerField(
                db_default=14,
                default=14,
                help_text="Days a queued job waits before expiring.",
            ),
        ),
        migrations.AddField(
            model_name="subscriptionplan",
            name="queue_expiry_warning_hours",
            field=models.PositiveSmallIntegerField(
                db_default=48,
                default=48,
                help_text="Hours before expiry to send the warning email.",
            ),
        ),
    ]
