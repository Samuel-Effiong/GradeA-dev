"""
Serializers for the super admin user LIST only (D7: retrieve, me, patch,
lock, unlock and the sign-in payload keep CustomUserSerializer unchanged).

ListCreditWalletSerializer gives exactly what CreditWalletSerializer gives,
read from what users.user_list.list_queryset() fetched once per page rather
than queried per row (D1, owner 2026-11-11). Every method falls back to the
parent's when that data is absent, so the class is safe anywhere.
users.tests_user_list_payload_pin pins the payload to the base commit's.
"""

from rest_framework import serializers

from billing.models import CONVERSION_FACTOR, CreditBucketType
from billing.serializers import CreditWalletSerializer

from . import user_list
from .serializers import CustomUserSerializer


def _prefetched_buckets(wallet):
    cache = getattr(wallet, "_prefetched_objects_cache", {})
    return cache.get("buckets")


def _is_live(bucket, now):
    return bucket.expires_at is None or bucket.expires_at > now


class ListCreditWalletSerializer(CreditWalletSerializer):
    # The parent's IntegerField(source="display_balance") queries per row.
    display_total_remaining_credits = serializers.SerializerMethodField(  # type: ignore[assignment]
        read_only=True
    )

    def to_representation(self, instance):
        # One clock for the row, as close as possible to the parent's
        # per-method timezone.now() calls.
        from django.utils import timezone

        self._now = timezone.now()
        return super().to_representation(instance)

    # -- buckets ----------------------------------------------------------

    def _live_remaining(self, obj, buckets):
        live = [b for b in buckets if _is_live(b, self._now)]
        return sum(b.total_credits - b.used_credits for b in live)

    def get_total_remaining_credits(self, obj) -> int:
        buckets = _prefetched_buckets(obj)
        if buckets is None:
            return super().get_total_remaining_credits(obj)
        return self._live_remaining(obj, buckets)

    def get_display_total_remaining_credits(self, obj) -> int:
        # The parent's field reads CreditWallet.display_balance:
        # floor(total_remaining_credits() / CONVERSION_FACTOR).
        buckets = _prefetched_buckets(obj)
        if buckets is None:
            return obj.display_balance
        return self._live_remaining(obj, buckets) // CONVERSION_FACTOR

    def get_active_buckets_count(self, obj) -> int:
        buckets = _prefetched_buckets(obj)
        if buckets is None:
            return super().get_active_buckets_count(obj)
        # The parent counts expires_at > now only (a null expiry is not
        # counted there).
        return sum(
            1 for b in buckets if b.expires_at is not None and b.expires_at > self._now
        )

    def get_monthly_credit_remaining(self, obj) -> int:
        buckets = _prefetched_buckets(obj)
        if buckets is None:
            return super().get_monthly_credit_remaining(obj)
        monthly = sorted(
            (
                b
                for b in buckets
                if b.bucket_type == CreditBucketType.MONTHLY
                and b.expires_at is not None
                and b.expires_at > self._now
            ),
            key=lambda b: b.pk,
        )
        if not monthly:
            return 0
        return monthly[0].remaining_credits // CONVERSION_FACTOR

    def get_credit_percentage_remaining(self, obj) -> float:
        buckets = _prefetched_buckets(obj)
        if buckets is None:
            return super().get_credit_percentage_remaining(obj)
        live = [b for b in buckets if _is_live(b, self._now)]
        total_allocated = sum(b.total_credits for b in live)
        if not total_allocated:
            return 0.0
        remaining = self._live_remaining(obj, buckets)
        return round((remaining / total_allocated) * 100, 2)

    def get_bucket_breakdown(self, obj):
        buckets = _prefetched_buckets(obj)
        if buckets is None:
            return super().get_bucket_breakdown(obj)
        included = [
            CreditBucketType.MONTHLY,
            CreditBucketType.CARRY_OVER,
            CreditBucketType.OVERAGE,
            CreditBucketType.TRIAL,
        ]
        queried = [*included, CreditBucketType.MANUAL_GRANT]
        raw = {}
        for b in buckets:
            if b.bucket_type in queried and _is_live(b, self._now):
                raw[b.bucket_type] = raw.get(b.bucket_type, 0) + (
                    b.total_credits - b.used_credits
                )
        breakdown = {bt: 0 for bt in included}
        # The parent floors each type's total, then folds MANUAL_GRANT into
        # OVERAGE.
        for bucket_type, total in raw.items():
            target = (
                CreditBucketType.OVERAGE
                if bucket_type == CreditBucketType.MANUAL_GRANT
                else bucket_type
            )
            breakdown[target] += total // CONVERSION_FACTOR
        return breakdown

    # -- plan -------------------------------------------------------------

    def get_monthly_credit_total(self, obj) -> int:
        user = obj.user
        if not hasattr(user, user_list.ACTIVE_SUBSCRIPTIONS):
            return super().get_monthly_credit_total(obj)
        return _ceiling(user) // CONVERSION_FACTOR

    def get_feature_usage_breakdown(self, obj):
        user = obj.user
        if not hasattr(user, user_list.ACTIVE_SUBSCRIPTIONS) or not all(
            hasattr(user, name) for name in user_list.CORE_FEATURES.values()
        ):
            return super().get_feature_usage_breakdown(obj)
        # The parent's wallet.active_subscription.
        if not getattr(user, user_list.ACTIVE_SUBSCRIPTIONS):
            return None
        return {
            feature: getattr(user, name)
            for feature, name in user_list.CORE_FEATURES.items()
        }


def _ceiling(user):
    """billing.subscription_resolver.get_monthly_credit_ceiling_for_user,
    read from the page's prefetches. Its order: individual, then a managed
    license (admin), then a teacher allocation."""
    subscriptions = getattr(user, user_list.ACTIVE_SUBSCRIPTIONS)
    if subscriptions:
        return subscriptions[0].plan.monthly_credits or 0

    allocations = getattr(user, user_list.ACTIVE_ALLOCATIONS)
    managed = getattr(user, user_list.MANAGED_LICENSES)
    if managed:
        license_sub = managed[0]
        own = [
            a
            for a in allocations
            if a.license_subscription_id == license_sub.pk and a.is_admin_allocation
        ]
        return own[0].monthly_allocation if own else 0

    teaching = sorted(
        (
            a
            for a in allocations
            if not a.is_admin_allocation and a.license_subscription.is_active
        ),
        key=lambda a: a.created_at,
        reverse=True,
    )
    if teaching:
        return teaching[0].monthly_allocation or 0
    return 0


class UserListSerializer(CustomUserSerializer):
    """The list row: the existing payload plus nine read-only values (D6:
    API keys, not user-facing text). Super admin only, as the list is."""

    credit_wallet = ListCreditWalletSerializer(read_only=True)
    last_login = serializers.DateTimeField(read_only=True)
    activated_at = serializers.DateTimeField(read_only=True)
    plan_tier = serializers.CharField(read_only=True, allow_null=True)
    plan_category = serializers.CharField(read_only=True, allow_null=True)
    plan_interval = serializers.CharField(read_only=True, allow_null=True)
    assignments_created = serializers.IntegerField(read_only=True)
    papers_graded = serializers.IntegerField(read_only=True)
    credits_left = serializers.IntegerField(read_only=True, allow_null=True)
    credits_used = serializers.IntegerField(read_only=True)
    # Brief 4: the signals (billing.intent_signals), teachers only.
    signal_count = serializers.IntegerField(read_only=True)
    at_80_percent = serializers.BooleanField(read_only=True)
    frequent_user = serializers.BooleanField(read_only=True)
    active_last_week = serializers.BooleanField(read_only=True)
    is_power_grader = serializers.BooleanField(read_only=True)

    class Meta(CustomUserSerializer.Meta):
        fields = [
            *CustomUserSerializer.Meta.fields,
            "last_login",
            "activated_at",
            "plan_tier",
            "plan_category",
            "plan_interval",
            "assignments_created",
            "papers_graded",
            "credits_left",
            "credits_used",
            "signal_count",
            "at_80_percent",
            "frequent_user",
            "active_last_week",
            "is_power_grader",
        ]
