"""
billing/webhooks.py
====================
Single entrypoint for all incoming Stripe webhook events.

Responsibilities of THIS file, and only this file:
1. Verify the Stripe signature against the raw request body.
2. Enforce idempotency via the StripeEvent ledger — Stripe redelivers
   events on timeout/ambiguous response/manual retry, so this MUST NOT
   be skipped. None of the handlers in stripe_service.py are safe to run
   twice for the same event (they create CreditBucket/CreditLedger rows).
3. Dispatch to the appropriate handler in stripe_service.py.

No business logic lives here — see StripeWebhookHandler in
stripe_service.py for that, so it stays testable without a fake HTTP
request.

Wire this up in urls.py with CSRF exempt (already handled here via the
decorator) and make sure no auth middleware tries to authenticate this
request as a normal user — Stripe calls it unauthenticated, signature
verification IS the auth.
"""

import logging

from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .imports import stripe
from .models import StripeEvent
from .stripe_service import StripeWebhookHandler

logger = logging.getLogger(__name__)

# Single dispatch table shared by BOTH webhook endpoints. This used to be
# duplicated inline in stripe_webhook and thin_webhook, which meant every
# new event type had to be wired in two places or it silently no-op'd on
# one of them.
_EVENT_HANDLERS = {
    "checkout.session.completed": StripeWebhookHandler.handle_checkout_completed,
    "invoice.payment_succeeded": StripeWebhookHandler.handle_invoice_payment_succeeded,
    "invoice.payment_failed": StripeWebhookHandler.handle_invoice_payment_failed,
    "customer.subscription.deleted": StripeWebhookHandler.handle_subscription_deleted,
    "charge.refunded": StripeWebhookHandler.handle_charge_refunded,
    "payment_intent.succeeded": StripeWebhookHandler.handle_payment_intent_succeeded,
    "payment_intent.payment_failed": StripeWebhookHandler.handle_payment_intent_failed,
    "setup_intent.succeeded": StripeWebhookHandler.handle_setup_intent_succeeded,
}


def _record_and_dispatch(event, *, log_prefix):
    """
    Shared idempotency + dispatch core for both webhook endpoints.

    Records the event id BEFORE processing (get_or_create is atomic at the
    DB level, so two near-simultaneous redeliveries can't both pass), then
    dispatches to the handler. On handler failure the StripeEvent record is
    removed and 500 returned so Stripe's retry is processed as a fresh
    delivery instead of being dropped as a duplicate forever.
    """
    _, created = StripeEvent.objects.get_or_create(
        stripe_event_id=event["id"],
        defaults={"event_type": event["type"], "payload": event["data"]},
    )
    if not created:
        logger.info(
            "%s: duplicate delivery of event %s, skipping.", log_prefix, event["id"]
        )
        return HttpResponse(status=200)

    event_type = event["type"]
    handler = _EVENT_HANDLERS.get(event_type)

    try:
        if handler is not None:
            handler(event["data"]["object"])
        else:
            logger.debug("%s: unhandled event type %s.", log_prefix, event_type)
    except Exception:
        StripeEvent.objects.filter(stripe_event_id=event["id"]).delete()
        logger.exception(
            "%s: error processing event %s (%s).",
            log_prefix,
            event["id"],
            event_type,
        )
        return HttpResponse(status=500)

    return HttpResponse(status=200)


@csrf_exempt
@require_POST
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        logger.warning("Stripe webhook: invalid payload.")
        return HttpResponseBadRequest("Invalid payload")
    except stripe.error.SignatureVerificationError:
        logger.warning("Stripe webhook: signature verification failed.")
        return HttpResponseBadRequest("Invalid signature")

    return _record_and_dispatch(event, log_prefix="Stripe webhook")


@csrf_exempt
@require_POST
def thin_webhook(request):
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")

    try:
        # Verify the signature
        thin_notification = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        logger.warning("Stripe thin webhook: invalid payload.")
        return HttpResponseBadRequest("Invalid payload")
    except stripe.error.SignatureVerificationError:
        logger.warning("Stripe thin webhook: signature verification failed.")
        return HttpResponseBadRequest("Invalid signature")

    event_id = thin_notification["id"]

    # Fetch the full event from Stripe API using the ID
    try:
        event = stripe.Event.retrieve(event_id)
    except Exception:
        logger.exception("Stripe thin webhook: failed to retrieve event %s", event_id)
        return HttpResponseBadRequest("Failed to retrieve event")

    return _record_and_dispatch(event, log_prefix="Stripe thin webhook")
