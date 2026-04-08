from django.db.models import F, Sum
from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from users.models import CustomUser

from .models import (
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
        total = self.get_monthly_credit_total(obj)
        if not total:
            return 0.0
        remaining = self.get_monthly_credit_remaining(obj)
        percentage = (remaining / total) * 100
        return round(min(percentage, 100.0), 2)


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


class BetaSummarySerializer(serializers.Serializer):
    total_beta_users = serializers.IntegerField()
    active_last_7_days_percent = serializers.FloatField()
    avg_credits_used = serializers.FloatField()
    percent_users_at_cap = serializers.FloatField()
    avg_days_to_first_action = serializers.FloatField()


class BetaCohortStatsSerializer(serializers.Serializer):
    standard_allocation = serializers.IntegerField()
    total_users_analyzed = serializers.IntegerField()
    average_credit_used = serializers.FloatField()
    median_credit_used = serializers.FloatField()
    p90_credit_used = serializers.FloatField()
    average_days_to_reach_cap = serializers.IntegerField()
    percent_unused_credits = serializers.FloatField()


class BetaFeatureMixSerializer(serializers.Serializer):
    grading_percent = serializers.FloatField()
    creation_percent = serializers.FloatField()
    other_percent = serializers.FloatField()
    average_feedback_depth_token = serializers.IntegerField()
    total_analytics_views = serializers.IntegerField()
    views_per_user = serializers.FloatField()
    primary_driver = serializers.CharField()
    engagement_quality = serializers.CharField()


class DailyTimeSeriesSerializer(serializers.Serializer):
    date = serializers.DateField()
    credits = serializers.IntegerField()


class PeakUsageHourSerializer(serializers.Serializer):
    hour_24h = serializers.IntegerField()
    total_credits = serializers.IntegerField()


class WeeklyGrowthSerializer(serializers.Serializer):
    week_start = serializers.DateField()
    total_credits = serializers.IntegerField()


class InfrastructureInsightSerializer(serializers.Serializer):
    peak_hour = serializers.IntegerField(allow_null=True)
    current_week_velocity = serializers.IntegerField()


class BetaUsageTrendSerializer(serializers.Serializer):
    daily_time_series = DailyTimeSeriesSerializer(many=True)
    peak_usage_hours = PeakUsageHourSerializer(many=True)
    weekly_growth = WeeklyGrowthSerializer(many=True)
    infrastructure_insight = InfrastructureInsightSerializer()


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
