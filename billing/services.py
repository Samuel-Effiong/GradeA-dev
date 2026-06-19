import logging

from dateutil.relativedelta import relativedelta  # type: ignore
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import (  # CreditUsageLog,; SubscriptionPlan,
    CONVERSION_FACTOR,
    BetaProfile,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditUsageLog,
    CreditWallet,
    PlanCategory,
    PlanType,
    UserSubscription,
)

logger = logging.getLogger(__name__)


class SubscriptionService:

    TRIAL_CREDITS_DISPLAY = 5_000  # User facing value
    TRIAL_CREDITS_RAW = 5_000 * 1_000
    TRIAL_DURATION_DAYS = 14

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
        """

        if plan.name == PlanType.BETA and not user.is_beta_eligible():
            raise ValueError("The Beta plan is restricted to teacher accounts.")

        now = timezone.now()
        billing_end = now + relativedelta(months=1)

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
            auto_renew=True,
        )

        # 3. Handle Wallet and Initial Credit Injection
        now = timezone.now()
        wallet, _ = CreditWallet.objects.get_or_create(user=user)

        # --- The cleanup pahse (Handling existing credits for upgrades)
        active_monthly = wallet.buckets.filter(
            bucket_type=CreditBucketType.MONTHLY, expires_at__gt=now
        ).first()

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
            expires_at=billing_end,
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
    def process_rollover_and_renewal(user_subscription):
        """
        Executed by Celery at billing_cycle_end
        """

        user = user_subscription.user

        # If there's a pending plan, use it; otherwise, renew the current one
        target_plan = user_subscription.pending_plan or user_subscription.plan
        old_plan = user_subscription.plan

        now = timezone.now()

        wallet = user.credit_wallet
        old_monthly_bucket = (
            wallet.buckets.select_for_update().filter(bucket_type="MONTHLY").first()
        )

        if old_monthly_bucket:
            unused_credits = old_monthly_bucket.remaining_credits

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
            old_monthly_bucket.save(update_fields=["expires_at", "updated_at"])

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
    def purchase_overage_block(wallet):
        # FIXME: Delete later, actual implementation in StripeOverageService
        """
        Logic to charge the user and inject a new Overage Bucket
        """

        user_sub = (
            wallet.user.subscriptions.filter(is_active=True)
            .select_related("plan")
            .first()
        )
        if not user_sub:
            return False

        plan = user_sub.plan

        # 1. Check if user has reached their maximum allowed blocks
        if wallet.overage_blocks_used >= plan.max_overage_blocks:
            return False

        # 2. Trigger Payment (placeholder)
        # In production: result = StripService.charge(wallet.user, plan.overage_block_price)
        payment_success = True

        if not payment_success:
            return False

        # 3. Create the OVERAGE bucket
        # Overage blocks usually expire at the end of the current billing cyce
        new_bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.OVERAGE,
            total_credits=plan.overage_block_size,
            used_credits=0,
            expires_at=user_sub.billing_cycle_end,
        )

        # 4. Increment the counter pon the wallet
        wallet.overage_blocks_used += 1
        wallet.save(update_fields=["overage_blocks_used"])

        # 5. Log the purchase in the Ledger
        CreditLedger.objects.create(
            user=wallet.user,
            bucket=new_bucket,
            ledger_type=CreditLedgerType.PURCHASE,
            amount=plan.overage_block_size,
            reference=f"Auto Overage Block #{wallet.overage_blocks_used} purchased",
            metadata={"price_charged": str(plan.overage_block_price)},
        )

        return True

    @staticmethod
    @transaction.atomic
    def expire_bucket(bucket):
        """
        Formalizes the loss of credits due to expiration
        """
        unused_amount = bucket.remaining_credits

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
        bucket.save(update_fields=["used_credits", "is_processed", "updated_at"])
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
    def grant_overage_bucket(wallet, user_sub, stripe_payment_intent_id=None):
        """Shared by the legacy auto-purchase path and the new Stripe-confirmed
        purchase paths (StripeOverageService + the payment_intent.uscceeded webhook fallback)
        so the bucket/ledger logic only lives in one place.
        """

        plan = user_sub.plan
        new_bucket = CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.OVERAGE,
            total_credits=plan.overage_block_size,
            used_credits=0,
            expires_at=user_sub.billing_cycle_end,
        )

        wallet.overage_block_used += 1
        wallet.save(update_fields=["overage_block_used", "updated_at"])

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
