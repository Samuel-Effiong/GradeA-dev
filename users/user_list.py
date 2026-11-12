"""
The super admin user list: its values, sort keys, filters and the query
that serves a page at a fixed cost (owner-approved 2026-11-11; design in
docs/evidence/user-list-sorting/DESIGN.md).

Every value is a SQL expression on the page query. `ensure()` adds the
expressions a request needs as aliases, so a sort key or filter works under
get_object() too (filter_queryset runs there). `list_queryset()` adds the
displayed ones as annotations, plus the select_related/prefetch_related the
existing payload (credit_wallet, settings) reads instead of querying per
row.
"""

from functools import partial

from django.db.models import (
    Case,
    F,
    Func,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

from assignments.models import Assignment
from billing import intent_signals as signal_rules
from billing.models import (
    CONVERSION_FACTOR,
    CreditBucket,
    CreditUsageLog,
    CreditWallet,
    LicenseSubscription,
    PlanTier,
    SchoolCreditAllocation,
    SubscriptionPlan,
    UserSubscription,
)
from students.models import StudentSubmission

from .models import UserTypes

#: D3 (SM -16): the plan_tier sort rank.
TIER_RANK = (
    PlanTier.TRIAL,
    PlanTier.BETA,
    PlanTier.STANDARD,
    PlanTier.PRO,
    PlanTier.POWER,
    PlanTier.CUSTOM,
)

#: The features CreditWalletSerializer.get_feature_usage_breakdown reports,
#: in its order, with the alias each one's cycle total is read from.
CORE_FEATURES = {
    "Assignment Extraction": "list_usage_assignment_extraction",
    "Answer Extraction": "list_usage_answer_extraction",
    "Grading Assignment": "list_usage_grading_assignment",
    "Assignment Generation": "list_usage_assignment_generation",
}

# Prefetch attribute names the list serializers read.
ACTIVE_SUBSCRIPTIONS = "list_active_subscriptions"
MANAGED_LICENSES = "list_managed_licenses"
ACTIVE_ALLOCATIONS = "list_active_allocations"


def _one(queryset, field):
    return Subquery(queryset.order_by("pk").values(field)[:1])


def _total(queryset, function, expression):
    """A correlated SUM/COUNT over `queryset` as one scalar subquery. A
    plain Func, not an aggregate, so the subquery has no GROUP BY."""
    return Subquery(
        queryset.order_by()
        .annotate(total=Func(expression, function=function))
        .values("total")[:1],
        output_field=IntegerField(),
    )


def _allocations(is_admin):
    return SchoolCreditAllocation.objects.filter(
        user=OuterRef("pk"),
        is_active=True,
        is_admin_allocation=is_admin,
        license_subscription__is_active=True,
    )


def _active_subscription():
    return UserSubscription.objects.filter(user=OuterRef("pk"), is_active=True)


def _cycle_usage(feature):
    """Usage of `feature` inside the active subscription's cycle, refunded
    rows included: get_feature_usage_breakdown's own sum."""
    sub = UserSubscription.objects.filter(user=OuterRef(OuterRef("pk")), is_active=True)
    return Coalesce(
        _total(
            CreditUsageLog.objects.filter(
                wallet__user=OuterRef("pk"),
                feature=feature,
                created_at__gte=_one(sub, "billing_cycle_start"),
                created_at__lte=_one(sub, "billing_cycle_end"),
            ),
            "SUM",
            F("amount"),
        ),
        0,
    )


def _expressions():
    """name -> (expression factory, names it depends on). Built per call:
    the bucket filter reads the clock."""
    now = timezone.now()
    live = Q(expires_at__isnull=True) | Q(expires_at__gt=now)

    def plan_field(field):
        return Subquery(
            SubscriptionPlan.objects.filter(pk=OuterRef("current_plan_id")).values(
                field
            )[:1]
        )

    exprs = {
        # Billing's own precedence (access_control._resolve_access_context):
        # license teacher > license admin > individual.
        "current_plan_id": (
            lambda: Coalesce(
                _one(_allocations(False), "license_subscription__plan_id"),
                _one(_allocations(True), "license_subscription__plan_id"),
                _one(_active_subscription(), "plan_id"),
            ),
            (),
        ),
        "plan_tier": (lambda: plan_field("tier"), ("current_plan_id",)),
        "plan_category": (lambda: plan_field("category"), ("current_plan_id",)),
        "plan_interval": (lambda: plan_field("interval"), ("current_plan_id",)),
        # The rank is read in the same plan lookup: a Case over plan_tier
        # repeated the whole plan subquery once per tier (EXPLAIN, NOTES).
        "plan_tier_rank": (
            lambda: Subquery(
                SubscriptionPlan.objects.filter(pk=OuterRef("current_plan_id"))
                .annotate(
                    rank=Case(
                        *(When(tier=t, then=Value(i)) for i, t in enumerate(TIER_RANK)),
                        default=Value(None),
                        output_field=IntegerField(),
                    )
                )
                .values("rank")[:1],
                output_field=IntegerField(),
            ),
            ("current_plan_id",),
        ),
        "activated_at": (lambda: F("email_verified_at"), ()),
        "assignments_created": (
            lambda: Coalesce(
                _total(
                    Assignment.objects.filter(course__teacher=OuterRef("pk")),
                    "COUNT",
                    F("pk"),
                ),
                0,
            ),
            (),
        ),
        "papers_graded": (
            lambda: Coalesce(
                _total(
                    StudentSubmission.objects.filter(
                        assignment__course__teacher=OuterRef("pk"),
                        ai_graded_at__isnull=False,
                    ),
                    "COUNT",
                    F("pk"),
                ),
                0,
            ),
            (),
        ),
        # billing.holds.spendable_credits: max(0, live buckets - holds).
        # Null with no wallet (D4).
        "credits_left_raw": (
            lambda: Case(
                When(credit_wallet__isnull=True, then=Value(None)),
                default=Greatest(
                    Value(0),
                    Coalesce(
                        _total(
                            CreditBucket.objects.filter(
                                live, wallet__user=OuterRef("pk")
                            ),
                            "SUM",
                            F("total_credits") - F("used_credits"),
                        ),
                        0,
                    )
                    - F("credit_wallet__reserved_credits"),
                ),
                output_field=IntegerField(),
            ),
            (),
        ),
        "credits_left": (
            lambda: F("credits_left_raw") / Value(CONVERSION_FACTOR),
            ("credits_left_raw",),
        ),
        # The dashboard's usage: all time, net of refunds. A user's charges
        # are the logs of the billed user, Coalesce(user_id, wallet__user_id)
        # = user (SM ruling, S10): user_id survives a deleted wallet, and an
        # old log without one is still found through its wallet.
        "credits_used_raw": (
            lambda: Coalesce(
                _total(
                    CreditUsageLog.objects.filter(
                        # Coalesce(user_id, wallet__user_id) = user. Written
                        # without a join on the wallet: an inner join would
                        # drop the logs whose wallet row is gone, the very
                        # ones user_id is kept for.
                        Q(user_id=OuterRef("pk"))
                        | Q(
                            user_id__isnull=True,
                            wallet_id__in=CreditWallet.objects.filter(
                                user=OuterRef(OuterRef("pk"))
                            ).values("pk"),
                        ),
                        is_refunded=False,
                    ),
                    "SUM",
                    F("amount"),
                ),
                0,
            ),
            (),
        ),
        "credits_used": (
            lambda: F("credits_used_raw") / Value(CONVERSION_FACTOR),
            ("credits_used_raw",),
        ),
    }
    for feature, name in CORE_FEATURES.items():
        exprs[name] = (partial(_cycle_usage, feature), ())
    return exprs


def ensure(queryset, *names, annotate=False):
    """Add each named value (and what it needs) unless already there."""
    exprs = _expressions()
    done = set(queryset.query.annotations)

    def add(qs, name):
        if name in done:
            return qs
        factory, needs = exprs[name]
        for need in needs:
            qs = add(qs, need)
        done.add(name)
        return (
            qs.annotate(**{name: factory()})
            if annotate
            else qs.alias(**{name: factory()})
        )

    for name in names:
        queryset = add(queryset, name)
    return queryset


#: The values the list response shows (UserListSerializer), plus what the
#: list wallet serializer reads.
LISTED = (
    "activated_at",
    "plan_tier",
    "plan_category",
    "plan_interval",
    "assignments_created",
    "papers_graded",
    "credits_left",
    "credits_used",
    *CORE_FEATURES.values(),
)


#: Brief 4: the signals come from the teacher's BetaProfile, teachers only
#: (Q-S3), so the list agrees with the Credit Usage page.
SIGNALS_PREFIX = "beta_profile__"
SIGNALS_SCOPE = Q(user_type=UserTypes.TEACHER)
SIGNAL_SORT_KEY = "signals"


def ensure_signals(queryset, now, annotate=False):
    """The four flags, signal_count and signals_score (billing.intent_signals)."""
    return signal_rules.annotate_signals(
        queryset, now, SIGNALS_PREFIX, SIGNALS_SCOPE, alias=not annotate
    )


def list_queryset(queryset, now=None):
    """The page query: the listed values, and every relation the existing
    payload reads, fetched once per page instead of once per row. `now` is
    the request's (billing.intent_signals.request_now)."""
    queryset = queryset.select_related("settings", "credit_wallet").prefetch_related(
        "credit_wallet__buckets",
        Prefetch(
            "subscriptions",
            queryset=UserSubscription.objects.filter(is_active=True)
            .select_related("plan")
            .order_by("pk"),
            to_attr=ACTIVE_SUBSCRIPTIONS,
        ),
        Prefetch(
            "managed_license_subscriptions",
            queryset=LicenseSubscription.objects.filter(is_active=True).order_by(
                "-created_at"
            ),
            to_attr=MANAGED_LICENSES,
        ),
        Prefetch(
            "school_credit_allocations",
            queryset=SchoolCreditAllocation.objects.filter(is_active=True)
            .select_related("license_subscription")
            .order_by("pk"),
            to_attr=ACTIVE_ALLOCATIONS,
        ),
    )
    queryset = ensure(queryset, *LISTED, annotate=True)
    return ensure_signals(
        queryset, now if now is not None else signal_rules.clock(), annotate=True
    )


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

#: ?ordering= key -> the value it sorts on (raw credits, the tier's rank).
SORT_KEYS = {
    "last_login": "last_login",
    "activated_at": "activated_at",
    "plan_tier": "plan_tier_rank",
    "plan_category": "plan_category",
    "plan_interval": "plan_interval",
    "assignments_created": "assignments_created",
    "papers_graded": "papers_graded",
    "credits_left": "credits_left_raw",
    "credit_usage": "credits_used_raw",
}
#: Keys that are model columns; last_login is one but sorts nulls last too.
#: No "username": CustomUser has none (`username = None`), so ordering by it
#: was a 500; the owner removed it (brief 6, 2026-11-11), and it is now
#: ignored like any unknown key.
COLUMN_KEYS = ("first_name", "last_name", "email")


def ordering_values():
    """Every value ?ordering= accepts, for the OpenAPI enum."""
    keys = (*COLUMN_KEYS, *SORT_KEYS, SIGNAL_SORT_KEY)
    return [v for key in keys for v in (key, f"-{key}")]


def apply_ordering(queryset, terms, now=None):
    """`terms` are already validated (DRF's remove_invalid_fields). Nulls go
    last both ways (D4); id breaks every tie. `signals` expands to the count,
    then the score, then id (billing.intent_signals.ordering_terms)."""
    if not terms:
        terms = list(queryset.model._meta.ordering)
    order = []
    for term in terms:
        desc = term.startswith("-")
        key = term.lstrip("-")
        if key == SIGNAL_SORT_KEY:
            queryset = ensure_signals(
                queryset, now if now is not None else signal_rules.clock()
            )
            order.extend(signal_rules.ordering_terms(desc))
        elif key in SORT_KEYS:
            target = SORT_KEYS[key]
            if target != "last_login":
                queryset = ensure(queryset, target)
            expr = F(target)
            order.append(
                expr.desc(nulls_last=True) if desc else expr.asc(nulls_last=True)
            )
        else:
            order.append(term)
    return queryset.order_by(*order, "id")
