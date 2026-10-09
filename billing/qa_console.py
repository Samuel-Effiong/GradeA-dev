"""
billing/qa_console.py
======================
An internal, superadmin-only web console for driving the real-Stripe QA
suite interactively instead of only from a terminal.

WHY THIS EXISTS
---------------
Testing billing meant either clicking through Stripe's own dashboard by
hand, or running `manage.py run_stripe_live_qa` and reading log output.
Neither lets you drive ONE test subscriber through a flow (subscribe,
upgrade, fail a payment, refund, cancel) and watch local state change
after every click, and neither lets you trigger or review a suite run
without shell access.

This is a UI over what already exists, not new billing logic: the
Console tab calls straight into billing/live_qa/chaos.py's action
functions (the same ones the seeded chaos walk exercises), and the
Dashboard tab calls run_suite / run_chaos_walk / shrink_chaos_failure
unchanged.

SECURITY MODEL — identical to billing/qa_time_travel.py
---------------------------------------------------------
Gated by the EXISTING settings.ENABLE_STRIPE_LIVE_QA flag and a real
Stripe TEST key (billing.stripe_live_qa.live_qa_enabled() — the same
function LiveQAHarness itself requires internally, so there is no way
to reach this tool without also being able to reach everything it
calls). When disabled, every view returns a bare Http404 — no hint the
tool exists — exactly matching qa_time_travel.py's documented
rationale. On top of that, every view requires an authenticated
superadmin (mirrors classrooms.permissions.IsSuperAdmin, restated here
as a plain function since these are ordinary Django views, not DRF
ones — this module needs to render HTML, which APIView does not do).

WHY THE CONSOLE TAB USES SESSION STATE, NOT A HARNESS OBJECT
----------------------------------------------------------------
LiveQAHarness is built to live for the duration of ONE process running
ONE suite — its cleanup() walks in-memory lists it populated itself
while creating things. An interactive console is the opposite shape:
one HTTP request per click, with nothing surviving between them. So the
"current test subscriber" here is a small dict of ids
(request.session["qa_console_subscriber"]) rather than a live object,
and each request:
  - rebuilds a REAL stripe_live_qa_scenarios.Subscriber from those ids
    (cheap — it is just a dataclass of ids plus a freshly re-fetched
    CustomUser/SubscriptionPlan), and
  - uses the BASE LiveQAHarness (not ConcurrentLiveQAHarness) for
    drain_events, since the base implementation polls Stripe directly
    per customer_id and needs no shared event-bus state carried over
    from a previous request.
Teardown (_teardown_session_subscriber) reuses LiveQAHarness.cleanup()
for real rather than reimplementing it: it builds a throwaway harness
and manually populates its tracking lists from the session-stored ids
before calling cleanup() — the method does not care how those lists
got populated, only that they did.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Optional

from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods
from rest_framework import serializers

import billing.live_qa  # noqa: F401  (registers fast + deep scenarios)
from users.models import UserTypes

from .live_qa.chaos import DEFAULT_ACTIONS, ChaosContext
from .models import (
    BillingInterval,
    LiveQARun,
    LiveQARunKind,
    PlanTier,
    SubscriptionPlan,
)
from .stripe_live_qa import CheckRecorder, LiveQAHarness, live_qa_enabled, require_plan
from .stripe_live_qa_scenarios import SCENARIOS, Subscriber, _establish_subscriber

logger = logging.getLogger(__name__)

SESSION_KEY = "qa_console_subscriber"


# --------------------------------------------------------------------------
# Guardrails
# --------------------------------------------------------------------------


def _qa_console_enabled() -> bool:
    """Identical gate to what LiveQAHarness itself requires — reusing it
    means there is no way to reach this console without also being able
    to reach every code path it drives."""
    return live_qa_enabled()


def _is_superadmin(user) -> bool:
    """Restates classrooms.permissions.IsSuperAdmin as a plain function:
    these are ordinary Django views (need to render HTML), not DRF
    APIViews, so the DRF permission class itself does not attach here."""
    return bool(
        user
        and user.is_authenticated
        and getattr(user, "user_type", None) == UserTypes.SUPER_ADMIN
        and user.is_superuser
    )


def _qa_console_required(view_func):
    """Combines the feature gate, auth and role check every view in this
    module needs. A bare 404 in every failure case — disabled, not
    logged in, or logged in but not a superadmin — matching
    qa_time_travel.py's "no hint this exists" rationale.

    Deliberately does NOT use django.contrib.auth.decorators.
    login_required: that redirects an unauthenticated request to
    LOGIN_URL, which this API-only project has no reason to have
    configured for a page like this, and a redirect is itself a hint
    the tool exists. _is_superadmin already checks is_authenticated
    (AnonymousUser.is_authenticated is always False), so no separate
    login check is needed."""

    def _wrapped(request, *args, **kwargs):
        if not _qa_console_enabled():
            raise Http404()
        if not _is_superadmin(request.user):
            raise Http404()
        return view_func(request, *args, **kwargs)

    return _wrapped


# --------------------------------------------------------------------------
# Serializers
# --------------------------------------------------------------------------


class ConsoleActionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=list(DEFAULT_ACTIONS))


class RunTriggerSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(
        choices=[LiveQARunKind.SCENARIO, LiveQARunKind.CHAOS]
    )
    scenario_names = serializers.ListField(
        child=serializers.ChoiceField(choices=list(SCENARIOS)), required=False
    )
    tier = serializers.ChoiceField(
        choices=["fast", "deep"], required=False, allow_blank=True
    )
    seed = serializers.IntegerField(required=False)
    steps = serializers.IntegerField(required=False, min_value=1, max_value=200)
    shrink = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        if attrs["kind"] == LiveQARunKind.CHAOS and "seed" not in attrs:
            raise serializers.ValidationError("seed is required for a chaos run.")
        if attrs["kind"] == LiveQARunKind.SCENARIO and not (
            attrs.get("scenario_names") or attrs.get("tier")
        ):
            raise serializers.ValidationError(
                "scenario_names or tier is required for a scenario run — "
                "leaving both empty would silently run every scenario, "
                "including hours-long deep-tier ones."
            )
        return attrs


def _validate_or_400(serializer):
    """serializer.is_valid(raise_exception=True) only produces a clean
    response inside a DRF APIView, which knows how to catch and render
    rest_framework.exceptions.ValidationError. These are plain Django
    views (they render HTML too, which APIView does not do), so that
    exception would otherwise propagate uncaught into Django's own
    error handling and fail confusingly. Returns (validated_data, None)
    on success, or (None, a 400 JsonResponse) on failure — check the
    second element first."""
    if serializer.is_valid():
        return serializer.validated_data, None
    return None, JsonResponse({"errors": serializer.errors}, status=400)


# --------------------------------------------------------------------------
# Result serialization
# --------------------------------------------------------------------------


def _serialize_result(result) -> dict:
    """Every QA-suite result type here (SuiteResult, ChaosWalkResult,
    ShrinkResult, and everything nested inside them) is a plain
    dataclass of JSON-safe fields — no datetimes anywhere in this
    family, all durations are float seconds — so dataclasses.asdict()
    is the whole implementation."""
    return dataclasses.asdict(result)


# --------------------------------------------------------------------------
# Console tab — session-backed, reuses billing.live_qa.chaos directly
# --------------------------------------------------------------------------


def _monthly_plan() -> SubscriptionPlan:
    return require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.MONTHLY)


def _pro_plan() -> SubscriptionPlan:
    return require_plan(tier=PlanTier.PRO, interval=BillingInterval.MONTHLY)


def _annual_plan() -> SubscriptionPlan:
    return require_plan(tier=PlanTier.STANDARD, interval=BillingInterval.ANNUAL)


def _session_subscriber_data(request) -> Optional[dict]:
    return request.session.get(SESSION_KEY)


def _build_context(request, data: dict) -> ChaosContext:
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.get(id=data["local_user_id"])
    sub = Subscriber(
        user=user,
        clock_id=data["clock_id"],
        customer_id=data["customer_id"],
        stripe_subscription_id=data["stripe_subscription_id"],
        plan=_monthly_plan(),
    )
    harness = LiveQAHarness(run_id=data["run_id"])
    # A fresh LiveQAHarness sets started_at to "now - 60s" and
    # dispatched_event_ids to empty, since it's built to live for one
    # whole suite run. The console instead builds a NEW harness on
    # EVERY click, so without restoring these from the session, the
    # event-poll floor silently creeps forward each request -- any
    # renewal event not fully drained within its own click's request
    # (e.g. Stripe hasn't emitted it yet, or more than 60s passed
    # between clicks) falls below the next click's floor and is
    # permanently invisible, leaving local state stuck while Stripe's
    # own subscription keeps renewing correctly on its side.
    # .get() with the pre-fix fallback: a session created before this
    # restore existed has no "started_at" key at all.
    harness.started_at = data.get("started_at", harness.started_at)
    harness.dispatched_event_ids = set(data.get("dispatched_event_ids", []))
    rec = CheckRecorder()
    return ChaosContext(
        harness=harness,
        sub=sub,
        rec=rec,
        monthly_plan=_monthly_plan(),
        pro_plan=_pro_plan(),
        annual_plan=_annual_plan(),
        using_failing_card=data.get("using_failing_card", False),
        cancelled=data.get("cancelled", False),
    )


def _describe_state(sub: Subscriber) -> dict:
    local = sub.local()
    wallet = sub.wallet()
    buckets: list = []
    by_type: list = []
    if wallet is not None:
        from django.db.models import Count, F, Q, Sum

        from .models import CreditBucket

        buckets = [
            {
                "type": b.bucket_type,
                "total_credits": b.total_credits,
                "used_credits": b.used_credits,
                "remaining_credits": b.remaining_credits,
                "expires_at": b.expires_at.isoformat() if b.expires_at else None,
                "is_processed": b.is_processed,
                # is_expired is a plain METHOD on CreditBucket, not a
                # property -- reading it without calling would put a
                # bound method in this dict and blow up JsonResponse.
                "is_expired": b.is_expired(),
                "created_at": b.created_at.isoformat(),
            }
            for b in CreditBucket.objects.filter(wallet=wallet).order_by("-created_at")[
                :10
            ]
        ]

        # The breakdown that makes a wrong total explainable at a glance:
        # "10M MONTHLY + 5M TRIAL" reads as a stacking bug immediately,
        # where a bare 15M total does not. Deliberately aggregated over
        # ALL buckets, not just the 10 most recent shown above -- the
        # subtotals must reconcile against the wallet totals, and a
        # truncated aggregate that silently disagrees would be worse
        # than no breakdown at all.
        #
        # LIVE deliberately restates CreditWallet.total_remaining_credits's
        # filter EXACTLY -- "expires_at is null OR in the future", and
        # notably NOT is_processed, which that aggregate ignores. Any
        # other definition here would make the subtotals disagree with
        # the wallet total for reasons that are an artifact of this
        # console rather than a real billing fault, and the whole point
        # of the breakdown is to explain that total.
        rows = (
            CreditBucket.objects.filter(wallet=wallet)
            .values("bucket_type")
            .annotate(
                bucket_count=Count("id"),
                total=Sum("total_credits"),
                used=Sum("used_credits"),
                remaining=Sum(F("total_credits") - F("used_credits")),
            )
            .order_by("bucket_type")
        )
        live_rows = {
            r["bucket_type"]: r
            for r in CreditBucket.objects.filter(wallet=wallet)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
            .values("bucket_type")
            .annotate(
                bucket_count=Count("id"),
                remaining=Sum(F("total_credits") - F("used_credits")),
            )
        }
        by_type = [
            {
                "type": r["bucket_type"],
                "bucket_count": r["bucket_count"],
                "total_credits": r["total"] or 0,
                "used_credits": r["used"] or 0,
                "remaining_credits": r["remaining"] or 0,
                "live_bucket_count": live_rows.get(r["bucket_type"], {}).get(
                    "bucket_count", 0
                ),
                "live_remaining_credits": (
                    live_rows.get(r["bucket_type"], {}).get("remaining") or 0
                ),
            }
            for r in rows
        ]
    return {
        "customer_id": sub.customer_id,
        "stripe_subscription_id": sub.stripe_subscription_id,
        "clock_id": sub.clock_id,
        "local_subscription": (
            {
                "plan": local.plan.name,
                "status": local.stripe_status,
                "is_active": local.is_active,
                "billing_cycle_start": local.billing_cycle_start.isoformat(),
                "billing_cycle_end": local.billing_cycle_end.isoformat(),
                "pending_plan": local.pending_plan.name if local.pending_plan else None,
            }
            if local is not None
            else None
        ),
        "wallet": (
            {
                "total_remaining_credits": wallet.total_remaining_credits(),
                # Excludes OVERAGE buckets -- see plan_remaining_credits's
                # own docstring. Differs from total_remaining_credits
                # whenever an overage block or a leftover TRIAL bucket
                # (see the forfeiture step in activate_subscription) is
                # present, which is exactly the distinction worth seeing
                # in this console.
                "plan_remaining_credits": wallet.plan_remaining_credits(),
                "overage_blocks_used": wallet.overage_blocks_used,
                # Per-bucket-type subtotals over EVERY bucket, live and
                # historical -- "buckets" below is only the 10 most
                # recent, so it cannot be summed to reconcile a total.
                "by_type": by_type,
                "buckets": buckets,
            }
            if wallet is not None
            else None
        ),
    }


def _teardown_session_subscriber(data: dict) -> list:
    harness = LiveQAHarness(run_id=data["run_id"])
    harness.clock_ids = [data["clock_id"]]
    harness.local_user_ids = [data["local_user_id"]]
    harness.dispatched_event_ids = set(data.get("dispatched_event_ids", []))
    return harness.cleanup()


@_qa_console_required
def qa_console_page(request):
    return render(
        request,
        "billing/qa_console.html",
        {
            "actions": sorted(DEFAULT_ACTIONS),
            "scenarios": sorted(SCENARIOS),
        },
    )


@_qa_console_required
@require_GET
def qa_console_state(request):
    """Read current state. `?drain=1` additionally pulls and dispatches
    any Stripe webhook events outstanding for this customer.

    That flag is what makes the page's auto-refresh worth anything.
    Local state only moves when a webhook is DISPATCHED, so a poll that
    merely re-read the database would sit frozen after a test-clock
    advance while Stripe had already renewed on its side -- the exact
    stale-state symptom this console was reported for. A plain (undrained)
    read stays the default because it touches no network at all, which is
    what a manual "Refresh state" click and every test wants.

    Draining is a WRITE despite living on a GET: dispatched event ids
    must be persisted back to the session or the next poll re-dispatches
    the same events. Redundant dispatch is caught downstream by
    _claim_stripe_event's idempotency, so a lost session write degrades
    to wasted work rather than double-billing -- but only if we actually
    try to persist it, which is why this is not fire-and-forget.
    """
    data = _session_subscriber_data(request)
    if data is None:
        return JsonResponse({"subscriber": None, "drained": 0})

    ctx = _build_context(request, data)
    drained = 0
    drain_error = None
    if request.GET.get("drain") == "1":
        try:
            drained = len(ctx.harness.drain_events(customer_id=ctx.sub.customer_id))
        except Exception as exc:  # noqa: BLE001
            # A polling loop must not turn one transient Stripe blip into
            # a dead page. Report it in-band so the UI can surface it and
            # keep polling, rather than 500ing and killing the timer.
            drain_error = repr(exc)
            logger.warning("[qa-console] auto-refresh drain failed: %s", drain_error)
        else:
            data["dispatched_event_ids"] = sorted(
                set(data.get("dispatched_event_ids", []))
                | ctx.harness.dispatched_event_ids
            )
            request.session[SESSION_KEY] = data
            request.session.modified = True

    return JsonResponse(
        {
            "subscriber": _describe_state(ctx.sub),
            "drained": drained,
            "drain_error": drain_error,
        }
    )


@_qa_console_required
@require_http_methods(["POST"])
def qa_console_new_subscriber(request):
    existing = _session_subscriber_data(request)
    if existing is not None:
        errors = _teardown_session_subscriber(existing)
        for error in errors:
            logger.warning("[qa-console] teardown before new subscriber: %s", error)

    harness = LiveQAHarness()
    rec = CheckRecorder()
    plan = _monthly_plan()
    sub = _establish_subscriber(harness, rec, plan=plan, label="console")
    harness.drain_events(customer_id=sub.customer_id)

    request.session[SESSION_KEY] = {
        "run_id": harness.run_id,
        # Captured once, here, and restored into every later
        # reconstructed harness by _build_context -- see the comment
        # there for why this must NOT be recomputed per request.
        "started_at": harness.started_at,
        # Subscriber.user is typed as `object` (it is a cross-app,
        # optional field shared with every other scenario in this
        # package) -- getattr rather than a direct attribute read keeps
        # that loose typing honest instead of asserting a narrower type
        # here alone.
        "local_user_id": str(getattr(sub.user, "id")),  # noqa: B009
        "clock_id": sub.clock_id,
        "customer_id": sub.customer_id,
        "stripe_subscription_id": sub.stripe_subscription_id,
        "using_failing_card": False,
        "cancelled": False,
        "dispatched_event_ids": list(harness.dispatched_event_ids),
    }
    request.session.modified = True

    return JsonResponse(
        {"subscriber": _describe_state(sub), "checks": [str(c) for c in rec.checks]}
    )


@_qa_console_required
@require_http_methods(["POST"])
def qa_console_action(request):
    data = _session_subscriber_data(request)
    if data is None:
        return JsonResponse(
            {"error": "No test subscriber yet — create one first."}, status=400
        )

    serializer = ConsoleActionSerializer(data=_json_body(request))
    validated, error_response = _validate_or_400(serializer)
    if error_response is not None:
        return error_response
    action_name = validated["action"]

    ctx = _build_context(request, data)
    fn = DEFAULT_ACTIONS[action_name][1]
    try:
        note = fn(ctx)
    except Exception as exc:  # noqa: BLE001 - a real action failing IS the answer
        note = f"RAISED: {exc!r}"
        logger.exception("[qa-console] action %s raised.", action_name)

    data["using_failing_card"] = ctx.using_failing_card
    data["cancelled"] = ctx.cancelled
    data["dispatched_event_ids"] = sorted(
        set(data.get("dispatched_event_ids", [])) | ctx.harness.dispatched_event_ids
    )
    request.session[SESSION_KEY] = data
    request.session.modified = True

    return JsonResponse(
        {"action": action_name, "note": note, "subscriber": _describe_state(ctx.sub)}
    )


@_qa_console_required
@require_http_methods(["POST"])
def qa_console_reset(request):
    data = _session_subscriber_data(request)
    errors: list = []
    if data is not None:
        errors = _teardown_session_subscriber(data)
        del request.session[SESSION_KEY]
        request.session.modified = True
    return JsonResponse({"cleanup_errors": errors})


def _json_body(request) -> dict:
    import json

    if not request.body:
        return {}
    return json.loads(request.body.decode("utf-8"))


# --------------------------------------------------------------------------
# Dashboard tab — background Celery runs, persisted in LiveQARun
# --------------------------------------------------------------------------


@_qa_console_required
@require_http_methods(["POST"])
def qa_console_runs_create(request):
    serializer = RunTriggerSerializer(data=_json_body(request))
    payload, error_response = _validate_or_400(serializer)
    if error_response is not None:
        return error_response

    run = LiveQARun.objects.create(
        kind=payload["kind"],
        scenario_names=payload.get("scenario_names") or [],
        tier=payload.get("tier") or "",
        seed=payload.get("seed"),
        steps=payload.get("steps"),
        shrink=payload.get("shrink", False),
        triggered_by=request.user,
    )

    from .tasks import run_live_qa_console_job

    async_result = run_live_qa_console_job.delay(str(run.id))
    run.celery_task_id = async_result.id
    run.save(update_fields=["celery_task_id", "updated_at"])

    return JsonResponse({"run_id": str(run.id)}, status=201)


@_qa_console_required
@require_GET
def qa_console_runs_list(request):
    runs = LiveQARun.objects.all()[:50]
    return JsonResponse(
        {
            "runs": [
                {
                    "id": str(r.id),
                    "kind": r.kind,
                    "status": r.status,
                    "summary": r.summary,
                    "created_at": r.created_at.isoformat(),
                    "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                }
                for r in runs
            ]
        }
    )


@_qa_console_required
@require_GET
def qa_console_runs_detail(request, run_id):
    from django.shortcuts import get_object_or_404

    run = get_object_or_404(LiveQARun, id=run_id)
    return JsonResponse(
        {
            "id": str(run.id),
            "kind": run.kind,
            "status": run.status,
            "summary": run.summary,
            "result_data": run.result_data,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        }
    )
