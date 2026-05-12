"The love of God"

from django.db import models
from django.db.models import F, Sum
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from users.models import CustomUser

from .models import (
    CONVERSION_FACTOR,
    BetaProfile,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditUsageLog,
    CreditWallet,
    SubscriptionPlan,
    UserSubscription,
)
from .services import SubscriptionService


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    """
    Serializer for the SubscriptionPlan model.
    Includes all fields to provide a comprehensive view of the plan details.
    """

    display_monthly_credits = serializers.ReadOnlyField()
    display_carry_over_max = serializers.ReadOnlyField()
    display_overage_block_size = serializers.ReadOnlyField()

    class Meta:
        model = SubscriptionPlan
        fields = [
            "id",
            "name",
            "display_name",
            "monthly_credits",
            "carry_over_percent",
            "carry_over_max",
            "carry_over_expiry_months",
            "overage_block_size",
            "overage_block_price",
            "max_overage_blocks",
            "is_active",
            "display_monthly_credits",
            "display_carry_over_max",
            "display_overage_block_size",
        ]

        extra_kwargs = {
            "monthly_credits": {"write_only": True},
            "carry_over_max": {"write_only": True},
            "overage_block_size": {"write_only": True},
        }

    @extend_schema_field(int)
    def get_display_monthly_credits(self, obj) -> int:
        return obj.display_monthly_credits

    def get_display_carry_over_max(
        self, obj
    ) -> int:  # Adjust to str if it's formatted as string
        return obj.display_carry_over_max

    def get_display_overage_block_size(
        self, obj
    ) -> int:  # Adjust to str if it's formatted as string
        return obj.display_overage_block_size


class UserSubscriptionSerializer(serializers.ModelSerializer):
    """
    Serializer for the UserSubscription model.
    """

    class Meta:
        model = UserSubscription
        fields = [
            "id",
            "user",
            "plan",
            "is_active",
            "billing_cycle_start",
            "billing_cycle_end",
            "auto_renew",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "created_at",
            "updated_at",
            "is_active",
            "billing_cycle_start",
            "billing_cycle_end",
        ]

        extra_kwargs = {
            "is_active": {"required": False},
        }

    def create(self, validated_data):
        # Delegate all business logic to the Service Layer
        return SubscriptionService.activate_subscription(
            user=validated_data["user"], plan=validated_data["plan"]
        )


class CreditBucketSerializer(serializers.ModelSerializer):
    """
    Serializer for the CreditBucket model.
    """

    remaining_credits = serializers.IntegerField(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = CreditBucket
        fields = [
            "id",
            "wallet",
            "bucket_type",
            "total_credits",
            "used_credits",
            "remaining_credits",
            "expires_at",
            "is_expired",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class CreditWalletSerializer(serializers.ModelSerializer):
    """
    Serializer for the CreditWallet model.
    """

    total_remaining_credits = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = CreditWallet
        fields = [
            "id",
            "user",
            "overage_blocks_used",
            "total_remaining_credits",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]

    def get_total_remaining_credits(self, obj) -> int:
        return obj.total_remaining_credits()

    def create(self, validated_data):
        request = self.context.get("request")

        if request and request.user:
            validated_data["user"] = request.user

        validated_data["overage_blocks_used"] = 0

        return super().create(validated_data)


class CreditLedgerSerializer(serializers.ModelSerializer):
    """
    Serializer for the CreditLedger model.
    """

    class Meta:
        model = CreditLedger
        fields = [
            "id",
            "user",
            "bucket",
            "ledger_type",
            "amount",
            "reference",
            "metadata",
            "created_at",
        ]
        read_only_fields = ["created_at"]


class CreditUsageLogSerializer(serializers.ModelSerializer):
    """
    Serializer for the CreditUsageLog model.
    """

    bucket_type = serializers.CharField(source="bucket.bucket_type", read_only=True)

    class Meta:
        model = CreditUsageLog
        fields = [
            "id",
            "wallet",
            "bucket_type",
            "amount",
            "feature",
            "task_type",
            "task_id",
            "created_at",
        ]
        read_only_fields = ["created_at"]


class SubscriptionSerializer(serializers.Serializer):
    user = serializers.PrimaryKeyRelatedField(queryset=CustomUser.objects.all())
    plan = serializers.PrimaryKeyRelatedField(queryset=SubscriptionPlan.objects.all())


class CreditWalletSummarySerializer(serializers.ModelSerializer):
    """
    Serializer for the CreditWallet model.
    """

    active_buckets_count = serializers.SerializerMethodField(read_only=True)
    display_total_remaining_credits = serializers.IntegerField(source="display_balance")
    total_remaining_credits = serializers.SerializerMethodField(read_only=True)

    # --- Progress Bar Fields ---
    # The plan's monthly allocation — the "100%" baseline for the progress bar
    monthly_credit_total = serializers.SerializerMethodField(read_only=True)
    # Only the current MONTHLY bucket remaining — excludes carry-over to keep math clean
    monthly_credit_remaining = serializers.SerializerMethodField(read_only=True)
    # Pre-calculated percentage (0–100) so the frontend doesn't need to do math
    credit_percentage_remaining = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = CreditWallet
        fields = [
            "id",
            "user",
            "overage_blocks_used",
            "active_buckets_count",
            "total_remaining_credits",
            "display_total_remaining_credits",
            "monthly_credit_total",
            "monthly_credit_remaining",
            "credit_percentage_remaining",
        ]
        read_only_fields = [
            "id",
            "user",
            "overage_blocks_used",
            "active_buckets_count",
            "total_credits",
        ]

    def get_total_remaining_credits(self, obj) -> int:
        return obj.total_remaining_credits()

    def get_active_buckets_count(self, obj) -> int:
        return obj.buckets.filter(expires_at__gt=timezone.now()).count()

    def get_monthly_credit_total(self, obj) -> int:
        """
        Returns the teacher's plan monthly credit allowance as a display value.
        This is the '100%' ceiling for the progress bar.
        """
        subscription = (
            obj.user.subscriptions.filter(is_active=True).select_related("plan").first()
        )
        if not subscription:
            return 0
        return subscription.plan.display_monthly_credits

    def get_monthly_credit_remaining(self, obj) -> int:
        """
        Returns only the remaining credits in the active MONTHLY bucket as a display value.
        Intentionally excludes CARRY_OVER so the bar always reflects the current month's budget.
        A value above 100% is impossible — carry-over is shown separately.
        """
        now = timezone.now()
        monthly_bucket = obj.buckets.filter(
            bucket_type=CreditBucketType.MONTHLY,
            expires_at__gt=now,
        ).first()
        if not monthly_bucket:
            return 0
        from .models import CONVERSION_FACTOR

        return monthly_bucket.remaining_credits // CONVERSION_FACTOR

    def get_credit_percentage_remaining(self, obj) -> float:
        """
        Returns the percentage of the monthly credit budget that remains (0.0 – 100.0).
        Color thresholds for the frontend progress bar:
          - >= 50%  → Green  (healthy)
          - >= 20%  → Amber  (warning)
          -  < 20%  → Red    (critical)
        """
        # total = self.get_monthly_credit_total(obj)
        # if not total:
        #     return 0.0
        # remaining = self.get_monthly_credit_remaining(obj)
        # percentage = (remaining / total) * 100
        # return round(min(percentage, 100.0), 2)

        # total = self.get_monthly_credit_total(obj)
        # if not total:
        #     return 0.0

        # remaining = getattr(obj, "display_balance", obj.display_balance)
        # percentage = (remaining / total) * 100
        # return round(min(percentage, 100.0), 2)

        now = timezone.now()

        active_buckets_query = obj.buckets.filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        )

        aggregate_result = active_buckets_query.aggregate(
            total_initial=models.Sum("total_credits")
        )
        total_allocated = aggregate_result["total_initial"] or 0

        # 2. Prevent Division by Zero (if they have absolutely no active buckets)
        if not total_allocated:
            return 0.0

        # 3. Calculate Global Percentage using the raw remaining value
        # (It's mathematically safer to stick to raw DB units here rather than display units)
        remaining = obj.total_remaining_credits()
        percentage = (remaining / total_allocated) * 100

        return round(percentage, 2)


class UsageSummarySerializer(serializers.Serializer):
    billing_cycle_start = serializers.DateTimeField()
    billing_cycle_end = serializers.DateTimeField()
    total_consumed = serializers.IntegerField()
    consumed_by_feature = serializers.DictField()
    consumed_by_bucket_type = serializers.DictField()


class OverageStatusSerializer(serializers.ModelSerializer):
    max_blocks = serializers.IntegerField(
        source="user.subscriptions.filter(is_active=True).first.plan.max_overage_blocks",
        read_only=True,
    )
    block_size = serializers.IntegerField(
        source="user.subscriptions.filter(is_active=True).first.plan.overage_block_size",
        read_only=True,
    )
    block_remaining = serializers.SerializerMethodField()
    current_overage_balance = serializers.SerializerMethodField()

    class Meta:
        model = CreditWallet
        fields = [
            "overage_blocks_used",
            "max_blocks",
            "block_size",
            "block_remaining",
            "current_overage_balance",
        ]

    def get_block_remaining(self, obj) -> int:
        plan = obj.user.subscriptions.filter(is_active=True).first().plan
        return max(0, plan.max_overage_blocks - obj.overage_blocks_used)

    def get_current_overage_balance(self, obj) -> int:
        return (
            obj.buckets.filter(bucket_type=CreditBucketType.OVERAGE).aggregate(
                total=Sum(F("total_credits") - F("used_credits"))
            )["total"]
            or 0
        )


class CarryOverHistorySerializer(serializers.ModelSerializer):
    days_until_expiry = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    remaining_credits = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = CreditBucket
        fields = [
            "id",
            "total_credits",
            "used_credits",
            "remaining_credits",
            "created_at",
            "expires_at",
            "days_until_expiry",
            "status",
        ]

    def get_remaining_credits(self, obj) -> int:
        return obj.remaining_credits

    def get_days_until_expiry(self, obj) -> int | None:
        if not obj.expires_at:
            return None
        delta = obj.expires_at - timezone.now()
        return max(0, delta.days)

    def get_status(self, obj) -> str:
        if obj.is_expired:
            return "expired"
        if obj.remaining_credits == 0:
            return "exhausted"
        return "active"


class ManualCreditTopUpSerializer(serializers.Serializer):
    """
    Input serializer for a superadmin manual credit grant.

    `amount` is expressed in display units (the user-facing number).
    Internally it is multiplied by CONVERSION_FACTOR before storage.
    For example, passing amount=500 injects 500,000 raw credits.
    """

    user_id = serializers.UUIDField(
        help_text="UUID of the user who will receive the credits."
    )
    amount = serializers.IntegerField(
        min_value=1,
        help_text=(
            "Credits to grant in display units (e.g. 500 = 500 AI credits visible "
            f"to the user). Stored internally as amount × {CONVERSION_FACTOR}."
        ),
    )
    reason = serializers.CharField(
        max_length=500,
        help_text=(
            "Human-readable explanation for the grant. This is written verbatim "
            "into the immutable audit ledger."
        ),
    )
    expires_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
        default=None,
        help_text=(
            "Optional expiry datetime (ISO 8601). If omitted or null, "
            "the granted credits never expire."
        ),
    )

    def validate_user_id(self, value):
        try:
            user = CustomUser.objects.get(id=value)
        except CustomUser.DoesNotExist as e:
            raise serializers.ValidationError(
                f"No user found with id {value!r}."
            ) from e
        return user  # Return the resolved instance for convenience in the view

    def validate_expires_at(self, value):
        if value and value <= timezone.now():
            raise serializers.ValidationError("expires_at must be a future datetime.")
        return value

    def validate(self, data):
        # Replace user_id with the resolved user object (set by validate_user_id)
        # so the view can pass it directly to ManualCreditService.
        return data


class ManualGrantBucketSerializer(serializers.ModelSerializer):
    """
    Read serializer for a MANUAL_GRANT CreditBucket.
    Exposes display-unit amounts and a computed status for the frontend.
    """

    display_total = serializers.SerializerMethodField(
        help_text=f"total_credits ÷ {CONVERSION_FACTOR} (user-facing amount)"
    )
    display_remaining = serializers.SerializerMethodField(
        help_text=f"remaining_credits ÷ {CONVERSION_FACTOR} (user-facing amount)"
    )
    display_used = serializers.SerializerMethodField(
        help_text=f"used_credits ÷ {CONVERSION_FACTOR} (user-facing amount)"
    )
    status = serializers.SerializerMethodField()
    days_until_expiry = serializers.SerializerMethodField()
    granted_by_email = serializers.SerializerMethodField(
        help_text="Email of the admin who created this grant (from the ledger metadata)."
    )
    ledger_reason = serializers.SerializerMethodField(
        help_text="The reason string recorded in the audit ledger."
    )
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = CreditBucket
        fields = [
            "id",
            "wallet",
            "bucket_type",
            "total_credits",
            "used_credits",
            "display_total",
            "display_remaining",
            "display_used",
            "expires_at",
            "days_until_expiry",
            "is_expired",
            "status",
            "granted_by_email",
            "ledger_reason",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_display_total(self, obj) -> int:
        return obj.total_credits // CONVERSION_FACTOR

    def get_display_remaining(self, obj) -> int:
        return obj.remaining_credits // CONVERSION_FACTOR

    def get_display_used(self, obj) -> int:
        return obj.used_credits // CONVERSION_FACTOR

    def get_is_expired(self, obj) -> bool:
        return obj.is_expired()

    def get_status(self, obj) -> str:
        if obj.is_expired():
            return "expired"
        if obj.remaining_credits == 0:
            return "exhausted"
        return "active"

    def get_days_until_expiry(self, obj) -> int | None:
        if not obj.expires_at:
            return None
        delta = obj.expires_at - timezone.now()
        return max(0, delta.days)

    def _get_ledger_entry(self, obj):
        """Helper: fetch the GRANT ledger entry for this bucket (cached on instance)."""
        if not hasattr(obj, "_grant_ledger"):
            obj._grant_ledger = obj.credit_ledgers.filter(ledger_type="GRANT").first()
        return obj._grant_ledger

    def get_granted_by_email(self, obj) -> str | None:
        ledger = self._get_ledger_entry(obj)
        if ledger and ledger.metadata:
            return ledger.metadata.get("granted_by_email")
        return None

    def get_ledger_reason(self, obj) -> str | None:
        ledger = self._get_ledger_entry(obj)
        return ledger.reference if ledger else None


class AdminGrantSummarySerializer(serializers.Serializer):
    """
    Aggregate summary of all manual grants — used on the superadmin dashboard.
    """

    total_grants = serializers.IntegerField()
    total_credits_granted_display = serializers.IntegerField(
        help_text="Sum of all granted credits in display units."
    )
    total_credits_remaining_display = serializers.IntegerField(
        help_text="Sum of all remaining credits across active MANUAL_GRANT buckets."
    )
    active_grants = serializers.IntegerField(
        help_text="Number of grants that are not expired and not exhausted."
    )
    expired_grants = serializers.IntegerField()
    exhausted_grants = serializers.IntegerField()


class BetaSummarySerializer(serializers.Serializer):
    total_beta_users = serializers.IntegerField()
    active_last_7_days_percent = serializers.FloatField()
    avg_credits_used = serializers.FloatField()
    percent_users_at_cap = serializers.FloatField()
    avg_days_to_first_action = serializers.FloatField()
    credit_used_greater_than_80_percent = serializers.FloatField()
    login_greater_than_8_days = serializers.FloatField()
    grading_percent_greater_than_creation_percent = serializers.FloatField()


class DailyTimeSeriesSerializer(serializers.Serializer):
    date = serializers.DateField()
    credits = serializers.IntegerField()


class BetaCohortStatsSerializer(serializers.Serializer):
    standard_allocation = serializers.IntegerField()
    total_users_analyzed = serializers.IntegerField()
    average_credit_used = serializers.FloatField()
    median_credit_used = serializers.FloatField()
    p90_credit_used = serializers.FloatField()
    average_days_to_reach_cap = serializers.IntegerField()
    percent_unused_credits = serializers.FloatField()


class FeatureConsumptionTimeSeriesSerializer(serializers.Serializer):
    date = serializers.DateField()
    avg_tokens_grading = serializers.FloatField()
    avg_tokens_feedback = serializers.FloatField()
    avg_tokens_creation = serializers.FloatField()


class BetaFeatureMixSerializer(serializers.Serializer):
    grading_percent = serializers.FloatField()
    creation_percent = serializers.FloatField()
    feedback_percent = serializers.FloatField()
    other_percent = serializers.FloatField()
    average_feedback_depth_token = serializers.IntegerField()
    total_analytics_views = serializers.IntegerField()
    views_per_user = serializers.FloatField()
    primary_driver = serializers.CharField()
    engagement_quality = serializers.CharField()
    consumption_time_series = FeatureConsumptionTimeSeriesSerializer(many=True)


class PeakUsageHourSerializer(serializers.Serializer):
    hour_24h = serializers.IntegerField()
    total_credits = serializers.IntegerField()


class WeeklyGrowthSerializer(serializers.Serializer):
    week_start = serializers.DateField()
    total_credits = serializers.IntegerField()


class InfrastructureInsightSerializer(serializers.Serializer):
    peak_hour = serializers.IntegerField(allow_null=True)
    current_week_velocity = serializers.IntegerField()


# class BetaUsageTrendSerializer(serializers.Serializer):
# daily_time_series = DailyTimeSeriesSerializer(many=True)
# peak_usage_hours = PeakUsageHourSerializer(many=True)
# weekly_growth = WeeklyGrowthSerializer(many=True)
# infrastructure_insight = InfrastructureInsightSerializer()


class UsageQuintileBreakdownSerializer(serializers.Serializer):
    group_label = serializers.CharField()
    user_count = serializers.IntegerField()
    user_ids = serializers.ListField(child=serializers.UUIDField())
    grading_percent = serializers.FloatField()
    feedback_percent = serializers.FloatField()
    creation_percent = serializers.FloatField()
    other_percent = serializers.FloatField()


class UsageQuintileResponseSerializer(serializers.Serializer):
    quintiles = UsageQuintileBreakdownSerializer(many=True)


class IntentSignalDistributionSerializer(serializers.Serializer):
    signal_count = serializers.IntegerField()
    user_count = serializers.IntegerField()
    user_ids = serializers.ListField(child=serializers.UUIDField())


class IntentSignalResponseSerializer(serializers.Serializer):
    distribution = IntentSignalDistributionSerializer(many=True)


class CreditUsageBucketSerializer(serializers.Serializer):
    bucket = serializers.CharField()
    user_count = serializers.IntegerField()
    user_ids = serializers.ListField(child=serializers.UUIDField())


class CreditUsageDistributionResponseSerializer(serializers.Serializer):
    distribution = CreditUsageBucketSerializer(many=True)


class ConversionLeadMetricsSerializer(serializers.Serializer):
    usage_percentage = serializers.FloatField()
    login_days = serializers.IntegerField()
    last_active = serializers.DateField(allow_null=True)
    primary_use_case = serializers.CharField()


class ConversionLeadFlagsSerializer(serializers.Serializer):
    at_80_percent = serializers.BooleanField()
    active_last_week = serializers.BooleanField()
    is_power_grader = serializers.BooleanField()


class ConversionLeadSerializer(serializers.Serializer):
    email = serializers.EmailField()
    score = serializers.FloatField()
    metrics = ConversionLeadMetricsSerializer()
    flags = ConversionLeadFlagsSerializer()


class BetaProfileSerializer(serializers.ModelSerializer):
    """
    Serializer for the BetaProfile model providing deep insights
    into user behavior and credit consumption.
    """

    # Bring in user details for context without extra DB hits
    # (handled via select_related in ViewSet)
    user_email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.get_full_name", read_only=True)

    # Calculated Fields for the Dashboard
    credits_remaining = serializers.SerializerMethodField()
    utilization_percentage = serializers.SerializerMethodField()

    class Meta:
        model = BetaProfile
        fields = [
            "id",
            "user_email",
            "full_name",
            "joined_beta_at",
            "first_ai_action_at",
            "last_active_at",
            "last_login_date",
            "initial_beta_credits",
            "total_credits_used",
            "credits_remaining",
            "credits_used_grading",
            "credits_used_creation",
            "analytics_view_count",
            "distinct_login_days",
            "has_hit_80_percent",
            "has_hit_cap",
            "conversion_probability",
            "days_to_first_action",
            "usage_velocity",
            "utilization_percentage",
        ]
        read_only_fields = [
            "total_credits_used",
            "conversion_probability",
            "usage_velocity",
        ]

    @extend_schema_field(serializers.IntegerField())
    def get_credits_remaining(self, obj) -> int:
        """Calculates current credit balance."""
        return max(0, obj.initial_beta_credits - obj.total_credits_used)

    @extend_schema_field(serializers.FloatField())
    def get_utilization_percentage(self, obj) -> float:
        """Calculates how much of the initial grant has been consumed."""
        if obj.initial_beta_credits == 0:
            return 0.0
        percentage = (obj.total_credits_used / obj.initial_beta_credits) * 100
        return round(percentage, 2)
