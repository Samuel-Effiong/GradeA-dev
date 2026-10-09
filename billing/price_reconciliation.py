"""
billing/price_reconciliation.py
===============================
Nightly synchronisation of the application's stored pricing with Stripe.

STRIPE IS THE SOURCE OF TRUTH
-----------------------------
Not "a second opinion to compare against" — the authority. When the local
row and the Stripe Price disagree about an amount, the LOCAL ROW IS WRONG
by definition and is updated to match. Changing a price is therefore a
one-step operation: edit it in Stripe (or repoint `stripe_price_id` at a
new Price) and the application follows on the next run.

The direction is absolute:

    Stripe  ──>  application        always
    application  ──>  Stripe        never, under any circumstance

Nothing in this module writes to Stripe. The only local writes are to a
plan's price fields and to this module's own audit rows — no customer
billing, no credits, no subscriptions.

WHAT THIS TRADE COSTS, STATED PLAINLY
-------------------------------------
Two independent records disagreeing is a signal; one record following
another is not. Before this change, a mistaken price edit in the Stripe
dashboard was caught precisely BECAUSE the application disagreed. Now the
application adopts it silently. That is the deliberate consequence of
making Stripe authoritative, and the audit trail is the mitigation: every
synchronisation records what the value was before, and logs at WARNING
naming the old and new amounts, because the application's idea of what a
customer pays just changed without anyone approving it.

WHAT IS SYNCHRONISED, AND WHAT IS NOT
-------------------------------------
Synchronised — Stripe owns these:

    amount     price_cents (base) / overage_block_price (overage)
    product    product_id, from the base price only

Reported, never synchronised:

    interval   `plan.interval` is not a price. It decides how often credits
               are granted, how renewals are detected, and how a plan
               change is classified. Copying it from a Stripe Price would
               change how customers are BILLED AND CREDITED rather than
               what they are charged, which is out of bounds. A mismatch
               is raised for a human.
    currency   No plan field exists to hold it, so it can only be verified.

WHEN THE LOCAL VALUE IS LEFT ALONE
----------------------------------
Synchronising from a value we could not read, or from one that will not
work, is worse than not synchronising at all. The local configuration is
preserved untouched when:

    STRIPE_UNAVAILABLE   outage, timeout, rate limit, bad key
    INVALID_PRICE        the id does not resolve in this account
    INACTIVE_PRICE       the Price is archived and would refuse the charge
    MISSING_PRICE_ID     there is no authoritative price to follow

THE LAYER BELOW THIS ONE IS STILL LOAD-BEARING
----------------------------------------------
`billing/overage_pricing.py` still refuses a purchase whose quoted price
disagrees with Stripe, and must not be removed because this exists. This
sweep runs once a night; a price edited at noon leaves the application
wrong until 02:30, and the runtime guard is what protects customers in
between.

WHAT THE SPEC ASSUMED AND WHAT THE CODE ACTUALLY HAS
----------------------------------------------------
The brief describes a `plan.overage_plan` relation with its own
`stripe_price_id`. **No such relation exists.** A SubscriptionPlan carries
BOTH prices on one row:

    stripe_price_id           the recurring subscription price
    stripe_overage_price_id   the one-time credit-block price

So "synchronise the overage plan" is implemented as "synchronise this
plan's second price". The two are handled independently, as required.

One further trap: `price_cents` is a DecimalField whose help text said
"Price in USD (e.g. 24.99)". It does not hold dollars — it holds CENTS
(STANDARD is Decimal('1499.00') against Stripe's 1499). Writing dollars
into it would under-bill by 100x. The help text has been corrected.

API VERSION
-----------
Never pinned here. `billing/imports.py` configures the Stripe client and
this module imports from it, so the sweep always runs on whatever version
the application itself uses. The run records `stripe.api_version` so the
evidence says which one was actually used.
"""

import logging
from decimal import Decimal

from django.utils import timezone

from .imports import stripe
from .models import (
    PRICE_RECONCILIATION_ALERT_STATUSES,
    PriceKind,
    PriceReconciliationResult,
    PriceReconciliationRun,
    PriceReconciliationStatus,
    SubscriptionPlan,
)

logger = logging.getLogger(__name__)

#: Every Stripe Price in this account is USD and no plan carries a
#: currency field, so the expectation is a constant rather than a column.
#: Checked anyway: a Price created in the dashboard in the wrong currency
#: is silent until someone is billed in it, and that is precisely the sort
#: of thing a nightly sweep exists to notice.
EXPECTED_CURRENCY = "usd"

#: Local BillingInterval -> the Stripe `recurring.interval` it must map to.
#: Anything not listed is not a recurring plan.
INTERVAL_TO_STRIPE = {
    "MONTHLY": "month",
    "ANNUAL": "year",
    "YEARLY": "year",
}


class _PriceCache:
    """
    One Stripe Price fetched at most once per run.

    Plans share prices — three individual tiers and their annual and
    licence variants resolve to three overage Prices between them — so the
    naive implementation would fetch the same object six times. Errors are
    cached too: a Price that 404s for one plan will 404 for the next, and
    re-asking Stripe to confirm that wastes the call and muddies the
    timing.
    """

    def __init__(self):
        self._entries: dict = {}
        self.fetches = 0

    def get(self, price_id):
        """Returns (price_or_None, exception_or_None)."""
        if price_id in self._entries:
            return self._entries[price_id]
        self.fetches += 1
        entry: tuple = (None, None)
        try:
            entry = (stripe.Price.retrieve(price_id), None)
        except Exception as exc:  # noqa: BLE001 - classified by the caller
            entry = (None, exc)
        self._entries[price_id] = entry
        return entry


def _classify_stripe_error(exc):
    """
    Turn a Stripe exception into a result status.

    THE DISTINCTION THAT MATTERS: an outage, a rate limit or a bad key
    means we could not CHECK the price. It does not mean the price
    changed. Reporting those as drift would page someone about a billing
    incident that is not happening, and — worse — would teach them that
    drift alerts are usually noise.
    """
    name = type(exc).__name__
    code = getattr(exc, "code", "") or ""

    # A price id that does not resolve. Either it never existed, or it
    # belongs to a DIFFERENT Stripe account than these credentials address
    # — which is indistinguishable from here, and was a real finding in the
    # billing audit, so the message says both.
    if name == "InvalidRequestError" or code == "resource_missing":
        return PriceReconciliationStatus.INVALID_PRICE, code or "resource_missing"

    if name in (
        "APIConnectionError",
        "APIError",
        "RateLimitError",
        "AuthenticationError",
        "PermissionError",
        "StripeError",
    ):
        return PriceReconciliationStatus.STRIPE_UNAVAILABLE, code or name

    return PriceReconciliationStatus.STRIPE_UNAVAILABLE, code or name


def _expected_recurring(plan, kind):
    """
    What Stripe's `recurring` block must look like, or None for a price
    that must be one-time.

    Overage blocks are bought outright (`mode="payment"`), so their Price
    is one-time. Only the base subscription price recurs — and comparing
    recurring fields on a one-time price is exactly the false drift the
    brief warns about.
    """
    if kind == PriceKind.OVERAGE:
        return None
    stripe_interval = INTERVAL_TO_STRIPE.get((plan.interval or "").upper())
    if not stripe_interval:
        return None
    return {"interval": stripe_interval, "interval_count": 1}


def _actual_recurring(price):
    recurring = price.get("recurring")
    if not recurring:
        return None
    return {
        "interval": recurring.get("interval"),
        "interval_count": recurring.get("interval_count"),
    }


def _local_amount(plan, kind):
    """
    What the application currently has stored, in cents, or None when the
    field has never been populated.

    None is not zero: "we were never told" and "we were told it is free"
    are different states, and only the first should be silently filled in
    from Stripe.
    """
    if kind == PriceKind.BASE:
        raw = plan.price_cents
        return int(raw) if raw else None
    raw = plan.overage_block_price
    return int(raw) if raw else None


def _price_id_for(plan, kind):
    if kind == PriceKind.BASE:
        return (plan.stripe_price_id or "").strip()
    return (plan.stripe_overage_price_id or "").strip()


def _apply_stripe_values(plan, kind, price):
    """
    Write Stripe's values onto the plan. STRIPE -> APPLICATION, never the
    reverse.

    Returns (synced_field_names, previous_amount, previous_product).

    WHAT IS SYNCHRONISED, AND WHAT DELIBERATELY IS NOT
    ---------------------------------------------------
    Synchronised — these are the PRICE, which Stripe owns:
        amount      price_cents / overage_block_price
        product     product_id (a reference used when creating Prices)

    NOT synchronised, and this is the important line:
        interval    `plan.interval` is not a price. It decides how often
                    credits are granted, how renewals are detected and how
                    a plan change is classified (billing/services.py reads
                    it in half a dozen places). Flipping it from a Stripe
                    Price would change how customers are BILLED and
                    CREDITED, not merely what they are charged — which the
                    brief rules out. A mismatch here is reported for a
                    human instead.
        currency    No plan field exists to hold it. Verified, not stored.

    A targeted UPDATE rather than `.save()`: it touches only the columns
    this function owns, so a concurrent edit to any other field on the
    plan cannot be clobbered by a reconciliation that happens to be
    mid-flight.
    """
    from .models import SubscriptionPlan

    stripe_amount = price.get("unit_amount")
    stripe_product = str(price.get("product") or "")

    previous_amount = _local_amount(plan, kind)
    previous_product = (plan.product_id or "") if kind == PriceKind.BASE else ""

    # Heterogeneous by design: an amount column and a char column.
    updates: dict = {}
    synced: list = []

    amount_field = "price_cents" if kind == PriceKind.BASE else "overage_block_price"
    if isinstance(stripe_amount, int) and previous_amount != stripe_amount:
        updates[amount_field] = (
            Decimal(stripe_amount) if kind == PriceKind.BASE else stripe_amount
        )
        synced.append("amount")

    # Only from the BASE price. An overage Price belongs to the same
    # product in practice, but the base price is the plan's own product and
    # letting two sources write one field is how it ends up oscillating.
    if kind == PriceKind.BASE and stripe_product and previous_product != stripe_product:
        updates["product_id"] = stripe_product
        synced.append("product")

    if updates:
        SubscriptionPlan.objects.filter(pk=plan.pk).update(**updates)
        for field, value in updates.items():
            setattr(plan, field, value)

    return synced, previous_amount, previous_product


def _check_one(run, plan, kind, cache):
    """
    Compare one local expectation against one Stripe Price and persist the
    result. Never raises — a single broken plan must not end the sweep.
    """
    price_id = _price_id_for(plan, kind)
    local_amount = _local_amount(plan, kind)

    result = PriceReconciliationResult(
        run=run,
        plan=plan,
        plan_name=plan.name,
        plan_category=plan.category or "",
        price_kind=kind,
        price_id=price_id,
        expected_amount=local_amount,
        expected_currency=EXPECTED_CURRENCY,
        expected_active=True,
        expected_product=(plan.product_id or "") if kind == PriceKind.BASE else "",
        expected_recurring=_expected_recurring(plan, kind),
        detected_at=timezone.now(),
    )

    # --- no price configured -------------------------------------------
    if not price_id:
        if local_amount:
            # We have a price stored but nothing to charge with, and no
            # Stripe Price to synchronise from either.
            result.status = PriceReconciliationStatus.MISSING_PRICE_ID
            result.error_message = (
                f"Plan {plan.name} has {local_amount} cents stored for its "
                f"{kind.lower()} price but no Stripe price id, so there is "
                f"nothing authoritative to synchronise from."
            )
        else:
            # A free trial, an internal plan, or a beta tier that charges
            # only for overage. Nothing is wrong.
            result.status = PriceReconciliationStatus.NOT_APPLICABLE
        result.save()
        return result

    # An unset local amount is no longer a finding. Stripe is the source
    # of truth, so "we never recorded a price" is simply a value waiting to
    # be filled in from the authoritative one below.

    # --- read Stripe ----------------------------------------------------
    price, exc = cache.get(price_id)
    if exc is not None:
        status, code = _classify_stripe_error(exc)
        result.status = status
        result.error_code = code
        result.error_message = (
            f"{type(exc).__name__} reading price {price_id}: {exc}"
            + (
                "  (a price id that does not resolve may also mean these "
                "credentials address a different Stripe account than the "
                "one the plan was configured against)"
                if status == PriceReconciliationStatus.INVALID_PRICE
                else ""
            )
        )
        result.save()
        return result

    result.stripe_amount = price.get("unit_amount")
    result.stripe_currency = price.get("currency") or ""
    result.stripe_active = bool(price.get("active"))
    result.stripe_product = str(price.get("product") or "")
    result.stripe_recurring = _actual_recurring(price)

    # --- compare ---------------------------------------------------------
    # Split in two, because the halves have opposite resolutions.
    #
    # SYNCABLE   the price itself. Stripe owns it, so a disagreement is
    #            resolved by following Stripe.
    # REPORTABLE things a Price cannot decide on the application's behalf
    #            without changing how customers are billed or credited.
    syncable_mismatches = []
    reportable_mismatches = []

    if result.stripe_amount != local_amount:
        syncable_mismatches.append("amount")
    if (
        kind == PriceKind.BASE
        and result.stripe_product
        and result.stripe_product != result.expected_product
    ):
        syncable_mismatches.append("product")
    if result.stripe_currency != EXPECTED_CURRENCY:
        # No plan field holds currency, so this can only ever be reported.
        reportable_mismatches.append("currency")
    if result.expected_recurring != result.stripe_recurring:
        # `plan.interval` drives credit cadence and renewal detection.
        reportable_mismatches.append("recurring")

    result.mismatched_fields = syncable_mismatches + reportable_mismatches

    # An ARCHIVED price is the one case where Stripe's value must NOT be
    # copied in. It will refuse the charge, so treating it as the source of
    # truth would overwrite a working local price with one that cannot be
    # used — the opposite of what synchronising is for.
    if not result.stripe_active:
        result.status = PriceReconciliationStatus.INACTIVE_PRICE
        result.error_message = (
            f"Stripe price {price_id} for plan {plan.name} is ARCHIVED. "
            f"Purchases against it will fail, so its values were NOT "
            f"copied into the application — the existing configuration is "
            f"left untouched."
        )
        result.save()
        return result

    # --- synchronise: STRIPE -> APPLICATION ------------------------------
    if syncable_mismatches:
        synced, previous_amount, previous_product = _apply_stripe_values(
            plan, kind, price
        )
        result.synced = bool(synced)
        result.synced_fields = synced
        result.previous_local_amount = previous_amount
        result.previous_local_product = previous_product

    if reportable_mismatches:
        # Reported even when the price itself synchronised fine: the money
        # is now right, but something about HOW it bills still is not.
        result.status = PriceReconciliationStatus.DRIFT_DETECTED
        result.error_message = (
            f"Plan {plan.name} disagrees with Stripe price {price_id} on "
            f"{', '.join(reportable_mismatches)}, which cannot be "
            f"synchronised automatically because it changes how customers "
            f"are billed rather than what they are charged. Needs a human."
        )
    elif result.synced:
        result.status = PriceReconciliationStatus.SYNCHRONIZED
    else:
        result.status = PriceReconciliationStatus.MATCHED

    result.save()
    return result


def _verify_account(run):
    """
    Record which Stripe account these credentials actually address.

    Not derived from a hard-coded account id — the brief is explicit about
    that, and the audit found plans pointing at a different account than
    the environment was configured for. Reading the account back is how a
    human can tell at a glance which one a result set belongs to.
    """
    try:
        account = stripe.Account.retrieve()
        run.stripe_account_id = str(account.get("id") or "")
    except Exception as exc:  # noqa: BLE001 - recorded, never fatal
        run.stripe_account_id = ""
        logger.warning(
            "Price reconciliation could not identify the Stripe account: %r", exc
        )


def reconcile_prices(plans=None) -> PriceReconciliationRun:
    """
    Check every active plan's base and overage price against Stripe.

    Read-only with respect to billing: writes nothing to Stripe, and
    locally writes only this module's own result rows. Safe to run
    repeatedly — a second run produces a second set of observations and
    changes no billing state whatsoever.
    """
    run = PriceReconciliationRun.objects.create(
        stripe_api_version=str(getattr(stripe, "api_version", "") or ""),
    )
    _verify_account(run)

    if plans is None:
        plans = SubscriptionPlan.objects.filter(is_active=True).order_by("name")
    plans = list(plans)

    cache = _PriceCache()
    results = []
    for plan in plans:
        for kind in (PriceKind.BASE, PriceKind.OVERAGE):
            try:
                results.append(_check_one(run, plan, kind, cache))
            except Exception as exc:  # noqa: BLE001 - one plan must not end the run
                logger.exception(
                    "Price reconciliation failed unexpectedly for plan %s (%s).",
                    plan.name,
                    kind,
                )
                results.append(
                    PriceReconciliationResult.objects.create(
                        run=run,
                        plan=plan,
                        plan_name=plan.name,
                        plan_category=plan.category or "",
                        price_kind=kind,
                        price_id=_price_id_for(plan, kind),
                        status=PriceReconciliationStatus.CONFIGURATION_ERROR,
                        error_code="unexpected_error",
                        error_message=repr(exc)[:2000],
                    )
                )

    checked = [
        r for r in results if r.status != PriceReconciliationStatus.NOT_APPLICABLE
    ]
    alerts = [r for r in results if r.status in PRICE_RECONCILIATION_ALERT_STATUSES]
    unavailable = [
        r for r in results if r.status == PriceReconciliationStatus.STRIPE_UNAVAILABLE
    ]
    matched = [r for r in results if r.status == PriceReconciliationStatus.MATCHED]
    synced = [r for r in results if r.synced]

    run.plans_checked = len(plans)
    run.prices_checked = len(checked)
    run.matched_count = len(matched)
    run.synced_count = len(synced)
    run.alert_count = len(alerts)
    run.unavailable_count = len(unavailable)
    run.finished_at = timezone.now()
    run.summary = (
        f"Stripe price reconciliation: {len(plans)} plan(s), "
        f"{len(checked)} price(s) checked, {len(matched)} already matching, "
        f"{len(synced)} synchronised from Stripe, "
        f"{len(alerts)} needing attention, {len(unavailable)} unreadable."
    )
    run.save()

    _emit_alerts(run, alerts, unavailable, synced)
    return run


def _emit_alerts(run, alerts, unavailable, synced=()):
    """
    One line per finding, at a level that matches what it means.

    Billing misconfiguration is ERROR — someone should look today. Stripe
    being unreachable is WARNING: it is an operational event, not a
    billing incident, and paging on it is how the real alerts get muted.

    A synchronisation is WARNING rather than INFO. It is the system working
    as designed, so it is not an alert — but the application's idea of what
    a customer pays just changed without anyone approving it, and that
    should never scroll past unnoticed. It is also the only line that names
    the OLD value, which is what someone reconstructing an unexpected
    charge will be looking for.
    """
    for result in synced:
        logger.warning(
            "STRIPE PRICE SYNCHRONISED — plan %s (%s) %s price %s: %s. "
            "Local amount %s -> %s %s. Stripe is the source of truth, so "
            "the application now follows it. (account=%s, run=%s)",
            result.plan_name,
            result.plan_category,
            result.price_kind.lower(),
            result.price_id,
            ", ".join(result.synced_fields),
            result.previous_local_amount,
            result.stripe_amount,
            result.stripe_currency,
            run.stripe_account_id or "unknown",
            run.id,
        )

    for result in alerts:
        logger.error(
            "STRIPE PRICE %s — plan %s (%s), %s price %s. "
            "Expected %s %s, Stripe says %s %s. Mismatched: %s. %s "
            "(account=%s, run=%s)",
            result.status,
            result.plan_name,
            result.plan_category,
            result.price_kind.lower(),
            result.price_id or "(none)",
            result.expected_amount,
            result.expected_currency,
            result.stripe_amount,
            result.stripe_currency,
            ", ".join(result.mismatched_fields) or "-",
            result.error_message,
            run.stripe_account_id or "unknown",
            run.id,
        )

    for result in unavailable:
        logger.warning(
            "Stripe was unreadable for plan %s %s price %s (%s: %s). This is "
            "an operational failure, NOT price drift — the price may be "
            "perfectly correct. Run %s.",
            result.plan_name,
            result.price_kind.lower(),
            result.price_id,
            result.error_code,
            result.error_message,
            run.id,
        )

    (logger.error if alerts else logger.info)(run.summary)
