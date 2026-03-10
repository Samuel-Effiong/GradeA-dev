import math
import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .errors import InsufficientCreditsError

# from .services import SubscriptionService

# Create your models here.

CONVERSION_FACTOR = 1000


class PlanType(models.TextChoices):
    STANDARD = "STANDARD", _("Standard")
    PRO = "PRO", _("Pro")
    POWER = "POWER", _("Power")
    BETA = "BETA", _("Beta")


class SubscriptionPlan(models.Model):
    """
    ```python
        Represents a subscription tier configuration.

        This model defines the parameters for different subscription levels, including
        monthly credit allocations, rollover policies for unused credits, and
        automated overage billing configurations. It acts as a template for user
        subscriptions, determining how credits are granted, carried over, and
        charged when limits are exceeded.
    ```

    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(
        max_length=20,
        choices=PlanType.choices,
        unique=True,
        help_text="Unique code for the plan",
    )
    display_name = models.CharField(
        max_length=100, null=True, blank=True, help_text="Name of the plan"
    )

    monthly_credits = models.PositiveIntegerField(
        default=0, help_text="Number of credits granted each month"
    )

    carry_over_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        help_text="Percentage of unused credits to carry over to the next month",
    )
    carry_over_max = models.PositiveIntegerField(
        default=0,
        help_text="Maximum number of credits that can be carried over to the next month",
    )
    carry_over_expiry_months = models.PositiveSmallIntegerField(
        default=0, help_text="Number of months credits can be carried over"
    )

    overage_block_size = models.PositiveIntegerField(
        default=0, help_text="Number of credits to add when overage is detected"
    )
    overage_block_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0.00,
        help_text="Price of each overage block",
    )

    max_overage_blocks = models.PositiveSmallIntegerField(
        default=0, help_text="Maximum number of overage blocks to add"
    )

    is_active = models.BooleanField(
        default=True, help_text="Whether the plan is active"
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def display_monthly_credits(self):
        return self.monthly_credits // CONVERSION_FACTOR

    @property
    def display_carry_over_max(self):
        return self.carry_over_max // CONVERSION_FACTOR

    @property
    def display_overage_block_size(self):
        return self.overage_block_size // CONVERSION_FACTOR


class UserSubscription(models.Model):
    """
    Represents the association between a user and a specific SubscriptionPlan.

    This model tracks the lifecycle of a user's subscription, including the current
    billing cycle period, active status, and auto-renewal settings. It serves as the
    primary record for determining a user's current billing tier and entitlement
    to credit allocations.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.CASCADE,
        related_name="subscriptions",
        help_text="User who owns the subscription",
    )
    plan = models.ForeignKey(
        "SubscriptionPlan",
        on_delete=models.PROTECT,
        related_name="user_subscriptions",
        help_text="Plan the user is subscribed to",
    )

    is_active = models.BooleanField(
        default=True, help_text="Whether the subscription is active"
    )

    billing_cycle_start = models.DateTimeField(
        help_text="Start date of the current billing cycle"
    )
    billing_cycle_end = models.DateTimeField(
        help_text="End date of the current billing cycle"
    )

    auto_renew = models.BooleanField(
        default=True, help_text="Whether the subscription auto-renews"
    )
    pending_plan = models.ForeignKey(
        "SubscriptionPlan",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="user_pending_subscriptions",
    )

    created_at = models.DateTimeField(
        auto_now_add=True, help_text="Date and time when the subscription was created"
    )
    updated_at = models.DateTimeField(
        auto_now=True, help_text="Date and time when the subscription was last updated"
    )

    class Meta:
        ordering = ["-created_at"]

        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_active=True),
                name="one_active_subscription_per_user",
            )
        ]


class CreditWallet(models.Model):
    """
    Centralized ledger for managing a user's credit balance and overage consumption.

    The CreditWallet serves as the primary entity for tracking a user's available
    credits across multiple sources (buckets). It facilitates the aggregation of
    monthly allocations, carry-over balances, and automated overage blocks. By
    maintaining a record of `overage_blocks

    Summarily:
    - It is the container for all credit buckets associated with a user.
    - It provides a unified view of the user's credit balance and tracks overage usage.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.OneToOneField(
        "users.CustomUser",
        on_delete=models.CASCADE,
        related_name="credit_wallet",
        help_text="User who owns the credit wallet",
    )

    # Track overage usage
    overage_blocks_used = models.PositiveSmallIntegerField(
        default=0,
        help_text="Number of overage blocks used in the current billing cycle",
    )

    created_at = models.DateTimeField(
        auto_now_add=True, help_text="Date and time when the credit wallet was created"
    )
    updated_at = models.DateTimeField(
        auto_now=True, help_text="Date and time when the credit wallet was last updated"
    )

    class Meta:
        ordering = ["-created_at"]

    def total_remaining_credits(self):
        """
        Calculates the total number of available credits across all active buckets.

        This method aggregates the `remaining_credits` from all `CreditBucket` instances
        associated with the user that have not yet expired and still have a positive
        balance. It considers all bucket types (Monthly, Carry Over, and Overage)
        to provide a unified view of the user

        Returns sum of all valid bucket credits (monthly + rollover + overage)
        """
        now = timezone.now()
        # valid_buckets = self.buckets.filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
        # total = sum(bucket.remaining_credits for bucket in valid_buckets if bucket.remaining_credits > 0)

        result = self.buckets.filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        ).aggregate(
            total=models.Sum(models.F("total_credits") - models.F("used_credits"))
        )

        return result["total"] or 0

    @transaction.atomic
    def consume_credits(self, amount, feature=None, task_type=None, task_id=None):
        """
        Consumes credits from the user's wallet.

        This method attempts to deduct the specified amount of credits from the user's
        available balance. It prioritizes consumption from the oldest valid buckets
        (Carry Over -> Monthly -> Overage) to ensure fair usage and minimize waste.

        Args:
            amount (int): The number of credits to consume.
            feature (str, optional): The feature or service for which credits are being consumed.
            task_type (str, optional): The type of task being performed.
            task_id (str, optional): The ID of the task.

        Returns:
            bool: True if credits were successfully consumed, False otherwise.
        """
        remaining = amount
        total_available = self.total_remaining_credits()

        while total_available < amount:

            # from .services import SubscriptionService

            # success = SubscriptionService.purchase_overage_block(wallet=self)

            success = True

            if not success:
                available_credits = self.total_remaining_credits()
                raise InsufficientCreditsError(
                    f"Insufficient credits and overage limit reached. "
                    f"Requested: {amount}, Available: {available_credits}"
                )

        # Define FIFO consumption order
        fifo_order = [
            CreditBucketType.CARRY_OVER,
            CreditBucketType.MONTHLY,
            CreditBucketType.OVERAGE,
        ]

        buckets = (
            self.buckets.select_for_update()
            .filter(bucket_type__in=fifo_order)
            .filter(
                models.Q(expires_at__isnull=True)
                | models.Q(expires_at__gt=timezone.now())
            )
            .order_by(
                models.Case(
                    *[
                        models.When(bucket_type=bucket_type, then=models.Value(i))
                        for i, bucket_type in enumerate(fifo_order)
                    ]
                ),
                "expires_at",
                "created_at",
            )
        )

        # total_deducted = 0
        usage_log = []
        ledger_log = []

        for bucket in buckets:
            if remaining <= 0:
                break

            deducted = bucket.consume_credits(remaining)
            remaining -= deducted

            usage_log.append(
                CreditUsageLog(
                    wallet=self,
                    bucket=bucket,
                    amount=deducted,
                    feature=feature,
                    task_type=task_type,
                    task_id=task_id,
                )
            )

            ledger_log.append(
                CreditLedger(
                    user=self.user,
                    bucket=bucket,
                    ledger_type=CreditLedgerType.CONSUME,
                    amount=-deducted,
                    reference=f"Consumption of {deducted} credits for {feature} ({task_type}: {task_id})",
                    metadata={
                        "feature": feature,
                        "task_type": task_type,
                        "task_id": task_id,
                    },
                )
            )

        CreditUsageLog.objects.bulk_create(usage_log)
        CreditLedger.objects.bulk_create(ledger_log)

        return amount  # Always returns the requested amount (all successfully deducted)

    @property
    def display_balance(self):
        """
        The "User-Friendly' balance shown on the frontend.
        We use floor to be safe so we never over-promise
        """
        return math.floor(self.total_remaining_credits() / CONVERSION_FACTOR)

    @property
    def display_overage_balance(self):
        """
        The "User-Friendly' overage balance shown on the frontend.
        We use ceil to be safe so we never under-promise
        """
        return math.ceil(self.overage_blocks_used / CONVERSION_FACTOR)


class CreditBucketType(models.TextChoices):
    MONTHLY = "MONTHLY", _("Monthly")
    CARRY_OVER = "CARRY_OVER", _("Carry Over")
    OVERAGE = "OVERAGE", _("Overage")


class CreditBucket(models.Model):
    """
    Represents a specific pool of credits granted to a user.

    Credits are categorized into buckets based on their source (Monthly, Carry Over, or Overage)
    to facilitate granular tracking of usage, expiration policies, and consumption priority.
    Each bucket tracks the initial allocation versus the remaining balance.

    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    wallet = models.ForeignKey(
        CreditWallet,
        on_delete=models.CASCADE,
        related_name="buckets",
        help_text="Wallet that owns the credit bucket",
    )
    bucket_type = models.CharField(
        max_length=20,
        choices=CreditBucketType.choices,
        help_text="Type of credit bucket",
    )

    total_credits = models.PositiveIntegerField(
        default=0, help_text="Total credits in the bucket"
    )
    used_credits = models.PositiveIntegerField(
        default=0, help_text="Credits consumed from this bucket"
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Date and time when the credits expire. Only for carry_over / overage",
    )

    created_at = models.DateTimeField(
        auto_now_add=True, help_text="Date and time when the credit bucket was created"
    )
    updated_at = models.DateTimeField(
        auto_now=True, help_text="Date and time when the credit bucket was last updated"
    )

    is_processed = models.BooleanField(
        default=False,
        help_text="True if the bucket has been handled by the expiry cleanup task",
    )

    class Meta:
        indexes = [
            models.Index(fields=["wallet", "bucket_type", "expires_at"]),
        ]

        ordering = ["expires_at", "created_at"]

    def is_expired(self):
        if self.expires_at and self.expires_at <= timezone.now():
            return True
        return False

    @property
    def remaining_credits(self):
        """
        Calculates the current balance of credits available for use.

        This property evaluates the bucket's status by checking if the credits have expired.
        If the bucket is expired, it returns 0. Otherwise, it returns the difference
        between total_credits and used_credits, ensuring the result is never negative.

        """
        if self.expires_at and timezone.now() > self.expires_at:
            return 0
        return max(0, self.total_credits - self.used_credits)

    def consume_credits(self, amount):
        """
        Deducts a specified amount of credits from the bucket's balance.

        This method calculates the actual number of credits that can be consumed based
        on the current `remaining_credits`. It ensures that the deduction does not
        exceed the available balance. The `used_credits` field is updated and
        persisted to the database.

        Args:
            amount (int): The number of credits requested to be consumed.

        Returns:
            int: The actual number of credits successfully deducted from this bucket.
        """
        deduct = min(self.remaining_credits, amount)
        self.used_credits += deduct
        self.save(update_fields=["used_credits", "updated_at"])
        return deduct


class CreditLedgerType(models.TextChoices):
    CONSUME = "CONSUME", _("Consume")
    REFUND = "REFUND", _("Refund")
    GRANT = "GRANT", _("Grant")
    EXPIRE = "EXPIRE", _("Expire")
    PURCHASE = "PURCHASE", _("Purchase")


class CreditLedger(models.Model):
    """
    Provides an immutable audit trail for all credit-related transactions.
    It records every change to a user's credit balance—including consumption, refunds,
    grants, expiration, and purchases—and links these events to specific credit buckets
    to ensure full traceability of the credit lifecycle.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.CASCADE,
        related_name="credit_ledgers",
        help_text="User who owns the credit ledger",
    )
    bucket = models.ForeignKey(
        CreditBucket,
        null=True,
        on_delete=models.SET_NULL,
        related_name="credit_ledgers",
        help_text="Credit bucket the ledger is associated with",
    )
    ledger_type = models.CharField(
        max_length=20,
        choices=CreditLedgerType.choices,
        help_text="Type of credit ledger",
    )
    amount = models.IntegerField(
        help_text="Amount of credits to be added or subtracted from the bucket, "
        "positive for additions, negative for subtractions"
    )

    reference = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        help_text="Reference for the credit ledger",
    )
    metadata = models.JSONField(
        null=True, blank=True, help_text="Metadata for the credit ledger"
    )
    created_at = models.DateTimeField(
        auto_now_add=True, help_text="Date and time when the credit ledger was created"
    )

    class Meta:
        ordering = ["-created_at"]


class CreditUsageLog(models.Model):
    """
    Detailed record of specific credit consumption events. This model
    tracks the exact bucket used for a transaction, enabling precise
    deduction logic and providing a historical log of how and when
    credits from a particular source were spent.

    Logs every instance of credit consumption from a user's wallet.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(
        CreditWallet,
        on_delete=models.CASCADE,
        related_name="credit_usage_logs",
        help_text="Credit wallet the usage log is associated with",
    )
    bucket = models.ForeignKey(
        CreditBucket,
        on_delete=models.CASCADE,
        related_name="credit_usage_logs",
        help_text="Credit bucket the usage log is associated with",
    )
    amount = models.IntegerField(help_text="Amount of credits consumed from the bucket")
    feature = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        help_text="Feature or service for which credits are being consumed",
    )
    task_type = models.CharField(
        max_length=200, null=True, blank=True, help_text="Type of task being performed"
    )
    task_id = models.CharField(
        max_length=200, null=True, blank=True, help_text="ID of the task"
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Date and time when the credit usage log was created",
    )

    is_refunded = models.BooleanField(
        default=False, help_text="Whether the credit was refunded"
    )

    class Meta:
        ordering = ["-created_at"]


class BetaProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="beta_profile",
    )

    # 1. Cohort & Timing
    joined_beta_at = models.DateTimeField(auto_now_add=True, db_index=True)
    first_ai_action_at = models.DateTimeField(null=True, blank=True)
    last_active_at = models.DateTimeField(null=True, blank=True)
    last_login_date = models.DateField(null=True, blank=True)

    # Credit Velocity
    initial_beta_credits = models.PositiveIntegerField(default=10_000_000)
    total_credits_used = models.PositiveIntegerField(default=0, db_index=True)

    # Feature Mix (Raw totals for accurate P90 / Median math)
    credits_used_grading = models.PositiveIntegerField(default=0)
    credits_used_creation = models.PositiveIntegerField(default=0)
    analytics_view_count = models.PositiveIntegerField(default=0)

    # Intent Signals
    distinct_login_days = models.PositiveSmallIntegerField(default=0)
    has_hit_80_percent = models.BooleanField(default=False)
    has_hit_cap = models.BooleanField(default=False)

    conversion_probability = models.FloatField(
        default=0.0,
        validators=[MinValueValidator(0.0), MaxValueValidator(100.0)],
    )
    days_to_first_action = models.PositiveSmallIntegerField(null=True, blank=True)
    usage_velocity = models.FloatField(default=0.0, db_index=True)

    class Meta:
        verbose_name = "Beta Usage Profile"
        ordering = ["-conversion_probability"]
        indexes = [
            models.Index(fields=["conversion_probability"]),
            models.Index(fields=["last_active_at"]),
            models.Index(fields=["joined_beta_at"]),
        ]

    def __str__(self):
        return (
            f"Beta Profile for {self.user.email} (Score: {self.conversion_probability})"
        )
