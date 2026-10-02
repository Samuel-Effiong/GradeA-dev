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
        tags=["Subscription — Stripe"], summary="Retrieve a single billing transaction"
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

        # No `.distinct()`. It cannot dedupe anything here and it is not
        # free: `CustomUser.school` is a ForeignKey (one school per user,
        # related_name="users"), so `School.objects.filter(users=user)`
        # yields at most one row and is consumed as a `school__in=`
        # subquery — which cannot duplicate outer rows. Measured at ~100k
        # ledger / 16.4k transaction rows: identical result sets with and
        # without (20 vs 20 for a school admin, 40 vs 40 for a teacher) but
        # 59.1ms vs 25.2ms on the first page, a 2.4x cost. EXPLAIN shows
        # why: DISTINCT forces a Sort+Unique across all ~40 selected
        # columns (width=9335) of the six select_related tables, instead of
        # a Nested Loop the LIMIT can short-circuit.
        #
        # If school membership ever becomes many-to-many, duplication
        # becomes reachable and this must come back —
        # test_a_school_admin_pages_without_duplicates is where that would
        # surface.
        return base_qs.filter(visibility)
