"""
billing/qa_time_travel.py
==========================
QA-ONLY utility: a single endpoint that lets testers simulate subscription
renewal (individual and license) without touching Stripe's dashboard or
this codebase directly.

THE PROBLEM THIS SOLVES
------------------------
Stripe Test Clocks let QA fast-forward *Stripe's* simulated time, so Stripe
fires invoice/renewal webhooks as if weeks or months have passed. But our
own server's real wall clock never moves. Every renewal idempotency guard
in this codebase (webhooks.py handlers, tasks.py Celery sweeps) compares
locally-stored dates — billing_cycle_end, trial_end, next_credit_grant_at —
against REAL `timezone.now()`. Since those local dates were stamped using
real time when the subscription was created, they're still genuinely in
the future relative to the real clock even after Stripe's simulated clock
has raced ahead — so the webhook arrives, but every "is this actually due
yet?" check correctly (from a real-time perspective) says no, and nothing
appears to happen. This isn't a bug in the guards — they're doing exactly
what they're supposed to for real customers. It's a mismatch this tool
exists specifically to close, for testing only.

WHAT THIS TOOL DOES
--------------------
1. Rewrites the relevant local date field(s) on ONE subscription so they
   are genuinely <= real `timezone.now()` — making the existing, unmodified
   idempotency/eligibility checks in webhooks.py and tasks.py correctly
   conclude "this is due" the next time they run.
2. Separately, best-effort, advances the Stripe Test Clock attached to
   that subscription's Stripe customer (if any) so Stripe ALSO generates
   the corresponding invoice/webhook on its own, without the tester
   touching the Stripe dashboard.

   The clock is always advanced to (next billing boundary + 1 hour). The
   boundary is read from `items.data[].current_period_end` — Stripe moved
   it off the top-level Subscription in API version 2025-03-31, and this
   codebase pins a stripe release well past that — falling back to the
   legacy top-level `current_period_end`, then `trial_end`. If no
   boundary can be determined, or the clock already sits past it, NO
   advance is issued and an explicit error is returned. A short advance
   that fails to cross a boundary is worse than none: Stripe emits no
   invoice, so the renewal QA subsequently observes actually came from
   the nightly reconcile Celery sweep (which renews off the PREVIOUS
   cycle's already-paid invoice) while the response reads as success.
   The response's top-level `warnings` list calls this out whenever the
   boundary was not crossed.

This tool NEVER calls renewal business logic directly (never invokes
SubscriptionService.process_rollover_and_renewal, LicenseSubscriptionService.
process_license_renewal, etc.) — doing so would test a shortcut, not the
real, production webhook/Celery-triggered path this exists to validate.

HARD GUARDRAILS (all independently enforced, all must pass)
-------------------------------------------------------------
1. `settings.ENABLE_BILLING_TIME_TRAVEL` must be explicitly True.
2. The configured Stripe API key must be a TEST key (`sk_test_...`).
3. Caller must pass IsSuperAdmin.
Failing (1) or (2) returns a bare 404 — not 403 — so a misconfigured
deployment gives no hint this endpoint exists. There is deliberately NO
`DEBUG`/`ENVIRONMENT` requirement — ENABLE_BILLING_TIME_TRAVEL is the
single toggle controlling reachability, so whoever controls that one
setting fully controls whether this endpoint is live. The Stripe
test-key check (2) is what actually prevents it from ever mutating real
subscription dates against LIVE Stripe data even if that toggle is
mistakenly left on somewhere.
"""

import logging
import time
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any

from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from classrooms.permissions import IsSuperAdmin

from .imports import stripe
from .models import (
    BillingInterval,
    LicenseBillingMethod,
    LicenseSubscription,
    SchoolCreditAllocation,
    UserSubscription,
)

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Guardrail check
# ----------------------------------------------------------------------


def _time_travel_enabled() -> bool:
    """
    Both conditions must hold. Any single failure disables the feature
    entirely — this is intentionally NOT a "most conditions" check; each
    guardrail is independently sufficient to block access.

    Deliberately does NOT check settings.DEBUG/ENVIRONMENT —
    ENABLE_BILLING_TIME_TRAVEL is the single toggle controlling whether
    this endpoint is reachable at all, in any environment. The Stripe
    test-key check still stands on its own regardless: even with the
    toggle on, this can never run against a live (non-sk_test_) key.
    """
    if not getattr(settings, "ENABLE_BILLING_TIME_TRAVEL", False):
        return False
    api_key = getattr(stripe, "api_key", "") or ""
    if not api_key.startswith("sk_test_"):
        return False
    return True


# ----------------------------------------------------------------------
# Test Clock attachment at customer-creation time
#
# Stripe only allows a Test Clock to be attached when the Customer is
# CREATED — there is no way to attach one later. So a customer made
# through the ordinary checkout flow is permanently clockless, and every
# advance attempt for it exits with "not attached to a Test Clock". That
# left the clock half of this tool inert for everything except customers
# hand-built in the Stripe dashboard.
#
# Opting in is deliberately narrow and explicit: the time-travel flag AND
# a Stripe test key AND the customer's email domain being listed in
# settings.BILLING_TEST_CLOCK_EMAIL_DOMAINS. That list defaults to empty,
# so with no configuration this changes nothing for anyone.
# ----------------------------------------------------------------------


def _test_clock_email_domains():
    """
    Normalized lowercase domain set from settings. Accepts entries with
    or without a leading '@'. The single entry "*" means "every customer
    created in this environment", for a dedicated QA deployment.
    """
    raw = getattr(settings, "BILLING_TEST_CLOCK_EMAIL_DOMAINS", None) or []
    if isinstance(raw, str):
        raw = [part for part in raw.replace(",", " ").split() if part]
    return {
        str(entry).strip().lstrip("@").lower() for entry in raw if str(entry).strip()
    }


def should_attach_test_clock(email) -> bool:
    """
    Every guardrail that gates the time-travel endpoint gates this too —
    a live Stripe key can never reach TestClock.create — plus the domain
    allow-list. Safe to call from production code paths.
    """
    if not _time_travel_enabled():
        return False

    domains = _test_clock_email_domains()
    if not domains:
        return False
    if "*" in domains:
        return True

    if not email or "@" not in str(email):
        return False
    return str(email).rsplit("@", 1)[-1].strip().lower() in domains


def new_customer_test_clock_kwargs(email, label=None) -> dict:
    """
    Returns `{"test_clock": <id>}` to splice into a stripe.Customer.create
    call, or `{}` when this customer should not get one.

    Raises (rather than degrading to `{}`) if clock creation fails. A
    customer created without a clock can NEVER be given one afterwards,
    so silently falling back would hand QA a permanently un-simulatable
    customer — the exact inert-tool failure this exists to remove. A
    loud failure in an explicitly QA-configured environment is cheap and
    retryable; a silent one costs a wasted test cycle to rediscover.
    """
    if not should_attach_test_clock(email):
        return {}

    if not (
        hasattr(stripe, "test_helpers") and hasattr(stripe.test_helpers, "TestClock")
    ):
        raise ValueError(
            "BILLING_TEST_CLOCK_EMAIL_DOMAINS matched this customer, but the "
            "installed stripe library does not expose "
            "stripe.test_helpers.TestClock. Upgrade the stripe package or "
            "clear the setting."
        )

    try:
        clock = stripe.test_helpers.TestClock.create(
            frozen_time=int(time.time()),
            name=(label or f"QA time travel — {email}")[:250],
        )
    except stripe.error.StripeError as exc:
        raise ValueError(
            "Could not create a Stripe Test Clock for this QA customer: "
            f"{getattr(exc, 'user_message', None) or exc}. Refusing to create "
            "a clockless customer, since a Test Clock cannot be attached "
            "after the fact."
        ) from exc

    clock_id = QATimeTravelService._safe_get(clock, "id")
    if not clock_id:
        raise ValueError(
            "Stripe returned a Test Clock with no id; refusing to create a "
            "clockless QA customer."
        )

    logger.warning(
        "[QA TIME TRAVEL] Created Test Clock %s for new Stripe customer (%s).",
        clock_id,
        email,
    )
    return {"test_clock": clock_id}


# ----------------------------------------------------------------------
# Serializer
# ----------------------------------------------------------------------


class BillingTimeTravelRequestSerializer(serializers.Serializer):
    SUBSCRIPTION_TYPES = ("INDIVIDUAL", "LICENSE")
    MODES = ("full_renewal", "mid_cycle_grant", "trial_expiry")

    subscription_type = serializers.ChoiceField(choices=SUBSCRIPTION_TYPES)
    subscription_id = serializers.UUIDField()
    mode = serializers.ChoiceField(choices=MODES, default="full_renewal")
    target_datetime = serializers.DateTimeField(
        required=False,
        allow_null=True,
        default=None,
        help_text=(
            "Optional. The past datetime to rewrite the relevant local "
            "field(s) to. Defaults to now minus a 5-minute safety buffer. "
            "Must not be in the future."
        ),
    )
    allocation_user_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        default=None,
        help_text=(
            "LICENSE + mid_cycle_grant only. Limits the refresh to one "
            "teacher's SchoolCreditAllocation. Omit to affect every "
            "currently active allocation under the license (matching "
            "process_license_monthly_credit_refreshes' own selection)."
        ),
    )
    advance_stripe_test_clock = serializers.BooleanField(default=True)
    wait_for_test_clock_ready = serializers.BooleanField(
        default=False,
        help_text=(
            "If True, polls the Test Clock's status for up to 15 seconds "
            "after issuing the advance. Purely a convenience — Stripe's "
            "own processing can take longer for complex accounts; the "
            "webhook will still arrive on its own even if this times out."
        ),
    )

    def validate_target_datetime(self, value):
        if value is None:
            return None
        if value > timezone.now():
            raise serializers.ValidationError(
                "target_datetime must not be in the future."
            )
        return value

    def validate(self, attrs):
        if attrs["subscription_type"] == "INDIVIDUAL" and attrs.get(
            "allocation_user_id"
        ):
            raise serializers.ValidationError(
                {"allocation_user_id": "Only applicable to LICENSE + mid_cycle_grant."}
            )
        if attrs["subscription_type"] == "LICENSE" and attrs["mode"] == "trial_expiry":
            raise serializers.ValidationError(
                {
                    "mode": "trial_expiry does not apply to LICENSE subscriptions — "
                    "licenses have no trial concept."
                }
            )
        return attrs


# ----------------------------------------------------------------------
# Core service
# ----------------------------------------------------------------------


class QATimeTravelService:
    """
    All logic isolated here, away from SubscriptionService /
    LicenseSubscriptionService, so this test-only tool can never be
    accidentally imported into or confused with production billing logic,
    and can be deleted wholesale (this file + one urls.py line + one
    settings flag) with zero impact on production code.
    """

    _DEFAULT_BUFFER = timedelta(minutes=5)
    # Advance to 1 hour PAST the next billing boundary, never merely 1 hour
    # past wherever the clock currently sits — see _resolve_billing_boundary.
    _TEST_CLOCK_ADVANCE_BUFFER_SECONDS = 3600
    _TEST_CLOCK_POLL_INTERVAL_SECONDS = 1
    _TEST_CLOCK_POLL_TIMEOUT_SECONDS = 15

    @staticmethod
    def _resolve_target(target_datetime):
        return target_datetime or (timezone.now() - QATimeTravelService._DEFAULT_BUFFER)

    # ------------------------------------------------------------------
    # Stripe billing-boundary extraction
    #
    # Stripe moved `current_period_end` OFF the top-level Subscription
    # object and onto each subscription ITEM in API version 2025-03-31.
    # This codebase pins stripe==14.4.1, which is well past that cutover,
    # so reading only the top-level field yields None — and an anchor of
    # None used to silently degrade into "advance 1 hour from wherever the
    # clock is", which never crosses a period boundary and therefore never
    # makes Stripe emit the renewal invoice this whole tool exists to
    # trigger. The same pre/post-2025-03-31 dual read already exists for
    # invoices in stripe_service._extract_invoice_subscription_id; this is
    # the subscription-side equivalent.
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_get(obj, key):
        """
        Reads `key` off a Stripe object, a plain dict, or an attribute-only
        object. Stripe's StripeObject supports both mapping and attribute
        access, but tests (and older library versions) may hand us either
        shape, and a missing key must never raise.
        """
        if obj is None:
            return None
        getter = getattr(obj, "get", None)
        if callable(getter):
            try:
                return getter(key)
            except TypeError:
                pass
        return getattr(obj, key, None)

    @staticmethod
    def _coerce_timestamp(value):
        """
        Stripe timestamps are positive Unix ints. Anything else — None,
        0, a string, a bool (which is an int subclass in Python, hence the
        explicit guard), a MagicMock leaking in from a test — is treated
        as "absent" rather than trusted, since a bogus anchor is exactly
        how the original defect stayed invisible.
        """
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            ts = int(value)
            return ts if ts > 0 else None
        return None

    @staticmethod
    def _collect_billing_boundaries(stripe_sub):
        """
        Every timestamp at which Stripe would advance this subscription's
        billing, newest API shape first. Returns a list of
        (unix_timestamp, source_label) — the label is echoed back in the
        response so QA can see WHICH field drove the advance.

        `trial_end` is included because a trialing subscription's next
        billing event is the trial ending, and mode='trial_expiry' needs
        the clock to cross exactly that.
        """
        candidates = []

        items = QATimeTravelService._safe_get(stripe_sub, "items")
        data = QATimeTravelService._safe_get(items, "data")
        try:
            item_list = list(data) if data is not None else []
        except TypeError:
            item_list = []

        for index, item in enumerate(item_list):
            ts = QATimeTravelService._coerce_timestamp(
                QATimeTravelService._safe_get(item, "current_period_end")
            )
            if ts is not None:
                candidates.append((ts, f"items.data[{index}].current_period_end"))

        # Pre-2025-03-31 API versions (and some expanded payloads) still
        # carry the top-level field — kept as a fallback so this keeps
        # working if the stripe pin is ever rolled back.
        top_level = QATimeTravelService._coerce_timestamp(
            QATimeTravelService._safe_get(stripe_sub, "current_period_end")
        )
        if top_level is not None:
            candidates.append((top_level, "current_period_end"))

        trial_end = QATimeTravelService._coerce_timestamp(
            QATimeTravelService._safe_get(stripe_sub, "trial_end")
        )
        if trial_end is not None:
            candidates.append((trial_end, "trial_end"))

        return candidates

    @staticmethod
    def _resolve_billing_boundary(stripe_sub, frozen_time):
        """
        Picks the boundary the clock must cross: the EARLIEST one still
        ahead of the clock, so a single advance triggers exactly the next
        billing event rather than skipping over several cycles at once.

        If nothing is ahead of the clock, returns the latest boundary
        found anyway — the caller compares it against `frozen_time` and
        reports "already past" rather than issuing a pointless advance.

        Returns (boundary_ts | None, source_label | None).
        """
        candidates = QATimeTravelService._collect_billing_boundaries(stripe_sub)
        if not candidates:
            return None, None

        ahead = [c for c in candidates if c[0] > frozen_time]
        if ahead:
            return min(ahead, key=lambda c: c[0])
        return max(candidates, key=lambda c: c[0])

    @staticmethod
    def _iso(timestamp):
        if timestamp is None:
            return None
        return datetime.fromtimestamp(timestamp, tz=dt_timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # INDIVIDUAL
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def rewind_individual_subscription(subscription_id, mode, target_datetime):
        target = QATimeTravelService._resolve_target(target_datetime)

        user_sub = (
            UserSubscription.objects.select_for_update()
            .select_related("plan", "user")
            .get(pk=subscription_id)
        )

        if not user_sub.is_active:
            raise ValueError("Subscription is not active — nothing to simulate.")

        before = {
            "billing_cycle_end": user_sub.billing_cycle_end,
            "trial_end": user_sub.trial_end,
            "next_credit_grant_at": user_sub.next_credit_grant_at,
        }
        changed_fields = []

        if mode == "trial_expiry":
            if not user_sub.is_trial:
                raise ValueError(
                    "Subscription is not a trial. Use mode='full_renewal' instead."
                )
            user_sub.trial_end = target
            user_sub.billing_cycle_end = target
            changed_fields = ["trial_end", "billing_cycle_end", "updated_at"]

        elif mode == "full_renewal":
            if user_sub.is_trial:
                raise ValueError(
                    "Subscription is currently a trial. Use mode='trial_expiry' "
                    "to simulate trial expiry instead — a trial does not renew "
                    "via invoice.payment_succeeded the same way a paid "
                    "subscription does."
                )
            if not user_sub.stripe_subscription_id:
                raise ValueError(
                    "This subscription has no stripe_subscription_id. Neither "
                    "the invoice.payment_succeeded webhook nor "
                    "reconcile_subscription_renewals (which requires "
                    "stripe_subscription_id__isnull=False) will process this "
                    "automatically — nothing to simulate here."
                )
            user_sub.billing_cycle_end = target
            changed_fields = ["billing_cycle_end", "updated_at"]
            # Keep next_credit_grant_at consistent for MONTHLY plans, where
            # it's supposed to always equal billing_cycle_end. For ANNUAL
            # plans it's deliberately independent — left untouched here;
            # the real renewal (activate_subscription) recomputes it fully
            # once triggered.
            if user_sub.plan.interval == BillingInterval.MONTHLY:
                user_sub.next_credit_grant_at = target
                changed_fields.append("next_credit_grant_at")

        elif mode == "mid_cycle_grant":
            if user_sub.is_trial:
                raise ValueError(
                    "mid_cycle_grant does not apply to trial subscriptions."
                )
            if user_sub.plan.interval != BillingInterval.ANNUAL:
                raise ValueError(
                    "mid_cycle_grant only applies to ANNUAL-interval plans — "
                    f"this subscription's plan interval is {user_sub.plan.interval}. "
                    "MONTHLY plans have no separate mid-cycle grant concept "
                    "(the renewal IS the credit grant); use full_renewal instead."
                )
            if user_sub.billing_cycle_end <= timezone.now():
                raise ValueError(
                    "This subscription's annual billing_cycle_end has already "
                    "elapsed locally — process_annual_plan_credit_grants "
                    "requires billing_cycle_end to still be in the future "
                    "(otherwise the real annual renewal takes over instead). "
                    "Use mode='full_renewal' to test that path, or restore "
                    "billing_cycle_end first."
                )
            user_sub.next_credit_grant_at = target
            changed_fields = ["next_credit_grant_at", "updated_at"]

        user_sub.save(update_fields=changed_fields)

        after = {
            "billing_cycle_end": user_sub.billing_cycle_end,
            "trial_end": user_sub.trial_end,
            "next_credit_grant_at": user_sub.next_credit_grant_at,
        }

        logger.warning(
            "[QA TIME TRAVEL] Individual subscription %s (user %s) rewound. "
            "mode=%s target=%s before=%s after=%s",
            user_sub.id,
            user_sub.user.email,
            mode,
            target.isoformat(),
            before,
            after,
        )

        return {
            "before": before,
            "after": after,
            "stripe_subscription_id": user_sub.stripe_subscription_id,
            # Stripe test-clock advancement is only meaningful for modes
            # backed by a real Stripe webhook signal. mid_cycle_grant is a
            # purely local, Celery-only concept — Stripe never fires
            # anything for it regardless of clock position.
            "stripe_advancement_applicable": mode in ("full_renewal", "trial_expiry"),
        }

    # ------------------------------------------------------------------
    # LICENSE
    # ------------------------------------------------------------------

    @staticmethod
    @transaction.atomic
    def rewind_license_subscription(
        subscription_id, mode, target_datetime, allocation_user_id=None
    ):
        target = QATimeTravelService._resolve_target(target_datetime)

        license_sub = (
            LicenseSubscription.objects.select_for_update()
            .select_related("plan", "school")
            .get(pk=subscription_id)
        )

        if not license_sub.is_active:
            raise ValueError("License is not active — nothing to simulate.")

        if mode == "full_renewal":
            if license_sub.billing_method != LicenseBillingMethod.STRIPE:
                raise ValueError(
                    "License is billed OFFLINE. Neither the "
                    "invoice.payment_succeeded webhook nor "
                    "process_license_renewals (which excludes "
                    "billing_method=OFFLINE) will process this "
                    "automatically. Use the renew-offline endpoint directly "
                    "to test offline renewal instead."
                )
            if not license_sub.stripe_subscription_id:
                raise ValueError(
                    "This license has no stripe_subscription_id — nothing "
                    "to simulate via webhook/Celery here."
                )

            before = {"billing_cycle_end": license_sub.billing_cycle_end}
            license_sub.billing_cycle_end = target
            license_sub.save(update_fields=["billing_cycle_end", "updated_at"])
            after = {"billing_cycle_end": license_sub.billing_cycle_end}

            logger.warning(
                "[QA TIME TRAVEL] License %s (school %s) rewound. "
                "mode=full_renewal target=%s before=%s after=%s",
                license_sub.id,
                license_sub.school.name,
                target.isoformat(),
                before,
                after,
            )

            return {
                "before": before,
                "after": after,
                "stripe_subscription_id": license_sub.stripe_subscription_id,
                "stripe_customer_id": license_sub.stripe_customer_id,
                "stripe_advancement_applicable": True,
                "affected_allocations": None,
            }

        elif mode == "mid_cycle_grant":
            if license_sub.billing_cycle_end <= timezone.now():
                raise ValueError(
                    "This license's billing_cycle_end has already elapsed "
                    "locally — process_license_monthly_credit_refreshes "
                    "requires license_subscription__billing_cycle_end__gt=now "
                    "(otherwise the real contract renewal takes over "
                    "instead). Use mode='full_renewal', or restore "
                    "billing_cycle_end first."
                )

            allocations_qs = SchoolCreditAllocation.objects.select_for_update().filter(
                license_subscription=license_sub, is_active=True
            )
            if allocation_user_id:
                allocations_qs = allocations_qs.filter(user_id=allocation_user_id)

            allocations = list(allocations_qs.select_related("user"))
            if not allocations:
                raise ValueError(
                    "No active SchoolCreditAllocation found matching this "
                    "request (check allocation_user_id, or that the license "
                    "has active allocations at all)."
                )

            affected = []
            for allocation in allocations:
                before_val = allocation.next_credit_grant_at
                allocation.next_credit_grant_at = target
                allocation.save(update_fields=["next_credit_grant_at", "updated_at"])
                affected.append(
                    {
                        "teacher_id": str(allocation.user_id),
                        "teacher_email": allocation.user.email,
                        "before": before_val,
                        "after": target,
                    }
                )

            logger.warning(
                "[QA TIME TRAVEL] License %s mid_cycle_grant rewound for "
                "%d allocation(s). target=%s affected=%s",
                license_sub.id,
                len(affected),
                target.isoformat(),
                affected,
            )

            return {
                "before": None,
                "after": None,
                "stripe_subscription_id": license_sub.stripe_subscription_id,
                "stripe_customer_id": license_sub.stripe_customer_id,
                # Purely local/Celery-driven, same reasoning as the
                # individual mid_cycle_grant mode — no Stripe signal exists
                # for this regardless of clock position.
                "stripe_advancement_applicable": False,
                "affected_allocations": affected,
            }

        raise ValueError(f"Unsupported mode for LICENSE: {mode!r}")

    # ------------------------------------------------------------------
    # Stripe Test Clock advancement (best-effort, never raises upward)
    # ------------------------------------------------------------------

    @staticmethod
    def advance_test_clock_for_subscription(
        stripe_subscription_id, wait_for_ready=False
    ):
        """
        Best-effort. NEVER lets an exception propagate — every failure mode
        is captured and returned as a structured result so the caller (the
        view) can always return HTTP 200 for the (already-committed) local
        date fix, with this section purely informational.

        Deliberately called AFTER the local-DB transaction has already
        committed — never holds a DB lock across this network call.

        The advance target is always (next billing boundary + 1 hour), so
        Stripe genuinely generates the renewal/trial-end invoice and fires
        the webhook. An advance that would NOT cross a boundary is
        reported as an explicit error with advanced=False rather than
        being issued and reported as success — a short advance looks green
        but leaves the real webhook path untested, with the nightly
        reconcile_subscription_renewals sweep silently renewing off the
        PREVIOUS cycle's already-paid invoice instead.
        """
        # Annotated because the values are deliberately heterogeneous —
        # booleans, ints, timestamps and human-readable strings share this
        # structure so the endpoint can return one flat diagnostic object.
        result: dict[str, Any] = {
            "attempted": False,
            "advanced": False,
            "test_clock_id": None,
            "previous_status": None,
            "new_status": None,
            "previous_frozen_time": None,
            "previous_frozen_time_iso": None,
            "billing_boundary": None,
            "billing_boundary_iso": None,
            "billing_boundary_source": None,
            "target_frozen_time": None,
            "target_frozen_time_iso": None,
            "advanced_seconds": None,
            "crossed_billing_boundary": False,
            "observed_frozen_time": None,
            "observed_frozen_time_iso": None,
            "waited": False,
            "error": None,
            "note": None,
        }

        if not stripe_subscription_id:
            result["note"] = "No stripe_subscription_id on this subscription — skipped."
            return result

        try:
            has_test_helpers = hasattr(stripe, "test_helpers") and hasattr(
                stripe.test_helpers, "TestClock"
            )
            if not has_test_helpers:
                result["error"] = (
                    "Installed stripe library version does not expose "
                    "stripe.test_helpers.TestClock. Upgrade the stripe "
                    "package to use automatic test-clock advancement."
                )
                return result

            result["attempted"] = True

            stripe_sub = stripe.Subscription.retrieve(stripe_subscription_id)
            customer_id = QATimeTravelService._safe_get(stripe_sub, "customer")

            if not customer_id:
                result["note"] = "Stripe subscription has no customer — skipped."
                return result

            customer = stripe.Customer.retrieve(customer_id)
            test_clock_id = customer.get("test_clock")

            if not test_clock_id:
                result["note"] = (
                    "This Stripe customer is not attached to a Test Clock — "
                    "advance it manually in the Stripe dashboard if needed, "
                    "or attach this customer to a test clock going forward."
                )
                return result

            result["test_clock_id"] = test_clock_id
            clock = stripe.test_helpers.TestClock.retrieve(test_clock_id)
            result["previous_status"] = clock.get("status")

            if clock.get("status") == "advancing":
                result["error"] = (
                    "Test clock is already advancing from a previous request "
                    "— Stripe does not allow overlapping advances. Wait for "
                    "it to finish and retry."
                )
                return result

            current_frozen_time = QATimeTravelService._coerce_timestamp(
                QATimeTravelService._safe_get(clock, "frozen_time")
            )
            if current_frozen_time is None:
                result["error"] = (
                    "Test clock reported no usable frozen_time, so there is "
                    "no reference point to advance from. Inspect test clock "
                    f"{test_clock_id} in the Stripe dashboard."
                )
                return result

            result["previous_frozen_time"] = current_frozen_time
            result["previous_frozen_time_iso"] = QATimeTravelService._iso(
                current_frozen_time
            )

            boundary, boundary_source = QATimeTravelService._resolve_billing_boundary(
                stripe_sub, current_frozen_time
            )

            if boundary is None:
                result["error"] = (
                    "Could not determine the next billing boundary for "
                    f"{stripe_subscription_id} — neither "
                    "items.data[].current_period_end, nor a top-level "
                    "current_period_end, nor trial_end was present on the "
                    "Stripe subscription. Without it, advancing the clock "
                    "could not be guaranteed to cross a period boundary, so "
                    "no advance was issued (advancing a short distance would "
                    "produce no renewal invoice while still looking like a "
                    "success)."
                )
                return result

            result["billing_boundary"] = boundary
            result["billing_boundary_iso"] = QATimeTravelService._iso(boundary)
            result["billing_boundary_source"] = boundary_source

            if boundary <= current_frozen_time:
                result["error"] = (
                    "The test clock is already at or past this "
                    f"subscription's next billing boundary ({boundary_source}"
                    f" = {QATimeTravelService._iso(boundary)}, clock = "
                    f"{QATimeTravelService._iso(current_frozen_time)}), so "
                    "advancing further cannot produce another renewal "
                    "invoice — Stripe already generated it when the clock "
                    "first crossed. Check webhook delivery for the invoice "
                    "that was already issued rather than advancing again."
                )
                return result

            target_frozen_time = (
                boundary + QATimeTravelService._TEST_CLOCK_ADVANCE_BUFFER_SECONDS
            )
            result["target_frozen_time"] = target_frozen_time
            result["target_frozen_time_iso"] = QATimeTravelService._iso(
                target_frozen_time
            )
            result["advanced_seconds"] = target_frozen_time - current_frozen_time

            advanced_clock = stripe.test_helpers.TestClock.advance(
                test_clock_id, frozen_time=target_frozen_time
            )
            result["advanced"] = True
            # True by construction: target = boundary + buffer, and we
            # returned early above unless boundary > current_frozen_time.
            result["crossed_billing_boundary"] = True
            result["new_status"] = QATimeTravelService._safe_get(
                advanced_clock, "status"
            )
            observed = QATimeTravelService._coerce_timestamp(
                QATimeTravelService._safe_get(advanced_clock, "frozen_time")
            )
            if observed is not None:
                result["observed_frozen_time"] = observed
                result["observed_frozen_time_iso"] = QATimeTravelService._iso(observed)

            if wait_for_ready:
                result["waited"] = True
                deadline = (
                    time.monotonic()
                    + QATimeTravelService._TEST_CLOCK_POLL_TIMEOUT_SECONDS
                )
                while time.monotonic() < deadline:
                    time.sleep(QATimeTravelService._TEST_CLOCK_POLL_INTERVAL_SECONDS)
                    polled = stripe.test_helpers.TestClock.retrieve(test_clock_id)
                    polled_status = QATimeTravelService._safe_get(polled, "status")
                    result["new_status"] = polled_status
                    polled_frozen = QATimeTravelService._coerce_timestamp(
                        QATimeTravelService._safe_get(polled, "frozen_time")
                    )
                    if polled_frozen is not None:
                        result["observed_frozen_time"] = polled_frozen
                        result["observed_frozen_time_iso"] = QATimeTravelService._iso(
                            polled_frozen
                        )
                    if polled_status != "advancing":
                        break
                if result["new_status"] == "advancing":
                    result["note"] = (
                        "Still advancing after the 15s poll window — this is "
                        "normal for accounts with several linked objects. "
                        "The webhook will still arrive once Stripe finishes; "
                        "no further action needed."
                    )

            return result

        except stripe.error.StripeError as exc:
            result["error"] = (
                f"Stripe error: {getattr(exc, 'user_message', None) or str(exc)}"
            )
            return result
        except Exception as exc:  # noqa: BLE001 — deliberately broad, see docstring
            logger.exception(
                "[QA TIME TRAVEL] Unexpected error advancing test clock for "
                "subscription %s",
                stripe_subscription_id,
            )
            result["error"] = f"Unexpected error: {exc}"
            return result

    # ------------------------------------------------------------------
    # Response warnings
    # ------------------------------------------------------------------

    @staticmethod
    def build_simulation_warnings(local_result, stripe_result):
        """
        Flags the specific silent-failure this module's clock handling
        exists to prevent: the local rewind succeeded (so SOMETHING will
        renew) but Stripe was never pushed across a billing boundary (so
        no invoice, no invoice.payment_succeeded webhook). Whatever QA
        observes next then comes from reconcile_subscription_renewals —
        which reads the PREVIOUS cycle's already-paid invoice, sees
        status="paid", and renews locally. Green result, wrong code path.

        Returns a list of strings; empty means nothing to flag.
        """
        warnings = []

        if not local_result.get("stripe_advancement_applicable"):
            # Local/Celery-only mode (mid_cycle_grant) — no Stripe signal
            # exists at any clock position, so silence is correct here.
            return warnings

        if stripe_result is None:
            warnings.append(
                "Stripe test clock advancement was skipped "
                "(advance_stripe_test_clock=false). The local dates were "
                "rewound, but Stripe will not emit an invoice webhook — any "
                "renewal you observe will come from the nightly "
                "reconcile/fallback Celery sweep, NOT the production "
                "invoice.payment_succeeded path."
            )
            return warnings

        if not stripe_result.get("crossed_billing_boundary"):
            detail = (
                stripe_result.get("error")
                or stripe_result.get("note")
                or "the test clock was not advanced past a billing boundary."
            )
            warnings.append(
                "Stripe did NOT cross a billing boundary, so no renewal "
                "invoice and no invoice.payment_succeeded webhook will be "
                "generated. Any renewal you observe after this call came "
                "from the Celery fallback sweep "
                "(reconcile_subscription_renewals / process_license_renewals) "
                "reading the previous cycle's already-paid invoice — NOT the "
                f"production webhook path. Reason: {detail}"
            )

        return warnings


# ----------------------------------------------------------------------
# View
# ----------------------------------------------------------------------


class BillingTimeTravelView(APIView):
    permission_classes = [IsAuthenticated, IsSuperAdmin]

    @extend_schema(
        summary="QA: simulate subscription renewal (time travel)",
        description=(
            "QA-only. Requires ENABLE_BILLING_TIME_TRAVEL=True, a Stripe "
            "TEST key, and superadmin auth — returns 404 otherwise. See "
            "billing/qa_time_travel.py module docstring for full details."
        ),
        request=BillingTimeTravelRequestSerializer,
    )
    def post(self, request, *args, **kwargs):
        if not _time_travel_enabled():
            # 404, not 403 — no acknowledgement this endpoint exists at
            # all when disabled. See module docstring guardrail list.
            from django.http import Http404

            raise Http404()

        serializer = BillingTimeTravelRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        d = serializer.validated_data

        model = (
            UserSubscription
            if d["subscription_type"] == "INDIVIDUAL"
            else LicenseSubscription
        )
        instance = get_object_or_404(model, pk=d["subscription_id"])

        try:
            if d["subscription_type"] == "INDIVIDUAL":
                local_result = QATimeTravelService.rewind_individual_subscription(
                    instance.pk, d["mode"], d["target_datetime"]
                )
            else:
                local_result = QATimeTravelService.rewind_license_subscription(
                    instance.pk,
                    d["mode"],
                    d["target_datetime"],
                    d["allocation_user_id"],
                )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        stripe_result = None
        if (
            d["advance_stripe_test_clock"]
            and local_result["stripe_advancement_applicable"]
        ):
            stripe_result = QATimeTravelService.advance_test_clock_for_subscription(
                local_result["stripe_subscription_id"], d["wait_for_test_clock_ready"]
            )
        elif d["advance_stripe_test_clock"]:
            stripe_result = {
                "attempted": False,
                "note": (
                    f"mode={d['mode']!r} has no corresponding Stripe webhook — "
                    "it is driven entirely by local Celery tasks, so test "
                    "clock advancement is skipped by design."
                ),
            }

        warnings = QATimeTravelService.build_simulation_warnings(
            local_result, stripe_result
        )

        logger.warning(
            "[QA TIME TRAVEL] Executed by superadmin %s: type=%s id=%s "
            "mode=%s warnings=%s",
            request.user.email,
            d["subscription_type"],
            d["subscription_id"],
            d["mode"],
            warnings or "none",
        )

        return Response(
            {
                "subscription_type": d["subscription_type"],
                "subscription_id": str(d["subscription_id"]),
                "mode": d["mode"],
                "local_changes": local_result,
                "stripe_test_clock": stripe_result,
                # Non-empty means the local rewind landed but Stripe will
                # NOT emit the corresponding webhook, so whatever renewal
                # QA observes afterwards came from the Celery fallback
                # sweep, not the production webhook path. HTTP is still
                # 200 because the local rewind genuinely committed.
                "warnings": warnings,
            },
            status=status.HTTP_200_OK,
        )
