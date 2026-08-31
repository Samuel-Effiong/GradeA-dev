"""
billing/immutable.py
====================

Append-only enforcement for the financial audit tables (`CreditLedger`
and `CreditUsageLog`).

Both models documented themselves as an "immutable audit trail" while
nothing enforced it: a `CASCADE` on the owning user deleted 17k+ ledger
rows with the account, and `.update()` / `.delete()` were reachable from
any code path. This module supplies the enforcement, at the only two
places Django lets us stand:

  * `pre_save`   - rejects writes to an already-persisted row.
  * `pre_delete` - rejects deletion of a row.

`pre_delete` is the load-bearing one, and the reason a `delete()`
override would not have been enough. Django's deletion collector does
NOT call `Model.delete()` or `QuerySet.delete()` for cascaded rows; it
issues bulk SQL directly, so an override is never consulted during a
cascade. Registering a `pre_delete` receiver is what forces the
collector off that path - `Collector.can_fast_delete` returns False as
soon as `_has_signal_listeners()` is true (Django 5.2) - after which the
signal fires per row and can refuse.

The queryset overrides in `AppendOnlyQuerySet` are deliberately
redundant with the signals. They exist to fail at the call site with a
useful message rather than part-way through a transaction.

WHAT THIS DOES NOT COVER
------------------------
This is application-level enforcement. It is bypassed by raw SQL,
`QuerySet._raw_delete()`, `TRUNCATE` (row-level triggers and signals
both miss it), `manage.py flush`, and by anything connecting outside
Django - `dbshell`, psql, the hosting provider's console. The database
role the app connects as is a Postgres superuser, so there is no
privilege barrier behind this layer either. Treat it as "protected
against application-level mistakes", not as a compliance guarantee. A
database trigger is the next step up if that guarantee is ever needed.

MUTABLE FIELDS
--------------
`CreditUsageLog.is_refunded` is deliberately exempt. The refund flow
(`SubscriptionService.refund_credits`) settles a usage log by flipping
that flag, and every reporting query in dashboard/, classrooms/ and
billing/ filters on it. Freezing it would mean redesigning refunds as
reversing rows and rewriting those queries. The financial substance of
the row - amounts, feature, task, wallet, bucket, timestamps - is
frozen; only the settled/unsettled marker moves.
"""

import contextlib
import threading

from django.core.exceptions import ValidationError
from django.db import models

# `auto_now`-style bookkeeping columns Django itself may rewrite, plus the
# per-model exemptions declared via `mutable_fields`.
_ALWAYS_ALLOWED = frozenset({"updated_at"})

_state = threading.local()


class ImmutableRecordError(ValidationError):
    """Raised when code attempts to alter or delete an append-only row."""


def mutations_allowed() -> bool:
    return getattr(_state, "allowed", False)


@contextlib.contextmanager
def allow_unsafe_mutation():
    """
    Temporarily lift append-only enforcement on the current thread.

    Intended for exactly two callers: test setup that needs to fabricate
    historical rows (e.g. back-dating `created_at`), and a supervised
    data-repair session. It is thread-local and does not leak across
    threads, but it DOES lift the guard for every append-only model for
    its duration, so keep the block as small as possible.

    Using this in ordinary application code defeats the point of the
    module and should be caught in review.
    """
    previous = getattr(_state, "allowed", False)
    _state.allowed = True
    try:
        yield
    finally:
        _state.allowed = previous


class AppendOnlyQuerySet(models.QuerySet):
    """
    Fails fast on bulk mutation. The signals below are the real
    enforcement; this exists so the error names the call site.
    """

    def update(self, **kwargs):
        allowed = set(self.model.mutable_fields) | _ALWAYS_ALLOWED
        illegal = sorted(set(kwargs) - allowed)
        if illegal and not mutations_allowed():
            raise ImmutableRecordError(
                f"{self.model.__name__} is append-only; cannot update "
                f"{illegal}. Record a correcting row instead."
            )
        return super().update(**kwargs)

    def delete(self):
        if not mutations_allowed():
            raise ImmutableRecordError(
                f"{self.model.__name__} is append-only; rows cannot be "
                "deleted. Record a reversing row instead."
            )
        return super().delete()


class AppendOnlyModel(models.Model):
    """
    Mixin for the audit tables. Declares the enforcement surface;
    `register_append_only_guards()` wires the signals.
    """

    #: Field names that may still be written after the row is created.
    mutable_fields: frozenset = frozenset()

    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def delete(self, *args, **kwargs):
        if not mutations_allowed():
            raise ImmutableRecordError(
                f"{type(self).__name__} is append-only; rows cannot be "
                "deleted. Record a reversing row instead."
            )
        return super().delete(*args, **kwargs)


def _guard_save(sender, instance, raw, update_fields, **kwargs):
    # `raw` is a loaddata/fixture load: the row is being reconstructed,
    # not edited, and blocking it would make fixtures unusable.
    if raw or mutations_allowed():
        return
    if instance._state.adding:
        return

    allowed = set(sender.mutable_fields) | _ALWAYS_ALLOWED
    touched = set(update_fields) if update_fields else None

    if touched is not None and touched <= allowed:
        return

    raise ImmutableRecordError(
        f"{sender.__name__} is append-only; row {instance.pk} cannot be "
        f"modified after creation "
        f"(fields={sorted(touched) if touched else 'all'}). "
        "Record a correcting row instead."
    )


def _guard_delete(sender, instance, **kwargs):
    if mutations_allowed():
        return
    raise ImmutableRecordError(
        f"{sender.__name__} is append-only; row {instance.pk} cannot be "
        "deleted. Record a reversing row instead."
    )


def register_append_only_guards(*models_to_guard):
    """
    Connect the guards for each model.

    Registering `pre_delete` has a deliberate side effect: it disables
    Django's fast-delete path for these models, so any cascade that
    reaches them materialises rows and fires the signal instead of
    issuing a bulk DELETE. That is precisely what makes the guard
    effective against cascades - and why the relations into these tables
    were also changed to non-cascading, so the guard is a backstop
    rather than something that routinely aborts user deletion.
    """
    from django.db.models.signals import pre_delete, pre_save

    for model in models_to_guard:
        label = model._meta.label_lower
        pre_save.connect(
            _guard_save,
            sender=model,
            dispatch_uid=f"append_only_save_{label}",
        )
        pre_delete.connect(
            _guard_delete,
            sender=model,
            dispatch_uid=f"append_only_delete_{label}",
        )
