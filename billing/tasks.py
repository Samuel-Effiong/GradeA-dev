"""
billing/tasks.py
================
Celery tasks for the billing pipeline.

Three independent tasks — each with a single, well-defined responsibility:

1. expire_active_trials
   Handles INDIVIDUAL trial expiry (by time or by credit exhaustion).
   Runs every 6 hours.

2. process_license_renewals
   Handles LicenseSubscription renewals for institutional plans.
   Completely separate from the individual pipeline.
   Runs nightly.

3. cleanup_expired_credit_buckets
   Formalizes expired CreditBucket entries in the ledger.
   Runs nightly after the two renewal tasks.
"""

import logging
from enum import Enum

from celery import shared_task
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .imports import stripe
from .license_service import (
    LicenseSubscriptionService,
    sync_teachers_under_license_to_mailerlite,
)
from .models import (
    BetaProfile,
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
from .services import AnalyticsService, SubscriptionService
from .stripe_service import (
    RENEWAL_BILLING_REASONS,
    _stripe_get,
    extract_invoice_billing_period,
)
from .webhooks import STRIPE_EVENT_CLAIM_STALE_AFTER, STRIPE_RETRY_WINDOW

logger = logging.getLogger(__name__)

# Which tier the nightly Beat schedule runs. "fast" is the ~20-30 minute
# envelope; "deep" takes hours and belongs in a weekly job.
SCHEDULED_LIVE_QA_TIER = "fast"

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
    reconcile_subscription_renewals, which handles the actual once-a-year
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

                    # Best-effort, and deliberately isolated: the
                    # deactivation above is already committed, so a broker
                    # outage (`kombu.exceptions.OperationalError` out of
                    # `.delay()`) must not travel up to the generic
                    # per-subscription handler below. Left bare, it did —
                    # and a Redis blip was counted and logged as a
                    # RECONCILIATION failure for a subscription that had in
                    # fact reconciled correctly, sending someone to
                    # investigate a billing problem that did not exist.
                    # This mirrors the five other dispatch sites in billing,
                    # which all wrap `.delay()` the same way.
                    try:
                        sync_user_to_mailerlite.delay(str(sub.user_id))
                    except Exception:
                        logger.exception(
                            "Could not queue the MailerLite sync for user %s "
                            "after deactivating subscription %s. The "
                            "deactivation itself succeeded and stands; only "
                            "the marketing-list sync was lost.",
                            sub.user_id,
                            sub.id,
                        )

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


#: How many times the sweeper will re-dispatch one event whose worker died
#: before starting. Bounded so a task that dies the same way every hour
#: (a poison payload, an OOM on one big event) stops being retried and
#: starts being reported to a human instead of cycling forever.
STRIPE_EVENT_MAX_RECOVERY_ATTEMPTS = 3


class RecoveryOutcome(Enum):
    """
    What the sweeper should do with an abandoned event after trying to
    recover it.

    The three cases are genuinely different and collapsing them is a bug:
    DEFERRED once returned the same value as DECLINED, and the sweeper
    duly marked FAILED every event it had failed to re-queue during a
    broker blip — stranding exactly the events this recovery exists to
    save, since Stripe has long since had its 200 and will not redeliver.
    """

    REDISPATCHED = "redispatched"  # queued to a live worker; leave it be
    DEFERRED = "deferred"  # try again next sweep; do NOT settle it
    DECLINED = "declined"  # not recoverable; settle it as FAILED


def _redispatch_abandoned_event(event) -> RecoveryOutcome:
    """
    Hand an abandoned-but-unstarted event back to a worker.

    RACE SAFETY
    -----------
    The re-claim is one conditional UPDATE fenced on the OLD `claimed_at`,
    which does three jobs at once:

    * Two sweepers running concurrently: both read the same stale row, but
      only one UPDATE matches — the loser's WHERE clause re-evaluates
      against the winner's freshly written claimed_at and matches nothing.
    * The original worker coming back from the dead: its terminal write in
      `_finish_stripe_event` is fenced on ITS claim token, which no longer
      matches, so it cannot settle a row it no longer owns.
    * A Stripe redelivery arriving mid-recovery: the new claimed_at is
      fresh, so `_claim_stripe_event` sees a live claim and answers 409
      rather than starting a second concurrent run.

    `handler_started_at` is left NULL and `recovery_attempts` incremented,
    so if this attempt also dies before starting, the next sweep can try
    again — up to the cap.
    """
    from .webhooks import _EVENT_HANDLERS

    if event.event_type not in _EVENT_HANDLERS:
        # Nothing to run. Settling it as SUCCEEDED would be a lie, so let
        # the caller mark it FAILED through the normal path.
        return RecoveryOutcome.DECLINED

    new_token = timezone.now()
    reclaimed = StripeEvent.objects.filter(
        pk=event.pk,
        status=StripeEventStatus.PROCESSING,
        claimed_at=event.claimed_at,
        handler_started_at__isnull=True,
    ).update(
        claimed_at=new_token,
        recovery_attempts=F("recovery_attempts") + 1,
        attempts=F("attempts") + 1,
        last_error="",
    )
    if not reclaimed:
        # Someone else got there first — another sweeper, a redelivery, or
        # the original worker finally starting. All three are fine, and in
        # all three the row now belongs to somebody else: leave it alone.
        return RecoveryOutcome.DEFERRED

    try:
        process_stripe_event.delay(event.stripe_event_id, new_token.isoformat())
    except Exception as exc:  # noqa: BLE001 - broker down; report, don't crash
        # The queue is unavailable. Release the claim back to its stale
        # state so the NEXT sweep retries, rather than leaving a fresh
        # claim nobody is working on — that would look healthy for a full
        # staleness window while nothing happened.
        StripeEvent.objects.filter(
            pk=event.pk,
            status=StripeEventStatus.PROCESSING,
            claimed_at=new_token,
        ).update(
            claimed_at=event.claimed_at,
            last_error=f"recovery could not reach the broker: {exc!r}"[:2000],
        )
        logger.error(
            "Could not re-queue abandoned Stripe event %s (%s): %r. Claim "
            "released; the next sweep will try again.",
            event.stripe_event_id,
            event.event_type,
            exc,
        )
        return RecoveryOutcome.DEFERRED

    logger.warning(
        "Recovered abandoned Stripe event %s (%s): its worker died before "
        "the handler started, so no Stripe call was made and it is safe to "
        "re-dispatch. Recovery attempt %d of %d.",
        event.stripe_event_id,
        event.event_type,
        event.recovery_attempts + 1,
        STRIPE_EVENT_MAX_RECOVERY_ATTEMPTS,
    )
    return RecoveryOutcome.REDISPATCHED


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

    recovered_count = 0
    exhausted_count = 0
    deferred_count = 0

    for event in stale_claims:
        try:
            # --- Recoverable? -----------------------------------------
            # A claim with no handler_started_at means the worker died
            # between claiming and doing ANYTHING: no Stripe call was
            # made, no row was written. Re-dispatching is safe, and it is
            # also the only way the event will ever run — since Item 1
            # made dispatch asynchronous the endpoint answers Stripe 200
            # the moment it claims, so Stripe considers the delivery
            # successful and will never redeliver. Marking it FAILED and
            # waiting for a retry that is never coming is how a dead
            # worker strands an event forever.
            if (
                event.handler_started_at is None
                and event.recovery_attempts < STRIPE_EVENT_MAX_RECOVERY_ATTEMPTS
            ):
                outcome = _redispatch_abandoned_event(event)
                if outcome is RecoveryOutcome.REDISPATCHED:
                    recovered_count += 1
                    continue
                if outcome is RecoveryOutcome.DEFERRED:
                    # Someone else owns it, or the broker was unreachable.
                    # Either way this row must NOT be settled — marking it
                    # FAILED here is what stranded events during a broker
                    # blip, because Stripe will never redeliver them.
                    deferred_count += 1
                    continue

            if (
                event.handler_started_at is None
                and event.recovery_attempts >= STRIPE_EVENT_MAX_RECOVERY_ATTEMPTS
            ):
                exhausted_count += 1
                logger.error(
                    "Stripe event %s (%s) has been re-dispatched %d time(s) "
                    "and each worker died before starting. Giving up on "
                    "automatic recovery — this needs a human: "
                    "manage.py replay_stripe_events --dry-run",
                    event.stripe_event_id,
                    event.event_type,
                    event.recovery_attempts,
                )

            # --- Not recoverable: died mid-handler --------------------
            # handler_started_at is set, so this worker may have got
            # part-way through Stripe calls that no database rollback can
            # undo (Refund.create, Subscription.modify). Replaying that
            # automatically could refund a customer twice with nobody
            # watching, so it is marked FAILED for a human instead.
            #
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
                last_error=(
                    "abandoned: processing claim went stale after the "
                    "handler had started; NOT auto-replayed because it may "
                    "have made irreversible Stripe calls"
                    if event.handler_started_at
                    else "abandoned: processing claim went stale"
                ),
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
        f"{recovered_count} abandoned claim(s) re-dispatched, "
        f"{abandoned_count} abandoned claim(s) marked FAILED, "
        f"{deferred_count} deferred to the next sweep, "
        f"{exhausted_count} past the recovery cap, "
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
    from .models import LiveQARun, LiveQARunKind, LiveQARunStatus
    from .stripe_live_qa import LiveQAConfigurationError, LiveQARefused, live_qa_enabled

    # Recorded BEFORE the enablement check, and finalised on every path.
    #
    # This row is the fix for a real gap: the task was scheduled correctly
    # and had fired 20 times on the deployed worker, but wrote nothing
    # anywhere, so "ran and passed", "ran and failed" and "did nothing
    # because it is switched off here" were indistinguishable from the
    # database. Scenario failures went only to worker logs — at ERROR, but
    # nobody was watching a log line. A real-Stripe suite you cannot tell
    # the status of is not a safety net.
    run = LiveQARun.objects.create(
        kind=LiveQARunKind.SCENARIO,
        status=LiveQARunStatus.RUNNING,
        tier=SCHEDULED_LIVE_QA_TIER,
        celery_task_id=str(getattr(self.request, "id", "") or ""),
        started_at=timezone.now(),
    )

    def _finish(status, summary, result_data=None):
        run.status = status
        run.summary = summary
        if result_data is not None:
            run.result_data = result_data
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
        return summary

    if not live_qa_enabled():
        # DEBUG, as before — a normal production worker must not log noise
        # every night. The difference is that the DB now says so out loud.
        message = (
            "Stripe live QA skipped: not enabled in this environment. "
            "(Needs ENABLE_STRIPE_LIVE_QA and sk_test_ Stripe keys.)"
        )
        logger.debug(message)
        return _finish(LiveQARunStatus.SKIPPED, message)

    from .qa_console import _serialize_result
    from .stripe_live_qa_scenarios import run_suite, scenarios_for_tier

    try:
        result = run_suite(scenarios_for_tier(SCHEDULED_LIVE_QA_TIER))
    except (LiveQARefused, LiveQAConfigurationError) as exc:
        # Misconfiguration, not a billing bug. WARNING, not ERROR: nobody
        # should be woken for a QA environment that is not set up.
        logger.warning("Stripe live QA could not run: %s", exc)
        return _finish(LiveQARunStatus.ERROR, f"Stripe live QA could not run: {exc}")
    except Exception as exc:  # noqa: BLE001 - must not leave the row RUNNING
        logger.exception("Stripe live QA crashed before completing.")
        return _finish(
            LiveQARunStatus.ERROR,
            f"Stripe live QA crashed before completing: {exc!r}",
        )

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
    return _finish(
        LiveQARunStatus.PASSED if result.passed else LiveQARunStatus.FAILED,
        summary,
        _serialize_result(result),
    )


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


@shared_task(bind=True, max_retries=0)
def recalculate_conversion_probabilities(self):
    """
    Nightly refresh of every BetaProfile's conversion score.

    AnalyticsService.calculate_conversion_probability has always carried the
    docstring "Called by midnight", but nothing ever called it: no Beat
    entry, no signal, no view. Meanwhile the sales-lead endpoints
    (BetaProfileViewSet.intent_signals / intent_signal_detail) sort and
    display `conversion_probability`, so every teacher ranked and rendered
    at a permanent 0.0. This task is the missing trigger.

    Deliberately NOT wrapped in a single transaction: the scorer saves one
    profile at a time and a failure on profile N must not discard the
    N-1 scores already written. Each profile is independent, so one bad row
    is logged and skipped rather than aborting the sweep.

    Pure analytics — reads counters that record_consumption/track_activity
    already maintain and writes two float fields. Touches no credits, no
    money and no Stripe.
    """
    scored_count = 0
    failed_count = 0

    # .iterator() keeps a large beta cohort off the heap; the scorer reads
    # only BetaProfile's own columns, so no select_related is needed.
    for profile in BetaProfile.objects.iterator():
        try:
            AnalyticsService.calculate_conversion_probability(profile)
            scored_count += 1
        except Exception as exc:
            failed_count += 1
            logger.error(
                "Conversion scoring failed for BetaProfile %s (user %s): %s",
                profile.pk,
                profile.user_id,
                str(exc),
                exc_info=True,
            )

    summary = (
        f"Conversion probability refresh: "
        f"{scored_count} scored, {failed_count} failed."
    )
    logger.info(summary)
    return summary


#: Overlap guard for the nightly price sweep. A cache TTL rather than a
#: row lock, so a worker killed mid-run releases it automatically — a lock
#: that survives a crash would silently disable the reconciliation until
#: someone noticed it had stopped reporting, which is the worst possible
#: failure for a watchdog.
#:
#: Comfortably longer than a run (~18 Stripe reads) and comfortably shorter
#: than the 24h gap to the next one.
PRICE_RECONCILIATION_LOCK_KEY = "billing:price-reconciliation:running"
PRICE_RECONCILIATION_LOCK_TIMEOUT_SECONDS = 30 * 60


@shared_task(bind=True, max_retries=0)
def reconcile_stripe_prices(self):
    """
    Nightly: does every plan still cost what this application thinks?

    DETECTION ONLY. Writes nothing to Stripe and no billing state locally
    — see billing/price_reconciliation.py. Repair stays a deliberate human
    act via `manage.py reconcile_overage_prices --fix`; that `--fix` must
    never be reachable from Beat, because which side of a mismatch is
    wrong is a money decision.

    Nightly rather than weekly. The whole sweep is ~18 Stripe reads for
    the current plan set, so the cost of checking daily is negligible
    against the cost of a wrong price standing for a week.
    """
    from django.core.cache import cache

    from .price_reconciliation import reconcile_prices

    # Two Beat instances, or a manual run overlapping the scheduled one,
    # would duplicate every Stripe read and write two competing result
    # sets for the same moment. Harmless to billing — nothing here mutates
    # — but it makes the audit trail ambiguous, which defeats the point.
    if not cache.add(
        PRICE_RECONCILIATION_LOCK_KEY,
        "1",
        timeout=PRICE_RECONCILIATION_LOCK_TIMEOUT_SECONDS,
    ):
        summary = (
            "Stripe price reconciliation: another run already holds the "
            "lock; skipping this one."
        )
        logger.info(summary)
        return summary

    try:
        run = reconcile_prices()
        return run.summary
    finally:
        # Released on every path, including a raise. The TTL is the
        # backstop for a hard kill that never reaches this line.
        cache.delete(PRICE_RECONCILIATION_LOCK_KEY)


@shared_task(bind=True, max_retries=0)
def reconcile_overage_prices(self):
    """
    Daily detector for quoted-vs-charged overage price divergence.

    THE SIBLING TASK DOES NOT COVER THIS
    ------------------------------------
    `reconcile_subscription_prices` compares a SUBSCRIPTION's Stripe price
    against its local plan. Overage blocks are one-time `mode="payment"`
    charges with their own price id, so nothing in that loop ever looked at
    them — which is why six of nine plans could drift unnoticed until a
    live-QA scenario happened to compare the two.

    WHAT DRIFT COSTS
    ----------------
    The customer is quoted `plan.overage_block_price` and charged whatever
    `stripe_overage_price_id` says. Measured: a school quoted 897 for three
    blocks and charged 1200.

    Detection, not repair — deliberately, and for the same reason as the
    sibling task: which number is right is a pricing decision. The purchase
    paths already REFUSE to quote while a plan is drifted (see
    billing/overage_pricing.py), so this task's job is to raise the alarm
    before a customer runs into that refusal, not to fix it.
    """
    from .overage_pricing import overage_price_drift

    report = overage_price_drift()
    if not report:
        summary = "Overage price reconciliation: no plans with overage pricing."
        logger.info(summary)
        return summary

    drifted = [row for row in report if not row["in_sync"] and not row["error"]]
    unreadable = [row for row in report if row["error"]]

    for row in drifted:
        logger.error(
            "OVERAGE PRICE DRIFT: plan %s quotes %s cents per block but "
            "Stripe price %s charges %s. Purchases on this plan are being "
            "REFUSED until the two agree.",
            row["name"],
            row["local_cents"],
            row["stripe_price_id"],
            row["stripe_cents"],
        )
    for row in unreadable:
        logger.error(
            "OVERAGE PRICE UNREADABLE: plan %s — %s",
            row["name"],
            row["error"],
        )

    summary = (
        f"Overage price reconciliation: {len(report)} plan(s) checked, "
        f"{len(drifted)} drifted, {len(unreadable)} unreadable."
    )
    (logger.error if (drifted or unreadable) else logger.info)(summary)
    return summary


@shared_task(bind=True, max_retries=0)
def reconcile_subscription_prices(self):
    """
    Daily detector for local-plan / Stripe-price divergence.

    WHY THIS IS A SEPARATE TASK, NOT A BRANCH IN
    reconcile_subscription_renewals
    ------------------------------------------------------------------
    That task filters `billing_cycle_end__lte=now` — it only ever looks at
    subscriptions that are OVERDUE. Price drift happens on subscriptions
    that are perfectly CURRENT, so bolting the check onto that loop would
    have inspected precisely the rows that cannot exhibit the problem.

    WHAT IT CATCHES
    ---------------
    The webhook handlers are decorated `@transaction.atomic` and call out to
    Stripe from inside the transaction. The sharp case is
    _handle_individual_upgrade_checkout_completed, which runs
    `stripe.Subscription.modify(...)` and THEN does substantial database
    work (activate_subscription / apply_immediate_plan_change plus saves).
    If that later database work raises, the transaction rolls back — but the
    Stripe call already happened and is not undone. The customer is left
    being billed the new price while our records still say the old plan, and
    nothing else notices: the renewals reconciler compares billing periods
    and subscription status, never price.

    This is detection, not repair. It deliberately makes no correcting write
    — the right correction (charge the customer for what we recorded, or
    record what they were charged) is a money decision for a human. An ERROR
    line per drifted subscription is the alarm.

    COST
    ----
    One `Subscription.list` page per 100 Stripe subscriptions, not one
    retrieve per local row, so the daily bill is a handful of API calls.
    """
    local_subs = list(
        UserSubscription.objects.filter(
            is_active=True,
            is_trial=False,
            stripe_subscription_id__isnull=False,
        )
        .exclude(stripe_subscription_id="")
        .select_related("user", "plan")
    )
    if not local_subs:
        summary = "Subscription price reconciliation: no subscriptions to check."
        logger.info(summary)
        return summary

    # Build {stripe_subscription_id: current_price_id} in bulk.
    stripe_prices = {}
    try:
        listing = stripe.Subscription.list(status="active", limit=100)
        for stripe_sub in listing.auto_paging_iter():
            items = _stripe_get(stripe_sub, "items") or {}
            data = _stripe_get(items, "data") or []
            if not data:
                continue
            price = _stripe_get(data[0], "price") or {}
            price_id = _stripe_get(price, "id")
            if price_id:
                stripe_prices[_stripe_get(stripe_sub, "id")] = price_id
    except stripe.error.StripeError as exc:
        # Without the listing there is nothing to compare against. Fail
        # loudly rather than returning a clean "0 drifted", which would read
        # as an all-clear.
        logger.error(
            "Subscription price reconciliation could not list Stripe "
            "subscriptions, so NO drift check was performed: %s",
            str(exc),
            exc_info=True,
        )
        raise

    checked_count = 0
    drifted_count = 0
    pending_change_count = 0
    unverified_count = 0
    skipped_no_price_count = 0

    for sub in local_subs:
        expected_price_id = sub.plan.stripe_price_id
        if not expected_price_id:
            # Free/manual/offline plans have no Stripe price to diverge from.
            skipped_no_price_count += 1
            continue

        actual_price_id = stripe_prices.get(sub.stripe_subscription_id)
        if actual_price_id is None:
            # Not in the active listing: cancelled, incomplete, past_due, or
            # belonging to another Stripe account. Counted rather than
            # ignored so the summary never overstates coverage.
            unverified_count += 1
            continue

        checked_count += 1
        if actual_price_id == expected_price_id:
            continue

        if sub.pending_plan_id or sub.stripe_schedule_id:
            # A deferred change is mid-flight. Around the cycle boundary
            # Stripe can legitimately show the new price a moment before the
            # webhook records it locally, so this is a WARNING, not an alarm.
            pending_change_count += 1
            logger.warning(
                "Subscription %s (user %s) is on Stripe price %s but plan %s "
                "expects %s. A deferred change is pending (pending_plan=%s, "
                "schedule=%s), so this is most likely the cycle-boundary race "
                "and should clear on the next run.",
                sub.id,
                sub.user.email,
                actual_price_id,
                sub.plan.name,
                expected_price_id,
                sub.pending_plan_id,
                sub.stripe_schedule_id,
            )
            continue

        drifted_count += 1
        logger.error(
            "PRICE DRIFT: subscription %s (user %s) is being billed on Stripe "
            "price %s, but the local plan %s expects price %s. The customer "
            "is paying for something other than what we recorded. No "
            "automatic correction has been made — this needs a human "
            "decision. Stripe subscription: %s",
            sub.id,
            sub.user.email,
            actual_price_id,
            sub.plan.name,
            expected_price_id,
            sub.stripe_subscription_id,
        )

    summary = (
        f"Subscription price reconciliation: "
        f"{checked_count} checked, "
        f"{drifted_count} drifted, "
        f"{pending_change_count} pending-change mismatches, "
        f"{unverified_count} not found in Stripe's active list, "
        f"{skipped_no_price_count} skipped (no Stripe price)."
    )
    logger.info(summary)
    return summary


@shared_task(bind=True, max_retries=0)
def process_stripe_event(self, event_id, claim_token_iso):
    """
    Run one already-claimed Stripe webhook event's handler.

    The HTTP endpoint claims the event (a committed, race-safe conditional
    UPDATE) and returns 200 in milliseconds; this task does the slow part.
    That split is what stops burst traffic being lost: measured on the
    deployed environment, only 2% of `invoice.payment_succeeded` deliveries
    survived burst hours while 100% survived quiet hours, purely because
    the handler held the request open for seconds.

    `claim_token_iso` is the fencing token — the `claimed_at` the endpoint
    wrote. `_finish_stripe_event` only settles the row if that token still
    matches, so a task whose claim was stolen by the stale-claim sweeper
    (because this worker was thought dead) cannot overwrite the new
    owner's result.

    max_retries=0: a failure marks the row FAILED, which is claimable, and
    Stripe's own redelivery is the retry mechanism. Retrying here as well
    would run the handler twice for one delivery.
    """
    from datetime import datetime

    from .models import StripeEvent, StripeEventStatus
    from .webhooks import _EVENT_HANDLERS, _finish_stripe_event, _run_handler_inline

    try:
        row = StripeEvent.objects.get(stripe_event_id=event_id)
    except StripeEvent.DoesNotExist:
        logger.error(
            "process_stripe_event: no StripeEvent row for %s. The claim "
            "should have been committed before this task was queued.",
            event_id,
        )
        return f"missing StripeEvent {event_id}"

    handler = _EVENT_HANDLERS.get(row.event_type)
    if handler is None:
        # The dispatch table changed between claim and execution.
        _finish_stripe_event(
            event_id,
            datetime.fromisoformat(claim_token_iso),
            StripeEventStatus.SUCCEEDED,
        )
        return f"{event_id}: no handler for {row.event_type}"

    # Rebuild the shape the handler expects. The row stores event["data"],
    # so `payload["object"]` is the same object the synchronous path passed.
    event = {"id": event_id, "type": row.event_type, "data": row.payload}

    response = _run_handler_inline(
        event,
        handler,
        datetime.fromisoformat(claim_token_iso),
        log_prefix="Stripe webhook (async)",
    )
    return f"{event_id}: {row.event_type} -> HTTP {response.status_code}"
