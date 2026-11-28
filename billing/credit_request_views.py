"""Epic B1c: the credit-request API (plan 08 §3.8), under
/api/v1/billing/credit-requests. Dark (404) while CREDIT_REQUESTS_ENABLED is
off.

    POST   /billing/credit-requests              a licensed teacher asks
    GET    /billing/credit-requests              own / license's / all
    GET    /billing/credit-requests/counts       license admin, super admin
    POST   /billing/credit-requests/{id}/approve license admin, super admin
                                                 (not in the API docs)
    POST   /billing/credit-requests/{id}/decline license admin, super admin
    POST   /billing/credit-requests/{id}/withdraw the requesting teacher
"""

import logging

import stripe
from django.http import Http404
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
    inline_serializer,
)
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from AutoGrader.error_messages import describe_user_error

from . import credit_requests
from .license_service import LicenseSubscriptionService
from .models import CreditRequest, CreditRequestStatus

logger = logging.getLogger(__name__)


class CreditRequestSerializer(serializers.ModelSerializer):
    teacher_name = serializers.SerializerMethodField()
    teacher_email = serializers.EmailField(source="teacher.email", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    decided_by_name = serializers.SerializerMethodField()

    class Meta:
        model = CreditRequest
        fields = [
            "id",
            "teacher",
            "teacher_name",
            "teacher_email",
            "license_subscription",
            "requested_blocks",
            "approved_blocks",
            "message",
            "status",
            "status_label",
            "note",
            "decline_reason",
            "decided_by_name",
            "decided_at",
            "purchase_intent",
            "offline_request",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_teacher_name(self, obj):
        return obj.teacher.get_full_name() or obj.teacher.email

    def get_decided_by_name(self, obj):
        user = obj.decided_by
        return (user.get_full_name() or user.email) if user else None


class CreateCreditRequestSerializer(serializers.Serializer):
    requested_blocks = serializers.IntegerField(
        min_value=1, max_value=LicenseSubscriptionService.MAX_BLOCKS_PER_PURCHASE
    )
    message = serializers.CharField(
        max_length=500, required=False, allow_blank=True, default=""
    )


class ApproveCreditRequestSerializer(serializers.Serializer):
    blocks = serializers.IntegerField(
        min_value=1,
        max_value=LicenseSubscriptionService.MAX_BLOCKS_PER_PURCHASE,
        required=False,
    )
    payment_method = serializers.ChoiceField(
        choices=["stripe", "offline_request"], default="stripe"
    )
    success_url = serializers.URLField(required=False)
    cancel_url = serializers.URLField(required=False)


class DeclineCreditRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500, required=False, allow_blank=True)


def _refusal(exc):
    return Response({"detail": str(exc)}, status=exc.status_code)


CREDIT_REQUESTS = ["Credit Requests"]
COUNTS_REPLY = inline_serializer(
    "CreditRequestCounts",
    {
        "pending": serializers.IntegerField(),
        "answered": serializers.IntegerField(),
        "granted": serializers.IntegerField(),
        "declined": serializers.IntegerField(),
    },
)
APPROVE_REPLY = inline_serializer(
    "CreditRequestApproved",
    {"request": CreditRequestSerializer(), "purchase": serializers.DictField()},
)


@extend_schema(tags=CREDIT_REQUESTS)
@extend_schema_view(
    list=extend_schema(
        tags=CREDIT_REQUESTS,
        parameters=[
            OpenApiParameter(
                "status", str, OpenApiParameter.QUERY, enum=CreditRequestStatus.values
            )
        ],
        responses=CreditRequestSerializer(many=True),
    ),
    create=extend_schema(
        tags=CREDIT_REQUESTS,
        request=CreateCreditRequestSerializer,
        responses={201: CreditRequestSerializer},
    ),
    counts=extend_schema(tags=CREDIT_REQUESTS, responses=COUNTS_REPLY),
    # Owner, 2026-11-27: hidden from the API docs only (the frontend gives
    # overage credit another way); the route works exactly as before, and
    # removing it needs its own yes.
    approve=extend_schema(
        exclude=True,
        tags=CREDIT_REQUESTS,
        request=ApproveCreditRequestSerializer,
        responses=APPROVE_REPLY,
    ),
    decline=extend_schema(
        tags=CREDIT_REQUESTS,
        request=DeclineCreditRequestSerializer,
        responses=CreditRequestSerializer,
    ),
    withdraw=extend_schema(
        tags=CREDIT_REQUESTS, request=None, responses=CreditRequestSerializer
    ),
)
class CreditRequestViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    lookup_value_regex = "[0-9a-fA-F-]{36}"

    def initial(self, request, *args, **kwargs):
        # Sign-in is checked first, so an anonymous caller gets 401 as on
        # every other route; a signed-in one gets 404 while the flag is off.
        super().initial(request, *args, **kwargs)
        if not credit_requests.enabled():
            raise Http404

    def list(self, request):
        rows = credit_requests.visible_to(request.user)
        wanted = request.query_params.get("status")
        if wanted:
            if wanted not in CreditRequestStatus.values:
                return Response(
                    {"status": [f"Unknown status {wanted!r}."]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            rows = rows.filter(status=wanted)
        return Response(CreditRequestSerializer(rows, many=True).data)

    def create(self, request):
        serializer = CreateCreditRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            row = credit_requests.create_request(
                request.user,
                data["requested_blocks"],
                data.get("message", ""),
            )
        except credit_requests.CreditRequestError as exc:
            return _refusal(exc)
        return Response(
            CreditRequestSerializer(row).data, status=status.HTTP_201_CREATED
        )

    @action(detail=False, methods=["get"], url_path="counts")
    def counts(self, request):
        return Response(credit_requests.counts_for(request.user))

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        serializer = ApproveCreditRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            result = credit_requests.approve_request(
                pk,
                request.user,
                blocks=data.get("blocks"),
                payment_method=data["payment_method"],
                success_url=data.get("success_url"),
                cancel_url=data.get("cancel_url"),
            )
        except credit_requests.CreditRequestError as exc:
            return _refusal(exc)
        except ValueError as exc:
            return Response(
                {
                    "detail": describe_user_error(
                        exc,
                        fallback_message="We couldn't start the overage purchase. Please try again.",
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except stripe.error.StripeError:
            logger.exception("Stripe error approving credit request %s", pk)
            return Response(
                {"detail": "An unexpected error occurred."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        row = CreditRequest.objects.select_related("teacher", "decided_by").get(pk=pk)
        return Response(
            {"request": CreditRequestSerializer(row).data, "purchase": result}
        )

    @action(detail=True, methods=["post"], url_path="decline")
    def decline(self, request, pk=None):
        serializer = DeclineCreditRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            row = credit_requests.decline_request(
                pk, request.user, serializer.validated_data.get("reason", "")
            )
        except credit_requests.CreditRequestError as exc:
            return _refusal(exc)
        return Response(CreditRequestSerializer(row).data)

    @action(detail=True, methods=["post"], url_path="withdraw")
    def withdraw(self, request, pk=None):
        try:
            row = credit_requests.withdraw_request(pk, request.user)
        except credit_requests.CreditRequestError as exc:
            return _refusal(exc)
        return Response(CreditRequestSerializer(row).data)
