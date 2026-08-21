"""
billing/stripe_live_qa.py
=========================
Safety guards and object lifecycle for the nightly REAL-Stripe QA suite.

WHY THIS EXISTS
---------------
Every other test in this codebase mocks Stripe, which means they encode
our BELIEFS about Stripe's API rather than its behaviour. C1 is the proof
that beliefs go stale silently: `current_period_end` moved off the
Subscription object onto `items.data[]` in API version 2025-03-31, the
QA time-travel endpoint broke, and hundreds of passing tests could not
see it — because every one of them mocked the shape we expected.

This module drives REAL Stripe test-mode objects through REAL billing
code, so a shape change breaks a nightly run instead of a customer.

WHAT IT DOES NOT COVER
----------------------
Stripe Checkout completion cannot be automated: `checkout.session` hands
back a URL that requires a browser to pay. So the suite creates
subscriptions with `stripe.Subscription.create` and establishes the local
row the way the checkout webhook does, then exercises everything
downstream (renewal, trial conversion, upgrade, deferred downgrade,
dunning). The checkout hop itself remains covered only by mocks. That is
a real gap and is stated here rather than papered over.

HARD GUARDRAILS
---------------
This module CREATES objects in Stripe. Unlike the read-mostly time-travel
endpoint, a mistake here writes. Every guard is independently sufficient
to block execution:

  1. `settings.ENABLE_STRIPE_LIVE_QA` must be explicitly True.
  2. BOTH `stripe.api_key` AND `settings.STRIPE_SECRET_KEY` must be
     `sk_test_` keys. An empty or unrecognised key is REFUSED, not
     assumed safe — "no key" must never read as "not live".
  3. Every single Stripe call routes through `guarded_call`, which
     re-checks (2) immediately before the call. There is deliberately no
     code path in this module that reaches Stripe without that check, so
     a key swapped mid-run cannot be raced.

Guard (3) is the important one. A single import-time assertion would be
cheap to defeat: settings can be reloaded, `stripe.api_key` is a plain
module attribute anyone can reassign. Re-asserting per call costs one
string comparison and removes the entire class of "it was test mode when
we started" bugs.

CLEANUP
-------
Deleting a Test Clock deletes every object attached to it, so clock
deletion is the primary cleanup and customer deletion is belt-and-braces.

Cleanup also deletes the `StripeEvent` ledger rows this run created. That
is not tidiness: a QA event left in FAILED state would be picked up by
`sweep_stale_stripe_events` three days later and logged at ERROR as
"a customer may have paid without receiving anything" — a false page
about a customer who never existed.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Any, Callable, Optional

from django.conf import settings

from users.models import CustomUser, UserTypes

from .imports import stripe
from .models import (
    BillingInterval,
    PlanCategory,
    StripeEvent,
    SubscriptionPlan,
    UserSubscription,
)

logger = logging.getLogger(__name__)

# Stamped into the metadata of every Stripe object this suite creates, so
# a human (or a cleanup sweep) can always tell QA wreckage from real data.
LIVE_QA_METADATA_KEY = "gradeaplus_live_qa"

# Shared Stripe test-mode PaymentMethod tokens. These are Stripe-provided
# constants, not secrets, and only resolve in test mode.
CARD_OK = "pm_card_visa"
# Attaches cleanly, then fails when actually charged — which is exactly
# the shape of a real expired/declined card at renewal time. A card that
# failed at attach time could not model dunning at all.
CARD_FAILS_ON_CHARGE = "pm_card_chargeCustomerFail"

# RFC 2606 reserves `.invalid` as a TLD guaranteed never to resolve, so a
# QA user address can never receive mail even if some code path tries.
DEFAULT_QA_EMAIL_DOMAIN = "stripe-live-qa.invalid"


class LiveQARefused(RuntimeError):
    """A guardrail blocked execution. Never catch this to continue."""


class LiveQAConfigurationError(RuntimeError):
    """The environment cannot support a run (missing plans, old SDK)."""


class LiveQATimeout(RuntimeError):
    """Stripe did not reach the expected state within the budget."""


class LiveQAInfrastructureError(RuntimeError):
    """The harness itself broke — a dead event poller, an exhausted event
    cursor, a persistent 429.

    Deliberately distinct from a scenario failure. A nightly run that
    reports "billing is broken" when really the poller thread died sends
    someone hunting a bug that does not exist, and the next real failure
    is trusted less for it.
    """


# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------


def _is_test_key(key: Any) -> bool:
    """
    Only an explicit `sk_test_` prefix counts. Empty, None, live, and
    anything unrecognised are all False — the caller REFUSES on False, so
    the default answer to "is this safe?" must be no.
    """
    return bool(key) and str(key).startswith("sk_test_")


def assert_test_mode(operation: str = "") -> None:
    """
    Refuse to touch Stripe unless every key in play is a test key.

    Checks the runtime key AND the configured key. They should agree, but
    if they ever diverge the safe reading is "something is wrong", not
    "pick the more convenient one".

    Deliberately never includes a key value in the error — only which
    source failed. An exception message ends up in logs and tickets.
    """
    for label, key in (
        ("stripe.api_key", getattr(stripe, "api_key", "")),
        ("settings.STRIPE_SECRET_KEY", getattr(settings, "STRIPE_SECRET_KEY", "")),
    ):
        if not _is_test_key(key):
            raise LiveQARefused(
                f"Stripe live-QA refused{f' for {operation}' if operation else ''}: "
                f"{label} is not a test key (expected an 'sk_test_' prefix). "
                "This suite CREATES Stripe objects and will never run against "
                "a live or unrecognised key."
            )


def live_qa_enabled() -> bool:
    """Both the explicit toggle and test-mode keys, independently."""
    if not getattr(settings, "ENABLE_STRIPE_LIVE_QA", False):
        return False
    try:
        assert_test_mode()
    except LiveQARefused:
        return False
    return True


def assert_live_qa_enabled() -> None:
    if not getattr(settings, "ENABLE_STRIPE_LIVE_QA", False):
        raise LiveQARefused(
            "Stripe live-QA refused: settings.ENABLE_STRIPE_LIVE_QA is not "
            "True. This suite creates real Stripe objects and real local "
            "users, so it must be switched on deliberately."
        )
    assert_test_mode("suite start")


def qa_email_domain() -> str:
    domain = getattr(settings, "STRIPE_LIVE_QA_EMAIL_DOMAIN", "") or ""
    return str(domain).strip().lstrip("@").lower() or DEFAULT_QA_EMAIL_DOMAIN


# Optional account-wide throttle, installed by the concurrent runner.
# It belongs HERE rather than in the worker pool because guarded_call is
# already established — and test-enforced — as the only path to Stripe,
# which makes the throttle unbypassable for free.
_rate_limiter = None


def set_rate_limiter(limiter) -> None:
    """Install (or clear, with None) the process-wide Stripe throttle."""
    global _rate_limiter
    _rate_limiter = limiter


def guarded_call(fn: Callable, /, *args, **kwargs):
    """
    The ONLY way this suite is allowed to reach Stripe.

    Re-asserts test mode immediately before every call. See the module
    docstring for why this is per-call rather than once at import.
    """
    # Order matters: refuse FIRST, throttle second. A call that must not
    # happen must not consume a rate-limit token or block on one.
    assert_test_mode(getattr(fn, "__qualname__", None) or repr(fn))
    limiter = _rate_limiter
    if limiter is not None:
        limiter.acquire()
    return fn(*args, **kwargs)


# --------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------


@dataclass
class Check:
    """One assertion. `detail` must carry the observed values — a nightly
    failure is read hours later by someone without the objects in hand."""

    name: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        return f"{'PASS' if self.passed else 'FAIL'} {self.name}: {self.detail}"


@dataclass
class ScenarioResult:
    name: str
    passed: bool = False
    checks: list = field(default_factory=list)
    error: str = ""
    duration_seconds: float = 0.0

    @property
    def failed_checks(self) -> list:
        return [c for c in self.checks if not c.passed]


@dataclass
class SuiteResult:
    run_id: str
    scenarios: list = field(default_factory=list)
    cleanup_errors: list = field(default_factory=list)
    # Outcomes that are neither pass nor fail: "reached Stripe's test-clock
    # ceiling at simulated year 3.4", "stopped at the wall-clock budget".
    # These must never turn a run red — they are facts about how far the
    # run got, not defects.
    notes: list = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.scenarios) and not self.cleanup_errors

    @property
    def failed_scenarios(self) -> list:
        return [s for s in self.scenarios if not s.passed]

    def summary(self) -> str:
        total = len(self.scenarios)
        failed = len(self.failed_scenarios)
        return (
            f"Stripe live QA run {self.run_id}: "
            f"{total - failed}/{total} scenarios passed, "
            f"{failed} failed, {len(self.cleanup_errors)} cleanup error(s)."
        )


class CheckRecorder:
    """Collects checks for one scenario. `expect` never raises, so a
    scenario reports EVERY failed assertion in one run rather than only
    the first — a nightly job you have to re-run to see the second
    problem wastes a whole day per bug."""

    def __init__(self) -> None:
        self.checks: list = []

    def expect(self, name: str, condition: bool, detail: str = "") -> bool:
        self.checks.append(Check(name=name, passed=bool(condition), detail=detail))
        return bool(condition)

    def expect_equal(self, name: str, actual, expected, detail: str = "") -> bool:
        ok = actual == expected
        suffix = f" ({detail})" if detail else ""
        return self.expect(name, ok, f"expected {expected!r}, got {actual!r}{suffix}")

    def expect_close(
        self, name: str, actual, expected, tolerance_seconds: int = 120
    ) -> bool:
        """Datetime comparison with tolerance. Stripe timestamps are
        whole seconds and our local write happens a moment later, so
        exact equality would be flaky for reasons that are not bugs."""
        if actual is None or expected is None:
            return self.expect(
                name, False, f"expected ~{expected!r}, got {actual!r} (None)"
            )
        delta = abs((actual - expected).total_seconds())
        return self.expect(
            name,
            delta <= tolerance_seconds,
            f"expected ~{expected.isoformat()}, got {actual.isoformat()} "
            f"(delta {delta:.0f}s, tolerance {tolerance_seconds}s)",
        )

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


# --------------------------------------------------------------------------
# Environment lookups
# --------------------------------------------------------------------------


def require_plan(*, tier, interval=BillingInterval.MONTHLY) -> SubscriptionPlan:
    """
    Find a real, Stripe-wired plan. Never creates one: a plan whose
    `stripe_price_id` does not exist in the Stripe test account would
    fail deep inside a scenario with an opaque Stripe error, so the
    check belongs here with a message that says what to fix.

    `tier` and `interval` are deliberately unannotated: callers pass
    Django TextChoices members, which mypy types as tuple[str, Any]
    rather than str, so a `str` annotation would produce noise at every
    call site without catching anything real.
    """
    plan = (
        SubscriptionPlan.objects.filter(
            category=PlanCategory.INDIVIDUAL,
            tier=tier,
            interval=interval,
            is_active=True,
        )
        .exclude(stripe_price_id__isnull=True)
        .exclude(stripe_price_id="")
        .order_by("price_cents")
        .first()
    )
    if plan is None:
        raise LiveQAConfigurationError(
            f"No active INDIVIDUAL {tier}/{interval} plan with a "
            "stripe_price_id exists. The live-QA suite needs real plans "
            "wired to real Stripe test prices — seed them before running."
        )
    return plan


def _require_test_clock_support() -> None:
    if not (
        hasattr(stripe, "test_helpers") and hasattr(stripe.test_helpers, "TestClock")
    ):
        raise LiveQAConfigurationError(
            "The installed stripe library does not expose "
            "stripe.test_helpers.TestClock, which the live-QA suite is "
            "built on. Upgrade the stripe package."
        )


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


class LiveQAHarness:
    """
    Owns every object a run creates, in Stripe and locally, and knows how
    to destroy them. Scenarios never call Stripe directly — they go
    through here so that tracking (and therefore cleanup) cannot be
    forgotten.
    """

    # A clock advance is asynchronous and Stripe re-bills every attached
    # subscription while it runs, so this is generous by design.
    CLOCK_READY_TIMEOUT = 300
    CLOCK_POLL_INTERVAL = 3
    CLOCK_POLL_MAX_INTERVAL = 15

    # Events appear a beat after the objects that caused them. "Drain
    # until quiet" rather than "sleep N seconds": we stop when Stripe
    # stops producing, instead of guessing how long that takes.
    EVENT_DRAIN_TIMEOUT = 120
    EVENT_POLL_INTERVAL = 3
    EVENT_QUIET_POLLS = 2
    MAX_EVENT_PAGES = 10

    def __init__(self, run_id: Optional[str] = None) -> None:
        assert_live_qa_enabled()
        _require_test_clock_support()

        self.run_id = run_id or uuid.uuid4().hex[:12]
        # `created.gte` floor for event polling. One minute of slack
        # absorbs clock skew between this host and Stripe; without it a
        # slightly-fast local clock silently filters out our own events.
        self.started_at = int(time.time()) - 60

        self.clock_ids: list = []
        self.customer_ids: list = []
        self.local_user_ids: list = []
        self.dispatched_event_ids: set = set()

    # -- metadata ---------------------------------------------------------

    def metadata(self, extra: Optional[dict] = None) -> dict:
        meta = {
            LIVE_QA_METADATA_KEY: "1",
            "live_qa_run_id": self.run_id,
        }
        if extra:
            meta.update({k: str(v) for k, v in extra.items()})
        return meta

    # -- Stripe object lifecycle -----------------------------------------

    def create_test_clock(self, label: str):
        clock = guarded_call(
            stripe.test_helpers.TestClock.create,
            frozen_time=int(time.time()),
            name=f"live-qa {self.run_id} {label}"[:250],
        )
        clock_id = clock["id"]
        self.clock_ids.append(clock_id)
        logger.info("[LIVE QA %s] Created test clock %s.", self.run_id, clock_id)
        return clock

    def create_customer(self, *, email: str, clock_id: str):
        customer = guarded_call(
            stripe.Customer.create,
            email=email,
            test_clock=clock_id,
            metadata=self.metadata({"email": email}),
        )
        self.customer_ids.append(customer["id"])
        return customer

    def attach_card(self, *, customer_id: str, token: str, set_default: bool = True):
        payment_method = guarded_call(
            stripe.PaymentMethod.attach, token, customer=customer_id
        )
        if set_default:
            guarded_call(
                stripe.Customer.modify,
                customer_id,
                invoice_settings={"default_payment_method": payment_method["id"]},
            )
        return payment_method

    def create_subscription(self, *, customer_id: str, price_id: str, **extra):
        params = {
            "customer": customer_id,
            "items": [{"price": price_id}],
            "metadata": self.metadata(),
            # Without this, a subscription whose first invoice cannot be
            # paid sits in `incomplete` forever and the dunning scenario
            # has nothing to observe.
            "payment_behavior": "allow_incomplete",
            "expand": ["latest_invoice"],
        }
        params.update(extra)
        return guarded_call(stripe.Subscription.create, **params)

    def retrieve_subscription(self, subscription_id: str):
        return guarded_call(stripe.Subscription.retrieve, subscription_id)

    # -- time -------------------------------------------------------------

    def advance_clock_to(self, clock_id: str, to_timestamp: int):
        """
        Advance and BLOCK until Stripe finishes re-billing. Returning
        before `ready` would let a scenario assert against a half-applied
        state and fail for a reason that is not a bug.
        """
        to_timestamp = int(to_timestamp)
        logger.info(
            "[LIVE QA %s] Advancing clock %s to %s.",
            self.run_id,
            clock_id,
            datetime.fromtimestamp(to_timestamp, tz=dt_timezone.utc).isoformat(),
        )
        guarded_call(
            stripe.test_helpers.TestClock.advance,
            clock_id,
            frozen_time=to_timestamp,
        )
        return self._wait_for_clock_ready(clock_id)

    def _wait_for_clock_ready(self, clock_id: str):
        deadline = time.monotonic() + self.CLOCK_READY_TIMEOUT
        last_status = None
        # Exponential backoff rather than a flat interval: with a dozen
        # actors advancing clocks concurrently, flat polling turns into a
        # steady stream of retrieves that eats the account's rate limit
        # while telling us nothing new.
        interval = float(self.CLOCK_POLL_INTERVAL)
        while time.monotonic() < deadline:
            clock = guarded_call(stripe.test_helpers.TestClock.retrieve, clock_id)
            last_status = clock.get("status")
            if last_status == "ready":
                return clock
            if last_status == "internal_failure":
                raise LiveQATimeout(
                    f"Stripe test clock {clock_id} reported internal_failure "
                    "while advancing. This is a Stripe-side fault, not a "
                    "billing bug — re-run before investigating our code."
                )
            time.sleep(interval)
            interval = min(interval * 1.6, self.CLOCK_POLL_MAX_INTERVAL)
        raise LiveQATimeout(
            f"Stripe test clock {clock_id} did not become ready within "
            f"{self.CLOCK_READY_TIMEOUT}s (last status: {last_status})."
        )

    # -- events -----------------------------------------------------------

    def drain_events(self, *, customer_id: str) -> list:
        """
        Poll Stripe for events belonging to `customer_id` and push each
        through the REAL webhook path exactly once.

        We poll rather than receive real webhook deliveries on purpose:
        delivery needs a publicly reachable URL, which a nightly job in
        CI does not have. What matters is that OUR HANDLERS correctly
        interpret REAL Stripe payloads, and polling exercises that fully
        — including the C3 idempotency ledger, since dispatch goes
        through `_record_and_dispatch` rather than calling handlers
        directly. Signature verification is the one part this cannot
        cover; it is unit-tested separately.
        """
        # Imported here, not at module scope: webhooks imports the
        # service layer, and keeping this local avoids adding a new
        # import edge into a module graph that billing/tasks.py already
        # pulls from both directions.
        from .webhooks import _record_and_dispatch

        log_prefix = f"[LIVE QA {self.run_id}]"
        deadline = time.monotonic() + self.EVENT_DRAIN_TIMEOUT
        dispatched: list = []
        quiet_polls = 0

        while time.monotonic() < deadline and quiet_polls < self.EVENT_QUIET_POLLS:
            new_events = self._fetch_new_events(customer_id)
            if not new_events:
                quiet_polls += 1
            else:
                quiet_polls = 0
                for event in new_events:
                    # Mark BEFORE dispatching. If the handler raises, the
                    # event must not be retried by the next poll — the
                    # ledger row already records the failure, and a
                    # silent retry loop here would mask it.
                    self.dispatched_event_ids.add(event["id"])
                    response = _record_and_dispatch(event, log_prefix=log_prefix)
                    dispatched.append((event["type"], response.status_code))
                    logger.info(
                        "%s dispatched %s (%s) -> %s",
                        log_prefix,
                        event["id"],
                        event["type"],
                        response.status_code,
                    )
            time.sleep(self.EVENT_POLL_INTERVAL)

        return dispatched

    def _fetch_new_events(self, customer_id: str) -> list:
        """
        Undispatched events for one customer, oldest first.

        Stripe cannot filter events by customer, so we window by
        `created` and filter client-side. Ordering matters: handlers
        assume they see `customer.subscription.created` before the
        invoice that follows it, and Stripe returns newest-first.
        """
        collected: list = []
        starting_after = None
        hit_page_cap = True

        for _ in range(self.MAX_EVENT_PAGES):
            params: dict = {"created": {"gte": self.started_at}, "limit": 100}
            if starting_after:
                params["starting_after"] = starting_after
            page = guarded_call(stripe.Event.list, **params)
            data = page.get("data") or []
            collected.extend(data)
            if not data or not page.get("has_more"):
                hit_page_cap = False
                break
            starting_after = data[-1]["id"]

        if hit_page_cap:
            logger.warning(
                "[LIVE QA %s] Hit the %d-page event scan cap; some events may "
                "not have been seen. If this recurs, the Stripe test account "
                "is too busy for a shared-account run.",
                self.run_id,
                self.MAX_EVENT_PAGES,
            )

        mine = [
            event
            for event in collected
            if _event_customer_id(event) == customer_id
            and event["id"] not in self.dispatched_event_ids
        ]
        # Ascending, id as a deterministic tiebreak for same-second events.
        mine.sort(key=lambda e: (e.get("created") or 0, e["id"]))
        return mine

    # -- local objects ----------------------------------------------------

    def create_local_user(self, label: str) -> CustomUser:
        """
        A real local user on a non-routable domain.

        Registration signals auto-activate a free trial for TEACHERs, so
        we clear any subscription rows they create — every scenario needs
        to start from a state it chose, not one a signal chose for it.
        """
        email = f"liveqa-{self.run_id}-{label}@{qa_email_domain()}"
        user = CustomUser.objects.create_user(
            email=email,
            password=uuid.uuid4().hex,  # nosec B106 - random, never used to log in
            user_type=UserTypes.TEACHER,
        )
        self.local_user_ids.append(user.id)

        removed, _ = UserSubscription.objects.filter(user=user).delete()
        if removed:
            logger.debug(
                "[LIVE QA %s] Cleared %d signal-created subscription row(s) for %s.",
                self.run_id,
                removed,
                email,
            )
        return user

    # -- teardown ---------------------------------------------------------

    def cleanup(self) -> list:
        """
        Best-effort teardown. Never raises: a cleanup failure must not
        mask the scenario result that preceded it. Returns the errors so
        the runner can report them — leaked Stripe objects and leaked
        ledger rows both need a human eventually.
        """
        errors: list = []

        # Ledger rows first — these are the ones that cause a FALSE PAGE
        # if left behind (see module docstring), so they matter more than
        # the Stripe objects.
        if self.dispatched_event_ids:
            try:
                deleted, _ = StripeEvent.objects.filter(
                    stripe_event_id__in=self.dispatched_event_ids
                ).delete()
                logger.info(
                    "[LIVE QA %s] Removed %d QA StripeEvent ledger row(s).",
                    self.run_id,
                    deleted,
                )
            except Exception as exc:  # noqa: BLE001 - teardown must not raise
                errors.append(f"StripeEvent cleanup failed: {exc!r}")
                logger.exception(
                    "[LIVE QA %s] StripeEvent cleanup failed.", self.run_id
                )

        for user_id in self.local_user_ids:
            try:
                CustomUser.objects.filter(id=user_id).delete()
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Local user {user_id} cleanup failed: {exc!r}")
                logger.exception(
                    "[LIVE QA %s] Failed to delete local user %s.",
                    self.run_id,
                    user_id,
                )

        # Deleting a clock cascades to its customers, subscriptions and
        # invoices, so this is the real cleanup.
        for clock_id in self.clock_ids:
            try:
                guarded_call(stripe.test_helpers.TestClock.delete, clock_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Test clock {clock_id} cleanup failed: {exc!r}")
                logger.exception(
                    "[LIVE QA %s] Failed to delete test clock %s.",
                    self.run_id,
                    clock_id,
                )

        # Belt-and-braces: a customer created before its clock was
        # tracked would otherwise survive. Already-deleted is not an
        # error worth reporting.
        for customer_id in self.customer_ids:
            try:
                guarded_call(stripe.Customer.delete, customer_id)
            except stripe.error.InvalidRequestError:
                pass
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Customer {customer_id} cleanup failed: {exc!r}")

        return errors


def _event_customer_id(event) -> Optional[str]:
    """
    The customer an event concerns, or None.

    Every type the suite cares about (invoice.*, customer.subscription.*,
    payment_intent.*, charge.*) carries `customer` on its data object,
    which is why filtering on it is sufficient here.
    """
    try:
        obj = event["data"]["object"]
    except (KeyError, TypeError):
        return None
    customer = obj.get("customer") if hasattr(obj, "get") else None
    if isinstance(customer, dict):
        return customer.get("id")
    return customer
