import logging

from dateutil.relativedelta import relativedelta  # type: ignore
from django.db import transaction
from django.db.models import F
from django.utils import timezone

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
    PlanCategory,
    PlanTier,
    PlanType,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
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
    @transaction.atomic
    def activate_subscription(user, plan):
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
        """

        if plan.name == PlanType.BETA and not user.is_beta_eligible():
            raise ValueError("The Beta plan is restricted to teacher accounts.")

        now = timezone.now()
        billing_end = now + SubscriptionService._billing_period_delta(plan)

        monthly_bucket_expiry = (
            now + relativedelta(months=1)
            if plan.interval == BillingInterval.ANNUAL
            else billing_end
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
            billing_cycle_start=now,
            billing_cycle_end=billing_end,
            next_credit_grant_at=monthly_bucket_expiry,
            auto_renew=True,
        )

        # 3. Handle Wallet and Initial Credit Injection
        now = timezone.now()
        wallet, _ = CreditWallet.objects.get_or_create(user=user)

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
                rollover_amount = min(
                    int(unused * (plan.carry_over_percent / 100)), plan.carry_over_max
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
                        metadata={"previous_bucket_id": str(active_monthly.id)},
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

        return subscription

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

        old_monthly = (
            wallet.buckets.select_for_update()
            .filter(bucket_type=CreditBucketType.MONTHLY)
            .first()
        )

        if old_monthly:
            unused = old_monthly.remaining_credits
            if unused > 0:
                rollover_amount = min(
                    int(unused * (plan.carry_over_percent / 100)), plan.carry_over_max
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
                        },
                    )
            old_monthly.expires_at = now
            old_monthly.save(update_fields=["expires_at", "updated_at"])

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
    def process_rollover_and_renewal(user_subscription):
        """
        Executed by Celery at billing_cycle_end
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
                # Calculate CARRY_OVER based on new plan rutes
                potential_rollover = int(
                    unused_credits * (target_plan.carry_over_percent / 100)
                )
                final_rollover_amount = min(
                    potential_rollover, target_plan.carry_over_max
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
                        },
                    )

            # Retire the Old Bucket
            old_monthly_bucket.expires_at = now
            old_monthly_bucket.is_processed = True
            old_monthly_bucket.save(
                update_fields=["expires_at", "is_processed", "updated_at"]
            )

        # Trigger the new activation
        return SubscriptionService.activate_subscription(user, target_plan)

    @staticmethod
    @transaction.atomic
    def schedule_downgrade(user, new_plan):
        """Schedule a downgrade for the end of the billing cycle"""

        # 1. Find the currently active subscription
        current_sub = (
            UserSubscription.objects.select_for_update()
            .filter(user=user, is_active=True)
            .first()
        )

        if not current_sub:
            raise ValueError("No active subscription to downgrade.")

        # 2. Disable auto-renew and store the target plan
        current_sub.auto_renew = False
        current_sub.pending_plan = new_plan
        current_sub.save(update_fields=["auto_renew", "pending_plan"])

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

        return subscription

    @staticmethod
    @transaction.atomic
    def expire_trial(user_subscription):
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

        Raises:
            ValueError: If the subscription is not a trial, or if the trial has not
                        yet ended.
        """
        if not user_subscription.is_trial:
            raise ValueError(
                f"Subscription {user_subscription.id} is not a trial subscription."
            )

        now = timezone.now()

        if user_subscription.trial_end and user_subscription.trial_end > now:
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
            unused = trial_bucket.remaining_credits
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

        logger.info(
            "Free trial expired for user %s (subscription %s). "
            "Unused credits forfeited.",
            user.email,
            user_subscription.id,
        )

    @staticmethod
    @transaction.atomic
    def convert_trial_to_paid(user, new_plan):
        # FIXME: DELETE THIS TOO, REDUNDANT, it does not charge from stripe

        """
        Converts an active free trial into a full paid subscription.

        This is the "upgrade from trial" action — triggered by the user choosing a
        paid plan before or during their trial. It is also the intended path called
        when a user clicks "Subscribe" from within the trial experience.

        What this does:
        1. Validates there is an active trial to convert.
        2. Expires the TRIAL bucket immediately — trial credits do NOT carry over
        into the paid plan (spec: trial is a separate, bounded experience).
        3. Calls activate_subscription() for the paid plan, which handles:
        - Deactivating the trial subscription
        - Creating the new paid subscription
        - Granting the first monthly credit bucket
        - Resetting overage counter
        4. Logs the conversion event in the ledger for analytics.

        Args:
            user (CustomUser): The user converting from trial.
            new_plan (SubscriptionPlan): The paid plan to activate. Must be INDIVIDUAL.

        Returns:
            UserSubscription: The new paid subscription.

        Raises:
            ValueError: If the user has no active trial, or if new_plan is not
                        INDIVIDUAL category.
        """
        # from .models import PlanCategory  # local import

        if new_plan.category != PlanCategory.INDIVIDUAL:
            raise ValueError(
                f"Cannot convert trial to a {new_plan.category} plan. "
                f"Only INDIVIDUAL plans are supported via this flow."
            )

        # Fetch the active trial subscription under lock
        trial_sub = (
            UserSubscription.objects.select_for_update()
            .filter(user=user, is_active=True, is_trial=True)
            .first()
        )
        if not trial_sub:
            raise ValueError(
                f"User {user.email} does not have an active free trial to convert."
            )

        now = timezone.now()
        wallet = user.credit_wallet

        # Expire the TRIAL bucket immediately — trial credits do not transfer
        trial_bucket = (
            wallet.buckets.select_for_update()
            .filter(
                bucket_type=CreditBucketType.TRIAL,
                expires_at__gt=now,  # still live
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
                        f"Trial credits forfeited on conversion to paid plan "
                        f"{new_plan.display_name or new_plan.name}."
                    ),
                    metadata={
                        "expired_amount": unused,
                        "conversion_plan": new_plan.name,
                        "subscription_id": str(trial_sub.id),
                        "converted_at": now.isoformat(),
                    },
                )
            # Expire the bucket so activate_subscription's cleanup logic
            # does not find a live MONTHLY bucket and attempt rollover.
            # (TRIAL bucket_type is excluded from the MONTHLY cleanup in
            # activate_subscription, so this is belt-and-suspenders.)
            trial_bucket.expires_at = now
            trial_bucket.is_processed = True
            trial_bucket.save(
                update_fields=["expires_at", "is_processed", "updated_at"]
            )

        logger.info(
            "Converting trial subscription %s for user %s to paid plan %s.",
            trial_sub.id,
            user.email,
            new_plan.name,
        )

        # activate_subscription deactivates the trial sub (via the
        # "deactivate existing active subscriptions" step) and creates the
        # new paid subscription + monthly credit bucket.
        new_subscription = SubscriptionService.activate_subscription(user, new_plan)

        logger.info(
            "Trial-to-paid conversion complete for user %s. "
            "New subscription ID: %s. Plan: %s.",
            user.email,
            new_subscription.id,
            new_plan.name,
        )

        return new_subscription

    @staticmethod
    @transaction.atomic
    def refund_credits(task_id):
        """
        Locates all consumption logs for a specific task and restores
        the credits to their original buckets.
        """

        # 1. Fetch all usage logs for this task
        usage_logs = CreditUsageLog.objects.filter(task_id=task_id)

        if not usage_logs.exists():
            return 0

        total_refunded = 0

        for log in usage_logs:
            bucket = log.bucket
            amount_to_restore = log.amount

            # 2. Restore the credits to the original bucket
            # Note: We decrease `used_credits` to increase `remaining_credits`
            bucket.used_credits = F("used_credits") - amount_to_restore
            bucket.save(update_fields=["used_credits", "updated_at"])

            # 3. Creaate the REFUND ledger entry for audit integrity
            CreditLedger.objects.create(
                user=log.wallet.user,
                bucket=bucket,
                ledger_type=CreditLedgerType.REFUND,
                amount=amount_to_restore,
                reference=f"Refund for failed task {task_id}",
                metadata={
                    "original_task_id": task_id,
                    "feature": log.feature,
                    "original_usage_log_id": str(log.id),
                },
            )

            total_refunded += amount_to_restore

        usage_logs.update(is_refunded=True)

        return total_refunded

    @staticmethod
    @transaction.atomic
    def grant_overage_bucket(wallet, plan, expires_at, stripe_payment_intent_id=None):
        """Shared by the legacy auto-purchase path and the new Stripe-confirmed
        purchase paths (StripeOverageService + the payment_intent.uscceeded webhook fallback)
        so the bucket/ledger logic only lives in one place.
        """

        # expires_at = user_sub.next_credit_grant_a or user_sub.billing_cycle_end

        # plan = user_sub.plan

        new_bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.OVERAGE,
            total_credits=plan.overage_block_size,
            used_credits=0,
            expires_at=expires_at,
        )

        CreditWallet.objects.filter(pk=wallet.pk).update(
            overage_blocks_used=F("overage_blocks_used") + 1
        )

        # Refresh the instance to get the updated value for logging
        wallet.refresh_from_db()

        CreditLedger.objects.create(
            user=wallet.user,
            bucket=new_bucket,
            ledger_type=CreditLedgerType.PURCHASE,
            amount=plan.overage_block_size,
            reference=f"Overage Block #{wallet.overage_blocks_used} purchased",
            metadata={
                "price_charged": str(plan.overage_block_price),
                "stripe_payment_intent_id": stripe_payment_intent_id,
            },
        )

        return new_bucket

    @staticmethod
    @transaction.atomic
    def finalize_trial_conversion_via_stripe(trial_sub):
        """
        Called from invoice.payment_succeeded when a Stripe trial's first real
        charge succeeds (billing_reason=subscription_cycle, previous status was
        trialing). Unlike convert_trial_to_paid (user manually upgrades mid-trial,
        possibly to a different plan), the Stripe subscription here is unchanged —
        only its status moved trialing -> active — so we update the SAME
        UserSubscription row in place rather than deactivating + creating a new one.
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
        billing_end = now + relativedelta(months=1)
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

        trial_sub.billing_cycle_start = now
        trial_sub.billing_cycle_end = billing_end
        trial_sub.save(
            update_fields=[
                "is_trial",
                "trial_end",
                "billing_cycle_start",
                "billing_cycle_end",
                "updated_at",
            ]
        )

        wallet.overage_block_used = 0
        wallet.save(update_fields=["overage_blocks_used", "updated_at"])

        bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=plan.monthly_credits,
            used_credits=0,
            expires_at=billing_end,
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

        return subscription


class ManualCreditService:
    """
    Handles superadmin-initiated manual credit grants.

    All grants create a dedicated MANUAL_GRANT CreditBucket so they are
    clearly distinguishable from subscription-driven credits in the ledger,
    wallet summary, and analytics. This keeps beta cohort data clean and
    gives support teams an unambiguous audit trail.
    """

    @staticmethod
    @transaction.atomic
    def top_up_credits(
        target_user, amount_display, reason, expires_at=None, granted_by=None
    ):
        """
        Injects a manual credit grant into a user's wallet.

        Args:
            target_user (CustomUser): The user receiving the credits.
            amount_display (int): Credit amount in display units (multiplied
                by CONVERSION_FACTOR internally). Must be >= 1.
            reason (str): Human-readable explanation shown in the audit ledger.
            expires_at (datetime | None): Optional expiry. None = credits never expire.
            granted_by (CustomUser | None): The admin authorising the grant, recorded
                in the ledger metadata for accountability.

        Returns:
            CreditBucket: The newly created MANUAL_GRANT bucket.

        Raises:
            ValueError: If amount_display is less than 1.
        """

        if amount_display < 1:
            raise ValueError("Credit amount must be at least 1.")

        raw_amount = amount_display * CONVERSION_FACTOR

        # Ensure the wallet exists (it should via signals, but be defensive)
        wallet, _ = CreditWallet.objects.get_or_create(user=target_user)

        # Create the MANUAL_GRANT bucket
        bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MANUAL_GRANT,
            total_credits=raw_amount,
            used_credits=0,
            expires_at=expires_at,
        )

        # Immutable audit ledger entry
        CreditLedger.objects.create(
            user=target_user,
            bucket=bucket,
            ledger_type=CreditLedgerType.GRANT,
            amount=raw_amount,
            reference=reason,
            metadata={
                "grant_type": "MANUAL_ADMIN_GRANT",
                "display_amount": amount_display,
                "raw_amount": raw_amount,
                "expires_at": expires_at.isoformat() if expires_at else None,
                "granted_by_id": str(granted_by.id) if granted_by else None,
                "granted_by_email": granted_by.email if granted_by else None,
            },
        )

        logger.info(
            "Manual credit grant: %d display credits (%d raw) granted to %s by %s. "
            "Bucket ID: %s. Expires: %s. Reason: %s",
            amount_display,
            raw_amount,
            target_user.email,
            granted_by.email if granted_by else "system",
            bucket.id,
            expires_at or "never",
            reason,
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
    def get_all_grants_summary():
        """
        Returns a queryset of all MANUAL_GRANT buckets across all users.
        Used by the superadmin dashboard to audit the full grant history.
        """
        return (
            CreditBucket.objects.filter(bucket_type=CreditBucketType.MANUAL_GRANT)
            .select_related("wallet__user")
            .order_by("-created_at")
        )


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

        grading_categories = ["Grading Assignment"]
        creation_categories = [
            "Assignment Extraction",
            "Assignment Generation",
        ]
        feedback_categories = ["Formatted Grade", "Student Summary"]

        if feature in grading_categories:
            profile.credits_used_grading = F("credits_used_grading") + amount
        elif feature in creation_categories:
            profile.credits_used_creation = F("credits_used_creation") + amount
        elif feature in feedback_categories:
            profile.credits_used_feedback = F("credits_used_feedback") + amount

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
