"""Analytics Step 4 (owner, 2026-11-25): the conversion page numbers.

For individual teachers only. "Paying" (owner Q1, option A) is a
Stripe-billed individual plan: category INDIVIDUAL, tier STANDARD, PRO, POWER
or CUSTOM, not a trial, stripe_subscription_id set; past_due still counts
while the subscription is active.

- paying_users_count: distinct users with such a subscription now.
- Trials: a trial starts when its TRIAL credit bucket is created (every trial
  makes one, and buckets are never deleted). Sign-ups and the conversion rate
  count only trials started on or after settings.CONVERSION_START_DATE.
- Churn (owner's formula): lost / base * 100, where base is the users paying
  at the period start and lost is those of them not paying at its end (their
  paid subscription ended during the period; a switch to another paid plan
  is not lost). The start date does not apply (owner Q2, option A).
  "Paying at T" reads UserSubscription.paid_since and ended_at. Before
  BillingSettings.subscription_history_from those are not exact, so a period
  that begins earlier has no figures (owner Q3).
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from datetime import timezone as dt_timezone
from typing import Optional

from django.conf import settings

from .models import (
    BillingSettings,
    CreditBucket,
    CreditBucketType,
    PlanCategory,
    PlanTier,
    UserSubscription,
)

PAID_TIERS = (PlanTier.STANDARD, PlanTier.PRO, PlanTier.POWER, PlanTier.CUSTOM)
WINDOW = timedelta(days=30)
MONTHS = 12


def paid_subscriptions():
    """Stripe-billed individual plan rows, active or not (owner Q1, A)."""
    return UserSubscription.objects.filter(
        plan__category=PlanCategory.INDIVIDUAL,
        plan__tier__in=PAID_TIERS,
        is_trial=False,
        stripe_subscription_id__isnull=False,
    ).exclude(stripe_subscription_id="")


def paying_users_count():
    return (
        paid_subscriptions().filter(is_active=True).values("user_id").distinct().count()
    )


def start_moment():
    """CONVERSION_START_DATE at 00:00 UTC."""
    return datetime.combine(
        settings.CONVERSION_START_DATE, time.min, tzinfo=dt_timezone.utc
    )


def percent(part, whole):
    return round(part * 100 / whole, 2) if whole else 0.0


def _trial_users(since, until):
    return (
        CreditBucket.objects.filter(
            bucket_type=CreditBucketType.TRIAL,
            created_at__gte=since,
            created_at__lte=until,
        )
        .values_list("wallet__user_id", flat=True)
        .distinct()
    )


def trial_numbers(now):
    """(new sign-ups in the last 30 days, trials since the start date,
    of those paying now, percent)."""
    start = start_moment()
    signups = _trial_users(max(now - WINDOW, start), now).count()
    trials = set(_trial_users(start, now))
    paying_now = set(
        paid_subscriptions()
        .filter(is_active=True, user_id__in=trials)
        .values_list("user_id", flat=True)
    )
    converted = len(paying_now)
    return signups, len(trials), converted, percent(converted, len(trials))


@dataclass(frozen=True)
class Churn:
    base_count: Optional[int]
    lost_count: Optional[int]
    churn_percent: Optional[float]


NO_DATA = Churn(None, None, None)


class PaidSpans:
    """Every paid row's (user, paid_since, ended_at, is_active), read once."""

    def __init__(self):
        self.spans = list(
            paid_subscriptions().values_list(
                "user_id", "paid_since", "ended_at", "is_active"
            )
        )
        self.history_from = (
            BillingSettings.objects.filter(pk=1)
            .values_list("subscription_history_from", flat=True)
            .first()
        )

    def paying_at(self, moment):
        users = set()
        for user_id, since, ended, active in self.spans:
            if since is None or since > moment:
                continue
            if (ended > moment) if ended is not None else active:
                users.add(user_id)
        return users

    def churn(self, start, end):
        if self.history_from is not None and start < self.history_from:
            return NO_DATA
        base = self.paying_at(start)
        lost = base - self.paying_at(end)
        return Churn(len(base), len(lost), percent(len(lost), len(base)))


def rolling_churn(now, spans=None):
    return (spans or PaidSpans()).churn(now - WINDOW, now)


def monthly_churn(now):
    """The last 12 calendar months (UTC), oldest first; the current month
    runs to now."""
    spans = PaidSpans()
    current = now.astimezone(dt_timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    starts = []
    month = current
    for _ in range(MONTHS):
        starts.append(month)
        month = (month - timedelta(days=1)).replace(day=1)
    rows = []
    for start in reversed(starts):
        following = (start + timedelta(days=32)).replace(day=1)
        partial = start == current
        figures = spans.churn(start, now if partial else following)
        rows.append(
            {
                "month": start.strftime("%Y-%m"),
                "base_count": figures.base_count,
                "lost_count": figures.lost_count,
                "churn_percent": figures.churn_percent,
                "partial": partial,
                "no_data": figures is NO_DATA,
            }
        )
    return rows
