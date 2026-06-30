"""
billing/stripe_service.py
==========================
All Stripe-specific business logic lives here, isolated from
SubscriptionService and LicenseSubscriptionService the same way those two
are isolated from each other.

Design principle: Checkout Session creation NEVER grants credits or
creates local subscription rows. That only happens once a webhook confirms
payment (StripeWebhookHandler). This keeps "subscribed" and "paid" as two
separate, auditable steps — closing the gap in the pre-Stripe flow where
SubscriptionManagementViewSet.create()/upgrade() granted credits with no
payment gate at all.

Classes:
- StripeCustomerService    — get-or-create Stripe Customer objects
- StripeCheckoutService    — builds Checkout Sessions for every "pay to
                              unlock" flow (individual subscribe, individual
                              trial, license create)
- StripeSubscriptionMutationService — direct Subscription.modify() for
                              upgrades on an EXISTING Stripe subscription
                              (no Checkout redirect needed; card's already
                              on file)
- StripeOverageService     — explicit, user-confirmed overage block
                              purchases via PaymentIntent
- StripeWebhookHandler     — what to do for each Stripe event type. Called
                              from webhooks.py, which only handles signature
                              verification + idempotency.
"""

import logging

# from django.conf import settings
from django.db import transaction
from django.utils import timezone

from classrooms.models import School
from users.models import CustomUser

from .imports import stripe
from .license_service import LicenseSubscriptionService
from .models import (  # CreditBucket,; CreditBucketType,; CreditLedgerType,
    CreditLedger,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from .services import SubscriptionService

logger = logging.getLogger(__name__)


class StripeCustomerService:
    """Get-or-create the Stripe Customer behind a CreditWallet."""

    @staticmethod
    def get_or_create_customer(user) -> str:
        wallet, _ = CreditWallet.objects.get_or_create(user=user)
        if wallet.stripe_customer_id:
            return wallet.stripe_customer_id

        customer = stripe.Customer.create(
            email=user.email,
            name=user.get_full_name() or user.email,
            metadata={"user_id": str(user.id)},
        )
        wallet.stripe_customer_id = customer.id
        wallet.save(update_fields=["stripe_customer_id", "updated_at"])
        return customer.id


class StripeCheckoutService:
    """
    Builds Stripe Checkout Sessions. Every session carries enough metadata
    for the webhook handler to reconstruct the local action it should take —
    we never trust client-supplied data after redirect, only what Stripe
    echoes back on the confirmed event.
    """

    @staticmethod
    def create_individual_subscribe_session(user, plan, success_url, cancel_url):
        """Paid (non-trial) individual subscription checkout — new subscribers only.
        Existing subscribers changing plans should use
        StripeSubscriptionMutationService.change_plan() instead."""
        if plan.category != PlanCategory.INDIVIDUAL:
            raise ValueError("Checkout subscribe is only valid for INDIVIDUAL plans.")
        if not plan.stripe_price_id:
            raise ValueError(f"Plan {plan.name} has no stripe_price_id configured.")

        existing_active = UserSubscription.objects.filter(
            user=user, is_active=True
        ).exists()
        if existing_active:
            raise ValueError(
                "User already has an active subscription. Use change_plan() to "
                "switch plans instead of creating a new checkout session."
            )

        customer_id = StripeCustomerService.get_or_create_customer(user)

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": plan.stripe_price_id, "quantity": 1}],
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "flow": "individual_subscribe",
                "user_id": str(user.id),
                "plan_id": str(plan.id),
            },
        )
        return session

    @staticmethod
    def create_individual_trial_session(user, plan, success_url, cancel_url):
        """
        14-day free trial — card collected upfront via
        payment_method_collection='always'. The first real charge fires
        automatically when the trial ends; that's handled by
        invoice.payment_succeeded / invoice.payment_failed in the webhook,
        NOT by this method, which only ever creates the trialing session.
        """
        if plan.category != PlanCategory.INDIVIDUAL:
            raise ValueError("Free trials are only available for INDIVIDUAL plans.")
        if not plan.stripe_price_id:
            raise ValueError(f"Plan {plan.name} has no stripe_price_id configured.")

        # Mirror the existing guards from SubscriptionService.activate_free_trial
        # so we fail fast before sending the user to Stripe at all.
        already_trialled = UserSubscription.objects.filter(
            user=user, is_trial=True
        ).exists()
        if already_trialled:
            raise ValueError(
                "This account has already used its free trial. "
                "Please subscribe to a paid plan."
            )

        active_sub = UserSubscription.objects.filter(user=user, is_active=True).exists()
        if active_sub:
            raise ValueError(
                "Cannot start a free trial while an active subscription exists."
            )

        customer_id = StripeCustomerService.get_or_create_customer(user)

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": plan.stripe_price_id, "quantity": 1}],
            subscription_data={
                "trial_period_days": SubscriptionService.TRIAL_DURATION_DAYS,
                "trial_settings": {
                    "end_behavior": {"missing_payment_method": "cancel"},
                },
            },
            payment_method_collection="always",
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "flow": "individual_trial",
                "user_id": str(user.id),
                "plan_id": str(plan.id),
            },
        )
        return session

    @staticmethod
    def create_license_session(
        school,
        plan,
        admin_user,
        contract_months,
        max_seats,
        teacher_emails,
        custom_price_cents,
        success_url,
        cancel_url,
    ):
        """
        License checkout. The LicenseSubscription row does NOT exist yet —
        it's created in the webhook handler after payment, mirroring the
        individual flow. Everything LicenseSubscriptionService.
        create_license_subscription() needs is round-tripped through
        session metadata (Stripe metadata values must be strings).

        NOTE per the agreed default: only call this for self-serve
        Pro/Power license tiers. Custom / is_contact_sales plans should be
        set up manually in the Stripe dashboard and the resulting
        stripe_subscription_id attached directly — don't route those
        through this method.
        """
        if plan.is_contact_sales:
            raise ValueError(
                f"Plan {plan.name} is contact-sales only and must be set up "
                "manually, not through self-serve checkout."
            )

        LicenseSubscriptionService.validate_license_plan(plan)
        LicenseSubscriptionService.validate_admin_user(admin_user, school)

        if custom_price_cents:
            line_item = {
                "price_data": {
                    "currency": "usd",
                    "product": plan.product_id,
                    "recurring": {"interval": "month"},
                    "unit_amount": custom_price_cents,
                },
                "quantity": 1,
            }
        else:
            if not plan.stripe_price_id:
                raise ValueError(f"Plan {plan.name} has no stripe_price_id configured.")
            line_item = {"price": plan.stripe_price_id, "quantity": 1}

        # Reuse the school's existing Stripe customer if they've billed
        # before (e.g. recreating a license after a prior cancellation).
        existing_license = (
            LicenseSubscription.objects.filter(
                school=school, stripe_customer_id__isnull=False
            )
            .order_by("-created_at")
            .first()
        )

        session_kwargs = {
            "mode": "subscription",
            "line_items": [line_item],
            "success_url": success_url,
            "cancel_url": cancel_url,
            "metadata": {
                "flow": "license_create",
                "school_id": str(school.id),
                "plan_id": str(plan.id),
                "admin_user_id": str(admin_user.id),
                "contract_months": str(contract_months),
                "max_seats": str(max_seats),
                "teacher_emails": ",".join(teacher_emails or []),
                "custom_price_cents": (
                    str(custom_price_cents) if custom_price_cents else ""
                ),
            },
        }
        if existing_license:
            session_kwargs["customer"] = existing_license.stripe_customer_id
        else:
            session_kwargs["customer_email"] = admin_user.email

        return stripe.checkout.Session.create(**session_kwargs)


class StripeSubscriptionMutationService:
    """
    For an EXISTING Stripe subscription only (card already on file —
    no Checkout redirect needed). New subscribers go through
    StripeCheckoutService instead.
    """

    @staticmethod
    def change_plan(user_sub, new_plan, proration_behavior="always_invoice"):
        """
        Upgrades an existing paid subscription immediately via
        Stripe.Subscription.modify(). No Checkout redirect is needed since
        the customer's card is already on file.

        With proration_behavior="always_invoice", Stripe immediately
        creates and attempts to pay an invoice for the prorated difference
        as part of the modify() call. Credits for the new plan are granted
        ONLY if that invoice actually gets paid — otherwise the Stripe
        subscription item is reverted back to the old price, so Stripe and
        the app never disagree about which plan the user is actually
        paying for.

        Known limitation: if the proration charge requires 3D Secure
        authentication (payment_intent.status == "requires_action"), this
        method reverts the change and raises rather than completing the
        3DS flow inline. The user needs to retry after re-authenticating
        their payment method. Threading a requires_action response through
        this synchronous call would mean granting credits from a webhook
        instead, which risks double-granting against the synchronous
        success path below — deliberately avoided in favour of a simpler,
        always-consistent state machine. Worth revisiting if 3DS declines
        turn out to be common for your customer base.

        Downgrades should keep using SubscriptionService.schedule_downgrade()
        (deferred to cycle end) — sync_price() applies the Stripe-side price
        change for those, at renewal time, not this method.
        """
        if not user_sub.stripe_subscription_id:
            raise ValueError(
                "This subscription has no associated Stripe subscription. "
                "(it may have been granted manually). Contact support to upgrade."
            )
        if not new_plan.stripe_price_id:
            raise ValueError(f"Plan {new_plan.name} has no stripe_price_id configured.")

        old_plan = user_sub.plan
        stripe_subscription_id = user_sub.stripe_subscription_id

        try:
            stripe_sub = stripe.Subscription.retrieve(user_sub.stripe_subscription_id)
        except stripe.error.StripeError as exc:
            raise ValueError(
                f"Could not retrieve Stripe subscription: {getattr(exc, 'user_message', None) or str(exc)}"
            ) from exc

        item_id = stripe_sub["items"]["data"][0]["id"]
        old_price_id = stripe_sub["items"]["data"][0]["price"]["id"]

        try:
            stripe.Subscription.modify(
                user_sub.stripe_subscription_id,
                items=[{"id": item_id, "price": new_plan.stripe_price_id}],
                proration_behavior=proration_behavior,
            )
        except stripe.error.CardError as exc:
            # Declined synchronously during the modify call itself - rare
            # since the decline usually surfaces on the resulting invoice
            # instead, but Stripe can reject some cards immediately

            raise ValueError(
                f"Card declined: {getattr(exc, 'user_message', None) or str(exc)}"
            ) from exc
        except stripe.error.StripeError as exc:
            raise ValueError(
                f"Stripe error while upgrading: {getattr(exc, 'user_message', None) or str(exc)}"
            ) from exc

        # Re-retrieve to see the invoice Stripe generated as a side effect
        # of the price change. Only present when proration_behavior
        # actually creates one — e.g. no invoice is generated if the two
        # plans happen to be priced identically.
        stripe_sub_refreshed = stripe.Subscription.retrieve(stripe_subscription_id)
        latest_invoice_id = stripe_sub_refreshed.get("latest_invoice")

        if latest_invoice_id:
            invoice = stripe.Invoice.retrieve(
                latest_invoice_id, expand=["payment_intent"]
            )

            if invoice.get("status") != "paid":
                # Payment didn't go through — declined, requires 3DS, etc.
                # Revert the subscription item so Stripe stops billing a
                # price the user never actually paid for, then raise.
                # Credits are NOT granted past this point.

                StripeSubscriptionMutationService._revert_to_previous_price(
                    stripe_subscription_id, item_id, old_price_id, invoice
                )

                payment_intent = invoice.get("payment_intent")

                pi_status = (
                    payment_intent.get("status")
                    if isinstance(payment_intent, dict)
                    else None
                )

                if pi_status == "requires_action":
                    raise ValueError(
                        "Upgrade payment requires additional authentication "
                        "(3D Secure) that can't be completed automatically here. "
                        "Please update your payment method and try again. "
                        "Your plan has not been changed."
                    )

                raise ValueError(
                    f"Upgrade payment failed (invoice status: {invoice['status']}). "
                    "Plan has not been changed."
                )

        # Payment succeeded (or no proration invoice was needed at all) —
        # grant credits for the new plan now
        updated_sub = SubscriptionService.activate_subscription(user_sub.user, new_plan)
        updated_sub.stripe_subscription_id = user_sub.stripe_subscription_id
        updated_sub.stripe_status = StripeSubscriptionStatus.ACTIVE
        updated_sub.save(
            update_fields=["stripe_subscription_id", "stripe_status", "updated_at"]
        )

        logger.info(
            "Upgraded subscription for user %s: %s -> %s (Stripe subscription %s).",
            user_sub.user.email,
            old_plan.name,
            new_plan.name,
            stripe_subscription_id,
        )
        return updated_sub

    @staticmethod
    def _revert_to_previous_price(
        stripe_subscription_id, item_id, old_price_id, invoice
    ):
        """
        Best-effort rollback when a proration invoice didn't get paid: puts
        the Stripe subscription item back on the old price and voids the
        unpaid invoice so it doesn't linger as a dangling charge attempt.
        Logs (rather than raises) on failure here — the caller is already
        mid-error-handling for the original payment failure, and a failed
        rollback shouldn't mask that with a different exception. It does
        mean rare rollback failures need manual reconciliation in Stripe.
        """

        try:
            stripe.Subscription.modify(
                stripe_subscription_id,
                items=[{"id": item_id, "price": old_price_id}],
                proration_behavior="none",
            )
        except stripe.error.StripeError:
            logger.exception(
                "Failed to revert subscription %s to its previous price %s "
                "after a failed upgrade payment. Manual reconciliation needed.",
                stripe_subscription_id,
                old_price_id,
            )

        try:
            if invoice.get("status") == "open":
                stripe.Invoice.void_invoice(invoice["id"])
        except stripe.error.StripeError:
            logger.exception(
                "Failed to void unpaid upgrade invoice %s for subscription %s.",
                invoice.get("id"),
                stripe_subscription_id,
            )

    @staticmethod
    def sync_price(user_sub, stripe_subscription_id, proration_behavior="none"):
        """
        Called after every renewal to make sure Stripe is charging for
        whatever plan the renewal actually resolved to (plan, or
        pending_plan if a downgrade/upgrade was scheduled). The invoice that
        just succeeded was correctly billed at the OLD price — a scheduled
        change should only take effect going forward — so this only updates
        the subscription item for the NEXT cycle, with no proration, no-op
        if the price is already correct.
        """
        if not user_sub.plan.stripe_price_id:
            return

        stripe_sub = stripe.Subscription.retrieve(stripe_subscription_id)
        current_item = stripe_sub["items"]["data"][0]
        if current_item["price"]["id"] == user_sub.plan.stripe_price_id:
            return

        stripe.Subscription.modify(
            stripe_subscription_id,
            items=[{"id": current_item["id"], "price": user_sub.plan.stripe_price_id}],
            proration_behavior=proration_behavior,
        )
        logger.info(
            "Synced Stripe price for subscription %s to plan %s.",
            stripe_subscription_id,
            user_sub.plan.name,
        )


class StripeOverageService:
    """Explicit, user-confirmed overage block purchases."""

    @staticmethod
    def purchase_overage_block(user):
        """
        Charges the customer's default payment method synchronously via a
        PaymentIntent. The user is present and just clicked "buy" — this is
        an on-session charge, so there's no off_session decline risk to
        design around, unlike an automatic background top-up would have.
        """
        wallet = CreditWallet.objects.select_for_update().get(user=user)
        user_sub = (
            user.subscriptions.filter(is_active=True).select_related("plan").first()
        )
        if not user_sub:
            raise ValueError("No active subscription found.")

        plan = user_sub.plan
        if wallet.overage_blocks_used >= plan.max_overage_blocks:
            raise ValueError("Maximum overage blocks reached for this billing cycle.")

        if not wallet.stripe_customer_id:
            raise ValueError("No Stripe customer on file for this user.")

        # Checkout (subscription mode) saves the card as the SUBSCRIPTION's
        # default_payment_method, not the Customer's invoice_settings —
        # those are two different fields. Check the subscription first;
        # only fall back to the customer-level default if the subscription
        # doesn't have one set (e.g. it was set manually some other way).
        default_pm = None
        if user_sub.stripe_subscription_id:
            stripe_sub = stripe.Subscription.retrieve(user_sub.stripe_subscription_id)
            default_pm = stripe_sub.get("default_payment_method")

        if not default_pm:
            customer = stripe.Customer.retrieve(wallet.stripe_customer_id)
            default_pm = (customer.get("invoice_settings") or {}).get(
                "default_payment_method"
            )

        if not default_pm:
            raise ValueError(
                "No default payment method on file. Add a card before "
                "purchasing overage credits."
            )

        intent = stripe.PaymentIntent.create(
            amount=plan.overage_block_price,
            currency="usd",
            customer=wallet.stripe_customer_id,
            payment_method=default_pm,
            confirm=True,
            automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
            metadata={
                "flow": "overage_block_purchase",
                "user_id": str(user.id),
                "wallet_id": str(wallet.id),
            },
        )

        if intent.status == "succeeded":
            # Grant the overage bucket atomically
            bucket = SubscriptionService.grant_overage_bucket(
                wallet, user_sub, intent.id
            )
            return {"status": "succeeded", "bucket": bucket}

        if intent.status == "requires_action":
            # 3DS or similar. The bucket is granted by the
            # payment_intent.succeeded webhook once authentication
            # completes, not here.
            return {"status": "requires_action", "client_secret": intent.client_secret}

        raise ValueError(f"Payment could not be completed (status: {intent.status}).")


class StripeWebhookHandler:
    """
    What to do for each Stripe event type. Called from webhooks.py, which
    only handles signature verification + idempotency — no business logic
    there, so this stays testable without a fake HTTP request.
    """

    # ------------------------------------------------------------------
    # checkout.session.completed
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def handle_checkout_completed(session):
        metadata = session.get("metadata", {}) or {}
        flow = metadata.get("flow")

        if flow == "individual_subscribe":
            StripeWebhookHandler._handle_individual_subscribe(session, metadata)
        elif flow == "individual_trial":
            StripeWebhookHandler._handle_individual_trial(session, metadata)
        elif flow == "license_create":
            StripeWebhookHandler._handle_license_create(session, metadata)
        else:
            logger.warning(
                "checkout.session.completed with unrecognized flow metadata: %r", flow
            )

    @staticmethod
    def _handle_individual_subscribe(session, metadata):
        user = CustomUser.objects.get(id=metadata["user_id"])
        plan = SubscriptionPlan.objects.get(id=metadata["plan_id"])

        subscription = SubscriptionService.activate_subscription(user, plan)
        subscription.stripe_subscription_id = session["subscription"]
        subscription.stripe_status = StripeSubscriptionStatus.ACTIVE
        subscription.save(
            update_fields=["stripe_subscription_id", "stripe_status", "updated_at"]
        )
        logger.info(
            "Stripe checkout completed: individual subscribe for user %s, plan %s.",
            user.email,
            plan.name,
        )

    @staticmethod
    def _handle_individual_trial(session, metadata):
        user = CustomUser.objects.get(id=metadata["user_id"])
        plan = SubscriptionPlan.objects.get(id=metadata["plan_id"])

        trial_sub = SubscriptionService.activate_free_trial(user, plan)
        trial_sub.stripe_subscription_id = session["subscription"]
        trial_sub.stripe_status = StripeSubscriptionStatus.TRIALING
        trial_sub.save(
            update_fields=["stripe_subscription_id", "stripe_status", "updated_at"]
        )
        logger.info(
            "Stripe checkout completed: trial started for user %s, plan %s.",
            user.email,
            plan.name,
        )

    @staticmethod
    def _handle_license_create(session, metadata):
        school = School.objects.get(id=metadata["school_id"])
        plan = SubscriptionPlan.objects.get(id=metadata["plan_id"])
        admin_user = CustomUser.objects.get(id=metadata["admin_user_id"])
        contract_months = int(metadata["contract_months"])
        max_seats = int(metadata["max_seats"])
        teacher_emails = [e for e in metadata.get("teacher_emails", "").split(",") if e]
        custom_price_cents = (
            int(metadata["custom_price_cents"])
            if metadata.get("custom_price_cents")
            else None
        )

        license_sub = LicenseSubscriptionService.create_license_subscription(
            school=school,
            plan=plan,
            admin_user=admin_user,
            teacher_emails=teacher_emails,
            contract_months=contract_months,
            max_seats=max_seats,
            custom_price_cents=custom_price_cents,
        )
        license_sub.stripe_subscription_id = session["subscription"]
        license_sub.stripe_customer_id = session["customer"]
        license_sub.stripe_status = StripeSubscriptionStatus.ACTIVE
        license_sub.save(
            update_fields=[
                "stripe_subscription_id",
                "stripe_customer_id",
                "stripe_status",
                "updated_at",
            ]
        )
        logger.info(
            "Stripe checkout completed: license created for school %s, plan %s.",
            school.name,
            plan.name,
        )

    # ------------------------------------------------------------------
    # invoice.payment_succeeded
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def handle_invoice_payment_succeeded(invoice):
        stripe_subscription_id = invoice.get("subscription")
        if not stripe_subscription_id:
            return  # one-off invoice item, not a subscription cycle

        billing_reason = invoice.get("billing_reason")

        user_sub = (
            UserSubscription.objects.filter(
                stripe_subscription_id=stripe_subscription_id
            )
            .select_for_update()
            .first()
        )
        if user_sub:
            StripeWebhookHandler._handle_individual_invoice_succeeded(
                user_sub, billing_reason
            )
            return

        license_sub = (
            LicenseSubscription.objects.filter(
                stripe_subscription_id=stripe_subscription_id
            )
            .select_for_update()
            .first()
        )
        if license_sub:
            # Monthly charge succeeded — purely a payment-health signal.
            # Per-teacher credit allocation/rollover for licenses is driven
            # by billing_cycle_end/contract_months in
            # process_license_renewals, NOT by every monthly Stripe
            # invoice. Do not grant credits here.
            license_sub.stripe_status = StripeSubscriptionStatus.ACTIVE
            license_sub.save(update_fields=["stripe_status", "updated_at"])
            logger.info("License %s monthly Stripe charge succeeded.", license_sub.id)
            return

        logger.warning(
            "invoice.payment_succeeded for unrecognized stripe_subscription_id %s",
            stripe_subscription_id,
        )

    @staticmethod
    def _handle_individual_invoice_succeeded(user_sub, billing_reason):
        if billing_reason != "subscription_cycle":
            # subscription_create is already handled by
            # checkout.session.completed; subscription_update (immediate
            # upgrade proration) is handled synchronously in
            # StripeSubscriptionMutationService.change_plan(). Nothing
            # further to do here for either.
            return

        now = timezone.now()
        stripe_subscription_id = user_sub.stripe_subscription_id

        # Idempotency guard: if already renewed (billing_cycle_end > now), just update status
        if user_sub.billing_cycle_end > now:
            user_sub.stripe_status = StripeSubscriptionStatus.ACTIVE
            user_sub.save(update_fields=["stripe_status", "updated_at"])

            # Ensure the Stripe price is in sync (if a pending plan exist)
            StripeSubscriptionMutationService.sync_price(
                user_sub, stripe_subscription_id
            )
            return

        if user_sub.is_trial:
            # Trial just ended and the first real charge succeeded.
            updated_sub = SubscriptionService.finalize_trial_conversion_via_stripe(
                user_sub
            )
        else:
            # Normal monthly renewal. activate_subscription() (called inside
            # process_rollover_and_renewal) creates a NEW UserSubscription
            # row and deactivates this one — the Stripe subscription id has
            # to be re-attached to the new row, not this (now inactive) one.

            updated_sub = SubscriptionService.process_rollover_and_renewal(user_sub)
            # If schedule_downgrade() (or an upgrade) set a different plan
            # than what Stripe is currently billing, sync it now — this
            # invoice was correctly billed at the old price, so the change
            # only applies going forward.
            StripeSubscriptionMutationService.sync_price(
                updated_sub, stripe_subscription_id
            )

        updated_sub.stripe_subscription_id = stripe_subscription_id
        updated_sub.stripe_status = StripeSubscriptionStatus.ACTIVE
        updated_sub.save(
            update_fields=["stripe_subscription_id", "stripe_status", "updated_at"]
        )

    # ------------------------------------------------------------------
    # invoice.payment_failed
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def handle_invoice_payment_failed(invoice):
        stripe_subscription_id = invoice.get("subscription")
        if not stripe_subscription_id:
            return

        user_sub = (
            UserSubscription.objects.filter(
                stripe_subscription_id=stripe_subscription_id
            )
            .select_for_update()
            .first()
        )
        if user_sub:
            if user_sub.is_trial:
                # Card declined at trial end — mirrors expire_trial(), no conversion.
                SubscriptionService.expire_trial(user_sub)
            else:
                user_sub.stripe_status = StripeSubscriptionStatus.PAST_DUE
                user_sub.save(update_fields=["stripe_status", "updated_at"])
            return

        license_sub = (
            LicenseSubscription.objects.filter(
                stripe_subscription_id=stripe_subscription_id
            )
            .select_for_update()
            .first()
        )
        if license_sub:
            license_sub.stripe_status = StripeSubscriptionStatus.PAST_DUE
            license_sub.save(update_fields=["stripe_status", "updated_at"])

    # ------------------------------------------------------------------
    # customer.subscription.deleted
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def handle_subscription_deleted(stripe_subscription):
        stripe_subscription_id = stripe_subscription["id"]

        user_sub = (
            UserSubscription.objects.filter(
                stripe_subscription_id=stripe_subscription_id, is_active=True
            )
            .select_for_update()
            .first()
        )
        if user_sub:
            if user_sub.is_trial:
                if user_sub.trial_end and user_sub.trial_end > timezone.now():
                    # Cancelled mid-trial, before natural expiry —
                    # expire_trial() would reject this since it requires
                    # trial_end to have already passed, so deactivate directly.
                    user_sub.is_active = False
                    user_sub.is_trial = False
                    user_sub.save(update_fields=["is_active", "is_trial", "updated_at"])
                else:
                    SubscriptionService.expire_trial(user_sub)
            else:
                user_sub.is_active = False
                user_sub.stripe_status = StripeSubscriptionStatus.CANCELED
                user_sub.save(
                    update_fields=["is_active", "stripe_status", "updated_at"]
                )
            return

        license_sub = (
            LicenseSubscription.objects.filter(
                stripe_subscription_id=stripe_subscription_id, is_active=True
            )
            .select_for_update()
            .first()
        )
        if license_sub:
            license_sub.is_active = False
            license_sub.stripe_status = StripeSubscriptionStatus.CANCELED
            license_sub.save(update_fields=["is_active", "stripe_status", "updated_at"])

    # ------------------------------------------------------------------
    # payment_intent.succeeded (overage block fallback for 3DS / requires_action)
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def handle_payment_intent_succeeded(payment_intent):
        metadata = payment_intent.get("metadata", {}) or {}
        if metadata.get("flow") != "overage_block_purchase":
            return

        # Idempotency against the synchronous path in StripeOverageService,
        # which already grants the bucket when no further action is needed.
        # This handler is only the fallback for the requires_action case.
        already_granted = CreditLedger.objects.filter(
            metadata__stripe_payment_intent_id=payment_intent["id"]
        ).exists()
        if already_granted:
            return

        user = CustomUser.objects.get(id=metadata["user_id"])
        wallet = user.credit_wallet
        user_sub = (
            user.subscriptions.filter(is_active=True).select_related("plan").first()
        )
        if user_sub:
            SubscriptionService.grant_overage_bucket(
                wallet, user_sub, payment_intent["id"]
            )
