"""
billing/license_overage_offline_views.py
=========================================
LicenseOverageOfflineRequestViewSet — superadmin-only review queue for
school-admin overage requests paid for outside Stripe.

Kept in a separate file to avoid bloating billing/license_views.py
further, mirroring billing/views_admin_credits.py's precedent for
superadmin-only endpoint groups.
"""

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter
from rest_framework.response import Response

from classrooms.permissions import IsSuperAdmin

from .license_service import LicenseSubscriptionService
from .models import LicenseOverageOfflineRequest
from .serializers import (
    ApproveOverageOfflineRequestSerializer,
    LicenseOverageOfflineRequestListSerializer,
    RejectOverageOfflineRequestSerializer,
)


class LicenseOverageOfflineRequestViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Superadmin-only review queue for LicenseOverageOfflineRequest rows —
    school-admin overage requests paid for outside Stripe (bank
    transfer, invoice, cash, etc.). Global across all schools/licenses,
    not nested under a single license, since the point is a cross-school
    review table.

    list/retrieve only via the standard router — creation happens
    exclusively through LicenseSubscriptionViewSet.purchase_overage
    (payment_method="offline_request"); approve/reject are the only
    mutations available here.
    """

    queryset = LicenseOverageOfflineRequest.objects.select_related(
        "license_subscription",
        "license_subscription__school",
        "license_subscription__plan",
        "requested_by",
        "reviewed_by",
    )
    serializer_class = LicenseOverageOfflineRequestListSerializer
    permission_classes = [IsSuperAdmin]
    http_method_names = ["get", "post", "options", "head"]

    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = [
        "status",
        "license_subscription",
        "license_subscription__school",
    ]
    ordering_fields = ["created_at"]
    ordering = ["created_at"]  # oldest-pending-first — a review queue triages by age

    @extend_schema(
        tags=["License Subscriptions"],
        summary="Approve an offline overage request",
        description=(
            "Superadmin-only. Re-validates the license and every "
            "allocated teacher's active status under lock at approval "
            "time — a request may have sat pending for days, during "
            "which the roster can drift. Teachers no longer active are "
            "skipped and recorded in `skipped_allocations` rather than "
            "blocking the whole approval. Records what was actually "
            "confirmed received (`amount_confirmed_cents` may differ "
            "from the originally quoted `amount_cents_quoted`)."
        ),
        request=ApproveOverageOfflineRequestSerializer,
        responses={200: LicenseOverageOfflineRequestListSerializer},
    )
    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        request_obj = self.get_object()
        serializer = ApproveOverageOfflineRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = LicenseSubscriptionService.approve_overage_offline_request(
                request_obj, performed_by=request.user, **serializer.validated_data
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            LicenseOverageOfflineRequestListSerializer(result).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["License Subscriptions"],
        summary="Reject an offline overage request",
        description=(
            "Superadmin-only. No credits are touched and no billing "
            "record is created — nothing financial happened."
        ),
        request=RejectOverageOfflineRequestSerializer,
        responses={200: LicenseOverageOfflineRequestListSerializer},
    )
    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        request_obj = self.get_object()
        serializer = RejectOverageOfflineRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = LicenseSubscriptionService.reject_overage_offline_request(
                request_obj, performed_by=request.user, **serializer.validated_data
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            LicenseOverageOfflineRequestListSerializer(result).data,
            status=status.HTTP_200_OK,
        )
