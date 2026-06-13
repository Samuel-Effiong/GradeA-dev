"""
ViewSets for License subscription management.

This module provides RESTful API endpoints for managing institutional
(License) subscriptions, including teacher enrollment, credit allocation,
and subscription lifecycle operations.
"""

from django.db import transaction
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from classrooms.models import School
from classrooms.permissions import IsNotStudent  # IsSuperAdmin
from users.models import CustomUser, UserTypes

from .license_service import LicenseSubscriptionService
from .models import LicenseSubscription, SchoolCreditAllocation, SubscriptionPlan
from .serializers import LicenseSubscriptionSerializer, SchoolCreditAllocationSerializer


class IsSchoolAdminOrSuperAdmin(IsAuthenticated):
    """
    Permission class for License subscription endpoints.
    Only school admins (for their school) or super admins can manage licenses.
    """

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False

        # Super admin can do anything
        if (
            request.user.is_superuser
            and request.user.user_type == UserTypes.SUPER_ADMIN
        ):
            return True

        # School admin can manage licenses for their school
        if request.user.user_type == UserTypes.SCHOOL_ADMIN:
            return True

        return False

    def has_object_permission(self, request, view, obj):
        # Super admin can do anything
        if (
            request.user.is_superuser
            and request.user.user_type == UserTypes.SUPER_ADMIN
        ):
            return True

        # School admin can only manage licenses for their school
        if request.user.user_type == UserTypes.SCHOOL_ADMIN:
            # Check if user is an admin for the license's school
            return obj.school.admins.filter(id=request.user.id).exists()

        return False


@extend_schema_view(
    list=extend_schema(
        tags=["License Subscriptions"],
        summary="List license subscriptions",
        description="Get list of all license subscriptions (filtered by school for school admins).",
    ),
    create=extend_schema(
        tags=["License Subscriptions"],
        summary="Create new license subscription",
        description="Create a new institutional license subscription for a school.",
    ),
    retrieve=extend_schema(
        tags=["License Subscriptions"],
        summary="Get license subscription details",
    ),
    update=extend_schema(
        tags=["License Subscriptions"],
        summary="Update license subscription",
    ),
    partial_update=extend_schema(
        tags=["License Subscriptions"],
        summary="Partially update license subscription",
    ),
    destroy=extend_schema(
        tags=["License Subscriptions"],
        summary="Cancel/delete license subscription",
    ),
)
class LicenseSubscriptionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing License subscriptions.

    A License subscription is an institutional subscription for a school,
    allowing multiple teachers to have independent credit allocations.

    Endpoints:
    - GET /api/billing/license-subscriptions/ - List all licenses
    - POST /api/billing/license-subscriptions/ - Create new license
    - GET /api/billing/license-subscriptions/{id}/ - Get license details
    - PATCH /api/billing/license-subscriptions/{id}/ - Update license
    - DELETE /api/billing/license-subscriptions/{id}/ - Cancel license
    - POST /api/billing/license-subscriptions/{id}/add-teachers/ - Enroll teachers
    - POST /api/billing/license-subscriptions/{id}/remove-teachers/ - Remove teachers
    - POST /api/billing/license-subscriptions/{id}/process-renewal/ - Manual renewal
    - GET /api/billing/license-subscriptions/{id}/renewal-info/ - Get renewal status
    """

    queryset = (
        LicenseSubscription.objects.all()
        .select_related("school", "admin_user", "plan")
        .prefetch_related("allocations__user")
    )
    serializer_class = LicenseSubscriptionSerializer
    permission_classes = [IsSchoolAdminOrSuperAdmin]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["school", "is_active", "auto_renew"]
    search_fields = ["school__name", "plan__name", "admin_user__email"]
    ordering_fields = ["created_at", "billing_cycle_end", "teacher_count"]
    ordering = ["-created_at"]

    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        """
        Filter licenses based on user role:
        - Super admin: sees all licenses
        - School admin: sees only licenses for their school(s)
        """
        queryset = super().get_queryset()

        if (
            not self.request.user.is_superuser
            or self.request.user.user_type != UserTypes.SUPER_ADMIN
        ):
            # School admin: filter to their school(s)
            if self.request.user.user_type == UserTypes.SCHOOL_ADMIN:
                # Get schools where user is admin
                admin_schools = School.objects.filter(admins=self.request.user)
                queryset = queryset.filter(school__in=admin_schools)
            else:
                # Other users should not see licenses
                queryset = queryset.none()

        return queryset

    def create(self, request, *args, **kwargs):
        """
        Create a new license subscription.

        Request body:
        {
            "school": <school_id>,
            "admin_user": <user_id>,
            "plan": <plan_id>,
            "teacher_ids": [<user_id>, ...] (optional)
        }
        """
        school_id = request.data.get("school")
        admin_user_id = request.data.get("admin_user")
        plan_id = request.data.get("plan")
        teacher_ids = request.data.get("teacher_ids", [])

        # Validate inputs
        if not all([school_id, admin_user_id, plan_id]):
            return Response(
                {"error": "school, admin_user, and plan are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Get objects
        school = get_object_or_404(School, id=school_id)
        admin_user = get_object_or_404(CustomUser, id=admin_user_id)
        plan = get_object_or_404(SubscriptionPlan, id=plan_id)

        # Check permission to manage this school
        if not (
            request.user.is_superuser
            and request.user.user_type == UserTypes.SUPER_ADMIN
        ):
            if request.user.user_type != UserTypes.SCHOOL_ADMIN:
                return Response(
                    {"error": "Only school admins can create licenses"},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if not school.admins.filter(id=request.user.id).exists():
                return Response(
                    {"error": "You are not an admin for this school"},
                    status=status.HTTP_403_FORBIDDEN,
                )

        try:
            with transaction.atomic():
                license_sub = LicenseSubscriptionService.create_license_subscription(
                    school=school,
                    plan=plan,
                    admin_user=admin_user,
                    teacher_ids=teacher_ids if teacher_ids else None,
                )

            serializer = self.get_serializer(license_sub)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(tags=["License Subscriptions"])
    @action(detail=True, methods=["post"])
    def add_teachers(self, request, pk=None):
        """
        Add teachers to an existing license subscription.

        Request body:
        {
            "teacher_ids": [<user_id>, ...]
        }

        Response:
        {
            "successful": <count>,
            "failed": <count>,
            "errors": [{"teacher_id": <id>, "error": <message>}]
        }
        """
        license_sub = self.get_object()
        teacher_ids = request.data.get("teacher_ids", [])

        if not teacher_ids:
            return Response(
                {"error": "teacher_ids is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                results = LicenseSubscriptionService.add_teachers_batch(
                    license_sub, teacher_ids
                )

            return Response(results, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(tags=["License Subscriptions"])
    @action(detail=True, methods=["post"])
    def remove_teachers(self, request, pk=None):
        """
        Remove teachers from a license subscription.

        Request body:
        {
            "teacher_ids": [<user_id>, ...]
        }

        Response:
        {
            "successful": <count>,
            "failed": <count>,
            "errors": [{"teacher_id": <id>, "error": <message>}]
        }
        """
        license_sub = self.get_object()
        teacher_ids = request.data.get("teacher_ids", [])

        if not teacher_ids:
            return Response(
                {"error": "teacher_ids is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        successful = 0
        failed = 0
        errors = []

        for teacher_id in teacher_ids:
            try:
                teacher = get_object_or_404(CustomUser, id=teacher_id)
                with transaction.atomic():
                    LicenseSubscriptionService.remove_teacher_from_license(
                        license_sub, teacher
                    )
                successful += 1
            except Exception as e:
                failed += 1
                errors.append({"teacher_id": teacher_id, "error": str(e)})

        return Response(
            {"successful": successful, "failed": failed, "errors": errors},
            status=status.HTTP_200_OK,
        )

    @extend_schema(tags=["License Subscriptions"])
    @action(detail=True, methods=["post"])
    def process_renewal(self, request, pk=None):
        """
        Manually trigger license renewal (normally done monthly by Celery).

        This should only be called by super admins and is primarily for testing.
        """
        if not (
            request.user.is_superuser
            and request.user.user_type == UserTypes.SUPER_ADMIN
        ):
            return Response(
                {"error": "Only super admins can manually process renewals"},
                status=status.HTTP_403_FORBIDDEN,
            )

        license_sub = self.get_object()

        try:
            with transaction.atomic():
                LicenseSubscriptionService.process_license_renewal(license_sub)

            return Response(
                {"status": "Renewal processed successfully"},
                status=status.HTTP_200_OK,
            )

        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

    @extend_schema(tags=["License Subscriptions"])
    @action(detail=True, methods=["get"])
    def renewal_info(self, request, pk=None):
        """
        Get renewal information for a license subscription.

        Returns:
        {
            "next_renewal_date": <datetime>,
            "days_until_renewal": <int>,
            "auto_renew": <bool>,
            "teacher_count": <int>,
            "active_teacher_count": <int>,
            "allocations": [...]
        }
        """
        license_sub = self.get_object()

        # from datetime import timedelta

        from django.utils import timezone

        now = timezone.now()
        days_until = (license_sub.billing_cycle_end - now).days

        return Response(
            {
                "next_renewal_date": license_sub.billing_cycle_end,
                "days_until_renewal": max(0, days_until),
                "auto_renew": license_sub.auto_renew,
                "is_active": license_sub.is_active,
                "teacher_count": license_sub.teacher_count,
                "active_teacher_count": license_sub.allocations.filter(
                    is_active=True
                ).count(),
                "allocations": SchoolCreditAllocationSerializer(
                    license_sub.allocations.all(), many=True
                ).data,
            }
        )


@extend_schema_view(
    list=extend_schema(
        tags=["Credit Allocations"],
        summary="List school credit allocations",
        description="Get list of credit allocations for teachers under licenses.",
    ),
    retrieve=extend_schema(
        tags=["Credit Allocations"],
        summary="Get allocation details",
    ),
)
class SchoolCreditAllocationViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for viewing school credit allocations.

    Teachers can see their own allocation. School admins can see allocations
    for their school. Super admins can see all allocations.

    Allocations are managed through the LicenseSubscriptionViewSet
    add_teachers and remove_teachers actions.

    Endpoints:
    - GET /api/billing/school-credit-allocations/ - List all allocations
    - GET /api/billing/school-credit-allocations/{id}/ - Get allocation details
    """

    queryset = (
        SchoolCreditAllocation.objects.all()
        .select_related("license_subscription", "user")
        .order_by("-created_at")
    )
    serializer_class = SchoolCreditAllocationSerializer
    permission_classes = [IsAuthenticated, IsNotStudent]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["license_subscription", "is_active"]
    search_fields = ["user__email", "license_subscription__school__name"]
    ordering_fields = ["created_at", "monthly_allocation"]

    http_method_names = ["get", "head", "options"]

    def get_queryset(self):
        """
        Filter allocations based on user role:
        - Super admin: sees all allocations
        - School admin: sees allocations for teachers in their school(s)
        - Teachers: sees only their own allocation
        """
        queryset = super().get_queryset()

        # Super admin: sees all
        if (
            self.request.user.is_superuser
            and self.request.user.user_type == UserTypes.SUPER_ADMIN
        ):
            return queryset

        # School admin: filter to their school(s)
        if self.request.user.user_type == UserTypes.SCHOOL_ADMIN:
            admin_schools = School.objects.filter(admins=self.request.user)
            return queryset.filter(license_subscription__school__in=admin_schools)

        # Teachers: filter to their own allocation
        return queryset.filter(user=self.request.user)
