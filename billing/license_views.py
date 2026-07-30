"""
ViewSets for License subscription management.

This module provides RESTful API endpoints for managing institutional
(License) subscriptions, including teacher enrollment, credit allocation,
and subscription lifecycle operations.
"""

import logging

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
    extend_schema_view,
)
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from AutoGrader.error_messages import describe_stripe_error, describe_user_error
from classrooms.models import School
from classrooms.permissions import IsNotStudent, IsSuperAdmin
from users.models import CustomUser, UserTypes

from .imports import stripe
from .license_service import LicenseSubscriptionService
from .models import (  # SubscriptionPlan,
    LicenseBillingMethod,
    LicenseBillingRecord,
    LicenseBillingRecordType,
    LicenseSubscription,
    SchoolCreditAllocation,
)
from .serializers import (  # PlanCategory,
    ChangeLicensePlanSerializer,
    ConvertToOfflineSerializer,
    CreditBucketSerializer,
    LicenseBillingRecordSerializer,
    LicenseOveragePurchaseResultSerializer,
    LicensePlanChangeResultSerializer,
    LicenseSubscriptionSerializer,
    ManualTeacherOverageGrantSerializer,
    OfflineLicenseRenewalSerializer,
    PurchaseLicenseOverageSerializer,
    SchoolCreditAllocationSerializer,
    UpdateLicenseSeatsSerializer,
)
from .stripe_service import StripeCheckoutService, StripeCustomerService
from .stripe_view_schemas import (  # PROCESS_RENEWAL_SCHEMA,
    ADD_TEACHERS_SCHEMA,
    LICENSE_CREATE_SCHEMA,
    REMOVE_TEACHERS_SCHEMA,
    RENEWAL_INFO_SCHEMA,
)

logger = logging.getLogger(__name__)


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
            return obj.admin_user == request.user

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

    def get_permissions(self):
        if self.action in [
            "add_teachers",
            "remove_teachers",
            "list",
            "retrieve",
            "purchase_overage",
            "setup_payment_method",
        ]:
            permission_classes = [IsSchoolAdminOrSuperAdmin]
        else:
            permission_classes = [IsSuperAdmin]

        return [permission() for permission in permission_classes]

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
                admin_schools = School.objects.filter(users=self.request.user)
                queryset = queryset.filter(school__in=admin_schools)
            else:
                # Other users should not see licenses
                queryset = queryset.none()

        return queryset

    @LICENSE_CREATE_SCHEMA
    def create(self, request, *args, **kwargs):
        """
        Create a new license subscription.

        Request body:
        {
            "school": <school_id>,
            "admin_user": <user_id>,
            "plan": <plan_id>,
            "teacher_ids": [<user_id>, ...] (optional),
            "billing_method": "STRIPE" | "OFFLINE" (optional, default STRIPE)
        }
        """

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        billing_method = data.get("billing_method") or LicenseBillingMethod.STRIPE

        if billing_method == LicenseBillingMethod.OFFLINE:
            with transaction.atomic():
                license_sub = serializer.save()

                LicenseBillingRecord.objects.create(
                    license_subscription=license_sub,
                    record_type=LicenseBillingRecordType.CREATED_OFFLINE,
                    amount_paid_cents=license_sub.custom_price_cents,
                    performed_by=request.user,
                    notes="License created via offline billing",
                )

                out_serializer = self.get_serializer(license_sub)

            response_data = dict(out_serializer.data)
            response_data["teacher_invitations"] = getattr(
                license_sub,
                "_teacher_enrollment_results",
                {"successful": 0, "failed": 0, "errors": []},
            )
            return Response(response_data, status=status.HTTP_201_CREATED)

        success_url = (
            request.data.get("success_url")
            or f"https://{settings.FRONTEND_DOMAIN}/billing/license-success"
        )
        cancel_url = (
            request.data.get("cancel_url")
            or f"https://{settings.FRONTEND_DOMAIN}/billing/license-cancelled"
        )

        try:
            session = StripeCheckoutService.create_license_session(
                school=data["school"],
                plan=data["plan"],
                admin_user=data["admin_user"],
                contract_months=data.get("contract_months", 12),
                max_seats=data.get("max_seats", 0),
                teacher_emails=data.get("teacher_emails", []),
                custom_price_cents=data.get("custom_price_cents"),
                success_url=success_url,
                cancel_url=cancel_url,
                carry_forward_teachers=data.get("carry_forward_teachers", True),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"checkout_url": session.url}, status=status.HTTP_200_OK)

    @ADD_TEACHERS_SCHEMA
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
        teacher_emails = request.data.get("teacher_emails", [])

        if not teacher_emails:
            return Response(
                {"error": "teacher_emails is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            with transaction.atomic():
                results = LicenseSubscriptionService.add_teachers_batch(
                    license_sub, teacher_emails
                )

            return Response(results, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error("Failed to add teachers to license %s", pk, exc_info=e)
            return Response(
                {
                    "error": describe_user_error(
                        e,
                        fallback_message=(
                            "We couldn't add these teachers to the license. "
                            "Please try again, or contact support if this "
                            "continues."
                        ),
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    @REMOVE_TEACHERS_SCHEMA
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
                logger.error(
                    "Failed to remove teacher %s from license", teacher_id, exc_info=e
                )
                failed += 1
                errors.append(
                    {
                        "teacher_id": teacher_id,
                        "error": describe_user_error(
                            e,
                            fallback_message=(
                                "We couldn't remove this teacher from the " "license."
                            ),
                        ),
                    }
                )

        return Response(
            {"successful": successful, "failed": failed, "errors": errors},
            status=status.HTTP_200_OK,
        )

    # @PROCESS_RENEWAL_SCHEMA
    # @action(detail=True, methods=["post"])
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
            logger.error(
                "Failed to process manual renewal for license %s", pk, exc_info=e
            )
            return Response(
                {
                    "error": describe_user_error(
                        e,
                        fallback_message=(
                            "Renewal could not be processed. Please try "
                            "again, or contact support if this continues."
                        ),
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    @RENEWAL_INFO_SCHEMA
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
                "stripe_status": license_sub.stripe_status,
                "teacher_count": license_sub.teacher_count,
                "active_teacher_count": license_sub.allocations.filter(
                    is_active=True, is_admin_allocation=False
                ).count(),
                "allocations": SchoolCreditAllocationSerializer(
                    license_sub.allocations.all(), many=True
                ).data,
            }
        )

    @extend_schema(
        tags=["License Subscriptions"],
        summary="Change license plan",
        description=(
            "Single endpoint for changing an existing license's plan, for "
            "BOTH billing methods (STRIPE and OFFLINE) — the caller never "
            "needs to know or send which one the license is on.\n\n"
            "**Behavior:**\n"
            "- STRIPE, price increase -> charged immediately (prorated).\n"
            "- STRIPE, price decrease -> plan/allocations updated now; the "
            "lower price applies starting the NEXT Stripe invoice (no "
            "refund for the current cycle).\n"
            "- STRIPE, price unchanged -> plan swapped locally, nothing "
            "charged or deferred.\n"
            "- OFFLINE, either direction -> no Stripe call; a billing "
            "record is logged and the school's invoice/contract must be "
            "adjusted manually.\n\n"
            "In every case the change applies immediately at the local "
            "(plan + teacher allocation) level — see the response `action` "
            "field to know exactly which of the above happened.\n\n"
            "`custom_price_cents` is optional. Omit it to keep the "
            "existing custom price (if any). Set it to an integer to "
            "override the plan price. Set it to `null` to remove the "
            "custom price and use the plan's default."
        ),
        request=ChangeLicensePlanSerializer,
        responses={200: LicensePlanChangeResultSerializer},
        examples=[
            OpenApiExample(
                "Keep existing custom price",
                summary="Only change plan",
                request_only=True,
                value={"plan": "7d79f7a3-936d-4d9c-a37d-4cfb471cbb06"},
            ),
            OpenApiExample(
                "Set custom price",
                summary="Override plan price",
                request_only=True,
                value={
                    "plan": "7d79f7a3-936d-4d9c-a37d-4cfb471cbb06",
                    "custom_price_cents": 2499,
                },
            ),
            OpenApiExample(
                "Remove custom price",
                summary="Revert to default plan price",
                request_only=True,
                value={
                    "plan": "7d79f7a3-936d-4d9c-a37d-4cfb471cbb06",
                    "custom_price_cents": None,
                },
            ),
            OpenApiExample(
                "STRIPE upgrade response",
                summary="Charged immediately",
                response_only=True,
                value={
                    "action": "charged",
                    "message": (
                        "License upgraded to Power License. The school was "
                        "charged the prorated difference immediately."
                    ),
                    "license": {"...": "LicenseSubscriptionSerializer fields"},
                },
            ),
            OpenApiExample(
                "OFFLINE response",
                summary="Recorded for manual invoicing",
                response_only=True,
                value={
                    "action": "recorded_offline",
                    "message": (
                        "License moved to Pro License. This license is "
                        "billed offline, so no Stripe charge was made. A "
                        "billing record was logged — remember to adjust "
                        "the school's invoice or contract to match the new "
                        "price separately."
                    ),
                    "license": {"...": "LicenseSubscriptionSerializer fields"},
                },
            ),
        ],
    )
    @action(detail=True, methods=["post"])
    def change_plan(self, request, pk=None):
        license_sub = self.get_object()

        serializer = ChangeLicensePlanSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_plan = serializer.validated_data["plan"]

        # # plan_id = request.data.get("plan")
        # # if not plan_id:
        # #     return Response(
        # #         {"detail": "The 'plan' field is required."},
        # #         status=status.HTTP_400_BAD_REQUEST,
        # #     )

        # # try:
        # #     new_plan = SubscriptionPlan.objects.get(id=plan_id, is_active=True)
        # # except SubscriptionPlan.DoesNotExist:
        # #     return Response(
        # #         {"detail": "Plan not found or inactive."},
        # #         status=status.HTTP_404_NOT_FOUND,
        # #     )

        # if new_plan.category != PlanCategory.LICENSE:
        #     return Response(
        #         {"detail": "Selected plan is not a LICENSE plan."},
        #         status=status.HTTP_400_BAD_REQUEST,
        #     )

        # Handle custom_price_cents
        if "custom_price_cents" in serializer.validated_data:
            remove_custom_price = (
                serializer.validated_data["custom_price_cents"] is None
            )
            custom_price_cents = serializer.validated_data["custom_price_cents"]
        else:
            remove_custom_price = False
            custom_price_cents = None

        try:
            result = LicenseSubscriptionService.select_plan(
                license_sub,
                new_plan,
                custom_price_cents=custom_price_cents,
                remove_custom_price=remove_custom_price,
                performed_by=request.user,
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception("Unexpected error changing license plan: %s", e)
            return Response(
                {"detail": "An unexpected error occurred."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            LicensePlanChangeResultSerializer(result).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["License Subscriptions"],
        summary="Update maximum teacher seats",
        description=(
            "Updates the maximum number of teacher seats for the license.\n\n"
            "- Increasing seats applies immediately and Stripe performs a prorated charge.\n"
            "- Decreasing seats takes effect at the next billing cycle.\n"
            "- The seat count cannot be reduced below the current number of active teachers."
        ),
        request=UpdateLicenseSeatsSerializer,
        responses={
            200: LicenseSubscriptionSerializer,
        },
    )
    @action(detail=True, methods=["post"])
    def update_seats(self, request, pk=None):
        license_sub = self.get_object()
        new_max_seats = request.data.get("max_seats")
        if new_max_seats is None:
            return Response(
                {"detail": "The 'max_seats' field is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            new_max_seats = int(new_max_seats)
            if new_max_seats <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return Response(
                {"detail": "max_seats must be a positive integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            updated_license = LicenseSubscriptionService.update_seats(
                license_sub, new_max_seats, performed_by=request.user
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception("Unexpected error updating seats: %s", e)
            return Response(
                {"detail": "An unexpected error occurred."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        serializer = self.get_serializer(updated_license)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["License Subscriptions"],
        summary="Purchase or grant overage blocks (teachers or the license admin)",
        description=(
            "Single endpoint for overage, auto-branching on the caller "
            "and on `payment_method`:\n\n"
            "- **School admin** (this license's admin_user), "
            "`payment_method='stripe'` (default): routed through Stripe "
            "Checkout. This call does NOT charge or grant anything "
            "itself — the response has a `checkout_url` to redirect the "
            "browser to. Credits are granted only after Stripe confirms "
            "payment via webhook. Works even if this license is billed "
            "OFFLINE — a Stripe customer is created/reused for the "
            "purchase without changing the license's billing_method.\n"
            "- **School admin**, `payment_method='offline_request'`: "
            "creates a pending request (paying outside Stripe — bank "
            "transfer, invoice, cash) for superadmin review. Nothing is "
            "charged or granted until a superadmin approves it via the "
            "license-overage-offline-requests endpoints.\n"
            "- **Super admin**: grants the blocks immediately with NO "
            "Stripe charge — an administrative grant on behalf of the "
            "school, regardless of `payment_method`.\n\n"
            "All paths share the same request shape: `total_blocks` + "
            "`allocations` (user UUID -> block count), which must sum "
            "exactly to `total_blocks`. Each block grants "
            "`plan.overage_block_size` credits. `success_url`/`cancel_url` "
            "are required only for the school-admin Stripe checkout path. "
            "`allocations` may target any active teacher under this "
            "license, and MAY ALSO target the license's own admin_user "
            "(topping up their fixed analytics allocation) — no other "
            "admin-flagged allocation ever qualifies."
        ),
        request=PurchaseLicenseOverageSerializer,
        responses={200: LicenseOveragePurchaseResultSerializer},
    )
    @action(detail=True, methods=["post"], url_path="purchase-overage")
    def purchase_overage(self, request, pk=None):
        license_sub = self.get_object()

        serializer = PurchaseLicenseOverageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            result = LicenseSubscriptionService.initiate_overage_purchase(
                license_sub,
                requesting_user=request.user,
                total_blocks=d["total_blocks"],
                allocations=d["allocations"],
                success_url=d.get("success_url"),
                cancel_url=d.get("cancel_url"),
                payment_method=d.get("payment_method", "stripe"),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except stripe.error.StripeError as exc:
            logger.exception(
                "Stripe error during license overage purchase for license %s: %s",
                license_sub.id,
                exc,
            )
            return Response(
                {
                    "detail": "Payment failed: "
                    + describe_stripe_error(exc, fallback_message="Please try again.")
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception:
            logger.exception(
                "Unexpected error during license overage purchase for license %s",
                license_sub.id,
            )
            return Response(
                {"detail": "An unexpected error occurred."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if result["action"] == "checkout":
            message = (
                "Redirecting to secure checkout to complete your overage purchase."
            )
        elif result["action"] == "offline_request_pending":
            message = (
                "Your overage request has been submitted for review. "
                "You'll be notified once it's approved or rejected."
            )
        else:
            message = (
                f"Granted {result['total_blocks']} overage block(s) across "
                f"{len(result['allocations'])} teacher(s)."
            )

        return Response(
            LicenseOveragePurchaseResultSerializer({**result, "message": message}).data,
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=["License Subscriptions"],
        summary="Renew an offline-billed license",
        description=(
            "Superadmin-only. Records that an OFFLINE license has been paid "
            "for and renews it: rolls over unused credits for every active "
            "teacher, grants a fresh monthly bucket per teacher, and sets a "
            "new billing_cycle_end. There is no cap on renewing early or "
            "'late' — this is a manual accounting action, not an automatic "
            "one."
        ),
        request=OfflineLicenseRenewalSerializer,
        responses={200: LicenseSubscriptionSerializer},
    )
    @action(detail=True, methods=["post"], url_path="renew-offline")
    def renew_offline(self, request, pk=None):
        license_sub = self.get_object()
        serializer = OfflineLicenseRenewalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        try:
            updated = LicenseSubscriptionService.process_offline_renewal(
                license_sub,
                performed_by=request.user,
                new_billing_cycle_end=d["new_billing_cycle_end"],
                amount_paid_cents=d.get("amount_paid_cents"),
                payment_reference=d.get("payment_reference"),
                payment_method_label=d.get("payment_method_label"),
                notes=d.get("notes"),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(self.get_serializer(updated).data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["License Subscriptions"],
        summary="Convert an offline license to Stripe self-serve billing",
        description=(
            "Superadmin-only. Creates a Stripe Checkout session; the license "
            "is only flipped to STRIPE billing once the checkout.session."
            "completed webhook confirms payment — not on this call."
        ),
        responses={200: OpenApiResponse(description="Returns a checkout_url.")},
    )
    @action(detail=True, methods=["post"], url_path="convert-to-stripe")
    def convert_to_stripe(self, request, pk=None):
        license_sub = self.get_object()

        success_url = (
            request.data.get("success_url")
            or f"https://{settings.FRONTEND_DOMAIN}/billing/license-conversion-success"
        )
        cancel_url = (
            request.data.get("cancel_url")
            or f"https://{settings.FRONTEND_DOMAIN}/billing/license-conversion-cancelled"
        )

        try:
            session = StripeCheckoutService.create_license_conversion_session(
                license_sub,
                initiated_by=request.user,
                success_url=success_url,
                cancel_url=cancel_url,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"checkout_url": session.url}, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["License Subscriptions"],
        summary="Convert a Stripe-billed license to offline billing",
        description=(
            "Superadmin-only. Immediately cancels the Stripe subscription "
            "(no automatic proration refund for unused time) and flips the "
            "license to OFFLINE billing."
        ),
        request=ConvertToOfflineSerializer,
        responses={200: LicenseSubscriptionSerializer},
    )
    @action(detail=True, methods=["post"], url_path="convert-to-offline")
    def convert_to_offline(self, request, pk=None):
        license_sub = self.get_object()
        serializer = ConvertToOfflineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            updated = LicenseSubscriptionService.convert_license_to_offline(
                license_sub,
                performed_by=request.user,
                notes=serializer.validated_data.get("notes"),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(self.get_serializer(updated).data, status=status.HTTP_200_OK)

    @extend_schema(
        tags=["License Subscriptions"],
        summary="Manually grant overage credits to a teacher under this license",
        description=(
            "Superadmin-only. Works for both OFFLINE and STRIPE-billed "
            "licenses — e.g. a goodwill comp, or an offline school's "
            "negotiated extra overage paid for outside Stripe."
        ),
        request=ManualTeacherOverageGrantSerializer,
        responses={201: CreditBucketSerializer},
    )
    @action(detail=True, methods=["post"], url_path="grant-teacher-overage")
    def grant_teacher_overage(self, request, pk=None):
        license_sub = self.get_object()
        serializer = ManualTeacherOverageGrantSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        teacher = get_object_or_404(CustomUser, id=d["teacher_id"])

        try:
            bucket = LicenseSubscriptionService.grant_manual_teacher_overage(
                license_sub,
                teacher,
                d["blocks"],
                performed_by=request.user,
                amount_paid_cents=d.get("amount_paid_cents"),
                payment_reference=d.get("payment_reference"),
                payment_method_label=d.get("payment_method_label"),
                notes=d.get("notes"),
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            CreditBucketSerializer(bucket).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        tags=["License Subscriptions"],
        summary="Add a payment method for self-serve overage purchases",
        description=(
            "For school admins whose license is billed OFFLINE (no Stripe "
            "subscription exists) but who want to self-serve purchase "
            "overage via Stripe. Returns a SetupIntent client_secret for the "
            "frontend to collect a card with Stripe Elements. This does NOT "
            "change the license's billing_method — it only lets Stripe be "
            "used for overage purchases."
        ),
        responses={200: OpenApiResponse(description="Returns a client_secret.")},
    )
    @action(detail=True, methods=["post"], url_path="setup-payment-method")
    def setup_payment_method(self, request, pk=None):
        license_sub = self.get_object()

        try:
            setup_intent = StripeCustomerService.create_license_setup_intent(
                license_sub, request.user
            )
        except stripe.error.StripeError as exc:
            logger.error(
                "Failed to create setup intent for license %s", pk, exc_info=exc
            )
            return Response(
                {
                    "detail": describe_stripe_error(
                        exc,
                        fallback_message=(
                            "We couldn't set up your payment method with our "
                            "payment provider. Please try again."
                        ),
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"client_secret": setup_intent.client_secret}, status=status.HTTP_200_OK
        )

    @extend_schema(
        tags=["License Subscriptions"],
        summary="Get a school's current active license subscription",
        description=(
            "Superadmin-only. Returns the single active LicenseSubscription "
            "for the given school, excluding all expired/past subscriptions. "
            "404 if the school has no active license."
        ),
        parameters=[
            OpenApiParameter(
                name="school",
                type=OpenApiTypes.UUID,
                location=OpenApiParameter.QUERY,
                required=True,
                description="ID of the school to look up.",
            )
        ],
        responses={200: LicenseSubscriptionSerializer},
    )
    @action(detail=False, methods=["get"], url_path="active")
    def active(self, request):
        school_id = request.query_params.get("school")
        if not school_id:
            return Response(
                {"detail": "The 'school' query parameter is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        license_sub = get_object_or_404(
            self.get_queryset(), school_id=school_id, is_active=True
        )
        return Response(self.get_serializer(license_sub).data)

    @extend_schema(
        tags=["License Subscriptions"],
        summary="Billing history for a license",
        description=(
            "Superadmin-only accounting trail: offline creation/renewals, "
            "plan/seat changes, billing-method conversions, and manual "
            "overage grants for this license."
        ),
        responses={200: LicenseBillingRecordSerializer(many=True)},
    )
    @action(detail=True, methods=["get"], url_path="billing-history")
    def billing_history(self, request, pk=None):
        license_sub = self.get_object()
        records = license_sub.billing_records.all()
        return Response(LicenseBillingRecordSerializer(records, many=True).data)


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
    filterset_fields = ["license_subscription", "is_active", "is_admin_allocation"]
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
            admin_schools = School.objects.filter(users=self.request.user)
            return queryset.filter(license_subscription__school__in=admin_schools)

        # Teachers: filter to their own allocation
        return queryset.filter(user=self.request.user)
