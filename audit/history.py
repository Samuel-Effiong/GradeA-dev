"""Epic A S4: before/after values for tracked records (plan 08 §5).

One event per changed record, with `before` and `after` holding only the
tracked fields that changed. What is tracked, and under which action, is the
REGISTRY below; the values each action may carry are
`audit.metadata.BEFORE_AFTER_ALLOWLIST`, and `registry_problems()` (asserted by
a test) keeps the two in step. IDs, statuses, flags, numbers and times only:
answer text, feedback, names and emails are never tracked (FR-A-04).

Two paths write history:

* **Instance saves and deletes** go through model signals. `pre_save` reads
  the stored values of the tracked fields (one query, only when the save can
  touch one); `post_save` diffs them against what was saved. A delete is
  recorded from `pre_delete`, while the row still exists, with `after=None`.
* **Queryset writes** (`.update()`) skip signals. Their call sites use
  `record_bulk(queryset, ...)` instead, which reads `before` under a row lock,
  applies the update and writes one event per row that changed. The S4 guard
  (`audit/tests_history_guard.py`) fails on any production `.update()` of a
  tracked field that does neither.

The actor is the request's signed-in user (S3's `current_request_actor`), or
SYSTEM outside a request. `record_bulk` takes an explicit keyword-only `actor`
for the one place the request has no user yet: the enrolment activation at
sign-in, which names the user that request just authenticated (SM ruling).

AI grading writes no GRADE_CHANGE (SM note 2): the grading save runs inside
`suppressed()`, and its before/after go onto the one GRADING_COMPLETED event
(`grade_snapshot`). Every event is emitted inside the writing transaction, so
a rolled-back change leaves no event; an audit failure never fails the write
(FR-A-11, the emitter swallows it).
"""

from __future__ import annotations

import datetime
import decimal
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from django.apps import apps
from django.db import transaction
from django.db.models.signals import post_save, pre_delete, pre_save

from .context import current_command_actor, current_request, current_request_actor
from .emitter import emit
from .enums import AuditAction
from .metadata import BEFORE_AFTER_ALLOWLIST

# ---------------------------------------------------------------------------
# The registry


@dataclass(frozen=True)
class Tracked:
    """How one model's changes are recorded.

    `fields` maps a model attname to the key it is recorded under (an FK is
    recorded by id: `plan_id`, never the related object). `school` and each
    `metadata` value are ORM lookups from the model (`student__school_id`),
    read in the same query as the before values.
    """

    model: str
    action: AuditAction
    fields: dict[str, str]
    school: Optional[str] = None
    metadata: dict[str, str] = field(default_factory=dict)
    record_create: bool = True
    record_delete: bool = True
    # A create is recorded only if this answers True for the new instance
    # (None: every create). For users, only a privileged account.
    create_filter: Optional[Callable[[Any], bool]] = None

    @property
    def target_type(self) -> str:
        return self.model.rsplit(".", 1)[1]

    def model_class(self):
        return apps.get_model(self.model)


def _privileged_user(user) -> bool:
    """A new account worth a PERMISSION_CHANGE: staff, superuser, or an admin
    role. A new teacher or student is recorded by its own flow (register,
    invitation, roster import) and would otherwise flood the trail."""
    return bool(
        user.is_staff
        or user.is_superuser
        or user.user_type in ("SCHOOL_ADMIN", "SUPER_ADMIN")
    )


REGISTRY = (
    Tracked(
        model="students.StudentSubmission",
        action=AuditAction.GRADE_CHANGE,
        fields={
            "score": "score",
            "score_percentage": "score_percentage",
            "max_points": "max_points",
            "graded_at": "graded_at",
            "is_published": "is_published",
            "needs_review": "needs_review",
        },
        school="student__school_id",
        metadata={"assignment_id": "assignment_id", "student_id": "student_id"},
        # A new submission is SUBMISSION_UPLOAD, not a grade change.
        record_create=False,
    ),
    Tracked(
        model="classrooms.StudentCourse",
        action=AuditAction.ROSTER_CHANGE,
        fields={"enrollment_status": "enrollment_status", "course_id": "course_id"},
        school="student__school_id",
        metadata={"course_id": "course_id", "student_id": "student_id"},
    ),
    Tracked(
        model="users.CustomUser",
        action=AuditAction.PERMISSION_CHANGE,
        # Not token_epoch (SM note 1), password, email, names or counters.
        fields={
            "user_type": "user_type",
            "is_active": "is_active",
            "is_staff": "is_staff",
            "is_superuser": "is_superuser",
            "school_id": "school_id",
        },
        school="school_id",
        create_filter=_privileged_user,
    ),
    Tracked(
        model="billing.UserSubscription",
        action=AuditAction.SUBSCRIPTION_CHANGE,
        fields={
            "plan_id": "plan_id",
            "is_active": "is_active",
            "is_trial": "is_trial",
            "stripe_status": "stripe_status",
            "billing_cycle_end": "billing_cycle_end",
            "cancelled_at": "cancelled_at",
            "auto_renew": "auto_renew",
        },
        school="user__school_id",
    ),
    Tracked(
        model="billing.LicenseSubscription",
        action=AuditAction.SUBSCRIPTION_CHANGE,
        fields={
            "plan_id": "plan_id",
            "is_active": "is_active",
            "max_seats": "max_seats",
            "stripe_status": "stripe_status",
            "billing_cycle_end": "billing_cycle_end",
            "auto_renew": "auto_renew",
        },
        school="school_id",
    ),
    Tracked(
        # A seat in use (SM R4).
        model="billing.SchoolCreditAllocation",
        action=AuditAction.SUBSCRIPTION_CHANGE,
        fields={"is_active": "is_active", "license_subscription_id": "license_id"},
        school="license_subscription__school_id",
    ),
)


def tracked_for(model) -> Optional[Tracked]:
    label = model._meta.label
    for spec in REGISTRY:
        if spec.model == label:
            return spec
    return None


def registry_problems() -> list[str]:
    """Every registered field must be allowed in its action's before/after,
    and must be a real concrete field of its model. Asserted by a test."""
    problems = []
    for spec in REGISTRY:
        allowed = BEFORE_AFTER_ALLOWLIST.get(spec.action, frozenset())
        attnames = {f.attname for f in spec.model_class()._meta.concrete_fields}
        for attname, key in spec.fields.items():
            if key not in allowed:
                problems.append(f"{spec.model}.{attname}: {key} not allowed")
            if attname not in attnames:
                problems.append(f"{spec.model}.{attname}: not a concrete field")
    return problems


# ---------------------------------------------------------------------------
# Suppression and actor

_suppressed: ContextVar[int] = ContextVar("audit_history_suppressed", default=0)


@contextmanager
def suppressed():
    """No history event from signals inside the block: the AI grading save
    (its values go on GRADING_COMPLETED) and record_bulk's own writes."""
    token = _suppressed.set(_suppressed.get() + 1)
    try:
        yield
    finally:
        _suppressed.reset(token)


_acting_as: ContextVar[Any] = ContextVar("audit_history_acting_as", default=None)


@contextmanager
def acting_as(user):
    """Name `user` as the actor of history events written inside the block,
    for a save on a request that has no signed-in user yet: the account
    activation at /auth/verify and the Google sign-in resurrection (SM
    ruling). `user` must be the account THIS request just authenticated -
    the one that proved the code or the Google identity - never a value
    taken from request input."""
    token = _acting_as.set(user)
    try:
        yield
    finally:
        _acting_as.reset(token)


def _actor():
    """The actor for a signal-written event: `acting_as`'s user if set, else
    S3's rule - the request's signed-in user - else the operator of the
    management command being run (H-69), or None (SYSTEM)."""
    return _acting_as.get() or current_request_actor() or current_command_actor()


# ---------------------------------------------------------------------------
# Values


def scalar(value):
    """A stored field value as an audit scalar. A Decimal keeps its exact
    digits as a string; a time is ISO 8601; an id is its string."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


def _lookups(spec):
    lookups = list(spec.fields)
    if spec.school:
        lookups.append(spec.school)
    lookups.extend(spec.metadata.values())
    return list(dict.fromkeys(lookups))


def _stored(spec, pks):
    """{pk: {lookup: value}} for the rows, from the database."""
    manager = spec.model_class()._base_manager
    rows = manager.filter(pk__in=pks).values("pk", *_lookups(spec))
    return {row["pk"]: row for row in rows}


def _diff(spec, before_row, after_row, only=None):
    """(before, after, changed keys) over the tracked fields, or None if no
    tracked field changed. `only` limits it to the attnames a save wrote."""
    before, after = {}, {}
    for attname, key in spec.fields.items():
        if only is not None and attname not in only:
            continue
        old = scalar(before_row.get(attname)) if before_row is not None else None
        new = scalar(after_row.get(attname)) if after_row is not None else None
        if before_row is not None and after_row is not None and old == new:
            continue
        if before_row is not None:
            before[key] = old
        if after_row is not None:
            after[key] = new
    changed = sorted(set(before) | set(after))
    if not changed:
        return None
    return (
        before if before_row is not None else None,
        after if after_row is not None else None,
        changed,
    )


def _emit(spec, pk, context_row, change, *, actor, source):
    before, after, changed = change
    metadata = {
        key: scalar(context_row.get(path)) for key, path in spec.metadata.items()
    }
    metadata.update(changed_fields=changed, source=source)
    return emit(
        spec.action,
        actor=actor,
        request=current_request(),
        target_type=spec.target_type,
        target_id=pk,
        school_id=context_row.get(spec.school) if spec.school else None,
        before=before,
        after=after,
        metadata=metadata,
    )


def snapshot(instance) -> dict:
    """The tracked values of `instance` as stored now, keyed as recorded.
    For GRADING_COMPLETED's before/after around the AI grading save."""
    spec = tracked_for(type(instance))
    if spec is None or instance.pk is None:
        return {}
    row = _stored(spec, [instance.pk]).get(instance.pk)
    if row is None:
        return {}
    return {key: scalar(row.get(attname)) for attname, key in spec.fields.items()}


def grade_change(before: dict, after: dict):
    """The changed grade fields between two snapshots, as (before, after),
    for GRADING_COMPLETED. Unchanged fields are left out of both."""
    changed = [key for key in after if before.get(key) != after.get(key)]
    return (
        {key: before.get(key) for key in changed},
        {key: after.get(key) for key in changed},
    )


# ---------------------------------------------------------------------------
# Signals: instance saves and deletes

_BEFORE_ATTR = "_audit_history_before"


def _on_pre_save(sender, instance, raw=False, update_fields=None, **kwargs):
    spec = tracked_for(sender)
    if spec is None or raw or _suppressed.get():
        return
    if instance._state.adding or instance.pk is None:
        setattr(instance, _BEFORE_ATTR, None)
        return
    if update_fields is not None and not _written(spec, update_fields):
        setattr(instance, _BEFORE_ATTR, False)  # nothing tracked is written
        return
    setattr(instance, _BEFORE_ATTR, _stored(spec, [instance.pk]).get(instance.pk))


def _written(spec, update_fields) -> set:
    """The tracked attnames a save with `update_fields` writes. A FK may be
    named either way: `plan` or `plan_id`."""
    named = set(update_fields)
    return {
        attname
        for attname in spec.fields
        if attname in named
        or (attname.endswith("_id") and attname[: -len("_id")] in named)
    }


def _on_post_save(
    sender, instance, created=False, raw=False, update_fields=None, **kwargs
):
    spec = tracked_for(sender)
    if spec is None or raw or _suppressed.get():
        return
    before_row = getattr(instance, _BEFORE_ATTR, None)
    if before_row is False:
        return
    if created:
        if not spec.record_create:
            return
        if spec.create_filter is not None and not spec.create_filter(instance):
            return
        before_row = None
    elif before_row is None:
        # An update of a row that did not exist before the save (or whose
        # pre_save was skipped): nothing to compare against.
        return
    stored = _stored(spec, [instance.pk]).get(instance.pk)
    if stored is None:
        return
    only = _written(spec, update_fields) if update_fields is not None else None
    change = _diff(spec, before_row, stored, only)
    if change is None:
        return
    _emit(
        spec,
        instance.pk,
        stored,
        change,
        actor=_actor(),
        source="create" if created else "save",
    )


def _on_pre_delete(sender, instance, **kwargs):
    spec = tracked_for(sender)
    if spec is None or not spec.record_delete or _suppressed.get():
        return
    stored = _stored(spec, [instance.pk]).get(instance.pk)
    if stored is None:
        return
    change = _diff(spec, stored, None)
    if change is None:
        return
    _emit(spec, instance.pk, stored, change, actor=_actor(), source="delete")


def connect():
    """Connect the history receivers for every registered model. Called from
    AuditConfig.ready()."""
    for spec in REGISTRY:
        model = spec.model_class()
        uid = f"audit_history:{spec.model}"
        pre_save.connect(_on_pre_save, sender=model, dispatch_uid=f"{uid}:pre_save")
        post_save.connect(_on_post_save, sender=model, dispatch_uid=f"{uid}:post_save")
        pre_delete.connect(
            _on_pre_delete, sender=model, dispatch_uid=f"{uid}:pre_delete"
        )


# ---------------------------------------------------------------------------
# Queryset writes

_REQUEST_ACTOR = object()


def record_bulk(queryset, *, actor=_REQUEST_ACTOR, **changes) -> int:
    """`queryset.update(**changes)`, plus one history event per row whose
    tracked values changed. Returns the number of rows updated, as `.update()`
    does. `before` is read under a row lock in the same transaction as the
    update, so a concurrent writer cannot slip between the two.

    `actor` is keyword-only. Leave it out to use the request's signed-in user
    (or SYSTEM). Pass it only with an object the code itself established -
    never a value taken from request input (SM ruling on R3)."""
    spec = tracked_for(queryset.model)
    if spec is None:
        raise ValueError(f"{queryset.model._meta.label} is not a tracked model")
    who = _actor() if actor is _REQUEST_ACTOR else actor
    with transaction.atomic():
        # The caller's queryset may be DISTINCT or annotated (the admin
        # changelist can be), and Postgres refuses FOR UPDATE on those. So
        # the rows are locked through the base manager by pk, in pk order
        # (no lock-order deadlock between two bulk writers), and the update
        # below still carries the caller's own filter: a conditional claim
        # (publish's is_published=False) is re-checked after the lock.
        pks = list(queryset.order_by().values_list("pk", flat=True))
        if not pks:
            return 0
        pks = list(
            spec.model_class()
            ._base_manager.select_for_update()
            .filter(pk__in=pks)
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        before = _stored(spec, pks)
        with suppressed():
            # The caller's filter rides along as a subquery (DISTINCT and
            # GROUP BY are fine there), evaluated now, after the lock.
            count = (
                spec.model_class()
                ._base_manager.filter(
                    pk__in=queryset.filter(pk__in=pks).order_by().values("pk")
                )
                .update(**changes)
            )
        after = _stored(spec, pks)
        for pk in pks:
            if pk not in before or pk not in after:
                continue
            change = _diff(spec, before[pk], after[pk])
            if change is not None:
                _emit(spec, pk, after[pk], change, actor=who, source="bulk")
    return count
