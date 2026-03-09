from django.db.models import Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime
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
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditUsageLog,
    CreditWallet,
    SubscriptionPlan,
    UserSubscription,
)
from .serializers import (  # SubscriptionSerializer,
    CarryOverHistorySerializer,
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
from .services import SubscriptionService

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
