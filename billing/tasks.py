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
from django.utils import timezone

from .imports import stripe
from .license_service import LicenseSubscriptionService
from .models import BillingInterval, CreditBucket, LicenseSubscription, UserSubscription
from .services import SubscriptionService

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=0)
def process_subscription_renewals(self):
    """
    Process renewals and trial expiry for all INDIVIDUAL UserSubscriptions
    whose billing_cycle_end has passed.

    Routing logic per subscription:
    - is_trial=True  → call expire_trial() (no renewal; user returns to no-sub state)
    - auto_renew=True OR pending_plan set → call process_rollover_and_renewal()
    - otherwise → deactivate silently (user cancelled)

    Trial subscriptions have auto_renew=False by design, so the is_trial
    check MUST come before the auto_renew / pending_plan check to avoid
    routing a trial into the deactivation-only branch and skipping the
    proper ledger EXPIRE entry written by expire_trial().

    Returns a summary string consumed by Celery Beat's result backend.
    """
    now = timezone.now()

    # Select all active individual subscriptions whose cycle has ended.
    # select_related("plan", "pending_plan") avoids N+1 on the routing checks below.
    expired_subs = UserSubscription.objects.filter(
        is_active=True,
        billing_cycle_end__lte=now,
    ).select_related("user", "plan", "pending_plan")

    renewed_count = 0
    trial_expired_count = 0
    deactivated_count = 0
    failed_count = 0

    for sub in expired_subs:
        try:
            if sub.is_trial:
                # --- Trial expired without conversion ---
                # expire_trial() writes the EXPIRE ledger entry for unused
                # trial credits and sets is_active=False, is_trial=False.
                SubscriptionService.expire_trial(sub)
                trial_expired_count += 1
                logger.info(
                    "Trial expired for user %s (subscription %s).",
                    sub.user.email,
                    sub.id,
                )

            elif sub.auto_renew or sub.pending_plan_id:
                # --- Normal renewal or scheduled plan change ---
                SubscriptionService.process_rollover_and_renewal(sub)
                renewed_count += 1
                logger.info(
                    "Renewed subscription %s for user %s (plan: %s).",
                    sub.id,
                    sub.user.email,
                    sub.plan.name,
                )

            else:
                # --- User cancelled (auto_renew=False, no pending plan) ---
                # Deactivate cleanly. Credit cleanup is handled by
                # cleanup_expired_credit_buckets which runs after this task.
                sub.is_active = False
                sub.save(update_fields=["is_active", "updated_at"])
                deactivated_count += 1
                logger.info(
                    "Deactivated cancelled subscription %s for user %s.",
                    sub.id,
                    sub.user.email,
                )

        except Exception as exc:
            failed_count += 1
            logger.error(
                "Failed to process individual subscription %s for user %s: %s",
                sub.id,
                sub.user.email,
                str(exc),
                exc_info=True,
            )
            # Continue — one bad subscription must not block the rest.

    summary = (
        f"Individual subscriptions processed: "
        f"{renewed_count} renewed, "
        f"{trial_expired_count} trials expired, "
        f"{deactivated_count} deactivated, "
        f"{failed_count} failed."
    )
    logger.info(summary)
    return summary


@shared_task(bind=True, max_retries=0)
def process_license_renewals(self):
    """
    Process renewals for all active LicenseSubscriptions whose
    billing_cycle_end has passed.

    Routing logic per license:
    - auto_renew=True  → call LicenseSubscriptionService.process_license_renewal()
                         which handles per-teacher rollover and new MONTHLY bucket
                         creation inside per-teacher savepoints.
    - auto_renew=False → deactivate the license. Teachers keep their current
                         credits until cleanup_expired_credit_buckets runs.

    Intentionally isolated from process_subscription_renewals to keep the
    two billing pipelines independently observable and independently
    schedulable (license contracts are 9/10/12-month, not monthly).

    Returns a summary string consumed by Celery Beat's result backend.
    """
    now = timezone.now()

    # Only fetch licenses whose cycle has genuinely ended.
    # select_related("school", "plan") prevents N+1 on logging and validation
    # inside process_license_renewal.
    expired_licenses = LicenseSubscription.objects.filter(
        is_active=True,
        billing_cycle_end__lte=now,
    ).select_related("school", "plan", "admin_user")

    renewed_count = 0
    deactivated_count = 0
    failed_count = 0

    for license_sub in expired_licenses:
        try:
            if license_sub.auto_renew:
                # process_license_renewal() is fully atomic at the license level,
                # with per-teacher inner savepoints so one teacher's failure does
                # not rollback credits already written for other teachers.
                LicenseSubscriptionService.process_license_renewal(license_sub)
                renewed_count += 1
                logger.info(
                    "Renewed license %s for school '%s' (plan: %s).",
                    license_sub.id,
                    license_sub.school.name,
                    license_sub.plan.name,
                )

            else:
                # School admin set auto_renew=False — deactivate.
                # Teachers' existing credit buckets will expire naturally;
                # cleanup_expired_credit_buckets will log the EXPIRE entries.

                if license_sub.stripe_subscription_id:
                    stripe.Subscription.modify(
                        license_sub.stripe_subscription_id, cancel_at_period_end=True
                    )

                license_sub.is_active = False
                license_sub.save(update_fields=["is_active", "updated_at"])

                deactivated_count += 1
                logger.info(
                    "Deactivated license %s for school '%s' (auto_renew=False).",
                    license_sub.id,
                    license_sub.school.name,
                )

        except Exception as exc:
            failed_count += 1
            logger.error(
                "Failed to process license renewal %s for school '%s': %s",
                license_sub.id,
                license_sub.school.name,
                str(exc),
                exc_info=True,
            )
            # Continue — one bad license must not block the rest.

    summary = (
        f"License subscriptions processed: "
        f"{renewed_count} renewed, "
        f"{deactivated_count} deactivated, "
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
