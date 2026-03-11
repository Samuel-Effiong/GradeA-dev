from datetime import timedelta

from django.db.models import F, Q, Sum
from django.db.models.aggregates import Avg, Count
from django.db.models.functions import ExtractHour, TruncDay, TruncWeek
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from django.views.decorators.vary import vary_on_headers
from django_filters.rest_framework import DjangoFilterBackend

# from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from classrooms.permissions import IsNotStudent, IsSuperAdmin, IsTeacher
from users.models import UserTypes

from .models import (
    BetaProfile,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditUsageLog,
    CreditWallet,
    SubscriptionPlan,
    UserSubscription,
)
from .serializers import (  # SubscriptionSerializer,
    BetaCohortStatsSerializer,
    BetaFeatureMixSerializer,
    BetaSummarySerializer,
    BetaUsageTrendSerializer,
    CarryOverHistorySerializer,
    ConversionLeadSerializer,
    CreditBucketSerializer,
    CreditLedgerSerializer,
    CreditUsageLogSerializer,
    CreditWalletSerializer,
    CreditWalletSummarySerializer,
    OverageStatusSerializer,
    SubscriptionPlanSerializer,
    UsageSummarySerializer,
    UserSubscriptionSerializer,
)
from .services import AnalyticsService, SubscriptionService

# from rest_framework.generics import GenericAPIView


@extend_schema_view(
    list=extend_schema(
        tags=["Subscription Plans"],
        summary="List all subscription plans",
        description="Retrieve a list of all available subscription plans, "
        "including their credit allocations and pricing.",
    ),
    retrieve=extend_schema(
        tags=["Subscription Plans"],
        summary="Retrieve a subscription plan",
        description="Retrieve detailed information about a specific subscription plan by its ID.",
    ),
    create=extend_schema(
        tags=["Subscription Plans"],
        summary="Create a new subscription plan",
        description="Create a new subscription plan. This action is restricted to super administrators.",
    ),
    update=extend_schema(
        tags=["Subscription Plans"],
        summary="Update a subscription plan",
        description="Update an existing subscription plan. This action is restricted to super administrators.",
    ),
    partial_update=extend_schema(
        tags=["Subscription Plans"],
        summary="Partially update a subscription plan",
        description="Partially update an existing subscription plan. This action is "
        "restricted to super administrators.",
    ),
    destroy=extend_schema(
        tags=["Subscription Plans"],
        summary="Delete a subscription plan",
        description="Delete a subscription plan. This action is restricted to super administrators.",
    ),
)
class SubscriptionPlanViewSet(viewsets.ModelViewSet):
    """
    Viewset for viewing and managing subscription plans.
    - LIST / RETRIEVE: Accessible by any authenticated user.
    - CREATE / UPDATE / DELETE: Restricted to Super Admins.
    """

    queryset = SubscriptionPlan.objects.all()
    serializer_class = SubscriptionPlanSerializer
    permission_classes = [IsAuthenticated, IsNotStudent]
    http_method_names = ["get", "head", "post", "patch", "delete", "options"]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        if self.action in ["list", "retrieve"]:
            permission_classes = [IsAuthenticated]
        else:
            permission_classes = [IsAuthenticated, IsNotStudent]
        return [permission() for permission in permission_classes]


@extend_schema_view(
    list=extend_schema(
        tags=["User Subscriptions"],
        summary="List user subscriptions",
        description="Retrieve a list of user subscriptions. Regular users see only their own, "
        "while superadmins see all.",
    ),
    retrieve=extend_schema(
        tags=["User Subscriptions"],
        summary="Retrieve a user subscription",
        description="Retrieve detailed information about a specific user subscription by its ID.",
    ),
    create=extend_schema(
        tags=["User Subscriptions"],
        summary="Create a new user subscription",
        description="Create a new user subscription. This action is restricted to super administrators.",
    ),
    update=extend_schema(
        tags=["User Subscriptions"],
        summary="Update a user subscription",
        description="Update an existing user subscription. This action is restricted to super administrators.",
    ),
    partial_update=extend_schema(
        tags=["User Subscriptions"],
        summary="Partially update a user subscription",
        description="Partially update an existing user subscription. This action is restricted "
        "to super administrators.",
    ),
    destroy=extend_schema(
        tags=["User Subscriptions"],
        summary="Delete a user subscription",
        description="Delete a user subscription. This action is restricted to super administrators.",
    ),
)
class UserSubscriptionViewSet(viewsets.ModelViewSet):
    """
    Viewset for viewing and managing user subscriptions.
    - LIST / RETRIEVE: Users see their own; Super Admins see all.
    - CREATE / UPDATE / DELETE: Restricted to Super Admins.
    """

    queryset = UserSubscription.objects.all()
    serializer_class = UserSubscriptionSerializer
    permission_classes = [IsAuthenticated, IsNotStudent]
    http_method_names = ["get", "head", "post", "patch", "delete", "options"]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["user", "plan", "is_active"]
    search_fields = ["user__email", "plan__name"]
    ordering_fields = ["created_at", "billing_cycle_end"]

    def get_queryset(self):
        """
        Filters the queryset based on the user's role.
        """
        queryset = super().get_queryset()
        if not (
            self.request.user.is_superuser
            and self.request.user.user_type == UserTypes.SUPER_ADMIN
        ):
            queryset = queryset.filter(user=self.request.user)
        return queryset

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        if self.action in ["list", "retrieve"]:
            permission_classes = [IsAuthenticated]
        elif self.action == "create":
            # Allows users to subscribe as long as they are not students
            permission_classes = [IsAuthenticated, IsTeacher]
        else:
            permission_classes = [IsAuthenticated, IsSuperAdmin]
        return [permission() for permission in permission_classes]


@extend_schema_view(
    list=extend_schema(
        tags=["Credit Wallets"],
        summary="List credit wallets",
        description="Retrieve a list of credit wallets. Regular users see only their own wallet.",
    ),
    retrieve=extend_schema(
        tags=["Credit Wallets"],
        summary="Retrieve a credit wallet",
        description="Retrieve detailed information about a specific credit wallet by its ID.",
    ),
    create=extend_schema(
        tags=["Credit Wallets"],
        summary="Create a new credit wallet",
        description="Create a new credit wallet. This action is restricted to super administrators.",
    ),
    update=extend_schema(
        tags=["Credit Wallets"],
        summary="Update a credit wallet",
        description="Update an existing credit wallet. This action is restricted to super administrators.",
    ),
    partial_update=extend_schema(
        tags=["Credit Wallets"],
        summary="Partially update a credit wallet",
        description="Partially update an existing credit wallet. This action is restricted to super administrators.",
    ),
    destroy=extend_schema(
        tags=["Credit Wallets"],
        summary="Delete a credit wallet",
        description="Delete a credit wallet. This action is restricted to super administrators.",
    ),
)
class CreditWalletViewSet(viewsets.ModelViewSet):
    """
    Viewset for viewing credit wallets.
    Write operations are not exposed via API as wallets are system-managed.
    """

    queryset = CreditWallet.objects.all()
    serializer_class = CreditWalletSerializer
    permission_classes = [IsAuthenticated, IsNotStudent]
    http_method_names = ["get", "head", "post", "patch", "delete", "options"]

    filter_backends = [DjangoFilterBackend, OrderingFilter]
    ordering_fields = ["created_at", "updated_at"]

    def get_queryset(self):
        queryset = super().get_queryset()
        if not (
            self.request.user.is_superuser
            and self.request.user.user_type == UserTypes.SUPER_ADMIN
        ):
            queryset = queryset.filter(user=self.request.user)
        return queryset


@extend_schema_view(
    list=extend_schema(
        tags=["Credit Buckets"],
        summary="List credit buckets",
        description="Retrieve a list of credit buckets associated with the user's wallet.",
    ),
    retrieve=extend_schema(
        tags=["Credit Buckets"],
        summary="Retrieve a credit bucket",
        description="Retrieve detailed information about a specific credit bucket.",
    ),
    create=extend_schema(
        tags=["Credit Buckets"],
        summary="Create a new credit bucket",
        description="Create a new credit bucket. This action is restricted to super administrators.",
    ),
    update=extend_schema(
        tags=["Credit Buckets"],
        summary="Update a credit bucket",
        description="Update an existing credit bucket. This action is restricted to super administrators.",
    ),
    partial_update=extend_schema(
        tags=["Credit Buckets"],
        summary="Partially update a credit bucket",
        description="Partially update an existing credit bucket. This action is restricted to super administrators.",
    ),
    destroy=extend_schema(
        tags=["Credit Buckets"],
        summary="Delete a credit bucket",
        description="Delete a credit bucket. This action is restricted to super administrators.",
    ),
)
class CreditBucketViewSet(viewsets.ModelViewSet):
    """
    Viewset for viewing credit buckets.
    Buckets track specific pools of credits (Monthly, Carry Over, Overage).
    """

    queryset = CreditBucket.objects.all()
    serializer_class = CreditBucketSerializer
    permission_classes = [IsAuthenticated, IsNotStudent]
    http_method_names = ["get", "head", "post", "patch", "delete", "options"]

    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["bucket_type", "wallet"]
    ordering_fields = ["expires_at", "created_at"]

    def get_queryset(self):
        queryset = super().get_queryset()
        if not (
            self.request.user.is_superuser
            and self.request.user.user_type == UserTypes.SUPER_ADMIN
        ):
            queryset = queryset.filter(wallet__user=self.request.user)
        return queryset


@extend_schema_view(
    list=extend_schema(
        tags=["Credit Ledgers"],
        summary="List credit ledger entries",
        description="Retrieve the audit trail of all credit transactions for the user.",
    ),
    retrieve=extend_schema(
        tags=["Credit Ledgers"],
        summary="Retrieve a credit ledger entry",
        description="Retrieve a specific ledger entry by its ID.",
    ),
    create=extend_schema(
        tags=["Credit Ledgers"],
        summary="Create a new credit ledger entry",
        description="Create a new credit ledger entry. This action is restricted "
        "to super administrators.",
    ),
    update=extend_schema(
        tags=["Credit Ledgers"],
        summary="Update a credit ledger entry",
        description="Update an existing credit ledger entry. This action is restricted "
        "to super administrators.",
    ),
    partial_update=extend_schema(
        tags=["Credit Ledgers"],
        summary="Partially update a credit ledger entry",
        description="Partially update an existing credit ledger entry. This action is "
        "restricted to super administrators.",
    ),
    destroy=extend_schema(
        tags=["Credit Ledgers"],
        summary="Delete a credit ledger entry",
        description="Delete a credit ledger entry. This action is restricted to "
        "super administrators.",
    ),
)
class CreditLedgerViewSet(viewsets.ModelViewSet):
    """
    Viewset for viewing credit ledger entries.
    Provides an immutable audit trail of all grants, consumptions, and expirations.
    """

    queryset = CreditLedger.objects.all()
    serializer_class = CreditLedgerSerializer
    permission_classes = [IsAuthenticated, IsNotStudent]
    http_method_names = ["get", "head", "post", "patch", "delete", "options"]

    filter_backends = [DjangoFilterBackend, OrderingFilter, SearchFilter]
    filterset_fields = ["ledger_type", "bucket"]
    search_fields = ["reference"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        queryset = super().get_queryset()
        if not (
            self.request.user.is_superuser
            and self.request.user.user_type == UserTypes.SUPER_ADMIN
        ):
            queryset = queryset.filter(user=self.request.user)
        return queryset


@extend_schema_view(
    list=extend_schema(
        tags=["Credit Usage Logs"],
        summary="List credit usage logs",
        description="Retrieve detailed logs of how credits were consumed for specific tasks.",
    ),
    retrieve=extend_schema(
        tags=["Credit Usage Logs"],
        summary="Retrieve a credit usage log",
        description="Retrieve a specific usage log by its ID.",
    ),
)
class CreditUsageLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Viewset for viewing credit usage logs.
    Tracks feature-level consumption for precise usage analytics.
    """

    queryset = CreditUsageLog.objects.all()
    serializer_class = CreditUsageLogSerializer
    permission_classes = [IsAuthenticated, IsNotStudent]
    http_method_names = ["get", "head", "post", "patch", "delete", "options"]

    filter_backends = [DjangoFilterBackend, OrderingFilter, SearchFilter]
    filterset_fields = ["wallet", "bucket", "feature", "task_type"]
    search_fields = ["task_id", "feature"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        queryset = super().get_queryset()
        if not (
            self.request.user.is_superuser
            and self.request.user.user_type == UserTypes.SUPER_ADMIN
        ):
            queryset = queryset.filter(wallet__user=self.request.user)
        return queryset


class SubscriptionManagementViewSet(viewsets.GenericViewSet):
    """
    Viewset for managing user subscriptions.
    """

    queryset = UserSubscription.objects.all()
    serializer_class = UserSubscriptionSerializer
    permission_classes = [IsAuthenticated, IsNotStudent]
    http_method_names = ["get", "head", "post", "patch", "delete", "options"]

    def get_queryset(self):
        return UserSubscription.objects.filter(user=self.request.user, is_active=True)

    @extend_schema(
        tags=["Subscription"],
        summary="Get my subscription",
        description="Get my subscription.",
        responses={
            200: OpenApiResponse(response=UserSubscriptionSerializer),
            404: OpenApiResponse(
                description="No active subscription found",
                examples=[
                    OpenApiExample(
                        name="No active subscription found",
                        value={
                            "status": "inactive",
                            "message": "No active subscription found",
                        },
                    )
                ],
            ),
        },
    )
    @action(detail=False, methods=["get"], url_path="me")
    def get_my_subscription(self, request, *args, **kwargs):
        subscription = self.get_queryset().first()
        if not subscription:
            return Response(
                {"detail": "No active subscription found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        serializer = self.get_serializer(subscription)
        return Response(serializer.data)

    @extend_schema(
        tags=["Subscription"],
        summary="Create a new subscription",
        description="Create a new subscription for the user.",
        request=UserSubscriptionSerializer,
        responses={
            201: OpenApiResponse(
                response=UserSubscriptionSerializer,
                description="Subscription created successfully",
            ),
            400: OpenApiResponse(
                description="Invalid input. Missing required fields or invalid data format"
            ),
        },
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        serializer.save()

        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["Subscription"],
        summary="Update a subscription",
        description="Update an existing subscription for the user.",
        responses={200: OpenApiResponse(response=SubscriptionPlanSerializer)},
    )
    @action(detail=False, methods=["get"], url_path="plan", url_name="plan")
    def plan(self, request, *args, **kwargs):
        plans = SubscriptionPlan.objects.all()
        serializer = SubscriptionPlanSerializer(plans, many=True)
        return Response(serializer.data)

    @extend_schema(
        exclude=True,
        tags=["Subscription"],
        summary="Get subscription status",
        description="Get subscription status for the user.",
        responses={
            200: OpenApiResponse(response=UserSubscriptionSerializer),
            404: OpenApiResponse(
                description="No active subscription found",
                examples=[
                    OpenApiExample(
                        name="No active subscription found",
                        value={
                            "status": "inactive",
                            "message": "No active subscription found",
                        },
                    )
                ],
            ),
        },
    )
    @action(detail=False, methods=["get"], url_path="status", url_name="status")
    def status(self, request, *args, **kwargs):
        user_subscription = self.get_queryset().first()
        if not user_subscription:
            return Response(
                {"status": "inactive", "message": "No active subscription found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = self.get_serializer(user_subscription)
        return Response(serializer.data)

    @extend_schema(
        tags=["Subscription"],
        summary="Cancel a subscription",
        description="Cancel an existing subscription for the user.",
        responses={
            200: OpenApiResponse(
                description="Subscription cancelled successfully",
                examples=[
                    OpenApiExample(
                        name="Subscription cancelled successfully",
                        value={
                            "status": "cancelled",
                            "message": "Subscription will not renew at the end of the current billing cycle",
                        },
                    )
                ],
            ),
            404: OpenApiResponse(
                description="No active subscription found to cancel",
                examples=[
                    OpenApiExample(
                        name="No active subscription found to cancel",
                        value={
                            "status": "inactive",
                            "message": "No active subscription found to cancel",
                        },
                    )
                ],
            ),
        },
    )
    @action(detail=False, methods=["POST"])
    def cancel(self, request, *args, **kwargs):
        user_subscription = self.get_queryset().first()
        if not user_subscription:
            return Response(
                {
                    "status": "inactive",
                    "message": "No active subscription found to cancel",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        user_subscription.auto_renew = False
        user_subscription.save(updated_fields=["auto_renew", "updated_at"])

        return Response(
            {
                "status": "cancelled",
                "message": "Subscription will not renew at the end of the current billing cycle",
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Subscription"],
        summary="Upgrade a subscription",
        description="Upgrade an existing subscription for the user.",
        request=UserSubscriptionSerializer,
        responses={
            200: OpenApiResponse(response=UserSubscriptionSerializer),
            404: OpenApiResponse(
                description="No active subscription found to upgrade",
                examples=[
                    OpenApiExample(
                        name="No active subscription found to upgrade",
                        value={
                            "status": "inactive",
                            "message": "No active subscription found to upgrade",
                        },
                    )
                ],
            ),
        },
    )
    @action(detail=False, methods=["POST"])
    def upgrade(self, request, *args, **kwargs):
        plan_id = request.data.get("plan")

        try:
            new_plan = SubscriptionPlan.objects.get(id=plan_id)
        except SubscriptionPlan.DoesNotExist:
            return Response(
                {"status": "error", "message": "Plan not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Upgrade is usually immediate. We call our Service Layer
        new_sub = SubscriptionService.activate_subscription(request.user, new_plan)

        serializer = self.get_serializer(new_sub)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["Subscription"],
        summary="Downgrade a subscription",
        description="Downgrade an existing subscription for the user.",
        responses={
            200: OpenApiResponse(
                description="Downgrade scheduled successfully",
                examples=[
                    OpenApiExample(
                        name="Downgrade scheduled successfully",
                        value={
                            "status": "scheduled",
                            "message": "Downgrade scheduled for the end of the current billing cycle",
                        },
                    )
                ],
            ),
            404: OpenApiResponse(
                description="No active subscription found to downgrade",
                examples=[
                    OpenApiExample(
                        name="No active subscription found to downgrade",
                        value={
                            "status": "inactive",
                            "message": "No active subscription found to downgrade",
                        },
                    )
                ],
            ),
        },
    )
    @action(detail=False, methods=["POST"])
    def downgrade(self, request, *args, **kwargs):
        plan_id = request.data.get("plan_id")

        try:
            new_plan = SubscriptionPlan.objects.get(id=plan_id)
        except SubscriptionPlan.DoesNotExist:
            return Response(
                {"status": "error", "message": "Plan not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Downgrade is scheduled for the end of the cycle
        SubscriptionService.schedule_downgrade(request.user, new_plan)

        return Response(
            {
                "status": "scheduled",
                "message": "Downgrade scheduled for the end of the current billing cycle",
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["Subscription"],
        summary="Get subscription history",
        description="Get subscription history for the user.",
        responses={
            200: OpenApiResponse(response=UserSubscriptionSerializer),
            404: OpenApiResponse(
                description="No subscription history found",
                examples=[
                    OpenApiExample(
                        name="No subscription history found",
                        value={
                            "status": "inactive",
                            "message": "No subscription history found",
                        },
                    )
                ],
            ),
        },
    )
    @action(detail=False, methods=["GET"])
    def history(self, request, *args, **kwargs):
        """
        Query Params:
        - status: 'active' or 'inactive'
        - from_date: 'YYYY-MM-DD'
        - to_date: 'YYYY-MM-DD'
        """
        user = request.user
        queryset = UserSubscription.objects.filter(user=user).order_by("-created_at")

        status = request.query_params.get("status")
        from_date = request.query_params.get("from_date")
        to_date = request.query_params.get("to_date")

        if status:
            if status == "active":
                queryset = queryset.filter(is_active=True)
            elif status == "inactive":
                queryset = queryset.filter(is_active=False)

        if from_date:
            queryset = queryset.filter(created_at__gte=parse_datetime(from_date))

        if to_date:
            queryset = queryset.filter(created_at__lte=parse_datetime(to_date))

        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        tags=["Subscription"],
        summary="Get credit wallet summary",
        description="Get credit wallet summary for the user.",
        responses={
            200: OpenApiResponse(response=CreditWalletSummarySerializer),
            404: OpenApiResponse(
                description="No wallet found",
                examples=[
                    OpenApiExample(
                        name="No wallet found",
                        value={"status": "inactive", "message": "No wallet found"},
                    )
                ],
            ),
        },
    )
    @action(
        detail=False,
        methods=["GET"],
        url_name="credit-wallet",
        url_path="credits/wallet",
    )
    def wallet(self, request, *args, **kwargs):
        wallet = request.user.credit_wallet

        # wallet_total_remaining_credits = wallet.total_remaining_credits
        wallet.active_buckets_count = wallet.buckets.filter(
            expires_at__gt=timezone.now()
        ).count()

        serializer = CreditWalletSummarySerializer(wallet)
        return Response(serializer.data)

    @extend_schema(
        tags=["Subscription"],
        summary="Get subscription credit summary",
        description="Get subscription summary for the user.",
        responses={
            200: OpenApiResponse(response=UsageSummarySerializer),
            404: OpenApiResponse(
                description="No subscription found",
                examples=[
                    OpenApiExample(
                        name="No subscription found",
                        value={
                            "status": "inactive",
                            "message": "No subscription found",
                        },
                    )
                ],
            ),
        },
    )
    @action(
        detail=False,
        methods=["GET"],
        url_name="credit-summary",
        url_path="credits/summary",
    )
    def summary(self, request, *args, **kwargs):
        user = request.user
        wallet = user.credit_wallet

        # 1. Identify the current billing window
        subscription = self.get_queryset().first()
        if not subscription:
            return Response(
                {"status": "inactive", "message": "No subscription found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        start = subscription.billing_cycle_start
        end = subscription.billing_cycle_end

        # 2 Aggregate logs within this window
        logs = CreditUsageLog.objects.filter(
            wallet=wallet, created_at__range=[start, end]
        )

        total_consumed = logs.aggregate(total=Sum("amount"))["total"] or 0

        # 3 Break down by feature (e.g.)
        by_feature = logs.values("feature").annotate(total=Sum("amount"))
        feature_map = {item["feature"]: item["total"] for item in by_feature}

        # 4 Break down by bucket type (.e.g., "MONTHLY", "CARRY_OVER", "OVERAGE")

        by_bucket_type = logs.values("bucket__bucket_type").annotate(
            total=Sum("amount")
        )
        bucket_type_map = {
            item["bucket__bucket_type"]: item["total"] for item in by_bucket_type
        }

        data = {
            "billing_cycle_start": start,
            "billing_cycle_end": end,
            "total_consumed": total_consumed,
            "consumed_by_feature": feature_map,
            "consumed_by_bucket_type": bucket_type_map,
        }

        serializer = UsageSummarySerializer(data)
        return Response(serializer.data)

    @extend_schema(
        tags=["Subscription"],
        summary="Get credit ledger",
        description="Get credit ledger for the user.",
        responses={
            200: OpenApiResponse(response=CreditLedgerSerializer),
        },
    )
    @action(
        detail=False,
        methods=["GET"],
        url_name="credit-ledger",
        url_path="credits/ledger",
    )
    def credit_ledger(self, request, *args, **kwargs):
        queryset = CreditLedger.objects.filter(user=request.user).order_by(
            "-created_at"
        )

        serializer = CreditLedgerSerializer(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        tags=["Subscription"],
        summary="Get credit usage logs",
        description="Get credit usage logs for the user.",
        responses={
            200: OpenApiResponse(response=CreditUsageLogSerializer),
        },
    )
    @action(
        detail=False,
        methods=["GET"],
        url_name="credit-usage-logs",
        url_path="credits/usage-logs",
    )
    def credit_usage_logs(self, request, *args, **kwargs):
        user = request.user
        wallet = user.credit_wallet
        logs = wallet.credit_usage_logs.all()
        serializer = CreditUsageLogSerializer(logs, many=True)
        return Response(serializer.data)

    @extend_schema(
        tags=["Subscription"],
        summary="Credit bucket usage",
        description="Get credit bucket usage for the user.",
        responses={
            200: OpenApiResponse(response=CreditBucketSerializer),
        },
    )
    @action(
        detail=False,
        methods=["GET"],
        url_name="credit-buckets",
        url_path="credits/buckets",
    )
    def credit_bucket(self, request, *args, **kwargs):
        queryset = CreditBucket.objects.filter(wallet__user=request.user).order_by(
            "expires_at", "-created_at"
        )
        serializer = CreditBucketSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(
        detail=False,
        methods=["GET"],
        url_name="credit-overage-status",
        url_path="credits/overage",
    )
    def credit_overage_status(self, request, *args, **kwargs):
        wallet = request.user.credit_wallet
        serializer = OverageStatusSerializer(wallet)

        return Response(serializer.data)

    @extend_schema(
        tags=["Subscription"],
        summary="Get credit carry-over history",
        description="Get credit carry-over history for the user.",
        responses={
            200: OpenApiResponse(response=CarryOverHistorySerializer),
        },
    )
    @action(
        detail=False,
        methods=["GET"],
        url_name="credit-carry-over-history",
        url_path="credits/carry-over",
    )
    def carry_over_history(self, request, *args, **kwargs):
        queryset = CreditBucket.objects.filter(
            wallet__user=request.user, bucket_type=CreditBucketType.CARRY_OVER
        ).order_by("-created_at")

        serializer = CarryOverHistorySerializer(queryset, many=True)
        return Response(serializer.data)


class BetaAnalyticViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = BetaProfile.objects.all()
    permission_classes = [IsSuperAdmin]

    @extend_schema(
        tags=["Beta Analytics"],
        summary="Log analytics dashboard view",
        description="Records when a user views or interacts with the analytics dashboard. "
        "This tracking data is used to measure engagement with analytics features "
        "and identify power users for conversion targeting.",
        responses={
            204: OpenApiResponse(
                description="View successfully logged. No content returned."
            ),
        },
    )
    @action(detail=False, methods=["GET"], url_path="log-view")
    def log_view(self, request, *args, **kwargs):
        """
        Endpoint for the frontend to report an when the user is view the
        analytic page or interacting with the dashboards
        """

        AnalyticsService.track_analytics_view(request.user)
        return Response({}, status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        tags=["Beta Analytics"],
        summary="Get beta program summary statistics",
        description="Retrieve high-level summary metrics for the entire beta cohort, including "
        "total users, recent activity rates, average credit consumption, capacity "
        "utilization, and onboarding speed. Cached for 15 minutes to optimize performance.",
        responses={
            200: OpenApiResponse(
                response=BetaSummarySerializer,
                description="Beta program summary statistics",
                examples=[
                    OpenApiExample(
                        name="Beta Summary Example",
                        value={
                            "total_beta_users": 1250,
                            "active_last_7_days_percent": 68.4,
                            "avg_credits_used": 3250000,
                            "percent_users_at_cap": 12.8,
                            "avg_days_to_first_action": 2.3,
                        },
                        description="Typical beta program summary showing healthy engagement",
                    )
                ],
            ),
        },
    )
    @method_decorator(cache_page(60 * 15, key_prefix="beta:summary"))
    @method_decorator(vary_on_headers("Authorization"))
    @action(detail=False, methods=["GET"], url_path="beta/summary")
    def summary(self, request, *args, **kwargs):
        now = timezone.now()
        seven_days_ago = now - timedelta(days=7)

        stats = self.get_queryset().aggregate(
            total=Count("id"),
            active_recent=Count("id", filter=Q(last_active_at__gte=seven_days_ago)),
            avg_usage=Avg("total_credits_used"),
            hit_cap=Count("id", filter=Q(has_hit_cap=True)),
            avg_days_to_action=Avg("days_to_first_action"),
        )

        total = stats["total"] or 1

        data = {
            "total_beta_users": total,
            "active_last_7_days_percent": round(
                (stats["active_recent"] / total) * 100, 2
            ),
            "avg_credits_used": round(stats["avg_usage"] or 0, 0),
            "percent_users_at_cap": round((stats["hit_cap"] / total) * 100, 2),
            "avg_days_to_first_action": round(stats["avg_days_to_action"] or 0, 1),
        }

        serializer = BetaSummarySerializer(data)
        return Response(serializer.data)

    @extend_schema(
        tags=["Beta Analytics"],
        summary="Get beta cohort statistical breakdown",
        description="Provides detailed statistical analysis of the 10 Million Token for the beta cohort, "
        "including median usage, 90th percentile consumption, average credits used, "
        "time-to-cap metrics, and unused credit percentages. This data directly informs "
        "pricing tier design for the August commercial launch.",
        responses={
            200: OpenApiResponse(
                response=BetaCohortStatsSerializer,
                description="Statistical breakdown of beta cohort usage patterns",
                examples=[
                    OpenApiExample(
                        name="Cohort Statistics Example",
                        value={
                            "standard_allocation": 10_000_000,
                            "total_users_analyzed": 1250,
                            "average_credit_used": 3250000,
                            "median_credit_used": 1800000,
                            "p90_credit_used": 7500000,
                            "average_days_to_cap": 45.3,
                            "percent_unused_credits": 67.5,
                        },
                        description="Example showing typical beta cohort distribution",
                    )
                ],
            ),
            204: OpenApiResponse(
                description="No beta usage data recorded yet",
                examples=[
                    OpenApiExample(
                        name="No Data Example",
                        value={"message": "No beta usage data recorded yet"},
                    )
                ],
            ),
        },
    )
    @action(detail=False, methods=["GET"], url_path="beta/cohort-stats")
    def cohort_stats(self, request, *args, **kwargs):
        """
        Statistical breakdown of 10 Million / 10k AI Credit Beta Cohort
        Used to determine priciing tiers for the August launch
        """
        # 1. Fetch all usage values in ascending oder for percentile math
        # We only pull the specific field to keep memory usage low
        usage_values = (
            self.get_queryset()
            .values_list("total_credits_used", flat=True)
            .order_by("total_credits_used")
        )

        count = usage_values.count()
        if count == 0:
            return Response(
                {
                    "message": "No beta usage data recorded yet",
                },
                status=status.HTTP_204_NO_CONTENT,
            )

        # 2. Statistical Calculations
        # Median (50th Percentile) and P90 (90th Percentile)
        median_credits = usage_values[int(count * 0.5)]
        p90_credits = usage_values[int(count * 0.9)]

        # Aggregates for Average and Time-to-Cap
        # We filter for users who have actually used credits to get an accurate 'Time-to-cap'
        aggregates = self.get_queryset().aggregate(
            avg_used=Avg("total_credits_used"),
            avg_days_to_cap=Avg(
                F("last_active_at__date") - F("joined_beta_at__date"),
                filter=Q(has_hit_cap=True),
            ),
        )

        avg_used = aggregates["avg_used"] or 0
        standard_allocation = 10_000_000

        # Calculate the percentage of total granted credits that remain unspent
        unused_credits_pct = max(
            0, ((standard_allocation - avg_used) / standard_allocation) * 100
        )

        data = {
            "standard_allocation": standard_allocation,
            "total_users_analyzed": count,
            "average_credit_used": round(avg_used, 0),
            "median_credit_used": median_credits,
            "p90_credit_used": p90_credits,
            "average_days_to_cap": round(aggregates["avg_days_to_cap"].days, 1),
            "percent_unused_credits": round(unused_credits_pct, 2),
        }

        serializer = BetaCohortStatsSerializer(data)
        return Response(serializer.data)

    @extend_schema(
        tags=["Beta Analytics"],
        summary="Analyze feature usage distribution",
        description="Analyzes how teachers allocate their credit budget across different features "
        "(Grading vs Creation vs Other). Calculates feedback depth (tokens per grading session), "
        "analytics engagement, and identifies the primary value driver. This data determines "
        "which features should be positioned as 'Core' versus 'Secondary' in the product offering.",
        responses={
            200: OpenApiResponse(
                response=BetaFeatureMixSerializer,
                description="Feature usage distribution and engagement quality metrics",
                examples=[
                    OpenApiExample(
                        name="Feature Mix Example",
                        value={
                            "grading_percent": 65.3,
                            "creation_percent": 28.7,
                            "other_percent": 6.0,
                            "average_feedback_depth_token": 1850,
                            "total_analytics_views": 3420,
                            "views_per_user": 2.7,
                            "primary_driver": "GRADING",
                            "engagement_quality": "HIGH",
                        },
                        description="Example showing grading-heavy usage with high engagement",
                    )
                ],
            ),
        },
    )
    @action(detail=False, methods=["GET"], url_path="beta/feature-mix")
    def feature_mix(self, request, *args, **kwargs):
        """
        Analyses how teachers are allocating their credit budget.
        Used to determine which features are 'Core' vs 'Secondary'.
        """

        # 1. Aggregate global totals across the entire cohort
        mix_stats = self.get_queryset().aggregate(
            total_spent=Sum("total_credits_used"),
            total_grading=Sum("credits_used_grading"),
            total_creation=Sum("credits_used_creation"),
            total_views=Sum("analytic_view_count"),
            # To calculate dept, we need to know how many actual tasks were run
            # We fetch this count from the related CreditUsageLog
            total_grading_events=Count(
                "user__credit_wallet__credit_usage_logs",
                filter=Q(
                    user_credit_wallet__credit_usage_logs__feature="Grading Assignment"
                ),
            ),
        )

        total_spent = mix_stats["total_spent"] or 1
        total_grading = mix_stats["total_grading"] or 0
        total_creation = mix_stats["total_creation"] or 0

        # 2. Calculate Feedback Depth (Tokens per Grading Task)
        # Higher tokens per grading session = Higher perceived value / depth
        grading_events = mix_stats["total_grading_events"] or 1
        avg_feedback_depth = total_grading / grading_events

        data = {
            "grading_percent": round((total_grading / total_spent) * 100, 2),
            "creation_percent": round((total_creation / total_spent) * 100, 2),
            "other_percent": round(
                ((total_spent - total_grading - total_creation) / total_spent) * 100, 2
            ),
            "average_feedback_depth_token": round(avg_feedback_depth, 0),
            "total_analytics_views": mix_stats["total_views"],
            "views_per_user": round(
                mix_stats["total_views"] / (self.get_queryset().count() or 1), 1
            ),
            "primary_driver": (
                "GRADING" if total_grading > total_creation else "CREATION"
            ),
            "engagement_quality": "HIGH" if avg_feedback_depth > 1500 else "LOW",
        }

        serializer = BetaFeatureMixSerializer(data)
        return Response(serializer.data)

    @extend_schema(
        tags=["Beta Analytics"],
        summary="Analyze temporal usage patterns",
        description="Analyzes credit consumption over time to identify usage patterns and predict "
        "server load. Returns daily time series (last 30 days), hourly distribution (0-23), "
        "and weekly growth trends (last 12 weeks). Infrastructure insights include peak usage "
        "hours and current velocity metrics for capacity planning.",
        responses={
            200: OpenApiResponse(
                response=BetaUsageTrendSerializer,
                description="Temporal usage patterns and infrastructure insights",
                examples=[
                    OpenApiExample(
                        name="Usage Trends Example",
                        value={
                            "daily_time_series": [
                                {"date": "2024-01-15", "credits": 12500000},
                                {"date": "2024-01-16", "credits": 14200000},
                            ],
                            "peak_usage_hours": [
                                {"hour_24h": 14, "total_credits": 45000000},
                                {"hour_24h": 15, "total_credits": 52000000},
                            ],
                            "weekly_growth": [
                                {"week_start": "2024-01-08", "total_credits": 85000000},
                                {"week_start": "2024-01-15", "total_credits": 92000000},
                            ],
                            "infrastructure_insight": {
                                "peak_hour": 15,
                                "current_week_velocity": 92000000,
                            },
                        },
                        description="Example showing peak afternoon usage and growing weekly velocity",
                    )
                ],
            ),
        },
    )
    @action(detail=False, methods=["GET"], url_path="beta/usage-trends")
    def usage_trends(self, request, *args, **kwargs):
        """
        Analyze temporal usage patterns to predict server load.
        Identifies 'Peak Hours' and 'Weekly Rhythms'
        """

        # 1. Credits Used Per Day (Last 30 Days)
        daily_usage = (
            CreditUsageLog.objects.annotate(day=TruncDay("created_at"))
            .values("day")
            .annotate(total=Sum("amount"))
            .order_by("day")[:30]
        )

        # 2. Peak Usage Hours (0 - 23)
        # Identifies when the AI 'Engine' are under the most stress
        hourly_distribution = (
            CreditUsageLog.objects.annotate(hour=ExtractHour("created_at"))
            .values("hour")
            .annotate(total=Sum("amount"))
            .order_by("hour")
        )

        # 3. Week-by-Week Trends
        # Helps identify if usage is growing or falling over the Beta period
        weekly_usage = (
            CreditUsageLog.objects.annotate(week=TruncWeek("created_at"))
            .values("week")
            .annotate(total=Sum("amount"))
            .order_by("-week")[:12]
        )

        data = {
            "daily_time_series": [
                {"date": d["day"].date(), "credits": d["total"]} for d in daily_usage
            ],
            "peak_usage_hours": [
                {"hour_24h": h["hour"], "total_credits": h["total"]}
                for h in hourly_distribution
            ],
            "weekly_growth": [
                {"week_start": w["week"].date(), "total_credits": w["total"]}
                for w in weekly_usage
            ],
            "infrastructure_insight": {
                "peak_hour": (
                    max(hourly_distribution, key=lambda x: x["total"])["hour"]
                    if hourly_distribution
                    else None
                ),
                "current_week_velocity": (
                    weekly_usage[0]["total"] if weekly_usage else 0
                ),
            },
        }

        serializer = BetaUsageTrendSerializer(data)
        return Response(serializer.data)

    @extend_schema(
        tags=["Beta Analytics"],
        summary="Identify high-intent conversion leads",
        description="Identifies 'Power Users' with high conversion probability based on four engagement "
        "triggers: (1) High consumption (≥80% of allocation), (2) High frequency (≥8 login days), "
        "(3) Recent activity (active in last 7 days), (4) Core value alignment (grading-focused). "
        "Returns ranked leads with conversion scores, detailed metrics, and behavioral flags for "
        "targeted sales outreach and August launch conversion planning.",
        responses={
            200: OpenApiResponse(
                response=ConversionLeadSerializer(many=True),
                description="Ranked list of high-intent conversion leads with scoring and metrics",
                examples=[
                    OpenApiExample(
                        name="Conversion Leads Example",
                        value=[
                            {
                                "email": "teacher1@school.edu",
                                "score": 87.5,
                                "metrics": {
                                    "usage_percentage": 92.3,
                                    "login_days": 15,
                                    "last_active": "2024-01-20",
                                    "primary_use_case": "Grading",
                                },
                                "flags": {
                                    "at_80_percent": True,
                                    "active_last_week": True,
                                    "is_power_grader": True,
                                },
                            },
                            {
                                "email": "teacher2@school.edu",
                                "score": 72.1,
                                "metrics": {
                                    "usage_percentage": 65.8,
                                    "login_days": 12,
                                    "last_active": "2024-01-19",
                                    "primary_use_case": "Creation",
                                },
                                "flags": {
                                    "at_80_percent": False,
                                    "active_last_week": True,
                                    "is_power_grader": False,
                                },
                            },
                        ],
                        description="Example showing two leads ranked by conversion probability",
                    )
                ],
            ),
        },
    )
    @action(detail=False, methods=["GET"], url_path="beta/intent-signals")
    def intent_signals(self, request, *args, **kwargs):
        """
        Identifies "High-Intent" teachers based on specific Beta engagement triggers
        Used for August Sales outreach and conversion planning.
        """

        seven_days_ago = timezone.now() - timedelta(days=7)

        # 1. Build the "Power User" Filter
        # Each 'Q' object represents one of your four core business rules
        power_user_query = (
            Q(
                # Trigger 1: High consumption (>= 80% of their 10M grant)
                has_hit_80_percent=True
            )
            | Q(
                # Trigger 2: High frequency (Logged in >=8 distinct days)
                distinct_login_days__gte=8
            )
            | Q(
                # Trigger 3: Sticky behavior (Active in the final week of Beta)
                last_active_at__gte=seven_days_ago
            )
            | Q(
                # Trigger 4: Core value (Uses Grading more than Assignment Creation)
                credits_used_grading__gt=F("credits_used_creation")
            )
        )

        # 2. Fetch the leads with their conversion probability score
        leads = (
            self.get_queryset()
            .filter(power_user_query)
            .select_related("user")
            .order_by("-conversion_probability", "-total_credits_used")
        )

        # 3. Structure the response for the Sales Team
        data = []
        for p in leads:
            data.append(
                {
                    "email": p.user.email,
                    "score": round(p.conversion_probability, 1),
                    "metrics": {
                        "usage_percentage": round(
                            (p.total_credits_used / p.initial_beta_credits) * 100, 1
                        ),
                        "login_days": p.distinct_login_days,
                        "last_active": (
                            p.last_active_at.date() if p.last_active_at else None
                        ),
                        "primary_use_case": (
                            "Grading"
                            if p.credits_used_grading > p.credits_used_creation
                            else "Creation"
                        ),
                    },
                    "flags": {
                        "at_80_percent": p.has_hit_80_percent,
                        "active_last_week": (
                            p.last_active_at >= seven_days_ago
                            if p.last_active_at
                            else False
                        ),
                        "is_power_grader": p.credits_used_grading
                        > p.credits_used_creation,
                    },
                }
            )

        serializer = ConversionLeadSerializer(data, many=True)
        return Response(serializer.data)
