"""
billing/views_admin_credits.py
==============================
AdminCreditManagementViewSet — superadmin-only manual credit grant endpoints.

Kept in a separate file to avoid bloating billing/views.py further.
Import and register this viewset in billing/urls.py.
"""

from datetime import timedelta

from django.db.models import Prefetch, Sum
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response

from classrooms.permissions import IsSuperAdmin
from users.models import CustomUser

from .models import (
    CONVERSION_FACTOR,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
)
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
        admin who authorised it. The recipient is notified by email.

        `blocks` is the number of credit blocks to grant, priced using the
        target user's own resolved plan (`plan.overage_block_size`) — the
        same block size used for paid overage purchases.

        `user_id` and `blocks` are required; `reason` and `expires_at` are
        optional. If `expires_at` is omitted or `null`, the credits **never
        expire**.
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
                            "ledger_reason": "Custom deal — Spring semester bonus",
                            "blocks_granted": 100,
                            "block_size": 5000,
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
        blocks = data["blocks"]
        reason = data["reason"]
        expires_at = data.get("expires_at")

        try:
            bucket = ManualCreditService.top_up_credits(
                target_user=target_user,
                blocks=blocks,
                reason=reason,
                expires_at=expires_at,
                granted_by=request.user,
            )
        except ValueError as e:
            raise ValidationError(str(e)) from e

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
        parameters=[
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number for pagination",
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of results per page (max 100)",
            ),
        ],
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
        except CustomUser.DoesNotExist as exc:
            raise NotFound(f"No user found with id {user_id!r}.") from exc

        grants = ManualCreditService.get_grant_history(target_user)
        page = self.paginate_queryset(grants)
        if page is not None:
            serializer = ManualGrantBucketSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

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
        parameters=[
            OpenApiParameter(
                name="page",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Page number for pagination",
            ),
            OpenApiParameter(
                name="page_size",
                type=OpenApiTypes.INT,
                location=OpenApiParameter.QUERY,
                description="Number of results per page (max 100)",
            ),
        ],
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
        page = self.paginate_queryset(grants)
        if page is not None:
            serializer = ManualGrantBucketSerializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = ManualGrantBucketSerializer(grants, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    # ------------------------------------------------------------------
    # Single grant detail
    # ------------------------------------------------------------------

    @extend_schema(
        tags=["Admin — Credit Management"],
        summary="Retrieve a single manual grant",
        description="""
        Returns the full detail of a single **MANUAL_GRANT** bucket, including
        the recipient's name/email, blocks allocated, block size, expiry, and
        the admin who authorised it.

        Pass the grant's bucket `id` as a path parameter.
        """,
        responses={
            200: OpenApiResponse(
                response=ManualGrantBucketSerializer,
                description="The requested manual grant.",
            ),
            404: OpenApiResponse(description="Grant not found."),
        },
    )
    @action(
        detail=False,
        methods=["get"],
        url_path="grants/detail/(?P<grant_id>[^/.]+)",
    )
    def grant_detail(self, request, grant_id=None, *args, **kwargs):
        bucket = (
            CreditBucket.objects.filter(
                bucket_type=CreditBucketType.MANUAL_GRANT, id=grant_id
            )
            .select_related("wallet__user")
            .prefetch_related("credit_ledgers")
            .first()
        )
        if not bucket:
            raise NotFound(f"No manual grant found with id {grant_id!r}.")

        serializer = ManualGrantBucketSerializer(bucket)
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
                            "total_blocks_granted": 62,
                            "total_credits_granted_display": 6200,
                            "total_credits_used_display": 1800,
                            "total_credits_remaining_display": 3100,
                            "unique_recipients": 9,
                            "active_grants": 8,
                            "expired_grants": 3,
                            "exhausted_grants": 1,
                            "expiring_soon_grants": 2,
                            "grants_by_admin": [
                                {
                                    "granted_by_email": "admin@gradea.com",
                                    "grants_count": 10,
                                    "total_blocks": 50,
                                    "total_credits_display": 5000,
                                },
                                {
                                    "granted_by_email": "ops@gradea.com",
                                    "grants_count": 2,
                                    "total_blocks": 12,
                                    "total_credits_display": 1200,
                                },
                            ],
                        },
                    )
                ],
            ),
        },
    )
    @action(detail=False, methods=["get"], url_path="grants/summary")
    def grants_summary(self, request, *args, **kwargs):
        now = timezone.now()
        expiring_soon_cutoff = now + timedelta(days=30)

        all_grants = CreditBucket.objects.filter(
            bucket_type=CreditBucketType.MANUAL_GRANT
        ).prefetch_related(
            Prefetch(
                "credit_ledgers",
                queryset=CreditLedger.objects.filter(
                    ledger_type=CreditLedgerType.GRANT
                ),
                to_attr="grant_ledgers",
            )
        )

        total_grants = all_grants.count()

        total_credits_granted = (
            all_grants.aggregate(total=Sum("total_credits"))["total"] or 0
        )
        total_credits_used = (
            all_grants.aggregate(total=Sum("used_credits"))["total"] or 0
        )
        unique_recipients = all_grants.values("wallet__user").distinct().count()

        # Status classification and per-admin/blocks breakdown both need
        # per-bucket property access (is_expired/remaining_credits) and
        # ledger metadata, neither of which is a plain DB column, so we
        # split into Python rather than a single complex ORM call.
        active = 0
        expired = 0
        exhausted = 0
        expiring_soon = 0
        total_remaining_raw = 0
        total_blocks_granted = 0
        by_admin: dict[str | None, dict] = {}

        for bucket in all_grants:
            if bucket.is_expired():
                expired += 1
            elif bucket.remaining_credits == 0:
                exhausted += 1
            else:
                active += 1
                total_remaining_raw += bucket.remaining_credits
                if bucket.expires_at and bucket.expires_at <= expiring_soon_cutoff:
                    expiring_soon += 1

            ledger = bucket.grant_ledgers[0] if bucket.grant_ledgers else None
            metadata = ledger.metadata if ledger and ledger.metadata else {}
            blocks = metadata.get("blocks") or 0
            granted_by_email = metadata.get("granted_by_email")

            total_blocks_granted += blocks

            admin_row = by_admin.setdefault(
                granted_by_email,
                {
                    "granted_by_email": granted_by_email,
                    "grants_count": 0,
                    "total_blocks": 0,
                    "total_credits_display": 0,
                },
            )
            admin_row["grants_count"] += 1
            admin_row["total_blocks"] += blocks
            admin_row["total_credits_display"] += (
                bucket.total_credits // CONVERSION_FACTOR
            )

        data = {
            "total_grants": total_grants,
            "total_blocks_granted": total_blocks_granted,
            "total_credits_granted_display": total_credits_granted // CONVERSION_FACTOR,
            "total_credits_used_display": total_credits_used // CONVERSION_FACTOR,
            "total_credits_remaining_display": total_remaining_raw // CONVERSION_FACTOR,
            "unique_recipients": unique_recipients,
            "active_grants": active,
            "expired_grants": expired,
            "exhausted_grants": exhausted,
            "expiring_soon_grants": expiring_soon,
            "grants_by_admin": sorted(
                by_admin.values(), key=lambda row: row["total_blocks"], reverse=True
            ),
        }

        serializer = AdminGrantSummarySerializer(data)
        return Response(serializer.data, status=status.HTTP_200_OK)
