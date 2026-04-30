"""
billing/views_admin_credits.py
==============================
AdminCreditManagementViewSet — superadmin-only manual credit grant endpoints.

Kept in a separate file to avoid bloating billing/views.py further.
Import and register this viewset in billing/urls.py.
"""

from django.db.models import Sum

# from django.utils import timezone
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from classrooms.permissions import IsSuperAdmin
from users.models import CustomUser

from .models import CONVERSION_FACTOR, CreditBucket, CreditBucketType
from .serializers import (
    AdminGrantSummarySerializer,
    ManualCreditTopUpSerializer,
    ManualGrantBucketSerializer,
)
from .services import ManualCreditService


class AdminCreditManagementViewSet(viewsets.GenericViewSet):
    """
    Superadmin-only endpoints for manual credit management.

    All actions require the requesting user to be a SUPER_ADMIN.
    Manual grants create a dedicated MANUAL_GRANT CreditBucket that is
    clearly distinct from subscription-driven credits in the ledger and
    analytics, preserving the integrity of beta cohort data.
    """

    permission_classes = [IsSuperAdmin]
    http_method_names = ["get", "post", "options", "head"]

    # ------------------------------------------------------------------
    # Top-up (grant) endpoint
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Admin — Credit Management"],
        summary="Grant manual credits to a user",
        description="""
        Inject a one-off credit grant into any user's wallet.

        Use this for:
        - Custom-negotiated deals that fall outside subscription tiers
        - Goodwill top-ups after service issues
        - School/pilot programme credit allocations

        The grant creates a dedicated **MANUAL_GRANT** CreditBucket and an
        immutable ledger entry recording the amount, reason, expiry, and the
        admin who authorised it.

        `amount` is supplied in **display units** (the number the teacher sees
        in the UI). Internally it is multiplied by 1000 before storage.

        If `expires_at` is omitted or `null`, the credits **never expire**.
        """,
        request=ManualCreditTopUpSerializer,
        responses={
            201: OpenApiResponse(
                response=ManualGrantBucketSerializer,
                description="Grant created successfully. Returns the new bucket.",
                examples=[
                    OpenApiExample(
                        "Successful grant",
                        value={
                            "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                            "wallet": "7cb85f64-1234-4562-b3fc-2c963f66afa6",
                            "bucket_type": "MANUAL_GRANT",
                            "total_credits": 500000,
                            "used_credits": 0,
                            "display_total": 500,
                            "display_remaining": 500,
                            "display_used": 0,
                            "expires_at": None,
                            "days_until_expiry": None,
                            "is_expired": False,
                            "status": "active",
                            "granted_by_email": "admin@gradea.com",
                            "ledger_reason": "Custom deal — 500 credits for Spring semester",
                            "created_at": "2025-04-01T10:00:00Z",
                            "updated_at": "2025-04-01T10:00:00Z",
                        },
                    )
                ],
            ),
            400: OpenApiResponse(
                description="Validation error — invalid user, amount, or expiry."
            ),
            403: OpenApiResponse(description="Forbidden — SuperAdmin access required."),
        },
    )
    @action(detail=False, methods=["post"], url_path="grant")
    def grant(self, request, *args, **kwargs):
        serializer = ManualCreditTopUpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data

        # validate_user_id returns the resolved CustomUser instance
        target_user = data["user_id"]
        amount_display = data["amount"]
        reason = data["reason"]
        expires_at = data.get("expires_at")

        bucket = ManualCreditService.top_up_credits(
            target_user=target_user,
            amount_display=amount_display,
            reason=reason,
            expires_at=expires_at,
            granted_by=request.user,
        )

        # Prefetch ledger so the serializer can read metadata without extra queries
        bucket.credit_ledgers.all()

        response_serializer = ManualGrantBucketSerializer(bucket)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)

    # ------------------------------------------------------------------
    # Grant history for a specific user
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Admin — Credit Management"],
        summary="List all manual grants for a specific user",
        description="""
        Returns the full history of **MANUAL_GRANT** buckets for a given user,
        including expired and exhausted grants, for audit purposes.

        Pass `user_id` as a path parameter.
        """,
        responses={
            200: OpenApiResponse(
                response=ManualGrantBucketSerializer(many=True),
                description="List of all manual grant buckets for the user.",
            ),
            404: OpenApiResponse(description="User not found."),
        },
    )
    @action(
        detail=False,
        methods=["get"],
        url_path="grants/user/(?P<user_id>[^/.]+)",
    )
    def user_grant_history(self, request, user_id=None, *args, **kwargs):
        try:
            target_user = CustomUser.objects.get(id=user_id)
        except CustomUser.DoesNotExist:
            return Response(
                {"detail": f"No user found with id {user_id!r}."},
                status=status.HTTP_404_NOT_FOUND,
            )

        grants = ManualCreditService.get_grant_history(target_user)
        serializer = ManualGrantBucketSerializer(grants, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    # ------------------------------------------------------------------
    # Platform-wide grant history
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Admin — Credit Management"],
        summary="List all manual grants across all users",
        description="""
        Returns all **MANUAL_GRANT** buckets across the entire platform, ordered
        by most recent first.

        Use this for a platform-wide audit of credit grants issued outside of
        subscription plans.
        """,
        responses={
            200: OpenApiResponse(
                response=ManualGrantBucketSerializer(many=True),
                description="Platform-wide list of all manual grant buckets.",
            ),
        },
    )
    @action(detail=False, methods=["get"], url_path="grants/all")
    def all_grants(self, request, *args, **kwargs):
        grants = ManualCreditService.get_all_grants_summary()
        serializer = ManualGrantBucketSerializer(grants, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    # ------------------------------------------------------------------
    # Aggregate summary
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Admin — Credit Management"],
        summary="Aggregate summary of all manual grants",
        description="""
        Returns platform-wide totals for manual credit grants:
        - How many grants have been issued
        - Total credits granted (display units)
        - Total credits still remaining across active grants
        - Breakdown by status (active / expired / exhausted)
        """,
        responses={
            200: OpenApiResponse(
                response=AdminGrantSummarySerializer,
                description="Aggregate grant statistics.",
                examples=[
                    OpenApiExample(
                        "Summary example",
                        value={
                            "total_grants": 12,
                            "total_credits_granted_display": 6200,
                            "total_credits_remaining_display": 3100,
                            "active_grants": 8,
                            "expired_grants": 3,
                            "exhausted_grants": 1,
                        },
                    )
                ],
            ),
        },
    )
    @action(detail=False, methods=["get"], url_path="grants/summary")
    def grants_summary(self, request, *args, **kwargs):
        # now = timezone.now()

        all_grants = CreditBucket.objects.filter(
            bucket_type=CreditBucketType.MANUAL_GRANT
        )

        total_grants = all_grants.count()

        total_credits_granted = (
            all_grants.aggregate(total=Sum("total_credits"))["total"] or 0
        )

        # Active: not expired AND has remaining credits
        # active_qs = all_grants.filter(expires_at__isnull=True) | all_grants.filter(
        #     expires_at__gt=now
        # )

        # We can't do complex property-based filtering in a single ORM call,
        # so we split into Python for status classification
        active = 0
        expired = 0
        exhausted = 0
        total_remaining_raw = 0

        for bucket in all_grants.iterator():
            if bucket.is_expired():
                expired += 1
            elif bucket.remaining_credits == 0:
                exhausted += 1
            else:
                active += 1
                total_remaining_raw += bucket.remaining_credits

        data = {
            "total_grants": total_grants,
            "total_credits_granted_display": total_credits_granted // CONVERSION_FACTOR,
            "total_credits_remaining_display": total_remaining_raw // CONVERSION_FACTOR,
            "active_grants": active,
            "expired_grants": expired,
            "exhausted_grants": exhausted,
        }

        serializer = AdminGrantSummarySerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)
