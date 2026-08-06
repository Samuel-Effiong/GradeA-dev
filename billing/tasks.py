"""
billing/tasks.py
================
Celery tasks for the billing pipeline.

Three independent tasks — each with a single, well-defined responsibility:

1. process_subscription_renewals
   Handles INDIVIDUAL UserSubscription renewals and trial expiry.
   Runs nightly (recommended: every hour so billing_cycle_end is caught promptly).

2. process_license_renewals
   Handles LicenseSubscription renewals for institutional plans.
   Completely separate from the individual pipeline.
   Runs nightly (recommended: same cadence as process_subscription_renewals).

3. cleanup_expired_credit_buckets
   Formalizes expired CreditBucket entries in the ledger.
   Runs nightly after the two renewal tasks.
"""

import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .imports import stripe
from .license_service import (
    LicenseSubscriptionService,
    sync_teachers_under_license_to_mailerlite,
)
from .models import (
    BillingInterval,
    CreditBucket,
    CreditWallet,
    LicenseBillingMethod,
    LicenseSubscription,
    SchoolCreditAllocation,
    StripeSubscriptionStatus,
    UserSubscription,
)
from .services import SubscriptionService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=0)
def process_subscription_renewals(self):
    """
    Process expired trials for INDIVIDUAL subscriptions.
    Paid renewals are handled by Stripe webhooks.
    """
    now = timezone.now()

    # Only fetch trials that have ended.
    expired_trials = UserSubscription.objects.filter(
        is_active=True,
        is_trial=True,
        billing_cycle_end__lte=now,
    ).select_related("user", "plan")

    trial_expired_count = 0
    failed_count = 0

    for sub in expired_trials:
        try:
            # expire_trial() writes the EXPIRE ledger entry and deactivates the sub
            SubscriptionService.expire_trial(sub)
            trial_expired_count += 1
            logger.info(
                "Trial expired for user %s (subscription %s).",
                sub.user.email,
                sub.id,
            )
        except Exception as exc:
            failed_count += 1
            logger.error(
                f"Failed to expire trial {sub.id} for user {sub.user.email}: {str(exc)}",
                exc_info=True,
            )
            # Continue — one bad subscription must not block the rest.

    summary = (
        f"Individual trial subscriptions processed: "
        f"{trial_expired_count} trials expired, "
        f"{failed_count} failed."
    )
    logger.info(summary)
    return summary


@shared_task(bind=True, max_retries=0)
def process_license_renewals(self):
    """
    Daily fallback for license renewals.
    - For auto_renew=True: checks Stripe invoice status; if paid, renews.
    - For auto_renew=False: deactivates and cancels Stripe subscription.
    """
    now = timezone.now()

    # Only fetch licenses whose cycle has genuinely ended.
    # select_related("school", "plan") prevents N+1 on logging and validation
    # inside process_license_renewal.
    expired_licenses = (
        LicenseSubscription.objects.filter(
            is_active=True,
            billing_cycle_end__lte=now,
        )
        .exclude(billing_method=LicenseBillingMethod.OFFLINE)
        .select_related("school", "plan")
    )

    renewed_count = 0
    deactivated_count = 0
    skipped_not_paid = 0
    failed_count = 0

    for license_sub in expired_licenses:
        try:
            # 1. Handle non-auto-renew: deactivate and cancel Stripe subscription
            if not license_sub.auto_renew:
                # Admin opted out - deactivate and cancel Stripe subscription
                if license_sub.stripe_subscription_id:
                    try:
                        stripe.Subscription.modify(
                            license_sub.stripe_subscription_id,
                            cancel_at_period_end=True,
                        )
                    except stripe.error.StripeError as exc:
                        logger.warning(
                            "Failed to cancel Stripe subscription for license %s: %s",
                            license_sub.id,
                            str(exc),
                        )

                license_sub.is_active = False
                license_sub.save(update_fields=["is_active", "updated_at"])
                sync_teachers_under_license_to_mailerlite(license_sub)
                deactivated_count += 1
                logger.info(
                    "License %s deactivated (auto_renew=False).",
                    license_sub.id,
                )
                continue

            # 2. Auto_renew enabled - must verify payment before renewal
            if not license_sub.stripe_subscription_id:
                # Fallback: no Stripe reference - renew anyway? Better to skip and alert
                logger.warning(
                    "License %s has no stripe_subscription_id; skipping renewal.",
                    license_sub.id,
                )
                continue

            # Fetch Stripe subscription and latest invoice
            stripe_sub = stripe.Subscription.retrieve(
                license_sub.stripe_subscription_id
            )

            latest_invoice_id = stripe_sub.get("latest_invoice")
            if not latest_invoice_id:
                continue

            invoice = stripe.Invoice.retrieve(latest_invoice_id)
            if invoice["status"] != "paid":
                # Payment not confirmed - update status and skip
                license_sub.stripe_status = StripeSubscriptionStatus.PAST_DUE
                license_sub.save(update_fields=["stripe_status", "updated_at"])
                skipped_not_paid += 1
                logger.info(
                    "License %s skipped: invoice %s status %s.",
                    license_sub.id,
                    invoice["id"],
                    invoice["status"],
                )
                continue

            # 3. Payment confirmed - but has renewal already happened?
            # The webhook should have done it, but if not, do it here
            # process_license_renewal is idempotent; it will skip if already renewed

            with transaction.atomic():
                locked_license = LicenseSubscription.objects.select_for_update().get(
                    pk=license_sub.pk
                )

                # Idempotency: if already renewed, skip
                if locked_license.billing_cycle_end > now:
                    logger.info(
                        "License %s already renewed; skipping reconciliation.",
                        locked_license.id,
                    )
                    continue
                LicenseSubscriptionService.process_license_renewal(locked_license)
                renewed_count += 1

        except Exception as exc:
            failed_count += 1
            logger.error(
                "Failed to process license %s: %s",
                license_sub.id,
                str(exc),
                exc_info=True,
            )

    summary = (
        f"License subscriptions processed: "
        f"{renewed_count} renewed (fallback), "
        f"{deactivated_count} deactivated, "
        f"{skipped_not_paid} skipped (not paid), "
        f"{failed_count} failed."
    )
    logger.info(summary)
    return summary


@shared_task(bind=True, max_retries=0)
def cleanup_expired_credit_buckets(self):
    """
    Finds all CreditBuckets that have physically expired (expires_at <= now)
    but have not yet been marked as processed, and formalizes their expiration
    in the ledger via SubscriptionService.expire_bucket().

    This task is intentionally downstream of both renewal tasks. Running it
    after renewals ensures that buckets retired during rollover (where
    expires_at is set to `now` by the renewal logic) are also swept up
    cleanly without requiring the renewal code to call expire_bucket directly.

    Only buckets with remaining credits generate an EXPIRE ledger entry;
    fully exhausted buckets are marked is_processed=True silently to keep
    the ledger free of zero-value noise.

    Returns a summary string consumed by Celery Beat's result backend.
    """
    now = timezone.now()

    expired_buckets = CreditBucket.objects.filter(
        expires_at__lte=now,
        is_processed=False,
    ).select_related("wallet__user")

    total_expired_count = 0
    total_value_lost = 0
    failed_count = 0

    for bucket in expired_buckets:
        try:
            value_lost = SubscriptionService.expire_bucket(bucket)
            total_expired_count += 1
            total_value_lost += value_lost
        except Exception as exc:
            failed_count += 1
            logger.error(
                "Failed to reconcile expired bucket %s (wallet: %s): %s",
                bucket.id,
                bucket.wallet_id,
                str(exc),
                exc_info=True,
            )
            continue

    summary = (
        f"Credit bucket cleanup: "
        f"{total_expired_count} buckets processed, "
        f"{total_value_lost} raw credits expired, "
        f"{failed_count} failed."
    )
    logger.info(summary)
    return summary


@shared_task(bind=True, max_retries=0)
def process_annual_plan_credit_grants(self):
    """
    For ANNUAL-interval individual plans only: grants the next month's
    MONTHLY credit bucket mid-cycle, since Stripe only bills once a year
    but credits still refresh monthly. Separate from
    process_subscription_renewals, which handles the actual once-a-year
    billing-cycle renewal (rollover into a new UserSubscription row, plan
    changes, Stripe price sync) for ALL plans including annual ones —
    that still happens correctly at billing_cycle_end regardless of this task.

    Eligibility: active, non-trial subscriptions on an ANNUAL plan where
    next_credit_grant_at has passed but billing_cycle_end has NOT yet
    passed. Once billing_cycle_end passes, it's the real annual renewal's
    job instead — this task explicitly excludes those to avoid overlap.
    """
    now = timezone.now()

    due_subs = UserSubscription.objects.filter(
        is_active=True,
        is_trial=False,
        plan__interval=BillingInterval.ANNUAL,
        next_credit_grant_at__lte=now,
        billing_cycle_end__gt=now,
    ).select_related("user", "plan")

    granted_count = 0
    failed_count = 0

    for sub in due_subs:
        try:
            SubscriptionService.process_mid_cycle_credit_grant(sub)
            granted_count += 1
        except Exception as exc:
            failed_count += 1
            logger.error(
                "Failed mid-cycle credit grant for subscription %s (user %s): %s",
                sub.id,
                sub.user.email,
                str(exc),
                exc_info=True,
            )

    summary = (
        f"Annual plan mid-cycle credit grants: "
        f"{granted_count} granted, {failed_count} failed."
    )
    logger.info(summary)
    return summary


@shared_task(bind=True)
def reconcile_subscription_renewals(self):
    """
    Daily safety net: ensures that all active individual subscriptions
    that should have been renewed by Stripe are actually renewed locally.
    """
    now = timezone.now()

    # Find active, non-trial subscriptions with local billing_cycle_end in the past.
    subscriptions = UserSubscription.objects.filter(
        is_active=True,
        is_trial=False,
        billing_cycle_end__lte=now,
        stripe_subscription_id__isnull=False,
    ).select_related("user", "plan")

    reconciled_count = 0
    skipped_past_due = 0
    skipped_not_paid = 0
    failed_count = 0

    for sub in subscriptions:
        try:
            # 1. Fetch the latest Stripe subscription data.
            stripe_sub = stripe.Subscription.retrieve(sub.stripe_subscription_id)

            # 2. If Stripe status indicates payment issues, update local status.
            if stripe_sub["status"] in ["past_due", "unpaid", "canceled"]:
                sub.stripe_status = stripe_sub["status"].upper()
                # If canceled or unpaid, mark inactive.
                if stripe_sub["status"] in ["canceled", "unpaid"]:
                    sub.is_active = False
                    sub.save(update_fields=["stripe_status", "is_active", "updated_at"])

                    from users.tasks import sync_user_to_mailerlite

                    sync_user_to_mailerlite.delay(str(sub.user_id))

                    logger.info(
                        "Subscription %s deactivated due to Stripe status: %s",
                        sub.id,
                        stripe_sub["status"],
                    )
                else:
                    sub.save(update_fields=["stripe_status", "updated_at"])
                    logger.info(
                        "Subscription %s marked PAST_DUE from Stripe.",
                        sub.id,
                    )
                skipped_past_due += 1
                continue

            # 3. Get the latest invoice.
            latest_invoice_id = stripe_sub.get("latest_invoice")
            if not latest_invoice_id:
                # No invoice yet – likely a free plan or just created; skip.
                continue

            invoice = stripe.Invoice.retrieve(latest_invoice_id)

            # 4. Only act if invoice is paid.
            if invoice["status"] != "paid":
                skipped_not_paid += 1
                # Update status to reflect unpaid invoice.
                sub.stripe_status = StripeSubscriptionStatus.PAST_DUE
                sub.save(update_fields=["stripe_status", "updated_at"])
                continue

            # 5. Invoice is paid – but has local renewal already happened?
            # Double-check with a row lock.
            with transaction.atomic():
                # Re-lock the subscription row to avoid race with webhook.
                locked_sub = UserSubscription.objects.select_for_update().get(pk=sub.pk)
                # If already renewed (billing_cycle_end > now), skip.
                if locked_sub.billing_cycle_end > now:
                    logger.info(
                        "Subscription %s already renewed by webhook. Skipping reconciliation.",
                        locked_sub.id,
                    )
                    continue

                # Process the renewal.
                if locked_sub.is_trial:
                    # Trial conversion? Should not happen here; trials handled by expiry task.
                    # But just in case, skip.
                    continue
                else:
                    updated_sub = SubscriptionService.process_rollover_and_renewal(
                        locked_sub
                    )

                # Re‑attach Stripe IDs and status.
                updated_sub.stripe_subscription_id = stripe_sub["id"]
                updated_sub.stripe_status = StripeSubscriptionStatus.ACTIVE
                updated_sub.save(
                    update_fields=[
                        "stripe_subscription_id",
                        "stripe_status",
                        "updated_at",
                    ]
                )

                reconciled_count += 1
                logger.info(
                    "Reconciliation renewed subscription %s for user %s.",
                    updated_sub.id,
                    updated_sub.user.email,
                )

        except Exception as exc:
            failed_count += 1
            logger.error(
                "Reconciliation failed for subscription %s (user %s): %s",
                sub.id,
                sub.user.email,
                str(exc),
                exc_info=True,
            )

    summary = (
        f"Subscription reconciliation: "
        f"{reconciled_count} renewed, "
        f"{skipped_past_due} skipped (past due), "
        f"{skipped_not_paid} skipped (not paid), "
        f"{failed_count} failed."
    )
    logger.info(summary)
    return summary


@shared_task(bind=True, max_retries=0)
def expire_active_trials(self):
    """
    Expire trials where either:
    1. trial_end has passed (14 days reached)
    2. User has no credits left (exhausted)

    This task runs independently of Stripe webhooks. It's a safety net to ensure
    trial access is cut immediately when time or credits run out.

    Design principles:
    - All-or-nothing per trial (atomic operations)
    - Continues on failure per trial (one bad trial doesn't block others)
    - Extensive logging for debugging
    - Handles edge cases (missing wallet, bucket corruption, etc.)

    Returns:
        str: Summary of processed trials
    """
    now = timezone.now()

    # Fetch Active Trials

    # Only fetch trials that are CURRENTLY marked is_active=True
    # (already-expired trials will have is_active=False)
    active_trials = UserSubscription.objects.select_related("user", "plan").filter(
        is_trial=True,
        is_active=True,
    )

    expired_by_time_count = 0
    expired_by_credits_count = 0
    failed_count = 0
    skipped_still_valid = 0

    for trial_sub in active_trials:
        try:
            user = trial_sub.user
            trial_end = trial_sub.trial_end

            # Check 1: Has the time window expired?
            if trial_end and trial_end <= now:
                # Time window expired — expire it

                SubscriptionService.expire_trial(trial_sub)
                expired_by_time_count += 1
                logger.info(
                    "Trial expired (14-day window passed) for user %s "
                    "(subscription %s, trial_end: %s).",
                    user.email,
                    trial_sub.id,
                    trial_end.isoformat(),
                )
                continue

            # Check 2: Has user exhausted all credits?

            # Safely fetch wallet: if missing, something is very wrong
            try:
                wallet = user.credit_wallet
            except CreditWallet.DoesNotExist:
                logger.error(
                    "Trial user %s (subscription %s) has no CreditWallet! "
                    "This should never happen. Skipping.",
                    user.email,
                    trial_sub.id,
                )
                failed_count += 1
                continue

            remaining_credits = wallet.total_remaining_credits()

            if remaining_credits <= 0:

                # User has no credits left - expire the trial immediately
                # even if the 14-day window hasn't closed yet.

                SubscriptionService.expire_trial(trial_sub, force=True)
                expired_by_credits_count += 1

                logger.info(
                    "Trial expired (credits exhausted) for user %s "
                    "(subscription %s, remaining: %d raw credits).",
                    user.email,
                    trial_sub.id,
                    remaining_credits,
                )
                continue

            # NO EXPIRATION: Trial is still valid (time remaining AND credits > 0)

            skipped_still_valid += 1
            logger.debug(
                "Trial still valid for user %s: %d days remaining, "
                "%d raw credits remaining.",
                user.email,
                max(0, (trial_end - now).days) if trial_end else 0,
                remaining_credits,
            )

        except Exception as exc:
            failed_count += 1

            logger.error(
                "Failed to process trial expiration for subscription %s (user %s): %s",
                trial_sub.id,
                trial_sub.user.email if trial_sub.user else "UNKNOWN",
                str(exc),
                exc_info=True,
            )
            # Continue to next trial - one failure shouldn't block the entire task

    summary = (
        f"Trial expiration task completed: "
        f"{expired_by_time_count} expired (14-day limit), "
        f"{expired_by_credits_count} expired (credits exhausted), "
        f"{skipped_still_valid} still valid, "
        f"{failed_count} failed."
    )
    logger.info(summary)
    return summary


@shared_task(bind=True, max_retries=0)
def process_license_monthly_credit_refreshes(self):
    """
    Monthly credit refresh for teachers under active licenses.
    For each active SchoolCreditAllocation with next_credit_grant_at <= now,
    expires the current monthly bucket, applies rollover, and grants a new monthly bucket.
    """
    now = timezone.now()

    # Get all active allocations that need a refresh, within active licenses.
    due_allocations = SchoolCreditAllocation.objects.filter(
        is_active=True,
        next_credit_grant_at__lte=now,
        license_subscription__is_active=True,
        license_subscription__billing_cycle_end__gt=now,
    ).select_related("license_subscription", "user", "license_subscription__plan")

    refreshed_count = 0
    # skipped_no_bucket = 0
    failed_count = 0

    for allocation in due_allocations:
        try:
            with transaction.atomic():
                # Lock the allocation and license rows
                locked_allocation = (
                    SchoolCreditAllocation.objects.select_for_update().get(
                        pk=allocation.pk
                    )
                )

                # Re-check conditions
                if not locked_allocation.is_active:
                    continue

                license_sub = locked_allocation.license_subscription
                if not license_sub.is_active or license_sub.billing_cycle_end <= now:
                    continue

                # Check if next_credit_grant_at is still due (avoid race)
                if locked_allocation.next_credit_grant_at > now:
                    continue

                # Perform the refresh
                LicenseSubscriptionService._refresh_teacher_credits(locked_allocation)
                refreshed_count += 1

        except Exception as exc:
            failed_count += 1
            logger.error(
                "Failed to refresh credits for allocation %s: %s",
                allocation.id,
                str(exc),
                exc_info=True,
            )

    summary = (
        f"License credit refresh completed: "
        f"{refreshed_count} refreshed, "
        f"{failed_count} failed."
    )
    logger.info(summary)
    return summary
