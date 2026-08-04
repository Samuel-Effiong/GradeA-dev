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

from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers, status, viewsets
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
            "billing context are rejected (403).\n\n"
            "This is a **live** read straight from Stripe on every call — "
            "nothing is cached locally. After `POST /payment-methods/` "
            "succeeds (see below), poll this endpoint until the new card "
            "shows up rather than assuming it's there immediately."
        ),
        responses={
            200: OpenApiResponse(
                response=PaymentMethodSerializer(many=True),
                description="Cards on file, in Stripe's default list order.",
                examples=[
                    OpenApiExample(
                        "Two cards, one default",
                        value=[
                            {
                                "id": "pm_1NxA2bCcDefault0001",
                                "brand": "visa",
                                "last4": "4242",
                                "exp_month": 12,
                                "exp_year": 2027,
                                "is_default": True,
                            },
                            {
                                "id": "pm_1NxA2bCcSpare0002",
                                "brand": "mastercard",
                                "last4": "4444",
                                "exp_month": 3,
                                "exp_year": 2026,
                                "is_default": False,
                            },
                        ],
                        response_only=True,
                    ),
                    OpenApiExample(
                        "No cards on file",
                        value=[],
                        response_only=True,
                    ),
                ],
            ),
            403: OpenApiResponse(
                description=(
                    "No billing context (e.g. a license teacher, or a user "
                    "with no subscription/license at all)."
                ),
                examples=[
                    OpenApiExample(
                        "License teacher",
                        value={
                            "detail": (
                                "Teachers don't manage billing directly — "
                                "payment methods are managed by your "
                                "school's license admin."
                            )
                        },
                        response_only=True,
                    )
                ],
            ),
        },
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
        description="""
Starts adding a **new** card. This does NOT attach a card by itself — there
is no "update an existing card" concept in Stripe (PCI rules mean card
numbers can never be edited in place), so every add always results in a
brand-new `PaymentMethod` object once completed, never a modification of one
that already exists.

**Flow (frontend must complete all 4 steps — this endpoint only does #1)**
1. Call this endpoint → receive `client_secret`.
2. Use Stripe Elements / `stripe.js` (`stripe.confirmCardSetup(client_secret, ...)`)
   client-side to collect the card and confirm the SetupIntent.
3. Stripe attaches the card to the customer and fires `setup_intent.succeeded`
   to our webhook — this is what actually makes the card appear on the
   customer, not step 1 or step 2 alone.
4. Poll `GET /payment-methods/` (or re-fetch) until the new card shows up.
   Do not assume the card exists just because step 2's client-side confirm
   call resolved — the webhook is the source of truth and may lag slightly.

**`set_as_default`**
Pass `true` to make this card the customer's default once it's attached;
omit or pass `false` to leave the existing default untouched. This choice is
only applied by the webhook after Stripe confirms the SetupIntent, same as
the card attachment itself.

**Repeated calls create repeated cards.** Calling this endpoint twice (e.g.
a retried or resubmitted form) and completing both SetupIntents produces
*two* separate cards on the customer — there is no dedup against
already-existing cards with the same number. Remove unwanted duplicates
with `DELETE /payment-methods/{id}/`.
""",
        request=inline_serializer(
            name="AddPaymentMethodRequest",
            fields={
                "set_as_default": serializers.BooleanField(
                    required=False,
                    default=False,
                    help_text=(
                        "If true, this card becomes the customer's default "
                        "payment method once the SetupIntent succeeds. "
                        "Defaults to false (existing default untouched)."
                    ),
                ),
            },
        ),
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="AddPaymentMethodResponse",
                    fields={
                        "client_secret": serializers.CharField(
                            help_text=(
                                "Pass to stripe.js "
                                "(stripe.confirmCardSetup) to collect the "
                                "card and complete the SetupIntent."
                            )
                        ),
                    },
                ),
                description=(
                    "SetupIntent created. No card exists yet — confirm it "
                    "client-side with stripe.js, then poll "
                    "GET /payment-methods/."
                ),
                examples=[
                    OpenApiExample(
                        "SetupIntent created",
                        value={"client_secret": "seti_1NxA2b_secret_a1b2c3d4e5"},
                        response_only=True,
                    )
                ],
            ),
            400: OpenApiResponse(
                description="Stripe rejected the SetupIntent creation.",
            ),
            403: OpenApiResponse(
                description=(
                    "No billing context (license teacher, or no "
                    "subscription/license at all)."
                ),
            ),
        },
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
            "Detaches a card (`pk` = Stripe PaymentMethod ID, e.g. "
            "`pm_1NxA2bCcDefault0001`) from the requesting user's resolved "
            "Stripe customer. Takes effect immediately and synchronously — "
            "unlike add, there is no webhook step to wait for.\n\n"
            "Blocked if this is the customer's ONLY card and an active "
            "Stripe-billed subscription depends on it — add another card "
            "first via `POST /payment-methods/`. Does not auto-promote "
            "another card to default; if the deleted card was the default, "
            "the customer is simply left with no default until one is "
            "explicitly set via `set-default`."
        ),
        responses={
            204: OpenApiResponse(description="Payment method removed."),
            400: OpenApiResponse(
                description=(
                    "This is the customer's only card and an active "
                    "auto-renewing Stripe subscription depends on it."
                ),
                examples=[
                    OpenApiExample(
                        "Last card blocked",
                        value={
                            "detail": (
                                "This is your only payment method and your "
                                "subscription renews automatically. Add "
                                "another card before removing this one."
                            )
                        },
                        response_only=True,
                    )
                ],
            ),
            403: OpenApiResponse(description="No billing context for this user."),
            404: OpenApiResponse(
                description=(
                    "`pk` doesn't exist on Stripe, or belongs to a "
                    "different customer than the caller's."
                ),
                examples=[
                    OpenApiExample(
                        "Not owned by caller",
                        value={
                            "detail": (
                                "That payment method does not belong to "
                                "your account."
                            )
                        },
                        response_only=True,
                    )
                ],
            ),
        },
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
        description=(
            "Sets `pk` (Stripe PaymentMethod ID) as the customer's default "
            "`invoice_settings.default_payment_method`. Synchronous — takes "
            "effect immediately, no webhook involved. The card must already "
            "be attached to the caller's resolved customer (i.e. it must "
            "show up in `GET /payment-methods/` first)."
        ),
        responses={
            204: OpenApiResponse(description="Default payment method updated."),
            400: OpenApiResponse(description="Stripe rejected the update."),
            403: OpenApiResponse(description="No billing context for this user."),
            404: OpenApiResponse(
                description=(
                    "`pk` doesn't exist on Stripe, or belongs to a "
                    "different customer than the caller's."
                ),
            ),
        },
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
