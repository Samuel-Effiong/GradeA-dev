"""
The four conversion signals, in one place (brief 4, owner-approved for beta
2026-11-11; SM -31 rulings, docs/evidence/signals-beta/DESIGN.md).

A teacher's BetaProfile meets a signal when:

- at_80_percent: has_hit_80_percent
- frequent_user: distinct_login_days >= 8
- active_last_week: last_active_at >= now - 7 days (inclusive; null -> False)
- is_power_grader: credits_used_grading > credits_used_creation

signal_count is how many are met (0-4). Every surface that shows them reads
these SQL expressions, so they cannot disagree:

- the sales-leads list (beta/intent-signals): its per-row "flags", and its
  optional `signals` ordering and filters;
- the signal distribution chart;
- the single-lead detail;
- the cohort summary's four counts;
- the super admin user list (users.user_list), teachers only.

`now` is read once per request through `clock()` (tests patch it) and kept on
the request (`request_now`).
"""

from datetime import timedelta
from typing import Any

import django_filters
from django.db.models import BooleanField, Case, F, IntegerField, Q, Value, When
from django.utils import timezone

FLAGS = ("at_80_percent", "frequent_user", "active_last_week", "is_power_grader")
FREQUENT_DAYS = 8
ACTIVE_WINDOW = timedelta(days=7)
SIGNAL_COUNTS = tuple(range(len(FLAGS) + 1))

#: The `signals` ordering text on the sales-leads list (Q-S2). Owner's plain
#: wording, 2026-11-12; the user list has its own text.
ORDERING_DOC = (
    "Sorts the leads. `signals` puts teachers with the fewest signals first; "
    "`-signals` puts teachers with the most signals first. Teachers with the "
    "same number of signals are sorted by conversion probability, highest "
    "first, with empty ones last, then by their ID. Left empty, leads come in "
    "order of conversion probability, highest first."
)
ORDERING_VALUES = ("signals", "-signals")


def clock():
    return timezone.now()


def request_now(request):
    """`now` for this request: read once, then reused by every caller."""
    holder = getattr(request, "_request", request)
    now = getattr(holder, "_intent_signals_now", None)
    if now is None:
        now = clock()
        holder._intent_signals_now = now
    return now


def flag_q(now, prefix=""):
    """name -> Q for each signal, on a BetaProfile reached through
    `prefix` (e.g. "beta_profile__")."""
    return {
        "at_80_percent": Q(**{f"{prefix}has_hit_80_percent": True}),
        "frequent_user": Q(**{f"{prefix}distinct_login_days__gte": FREQUENT_DAYS}),
        "active_last_week": Q(**{f"{prefix}last_active_at__gte": now - ACTIVE_WINDOW}),
        "is_power_grader": Q(
            **{f"{prefix}credits_used_grading__gt": F(f"{prefix}credits_used_creation")}
        ),
    }


def expressions(now, prefix="", scope=None):
    """name -> SQL expression for the four flags, signal_count and
    signals_score. `scope` (a Q) limits who can have signals at all; anyone
    outside it gets every flag False, 0 and a null score."""
    conditions = flag_q(now, prefix)
    if scope is not None:
        conditions = {name: scope & q for name, q in conditions.items()}
    exprs: dict[str, Any] = {
        name: Case(
            When(q, then=Value(True)), default=Value(False), output_field=BooleanField()
        )
        for name, q in conditions.items()
    }
    total: Any = None
    for q in conditions.values():
        one = Case(
            When(q, then=Value(1)), default=Value(0), output_field=IntegerField()
        )
        total = one if total is None else total + one
    exprs["signal_count"] = total
    score = F(f"{prefix}conversion_probability")
    exprs["signals_score"] = (
        score if scope is None else Case(When(scope, then=score), default=Value(None))
    )
    return exprs


def annotate_signals(queryset, now, prefix="", scope=None, alias=False):
    """Add the four flags, signal_count and signals_score unless present."""
    present = set(queryset.query.annotations)
    missing = {
        name: expr
        for name, expr in expressions(now, prefix, scope).items()
        if name not in present
    }
    if not missing:
        return queryset
    return queryset.alias(**missing) if alias else queryset.annotate(**missing)


def ordering_terms(descending, id_field="id"):
    """The `signals` order on an annotated queryset (Q-S2): the count in the
    requested direction, then the score highest first with no score last,
    then id ascending, whatever the direction."""
    count = F("signal_count")
    return [
        count.desc() if descending else count.asc(),
        F("signals_score").desc(nulls_last=True),
        F(id_field).asc(),
    ]


def filter_signals(queryset, name, value):
    """The shared filters, on an annotated queryset."""
    if name == "signals_min":
        return queryset.filter(signal_count__gte=int(value))
    return queryset.filter(**{name: value == "true"})


def count_filter(method):
    """signals_min: 0-4."""
    return django_filters.ChoiceFilter(
        choices=[(str(n), str(n)) for n in SIGNAL_COUNTS], method=method
    )


def flag_filter(method):
    """true or false only. django-filter's BooleanFilter quietly ignores any
    other value; these refuse it with a 400 (R2, NOTES)."""
    return django_filters.ChoiceFilter(
        choices=[("true", "true"), ("false", "false")], method=method
    )


class SignalFilterSet(django_filters.FilterSet):
    """signals_min (0-4) and one true/false filter per flag. A bad value is a
    400 with django-filter's own message."""

    signals_min = count_filter("filter_signal")
    at_80_percent = flag_filter("filter_signal")
    frequent_user = flag_filter("filter_signal")
    active_last_week = flag_filter("filter_signal")
    is_power_grader = flag_filter("filter_signal")

    def filter_signal(self, queryset, name, value):
        return filter_signals(queryset, name, value)


FILTER_NAMES = ("signals_min", *FLAGS)
