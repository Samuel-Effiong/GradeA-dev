import logging
from collections import defaultdict

from dateutil.relativedelta import relativedelta  # type: ignore
from django.conf import settings
from django.db import transaction
from django.db.models import F, Q, Value
from django.db.models.functions import Greatest
from django.template.loader import render_to_string
from django.utils import timezone

from AutoGrader.dispatch import safe_delay
from AutoGrader.tasks import send_email_task
from users.mailerlite_service import queue_sync

from .models import (  # CreditUsageLog,; SubscriptionPlan,
    CONVERSION_FACTOR,
    BetaProfile,
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditUsageLog,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from .subscription_resolver import (
    SOURCE_INDIVIDUAL,
    SOURCE_LICENSE_ADMIN,
    SOURCE_LICENSE_TEACHER,
    resolve_user_billing_context,
)

logger = logging.getLogger(__name__)


class SubscriptionService:

    TRIAL_CREDITS_DISPLAY = 5_000  # User facing value
    TRIAL_CREDITS_RAW = 5_000 * 1_000
    TRIAL_DURATION_DAYS = 14

    @staticmethod
    def _billing_period_delta(plan):

        if plan.interval == BillingInterval.ANNUAL:
            return relativedelta(years=1)
        return relativedelta(months=1)

    @staticmethod
    def _resolve_billing_period(
        plan, period_start=None, period_end=None, *, context=""
    ):
        """
        Decides the local billing period for a new/renewed subscription.

        WHY THIS EXISTS
        ----------------
        These dates used to be computed as `timezone.now() + one interval`,
        where "now" is whenever OUR server happened to process the webhook —
        not the period boundary Stripe actually billed. Local dates therefore
        sat a webhook-latency behind Stripe's, and the offset changed every
        cycle. That made the `billing_cycle_end > now` idempotency guard
        probabilistic: after a slow cycle followed by a fast one, a genuine
        renewal webhook lands while billing_cycle_end is still in the future
        and is silently swallowed, so the customer's credits only arrive when
        the next nightly reconcile sweep runs.

        When Stripe's authoritative period is supplied, it wins. Everything
        downstream keeps comparing `billing_cycle_end > timezone.now()`
        against the local database exactly as before — Stripe is the source
        of truth, this table is its synchronized projection, and no ordinary
        billing decision needs a live Stripe call.

        FALLBACK IS DELIBERATE, NOT DEFENSIVE PADDING
        ----------------------------------------------
        A malformed or nonsensical period is worse than no period at all: an
        end date in the past would make the subscription instantly "due"
        again (a renewal loop that re-grants credits every sweep), and a
        wildly future one would hand out free service. So a period is used
        only if it is internally consistent AND still open. Otherwise this
        falls back to the old wall-clock computation and logs loudly, which
        is the previous behaviour — never worse than today.

        Returns (cycle_start, cycle_end, credit_grant_at).
        """
        now = timezone.now()

        def _wall_clock():
            cycle_end = now + SubscriptionService._billing_period_delta(plan)
            grant_at = (
                now + relativedelta(months=1)
                if plan.interval == BillingInterval.ANNUAL
                else cycle_end
            )
            return now, cycle_end, grant_at

        if period_start is None or period_end is None:
            # No Stripe period available (trial activation, offline flows,
            # direct admin activation). Not an error — the wall clock is the
            # correct authority when Stripe isn't driving the change.
            return _wall_clock()

        reason = None
        if period_end <= period_start:
            reason = "period_end is not after period_start"
        elif period_end <= now:
            reason = (
                "period_end has already elapsed, which would leave the "
                "subscription instantly due for renewal again"
            )

        if reason:
            logger.warning(
                "Ignoring Stripe billing period for %s (%s -> %s): %s. "
                "Falling back to a wall-clock period. This should not happen "
                "— investigate the Stripe payload rather than dismissing it.",
                context or plan.name,
                period_start.isoformat() if period_start else None,
                period_end.isoformat() if period_end else None,
                reason,
            )
            return _wall_clock()

        if plan.interval == BillingInterval.ANNUAL:
            # Credits still refresh monthly on an annual plan, anchored to
            # the real period start so the monthly clock cannot drift either.
            grant_at = period_start + relativedelta(months=1)
            if grant_at <= now:
                # A badly delayed webhook could otherwise mint a MONTHLY
                # bucket that is already expired. Keep the grant in the
                # future; process_annual_plan_credit_grants catches up.
                grant_at = now + relativedelta(months=1)
            grant_at = min(grant_at, period_end)
        else:
            grant_at = period_end

        return period_start, period_end, grant_at

    @staticmethod
    @transaction.atomic
    def activate_subscription(user, plan, *, period_start=None, period_end=None):
        """
        Handles the entire lifecycle of a subscription change

        Ensures atomicity across:
        1. Deactivating old plans
        2. Creating new plan
        3. Initializing wallet
        4. Crediting credits
        5. Auditing

        billing_cycle_end tracks the REAL billing/contract cycle (matches
        whatever Stripe is actually charging on — 1 month or 1 year).
        monthly_bucket_expiry tracks when the current credit bucket runs out,
        which for ANNUAL plans is intentionally shorter than billing_cycle_end
        — credits still refresh monthly even though Stripe only bills yearly.
        For MONTHLY plans these two dates are always the same value.

        Args:
            period_start / period_end: Stripe's authoritative billing period
                for the cycle being activated. Callers driven by a Stripe
                event (renewal invoice, interval-crossing upgrade) should
                pass these so the local dates mirror Stripe exactly instead
                of drifting by however long the webhook took to arrive; see
                _resolve_billing_period. Omitting them keeps the historical
                wall-clock behaviour, which stays correct for flows Stripe
                does not drive.
        """

        if plan.name == PlanType.BETA and not user.is_beta_eligible():
            raise ValueError("The Beta plan is restricted to teacher accounts.")

        cycle_start, billing_end, monthly_bucket_expiry = (
            SubscriptionService._resolve_billing_period(
                plan,
                period_start,
                period_end,
                context=f"activate_subscription(user={user.email}, plan={plan.name})",
            )
        )

        # 1. Deactivate any existing active subscriptions
        UserSubscription.objects.filter(user=user, is_active=True).update(
            is_active=False
        )

        # 2. Create new UserSubscription
        subscription = UserSubscription.objects.create(
            user=user,
            plan=plan,
            is_active=True,
            billing_cycle_start=cycle_start,
            billing_cycle_end=billing_end,
            next_credit_grant_at=monthly_bucket_expiry,
            auto_renew=True,
        )

        # 3. Handle Wallet and Initial Credit Injection
        now = timezone.now()
        wallet, _ = CreditWallet.objects.get_or_create(user=user)

        # --- Trial forfeiture phase ---
        # activate_subscription is the generic "grant this user a real
        # paid plan" entry point, reached from more than one caller
        # (this fresh-signup path is also what
        # StripeWebhookHandler._handle_individual_checkout falls back to
        # whenever checkout completes WITHOUT a trial_subscription_id in
        # its metadata). Every new teacher gets an automatic 14-day,
        # 5,000-credit TRIAL bucket on signup (activate_automatic_free_
        # trial), independent of what they do next — without this step,
        # a user who converts to paid through any path that doesn't
        # explicitly route through finalize_trial_to_paid_conversion
        # keeps that trial balance stacked on top of their new plan's
        # grant for whatever remains of the trial's 14 days. Mirrors
        # finalize_trial_to_paid_conversion's own forfeiture step, which
        # only covers the ONE path that already knows to call it.
        trial_bucket = (
            wallet.buckets.select_for_update()
            .filter(
                bucket_type=CreditBucketType.TRIAL,
                is_processed=False,
                expires_at__gt=now,
            )
            .first()
        )
        if trial_bucket:
            unused = trial_bucket.remaining_credits
            if unused > 0:
                CreditLedger.objects.create(
                    user=user,
                    bucket=trial_bucket,
                    ledger_type=CreditLedgerType.EXPIRE,
                    amount=unused,
                    reference=(
                        f"Trial credits forfeited on activating "
                        f"{plan.display_name or plan.name}"
                    ),
                    metadata={
                        "expired_amount": unused,
                        "new_plan_id": str(plan.id),
                        "conversion_type": "ACTIVATE_SUBSCRIPTION_FALLBACK",
                    },
                )
            trial_bucket.expires_at = now
            trial_bucket.is_processed = True
            trial_bucket.save(
                update_fields=["expires_at", "is_processed", "updated_at"]
            )
            logger.info(
                "Expired TRIAL bucket %s for user %s on activate_subscription "
                "(forfeited %d credits).",
                trial_bucket.id,
                user.email,
                unused,
            )

        # --- The cleanup pahse (Handling existing credits for upgrades)
        active_monthly = (
            wallet.buckets.select_for_update()
            .filter(bucket_type=CreditBucketType.MONTHLY, expires_at__gt=now)
            .order_by("created_at")
            .first()
        )

        if active_monthly:
            unused = active_monthly.remaining_credits

            if unused > 0:
                # We use the NEW Plan's rollover rules to be generous
                rollover_amount, cap_meta = wallet.compute_capped_rollover(
                    plan, unused, now=now
                )

                if rollover_amount > 0:
                    # Create the Carry over bucket
                    expiry = now + relativedelta(
                        months=1 * plan.carry_over_expiry_months
                    )

                    bucket = CreditBucket.objects.create(
                        wallet=wallet,
                        bucket_type=CreditBucketType.CARRY_OVER,
                        total_credits=rollover_amount,
                        used_credits=0,
                        expires_at=expiry,
                    )

                    CreditLedger.objects.create(
                        user=user,
                        bucket=bucket,
                        ledger_type=CreditLedgerType.GRANT,
                        amount=rollover_amount,
                        reference=f"Upgrade Rollover from expired {active_monthly.bucket_type} bucket",
                        metadata={
                            "previous_bucket_id": str(active_monthly.id),
                            **cap_meta,
                        },
                    )
                elif cap_meta["requested_rollover"] > 0:
                    # Fully suppressed by max_bank — nothing granted, so
                    # there's no bucket/ledger to attach metadata to.
                    # Logged for visibility only.
                    logger.info(
                        "Rollover fully suppressed by max_bank for user %s: "
                        "requested %d, room 0 (%s).",
                        user.email,
                        cap_meta["requested_rollover"],
                        cap_meta,
                    )

            # Crucial: Delete or expire the old monthly bucket so they don't have two active monthly buckets
            active_monthly.expires_at = now
            active_monthly.save(update_fields=["expires_at"])

        # 4. Ensure we reset overage usage for the new cycle
        wallet.overage_blocks_used = 0
        wallet.save(update_fields=["overage_blocks_used"])

        # 5. Create the MONTHLY Credit Bucket
        bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=plan.monthly_credits,
            used_credits=0,
            expires_at=monthly_bucket_expiry,
        )

        # 6 Create immutable audit ledger
        CreditLedger.objects.create(
            user=user,
            bucket=bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=plan.monthly_credits,
            reference=f"Initial allocation for {plan.display_name or plan.name}",
            metadata={"subscription_id": str(subscription.id)},
        )

        queue_sync(user)

        return subscription

    @staticmethod
    @transaction.atomic
    def apply_immediate_plan_change(user_sub, new_plan):
        """
        Swaps `user_sub` onto `new_plan` IN PLACE, for the specific case
        where Stripe's own billing_cycle_anchor has NOT moved — i.e. a
        same-interval plan change applied via a plain
        `stripe.Subscription.modify(items=[...])` price swap (upgrade or
        the "no additional charge needed" branch). Stripe does not reset a
        subscription's billing/renewal date just because an item's price
        changed, so this method must not either.

        Contrast with `activate_subscription()`, which unconditionally
        resets billing_cycle_start/billing_cycle_end/next_credit_grant_at
        to "now + one period" — correct ONLY when Stripe's cycle has
        genuinely just reset (a brand new Stripe subscription from
        checkout, a real periodic renewal, or an INTERVAL-CROSSING change
        e.g. MONTHLY -> ANNUAL, which Stripe itself treats as starting a
        fresh billing period). Calling activate_subscription() instead of
        this method for a same-interval immediate upgrade was the root
        cause of local billing_cycle_end permanently drifting away from
        Stripe's real invoice date — silently swallowing the next real
        renewal's credit rollover, and feeding a wrong "effective date"
        into any later scheduled downgrade built from billing_cycle_end.

        What this does:
        1. Rolls over unused credits in the current MONTHLY bucket into a
           CARRY_OVER bucket, under new_plan's rollover rules — identical
           math to activate_subscription()'s cleanup phase.
        2. Grants a fresh MONTHLY bucket sized for new_plan, expiring at
           the SAME next_credit_grant_at/billing_cycle_end the
           subscription already had — that clock is untouched by a
           same-interval price swap.
        3. Updates `user_sub.plan` to new_plan on the SAME row (no
           deactivate-and-recreate) — Stripe's subscription identity
           didn't change either.
        4. Clears pending_plan/pending_change_type/pending_change_note/
           stripe_schedule_id — an immediate change always supersedes
           anything previously scheduled. Callers are still responsible
           for releasing the Stripe-side SubscriptionSchedule (if any)
           BEFORE calling this, same convention as every other mutation
           in this module.
        5. Does NOT touch billing_cycle_start, billing_cycle_end,
           next_credit_grant_at, auto_renew, overage_blocks_used,
           stripe_subscription_id, or stripe_customer_id — none of those
           changed on Stripe's side, so none of them change here.

        Args:
            user_sub (UserSubscription): The current active, non-trial
                subscription being upgraded. Must already have a fresh
                `.plan` (e.g. from a row lock taken by the caller).
            new_plan (SubscriptionPlan): The plan being switched to.
                Must be the SAME billing interval as user_sub.plan —
                callers must route interval-crossing changes through
                activate_subscription() instead.

        Returns:
            UserSubscription: The same row, now on new_plan.

        Raises:
            ValueError: If user_sub is not active, or if new_plan's
                interval differs from user_sub.plan's interval (defensive
                — this method must never be used for an interval-crossing
                change).
        """
        user_sub = UserSubscription.objects.select_for_update().get(pk=user_sub.pk)

        if not user_sub.is_active:
            raise ValueError(
                f"Subscription {user_sub.id} is not active — cannot apply an "
                "immediate plan change to it."
            )

        old_plan = user_sub.plan
        if old_plan.interval != new_plan.interval:
            raise ValueError(
                f"apply_immediate_plan_change cannot cross billing intervals "
                f"({old_plan.interval} -> {new_plan.interval}) — Stripe resets "
                "the billing cycle anchor for an interval change, so that case "
                "must go through activate_subscription() instead."
            )

        user = user_sub.user
        now = timezone.now()
        wallet, _ = CreditWallet.objects.get_or_create(user=user)

        # --- Roll over unused credits from the bucket being replaced ---
        active_monthly = (
            wallet.buckets.select_for_update()
            .filter(bucket_type=CreditBucketType.MONTHLY, expires_at__gt=now)
            .order_by("created_at")
            .first()
        )

        if active_monthly:
            unused = active_monthly.remaining_credits

            if unused > 0:
                rollover_amount, cap_meta = wallet.compute_capped_rollover(
                    new_plan, unused, now=now
                )

                if rollover_amount > 0:
                    expiry = now + relativedelta(
                        months=1 * new_plan.carry_over_expiry_months
                    )
                    carry_bucket = CreditBucket.objects.create(
                        wallet=wallet,
                        bucket_type=CreditBucketType.CARRY_OVER,
                        total_credits=rollover_amount,
                        used_credits=0,
                        expires_at=expiry,
                    )
                    CreditLedger.objects.create(
                        user=user,
                        bucket=carry_bucket,
                        ledger_type=CreditLedgerType.GRANT,
                        amount=rollover_amount,
                        reference=(
                            f"Rollover from {old_plan.name} on immediate "
                            f"upgrade to {new_plan.name}"
                        ),
                        metadata={
                            "previous_bucket_id": str(active_monthly.id),
                            "subscription_id": str(user_sub.id),
                            **cap_meta,
                        },
                    )
                elif cap_meta["requested_rollover"] > 0:
                    logger.info(
                        "Immediate-upgrade rollover fully suppressed by "
                        "max_bank for user %s (%s -> %s): requested %d (%s).",
                        user.email,
                        old_plan.name,
                        new_plan.name,
                        cap_meta["requested_rollover"],
                        cap_meta,
                    )

            active_monthly.expires_at = now
            active_monthly.save(update_fields=["expires_at", "updated_at"])

        # --- Grant the new plan's MONTHLY bucket, on the EXISTING clock ---
        new_bucket_expiry = user_sub.next_credit_grant_at or user_sub.billing_cycle_end

        new_bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=new_plan.monthly_credits,
            used_credits=0,
            expires_at=new_bucket_expiry,
        )

        CreditLedger.objects.create(
            user=user,
            bucket=new_bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=new_plan.monthly_credits,
            reference=f"Immediate upgrade from {old_plan.name} to {new_plan.name}",
            metadata={
                "subscription_id": str(user_sub.id),
                "grant_type": "IMMEDIATE_PLAN_CHANGE",
                "previous_plan_id": str(old_plan.id),
            },
        )

        # --- Swap the plan in place; clear any superseded scheduled change ---
        user_sub.plan = new_plan
        user_sub.pending_plan = None
        user_sub.pending_change_type = None
        user_sub.pending_change_note = None
        user_sub.stripe_schedule_id = None
        user_sub.save(
            update_fields=[
                "plan",
                "pending_plan",
                "pending_change_type",
                "pending_change_note",
                "stripe_schedule_id",
                "updated_at",
            ]
        )

        logger.info(
            "Applied immediate same-interval plan change for user %s "
            "(subscription %s): %s -> %s. billing_cycle_end unchanged (%s).",
            user.email,
            user_sub.id,
            old_plan.name,
            new_plan.name,
            user_sub.billing_cycle_end.isoformat(),
        )

        queue_sync(user)

        return user_sub

    @staticmethod
    @transaction.atomic
    def process_mid_cycle_credit_grant(user_subscription):
        """
        For ANNUAL-interval plans only. Stripe bills once a year, but credits
        still refresh monthly throughout that year. This grants the next
        month's MONTHLY bucket WITHOUT creating a new UserSubscription row,
        without touching Stripe, and without resolving pending_plan — none of
        that happens until the real annual renewal at billing_cycle_end.
        Rollover of unused credits between months follows the same
        carry_over_percent/carry_over_max rules as a normal full renewal.
        """
        user_subscription = UserSubscription.objects.select_for_update().get(
            id=user_subscription.id
        )
        plan = user_subscription.plan
        if plan.interval != BillingInterval.ANNUAL:
            raise ValueError(
                f"process_mid_cycle_credit_grant called on a {plan.interval} "
                "plan; only ANNUAL plans use mid-cycle credit grants."
            )

        user = user_subscription.user
        now = timezone.now()
        wallet = user.credit_wallet

        # is_processed=False + explicit ordering, matching the two sibling
        # rollover implementations (process_rollover_and_renewal below, and
        # LicenseSubscriptionService._rollover_and_grant_monthly_bucket).
        #
        # Without BOTH of these this picks the wrong bucket from the second
        # mid-cycle grant onward: CreditBucket.Meta.ordering is
        # ["expires_at", "created_at"], and a retired bucket's expires_at is
        # set to its retirement time — always earlier than the live bucket's
        # expiry — so an unordered .first() keeps returning month 1's bucket
        # for the rest of the year. That both re-rolls month 1 repeatedly and
        # silently drops months 2..11's genuinely unused credits.
        old_monthly = (
            wallet.buckets.select_for_update()
            .filter(bucket_type=CreditBucketType.MONTHLY, is_processed=False)
            .order_by("-created_at")
            .first()
        )

        if old_monthly:
            # NOT old_monthly.remaining_credits — this task runs for subs
            # whose next_credit_grant_at (== this bucket's expires_at) has
            # already passed, so that property would already read 0 and
            # silently suppress every mid-cycle rollover. Use the raw
            # total-minus-used instead, same fix as expire_trial()'s
            # analogous bug.
            unused = max(0, old_monthly.total_credits - old_monthly.used_credits)
            if unused > 0:
                rollover_amount, cap_meta = wallet.compute_capped_rollover(
                    plan, unused, now=now
                )
                if rollover_amount > 0:
                    expiry = now + relativedelta(
                        months=1 * plan.carry_over_expiry_months
                    )
                    carry_bucket = CreditBucket.objects.create(
                        wallet=wallet,
                        bucket_type=CreditBucketType.CARRY_OVER,
                        total_credits=rollover_amount,
                        used_credits=0,
                        expires_at=expiry,
                    )
                    CreditLedger.objects.create(
                        user=user,
                        bucket=carry_bucket,
                        ledger_type=CreditLedgerType.GRANT,
                        amount=rollover_amount,
                        reference=f"Mid-cycle rollover within annual plan {plan.name}",
                        metadata={
                            "previous_unused": unused,
                            "subscription_id": str(user_subscription.id),
                            **cap_meta,
                        },
                    )
                elif cap_meta["requested_rollover"] > 0:
                    logger.info(
                        "Mid-cycle rollover fully suppressed by max_bank "
                        "for user %s (subscription %s): requested %d (%s).",
                        user.email,
                        user_subscription.id,
                        cap_meta["requested_rollover"],
                        cap_meta,
                    )
            old_monthly.expires_at = now
            # is_processed=True is what makes the selector above correct on
            # the NEXT grant. It also means cleanup_expired_credit_buckets no
            # longer writes an EXPIRE row for this bucket's post-rollover
            # remainder — which is already how both renewal paths behave
            # (process_rollover_and_renewal, _rollover_and_grant_monthly_bucket),
            # so this makes mid-cycle consistent with them rather than
            # introducing a new policy. Deliberately NOT expire_bucket(): that
            # logs the full total-minus-used, including the slice just
            # re-granted as CARRY_OVER, double-counting it in the ledger.
            old_monthly.is_processed = True
            old_monthly.save(update_fields=["expires_at", "is_processed", "updated_at"])

        next_grant_at = now + relativedelta(months=1)
        # Never let the bucket outlive the actual annual contract — in the
        # final partial month, cap it at billing_cycle_end so the real
        # annual renewal (rollover + possible plan change + Stripe price
        # sync) takes over cleanly instead of overlapping with this grant.
        bucket_expiry = min(next_grant_at, user_subscription.billing_cycle_end)

        new_bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=plan.monthly_credits,
            used_credits=0,
            expires_at=bucket_expiry,
        )
        CreditLedger.objects.create(
            user=user,
            bucket=new_bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=plan.monthly_credits,
            reference=f"Mid-cycle monthly credit grant for annual plan {plan.display_name or plan.name}",
            metadata={
                "subscription_id": str(user_subscription.id),
                "grant_type": "ANNUAL_MID_CYCLE",
            },
        )

        wallet.overage_blocks_used = 0
        wallet.save(update_fields=["overage_blocks_used", "updated_at"])

        user_subscription.next_credit_grant_at = bucket_expiry
        user_subscription.save(update_fields=["next_credit_grant_at", "updated_at"])

        logger.info(
            "Mid-cycle credit grant for annual subscription %s (user %s): "
            "%d credits, next grant at %s.",
            user_subscription.id,
            user.email,
            plan.monthly_credits,
            bucket_expiry,
        )
        return user_subscription

    @staticmethod
    @transaction.atomic
    def process_rollover_and_renewal(
        user_subscription, *, period_start=None, period_end=None
    ):
        """
        Executed at billing_cycle_end — by the invoice.payment_succeeded
        webhook normally, or by the nightly reconcile sweep as a fallback.

        `period_start`/`period_end` are Stripe's authoritative period for
        the NEW cycle, taken from the renewal invoice. Passing them keeps
        the local cycle aligned with Stripe's instead of drifting by the
        webhook's processing latency every month; see
        _resolve_billing_period for what happens when they are absent or
        implausible.
        """

        # Lock the subscription row to prevent concurrent processing
        user_subscription = UserSubscription.objects.select_for_update().get(
            pk=user_subscription.pk
        )

        user = user_subscription.user

        # If there's a pending plan, use it; otherwise, renew the current one
        target_plan = user_subscription.pending_plan or user_subscription.plan
        old_plan = user_subscription.plan

        now = timezone.now()

        wallet = user.credit_wallet
        old_monthly_bucket = (
            wallet.buckets.select_for_update()
            .filter(bucket_type="MONTHLY", is_processed=False)
            .order_by("-created_at")
            .first()
        )

        if old_monthly_bucket:
            unused_credits = max(
                0, old_monthly_bucket.total_credits - old_monthly_bucket.used_credits
            )

            if unused_credits > 0:
                final_rollover_amount, cap_meta = wallet.compute_capped_rollover(
                    target_plan, unused_credits, now=now
                )

                if final_rollover_amount > 0:
                    # Create the Carry over bucket
                    expiry_date = now + relativedelta(
                        months=1 * target_plan.carry_over_expiry_months
                    )

                    carry_bucket = CreditBucket.objects.create(
                        wallet=wallet,
                        bucket_type=CreditBucketType.CARRY_OVER,
                        total_credits=final_rollover_amount,
                        used_credits=0,
                        expires_at=expiry_date,
                    )

                    CreditLedger.objects.create(
                        user=user,
                        bucket=carry_bucket,
                        ledger_type=CreditLedgerType.GRANT,
                        amount=final_rollover_amount,
                        reference=f"Rollover from {old_plan.name} to {target_plan.name}",
                        metadata={
                            "previous_unused": unused_credits,
                            "rollover_applied_percent": str(
                                target_plan.carry_over_percent
                            ),
                            **cap_meta,
                        },
                    )
                elif cap_meta["requested_rollover"] > 0:
                    logger.info(
                        "Renewal rollover fully suppressed by max_bank for "
                        "user %s (%s -> %s): requested %d (%s).",
                        user.email,
                        old_plan.name,
                        target_plan.name,
                        cap_meta["requested_rollover"],
                        cap_meta,
                    )

            # Retire the Old Bucket
            old_monthly_bucket.expires_at = now
            old_monthly_bucket.is_processed = True
            old_monthly_bucket.save(
                update_fields=["expires_at", "is_processed", "updated_at"]
            )

        # Trigger the new activation
        return SubscriptionService.activate_subscription(
            user, target_plan, period_start=period_start, period_end=period_end
        )

    @staticmethod
    @transaction.atomic
    def schedule_plan_change(user, new_plan, change_type, note, stripe_schedule_id):
        """
        Persists a plan change that's been scheduled to take effect at
        billing_cycle_end. This is the LOCAL half of scheduling — the
        Stripe-side half (creating/updating the actual
        SubscriptionSchedule that makes Stripe bill the correct price at
        the correct moment) must already have happened before this is
        called; `stripe_schedule_id` is required specifically so that
        can't be skipped.

        `change_type` and `note` are persisted (not just returned in the
        API response) so the frontend can show the user an accurate,
        stable explanation on every subsequent visit — not only in the
        one-time response to the request that scheduled it.

        auto_renew is deliberately left untouched (stays True): the
        subscription still renews, just onto `new_plan`.

        Calling this again while something is already scheduled simply
        overwrites all four fields with the new selection — the most
        recent choice always wins.

        Args:
            user (CustomUser): The user requesting the change.
            new_plan (SubscriptionPlan): The plan to switch to at cycle end.
            change_type (str): One of PendingChangeType's values.
            note (str): Persisted, user-facing explanation, already fully
                composed by the caller.
            stripe_schedule_id (str): The Stripe SubscriptionSchedule ID
                already created/updated to enforce this change on Stripe's
                side. Required — see module docstring for why.

        Returns:
            UserSubscription: The updated (still currently-active) subscription.

        Raises:
            ValueError: If the user has no active subscription, or if
                stripe_schedule_id is falsy (defensive — this should never
                happen given the type hint, but a silent local-only
                schedule is exactly the bug this field exists to prevent).
        """
        if not stripe_schedule_id:
            raise ValueError(
                "stripe_schedule_id is required to schedule a plan change — "
                "the Stripe-side schedule must be created before persisting "
                "the pending change locally."
            )

        current_sub = (
            UserSubscription.objects.select_for_update()
            .filter(user=user, is_active=True)
            .select_related("plan")
            .first()
        )

        if not current_sub:
            raise ValueError("No active subscription to schedule a change for.")

        current_sub.pending_plan = new_plan
        current_sub.pending_change_type = change_type
        current_sub.pending_change_note = note
        current_sub.stripe_schedule_id = stripe_schedule_id
        current_sub.save(
            update_fields=[
                "pending_plan",
                "pending_change_type",
                "pending_change_note",
                "stripe_schedule_id",
                "updated_at",
            ]
        )

        logger.info(
            "Plan change scheduled for user %s: %s -> %s (type=%s, "
            "stripe_schedule=%s), effective %s.",
            user.email,
            current_sub.plan.name,
            new_plan.name,
            change_type,
            stripe_schedule_id,
            current_sub.billing_cycle_end.isoformat(),
        )
        return current_sub

    @staticmethod
    @transaction.atomic
    def cancel_scheduled_plan_change(user):
        """
        Clears ANY previously-scheduled plan change (downgrade, deferred
        upgrade, or lateral interval switch) so the subscription simply
        renews onto its current plan as normal.

        Pure local-DB operation — does NOT call Stripe. The caller MUST
        release the Stripe-side SubscriptionSchedule (see
        StripeSubscriptionScheduleService.release_schedule) BEFORE calling
        this, so a failed Stripe call doesn't leave local state claiming
        "cancelled" while Stripe still executes the old transition.

        Idempotent: if nothing is scheduled, this is a harmless no-op that
        still returns the current subscription rather than raising.

        Args:
            user (CustomUser): The user cancelling their scheduled change.

        Returns:
            UserSubscription: The updated subscription, with pending_plan,
                pending_change_type, pending_change_note, and
                stripe_schedule_id all cleared.

        Raises:
            ValueError: If the user has no active subscription at all.
        """
        current_sub = (
            UserSubscription.objects.select_for_update()
            .filter(user=user, is_active=True)
            .select_related("plan")
            .first()
        )

        if not current_sub:
            raise ValueError("No active subscription found.")

        if current_sub.pending_plan_id:
            previous_pending = current_sub.pending_plan
            current_sub.pending_plan = None
            current_sub.pending_change_type = None
            current_sub.pending_change_note = None
            current_sub.stripe_schedule_id = None
            current_sub.save(
                update_fields=[
                    "pending_plan",
                    "pending_change_type",
                    "pending_change_note",
                    "stripe_schedule_id",
                    "updated_at",
                ]
            )
            logger.info(
                "Cancelled scheduled plan change for user %s (was pending " "-> %s).",
                user.email,
                previous_pending.name if previous_pending else "unknown",
            )

        return current_sub

    # @staticmethod
    # @transaction.atomic
    # def cancel_scheduled_downgrade(user):
    #     """
    #     Clears a previously-scheduled downgrade (pending_plan) so the
    #     subscription simply renews onto its current plan as normal.

    #     Idempotent: if nothing is pending, this is a harmless no-op that still
    #     returns the current subscription (does NOT raise) — callers that just
    #     want to guarantee "no downgrade pending" after calling this don't need
    #     to special-case "there wasn't one to begin with".

    #     Args:
    #         user (CustomUser): The user cancelling their scheduled downgrade.

    #     Returns:
    #         UserSubscription: The updated subscription, with pending_plan=None.

    #     Raises:
    #         ValueError: If the user has no active subscription at all.
    #     """
    #     current_sub = (
    #         UserSubscription.objects.select_for_update()
    #         .filter(user=user, is_active=True)
    #         .select_related("plan", "pending_plan")
    #         .first()
    #     )

    #     if not current_sub:
    #         raise ValueError("No active subscription found.")

    #     if current_sub.pending_plan_id:
    #         previous_pending = current_sub.pending_plan
    #         current_sub.pending_plan = None
    #         current_sub.save(update_fields=["pending_plan", "updated_at"])
    #         logger.info(
    #             "Cancelled scheduled downgrade for user %s (was pending -> %s).",
    #             user.email,
    #             previous_pending.name if previous_pending else "unknown",
    #         )

    #     return current_sub

    @staticmethod
    @transaction.atomic
    def expire_bucket(bucket):
        """
        Formalizes the loss of credits due to expiration
        """
        bucket = CreditBucket.objects.select_for_update().get(pk=bucket.pk)
        unused_amount = max(0, bucket.total_credits - bucket.used_credits)

        if unused_amount > 0:
            # Create the `EXPIRE' ledger entry to balance the books
            CreditLedger.objects.create(
                user=bucket.wallet.user,
                bucket=bucket,
                ledger_type=CreditLedgerType.EXPIRE,
                amount=unused_amount,
                reference=f"Automatic expiration of {bucket.bucket_type} bucket.",
                metadata={
                    "expired_amount": unused_amount,
                    "total_at_start": bucket.total_credits,
                    "used_before_expiration": bucket.used_credits,
                },
            )

        bucket.is_processed = True
        bucket.save(update_fields=["is_processed", "updated_at"])
        return unused_amount

    @staticmethod
    @transaction.atomic
    def activate_free_trial(user, plan):
        """
        Starts a free trial for an INDIVIDUAL plan.

        Rules enforced:
        - One trial ever per user, across all time (checks historical subscriptions).
        - Only INDIVIDUAL-category plans are eligible; LICENSE plans have no trial.
        - User must not already have an active subscription of any kind.
        - Grants exactly 5,000 display credits (5,000,000 raw) in a TRIAL bucket
        that expires at trial_end (14 days from activation).
        - The subscription is marked is_trial=True and trial_end is set.
        - No carry-over rollover is applied when a trial ends — trial credits
        simply expire; any unused amount is logged with EXPIRE ledger entry.

        Args:
            user (CustomUser): The user starting the trial.
            plan (SubscriptionPlan): The plan to trial. Must be INDIVIDUAL category.

        Returns:
            UserSubscription: The newly created trial subscription.

        Raises:
            ValueError: If the user has already used a trial, if the plan is not
                        INDIVIDUAL category, or if the user has an active subscription.
        """
        # from .models import PlanCategory  # local import avoids circular import risk

        # Guard 1 — only INDIVIDUAL plans have a free trial
        if plan.category != PlanCategory.INDIVIDUAL:
            raise ValueError(
                f"Free trials are only available for INDIVIDUAL plans, "
                f"not {plan.category}."
            )

        # Guard 2 — one trial per user, ever (check entire subscription history)
        already_trialled = UserSubscription.objects.filter(
            user=user,
            is_trial=True,
        ).exists()
        if already_trialled:
            raise ValueError(
                "This account has already used its free trial. "
                "Please subscribe to a paid plan."
            )

        # Guard 3 — must not have an active subscription already
        active_sub = UserSubscription.objects.filter(user=user, is_active=True).first()
        if active_sub:
            raise ValueError(
                "Cannot start a free trial while an active subscription exists. "
                "Cancel the current subscription first."
            )

        now = timezone.now()
        trial_end = now + relativedelta(days=SubscriptionService.TRIAL_DURATION_DAYS)

        # Create the trial subscription.
        # billing_cycle_end matches trial_end — the "billing cycle" for a trial
        # is the trial window itself. Celery's renewal pipeline reads billing_cycle_end
        # to decide when to act, so this keeps trial expiry in the same pipeline.
        subscription = UserSubscription.objects.create(
            user=user,
            plan=plan,
            is_active=True,
            is_trial=True,
            trial_end=trial_end,
            billing_cycle_start=now,
            billing_cycle_end=trial_end,
            auto_renew=False,  # trial never auto-renews into a paid sub without explicit action
        )

        # Ensure wallet exists
        wallet, _ = CreditWallet.objects.get_or_create(user=user)

        # Reset overage counter for this new cycle
        wallet.overage_blocks_used = 0
        wallet.save(update_fields=["overage_blocks_used"])

        # Create the TRIAL credit bucket
        trial_bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.TRIAL,
            total_credits=SubscriptionService.TRIAL_CREDITS_RAW,
            used_credits=0,
            expires_at=trial_end,
        )

        # Immutable audit ledger entry
        CreditLedger.objects.create(
            user=user,
            bucket=trial_bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=SubscriptionService.TRIAL_CREDITS_RAW,
            reference=f"Free trial activation for {plan.display_name or plan.name}",
            metadata={
                "grant_type": "FREE_TRIAL",
                "display_amount": SubscriptionService.TRIAL_CREDITS_DISPLAY,
                "raw_amount": SubscriptionService.TRIAL_CREDITS_RAW,
                "trial_duration_days": SubscriptionService.TRIAL_DURATION_DAYS,
                "trial_end": trial_end.isoformat(),
                "subscription_id": str(subscription.id),
            },
        )

        logger.info(
            "Free trial activated for user %s on plan %s. "
            "Trial ends %s. Subscription ID: %s. Credits: %d display (%d raw).",
            user.email,
            plan.name,
            trial_end.isoformat(),
            subscription.id,
            SubscriptionService.TRIAL_CREDITS_DISPLAY,
            SubscriptionService.TRIAL_CREDITS_RAW,
        )

        queue_sync(user)

        return subscription

    @staticmethod
    @transaction.atomic
    def expire_trial(user_subscription, force=False):
        """
        Called by Celery when a trial subscription's billing_cycle_end (= trial_end)
        has passed and the user has NOT converted to a paid plan.

        What this does:
        1. Expires any remaining TRIAL bucket credits (logs EXPIRE ledger entry).
        2. Marks the subscription is_active=False, is_trial=False.
        3. Does NOT create a new subscription — the user returns to having no sub.

        The user can still sign up for a paid plan after this — activate_subscription()
        handles users with no current subscription cleanly.

        Args:
            user_subscription (UserSubscription): The expired trial subscription.
            force (bool): Skip the "trial_end has passed" guard. Needed by
                expire_active_trials()'s credit-exhaustion path, which
                deliberately expires a trial early — before its natural
                trial_end — once its credits run out. Time-based expiry
                (the default, force=False) should never bypass this guard.

        Raises:
            ValueError: If the subscription is not a trial, or if the trial has not
                        yet ended (unless force=True).
        """
        if not user_subscription.is_trial:
            raise ValueError(
                f"Subscription {user_subscription.id} is not a trial subscription."
            )

        now = timezone.now()

        if (
            not force
            and user_subscription.trial_end
            and user_subscription.trial_end > now
        ):
            raise ValueError(
                f"Trial for subscription {user_subscription.id} has not ended yet. "
                f"Trial end: {user_subscription.trial_end}"
            )

        user = user_subscription.user

        # Expire any remaining TRIAL bucket
        wallet = user.credit_wallet
        trial_bucket = (
            wallet.buckets.select_for_update()
            .filter(bucket_type=CreditBucketType.TRIAL)
            .first()
        )

        if trial_bucket:
            # NOT trial_bucket.remaining_credits — that property returns 0
            # once expires_at has passed, which is normally already true by
            # the time this runs (we're here BECAUSE trial_end passed). Use
            # the raw total-minus-used so genuinely-unused credits still
            # get logged as forfeited instead of silently vanishing.
            unused = max(0, trial_bucket.total_credits - trial_bucket.used_credits)
            if unused > 0:
                CreditLedger.objects.create(
                    user=user,
                    bucket=trial_bucket,
                    ledger_type=CreditLedgerType.EXPIRE,
                    amount=unused,
                    reference="Free trial expired — unused trial credits forfeited.",
                    metadata={
                        "expired_amount": unused,
                        "total_at_start": trial_bucket.total_credits,
                        "used_before_expiration": trial_bucket.used_credits,
                        "trial_end": (
                            user_subscription.trial_end.isoformat()
                            if user_subscription.trial_end
                            else None
                        ),
                        "subscription_id": str(user_subscription.id),
                    },
                )
            # Mark the bucket itself as processed/expired
            trial_bucket.expires_at = now
            trial_bucket.is_processed = True
            trial_bucket.save(
                update_fields=["expires_at", "is_processed", "updated_at"]
            )

        # Deactivate the subscription
        user_subscription.is_active = False
        user_subscription.is_trial = False
        user_subscription.save(update_fields=["is_active", "is_trial", "updated_at"])

        from users.tasks import sync_user_to_mailerlite

        safe_delay(sync_user_to_mailerlite, str(user.id))

        logger.info(
            "Free trial expired for user %s (subscription %s). "
            "Unused credits forfeited.",
            user.email,
            user_subscription.id,
        )

    @staticmethod
    def refund_credits(task_id, reason=None):
        """
        Restores credits consumed under `task_id` to their originating
        buckets.

        Idempotent: only logs with is_refunded=False are considered, and
        both those logs and the buckets they point at are locked FOR UPDATE,
        so a concurrent or Celery-redelivered caller cannot double-refund —
        the second caller blocks on the first's row locks, then re-reads and
        finds nothing left to do.
        """
        if not task_id:
            return 0

        refund_note = reason or "failed task"
        analytics_reversals = defaultdict(int)  # (user_id, feature) -> amount
        users_by_id = {}
        total_refunded = 0
        refunded_log_ids = []

        with transaction.atomic():
            # Lock the usage logs first: this both claims them against a
            # concurrent refunder and gives us the wallet/bucket set to lock.
            # of=("self",) restricts the lock to the log rows themselves —
            # without it, select_for_update + select_related locks every
            # joined table (buckets, wallets, and even users) in join order,
            # which both over-locks and defeats the deliberate wallet-then-
            # bucket lock ordering established just below.
            logs = list(
                CreditUsageLog.objects.select_for_update(of=("self",))
                .filter(task_id=task_id, is_refunded=False)
                .select_related("bucket", "wallet__user")
                .order_by("created_at")
            )
            if not logs:
                return 0

            # Lock wallet rows before bucket rows, in the same order
            # CreditWallet.consume_credits does (select_for_update on the
            # wallet, then on its buckets). Locking in the opposite order
            # here would deadlock against a concurrent consume.
            wallet_ids = sorted({log.wallet_id for log in logs})
            list(
                CreditWallet.objects.select_for_update()
                .filter(pk__in=wallet_ids)
                .order_by("pk")
            )

            bucket_ids = sorted({log.bucket_id for log in logs})
            buckets = (
                CreditBucket.objects.select_for_update()
                .filter(pk__in=bucket_ids)
                .in_bulk()
            )

            ledger_rows = []

            for log in logs:
                bucket = buckets.get(log.bucket_id)
                if bucket is None:
                    # Bucket vanished (CASCADE would normally have taken the
                    # log with it, so this is defensive). Mark refunded
                    # anyway so we don't keep retrying a dead reference.
                    refunded_log_ids.append(log.id)
                    continue

                # used_credits is a PositiveIntegerField with a Postgres
                # CHECK >= 0. Clamp to a concrete value rather than an
                # F()-decrement so a partially-refunded or externally-reset
                # bucket can never push it negative and raise IntegrityError.
                amount = max(0, min(log.amount, bucket.used_credits))

                if amount:
                    bucket.used_credits -= amount
                    bucket.save(update_fields=["used_credits", "updated_at"])

                    user = log.wallet.user
                    users_by_id[user.id] = user
                    analytics_reversals[(user.id, log.feature)] += amount
                    total_refunded += amount

                    # Only a refund that actually moved credits earns a
                    # ledger entry — a zero-amount clamp (bucket already
                    # externally drained/reset) is just audit noise; the
                    # is_refunded flip below still records that the log
                    # was settled.
                    ledger_rows.append(
                        CreditLedger(
                            user=log.wallet.user,
                            bucket=bucket,
                            ledger_type=CreditLedgerType.REFUND,
                            amount=amount,
                            reference=f"Refund for {refund_note} ({task_id})",
                            metadata={
                                "original_task_id": task_id,
                                "feature": log.feature,
                                "task_type": log.task_type,
                                "original_usage_log_id": str(log.id),
                                "logged_amount": log.amount,
                                "refunded_amount": amount,
                                "reason": refund_note,
                            },
                        )
                    )
                refunded_log_ids.append(log.id)

            CreditLedger.objects.bulk_create(ledger_rows)
            CreditUsageLog.objects.filter(id__in=refunded_log_ids).update(
                is_refunded=True
            )

            for (user_id, feature), amount in analytics_reversals.items():
                AnalyticsService.record_refund(
                    user=users_by_id[user_id], amount=amount, feature=feature
                )

            # Reverse the per-cycle license consumption rollup that
            # consume_credits recorded (CreditWallet._record_license_
            # consumption). Clamped at zero: the counter may already have
            # been reset by a cycle renewal between consume and refund.
            license_reversals = defaultdict(int)  # user_id -> amount
            for (user_id, _feature), amount in analytics_reversals.items():
                license_reversals[user_id] += amount
            for user_id, amount in license_reversals.items():
                allocation = (
                    SchoolCreditAllocation.objects.filter(
                        user_id=user_id,
                        is_active=True,
                        is_admin_allocation=False,
                        license_subscription__is_active=True,
                    )
                    .only("id", "license_subscription_id")
                    .first()
                )
                if allocation:
                    LicenseSubscription.objects.filter(
                        pk=allocation.license_subscription_id
                    ).update(
                        total_credits_consumed=Greatest(
                            F("total_credits_consumed") - amount, Value(0)
                        ),
                        updated_at=timezone.now(),
                    )

        logger.info(
            "Refunded %s credits across %s usage log(s) for task %s (%s)",
            total_refunded,
            len(refunded_log_ids),
            task_id,
            refund_note,
        )
        return total_refunded

    @staticmethod
    @transaction.atomic
    def grant_overage_bucket(wallet, plan, quantity=1, stripe_payment_intent_id=None):
        """Shared by the legacy auto-purchase path and the new Stripe-confirmed
        purchase paths (StripeOverageService + the payment_intent.uscceeded webhook fallback)
        so the bucket/ledger logic only lives in one place.

        Overage blocks never expire (expires_at=None) — a purchased block
        is a standing balance the customer paid for, and consumption
        order already draws it down last, after every free/rollover
        bucket. Tying it to the billing cycle it happened to be bought in
        would forfeit paid-for value the customer never got to use.
        """

        new_bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.OVERAGE,
            total_credits=plan.overage_block_size * quantity,
            used_credits=0,
            expires_at=None,
        )

        CreditWallet.objects.filter(pk=wallet.pk).update(
            overage_blocks_used=F("overage_blocks_used") + quantity
        )

        # Refresh the instance to get the updated value for logging
        wallet.refresh_from_db()

        CreditLedger.objects.create(
            user=wallet.user,
            bucket=new_bucket,
            ledger_type=CreditLedgerType.PURCHASE,
            amount=plan.overage_block_size * quantity,
            reference=f"Overage Block #{wallet.overage_blocks_used} purchased",
            metadata={
                "price_charged": str(plan.overage_block_price),
                "quantity": str(quantity),
                "stripe_payment_intent_id": stripe_payment_intent_id,
            },
        )

        return new_bucket

    @staticmethod
    @transaction.atomic
    def finalize_trial_conversion_via_stripe(
        trial_sub, *, period_start=None, period_end=None
    ):
        """
        Called from invoice.payment_succeeded when a Stripe trial's first real
        charge succeeds (billing_reason=subscription_cycle, previous status was
        trialing). Unlike finalize_trial_to_paid_conversion (user manually
        upgrades mid-trial, possibly to a different plan), the Stripe
        subscription here is unchanged —
        only its status moved trialing -> active — so we update the SAME
        UserSubscription row in place rather than deactivating + creating a new one.

        `period_start`/`period_end` carry Stripe's authoritative period for
        the first paid cycle; see _resolve_billing_period.
        """

        if not trial_sub.is_trial:
            logger.warning(
                "finalize_trial_conversion_via_stripe called on non-trial subscription %s. Ignoring.",
                trial_sub.id,
            )

            return trial_sub

        user = trial_sub.user
        plan = trial_sub.plan

        now = timezone.now()
        cycle_start, billing_end, grant_at = (
            SubscriptionService._resolve_billing_period(
                plan,
                period_start,
                period_end,
                context=f"trial conversion (subscription={trial_sub.id})",
            )
        )
        wallet = user.credit_wallet

        trial_bucket = (
            wallet.buckets.select_for_update()
            .filter(bucket_type=CreditBucketType.TRIAL, expires_at__gt=now)
            .first()
        )

        if trial_bucket:
            unused = trial_bucket.remaining_credits

            if unused > 0:
                CreditLedger.objects.create(
                    user=user,
                    bucket=trial_bucket,
                    ledger_type=CreditLedgerType.EXPIRE,
                    amount=unused,
                    reference="Trial credits forfeited - Trial converted to paid via Stripe.",
                    metadata={
                        "expired_amount": unused,
                        "subscription_id": str(trial_sub.id),
                    },
                )

            trial_bucket.expires_at = now
            trial_bucket.is_processed = True
            trial_bucket.save(
                update_fields=["expires_at", "is_processed", "updated_at"]
            )

        trial_sub.is_trial = False
        trial_sub.trial_end = None

        trial_sub.billing_cycle_start = cycle_start
        trial_sub.billing_cycle_end = billing_end
        # MUST be persisted. process_annual_plan_credit_grants filters
        # next_credit_grant_at__lte=now, and SQL excludes NULL — so leaving
        # this unset means an annual subscriber who converted from a trial
        # is never picked up again and receives nothing for the remaining
        # eleven months of a year they have paid for.
        trial_sub.next_credit_grant_at = grant_at
        trial_sub.save(
            update_fields=[
                "is_trial",
                "trial_end",
                "billing_cycle_start",
                "billing_cycle_end",
                "next_credit_grant_at",
                "updated_at",
            ]
        )

        wallet.overage_blocks_used = 0
        wallet.save(update_fields=["overage_blocks_used", "updated_at"])

        bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=plan.monthly_credits,
            used_credits=0,
            # grant_at, NOT billing_end: on an ANNUAL plan billing_end is a
            # year away, which would stretch a single month's allocation
            # across twelve. _resolve_billing_period returns
            # grant_at == period_end for MONTHLY plans, so monthly behaviour
            # is bit-for-bit unchanged — that is what makes this safe.
            expires_at=grant_at,
        )

        CreditLedger.objects.create(
            user=user,
            bucket=bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=plan.monthly_credits,
            reference=f"Trial converted to paid via Stripe for {plan.display_name or plan.name}",
            metadata={
                "subscription_id": str(trial_sub.id),
                "grant_type": "TRIAL_TO_PAID_STRIPE",
            },
        )

        logger.info(
            "Trial converted to paid via Stripe for user %s (subscription %s).",
            user.email,
            trial_sub.id,
        )

        queue_sync(user)

        return trial_sub

    @staticmethod
    @transaction.atomic
    def finalize_trial_to_paid_conversion(trial_sub, new_plan, stripe_subscription_id):
        """
        Called from webhook (checkout.session.completed with flow='trial_to_paid')
        to finalize a mid-cycle trial→paid upgrade after Stripe payment succeeds.

        This method:
        1. Expires the existing TRIAL credit bucket
        2. Updates the trial_sub.plan to the new_plan (KEY BUG FIX)
        3. Flags trial_sub as no longer trial (is_trial=False)
        4. Grants new MONTHLY bucket with new_plan's credits
        5. Attaches Stripe subscription ID to the same trial_sub row

        Unlike finalize_trial_conversion_via_stripe() (which is for auto-charge
        after 14 days), this is for ACTIVE user upgrade mid-trial with explicit
        plan change.

        Args:
            trial_sub: The UserSubscription with is_trial=True, is_active=True
            new_plan: The SubscriptionPlan user is converting to
            stripe_subscription_id: The Stripe subscription ID from checkout

        Returns:
            The updated trial_sub (same row, modified in place)

        Raises:
            ValueError: If trial_sub is not active, or if bucket creation fails
        """

        # GUARD: Ensure this is actually a trial subscription
        if not trial_sub.is_trial or not trial_sub.is_active:
            raise ValueError(
                f"Subscription {trial_sub.id} is not an active trial. "
                "Cannot finalize trial-to-paid conversion."
            )

        user = trial_sub.user
        now = timezone.now()

        # Calculate the new billing cycle end
        # This handles both MONTHLY and ANNUAL plans
        billing_cycle_end = now + SubscriptionService._billing_period_delta(new_plan)

        # Monthly bucket always expires at 1 month from now (even for annual plans)
        # For ANNUAL plans, next_credit_grant_at = now + 1 month
        # Credits refresh monthly but Stripe charges yearly

        monthly_bucket_expiry = (
            now + relativedelta(months=1)
            if new_plan.interval == BillingInterval.ANNUAL
            else billing_cycle_end
        )

        wallet = user.credit_wallet

        # --- STEP 1: Expire the existing TRIAL bucket ---
        trial_bucket = (
            wallet.buckets.select_for_update()
            .filter(bucket_type=CreditBucketType.TRIAL, expires_at__gt=now)
            .first()
        )

        if trial_bucket:
            unused = trial_bucket.remaining_credits

            # Log unused trial credits as forfeited (they don't carry over)
            if unused > 0:
                CreditLedger.objects.create(
                    user=user,
                    bucket=trial_bucket,
                    ledger_type=CreditLedgerType.EXPIRE,
                    amount=unused,
                    reference=(
                        f"Trial credits forfeited on mid-cycle upgrade to "
                        f"{new_plan.display_name or new_plan.name}"
                    ),
                    metadata={
                        "expired_amount": unused,
                        "trial_subscription_id": str(trial_sub.id),
                        "new_plan_id": str(new_plan.id),
                        "conversion_type": "MID_CYCLE_PAID_UPGRADE",
                    },
                )

            # Mark the trial bucket as expired (no longer available for use)
            trial_bucket.expires_at = now
            trial_bucket.is_processed = True
            trial_bucket.save(
                update_fields=["expires_at", "is_processed", "updated_at"]
            )

            logger.info(
                "Expired TRIAL bucket %s for user %s (forfeited %d credits)",
                trial_bucket.id,
                user.email,
                unused,
            )

        # --- STEP 2: Update the trial subscription to mark it as no longer trial ---

        trial_sub.is_trial = False
        trial_sub.trial_end = None  # Clear the trial expiry date
        trial_sub.plan = new_plan
        trial_sub.billing_cycle_start = now
        trial_sub.billing_cycle_end = billing_cycle_end
        trial_sub.next_credit_grant_at = monthly_bucket_expiry
        trial_sub.stripe_subscription_id = stripe_subscription_id  # Attach Stripe ID
        trial_sub.stripe_status = StripeSubscriptionStatus.ACTIVE

        trial_sub.save(
            update_fields=[
                "is_trial",
                "trial_end",
                "plan",
                "billing_cycle_start",
                "billing_cycle_end",
                # Assigned just above. Omitting it here silently discarded
                # the assignment — the in-memory object looked correct, so
                # only a reload from the DB revealed it.
                "next_credit_grant_at",
                "stripe_subscription_id",
                "stripe_status",
                "updated_at",
            ]
        )

        # --- STEP 3: Reset overage counter for the new cycle ---
        wallet.overage_blocks_used = 0
        wallet.save(update_fields=["overage_blocks_used", "updated_at"])

        # --- STEP 4: Create MONTHLY bucket with new plan's credits ---
        monthly_bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=new_plan.monthly_credits,
            used_credits=0,
            expires_at=monthly_bucket_expiry,
        )

        # --- STEP 5: Audit ledger entry ---
        CreditLedger.objects.create(
            user=user,
            bucket=monthly_bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=new_plan.monthly_credits,
            reference=(
                f"Mid-cycle trial-to-paid conversion to "
                f"{new_plan.display_name or new_plan.name}. "
                f"Stripe subscription {stripe_subscription_id}"
            ),
            metadata={
                "subscription_id": str(trial_sub.id),
                "plan_id": str(new_plan.id),
                "plan_interval": new_plan.interval,  # ← TRACK INTERVAL
                "grant_type": "TRIAL_TO_PAID_MID_CYCLE",
                "stripe_subscription_id": stripe_subscription_id,
                "trial_forfeited": True,  # Signal that trial credits were not carried over
                "billing_cycle_end": billing_cycle_end.isoformat(),
                "next_credit_grant_at": monthly_bucket_expiry.isoformat(),
            },
        )

        logger.info(
            "Finalized trial-to-paid conversion for user %s. "
            "Trial subscription %s upgraded to plan %s (interval: %s). "
            "Granted %d credits. Billing cycle: %s → %s. "
            "Next credit grant: %s. Stripe subscription: %s",
            user.email,
            trial_sub.id,
            new_plan.name,
            new_plan.interval,  # ← LOG INTERVAL
            new_plan.monthly_credits,
            now.isoformat(),
            billing_cycle_end.isoformat(),
            monthly_bucket_expiry.isoformat(),
            stripe_subscription_id,
        )

        queue_sync(user)

        return trial_sub

    @staticmethod
    @transaction.atomic
    def activate_automatic_free_trial(user):
        """
        Automatically activate a free trial for a newly registered user.

        Called from users/signals.py on CustomUser creation. This is the new,
        simplified trial activation flow — no Stripe, no card collection,
        no user action needed.

        What this does:
        1. Checks if user has EVER had a trial (one-time per account).
        2. Creates a UserSubscription with is_trial=True.
        3. Grants 5,000 display credits (5,000,000 raw) in a TRIAL bucket.
        4. Sets trial_end to now + 14 days.
        5. Logs the grant in the immutable ledger.

        Trial expires when EITHER:
        - 14 days pass (trial_end is reached)
        - User exhausts all 5,000 credits (whichever comes first)
        - Celery task expire_active_trials() marks it inactive

        User's access is cut when:
        - is_active=False (trial expired) OR
        - total_remaining_credits=0 (credits exhausted)

        Args:
            user (CustomUser): The newly created user.

        Returns:
            UserSubscription: The newly created trial subscription.

        Raises:
            ValueError: If user has already used a trial.
            Exception: If wallet or bucket creation fails (will be caught and logged).

        Edge cases handled:
        ✓ Concurrent registration (DB-level atomicity)
        ✓ User already has trial (guards with exists check)
        ✓ Wallet already exists (get_or_create handles this)
        ✓ CreditBucket creation fails (transaction rolls back)
        ✓ Non-teacher users (caller must filter these out)
        ✓ License-invited users (caller must skip these)
        """

        now = timezone.now()
        trial_end = now + relativedelta(days=SubscriptionService.TRIAL_DURATION_DAYS)

        # GUARD 1: Check if user has EVER had a trial (even expired ones)

        # This is the ONE critical guard for automatic trial
        # Use select_for_update to lock the user row during this check
        # preventing concurrent registration from creating two trials.

        existing_trial = (
            UserSubscription.objects.select_for_update()
            .filter(user=user, is_trial=True)
            .exists()
        )

        if existing_trial:
            raise ValueError(
                f"User {user.email} has already used the free trial. "
                "Free trial can only be activated once per account."
            )

        # CREATE TRIAL SUBSCRIPTION

        # Use the built-in STANDARD plan as the trial base
        try:
            # REtrieve the Free trial plan
            trial_plan = SubscriptionPlan.objects.get(
                tier=PlanTier.TRIAL, category=PlanCategory.INDIVIDUAL
            )
        except SubscriptionPlan.DoesNotExist as exc:
            raise ValueError(
                "Free trial plan not found. Please create one in the admin panel."
            ) from exc
        except Exception as e:
            logger.error("Error retrieving free trial plan: %s", e)
            raise

        subscription = UserSubscription.objects.create(
            user=user,
            plan=trial_plan,
            is_active=True,
            is_trial=True,
            trial_end=trial_end,
            billing_cycle_start=now,
            billing_cycle_end=trial_end,
            auto_renew=False,
        )

        # ENSURE WALLET EXISTS & RESET OVERAGE

        # Should exist from users.signals, but be defensive
        wallet, _ = CreditWallet.objects.get_or_create(user=user)

        # Reset overage counter for this trial period
        wallet.overage_blocks_used = 0
        wallet.save(update_fields=["overage_blocks_used", "updated_at"])

        # CREATE TRIAL CREDIT BUCKET

        # Expires at trial_end (14 days from now)
        trial_bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.TRIAL,
            total_credits=SubscriptionService.TRIAL_CREDITS_RAW,
            used_credits=0,
            expires_at=trial_end,
        )

        # AUDIT LEDGER ENTRY

        # Immutable record for compliance and debugging
        CreditLedger.objects.create(
            user=user,
            bucket=trial_bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=SubscriptionService.TRIAL_CREDITS_RAW,
            reference="Automatic free trial activation on user registration",
            metadata={
                "grant_type": "AUTOMATIC_TRIAL_REGISTRATION",
                "display_amount": SubscriptionService.TRIAL_CREDITS_DISPLAY,
                "raw_amount": SubscriptionService.TRIAL_CREDITS_RAW,
                "trial_duration_days": SubscriptionService.TRIAL_DURATION_DAYS,
                "trial_end": trial_end.isoformat(),
                "subscription_id": str(subscription.id),
                "activation_method": "AUTOMATIC_REGISTRATION",
            },
        )

        logger.info(
            "Automatic free trial activated for user %s on registration. "
            "Subscription ID: %s. Credits: %d display (%d raw). "
            "Trial ends: %s.",
            user.email,
            subscription.id,
            SubscriptionService.TRIAL_CREDITS_DISPLAY,
            SubscriptionService.TRIAL_CREDITS_RAW,
            trial_end.isoformat(),
        )

        queue_sync(user)

        return subscription


class ManualCreditService:
    """
    Handles superadmin-initiated manual credit grants.

    All grants create a dedicated MANUAL_GRANT CreditBucket so they are
    clearly distinguishable from subscription-driven credits in the ledger,
    wallet summary, and analytics. This keeps beta cohort data clean and
    gives support teams an unambiguous audit trail.
    """

    # Postgres PositiveIntegerField ceiling — total_credits can never exceed this.
    MAX_STORABLE_RAW_CREDITS = 2_147_483_647

    @staticmethod
    def _resolve_plan_for_block_size(target_user):
        """
        Resolves the SubscriptionPlan governing this user's overage/block
        terms, regardless of track (individual, license teacher, or
        license admin). Mirrors ManualWalletSummarySerializer._get_active_plan
        in billing/serializers.py so "a block" means the same thing here
        as it does in the paid overage-purchase flow.
        """
        context = resolve_user_billing_context(target_user)
        if context.source == SOURCE_INDIVIDUAL:
            return context.user_subscription.plan if context.user_subscription else None
        if context.source in (SOURCE_LICENSE_TEACHER, SOURCE_LICENSE_ADMIN):
            return (
                context.license_subscription.plan
                if context.license_subscription
                else None
            )
        return None

    @staticmethod
    def _notify_user_of_grant(target_user, blocks, display_amount, reason, expires_at):
        """Best-effort — must never raise out of top_up_credits."""

        def _dispatch():
            try:
                html_message = render_to_string(
                    "email/manual_credit_grant.html",
                    {
                        "user": target_user,
                        "blocks": blocks,
                        "display_amount": display_amount,
                        "reason": reason,
                        "expires_at": expires_at,
                        "current_year": timezone.now().year,
                        "support_email": settings.SUPPORT_EMAIL,
                    },
                )
                send_email_task.delay(
                    subject="You've received bonus AI credits — GradeA+",
                    message=(
                        f"You've been credited {display_amount} AI credits "
                        "on your GradeA+ account."
                    ),
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[target_user.email],
                    html_message=html_message,
                )
            except Exception:
                logger.exception(
                    "Failed to queue manual-credit-grant email to %s",
                    target_user.email,
                )

        transaction.on_commit(_dispatch)

    @staticmethod
    @transaction.atomic
    def top_up_credits(
        target_user, blocks, reason="", expires_at=None, granted_by=None
    ):
        """
        Injects a manual credit grant into a user's wallet, priced in blocks.

        Args:
            target_user (CustomUser): The user receiving the credits.
            blocks (int): Number of credit blocks to grant. Must be >= 1.
                Raw credits = blocks × target_user's resolved plan.overage_block_size.
            reason (str): Optional human-readable explanation shown in the audit ledger.
            expires_at (datetime | None): Optional expiry. None = credits never expire.
            granted_by (CustomUser | None): The admin authorising the grant, recorded
                in the ledger metadata for accountability.

        Returns:
            CreditBucket: The newly created MANUAL_GRANT bucket.

        Raises:
            ValueError: If blocks is less than 1, the target user has no
                resolvable plan to price a block against, or the resulting
                raw credit amount exceeds what the database column can store.
        """

        if blocks < 1:
            raise ValueError("Block count must be at least 1.")

        plan = ManualCreditService._resolve_plan_for_block_size(target_user)
        if not plan or not plan.overage_block_size:
            raise ValueError(
                f"{target_user.email} has no active plan to determine block size."
            )

        raw_amount = blocks * plan.overage_block_size
        if raw_amount > ManualCreditService.MAX_STORABLE_RAW_CREDITS:
            raise ValueError(
                "Grant exceeds the maximum storable credit amount; reduce blocks."
            )

        # Ensure the wallet exists (it should via signals, but be defensive)
        wallet, _ = CreditWallet.objects.get_or_create(user=target_user)

        # Create the MANUAL_GRANT bucket. bucket_type stays MANUAL_GRANT (not
        # OVERAGE) so admin overrides remain distinguishable from purchased
        # overage in the ledger/analytics; wallet.overage_blocks_used is
        # deliberately left untouched since that counter caps *purchased*
        # overage against plan.max_overage_blocks, which doesn't apply here.
        bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MANUAL_GRANT,
            total_credits=raw_amount,
            used_credits=0,
            expires_at=expires_at,
        )

        display_amount = raw_amount // CONVERSION_FACTOR

        # Immutable audit ledger entry
        CreditLedger.objects.create(
            user=target_user,
            bucket=bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=raw_amount,
            reference=reason,
            metadata={
                "grant_type": "MANUAL_ADMIN_GRANT",
                "blocks": blocks,
                "block_size": plan.overage_block_size,
                "display_amount": display_amount,
                "raw_amount": raw_amount,
                "expires_at": expires_at.isoformat() if expires_at else None,
                "granted_by_id": str(granted_by.id) if granted_by else None,
                "granted_by_email": granted_by.email if granted_by else None,
            },
        )

        logger.info(
            "Manual credit grant: %d block(s), %d display credits (%d raw) granted "
            "to %s by %s. Bucket ID: %s. Expires: %s. Reason: %s",
            blocks,
            display_amount,
            raw_amount,
            target_user.email,
            granted_by.email if granted_by else "system",
            bucket.id,
            expires_at or "never",
            reason,
        )

        ManualCreditService._notify_user_of_grant(
            target_user, blocks, display_amount, reason, expires_at
        )

        return bucket

    @staticmethod
    def get_grant_history(target_user):
        """
        Returns all MANUAL_GRANT buckets for a user, most recent first.
        Includes both active and expired grants for full audit visibility.
        """

        return (
            CreditBucket.objects.filter(
                wallet__user=target_user,
                bucket_type=CreditBucketType.MANUAL_GRANT,
            )
            .select_related("wallet__user")
            .prefetch_related("credit_ledgers")
            .order_by("-created_at")
        )

    @staticmethod
    def get_all_grants_summary(search=None):
        """
        Returns a queryset of all MANUAL_GRANT buckets across all users.
        Used by the superadmin dashboard to audit the full grant history.

        `search`, if given, filters by recipient name/email, the grant's
        reason, or the granting admin's email.
        """
        grants = (
            CreditBucket.objects.filter(bucket_type=CreditBucketType.MANUAL_GRANT)
            .select_related("wallet__user")
            .order_by("-created_at")
        )

        if search:
            grants = grants.filter(
                Q(wallet__user__email__icontains=search)
                | Q(wallet__user__first_name__icontains=search)
                | Q(wallet__user__last_name__icontains=search)
                | Q(credit_ledgers__reference__icontains=search)
                | Q(credit_ledgers__metadata__granted_by_email__icontains=search)
            ).distinct()

        return grants


# Shared by AnalyticsService.record_consumption and .record_refund so the
# two paths can never drift apart — a refund must decrement the exact
# per-feature counter its matching consumption incremented.
FEATURE_TO_ANALYTICS_FIELD = {
    "Grading Assignment": "credits_used_grading",
    "Assignment Extraction": "credits_used_creation",
    "Assignment Generation": "credits_used_creation",
    "Formatted Grade": "credits_used_feedback",
    "Student Summary": "credits_used_feedback",
}


class AnalyticsService:

    @staticmethod
    def track_activity(user):
        if not user.is_beta_eligible():
            return

        profile, created = BetaProfile.objects.get_or_create(user=user)
        now = timezone.now()
        today = now.date()

        update_fields = ["last_active_at"]
        profile.last_active_at = now

        # Only increment distinct days if it's a new calendar day
        if profile.last_login_date != today:
            profile.distinct_login_days = F("distinct_login_days") + 1
            profile.last_login_date = today
            update_fields.extend(["distinct_login_days", "last_login_date"])

        profile.save(update_fields=update_fields)

    @staticmethod
    @transaction.atomic
    def record_consumption(user, amount, feature):
        """
        Update usage metrics in real-time
        Called inside the 'consume_credits' flow

        """
        if not user.is_beta_eligible():
            return

        profile, created = BetaProfile.objects.get_or_create(user=user)
        now = timezone.now()

        # 1. Track First Action
        if not profile.first_ai_action_at:
            profile.first_ai_action_at = now

            # Calculate days since joining
            delta = now - profile.joined_beta_at
            profile.days_to_first_action = max(0, delta.days)

        # 2. Update Raw Total using F() expressions to prevent race conditions
        profile.total_credits_used = F("total_credits_used") + amount

        analytics_field = FEATURE_TO_ANALYTICS_FIELD.get(feature)
        if analytics_field:
            setattr(profile, analytics_field, F(analytics_field) + amount)

        profile.save()

        # 3. Refresh from DB to check thresholds (after F() expressions is applied)
        profile.refresh_from_db()

        # Check Thresholds
        usage_ratio = profile.total_credits_used / profile.initial_beta_credits

        if usage_ratio >= 1.0:
            profile.has_hit_cap = True
        elif usage_ratio >= 0.8:
            profile.has_hit_80_percent = True

        profile.save(
            update_fields=[
                "has_hit_cap",
                "has_hit_80_percent",
                "first_ai_action_at",
                "days_to_first_action",
            ]
        )

    @staticmethod
    @transaction.atomic
    def record_refund(user, amount, feature):
        """
        Reverses record_consumption for credits that were refunded (e.g. an
        AI grading run that failed partway through — see
        SubscriptionService.refund_credits, which calls this once per
        (user, feature) it refunds).

        Deliberately does NOT clear has_hit_cap / has_hit_80_percent: those
        are "ever reached" signals used only by beta reporting (grep confirms
        no read outside billing/serializers.py and billing/views.py) — not
        access gates — so un-setting them would rewrite history for a teacher
        who genuinely did burn through their allocation at some point.
        """
        if amount <= 0 or not user.is_beta_eligible():
            return

        profile = BetaProfile.objects.filter(user=user).first()
        if profile is None:
            return

        # total_credits_used and the per-feature counters are all
        # PositiveIntegerField (Postgres CHECK >= 0) — clamp at 0 rather than
        # a bare F() decrement, since a refund can in principle outrun the
        # counter (e.g. the profile was created after some consumption, or
        # credits were reset out of band).
        update_fields = ["total_credits_used"]
        profile.total_credits_used = Greatest(
            F("total_credits_used") - amount, Value(0)
        )

        analytics_field = FEATURE_TO_ANALYTICS_FIELD.get(feature)
        if analytics_field:
            setattr(
                profile,
                analytics_field,
                Greatest(F(analytics_field) - amount, Value(0)),
            )
            update_fields.append(analytics_field)

        profile.save(update_fields=update_fields)

    @staticmethod
    def calculate_conversion_probability(profile):
        """
        The "Scoring Engine". Calculates probability from 0 - 100
        Called by midnight
        """
        score = 0

        # +30 points for high engagement (8+ distinct days)
        if profile.distinct_login_days >= 8:
            score += 30

        # +30 point for high credit usage (80% or more)
        if profile.has_hit_80_percent:
            score += 30

        # +20 point for "Sticky" users (Active in the last 7 days)
        if profile.last_active_at:
            days_since_active = (timezone.now() - profile.last_active_at).days
            if days_since_active <= 7:
                score += 20

        # +20 points for "Core Use Case" (Grading > Creation)
        if profile.credits_used_grading > profile.credits_used_creation:
            score += 20

        # Calculate Velocity (Credits per day)
        days_since_joined = (timezone.now() - profile.joined_beta_at).days
        profile.usage_velocity = profile.total_credits_used / days_since_joined

        profile.conversion_probability = float(score)
        profile.save(update_fields=["conversion_probability", "usage_velocity"])

    @staticmethod
    def track_analytics_view(user):
        """
        Increment the analytic view count for a user
        Called when a teacher interacts with their performance dashboard
        """

        if not user.is_beta_eligible():
            return

        # We use F() to ensure the increment is atomic and thread-safe
        BetaProfile.objects.filter(user=user).update(
            analytics_view_count=F("analytics_view_count") + 1,
            last_active_at=timezone.now(),
        )
