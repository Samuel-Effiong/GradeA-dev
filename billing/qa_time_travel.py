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
from datetime import timedelta

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
    _TEST_CLOCK_ADVANCE_BUFFER_SECONDS = 3600  # 1 hour past current_period_end
    _TEST_CLOCK_POLL_INTERVAL_SECONDS = 1
    _TEST_CLOCK_POLL_TIMEOUT_SECONDS = 15

    @staticmethod
    def _resolve_target(target_datetime):
        return target_datetime or (timezone.now() - QATimeTravelService._DEFAULT_BUFFER)

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
        """
        result = {
            "attempted": False,
            "advanced": False,
            "test_clock_id": None,
            "previous_status": None,
            "new_status": None,
            "target_frozen_time": None,
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
            customer_id = stripe_sub.get("customer")
            current_period_end = stripe_sub.get("current_period_end")

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

            current_frozen_time = clock.get("frozen_time") or 0
            anchor = current_period_end or current_frozen_time
            target_frozen_time = (
                max(anchor, current_frozen_time)
                + QATimeTravelService._TEST_CLOCK_ADVANCE_BUFFER_SECONDS
            )
            result["target_frozen_time"] = target_frozen_time

            advanced_clock = stripe.test_helpers.TestClock.advance(
                test_clock_id, frozen_time=target_frozen_time
            )
            result["advanced"] = True
            result["new_status"] = advanced_clock.get("status")

            if wait_for_ready:
                result["waited"] = True
                deadline = (
                    time.monotonic()
                    + QATimeTravelService._TEST_CLOCK_POLL_TIMEOUT_SECONDS
                )
                while time.monotonic() < deadline:
                    time.sleep(QATimeTravelService._TEST_CLOCK_POLL_INTERVAL_SECONDS)
                    polled = stripe.test_helpers.TestClock.retrieve(test_clock_id)
                    result["new_status"] = polled.get("status")
                    if polled.get("status") != "advancing":
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

        logger.warning(
            "[QA TIME TRAVEL] Executed by superadmin %s: type=%s id=%s " "mode=%s",
            request.user.email,
            d["subscription_type"],
            d["subscription_id"],
            d["mode"],
        )

        return Response(
            {
                "subscription_type": d["subscription_type"],
                "subscription_id": str(d["subscription_id"]),
                "mode": d["mode"],
                "local_changes": local_result,
                "stripe_test_clock": stripe_result,
            },
            status=status.HTTP_200_OK,
        )
