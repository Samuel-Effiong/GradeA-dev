from dateutil.relativedelta import relativedelta  # type: ignore
from django.db import transaction
from django.utils import timezone

from .models import (  # CreditUsageLog,; SubscriptionPlan,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
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
