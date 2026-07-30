"""
billing/payment_method_views.py
================================
PaymentMethodViewSet — list/add/delete/set-default for the cards
attached to whichever Stripe customer belongs to the requesting user's
CURRENT billing context (individual subscriber or license admin — never
both at once, and license teachers never manage billing directly).

Kept in a separate file to avoid bloating billing/views.py or
billing/license_views.py with yet another unrelated concern, mirroring
billing/license_overage_offline_views.py's precedent.

Not model-backed — every response here reflects LIVE Stripe state, read
on each request rather than cached/duplicated locally.
"""

import logging

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from AutoGrader.error_messages import describe_stripe_error
from classrooms.permissions import IsNotStudent

from .imports import stripe
from .models import LicenseBillingMethod, StripeSubscriptionStatus
from .serializers import PaymentMethodSerializer
from .stripe_service import StripeCustomerService
from .subscription_resolver import (
    SOURCE_INDIVIDUAL,
    SOURCE_LICENSE_ADMIN,
    resolve_user_billing_context,
)

logger = logging.getLogger(__name__)


class PaymentMethodViewSet(viewsets.ViewSet):
    """
    Manages cards on the requesting user's own resolved Stripe customer.
    Only list/create/destroy + a custom set-default action are defined —
    there is no "retrieve one" or "update" concept for a payment method
    here, so those simply aren't wired up.
    """

    permission_classes = [IsAuthenticated, IsNotStudent]

    @staticmethod
    def _forbidden(exc: ValueError) -> Response:
        return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)

    @staticmethod
    def _stripe_error_response(exc, fallback_message: str) -> Response:
        return Response(
            {"detail": describe_stripe_error(exc, fallback_message=fallback_message)},
            status=status.HTTP_400_BAD_REQUEST,
        )

    def _get_owned_payment_method(self, customer_id: str, pm_id: str):
        """
        Retrieves a PaymentMethod and verifies it belongs to customer_id
        before letting a caller act on it — Stripe will happily return
        (or detach) ANY payment method ID passed to it, so ownership must
        be checked here rather than trusted from the URL.

        Returns:
            (payment_method, None) on success, or (None, error_response)
            if the lookup failed or ownership doesn't match.
        """
        try:
            payment_method = stripe.PaymentMethod.retrieve(pm_id)
        except stripe.error.StripeError as exc:
            return None, self._stripe_error_response(
                exc, "We couldn't find that payment method."
            )

        if payment_method.customer != customer_id:
            return None, Response(
                {"detail": "That payment method does not belong to your account."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return payment_method, None

    @staticmethod
    def _blocks_last_card_deletion(context, customer_id: str, pm_id: str) -> bool:
        """
        Only the customer's LAST card, while an active Stripe-billed
        subscription depends on it, is protected from deletion — leaving
        an auto-renewing subscription with no card at all would fail
        silently at the next renewal attempt. Any other deletion
        (spare card, or no active Stripe subscription, e.g. an
        OFFLINE-billed license) is allowed freely.
        """
        has_active_stripe_subscription = False
        if context.source == SOURCE_INDIVIDUAL:
            sub = context.user_subscription
            has_active_stripe_subscription = bool(
                sub
                and sub.stripe_subscription_id
                and sub.stripe_status == StripeSubscriptionStatus.ACTIVE
            )
        elif context.source == SOURCE_LICENSE_ADMIN:
            license_sub = context.license_subscription
            has_active_stripe_subscription = bool(
                license_sub
                and license_sub.billing_method == LicenseBillingMethod.STRIPE
                and license_sub.auto_renew
                and license_sub.stripe_subscription_id
            )

        if not has_active_stripe_subscription:
            return False

        cards = stripe.PaymentMethod.list(customer=customer_id, type="card").data
        return len(cards) <= 1 and any(card.id == pm_id for card in cards)

    @extend_schema(
        tags=["Payment Methods"],
        summary="List payment methods on file",
        description=(
            "Lists the cards attached to the requesting user's resolved "
            "Stripe customer — their own individual customer if they're an "
            "individually-billed subscriber, or their school's customer if "
            "they're a license admin. License teachers and users with no "
            "billing context are rejected (403)."
        ),
        responses={200: PaymentMethodSerializer(many=True)},
    )
    def list(self, request):
        try:
            customer_id = StripeCustomerService.get_customer_for_request_user(
                request.user
            )
        except ValueError as exc:
            return self._forbidden(exc)

        try:
            payment_methods = stripe.PaymentMethod.list(
                customer=customer_id, type="card"
            )
            customer = stripe.Customer.retrieve(customer_id)
        except stripe.error.StripeError as exc:
            logger.exception(
                "Failed to list payment methods for customer %s", customer_id
            )
            return self._stripe_error_response(
                exc, "We couldn't load your payment methods. Please try again."
            )

        default_pm_id = (customer.get("invoice_settings") or {}).get(
            "default_payment_method"
        )
        if hasattr(default_pm_id, "id"):
            default_pm_id = default_pm_id.id

        data = [
            {
                "id": pm.id,
                "brand": pm.card.brand,
                "last4": pm.card.last4,
                "exp_month": pm.card.exp_month,
                "exp_year": pm.card.exp_year,
                "is_default": pm.id == default_pm_id,
            }
            for pm in payment_methods.auto_paging_iter()
        ]
        return Response(
            PaymentMethodSerializer(data, many=True).data, status=status.HTTP_200_OK
        )

    @extend_schema(
        tags=["Payment Methods"],
        summary="Add a payment method",
        description=(
            "Creates a SetupIntent for the requesting user's resolved Stripe "
            "customer and returns a client_secret for the frontend to "
            "collect a card with Stripe Elements. The card is only actually "
            "attached once Stripe confirms the SetupIntent succeeded (via "
            "webhook) — nothing changes here synchronously. Pass "
            "set_as_default=true to make this the customer's default "
            "payment method once added; omitted/false leaves the existing "
            "default untouched."
        ),
        request=OpenApiResponse(description="{ set_as_default?: bool }"),
        responses={200: OpenApiResponse(description="Returns a client_secret.")},
    )
    def create(self, request):
        set_as_default = bool(request.data.get("set_as_default", False))

        try:
            setup_intent = StripeCustomerService.create_setup_intent_for_request_user(
                request.user, set_as_default=set_as_default
            )
        except ValueError as exc:
            return self._forbidden(exc)
        except stripe.error.StripeError as exc:
            logger.exception(
                "Failed to create setup intent for user %s", request.user.id
            )
            return self._stripe_error_response(
                exc, "We couldn't set up your payment method. Please try again."
            )

        return Response(
            {"client_secret": setup_intent.client_secret}, status=status.HTTP_200_OK
        )

    @extend_schema(
        tags=["Payment Methods"],
        summary="Remove a payment method",
        description=(
            "Detaches a card from the requesting user's resolved Stripe "
            "customer. Blocked if this is the customer's ONLY card and an "
            "active Stripe-billed subscription depends on it — add another "
            "card first. Does not auto-promote another card to default; "
            "if the deleted card was the default, the customer is simply "
            "left with no default until one is explicitly set."
        ),
        responses={204: OpenApiResponse(description="Payment method removed.")},
    )
    def destroy(self, request, pk=None):
        try:
            customer_id = StripeCustomerService.get_customer_for_request_user(
                request.user
            )
        except ValueError as exc:
            return self._forbidden(exc)

        _, error_response = self._get_owned_payment_method(customer_id, pk)
        if error_response is not None:
            return error_response

        context = resolve_user_billing_context(request.user)
        if self._blocks_last_card_deletion(context, customer_id, pk):
            return Response(
                {
                    "detail": (
                        "This is your only payment method and your "
                        "subscription renews automatically. Add another "
                        "card before removing this one."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            stripe.PaymentMethod.detach(pk)
        except stripe.error.StripeError as exc:
            logger.exception(
                "Failed to detach payment method %s for customer %s",
                pk,
                customer_id,
            )
            return self._stripe_error_response(
                exc, "We couldn't remove that card. Please try again."
            )

        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        tags=["Payment Methods"],
        summary="Set a payment method as default",
        responses={204: OpenApiResponse(description="Default payment method updated.")},
    )
    @action(detail=True, methods=["post"], url_path="set-default")
    def set_default(self, request, pk=None):
        try:
            customer_id = StripeCustomerService.get_customer_for_request_user(
                request.user
            )
        except ValueError as exc:
            return self._forbidden(exc)

        _, error_response = self._get_owned_payment_method(customer_id, pk)
        if error_response is not None:
            return error_response

        try:
            stripe.Customer.modify(
                customer_id, invoice_settings={"default_payment_method": pk}
            )
        except stripe.error.StripeError as exc:
            logger.exception(
                "Failed to set default payment method %s for customer %s",
                pk,
                customer_id,
            )
            return self._stripe_error_response(
                exc,
                "We couldn't update your default payment method. Please try again.",
            )

        return Response(status=status.HTTP_204_NO_CONTENT)
