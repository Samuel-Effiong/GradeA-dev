from django.db.models import F, Sum
from django.utils import timezone
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

    def get_total_remaining_credits(self, obj):
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

    # total_remaining_credits = serializers.SerializerMethodField(read_only=True)
    active_buckets_count = serializers.SerializerMethodField(read_only=True)
    display_total_remaining_credits = serializers.IntegerField(source="display_balance")
    total_remaining_credits = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = CreditWallet
        fields = [
            "id",
            "user",
            "overage_blocks_used",
            "active_buckets_count",
            "total_remaining_credits",
            "display_total_remaining_credits",
        ]
        read_only_fields = [
            "id",
            "user",
            "overage_blocks_used",
            # "total_remaining_credits",
            "active_buckets_count",
            "total_credits",
        ]

    def get_total_remaining_credits(self, obj):
        return obj.total_remaining_credits()

    def get_active_buckets_count(self, obj):
        return obj.buckets.filter(expires_at__gt=timezone.now()).count()


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

    def get_block_remaining(self, obj):
        plan = obj.user.subscriptions.filter(is_active=True).first().plan
        return max(0, plan.max_overage_blocks - obj.overage_blocks_used)

    def get_current_overage_balance(self, obj):
        return (
            obj.buckets.filter(bucket_type=CreditBucketType.OVERAGE).aggregate(
                total=Sum(F("total_credits") - F("used_credits"))
            )["total"]
            or 0
        )


class CarryOverHistorySerializer(serializers.ModelSerializer):
    days_until_expiry = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()

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

    def get_days_until_expiry(self, obj):
        if not obj.expires_at:
            return None
        delta = obj.expires_at - timezone.now()
        return max(0, delta.days)

    def get_status(self, obj):
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
    avg_days_to_action = serializers.FloatField()


class BetaCohortStatsSerializer(serializers.Serializer):
    standard_allocation = serializers.IntegerField()
    total_users_analyzed = serializers.IntegerField()
    average_credits_used = serializers.FloatField()
    median_credits_used = serializers.FloatField()
    p90_credits_used = serializers.FloatField()
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
