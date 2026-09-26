"""
billing/overage_pricing.py
==========================
Who decides what an overage block costs.

THE DEFECT THIS CLOSES
----------------------
Two numbers claimed to be the price of one block, and nothing made them
agree:

    plan.overage_block_price      what we QUOTE, record on the purchase
                                  intent, and show the customer
    plan.stripe_overage_price_id  what Stripe actually CHARGES

Both overage checkout flows build their Stripe session from the price id
and every local number from the column. Measured against real Stripe
(test mode, 2026-09-10), six of the nine plans with overage pricing
disagreed:

    plan             local   stripe   nickname
    STANDARD           500      500   Standard Overage   ok
    STANDARD_ANNUAL    500      500   Standard Overage   ok
    PRO_ANNUAL         400      400   Pro Overage        ok
    PRO                500      400   Pro Overage        DRIFT
    POWER              500      300   Power Overage      DRIFT
    POWER_ANNUAL         3      300   Power Overage      DRIFT
    BETA                 0      500   Standard Overage   DRIFT
    PRO_LICENSE        299      400   Pro Overage        DRIFT
    POWER_LICENSE      299      300   Power Overage      DRIFT

A school buying three PRO_LICENSE blocks was quoted 897 and charged 1200.

WHICH ONE IS THE SOURCE OF TRUTH
--------------------------------
STRIPE IS, and not by preference — by mechanism. Stripe is what moves the
money. A local column cannot be authoritative about a charge it does not
make; it can only be right or wrong about it.

The Stripe side is also demonstrably the deliberate one. The three prices
are nicknamed "Power Overage" (300), "Pro Overage" (400) and "Standard
Overage" (500) — a coherent tier-descending volume discount whose names
match the tier names exactly — and the three plan rows that agree with it
(STANDARD, STANDARD_ANNUAL, PRO_ANNUAL) sit exactly where that scheme
predicts. The drifted rows read like rows never updated when tier pricing
landed: PRO and POWER both still carry STANDARD's 500, POWER_ANNUAL's 3 is
a units slip, and BETA's 0 was never set at all.

WHAT THIS MODULE DOES, AND DELIBERATELY DOES NOT
------------------------------------------------
It REFUSES to quote a price we are not going to charge. That is the whole
point: the failure being prevented is not "the column is wrong", it is
"the customer is told one number and billed another". A purchase that
cannot be priced honestly does not happen.

It does NOT rewrite plan rows on its own. Aligning the column to Stripe
changes nobody's actual charge — it only makes the displayed and recorded
amount truthful — but it is still a money-facing edit, so it belongs to a
human running `reconcile_overage_prices --fix`, not to an import side
effect or a migration that would run blind against a database this code
has never seen.

THE LICENSE ROWS ARE ENTANGLED WITH THE ACCOUNT-2 WORK
------------------------------------------------------
PRO_LICENSE and POWER_LICENSE both carry 299 — a flat per-block school
price, independent of tier — while pointing at the INDIVIDUAL tier's
Stripe prices, because no school-specific Stripe price exists. So their
drift is not a typo; it is a pricing intent that was never given a Stripe
price to live in. Correcting them to 400/300 makes the quote honest today,
but the real fix is a school overage price in the deployed account, which
is tracked configuration work (Items 3/4) and is deliberately NOT done
here. See docs/CODEBASE_AUDIT_SECTIONS.md.

COST
----
`Price.retrieve` per plan, cached for STRIPE_PRICE_CACHE_TTL. Checkout
already makes a Stripe round trip, so the added latency on the purchase
path is one cached read, and the nightly reconciler pays at most one call
per distinct price id.
"""

import logging

from django.core.cache import cache

from .imports import stripe

logger = logging.getLogger(__name__)

#: Long enough that a burst of purchases costs one lookup, short enough
#: that a price corrected in the Stripe dashboard takes effect within a
#: coffee break rather than needing a deploy.
STRIPE_PRICE_CACHE_TTL = 300

_CACHE_PREFIX = "billing:stripe-overage-price:"


class OveragePriceUnavailable(ValueError):
    """Stripe could not tell us what this plan charges."""


class OveragePriceMismatch(ValueError):
    """
    The plan quotes one price and Stripe charges another.

    Raised instead of quoting, so the customer is never shown a number we
    will not honour.
    """

    def __init__(self, plan, local_cents, stripe_cents):
        self.plan = plan
        self.local_cents = local_cents
        self.stripe_cents = stripe_cents
        super().__init__(
            f"Overage pricing for plan {plan.name} is out of sync: this "
            f"system quotes {local_cents} cents per block but Stripe price "
            f"{plan.stripe_overage_price_id} charges {stripe_cents}. "
            f"Refusing to quote a price we would not charge."
        )


def _cache_key(price_id) -> str:
    return f"{_CACHE_PREFIX}{price_id}"


def stripe_overage_unit_price(plan, *, use_cache: bool = True):
    """
    What Stripe will actually charge per block for `plan`, in cents.

    Returns None when the plan has no Stripe overage price configured at
    all — a different condition from "we could not reach Stripe", which
    raises, because the two need different handling: the first means this
    plan does not sell overage, the second means we do not currently know
    what it costs.
    """
    price_id = (plan.stripe_overage_price_id or "").strip()
    if not price_id:
        return None

    key = _cache_key(price_id)
    if use_cache:
        cached = cache.get(key)
        if cached is not None:
            return cached

    try:
        price = stripe.Price.retrieve(price_id)
    except Exception as exc:  # noqa: BLE001 - reported as a domain error
        raise OveragePriceUnavailable(
            f"Could not read Stripe price {price_id} for plan {plan.name}: {exc!r}"
        ) from exc

    unit_amount = price.get("unit_amount")
    if not isinstance(unit_amount, int):
        # A tiered or metered price cannot be multiplied by a block count,
        # which is the only thing the quote knows how to do.
        raise OveragePriceUnavailable(
            f"Stripe price {price_id} for plan {plan.name} has no flat "
            f"unit_amount (got {unit_amount!r}); this system can only quote "
            f"a per-block price."
        )

    cache.set(key, unit_amount, STRIPE_PRICE_CACHE_TTL)
    return unit_amount


def assert_overage_price_in_sync(plan) -> int:
    """
    Confirm the plan quotes what Stripe charges, and return that price.

    Called before any overage quote or checkout session. Raises
    OveragePriceMismatch when the two disagree — refusing the purchase is
    the correct outcome, because the alternative is telling the customer a
    price and charging them a different one.

    A plan with no Stripe overage price configured is left alone: it is
    handled by the existing "this plan does not support overage" checks,
    and raising here would turn a clear message into a confusing one.
    """
    stripe_cents = stripe_overage_unit_price(plan)
    if stripe_cents is None:
        return plan.overage_block_price or 0

    local_cents = plan.overage_block_price or 0
    if local_cents != stripe_cents:
        logger.error(
            "OVERAGE PRICE DRIFT on plan %s: local %s cents vs Stripe price "
            "%s at %s cents. Purchases on this plan are refused until the "
            "two agree — run `manage.py reconcile_overage_prices` for the "
            "full picture.",
            plan.name,
            local_cents,
            plan.stripe_overage_price_id,
            stripe_cents,
        )
        raise OveragePriceMismatch(plan, local_cents, stripe_cents)
    return stripe_cents


def overage_price_drift(plans=None) -> list:
    """
    Every active plan whose quoted overage price disagrees with Stripe.

    Used by the nightly reconciler and the management command. Never
    raises on one bad plan — a single unreadable price must not hide the
    drift on every other.
    """
    from .models import SubscriptionPlan

    if plans is None:
        plans = (
            SubscriptionPlan.objects.filter(is_active=True)
            .exclude(stripe_overage_price_id="")
            .exclude(stripe_overage_price_id__isnull=True)
            .order_by("name")
        )

    report = []
    for plan in plans:
        entry = {
            "plan": plan,
            "name": plan.name,
            "local_cents": plan.overage_block_price or 0,
            "stripe_price_id": plan.stripe_overage_price_id,
            "stripe_cents": None,
            "error": None,
            "in_sync": False,
        }
        try:
            entry["stripe_cents"] = stripe_overage_unit_price(plan, use_cache=False)
        except OveragePriceUnavailable as exc:
            entry["error"] = str(exc)
            report.append(entry)
            continue
        entry["in_sync"] = entry["stripe_cents"] == entry["local_cents"]
        report.append(entry)
    return report
