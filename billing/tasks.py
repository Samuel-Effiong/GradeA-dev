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
    StripeEvent,
    StripeEventStatus,
    StripeSubscriptionStatus,
    UserSubscription,
)
from .services import SubscriptionService
from .stripe_service import RENEWAL_BILLING_REASONS, extract_invoice_billing_period
from .webhooks import STRIPE_EVENT_CLAIM_STALE_AFTER, STRIPE_RETRY_WINDOW

logger = logging.getLogger(__name__)

# How many recent paid invoices to scan when the newest one isn't the
# renewal we're looking for (e.g. an upgrade proration landed after it).
_INVOICE_LOOKBACK_LIMIT = 10


def _invoice_period_end(invoice):
    """
    Latest service-period end this invoice covers, as a Unix timestamp,
    or None if the payload carries no usable period at all.

    Reads the invoice-level `period_end` and every line item's
    `period.end`, taking the max — a renewal invoice's line items carry
    the authoritative subscription period even when the invoice-level
    field is absent or narrower.
    """
    candidates = []

    def _add(value):
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)) and value > 0:
            candidates.append(int(value))

    _add(invoice.get("period_end"))

    lines = invoice.get("lines") or {}
    for line in lines.get("data") or []:
        period = line.get("period") or {}
        _add(period.get("end"))

    return max(candidates) if candidates else None


def _find_new_period_paid_invoice(
    stripe_subscription_id, local_cycle_end, latest_invoice=None
):
    """
    Finds a PAID renewal invoice whose service period extends BEYOND the
    local billing_cycle_end — i.e. proof that Stripe has actually billed
    a new cycle.

    Why this exists: the reconcile sweeps used to accept any
    `latest_invoice` with status="paid". That invoice is normally the
    PREVIOUS cycle's, which is of course still paid — so whenever Stripe
    had not yet renewed (a stalled test clock, a subscription paused on
    Stripe's side, a schedule boundary not reached), the sweep would
    "reconcile" a renewal that never happened: granting a fresh cycle of
    credits the customer never paid for and pushing local
    billing_cycle_end a month past Stripe's real period, after which the
    genuine renewal webhook gets swallowed by the idempotency guard.

    Checks `latest_invoice` first (already fetched, zero extra API
    calls), then falls back to scanning recent paid invoices — the newest
    invoice can legitimately be an upgrade proration that landed after
    the cycle invoice.

    Returns the qualifying invoice, or None. Never raises.
    """
    cutoff = int(local_cycle_end.timestamp())

    def _qualifies(invoice):
        if not invoice:
            return False
        if invoice.get("status") != "paid":
            return False
        if invoice.get("billing_reason") not in RENEWAL_BILLING_REASONS:
            return False
        period_end = _invoice_period_end(invoice)
        return period_end is not None and period_end > cutoff

    if _qualifies(latest_invoice):
        return latest_invoice

    try:
        listed = stripe.Invoice.list(
            subscription=stripe_subscription_id,
            status="paid",
            limit=_INVOICE_LOOKBACK_LIMIT,
        )
    except stripe.error.StripeError as exc:
        logger.warning(
            "Could not list invoices for subscription %s while verifying a "
            "new billing period: %s",
            stripe_subscription_id,
            exc,
        )
        return None

    for invoice in listed.get("data") or []:
        if _qualifies(invoice):
            return invoice

    return None


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
    skipped_no_new_period = 0
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

            # 2b. Same trap as the individual sweep: the PREVIOUS cycle's
            # invoice is paid too. Require one covering a period beyond the
            # local cycle end before treating this as a real renewal.
            renewal_invoice = _find_new_period_paid_invoice(
                license_sub.stripe_subscription_id,
                license_sub.billing_cycle_end,
                latest_invoice=invoice,
            )
            if renewal_invoice is None:
                skipped_no_new_period += 1
                logger.info(
                    "License %s: latest invoice %s is paid but no paid "
                    "renewal invoice covering a period beyond %s was found — "
                    "Stripe has not billed a new cycle yet. Skipping rather "
                    "than renewing off a previous-cycle invoice.",
                    license_sub.id,
                    invoice.get("id"),
                    license_sub.billing_cycle_end.isoformat(),
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
        f"{skipped_no_new_period} skipped (no new-period invoice), "
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
    skipped_no_new_period = 0
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

            # 4b. Paid is not enough: the PREVIOUS cycle's invoice is also
            # paid. Require an invoice that actually covers a period past
            # our local cycle end, or Stripe hasn't billed a new cycle and
            # renewing here would grant an unpaid-for cycle of credits.
            renewal_invoice = _find_new_period_paid_invoice(
                sub.stripe_subscription_id,
                sub.billing_cycle_end,
                latest_invoice=invoice,
            )
            if renewal_invoice is None:
                skipped_no_new_period += 1
                logger.info(
                    "Subscription %s (user %s): latest invoice %s is paid but "
                    "no paid renewal invoice covering a period beyond %s was "
                    "found — Stripe has not billed a new cycle yet. Skipping "
                    "rather than renewing off a previous-cycle invoice.",
                    sub.id,
                    sub.user.email,
                    invoice.get("id"),
                    sub.billing_cycle_end.isoformat(),
                )
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
                    # renewal_invoice is the invoice we verified above as
                    # covering a NEW period, so its line-item period is the
                    # right anchor for the local cycle — the sweep produces
                    # exactly the dates the webhook would have.
                    period_start, period_end = extract_invoice_billing_period(
                        renewal_invoice
                    )
                    updated_sub = SubscriptionService.process_rollover_and_renewal(
                        locked_sub, period_start=period_start, period_end=period_end
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
        f"{skipped_no_new_period} skipped (no new-period invoice), "
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
def sweep_stale_stripe_events(self):
    """
    Watchdog for the Stripe webhook idempotency ledger (billing/webhooks.py).

    Two jobs, neither of which ever re-runs a handler:

    1. Settle abandoned claims. A PROCESSING row whose claim is older than
       STRIPE_EVENT_CLAIM_STALE_AFTER belongs to a worker that was killed
       mid-handler. The claim logic already treats these as re-claimable,
       so this is not needed for correctness — it exists so "needs
       attention" collapses into a single queryable status (FAILED)
       instead of being split across two.

    2. Report failures, loudly once they are past saving. Stripe retries a
       failed delivery for ~3 days and then gives up forever, so a FAILED
       row that crosses STRIPE_RETRY_WINDOW will never be retried by
       anyone but a human. That is the only case that needs someone NOW,
       so it is the only case that logs at ERROR.

    This task deliberately does NOT re-dispatch handlers. They reach
    stripe.Refund.create and Subscription.modify, which a database
    rollback cannot undo — an automatic replay loop could refund a
    customer twice with nobody watching. Repair is human-gated via
    `manage.py replay_stripe_events`, which defaults to --dry-run.
    """
    now = timezone.now()
    stale_cutoff = now - STRIPE_EVENT_CLAIM_STALE_AFTER
    unretriable_cutoff = now - STRIPE_RETRY_WINDOW

    abandoned_count = 0
    failed_count = 0

    stale_claims = StripeEvent.objects.filter(
        status=StripeEventStatus.PROCESSING,
        claimed_at__lt=stale_cutoff,
    ).iterator()

    for event in stale_claims:
        try:
            # Fenced on claimed_at, same as _finish_stripe_event: if a
            # delivery re-claimed this row between the query and now, its
            # fresh claim must not be clobbered.
            abandoned_count += StripeEvent.objects.filter(
                pk=event.pk,
                status=StripeEventStatus.PROCESSING,
                claimed_at=event.claimed_at,
            ).update(
                status=StripeEventStatus.FAILED,
                completed_at=now,
                last_error="abandoned: processing claim went stale",
            )
        except Exception as exc:
            failed_count += 1
            logger.error(
                "Failed to sweep stale StripeEvent %s: %s",
                event.pk,
                str(exc),
                exc_info=True,
            )
            continue

    failed_qs = StripeEvent.objects.filter(status=StripeEventStatus.FAILED)
    failed_retriable = failed_qs.filter(completed_at__gte=unretriable_cutoff).count()
    failed_unretriable = failed_qs.filter(completed_at__lt=unretriable_cutoff).count()

    if failed_unretriable:
        logger.error(
            "%d Stripe webhook event(s) FAILED and are past Stripe's ~%d day "
            "retry window — they will NEVER be retried automatically, so a "
            "customer may have paid without receiving anything. Inspect them "
            "in the admin (Billing > Stripe events, status=FAILED) and repair "
            "with: manage.py replay_stripe_events --dry-run",
            failed_unretriable,
            STRIPE_RETRY_WINDOW.days,
        )

    summary = (
        f"Stripe event sweep: "
        f"{abandoned_count} abandoned claim(s) marked FAILED, "
        f"{failed_retriable} FAILED within Stripe's retry window, "
        f"{failed_unretriable} FAILED past it, "
        f"{failed_count} sweep error(s)."
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


@shared_task(bind=True, max_retries=0)
def nightly_stripe_live_qa(self):
    """
    Run the billing QA scenarios against REAL Stripe test mode.

    Every other billing test mocks Stripe, so they encode our beliefs
    about its API rather than its behaviour. C1 is the proof that beliefs
    go stale silently: a field moved in API 2025-03-31, the QA
    time-travel tool broke, and hundreds of passing tests could not see
    it because they all mocked the shape we expected. This task is the
    thing that would have noticed.

    Off unless ENABLE_STRIPE_LIVE_QA is set AND the Stripe keys are test
    keys — so on a normal production worker this is a no-op that logs at
    DEBUG and returns. It is meant for a staging/QA worker.

    max_retries=0 on purpose: a failure here is a signal to investigate,
    not a transient to paper over, and retrying would create a second set
    of Stripe objects while the first set is still being diagnosed.

    Escalation follows the sweep_stale_stripe_events convention — a
    FAILURE logs at ERROR naming the scenario and the repair command,
    because a broken billing assumption is exactly the class of bug that
    reaches customers as lost money.
    """
    from .stripe_live_qa import LiveQAConfigurationError, LiveQARefused, live_qa_enabled

    if not live_qa_enabled():
        logger.debug(
            "Stripe live QA is not enabled in this environment; skipping. "
            "(Needs ENABLE_STRIPE_LIVE_QA and sk_test_ Stripe keys.)"
        )
        return "Stripe live QA skipped: not enabled in this environment."

    from .stripe_live_qa_scenarios import run_suite

    try:
        result = run_suite()
    except (LiveQARefused, LiveQAConfigurationError) as exc:
        # Misconfiguration, not a billing bug. WARNING, not ERROR: nobody
        # should be woken for a QA environment that is not set up.
        logger.warning("Stripe live QA could not run: %s", exc)
        return f"Stripe live QA could not run: {exc}"

    for scenario in result.scenarios:
        if scenario.passed:
            continue
        detail = scenario.error or "; ".join(
            str(check) for check in scenario.failed_checks
        )
        logger.error(
            "Stripe live QA scenario %s FAILED against real Stripe: %s. "
            "This means real Stripe behaviour no longer matches what the "
            "billing code assumes — mocked tests CANNOT catch this. "
            "Reproduce with: manage.py run_stripe_live_qa --scenario %s",
            scenario.name,
            detail,
            scenario.name,
        )

    for error in result.cleanup_errors:
        # Leaked objects cost nothing in test mode but accumulate, and a
        # leaked ledger row would make the event sweeper page falsely.
        logger.warning("Stripe live QA cleanup problem: %s", error)

    summary = result.summary()
    logger.info(summary)
    return summary


@shared_task(bind=True, max_retries=0)
def run_live_qa_console_job(self, run_id):
    """
    Executes ONE billing.models.LiveQARun triggered from the internal QA
    web console (billing/qa_console.py) — a scenario/tier run via
    run_suite, or a chaos walk (or shrink) via billing.live_qa.chaos.

    max_retries=0 for the same reason as nightly_stripe_live_qa: a
    retry here would create a second set of real Stripe objects while
    the first run is still being looked at.

    Wrapped end-to-end in try/except so an unexpected crash (not a
    scenario failing — run_suite and run_chaos_walk already catch and
    report those — but something failing before either even gets to
    run, e.g. a bad run_id) still leaves the row in a terminal FAILED
    state with the exception recorded, rather than stuck at RUNNING
    forever with nothing to explain why.
    """
    from .models import LiveQARun, LiveQARunKind, LiveQARunStatus
    from .qa_console import _serialize_result

    run = LiveQARun.objects.get(id=run_id)
    run.status = LiveQARunStatus.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at", "updated_at"])

    try:
        if run.kind == LiveQARunKind.CHAOS:
            from .live_qa.chaos import run_chaos_walk, shrink_chaos_failure

            if run.shrink:
                # A shrink's job is to find a minimal repro, not to
                # report whether billing behaved correctly — that
                # question was already answered by the ORIGINAL failing
                # walk this shrink is minimizing. So "PASSED" here means
                # "the shrink operation completed", regardless of
                # whether it found a reproducing prefix; the actual
                # finding is in the summary/result_data.
                shrink_result = shrink_chaos_failure(run.seed, run.steps)
                run.result_data = _serialize_result(shrink_result)
                run.summary = shrink_result.summary()
                passed = True
            else:
                walk_result = run_chaos_walk(run.seed, run.steps)
                run.result_data = _serialize_result(walk_result)
                run.summary = walk_result.summary()
                passed = not walk_result.failed
        else:
            from .stripe_live_qa_scenarios import run_suite, scenarios_for_tier

            names = run.scenario_names or (
                scenarios_for_tier(run.tier) if run.tier else None
            )
            suite_result = run_suite(names)
            run.result_data = _serialize_result(suite_result)
            run.summary = suite_result.summary()
            passed = suite_result.passed

        run.status = LiveQARunStatus.PASSED if passed else LiveQARunStatus.FAILED
    except Exception as exc:  # noqa: BLE001 - must not leave the row stuck RUNNING
        run.status = LiveQARunStatus.FAILED
        run.summary = f"Console run crashed before completing: {exc!r}"
        logger.exception("[qa-console] run %s (%s) crashed.", run.id, run.kind)
    finally:
        run.finished_at = timezone.now()
        run.save(
            update_fields=[
                "status",
                "summary",
                "result_data",
                "finished_at",
                "updated_at",
            ]
        )

    return run.summary
