import math
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .errors import InsufficientCreditsError

# from .services import SubscriptionService

# Create your models here.

CONVERSION_FACTOR = 1000


class StripeSubscriptionStatus(models.TextChoices):
    TRIALING = "TRIALING", _("Trialing")
    ACTIVE = "ACTIVE", _("Active")
    PAST_DUE = "PAST_DUE", _("Past Due")
    CANCELED = "CANCELED", _("Canceled")
    INCOMPLETE = "INCOMPLETE", _("Incomplete")
    UNPAID = "UNPAID", _("Unpaid")


class PlanType(models.TextChoices):
    # Individual
    STANDARD = "STANDARD", _("Standard")
    PRO = "PRO", _("Pro")
    POWER = "POWER", _("Power")
    BETA = "BETA", _("Beta")  # Internal only, not in spec
    CUSTOM = "CUSTOM", _("Custom")
    STANDARD_ANNUAL = "STANDARD_ANNUAL", _("Standard Annual")
    PRO_ANNUAL = "PRO_ANNUAL", _("Pro Annual")
    POWER_ANNUAL = "POWER_ANNUAL", _("Power Annual")
    TRIAL = "TRIAL", _("Trial")

    # License
    PRO_LICENSE = "PRO_LICENSE", _("Pro License")
    POWER_LICENSE = "POWER_LICENSE", _("Power License")
    CUSTOM_LICENSE_STARTER = "CUSTOM_LICENSE_STARTER", _("Custom License Starter")
    CUSTOM_LICENSE_MID = "CUSTOM_LICENSE_MID", _("Custom License Mid")
    CUSTOM_LICENSE_HIGH = "CUSTOM_LICENSE_HIGH", _("Custom License High")


class PlanCategory(models.TextChoices):
    INDIVIDUAL = "INDIVIDUAL", _("Individual")
    LICENSE = "LICENSE", _("License")


class PlanTier(models.TextChoices):
    STANDARD = "STANDARD", _("Standard")
    PRO = "PRO", _("Pro")
    POWER = "POWER", _("Power")
    BETA = "BETA", _("Beta")
    CUSTOM = "CUSTOM", _("Custom")
    TRIAL = "TRIAL", _("Trial")


class BillingInterval(models.TextChoices):
    MONTHLY = "MONTHLY", _("Monthly")
    ANNUAL = "ANNUAL", _("Annual")
    NONE = "NONE", _("None")


class PlanHighlight(models.TextChoices):
    """
    Highlights for a plan
    """

    BEST_VALUE = "BEST_VALUE", _("Best Value")
    MOST_POPULAR = "GREAT_VALUE", _("Great Value")


class PlanFeatureKey(models.TextChoices):
    ADVANCED_COURSE_ANALYTICS = "ADVANCED_COURSE_ANALYTICS", _(
        "Advanced Course Analytics"
    )
    ADVANCED_ASSIGNMENT_ANALYTICS = "ADVANCED_ASSIGNMENT_ANALYTICS", _(
        "Advanced Assignment Analytics"
    )
    AI_PROMPT_ASSIGNMENT_CREATION = "AI_PROMPT_ASSIGNMENT_CREATION", _(
        "AI Prompt-Based Assignment Creation"
    )
    AI_PROMPT_ANALYTICS_SUMMARY = "AI_PROMPT_ANALYTICS_SUMMARY", _(
        "AI Prompt-Based Analytics Summarization"
    )
    PRE_SCHEDULED_GRADING = "PRE_SCHEDULED_GRADING", _("Pre-Scheduled Grading")
    ADVANCED_STUDENT_ANALYTICS = "ADVANCED_STUDENT_ANALYTICS", _(
        "Advanced Student Analytics/Insights"
    )
    AI_EMAIL_FEEDBACK = "AI_EMAIL_FEEDBACK", _(
        "Optional AI Email Feedback for Assignments"
    )
    CREDIT_ROLLOVER_25 = "CREDIT_ROLLOVER_25", _("25% AI Credit Rollover")
    # Display-only / catalogue labels
    UNLIMITED_COURSES = "UNLIMITED_COURSES", _("Unlimited Courses (Archivable)")
    INVITE_STUDENTS_UPLOAD = "INVITE_STUDENTS_UPLOAD", _("Invite Students to Upload")
    BATCH_GRADING = "BATCH_GRADING", _("Batch Grading/Uploading")
    BASIC_INSIGHTS = "BASIC_INSIGHTS", _("Basic Course/Insights")
    ADMIN_MANAGED_BILLING = "ADMIN_MANAGED_BILLING", _("Admin-Managed Billing")
    SHARED_CREDIT_POOL = "SHARED_CREDIT_POOL", _("Shared Credit Pool")
    DEDICATED_SUPPORT = "DEDICATED_SUPPORT", _("Dedicated Support")


class LicenseBillingMethod(models.TextChoices):
    STRIPE = "STRIPE", _("Stripe")
    OFFLINE = "OFFLINE", _("Offline / Manually Billed")


class LicenseBillingRecordType(models.TextChoices):
    CREATED_OFFLINE = "CREATED_OFFLINE", _("Created (Offline)")
    RENEWED_OFFLINE = "RENEWED_OFFLINE", _("Renewed (Offline)")
    PLAN_CHANGE_OFFLINE = "PLAN_CHANGE_OFFLINE", _("Plan Changed (Offline)")
    SEATS_CHANGE_OFFLINE = "SEATS_CHANGE_OFFLINE", _("Seats Changed (Offline)")
    CONVERTED_TO_STRIPE = "CONVERTED_TO_STRIPE", _("Converted to Stripe")
    CONVERTED_TO_OFFLINE = "CONVERTED_TO_OFFLINE", _("Converted to Offline")
    MANUAL_OVERAGE_GRANT = "MANUAL_OVERAGE_GRANT", _("Manual Overage Grant")


class PlanFeature(models.Model):
    """
    Master catalogue of every feature that can appear on a plan card.
    One row per feature key — label lives here, not on the plan.
    """

    key = models.CharField(
        max_length=60,
        choices=PlanFeatureKey.choices,
        unique=True,
        primary_key=True,  # key IS the identity, no surrogate needed
    )
    label = models.CharField(
        max_length=200,
        help_text="Human-readable label shown on the pricing page",
    )
    is_gating_feature = models.BooleanField(
        default=False,
        help_text=(
            "True = used in code to gate access to a feature. "
            "False = display-only catalogue label."
        ),
    )

    class Meta:
        ordering = ["key"]

    def __str__(self):
        return f"{self.key}: {self.label}"


class PlanFeatureInclusion(models.Model):
    """
    Through table for the SubscriptionPlan ↔ PlanFeature M2M.

    Stores whether a feature is included on a plan AND the display
    order so the pricing page renders features in a consistent sequence.
    """

    plan = models.ForeignKey(
        "SubscriptionPlan",
        on_delete=models.CASCADE,
        related_name="feature_inclusions",
    )
    feature = models.ForeignKey(
        PlanFeature,
        on_delete=models.PROTECT,  # never silently delete a feature from all plans
        related_name="plan_inclusions",
    )
    included = models.BooleanField(
        default=False,
        help_text="Whether this feature is available on this plan",
    )
    display_order = models.PositiveSmallIntegerField(
        default=0,
        help_text="Controls render order on the pricing page card",
    )

    class Meta:
        unique_together = [("plan", "feature")]
        ordering = ["plan", "display_order"]

    def __str__(self):
        status = "✓" if self.included else "✗"
        return f"{self.plan.display_name} {status} {self.feature.key}"


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

    # --- Identity ---
    name = models.CharField(
        max_length=100,
        choices=PlanType.choices,
        unique=True,
        help_text="Unique code for the plan",
    )
    display_name = models.CharField(
        max_length=100, null=True, blank=True, help_text="Name of the plan"
    )
    tagline = models.CharField(
        max_length=100,
        blank=True,
        help_text="Short phrase describing the plan",
    )

    # --- Categorization ---
    category = models.CharField(
        max_length=20,
        choices=PlanCategory.choices,
        default=PlanCategory.INDIVIDUAL,
    )
    tier = models.CharField(
        max_length=20,
        choices=PlanTier.choices,
        default=PlanTier.STANDARD,
    )

    interval = models.CharField(
        max_length=20,
        choices=BillingInterval.choices,
        default=BillingInterval.MONTHLY,
    )

    # --- Stripe ---
    product_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Stripe Product ID (prod_xxx). Internal reference only.n",
    )

    stripe_price_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Stripe Price ID (price_xxx). Sent to frontend as `price_id`.",
    )

    # --- Pricing ---
    price_cents = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Price in USD (e.g. 24.99)",
    )

    # --- Credits ---
    monthly_credits = models.PositiveIntegerField(
        default=0,
        null=True,
        blank=True,
        help_text=(
            "Raw credits granted per cycle (display value × 1000). "
            "Null for Custom/contact-sales plans."
        ),
    )

    # --- Rollover ---

    carry_over_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text="Percentage of unused credits to carry over (e.g. 25.00 for 25%)",
    )

    carry_over_max = models.PositiveIntegerField(
        default=0,
        help_text="Maximum raw credits that can be carried over (display value × 1000)",
    )

    carry_over_expiry_months = models.PositiveSmallIntegerField(
        default=0,
        help_text="How many months carry-over credits remain valid",
    )

    # --- Overage ---
    overage_block_size = models.PositiveIntegerField(
        default=0,
        help_text="Raw credits per overage block (display value × 1000, e.g. 5_000_000 = 5K)",
    )
    # overage_block_price = models.PositiveIntegerField(
    #     default=0,
    #     help_text="Price per overage block in USD cents (e.g. 400 = $4.00)",
    # )

    overage_block_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Price per overage block in USD cents (e.g. 400 = $4.00)",
    )

    max_overage_blocks = models.PositiveSmallIntegerField(
        default=0,
        help_text="Maximum overage blocks a user can purchase per cycle",
    )

    # --- Features ---
    features = models.ManyToManyField(
        PlanFeature,
        through="PlanFeatureInclusion",
        related_name="plans",
    )

    # --- Display ---

    highlight = models.CharField(
        max_length=20,
        choices=PlanHighlight.choices,
        null=True,
        blank=True,
        help_text="Optional badge shown on the plan card (BEST_VALUE or GREAT_VALUE)",
    )
    is_contact_sales = models.BooleanField(
        default=False,
        help_text="If true, frontend shows 'Contact sales' instead of a checkout CTA",
    )

    is_active = models.BooleanField(
        default=True, help_text="Whether the plan is active"
    )

    class Meta:
        ordering = ["category", "tier"]

    def __str__(self):
        return self.name

    @property
    def display_monthly_credits(self) -> int | None:
        if self.monthly_credits is None:
            return None
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

    is_trial = models.BooleanField(
        default=False,
        help_text=(
            "True while this subscription is in its free trial period ",
            "Flipped to False on trial expiry or paid conversion",
        ),
        null=True,
        blank=True,
    )

    trial_end = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "Datetime when the free trial expires. Null for non-trial subscriptions. "
            "Celery checks this field nightly to trigger expiry cleanup"
        ),
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

    stripe_subscription_id = models.CharField(
        max_length=255, null=True, blank=True, db_index=True
    )
    stripe_customer_id = models.CharField(
        max_length=255, null=True, blank=True, db_index=True
    )
    stripe_status = models.CharField(
        max_length=20, choices=StripeSubscriptionStatus.choices, null=True, blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True, help_text="Date and time when the subscription was created"
    )
    updated_at = models.DateTimeField(
        auto_now=True, help_text="Date and time when the subscription was last updated"
    )

    next_credit_grant_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "When the current MONTHLY credit bucket expires and the next one "
            "is due. For MONTHLY-interval plans this always coincides with "
            "billing_cycle_end. For ANNUAL-interval plans it's independent — "
            "Stripe only bills once a year, but credits still refresh monthly, "
            "so this tracks the monthly refresh clock separately from the "
            "yearly billing/contract clock."
        ),
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

    stripe_customer_id = models.CharField(
        max_length=255, null=True, blank=True, db_index=True
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
        Consumes credits from the user's wallet using a smart expiry-aware FIFO strategy.

        Consumption order is determined by expiry date (soonest-expiring first) among
        CARRY_OVER, MONTHLY, and MANUAL_GRANT buckets, with OVERAGE always consumed last.
        MANUAL_GRANT buckets with no expiry are consumed after time-bounded buckets
        but before OVERAGE, ensuring goodwill credits don't displace subscription value
        unnecessarily while still being used before paid overage blocks.

        Args:
            amount (int): Raw credits to consume.
            feature (str, optional): Feature identifier for analytics.
            task_type (str, optional): Task type for analytics.
            task_id (str, optional): Task ID for refund traceability.

        Returns:
            int: The amount consumed (always equals requested amount on success).

        Raises:
            InsufficientCreditsError: If total available credits are less than requested.
        """
        total_available = self.total_remaining_credits()

        while total_available < amount:
            # from .services import SubscriptionService

            # success = SubscriptionService.purchase_overage_block(wallet=self)
            #
            # if not success:
            #     available_credits = self.total_remaining_credits()
            raise InsufficientCreditsError(
                f"Insufficient credits and overage limit reached. "
                f"Requested: {amount}, Available: {total_available}"
            )

        now = timezone.now()

        # --- Build the ordered bucket query ---
        # Strategy:
        #   1. OVERAGE always comes last (it costs money).
        #   2. Among CARRY_OVER, MONTHLY, MANUAL_GRANT: order by expires_at ASC
        #      so soonest-to-expire credits are drained first.
        #   3. Buckets with no expiry (null expires_at) sit after time-bounded
        #      ones but before OVERAGE — achieved via nulls_last on expires_at.
        #   4. Within the same expires_at value, prefer CARRY_OVER → MONTHLY →
        #      MANUAL_GRANT via a type-priority tiebreaker.

        consumable_types = [
            CreditBucketType.CARRY_OVER,
            CreditBucketType.TRIAL,
            CreditBucketType.MONTHLY,
            CreditBucketType.MANUAL_GRANT,
            CreditBucketType.OVERAGE,
        ]

        type_priority = models.Case(
            models.When(bucket_type=CreditBucketType.CARRY_OVER, then=models.Value(0)),
            models.When(bucket_type=CreditBucketType.TRIAL, then=models.Value(1)),
            models.When(bucket_type=CreditBucketType.MONTHLY, then=models.Value(2)),
            models.When(
                bucket_type=CreditBucketType.MANUAL_GRANT, then=models.Value(3)
            ),
            models.When(bucket_type=CreditBucketType.OVERAGE, then=models.Value(4)),
            default=models.Value(5),
            output_field=models.IntegerField(),
        )

        # Sentinel: push overage to the very back regardless of its expires_at
        is_overage = models.Case(
            models.When(bucket_type=CreditBucketType.OVERAGE, then=models.Value(1)),
            default=models.Value(0),
            output_field=models.IntegerField(),
        )

        buckets = (
            self.buckets.select_for_update()
            .filter(bucket_type__in=consumable_types)
            .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
            .annotate(
                overage_sentinel=is_overage,
                type_priority=type_priority,
            )
            .order_by(
                "overage_sentinel",
                models.F("expires_at").asc(nulls_last=True),
                "type_priority",
                "created_at",
            )
        )

        remaining = amount
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
                    reference=(
                        f"Consumption of {deducted} credits for "
                        f"{feature} ({task_type}: {task_id})"
                    ),
                    metadata={
                        "feature": feature,
                        "task_type": task_type,
                        "task_id": task_id,
                    },
                )
            )

        CreditUsageLog.objects.bulk_create(usage_log)
        CreditLedger.objects.bulk_create(ledger_log)

        return amount

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
    MANUAL_GRANT = "MANUAL_GRANT", _("Manual Grant")
    TRIAL = "TRIAL", _("Free Trial")


class CreditBucket(models.Model):
    """
    Represents a specific pool of credits granted to a user.

    Credits are categorized into buckets based on their source (Monthly, Carry Over, or Overage)
    to facilitate granular tracking of usage, expiration policies, and consumption priority.
    Each bucket tracks the initial allocation versus the remaining balance.

    - MONTHLY: Regular subscription allocation
    - CARRY_OVER: Unused credits rolled over from a previous cycle
    - OVERAGE: Purchased blocks beyond the subscription limit
    - MANUAL_GRANT: Credits manually added by a superadmin
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
        help_text=(
            "Date and time when the credits expire. Only for carry_over / overage",
            "Null means the credits never expire (valid for MANUAL_GRANT).",
        ),
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
        if self.is_expired():
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
    PLAN_CHANGE = "PLAN_CHANGE", _("Plan Change")


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
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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
    initial_beta_credits = models.PositiveIntegerField(default=20_000_000)
    total_credits_used = models.PositiveIntegerField(default=0, db_index=True)

    # Feature Mix (Raw totals for accurate P90 / Median math)
    credits_used_grading = models.PositiveIntegerField(default=0)
    credits_used_creation = models.PositiveIntegerField(default=0)
    credits_used_feedback = models.PositiveIntegerField(default=0)
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
            f"Beta Profile for {self.user.email} "
            f"(Score: {self.conversion_probability})"
        )


class LicenseSubscription(models.Model):
    """
    Represents a school/institutional subscription for multiple teachers.

    A License subscription is created at the school level and managed by a school admin.
    Teachers under this license get individual credit allocations but cannot modify
    their billing. The license handles one Stripe subscription for the entire school.

    Key differences from UserSubscription:
    - One License can serve multiple teachers
    - Teachers do not control billing (read-only)
    - Each teacher gets an individual SchoolCreditAllocation
    - School admin manages upgrades/downgrades/cancellation
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # --- School & Admin ---
    school = models.ForeignKey(
        "classrooms.School",
        on_delete=models.CASCADE,
        related_name="license_subscriptions",
        help_text="School that owns this license",
    )
    admin_user = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.PROTECT,
        related_name="managed_license_subscriptions",
        help_text="Admin user who manages this license (typically school admin)",
    )

    # --- Plan & Billing ---
    plan = models.ForeignKey(
        "SubscriptionPlan",
        on_delete=models.PROTECT,
        related_name="license_subscriptions",
        help_text="License plan (must have category=LICENSE)",
    )

    contract_months = models.PositiveSmallIntegerField(
        default=12,
        help_text=(
            "Contract duration in months. Schools can choose 9, 10, or 12 month "
            "billing periods. This determines how far ahead billing_cycle_end is set "
            "on creation and each renewal."
        ),
    )

    max_seats = models.PositiveIntegerField(
        default=1,
        help_text=(
            "Maximum number of teacher seats allowed under this license. "
            "0 = unlimited (e.g. for Custom contracts). Enforced on enrollment."
        ),
    )

    billing_cycle_start = models.DateTimeField(
        help_text="Start date of the current billing cycle"
    )
    billing_cycle_end = models.DateTimeField(
        help_text="End date of the current billing cycle"
    )

    # --- Status ---
    is_active = models.BooleanField(
        default=True, help_text="Whether the license subscription is active"
    )
    auto_renew = models.BooleanField(
        default=True, help_text="Whether the license auto-renews at cycle end"
    )

    # --- Stripe Integration ---
    stripe_subscription_id = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Stripe subscription ID for this license (one per school)",
    )

    stripe_customer_id = models.CharField(
        max_length=255, null=True, blank=True, db_index=True
    )

    stripe_status = models.CharField(
        max_length=20, choices=StripeSubscriptionStatus.choices, null=True, blank=True
    )

    billing_method = models.CharField(
        max_length=20,
        choices=LicenseBillingMethod.choices,
        default=LicenseBillingMethod.STRIPE,
        help_text=(
            "STRIPE = billed automatically via a Stripe subscription. "
            "OFFLINE = manually billed/renewed by a superadmin outside the platform "
            "(wire transfer, PO, etc). Convertible in either direction."
        ),
    )

    custom_price_cents = models.IntegerField(
        null=True,
        blank=True,
        help_text="Negotiated monthly price for this license, overriding the plan's default price. Set by super admin.",
    )

    total_credits_consumed = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Total raw credits consumed by all teachers under this "
            "license in the current billing cycle. Used for global cap enforcement."
        ),
    )

    # --- Timestamps ---
    created_at = models.DateTimeField(
        auto_now_add=True, help_text="Date and time when the license was created"
    )
    updated_at = models.DateTimeField(
        auto_now=True, help_text="Date and time when the license was last updated"
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "License Subscription"
        verbose_name_plural = "License Subscriptions"

    def __str__(self):
        return f"{self.school.name} - {self.plan.display_name or self.plan.name}"

    @property
    def teacher_count(self):
        """Returns number of active teachers enrolled under this license."""
        return self.allocations.filter(is_active=True).count()

    @property
    def seats_remaining(self) -> int | None:
        """
        Returns the number of remaining enrollable seats.
        Returns None if max_seats=0 (unlimited).
        """
        if self.max_seats == 0:
            return None  # unlimited
        return max(0, self.max_seats - self.teacher_count)


class LicenseBillingRecord(models.Model):
    """
    Immutable accounting trail for anything billing-relevant that happens
    to a LicenseSubscription outside a normal Stripe invoice — offline
    creation, offline renewal, offline plan/seat changes, billing-method
    conversions, and manual overage grants. Mirrors how CreditLedger is an
    immutable audit trail for wallet-level events; this is the license-level
    equivalent for events that never touch Stripe (or that need a
    superadmin's payment reference recorded even when Stripe was involved,
    e.g. a manual overage comp).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    license_subscription = models.ForeignKey(
        LicenseSubscription,
        on_delete=models.CASCADE,
        related_name="billing_records",
    )
    record_type = models.CharField(
        max_length=30, choices=LicenseBillingRecordType.choices
    )

    # Accounting detail — all optional since not every record type involves
    # a specific payment (e.g. CONVERTED_TO_OFFLINE has no amount).
    amount_paid_cents = models.IntegerField(null=True, blank=True)
    payment_reference = models.CharField(
        max_length=250,
        null=True,
        blank=True,
        help_text="Invoice #, PO #, wire confirmation, check #, etc.",
    )
    payment_method_label = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="e.g. 'Wire transfer', 'Check', 'PO #4471'",
    )
    notes = models.TextField(null=True, blank=True)

    # Only populated for RENEWED_OFFLINE
    previous_billing_cycle_end = models.DateTimeField(null=True, blank=True)
    new_billing_cycle_end = models.DateTimeField(null=True, blank=True)

    performed_by = models.ForeignKey(
        "users.CustomUser",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="license_billing_actions",
        help_text="The superadmin who performed this action. Null if system/webhook-initiated.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["license_subscription", "record_type"]),
        ]


class SchoolCreditAllocation(models.Model):
    """
    Represents an individual teacher's credit allocation under a LicenseSubscription.

    Each teacher under a license gets their own SchoolCreditAllocation, which defines:
    - Which license they belong to
    - Their monthly credit allocation (independent from other teachers)
    - Their associated CreditWallet (where credits are actually stored)

    This is the bridge between LicenseSubscription and individual teacher CreditWallets.
    It ensures each teacher has independent credit tracking and consumption.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # --- License & Teacher ---
    license_subscription = models.ForeignKey(
        LicenseSubscription,
        on_delete=models.CASCADE,
        related_name="allocations",
        help_text="License subscription this allocation belongs to",
    )
    user = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.CASCADE,
        related_name="school_credit_allocations",
        help_text="Teacher enrolled under this license",
    )

    # --- Allocation ---
    monthly_allocation = models.PositiveIntegerField(
        help_text="Raw monthly credit allocation for this teacher (display value × 1000)",
    )

    # --- Status ---
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this teacher is actively enrolled under the license",
    )

    # --- Timestamps ---
    created_at = models.DateTimeField(
        auto_now_add=True, help_text="Date and time when the allocation was created"
    )
    updated_at = models.DateTimeField(
        auto_now=True, help_text="Date and time when the allocation was last updated"
    )

    next_credit_grant_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the next monthly credit refresh is due for this teacher under the license",
    )

    class Meta:
        unique_together = [("license_subscription", "user")]
        ordering = ["created_at"]
        verbose_name = "School Credit Allocation"
        verbose_name_plural = "School Credit Allocations"
        indexes = [
            models.Index(fields=["license_subscription", "is_active"]),
            models.Index(fields=["user", "is_active"]),
        ]

    def __str__(self):
        return f"{self.user.email} under {self.license_subscription.school.name}"

    @property
    def display_monthly_allocation(self) -> int:
        """Returns display value (raw value / 1000)"""
        return self.monthly_allocation // CONVERSION_FACTOR


class StripeEvent(models.Model):
    """Idempotency ledger for Stripe webhook events - see billing/webhooks.py"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    stripe_event_id = models.CharField(
        max_length=255, null=True, blank=True, db_index=True
    )
    event_type = models.CharField(max_length=100)
    payload = models.JSONField(null=True, blank=True)
    processed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-processed_at"]
