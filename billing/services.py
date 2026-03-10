from dateutil.relativedelta import relativedelta  # type: ignore
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import (  # CreditUsageLog,; SubscriptionPlan,
    BetaProfile,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditUsageLog,
    CreditWallet,
    UserSubscription,
)


class SubscriptionService:

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
                            "rollover_applied_percent": str(target_plan.car),
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
        current_sub = UserSubscription.objects.select_for_update().filter(
            user=user, is_active=True
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
            total_credits=plan.max_overage_blocks,
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

        usage_logs.is_refunded = True
        usage_logs.save(update_fields=["is_refunded", "updated_at"])

        return total_refunded


class AnalyticsService:

    @staticmethod
    def track_activity(user):
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

        if feature == "Grading":
            profile.total_credits_used_grading = (
                F("total_credits_used_grading") + amount
            )
        elif feature == "Assignment":
            profile.credits_used_creation = F("credits_used_creation") + amount

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
        if profile.credit_used_grading > profile.credit_used_creation:
            score += 20

        # Calculate Velocity (Credits per day)
        days_since_joined = (timezone.now() - profile.joined_beta_at).days
        profile.usage_velocity = profile.total_credits_used / days_since_joined

        profile.conversion_probability = float(score)
        profile.save(update_fields=["conversion_probability", "usage_velocity"])
