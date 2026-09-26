import logging
import math
import uuid
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from .errors import InsufficientCreditsError
from .immutable import AppendOnlyModel, register_append_only_guards

# Create your models here.

CONVERSION_FACTOR = 1000
logger = logging.getLogger(__name__)


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
    OFFLINE_OVERAGE_REQUEST_APPROVED = (
        "OFFLINE_OVERAGE_REQUEST_APPROVED",
        _("Offline Overage Request Approved"),
    )
    CANCELLED = "CANCELLED", _("Cancelled")


class PendingChangeType(models.TextChoices):
    DOWNGRADE = "DOWNGRADE", _("Downgrade")
    UPGRADE_DEFERRED = "UPGRADE_DEFERRED", _("Upgrade (deferred)")
    LATERAL_DEFERRED = "LATERAL_DEFERRED", _("Interval change (deferred)")


PLAN_TIER_HIERARCHY = [
    PlanTier.STANDARD,
    PlanTier.PRO,
    PlanTier.POWER,
]


def get_tier_rank(tier):
    """
    Returns the position of `tier` in PLAN_TIER_HIERARCHY (0 = lowest value}.
    A higher rank means a more valuable/featureful tier, indepedent of price
    """

    try:
        return PLAN_TIER_HIERARCHY.index(tier)
    except ValueError:
        raise ValueError(
            f"Tier {tier!r} is not part of PLAN_TIER_HIERARCHY and has no "
            f"defined upgrade/downgrade ranking. Add it to "
            f"PLAN_TIER_HIERARCHY in models.py if this tier should be "
            f"comparable."
        ) from None


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
    stripe_overage_price_id = models.CharField(
        max_length=100, null=True, blank=True, help_text="Stripe Price ID for overage"
    )

    # --- Pricing ---
    price_cents = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text=(
            "Base subscription price in CENTS, matching Stripe's "
            "`unit_amount` exactly — STANDARD is 1499, not 14.99. The field "
            "name is right and the old help text was wrong; entering "
            "dollars here would under-bill by 100x and the nightly "
            "reconciliation would report it as drift against Stripe. "
            "Decimal rather than integer only for historical reasons."
        ),
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

    max_bank = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=(
            "Ceiling on a user's total LIVE banked balance — MONTHLY + "
            "CARRY_OVER buckets combined (raw credits, display value × "
            "1000). OVERAGE and MANUAL_GRANT credits are exempt and never "
            "counted against this. Null = no cap. At every rollover point, "
            "the carryover portion (never the monthly grant) is trimmed so "
            "the combined live total never exceeds this value."
        ),
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

    overage_block_price = models.IntegerField(
        default=0,
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
    def display_max_bank(self) -> int | None:
        if self.max_bank is None:
            return None
        return self.max_bank // CONVERSION_FACTOR

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

    cancelled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the user (or a Stripe-side action) scheduled this "
            "subscription to stop renewing. Set alongside "
            "auto_renew=False by the cancel endpoint and by the "
            "customer.subscription.updated webhook; cleared again on "
            "resume. Purely informational — auto_renew remains the "
            "operative flag — but it is the only record of WHEN the "
            "cancellation was requested, which the frontend shows back "
            "to the user. Null for trials (whose auto_renew is False "
            "from birth without any cancellation having happened) and "
            "for rows cancelled before this field existed."
        ),
    )
    pending_plan = models.ForeignKey(
        "SubscriptionPlan",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="user_pending_subscriptions",
    )

    pending_change_type = models.CharField(
        max_length=20,
        choices=PendingChangeType.choices,
        null=True,
        blank=True,
        help_text=(
            "Why pending_plan is scheduled rather than applied "
            "immediately. Null whenever pending_plan is null."
        ),
    )

    pending_change_note = models.TextField(
        null=True,
        blank=True,
        help_text=(
            "Persisted, user-facing explanation of the scheduled "
            "change, captured at schedule time so it stays stable "
            "and accurate for the frontend to display on every "
            "visit, independent of any later catalog/pricing "
            "changes. Null whenever pending_plan is null."
        ),
    )

    stripe_schedule_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "ID of the Stripe SubscriptionSchedule managing a deferred "
            "plan change for this subscription, if any. Set whenever "
            "pending_plan is scheduled (StripeSubscriptionScheduleService "
            ".schedule_plan_change_on_stripe); cleared when the schedule "
            "is released — either because the scheduled change was "
            "cancelled, or because it was superseded by an immediate "
            "change instead. May remain set even after a scheduled "
            "change actually takes effect at renewal — Stripe schedules "
            "with an open-ended final phase don't self-terminate, so the "
            "same schedule is reused for the NEXT deferred change on this "
            "subscription rather than creating a new one each time."
        ),  # <-- NEW
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
    # Credits that a lost chargeback should have reclaimed but could not,
    # because the customer had already spent them. Recorded as a debt
    # rather than by deleting usage history — the work was really done and
    # really cost us; hiding it would make the ledger lie.
    dispute_deficit_credits = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Credits owed back after a lost chargeback that were already "
            "consumed and so could not be reclaimed from a bucket."
        ),
    )
    # The same debt, arising from a refund rather than a chargeback. Kept
    # as a SEPARATE counter rather than folded into the dispute one so the
    # two causes stay tellable apart in the audit trail — "we gave this
    # money back" and "an issuer took it from us" are different events with
    # different follow-up, even though their effect on entitlement is
    # identical. See billing/payment_refunds.py.
    refund_deficit_credits = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Credits owed back after a refund that were already consumed "
            "and so could not be reclaimed from a bucket."
        ),
    )
    # Set when a deficit is recorded. Checked by consume_credits so a
    # customer whose payment was charged back or refunded cannot keep
    # spending on it. Derived from the two counters above via
    # sync_consumption_block() — never set directly, or the two causes can
    # clear each other's block.
    is_consumption_blocked = models.BooleanField(
        default=False,
        help_text=(
            "Blocks further credit consumption while an unsettled dispute "
            "or refund deficit exists. Cleared by hand once the account is "
            "settled."
        ),
    )
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

    @cached_property
    def active_subscription(self):
        return self.user.subscriptions.filter(is_active=True).first()

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
        result = self.buckets.filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        ).aggregate(
            total=models.Sum(models.F("total_credits") - models.F("used_credits"))
        )

        return result["total"] or 0

    def plan_remaining_credits(self):
        """
        Like total_remaining_credits(), but excludes OVERAGE buckets.

        OVERAGE buckets are purchased reactively, after a user has already
        exhausted their plan — they aren't part of a fixed allocation, so
        folding them into a "% of plan consumed" figure would make that
        percentage swing unpredictably every time a user buys more, rather
        than reflecting how much of their actual plan they've used.
        """
        now = timezone.now()
        result = (
            self.buckets.filter(
                models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
            )
            .exclude(bucket_type=CreditBucketType.OVERAGE)
            .aggregate(
                total=models.Sum(models.F("total_credits") - models.F("used_credits"))
            )
        )
        return result["total"] or 0

    def plan_used_credits(self):
        """
        Credits consumed from the CURRENTLY LIVE non-overage buckets — the
        companion numerator to plan_remaining_credits(): both are read from
        the same live bucket rows, so used / (used + remaining) is a
        coherent "% of current plan consumed". (Summing CreditUsageLog
        instead would mix an all-time numerator with a current-cycle
        denominator, inflating the percentage toward 100% as history
        accumulates.) Refunds are already reflected here because
        refund_credits decrements the bucket's used_credits directly.
        """
        now = timezone.now()
        result = (
            self.buckets.filter(
                models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
            )
            .exclude(bucket_type=CreditBucketType.OVERAGE)
            .aggregate(total=models.Sum("used_credits"))
        )
        return result["total"] or 0

    def live_carry_over_total(self, now=None, exclude_bucket_id=None, lock=True):
        """
        Sum of remaining (total_credits - used_credits) across every
        currently-live CARRY_OVER bucket for this wallet. Used as the
        "how much banked balance already exists" input to max_bank
        enforcement — see compute_capped_rollover.

        lock=True (default) takes select_for_update() on the underlying
        bucket rows, so this MUST be called from within an active
        transaction (every current call site already is). Deliberately
        avoids combining select_for_update() with .aggregate() — locks
        the rows via a plain queryset first, then sums in Python, to
        sidestep any DB-backend inconsistency around locking aggregated
        queries.

        exclude_bucket_id: optional — excludes one bucket from the sum
        (for a future case where the bucket being retired is itself a
        CARRY_OVER bucket; no current call site needs this, since every
        existing rollover site retires a MONTHLY bucket, but the hook is
        cheap to keep for future-proofing).
        """
        now = now or timezone.now()
        qs = self.buckets.filter(bucket_type=CreditBucketType.CARRY_OVER).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        )
        if exclude_bucket_id:
            qs = qs.exclude(pk=exclude_bucket_id)
        if lock:
            qs = qs.select_for_update()

        buckets = list(qs.only("total_credits", "used_credits"))
        return sum(max(0, b.total_credits - b.used_credits) for b in buckets)

    def compute_capped_rollover(
        self,
        plan,
        unused_credits,
        monthly_amount=None,
        now=None,
        exclude_bucket_id=None,
    ):
        """
        Single source of truth for how much of `unused_credits` may
        actually roll over into a new CARRY_OVER bucket, after applying
        plan.max_bank as a ceiling on the wallet's total live banked
        balance (MONTHLY + CARRY_OVER only — OVERAGE and MANUAL_GRANT are
        exempt by design and never enter this calculation).

        The monthly grant itself is NEVER trimmed — only the carryover
        portion is reduced to make room. If max_bank is smaller than the
        monthly grant alone, carryover is forced to 0 (never negative);
        this is logged as a likely plan misconfiguration rather than
        silently accepted.

        Args:
            plan: The SubscriptionPlan whose carry_over_percent/max_bank
                govern this rollover. For a plan CHANGE, this must be the
                TARGET plan (the new plan's rules apply to the rollover
                that feeds into it) — matches the existing convention at
                every call site.
            unused_credits: Raw credits left unused in the bucket being
                retired.
            monthly_amount: The actual number of credits about to occupy
                the new MONTHLY bucket alongside this rollover. Defaults
                to plan.monthly_credits (correct for individual
                subscriptions, where the grant always equals the plan
                default). License callers MUST pass the real grant_amount
                here, since it can be less than plan.monthly_credits when
                capped by the license's seat/global budget — using the
                nominal plan value there would under-count room and
                over-trim carryover.
            now: Reference timestamp for "currently live" (defaults to
                timezone.now()).
            exclude_bucket_id: see live_carry_over_total.

        Returns:
            tuple[int, dict]: (final_rollover_amount, capping_metadata).
            capping_metadata is always safe to merge into a CreditLedger
            entry's `metadata` field and includes enough detail to fully
            reconstruct the decision after the fact:
                requested_rollover, final_rollover, max_bank_applied
                (bool), max_bank, existing_live_carry_over,
                monthly_amount_used.
        """
        requested = int(unused_credits * (plan.carry_over_percent / 100))
        requested = max(0, requested)

        if plan.max_bank is None:
            return requested, {
                "requested_rollover": requested,
                "final_rollover": requested,
                "max_bank_applied": False,
                "max_bank": None,
                "existing_live_carry_over": None,
                "monthly_amount_used": None,
            }

        now = now or timezone.now()
        existing_carry_over = self.live_carry_over_total(
            now=now, exclude_bucket_id=exclude_bucket_id
        )
        effective_monthly = (
            plan.monthly_credits if monthly_amount is None else monthly_amount
        ) or 0

        if plan.max_bank < effective_monthly:
            logger.warning(
                "Plan %s has max_bank (%d) LOWER than its monthly grant "
                "(%d) for wallet %s — this is a plan misconfiguration. "
                "Carryover forced to 0; monthly grant is never trimmed.",
                plan.name,
                plan.max_bank,
                effective_monthly,
                self.id,
            )

        room = max(0, plan.max_bank - effective_monthly - existing_carry_over)
        final = max(0, min(requested, room))

        return final, {
            "requested_rollover": requested,
            "final_rollover": final,
            "max_bank_applied": final < requested,
            "max_bank": plan.max_bank,
            "existing_live_carry_over": existing_carry_over,
            "monthly_amount_used": effective_monthly,
        }

    @transaction.atomic
    def consume_credits(
        self,
        amount,
        feature=None,
        task_type=None,
        task_id=None,
        course=None,
        school=None,
    ):
        """
        Consumes credits from the user's wallet using a type-priority,
        expiry-aware FIFO strategy.

        Consumption order is determined PRIMARILY by bucket type —
        CARRY_OVER -> TRIAL -> MONTHLY -> MANUAL_GRANT -> OVERAGE — not by
        expiry date. This is deliberate: CARRY_OVER and TRIAL are one-shot
        pools that are permanently forfeited at their own expiry with no
        further chance to roll over, whereas unused MONTHLY balance gets
        another chance to become carry-over at the NEXT rollover (subject
        to the plan's cap). Prioritizing the pools that are actually at
        risk of permanent loss ahead of the renewable one minimizes real
        credit waste — draining by "soonest expiry" instead would let a
        long-lived CARRY_OVER bucket (e.g. carry_over_expiry_months > 1)
        sit untouched while MONTHLY drains first, which is backwards.
        Expiry date is only a SECONDARY tiebreaker, used to order multiple
        buckets of the same type against each other (e.g. two CARRY_OVER
        buckets alive at once from different rollovers). OVERAGE always
        comes last regardless of its own (now always-null) expiry, since
        it costs money and free/rollover credit should be exhausted first.

        Args:
            amount (int): Raw credits to consume.
            feature (str, optional): Feature identifier for analytics.
            task_type (str, optional): Task type for analytics.
            task_id (str, optional): Task ID for refund traceability.
            course (Course, optional): Course the task was performed under,
                when known, for per-session usage attribution.
            school (School, optional): The billed user's school at the
                moment of consumption — a snapshot for historically
                accurate school-level reporting even if the user is later
                reassigned to a different school.

        Returns:
            int: The amount consumed (always equals requested amount on success).

        Raises:
            InsufficientCreditsError: If total available credits are less than requested.
        """
        # Lock CreditWallet row to serialize consumption requests
        locked = CreditWallet.objects.select_for_update().get(pk=self.pk)

        # Re-read under the lock: a dispute landing concurrently must not be
        # raced past by a consumption that read the flag a moment earlier.
        if locked.is_consumption_blocked:
            raise InsufficientCreditsError(
                "Credit consumption is blocked on this account: a reversed "
                f"payment left an unsettled deficit of "
                f"{locked.total_deficit_credits} credits "
                f"({locked.dispute_deficit_credits} from chargebacks, "
                f"{locked.refund_deficit_credits} from refunds)."
            )

        total_available = self.total_remaining_credits()

        if total_available < amount:
            raise InsufficientCreditsError(
                f"Insufficient credits. "
                f"Requested: {amount}, Available: {total_available}"
            )

        now = timezone.now()

        # --- Build the ordered bucket query ---
        # Strategy:
        #   1. Order PRIMARILY by type_priority: CARRY_OVER -> TRIAL ->
        #      MONTHLY -> MANUAL_GRANT -> OVERAGE. This one ordinal alone
        #      guarantees OVERAGE always comes last (it has the highest
        #      value) — no separate sentinel needed.
        #   2. Within the same type, order by expires_at ASC (soonest
        #      first) so multiple buckets of one type — e.g. two
        #      CARRY_OVER buckets alive at once from different rollovers —
        #      still drain the more time-sensitive one first.
        #   3. Buckets with no expiry (null expires_at) sort after
        #      time-bounded ones of the same type — achieved via
        #      nulls_last on expires_at.

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

        buckets = (
            self.buckets.select_for_update()
            .filter(bucket_type__in=consumable_types)
            .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
            .annotate(type_priority=type_priority)
            .order_by(
                "type_priority",
                models.F("expires_at").asc(nulls_last=True),
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

            if not deducted:
                continue

            usage_log.append(
                CreditUsageLog.build(
                    wallet=self,
                    bucket=bucket,
                    amount=deducted,
                    feature=feature,
                    task_type=task_type,
                    task_id=task_id,
                    course=course,
                    school=school,
                )
            )

            ledger_log.append(
                CreditLedger.build(
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

        if remaining > 0:
            # Defensive: total_remaining_credits() said the balance was
            # sufficient, but the locked bucket scan couldn't cover the
            # full amount (e.g. a bucket crossed its expires_at between the
            # two reads). Raising here rolls the whole charge back rather
            # than silently under-charging while reporting the full amount
            # as consumed.
            raise InsufficientCreditsError(
                f"Insufficient credits. Requested: {amount}, "
                f"Available: {amount - remaining}"
            )

        CreditUsageLog.objects.bulk_create(usage_log)
        CreditLedger.objects.bulk_create(ledger_log)

        # Explicit call, NOT a post_save signal: bulk_create never emits
        # post_save, so a signal-based hook here silently never fires (which
        # is exactly how license consumption tracking was broken before).
        self._record_license_consumption(amount)

        return amount

    def _record_license_consumption(self, amount):
        """
        Roll this consumption up into the owning LicenseSubscription's
        per-cycle total_credits_consumed, if the wallet owner is an active
        non-admin teacher seat under an active license. Admin analytics
        allocations (is_admin_allocation=True) are deliberately excluded,
        matching every other place that counts license consumption.

        Runs inside consume_credits' transaction; the single-statement F()
        update is atomic, so no extra row lock is needed. Acquired last —
        after the wallet and bucket locks — in both the consume and refund
        paths, so lock ordering stays consistent between them.
        """
        if amount <= 0:
            return

        allocation = (
            SchoolCreditAllocation.objects.filter(
                user=self.user,
                is_active=True,
                is_admin_allocation=False,
                license_subscription__is_active=True,
            )
            .only("id", "license_subscription_id")
            .first()
        )
        if not allocation:
            return

        LicenseSubscription.objects.filter(
            pk=allocation.license_subscription_id
        ).update(
            total_credits_consumed=models.F("total_credits_consumed") + amount,
            updated_at=timezone.now(),
        )

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

    @property
    def total_deficit_credits(self) -> int:
        """Everything owed back, whatever took the money away."""
        return (self.dispute_deficit_credits or 0) + (self.refund_deficit_credits or 0)

    def sync_consumption_block(self, *, save=True):
        """
        Recompute the consumption block from BOTH deficit counters.

        The single place allowed to write `is_consumption_blocked`. Setting
        it from one counter alone is the bug this exists to prevent: a
        chargeback won late would clear the block even though an unrelated
        refund deficit was still outstanding, and vice versa.
        """
        blocked = self.total_deficit_credits > 0
        if blocked == self.is_consumption_blocked:
            return False
        self.is_consumption_blocked = blocked
        if save:
            self.save(update_fields=["is_consumption_blocked", "updated_at"])
        return True


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
    # Credits clawed back because the payment that bought them was lost to
    # a chargeback. A NEGATIVE amount, appended like everything else — the
    # original GRANT row is never edited or deleted, so the history still
    # shows what was given and a separate row shows what was taken back.
    DISPUTE_REVERSAL = "DISPUTE_REVERSAL", _("Dispute Reversal")
    # The same claw-back, because the payment was REFUNDED rather than
    # charged back. Deliberately distinct from REFUND above, which means
    # the opposite thing — REFUND returns credits TO the customer when a
    # task failed; REFUND_REVERSAL takes credits back because we returned
    # their money. Confusing the two would invert a balance.
    REFUND_REVERSAL = "REFUND_REVERSAL", _("Refund Reversal")


class CreditLedger(AppendOnlyModel):
    """
    Provides an immutable audit trail for all credit-related transactions.
    It records every change to a user's credit balance—including consumption, refunds,
    grants, expiration, and purchases—and links these events to specific credit buckets
    to ensure full traceability of the credit lifecycle.

    "Immutable" is enforced, not merely asserted — see billing/immutable.py.
    Rows cannot be updated or deleted through the ORM, and no relation into
    this table cascades or nulls, so a row outlives the user, wallet and
    bucket it describes. Corrections are made by recording a new row.

    Identity is stored as VALUES (`user_id`, `user_email`), not as a
    foreign key. A relation would tie the audit record's survival to
    another mutable row; an audit trail that disappears with its subject
    is not an audit trail. Construct rows with `record()` / `build()` so
    the email snapshot is captured consistently.
    """

    mutable_fields = frozenset()

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "ID of the user this entry belongs to, stored as a plain value. "
            "Deliberately NOT a ForeignKey: the entry must survive deletion "
            "of the account. May reference a user that no longer exists."
        ),
    )
    user_email = models.CharField(
        max_length=254,
        null=True,
        blank=True,
        help_text=(
            "The user's email captured at write time — the only human-"
            "readable attribution left once the account is gone. A snapshot, "
            "deliberately not derived by joining to the live user row."
        ),
    )
    bucket = models.ForeignKey(
        CreditBucket,
        null=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="credit_ledgers",
        help_text=(
            "Credit bucket the ledger is associated with. DO_NOTHING with no "
            "database constraint: the previous SET_NULL did not delete the "
            "row but did UPDATE it, which is itself a mutation of an "
            "immutable record. The id is retained even once the bucket is "
            "gone."
        ),
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
    #: Which Stripe payment this row is attributable to, promoted out of
    #: `metadata` into a real indexed column.
    #:
    #: Reversing a payment needs the exact opposite of what the rest of
    #: billing needs: not "what does this wallet hold" but "what did THIS
    #: payment buy, wherever it landed". A school overage purchase spreads
    #: one payment across many teachers' wallets, so there is no single
    #: wallet to scope the question to. Reading it back out of the JSON
    #: `metadata` would work but is a sequential scan of the fastest-growing
    #: table in the schema, executed inside a webhook transaction holding
    #: locks — the same cost that made the unscoped form of
    #: `_overage_already_granted` unacceptable.
    #:
    #: Populated automatically by `build()` from the metadata the grant
    #: paths already write, so it cannot drift from it and no existing call
    #: site had to change. Backfilled for historical rows by migration 0065.
    #:
    #: Indexed via Meta.indexes rather than `db_index=True` so the index
    #: can be built CONCURRENTLY (migration 0065) and can be PARTIAL — the
    #: overwhelming majority of ledger rows are consumption, which has no
    #: payment behind it, so indexing only the non-null rows keeps it a
    #: fraction of the size of a full one.
    stripe_payment_intent_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        help_text=(
            "Stripe PaymentIntent this entry is attributable to, when the "
            "entry came from a payment. Used to attribute a refund or "
            "chargeback back to the exact credits it bought."
        ),
    )
    created_at = models.DateTimeField(
        auto_now_add=True, help_text="Date and time when the credit ledger was created"
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # "What did this payment buy, wherever it landed" — the lookup
            # every refund and chargeback reversal starts from. Partial:
            # only rows that came from a payment carry the column, and a
            # query always supplies a concrete id, so the planner can use
            # it.
            models.Index(
                fields=["stripe_payment_intent_id"],
                name="bill_ledger_pi_idx",
                condition=models.Q(stripe_payment_intent_id__isnull=False),
            ),
            # The list endpoint is `filter(user_id=...)` + `-created_at`,
            # and the table only had a standalone user_id index. Measured
            # with EXPLAIN ANALYZE: the plan was a Bitmap Heap Scan on
            # user_id feeding a top-N heapsort, i.e. it read EVERY row the
            # user owns to return a page of 20. Cost grows linearly with a
            # user's history, on the fastest-growing table in the schema.
            models.Index(
                fields=["user_id", "-created_at"],
                name="bill_ledger_user_t_idx",
            ),
        ]

    @classmethod
    def build(cls, *, user=None, **kwargs):
        """
        An UNSAVED ledger row with the user's identity captured as values.

        Use for `bulk_create`. Taking the user object rather than raw ids
        keeps the id and the email snapshot from drifting apart — the
        email is the only attribution that survives account deletion, and
        it is easy to forget when setting `user_id` by hand.
        """
        if user is not None:
            kwargs["user_id"] = user.id
            kwargs["user_email"] = user.email
        # Promote the payment attribution out of `metadata` rather than
        # asking every call site to pass it twice. Derived, never
        # overridden: if a caller sets the column explicitly that wins, but
        # otherwise the column and the JSON can never disagree.
        if not kwargs.get("stripe_payment_intent_id"):
            payment_intent_id = (kwargs.get("metadata") or {}).get(
                "stripe_payment_intent_id"
            )
            if payment_intent_id:
                kwargs["stripe_payment_intent_id"] = str(payment_intent_id)
        return cls(**kwargs)

    @classmethod
    def record(cls, *, user=None, **kwargs):
        """Create and save a ledger row. The append-only replacement for
        `CreditLedger.objects.create(user=...)`."""
        row = cls.build(user=user, **kwargs)
        row.save()
        return row


class CreditUsageLog(AppendOnlyModel):
    """
    Detailed record of specific credit consumption events. This model
    tracks the exact bucket used for a transaction, enabling precise
    deduction logic and providing a historical log of how and when
    credits from a particular source were spent.

    Logs every instance of credit consumption from a user's wallet.
    """

    #: The refund flow (SubscriptionService.refund_credits) settles a log by
    #: flipping this flag, and reporting across dashboard/, classrooms/ and
    #: billing/ filters on it. Freezing it would mean redesigning refunds as
    #: reversing rows. The financial substance of the row stays frozen.
    mutable_fields = frozenset({"is_refunded"})

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_id = models.UUIDField(
        null=True,
        blank=True,
        db_index=True,
        help_text=(
            "ID of the billed user, stored as a plain value. Previously "
            "reachable only by joining through `wallet`, which meant the "
            "log died with the wallet and the account."
        ),
    )
    user_email = models.CharField(
        max_length=254,
        null=True,
        blank=True,
        help_text=(
            "The billed user's email captured at write time — a snapshot, "
            "deliberately not derived by joining to the live user row."
        ),
    )
    wallet = models.ForeignKey(
        CreditWallet,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="credit_usage_logs",
        help_text=(
            "Credit wallet the usage log is associated with. DO_NOTHING "
            "with no database constraint so deleting a wallet (or the user "
            "above it) can no longer cascade this audit row away. The "
            "relation is kept — rather than reduced to a bare id — because "
            "reporting joins through it; use `user_id` when the row may be "
            "orphaned, since a join drops orphans silently."
        ),
    )
    bucket = models.ForeignKey(
        CreditBucket,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="credit_usage_logs",
        help_text="Credit bucket the usage log is associated with",
    )
    course = models.ForeignKey(
        "classrooms.Course",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="credit_usage_logs",
        help_text=(
            "Course the consuming task was performed under, when known. "
            "Null for tasks with no course context (e.g. custom AI chat, "
            "school-wide summaries) or for usage logged before this field "
            "existed."
        ),
    )
    school = models.ForeignKey(
        "classrooms.School",
        null=True,
        blank=True,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        related_name="credit_usage_logs",
        help_text=(
            "The school the billed user (teacher or school admin) belonged "
            "to at the moment these credits were consumed — a snapshot, "
            "deliberately NOT derived by joining to the user's current "
            "`school` FK. That field is mutable (a teacher can be "
            "reassigned to a different school after the fact), so joining "
            "live would retroactively misattribute historical usage to "
            "whichever school the user happens to belong to today. Null "
            "if the user had no school at consumption time (e.g. an "
            "individual, non-license teacher), or for usage logged before "
            "this field existed."
        ),
    )
    amount = models.IntegerField(help_text="Amount of credits consumed from the bucket")
    feature = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        help_text="Feature or service for which credits are being consumed",
        db_index=True,
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
        indexes = [
            models.Index(fields=["task_id"], name="billing_usagelog_task_idx"),
            # Same shape as CreditLedger: the endpoint filters on the
            # owner and orders by -created_at, and without a composite the
            # planner reads the user's whole history to return one page.
            # `wallet_id` is indexed here as well as `user_id` because the
            # viewset scopes via `wallet__user`.
            models.Index(
                fields=["wallet", "-created_at"],
                name="bill_usagelog_wallet_t_idx",
            ),
            models.Index(
                fields=["user_id", "-created_at"],
                name="bill_usagelog_user_t_idx",
            ),
        ]

    @classmethod
    def build(cls, *, wallet=None, user=None, **kwargs):
        """
        An UNSAVED usage log with the billed user's identity captured as
        values. `user` defaults to the wallet's owner, which is who is
        billed in every current call path; pass it explicitly only when
        those differ.
        """
        if wallet is not None:
            kwargs["wallet"] = wallet
            if user is None:
                user = wallet.user
        if user is not None:
            kwargs["user_id"] = user.id
            kwargs["user_email"] = user.email
        return cls(**kwargs)

    @classmethod
    def record(cls, *, wallet=None, user=None, **kwargs):
        """Create and save a usage log with identity captured."""
        row = cls.build(wallet=wallet, user=user, **kwargs)
        row.save()
        return row


register_append_only_guards(CreditLedger, CreditUsageLog)


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
            "license in the current MONTHLY consumption window (see "
            "consumption_window_start). Measured against "
            "max_seats * plan.monthly_credits, which is a monthly figure — "
            "so this must be reset monthly, NOT once per contract."
        ),
    )

    consumption_window_start = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Start of the monthly window total_credits_consumed is counting. "
            "Exists so the reset is idempotent and self-serializing: the "
            "monthly refresh runs once per TEACHER, so a naive reset would "
            "fire N times a month and discard consumption recorded between "
            "teachers refreshed on different days. Null means never reset "
            "(pre-migration rows); it is backfilled to billing_cycle_start."
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
        """
        Returns number of active TEACHERS enrolled under this license.
        Excludes the admin's own analytics allocation
        (is_admin_allocation=True) - The admin is not a teacher and does not
        occupy a paid seat
        """
        return self.allocations.filter(
            is_active=True, is_admin_allocation=False
        ).count()

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
        max_length=40, choices=LicenseBillingRecordType.choices
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


class LicenseOveragePurchaseStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    COMPLETED = "COMPLETED", _("Completed")
    FAILED = "FAILED", _("Failed")


class LicenseOveragePurchaseIntent(models.Model):
    """
    Tracks a school-admin-initiated overage purchase from the moment a
    Stripe Checkout Session is created until it's fulfilled (or fails).

    This exists so that:
      1. The allocation map (which can be arbitrarily large) never has to
         be crammed into Stripe metadata (500-char/value limit) — only
         this row's UUID goes into metadata.
      2. Price is snapshotted at purchase-initiation time, so a plan price
         change between checkout creation and webhook fulfillment can't
         cause the wrong amount of credit to be granted for what was paid.
      3. There's a durable, queryable record for manual reconciliation if
         a payment succeeds but fulfillment can't fully complete (e.g. a
         teacher was removed from the license mid-flight).

    NOT used for the superadmin offline-grant path — that path never
    touches Stripe and grants immediately, so there's nothing pending to
    track.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    license_subscription = models.ForeignKey(
        LicenseSubscription,
        on_delete=models.CASCADE,
        related_name="overage_purchase_intents",
    )
    initiated_by = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.PROTECT,
        related_name="license_overage_purchase_intents",
    )

    total_blocks = models.PositiveIntegerField()
    allocations = models.JSONField(
        help_text="Mapping of teacher user UUID (string) -> blocks purchased."
    )

    # Snapshots taken at creation time — see class docstring point 2.
    block_size_snapshot = models.PositiveIntegerField(
        help_text="plan.overage_block_size (raw credits per block) at purchase time."
    )
    unit_price_cents_snapshot = models.PositiveIntegerField(
        help_text="plan.overage_block_price (cents per block) at purchase time."
    )
    amount_cents = models.PositiveIntegerField(
        help_text="total_blocks * unit_price_cents_snapshot."
    )

    status = models.CharField(
        max_length=20,
        choices=LicenseOveragePurchaseStatus.choices,
        default=LicenseOveragePurchaseStatus.PENDING,
    )
    stripe_checkout_session_id = models.CharField(
        max_length=255, null=True, blank=True, db_index=True, unique=True
    )
    stripe_payment_intent_id = models.CharField(
        max_length=255, null=True, blank=True, db_index=True
    )
    failure_reason = models.TextField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["license_subscription", "status"]),
        ]


class LicenseOverageOfflineRequestStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    APPROVED = "APPROVED", _("Approved")
    REJECTED = "REJECTED", _("Rejected")


class LicenseOverageOfflineRequest(models.Model):
    """
    Tracks a school-admin-initiated request to purchase overage blocks
    paid for OUTSIDE Stripe (bank transfer, invoice, cash, etc.) —
    parallel to LicenseOveragePurchaseIntent (the Stripe Checkout path)
    and _grant_overage_offline (the superadmin comp-grant path), but
    distinct from both:

      - Unlike the Stripe intent, there's no payment processor to
        confirm against, so a human superadmin must explicitly review
        and approve/reject.
      - Unlike the superadmin comp-grant, this always starts PENDING —
        nothing is ever granted at creation time, and it's the SCHOOL
        that pays (off-platform), not a free administrative grant.

    Kept as its own model rather than folded into LicenseOveragePurchaseIntent
    so PENDING/status semantics never become ambiguous between "awaiting
    a Stripe webhook" and "awaiting human review", and so Stripe-only
    fields don't accumulate as always-null columns here.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    license_subscription = models.ForeignKey(
        LicenseSubscription,
        on_delete=models.CASCADE,
        related_name="overage_offline_requests",
    )
    requested_by = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.PROTECT,
        related_name="license_overage_offline_requests",
    )

    total_blocks = models.PositiveIntegerField()
    allocations = models.JSONField(
        help_text="Mapping of teacher user UUID (string) -> blocks requested."
    )

    # Snapshots at request time — same rationale as
    # LicenseOveragePurchaseIntent: what the school admin was quoted must
    # not drift if the plan's pricing changes before a superadmin reviews.
    block_size_snapshot = models.PositiveIntegerField(
        help_text="plan.overage_block_size (raw credits per block) at request time."
    )
    unit_price_cents_snapshot = models.PositiveIntegerField(
        help_text="plan.overage_block_price (cents per block) at request time."
    )
    amount_cents_quoted = models.PositiveIntegerField(
        help_text="total_blocks * unit_price_cents_snapshot at request time."
    )

    status = models.CharField(
        max_length=20,
        choices=LicenseOverageOfflineRequestStatus.choices,
        default=LicenseOverageOfflineRequestStatus.PENDING,
    )

    # Populated on APPROVE — the superadmin's real attestation of what
    # was actually confirmed received, independent of amount_cents_quoted
    # (a discount may have been negotiated off-platform).
    amount_confirmed_cents = models.PositiveIntegerField(null=True, blank=True)
    payment_reference = models.CharField(max_length=250, null=True, blank=True)
    payment_method_label = models.CharField(max_length=100, null=True, blank=True)

    # Partial-fulfillment bookkeeping — mirrors the Stripe webhook's
    # fulfilled/skipped split for teachers no longer active by review time.
    fulfilled_allocations = models.JSONField(null=True, blank=True)
    skipped_allocations = models.JSONField(null=True, blank=True)

    # Populated on REJECT.
    rejection_reason = models.TextField(null=True, blank=True)

    reviewed_by = models.ForeignKey(
        "users.CustomUser",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="license_overage_offline_reviews",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    license_billing_record = models.ForeignKey(
        LicenseBillingRecord,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="overage_offline_requests",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["license_subscription", "status"]),
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

    is_admin_allocation = models.BooleanField(
        default=False,
        help_text=(
            "True if this allocation belongs to the license's admin_user "
            "(a fixed analytics-only credit grant for the school admin's "
            "dashboard), not to an enrolled teacher. Excluded from "
            "teacher_count/seats_remaining/active_teacher_count, from the "
            "monthly_allocation overwrite on plan changes, and from "
            "LicenseSubscription.total_credits_consumed."
        ),
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


class StripeEventStatus(models.TextChoices):
    """
    Lifecycle of one Stripe webhook delivery in the idempotency ledger.

    PROCESSING with a fresh `claimed_at` IS the claim: a second, concurrent
    redelivery of the same event sees it and backs off with 409 rather than
    reporting success for work that has not finished yet.

    SUCCEEDED is the ONLY state that suppresses a redelivery — that is the
    whole safety property. FAILED is deliberately re-claimable so Stripe's
    own retry (or a manual replay) does the work.

    See billing/webhooks.py for the state machine.
    """

    PROCESSING = "PROCESSING", _("Processing")
    SUCCEEDED = "SUCCEEDED", _("Succeeded")
    FAILED = "FAILED", _("Failed")


class StripeEvent(models.Model):
    """Idempotency ledger for Stripe webhook events - see billing/webhooks.py"""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    stripe_event_id = models.CharField(max_length=255, unique=True, db_index=True)
    event_type = models.CharField(max_length=100)
    payload = models.JSONField(null=True, blank=True)
    processed_at = models.DateTimeField(
        auto_now_add=True,
        help_text=_(
            "When this event was FIRST seen. Write-once (auto_now_add) — it is "
            "NOT the completion time; see completed_at for that. Kept under "
            "this name because Meta.ordering and the "
            "backfill_billing_transactions command both read it."
        ),
    )
    status = models.CharField(
        max_length=20,
        choices=StripeEventStatus.choices,
        default=StripeEventStatus.PROCESSING,
        db_index=True,
        help_text=_(
            "Processing claim state. Only SUCCEEDED suppresses a redelivery — "
            "see StripeEventStatus."
        ),
    )
    claimed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_(
            "When the current (or most recent) processing claim was acquired. "
            "Doubles as the fencing token for the terminal write, and is what "
            "identifies a claim abandoned by a killed worker."
        ),
    )
    handler_started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_(
            "When a worker actually began running the handler for the "
            "current claim — NOT when the claim was taken. The gap between "
            "the two is the whole point: a claim with no start means the "
            "worker died before touching anything and the event can be "
            "safely re-dispatched, while a claim WITH a start may have got "
            "part-way through irreversible Stripe calls and must not be "
            "replayed automatically."
        ),
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When processing reached a terminal state (SUCCEEDED/FAILED)."),
    )
    attempts = models.PositiveIntegerField(
        default=0,
        help_text=_("How many times a worker has claimed this event."),
    )
    recovery_attempts = models.PositiveIntegerField(
        default=0,
        help_text=_(
            "How many times the sweeper has re-dispatched this event after "
            "a worker abandoned its claim. Capped, so a task that dies the "
            "same way every time stops being retried and starts being "
            "reported."
        ),
    )
    last_error = models.TextField(
        blank=True,
        default="",
        help_text=_("Truncated exception text from the most recent failed attempt."),
    )

    class Meta:
        ordering = ["-processed_at"]
        indexes = [
            # The sweeper's two scans: stale-PROCESSING reclaim, and the
            # FAILED report split by Stripe's retry window.
            models.Index(fields=["status", "claimed_at"]),
            models.Index(fields=["status", "completed_at"]),
        ]

    def __str__(self):
        return f"{self.stripe_event_id} ({self.event_type}) - {self.status}"


class BillingTransactionSource(models.TextChoices):
    INDIVIDUAL = "INDIVIDUAL", _("Individual")
    LICENSE = "LICENSE", _("License")


class BillingTransactionType(models.TextChoices):
    INDIVIDUAL_SUBSCRIPTION_CHARGE = "INDIVIDUAL_SUBSCRIPTION_CHARGE", _(
        "Individual Subscription Charge"
    )
    INDIVIDUAL_TRIAL_CONVERSION_CHARGE = "INDIVIDUAL_TRIAL_CONVERSION_CHARGE", _(
        "Trial Conversion Charge"
    )
    INDIVIDUAL_UPGRADE_CHARGE = "INDIVIDUAL_UPGRADE_CHARGE", _(
        "Individual Upgrade Charge"
    )
    INDIVIDUAL_OVERAGE_PURCHASE = "INDIVIDUAL_OVERAGE_PURCHASE", _(
        "Individual Overage Purchase"
    )
    LICENSE_INITIAL_CHARGE = "LICENSE_INITIAL_CHARGE", _("License Initial Charge")
    LICENSE_SUBSCRIPTION_CHARGE = "LICENSE_SUBSCRIPTION_CHARGE", _(
        "License Renewal Charge"
    )
    LICENSE_PLAN_CHANGE_CHARGE = "LICENSE_PLAN_CHANGE_CHARGE", _(
        "License Plan Change Charge"
    )
    LICENSE_SEAT_CHANGE_CHARGE = "LICENSE_SEAT_CHANGE_CHARGE", _(
        "License Seat Increase Charge"
    )
    LICENSE_OVERAGE_PURCHASE = "LICENSE_OVERAGE_PURCHASE", _("License Overage Purchase")
    LICENSE_OFFLINE_RENEWAL = "LICENSE_OFFLINE_RENEWAL", _("License Offline Renewal")
    LICENSE_OFFLINE_PLAN_CHANGE = "LICENSE_OFFLINE_PLAN_CHANGE", _(
        "License Offline Plan Change"
    )
    LICENSE_OFFLINE_MANUAL_OVERAGE_GRANT = "LICENSE_OFFLINE_MANUAL_OVERAGE_GRANT", _(
        "Manual Overage Grant"
    )
    LICENSE_OFFLINE_OVERAGE_PURCHASE = "LICENSE_OFFLINE_OVERAGE_PURCHASE", _(
        "License Overage Purchase (Offline)"
    )
    OTHER = "OTHER", _("Other")


class BillingTransactionStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    PAID = "PAID", _("Paid")
    FAILED = "FAILED", _("Failed")
    REFUNDED = "REFUNDED", _("Refunded")
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED", _("Partially Refunded")
    VOIDED = "VOIDED", _("Voided")
    MANUAL = "MANUAL", _("Recorded Manually (Offline)")
    # A chargeback is open against this payment. The money has ALREADY left
    # our balance (Stripe debits at dispute creation, not at closure), but
    # the outcome is undecided, so entitlement is deliberately untouched.
    DISPUTED = "DISPUTED", _("Disputed (chargeback open)")
    # The chargeback was upheld. The money is gone for good and the credits
    # it bought have been reversed.
    DISPUTE_LOST = "DISPUTE_LOST", _("Dispute lost (charged back)")


class BillingTransactionMethod(models.TextChoices):
    STRIPE = "STRIPE", _("Stripe")
    OFFLINE = "OFFLINE", _("Offline")


class BillingTransaction(models.Model):
    """
    Unified, money-only ledger across BOTH the INDIVIDUAL and LICENSE
    billing tracks — subscription charges, upgrades, overage purchases,
    refunds, and offline (manually recorded) license events. Backs the
    single GET /billing/invoices/ endpoint.

    Distinct from CreditLedger (credit movements, not money) and from
    LicenseBillingRecord (offline-license-only accounting notes, no
    Stripe-billed coverage). This table is the superset "what did money
    do" view across both.

    Rows are upserted — never duplicated — via
    BillingTransactionService.record(), keyed on whichever identifier is
    most specific: stripe_invoice_id, else stripe_payment_intent_id, else
    license_billing_record. See the unique constraints below; they are
    the DB-level backstop for that same idempotency guarantee.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    source = models.CharField(max_length=20, choices=BillingTransactionSource.choices)
    transaction_type = models.CharField(
        max_length=50, choices=BillingTransactionType.choices
    )
    status = models.CharField(
        max_length=20,
        choices=BillingTransactionStatus.choices,
        default=BillingTransactionStatus.PENDING,
    )
    billing_method = models.CharField(
        max_length=20,
        choices=BillingTransactionMethod.choices,
        default=BillingTransactionMethod.STRIPE,
    )

    # --- Ownership / linkage ---
    user = models.ForeignKey(
        "users.CustomUser",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="billing_transactions",
        help_text="For INDIVIDUAL transactions, the subscriber. For "
        "LICENSE transactions, the admin who initiated the action (if "
        "any) — the school is the real owner, tracked via `school`.",
    )
    user_subscription = models.ForeignKey(
        UserSubscription,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="billing_transactions",
    )
    license_subscription = models.ForeignKey(
        LicenseSubscription,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="billing_transactions",
    )
    school = models.ForeignKey(
        "classrooms.School",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="billing_transactions",
        help_text="Denormalized from license_subscription at write time "
        "so history survives the license row changing, and permission "
        "checks don't need an extra join.",
    )

    # --- Money ---
    amount_cents = models.IntegerField(
        default=0, help_text="Gross amount charged/recorded, in cents."
    )
    refunded_amount_cents = models.IntegerField(default=0)
    currency = models.CharField(max_length=10, default="usd")

    # --- Stripe references ---
    stripe_invoice_id = models.CharField(
        max_length=255, null=True, blank=True, db_index=True
    )
    stripe_payment_intent_id = models.CharField(
        max_length=255, null=True, blank=True, db_index=True
    )
    stripe_checkout_session_id = models.CharField(
        max_length=255, null=True, blank=True, db_index=True
    )
    stripe_charge_id = models.CharField(
        max_length=255, null=True, blank=True, db_index=True
    )
    stripe_subscription_id = models.CharField(
        max_length=255, null=True, blank=True, db_index=True
    )
    receipt_url = models.URLField(
        max_length=500,
        null=True,
        blank=True,
        help_text="Stripe-hosted receipt/invoice page for this purchase.",
    )

    # --- Offline reference ---
    license_billing_record = models.ForeignKey(
        LicenseBillingRecord,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="billing_transactions",
        help_text="Set for OFFLINE license events — links back to the "
        "detailed LicenseBillingRecord (payment reference, notes, etc).",
    )

    description = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(null=True, blank=True)

    performed_by = models.ForeignKey(
        "users.CustomUser",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="performed_billing_transactions",
        help_text="The admin/superadmin who performed this action, for "
        "manual/offline events. Null for customer- or webhook-driven ones.",
    )

    occurred_at = models.DateTimeField(
        db_index=True,
        help_text="When the underlying billing event actually happened "
        "(may predate created_at for backfilled rows).",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["user", "occurred_at"]),
            models.Index(fields=["license_subscription", "occurred_at"]),
            models.Index(fields=["school", "occurred_at"]),
            models.Index(fields=["source", "transaction_type"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["stripe_invoice_id"],
                condition=models.Q(stripe_invoice_id__isnull=False),
                name="uniq_billing_txn_stripe_invoice",
            ),
            models.UniqueConstraint(
                fields=["stripe_payment_intent_id"],
                condition=models.Q(stripe_payment_intent_id__isnull=False),
                name="uniq_billing_txn_payment_intent",
            ),
            models.UniqueConstraint(
                fields=["license_billing_record"],
                condition=models.Q(license_billing_record__isnull=False),
                name="uniq_billing_txn_license_billing_record",
            ),
        ]

    def __str__(self):
        return f"{self.transaction_type} — {self.amount_cents / 100:.2f} {self.currency.upper()} ({self.status})"

    @property
    def display_amount(self):
        return round(self.amount_cents / 100, 2)

    @property
    def display_refunded_amount(self):
        return round(self.refunded_amount_cents / 100, 2)


class DisputeStatus(models.TextChoices):
    """
    Stripe's own dispute statuses, verified against the live test API on
    2026-09-08 by driving a real dispute through its whole lifecycle.

    The `warning_*` values are an INQUIRY — a pre-dispute question from the
    issuer. No money moves for an inquiry, which is why nothing in the
    entitlement logic reacts to them.
    """

    WARNING_NEEDS_RESPONSE = "warning_needs_response", _("Inquiry: needs response")
    WARNING_UNDER_REVIEW = "warning_under_review", _("Inquiry: under review")
    WARNING_CLOSED = "warning_closed", _("Inquiry: closed")
    NEEDS_RESPONSE = "needs_response", _("Chargeback: needs response")
    UNDER_REVIEW = "under_review", _("Chargeback: under review")
    WON = "won", _("Won")
    LOST = "lost", _("Lost")


#: How far through the lifecycle each status is. Used to reject stale,
#: out-of-order deliveries: Stripe does NOT guarantee event ordering, so a
#: delayed `charge.dispute.created` can arrive after `charge.dispute.closed`
#: and must not drag a settled dispute back to needs_response.
DISPUTE_STATUS_RANK = {
    DisputeStatus.WARNING_NEEDS_RESPONSE: 0,
    DisputeStatus.WARNING_UNDER_REVIEW: 1,
    DisputeStatus.WARNING_CLOSED: 2,
    DisputeStatus.NEEDS_RESPONSE: 3,
    DisputeStatus.UNDER_REVIEW: 4,
    DisputeStatus.LOST: 5,
    DisputeStatus.WON: 6,
}


class PaymentDispute(models.Model):
    """
    One Stripe dispute (chargeback or inquiry), mirrored locally.

    WHY THIS EXISTS: nothing in billing reacted to disputes at all. A
    chargeback silently removed the money and the dispute fee from the
    Stripe balance while the local BillingTransaction still read PAID and
    the customer kept the credits the payment bought. The evidence deadline
    would pass unanswered, losing by default.

    Keyed on `stripe_dispute_id`, which is what makes the handlers safe
    against duplicate delivery: every dispute webhook carries the FULL
    Dispute object with its current status, so each delivery is an
    idempotent "set the state to this" rather than a step in a sequence
    that must arrive in order.

    Measured against real Stripe (test mode, 2026-09-08) — the amounts here
    are separate on purpose:
        dispute created : balance -2499, fee 1500  (net -3999)
        dispute won     : balance +2499, fee    0  (the fee is NOT returned)
    So winning still costs the fee, and `amount_cents` alone would overstate
    what we get back.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    stripe_dispute_id = models.CharField(max_length=255, unique=True, db_index=True)
    stripe_charge_id = models.CharField(max_length=255, blank=True, default="")
    stripe_payment_intent_id = models.CharField(
        max_length=255, blank=True, default="", db_index=True
    )

    # Nullable: a dispute can arrive for a payment we never recorded (an
    # invoice paid before this app owned the account, say). That is exactly
    # the case that must NOT be dropped — it becomes a row flagged for a
    # human instead.
    billing_transaction = models.ForeignKey(
        "BillingTransaction",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="disputes",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="payment_disputes",
    )

    status = models.CharField(
        max_length=32, choices=DisputeStatus.choices, db_index=True
    )
    reason = models.CharField(max_length=64, blank=True, default="")
    amount_cents = models.IntegerField(default=0)
    fee_cents = models.IntegerField(
        default=0, help_text="Stripe's dispute fee. Not returned even on a win."
    )
    currency = models.CharField(max_length=10, default="usd")

    evidence_due_by = models.DateTimeField(null=True, blank=True)
    funds_withdrawn_at = models.DateTimeField(null=True, blank=True)
    funds_reinstated_at = models.DateTimeField(null=True, blank=True)

    # Credit reversal, recorded once and only once.
    credits_reversed = models.BooleanField(default=False)
    credits_reversed_amount = models.PositiveIntegerField(default=0)
    credits_deficit_amount = models.PositiveIntegerField(
        default=0,
        help_text="Portion of the reversal that was already spent and could not be reclaimed.",
    )
    #: WHOSE deficit, as {wallet_id: credits}.
    #:
    #: A map rather than a single number because one school overage
    #: payment spreads across several teachers, so a chargeback on it can
    #: leave several of them blocked. A late win then has to lift the
    #: block from each — and `billing_transaction.user` is the wrong
    #: answer there, being one teacher at best and null for a license
    #: payment. Without this, a won chargeback would leave teachers
    #: permanently unable to spend.
    deficit_by_wallet = models.JSONField(default=dict, blank=True)

    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    raw_payload = models.JSONField(
        null=True, blank=True, help_text="Last Dispute object received, for audit."
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self) -> str:
        return f"{self.stripe_dispute_id} ({self.status})"

    @property
    def is_inquiry(self) -> bool:
        """Inquiries move no money and must not touch entitlement."""
        return str(self.status).startswith("warning")

    @property
    def total_loss_cents(self) -> int:
        """What a lost dispute actually costs: the amount plus the fee."""
        return self.amount_cents + self.fee_cents


class PaymentRefund(models.Model):
    """
    The credit-side consequence of refunding a payment, tracked per
    PaymentIntent.

    WHY THIS EXISTS: `charge.refunded` recorded the money and stopped
    there. Measured on a real handler run — a fully refunded 500-credit
    overage purchase left the BillingTransaction REFUNDED and all 500
    credits still spendable. Money state and entitlement state diverged
    silently, which is exactly the thing that must never happen.

    KEYED ON THE PAYMENT INTENT, NOT THE EVENT. Stripe's
    `amount_refunded` is CUMULATIVE across every refund on the charge, so
    the natural formulation is a target ("this payment is now 60%
    refunded, so 60% of what it bought should be gone") rather than a
    delta. That makes duplicate delivery a no-op and multiple partial
    refunds compose correctly, with no separate idempotency ledger.

    Both stored amounts are MONOTONIC — a delivery carrying a smaller
    `amount_refunded` than one already seen is a stale redelivery arriving
    out of order, and must never un-reverse credits already taken back.

    ONE CHARGE PER PAYMENT INTENT is assumed, which holds for every flow
    that reaches here: a PaymentIntent has at most one succeeded charge,
    and only a succeeded charge can be refunded. A second charge id on the
    same intent is logged for manual review rather than silently folded
    into the totals.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    stripe_payment_intent_id = models.CharField(
        max_length=255, unique=True, db_index=True
    )
    stripe_charge_id = models.CharField(max_length=255, blank=True, default="")

    billing_transaction = models.ForeignKey(
        "BillingTransaction",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="refunds",
    )

    amount_captured_cents = models.PositiveIntegerField(default=0)
    amount_refunded_cents = models.PositiveIntegerField(
        default=0, help_text="Cumulative across all refunds on the charge. Monotonic."
    )
    currency = models.CharField(max_length=10, default="usd")

    credits_granted = models.PositiveIntegerField(
        default=0,
        help_text="Credits this payment bought, summed from its grant ledger rows.",
    )
    credits_reversed = models.PositiveIntegerField(
        default=0, help_text="Cumulative credits reclaimed for this payment."
    )
    credits_deficit = models.PositiveIntegerField(
        default=0,
        help_text=(
            "Portion of the reversal that was already spent and so could "
            "not be reclaimed. Recorded as debt; usage history is never "
            "rewritten."
        ),
    )

    #: Wallets whose overage block allowance has already been given back,
    #: as {wallet_id: blocks}. A map rather than a counter because one
    #: school payment grants blocks to several teachers, and a later
    #: partial refund must not return the same teacher's block twice.
    blocks_restored_by_wallet = models.JSONField(default=dict, blank=True)

    #: Set when the refund was issued as a deliberate goodwill gesture and
    #: the customer is meant to KEEP what they bought — see
    #: billing/payment_refunds.py for how an operator asks for this.
    retain_credits = models.BooleanField(
        default=False,
        help_text=(
            "Deliberate business decision to absorb the loss and let the "
            "customer keep the credits. Set from Stripe refund metadata."
        ),
    )
    notes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["-created_at"])]

    def __str__(self) -> str:
        return (
            f"{self.stripe_payment_intent_id} "
            f"({self.amount_refunded_cents}/{self.amount_captured_cents} refunded)"
        )

    @property
    def is_fully_refunded(self) -> bool:
        return (
            self.amount_captured_cents > 0
            and self.amount_refunded_cents >= self.amount_captured_cents
        )

    @property
    def refunded_fraction(self) -> float:
        if not self.amount_captured_cents:
            return 0.0
        return min(1.0, self.amount_refunded_cents / self.amount_captured_cents)

    @property
    def target_credits_reversed(self) -> int:
        """
        How many credits SHOULD be gone given the money returned so far.

        Floored, so rounding always favours the customer: a 50% refund of a
        501-credit grant reclaims 250, not 251.
        """
        if self.amount_captured_cents <= 0 or self.credits_granted <= 0:
            return 0
        if self.amount_refunded_cents >= self.amount_captured_cents:
            return self.credits_granted
        return (
            self.credits_granted * self.amount_refunded_cents
        ) // self.amount_captured_cents


class LiveQARunKind(models.TextChoices):
    SCENARIO = "SCENARIO", _("Scenario")
    CHAOS = "CHAOS", _("Chaos")


class LiveQARunStatus(models.TextChoices):
    PENDING = "PENDING", _("Pending")
    RUNNING = "RUNNING", _("Running")
    PASSED = "PASSED", _("Passed")
    FAILED = "FAILED", _("Failed")
    # The scheduled run fired but deliberately did no work — this worker is
    # not a QA worker (ENABLE_STRIPE_LIVE_QA off, or live Stripe keys).
    # Recorded rather than silent: "nothing ran because it is switched off"
    # and "nothing ran because something is broken" look identical from the
    # outside, and telling them apart is the whole point of this row.
    SKIPPED = "SKIPPED", _("Skipped (not enabled here)")
    # Fired, was supposed to run, and could not — a misconfigured QA
    # environment. Distinct from FAILED, which means Stripe's real
    # behaviour disagreed with the billing code.
    ERROR = "ERROR", _("Could not run (misconfigured)")


class LiveQARun(models.Model):
    """
    One run of the real-Stripe QA suite — triggered from the internal QA
    web console (billing/qa_console.py) OR by the nightly
    `nightly_stripe_live_qa` Beat task, persisted here because the
    Celery result backend is Redis with a 1-hour expiry
    (CELERY_RESULT_EXPIRES) — this is the durable record of what ran,
    with what parameters, and what it found.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    kind = models.CharField(max_length=20, choices=LiveQARunKind.choices)
    status = models.CharField(
        max_length=20,
        choices=LiveQARunStatus.choices,
        default=LiveQARunStatus.PENDING,
    )
    celery_task_id = models.CharField(max_length=255, blank=True, default="")

    # Fields used when kind is SCENARIO
    scenario_names = models.JSONField(default=list, blank=True)
    tier = models.CharField(max_length=10, blank=True, default="")

    # Fields used when kind is CHAOS
    seed = models.IntegerField(null=True, blank=True)
    steps = models.IntegerField(null=True, blank=True)
    shrink = models.BooleanField(default=False)

    summary = models.TextField(blank=True, default="")
    result_data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Serialized SuiteResult / ChaosWalkResult / ShrinkResult.",
    )

    triggered_by = models.ForeignKey(
        "users.CustomUser",
        on_delete=models.SET_NULL,
        null=True,
        related_name="live_qa_runs",
    )

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.kind} run {self.id} ({self.status})"


# ----------------------------------------------------------------------
# Nightly Stripe price reconciliation
# ----------------------------------------------------------------------


class PriceReconciliationStatus(models.TextChoices):
    """
    The outcome of checking ONE local price expectation against the Stripe
    Price it points at.

    Deliberately more than a boolean. Collapsing these into "drift" was the
    specific failure to avoid: a Stripe outage and a changed price produce
    identical symptoms at the call site (we could not confirm the amount)
    but demand opposite responses — one is a billing incident, the other is
    a network blip that will clear on its own. An alert that cannot tell
    them apart is one nobody trusts at 3am.
    """

    MATCHED = "MATCHED", _("Matched")
    #: Local configuration disagreed with Stripe and was UPDATED to match.
    #: The normal outcome of a price change, not a fault — Stripe is the
    #: source of truth, so the application following it is the system
    #: working. Distinguished from MATCHED so the audit trail can answer
    #: "when did this price change, and from what?"
    SYNCHRONIZED = "SYNCHRONIZED", _("Synchronized from Stripe")
    #: Disagreement in something that CANNOT be synchronized because it
    #: would change how customers are billed rather than what they are
    #: charged — a billing interval, say. Reported for a human.
    DRIFT_DETECTED = "DRIFT_DETECTED", _("Drift detected")
    MISSING_PRICE_ID = "MISSING_PRICE_ID", _("Missing price id")
    INVALID_PRICE = "INVALID_PRICE", _("Invalid price")
    INACTIVE_PRICE = "INACTIVE_PRICE", _("Inactive price")
    ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH", _("Stripe account mismatch")
    STRIPE_UNAVAILABLE = "STRIPE_UNAVAILABLE", _("Stripe unavailable")
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR", _("Configuration error")
    #: The plan legitimately has no price of this kind — a free trial, an
    #: internal plan, a beta tier that charges only for overage. Recorded
    #: rather than skipped silently, so the run can show it was considered.
    NOT_APPLICABLE = "NOT_APPLICABLE", _("Not applicable")


#: Outcomes that mean the BILLING CONFIGURATION is wrong and a human needs
#: to look. Deliberately excludes STRIPE_UNAVAILABLE — see the docstring
#: above.
PRICE_RECONCILIATION_ALERT_STATUSES = frozenset(
    {
        PriceReconciliationStatus.DRIFT_DETECTED,
        PriceReconciliationStatus.MISSING_PRICE_ID,
        PriceReconciliationStatus.INVALID_PRICE,
        PriceReconciliationStatus.INACTIVE_PRICE,
        PriceReconciliationStatus.ACCOUNT_MISMATCH,
        PriceReconciliationStatus.CONFIGURATION_ERROR,
    }
)


class PriceKind(models.TextChoices):
    """
    Which of a plan's two billing surfaces a result is about.

    A plan carries both on one row — `stripe_price_id` for the recurring
    subscription and `stripe_overage_price_id` for one-time credit blocks.
    They are separate Stripe Prices and drift independently, which is why
    every result names which one it checked.
    """

    BASE = "BASE", _("Base subscription price")
    OVERAGE = "OVERAGE", _("Overage block price")


class PriceReconciliationRun(models.Model):
    """One nightly sweep. The parent of its per-price results."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    #: The Stripe account these credentials actually address, read back
    #: from Stripe rather than assumed. Account drift between environments
    #: was a real finding in the billing audit, so every run records which
    #: account it was talking to.
    stripe_account_id = models.CharField(max_length=255, blank=True, default="")
    #: Whatever API version the application itself uses. Never pinned by
    #: the reconciler — verifying against a version production does not use
    #: is how the last round of verification missed a removed field.
    stripe_api_version = models.CharField(max_length=64, blank=True, default="")
    plans_checked = models.PositiveIntegerField(default=0)
    prices_checked = models.PositiveIntegerField(default=0)
    matched_count = models.PositiveIntegerField(default=0)
    synced_count = models.PositiveIntegerField(default=0)
    alert_count = models.PositiveIntegerField(default=0)
    unavailable_count = models.PositiveIntegerField(default=0)
    summary = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["-started_at"])]

    def __str__(self):
        return f"Price reconciliation {self.id} ({self.summary or 'in progress'})"

    @property
    def needs_attention(self) -> bool:
        return self.alert_count > 0


class PriceReconciliationResult(models.Model):
    """
    One local expectation checked against one Stripe Price.

    Every comparison stores BOTH sides. Recording only the verdict would
    make the record useless for the thing it exists for — someone opening
    it at 3am needs to see what we expected and what Stripe said, without
    re-running anything or trusting that the code that wrote it was right.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        PriceReconciliationRun, on_delete=models.CASCADE, related_name="results"
    )
    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.CASCADE, related_name="price_reconciliations"
    )
    plan_name = models.CharField(max_length=100, blank=True, default="")
    plan_category = models.CharField(max_length=20, blank=True, default="")
    price_kind = models.CharField(max_length=10, choices=PriceKind.choices)
    price_id = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=24, choices=PriceReconciliationStatus.choices)

    expected_amount = models.IntegerField(null=True, blank=True)
    stripe_amount = models.IntegerField(null=True, blank=True)
    expected_currency = models.CharField(max_length=10, blank=True, default="")
    stripe_currency = models.CharField(max_length=10, blank=True, default="")
    expected_active = models.BooleanField(null=True, blank=True)
    stripe_active = models.BooleanField(null=True, blank=True)
    expected_product = models.CharField(max_length=255, blank=True, default="")
    stripe_product = models.CharField(max_length=255, blank=True, default="")
    expected_recurring = models.JSONField(null=True, blank=True)
    stripe_recurring = models.JSONField(null=True, blank=True)

    #: Every field that disagreed, so an alert can name them without
    #: re-deriving the comparison.
    mismatched_fields = models.JSONField(default=list, blank=True)

    #: --- Synchronisation audit trail ---------------------------------
    #: Stripe is the source of truth for prices, so a disagreement is
    #: RESOLVED by updating the local row rather than reported for a human
    #: to resolve. That makes the before/after the only surviving record of
    #: what the application used to charge, which is why it is stored
    #: rather than merely logged: a log line rotates away, and this is the
    #: answer to "when did this price change, and from what?".
    synced = models.BooleanField(default=False)
    synced_fields = models.JSONField(default=list, blank=True)
    previous_local_amount = models.IntegerField(null=True, blank=True)
    previous_local_product = models.CharField(max_length=255, blank=True, default="")

    error_code = models.CharField(max_length=100, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    detected_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["plan_name", "price_kind"]
        indexes = [
            models.Index(fields=["status", "-detected_at"]),
            models.Index(fields=["plan", "price_kind"]),
        ]

    def __str__(self):
        return f"{self.plan_name} {self.price_kind}: {self.status}"

    @property
    def needs_attention(self) -> bool:
        return self.status in PRICE_RECONCILIATION_ALERT_STATUSES
