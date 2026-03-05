from django.utils import timezone
from rest_framework import serializers

from users.models import CustomUser

from .models import (
    CreditBucket,
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

    class Meta:
        model = CreditUsageLog
        fields = [
            "id",
            "wallet",
            "bucket",
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
    total_credits = serializers.IntegerField(source="display_balance")

    class Meta:
        model = CreditWallet
        fields = [
            "id",
            "user",
            "overage_blocks_used",
            # "total_remaining_credits",
            "active_buckets_count",
            "total_credits",
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
