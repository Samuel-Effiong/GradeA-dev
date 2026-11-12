"""R2: the beta tallies follow the wallet charges.

BetaProfile.total_credits_used and the per-feature counters
(FEATURE_TO_ANALYTICS_FIELD) are running counts kept by
AnalyticsService.record_consumption / record_refund. Nothing checked them,
so they could drift from the charges (one local teacher's tally was about 9x
their charges after their wallet was recreated). `reconcile` sets them back
to the charges, in the same raw unit, and recomputes what is derived from
them: the 80% and cap flags, usage_velocity and conversion_probability.

The charges are `charged_logs()`: every CreditUsageLog with is_refunded
false, owned by Coalesce(user_id, the wallet's user). That is the one
definition of "what a teacher was charged"; the users list uses it too, so
the two numbers match.

The flags always match real usage (owner, 2026-11-12): set and cleared
from the corrected total, with AnalyticsService.usage_flags, the rule
record_consumption and record_refund use too. Used by migration 0075 (once)
and the nightly task billing.tasks.reconcile_beta_tallies.

Model classes are parameters so the migration can pass its historical
models; nothing here imports a model at module level for that reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from django.db import transaction
from django.db.models import OuterRef, QuerySet, Subquery, Sum, UUIDField
from django.db.models.functions import Coalesce
from django.utils import timezone

from billing.charges import charged_q
from billing.services import FEATURE_TO_ANALYTICS_FIELD, AnalyticsService

TOTAL_FIELD = "total_credits_used"
#: The per-feature counters, in a stable order.
FEATURE_FIELDS = tuple(sorted(set(FEATURE_TO_ANALYTICS_FIELD.values())))
TALLY_FIELDS = (TOTAL_FIELD, *FEATURE_FIELDS)
FLAG_FIELDS = ("has_hit_80_percent", "has_hit_cap")
SCORE_FIELDS = ("conversion_probability", "usage_velocity")

BATCH_SIZE = 500


def _models(usage_log_model, wallet_model):
    if usage_log_model is None or wallet_model is None:
        from billing.models import CreditUsageLog, CreditWallet

        usage_log_model = usage_log_model or CreditUsageLog
        wallet_model = wallet_model or CreditWallet
    return usage_log_model, wallet_model


def charged_logs(
    user_ids: Iterable[Any] | None = None,
    *,
    usage_log_model=None,
    wallet_model=None,
) -> QuerySet:
    """The charges: usage logs with is_refunded false, annotated with
    `charged_user_id` = Coalesce(user_id, the wallet's user_id).

    The wallet's user is read through a subquery, not a join: the wallet FK
    has no database constraint, and an inner join would silently drop a log
    whose wallet was deleted even though its own user_id is set.

    `user_ids` narrows it to those users with billing.charges.charged_q, the
    rule the users list uses too. Default ordering is cleared so the result
    can be grouped.
    """
    usage_log_model, wallet_model = _models(usage_log_model, wallet_model)
    wallet_user = wallet_model.objects.filter(pk=OuterRef("wallet_id")).values(
        "user_id"
    )[:1]
    logs = usage_log_model.objects.all()
    if user_ids is not None:
        logs = logs.filter(charged_q(user_ids=user_ids, wallet_model=wallet_model))
    else:
        logs = logs.filter(is_refunded=False)
    return logs.annotate(
        charged_user_id=Coalesce(
            "user_id", Subquery(wallet_user), output_field=UUIDField()
        )
    ).order_by()


def charges_by_user(
    user_ids: Iterable[Any], *, usage_log_model=None, wallet_model=None
) -> dict[Any, dict[str, int]]:
    """{user_id: {tally field: charged amount}} for every user in user_ids
    (zeros when a user has no charges). One grouped query."""
    user_ids = list(user_ids)
    sums = {
        uid: {name: 0 for name in TALLY_FIELDS} for uid in user_ids
    }  # type: dict[Any, dict[str, int]]
    rows = (
        charged_logs(
            user_ids, usage_log_model=usage_log_model, wallet_model=wallet_model
        )
        .values("charged_user_id", "feature")
        .annotate(amount=Sum("amount"))
    )
    for row in rows:
        tallies = sums.get(row["charged_user_id"])
        if tallies is None:
            continue
        amount = row["amount"] or 0
        tallies[TOTAL_FIELD] += amount
        name = FEATURE_TO_ANALYTICS_FIELD.get(row["feature"])
        if name:
            tallies[name] += amount
    return sums


@dataclass(frozen=True)
class Correction:
    """One profile that `reconcile` changed: its tallies and flags before
    and after (only the fields that changed)."""

    profile_id: Any
    user_id: Any
    before: dict[str, Any] = field(default_factory=dict)
    after: dict[str, Any] = field(default_factory=dict)

    @property
    def total_gap(self) -> int:
        """New total minus old (negative when the tally was above the
        charges); 0 when only a counter or flag changed."""
        if TOTAL_FIELD not in self.after:
            return 0
        return self.after[TOTAL_FIELD] - self.before[TOTAL_FIELD]


def _flags(profile, total: int) -> dict[str, bool]:
    """has_hit_80_percent / has_hit_cap from the total (set or cleared).
    Allocation 0 leaves both untouched."""
    flags = AnalyticsService.usage_flags(total, profile.initial_beta_credits)
    if flags is None:
        return {name: getattr(profile, name) for name in FLAG_FIELDS}
    return flags


def _corrections(profiles, sums):
    """(profile, Correction) for each profile whose tallies or flags differ
    from the charges. Updates the profile objects in memory."""
    found = []
    for profile in profiles:
        target = dict(sums[profile.user_id])
        target.update(_flags(profile, target[TOTAL_FIELD]))
        before = {
            name: getattr(profile, name)
            for name, value in target.items()
            if getattr(profile, name) != value
        }
        if not before:
            continue
        after = {name: target[name] for name in before}
        for name, value in after.items():
            setattr(profile, name, value)
        found.append((profile, Correction(profile.pk, profile.user_id, before, after)))
    return found


def reconcile(
    profiles: QuerySet,
    *,
    usage_log_model=None,
    wallet_model=None,
    now: datetime | None = None,
    batch_size: int = BATCH_SIZE,
) -> list[Correction]:
    """Set each profile's tallies to its charges, recompute the flags, and
    rescore the profiles that changed. Returns one Correction per changed
    profile; a profile that already matches is not written.

    Per batch: one grouped read to find the drifted profiles, then, only if
    there are any, a short transaction that locks them, re-reads their
    charges and writes them with one bulk_update. Reading the charges after
    the lock means a consumption committed meanwhile is counted, and one
    still in flight waits for the lock and adds its F() increment on top.
    """
    usage_log_model, wallet_model = _models(usage_log_model, wallet_model)
    model = profiles.model
    now = now or timezone.now()
    fields = [*TALLY_FIELDS, *FLAG_FIELDS, *SCORE_FIELDS]
    result: list[Correction] = []

    ids = list(profiles.order_by("pk").values_list("pk", flat=True))
    for start in range(0, len(ids), batch_size):
        batch = model.objects.filter(pk__in=ids[start : start + batch_size])
        loaded = list(batch)
        sums = charges_by_user(
            [p.user_id for p in loaded],
            usage_log_model=usage_log_model,
            wallet_model=wallet_model,
        )
        drifted = [p.pk for p, _ in _corrections(loaded, sums)]
        if not drifted:
            continue
        with transaction.atomic():
            locked = list(
                model.objects.select_for_update().filter(pk__in=drifted).order_by("pk")
            )
            sums = charges_by_user(
                [p.user_id for p in locked],
                usage_log_model=usage_log_model,
                wallet_model=wallet_model,
            )
            changed = _corrections(locked, sums)
            for profile, _ in changed:
                score, velocity = AnalyticsService.conversion_score(profile, now=now)
                profile.conversion_probability = score
                profile.usage_velocity = velocity
            model.objects.bulk_update([p for p, _ in changed], fields)
            result.extend(c for _, c in changed)
    return result
