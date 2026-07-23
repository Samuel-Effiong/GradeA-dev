from django.db.models import Q
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import viewsets
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import IsAuthenticated

from classrooms.models import School
from classrooms.permissions import IsNotStudent
from users.models import UserTypes

from .models import BillingTransaction, BillingTransactionSource
from .serializers import BillingTransactionSerializer


@extend_schema_view(
    list=extend_schema(
        tags=["Subscription — Stripe"],
        summary="List billing transactions (auto-detects individual vs license)",
        description=(
            "Single endpoint covering every money-bearing billing event "
            "for both the INDIVIDUAL and LICENSE tracks.\n\n"
            "- An individual subscriber sees only their own transactions.\n"
            "- A school admin additionally sees every LICENSE transaction "
            "for school(s) they administer (Stripe-billed AND offline). "
            "Regular enrolled teachers do NOT see license billing history.\n"
            "- A super admin sees everything.\n\n"
            "Filter with `?source=`, `?transaction_type=`, `?status=`, "
            "`?billing_method=`; order with `?ordering=occurred_at` "
            "(default `-occurred_at`)."
        ),
    ),
    retrieve=extend_schema(
        tags=["Billing Transactions"], summary="Retrieve a single billing transaction"
    ),
)
class BillingTransactionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = BillingTransactionSerializer
    permission_classes = [IsAuthenticated, IsNotStudent]
    http_method_names = ["get", "head", "options"]

    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_fields = ["source", "transaction_type", "status", "billing_method"]
    ordering_fields = ["occurred_at", "amount_cents"]
    ordering = ["-occurred_at"]

    def get_queryset(self):
        user = self.request.user
        base_qs = BillingTransaction.objects.select_related(
            "school",
            "license_subscription",
            "user",
            "performed_by",
            "user_subscription",
        )

        if user.is_superuser and user.user_type == UserTypes.SUPER_ADMIN:
            return base_qs.all()

        visibility = Q(source=BillingTransactionSource.INDIVIDUAL, user=user)

        if user.user_type == UserTypes.SCHOOL_ADMIN:
            admin_schools = School.objects.filter(users=user)
            visibility |= Q(
                source=BillingTransactionSource.LICENSE, school__in=admin_schools
            )

        return base_qs.filter(visibility).distinct()
