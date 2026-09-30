"""The one way to write an audit event.

    from audit.emitter import emit
    from audit.enums import AuditAction

    emit(AuditAction.ASSIGNMENT_COPY, actor=request.user, request=request,
         target_type="Assignment", target_id=assignment.id)

What `emit` guarantees:

* FR-A-02 - the schema is fixed. The action must be a known `AuditAction`, the
  outcome and error class must agree (a non-success outcome carries an error
  class, a success carries none), ids must be UUIDs. Anything else is REJECTED:
  nothing is written and an operational error is logged.
* FR-A-04 / X-4 - a student's email, address and browser are never stored, and
  `metadata`, `before` and `after` go through the allow-list in `metadata.py`.
* X-5 - the trace id is the server's, never the client's: an explicit
  `audit.context.trace_context()` if one is open, else the id
  `AutoGrader.request_context` already propagates across the request and every
  Celery hop it dispatches, else a fresh one. The client's own `X-Request-ID`
  is kept separately as untrusted `client_correlation_id`, never as `trace_id`.
* A6 - the retention class is derived, never chosen: student-record actions are
  kept three years, and a caller can raise (never lower) an event to that class.
* FR-A-11 - it never raises into the user's action. A rejected write, a broken
  table or an unreachable database all end as `None` plus one error log line.
  The write runs in its own savepoint so a failed INSERT cannot poison the
  caller's transaction.

The error log line names the action and the KIND of failure only. It never
includes an exception's text or any value from the event: database errors quote
the failing row, which would carry the very data this module keeps out.

A row is written inside the caller's transaction, so it is rolled back with it.
Emit a failure event after the failing transaction has rolled back, not inside.
"""

import ipaddress
import logging
import re
import uuid
from typing import Optional

from django.db import transaction

from AutoGrader.request_context import client_request_id_from_header, get_request_id

from . import metrics as audit_metrics
from .context import current_trace_id, record_stored_event
from .enums import (
    STUDENT_RECORD_ACTIONS,
    ActorRole,
    AuditAction,
    AuditOutcome,
    ErrorClass,
    RetentionClass,
)
from .metadata import sanitise, sanitise_metadata_for_action
from .models import AuditEvent

# BE-A-09 #2: grading never falls back to a nano-tier model (see
# ai_processor/services.py's execute_graded_task), only to this short list -
# so "did grading use the fallback model" is "is the model actually used one
# of these". Imported here, not at ai_processor.services import time, to
# keep this chokepoint's own import surface small (mirrors _client_ip's
# local import below).
_GRADING_ACTIONS = frozenset(
    {AuditAction.GRADING_COMPLETED, AuditAction.GRADING_FAILED}
)

logger = logging.getLogger(__name__)

_TARGET_TYPE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_REASON_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_CLIENT_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
_SAFE_ACTION = re.compile(r"[A-Z][A-Z0-9_]{0,63}")

MAX_USER_AGENT = 512
MAX_EMAIL = 254


class AuditValidationError(ValueError):
    """A write that breaks the fixed schema. The message is a fixed phrase and
    never contains a caller-supplied value, so it is safe to log."""


def _resolve_trace_id() -> uuid.UUID:
    """X-5 / FR-A-03: the id that ties one action's audit events, logs and
    Celery tasks together - server-authoritative, never a client's own value.

    Order: an explicit `audit.context.trace_context()` (a caller that is
    deliberately joining a trace - tests, and any future explicit grouping)
    wins first. Otherwise, the id `AutoGrader.request_context` already
    propagates across the web request and every Celery hop it dispatches
    (`RequestIDMiddleware`, `celery_signals.py` - confirmed wired end to end).
    That id is always minted by the server (S5 part 0): the middleware never
    adopts an inbound `X-Request-ID`, which it keeps apart as
    `client_request_id`. Anything else that set it is still parsed, never
    used as-is: a value that is not a UUID is dropped. With neither
    available, a fresh id is minted so every event still gets one.
    """
    explicit = current_trace_id()
    if explicit is not None:
        return explicit
    inbound = get_request_id()
    if inbound:
        try:
            return uuid.UUID(inbound)
        except ValueError:
            pass
    return uuid.uuid4()


def resolve_trace_id() -> uuid.UUID:
    """The server trace id for the current request or task (see
    `_resolve_trace_id`). Public for other layers that log against the same
    trace, e.g. the AI provider call (S5)."""
    return _resolve_trace_id()


def emit(
    action,
    *,
    actor=None,
    target_type,
    target_id=None,
    outcome=AuditOutcome.SUCCESS,
    error_class=None,
    reason_code=None,
    request=None,
    school_id=None,
    department_id=None,
    before=None,
    after=None,
    metadata=None,
    touches_student_record=False,
    strict=False,
):
    """Record one audit event. Returns the saved `AuditEvent`, or None if it
    was rejected or could not be stored.

    `strict=True` re-raises `AuditValidationError` for a rejected write so a
    call-site test can see the mistake. It never makes a storage failure raise.
    """
    label = _safe_label(action)
    try:
        fields, dropped = _build(
            action,
            actor=actor,
            target_type=target_type,
            target_id=target_id,
            outcome=outcome,
            error_class=error_class,
            reason_code=reason_code,
            request=request,
            school_id=school_id,
            department_id=department_id,
            before=before,
            after=after,
            metadata=metadata,
            touches_student_record=touches_student_record,
        )
    except AuditValidationError as exc:
        _alert("rejected", label, str(exc))
        if strict:
            raise
        return None
    except Exception as exc:  # noqa: BLE001 - a bug here must not fail the user
        _alert("rejected", label, f"unexpected {type(exc).__name__}")
        return None

    if dropped:
        # A call site sent something the allow-list refuses. The event is still
        # worth recording; the offending KEYS are reported, never their values.
        keys = ", ".join(sorted({f"{key} ({why})" for key, why in dropped}))
        logger.error(
            "audit event metadata dropped: action=%s keys=%s",
            label,
            keys,
            extra={"audit_kind": "metadata_dropped", "audit_action": label},
        )

    try:
        with transaction.atomic():
            event = AuditEvent.objects.create(**fields)
    except Exception as exc:  # noqa: BLE001 - FR-A-11: never fail the user action
        _alert("store_failed", label, type(exc).__name__)
        # BE-A-09 #5: this is specifically the write failure FR-A-11 exists
        # to swallow - the signal an alert needs is that it happened at
        # all, not what it was, so the tag stays limited to the action.
        audit_metrics.count("audit_emit_failures_total", tags={"action": label})
        return None

    # S1: the request has an event, so the generic STATE_CHANGE fallback is
    # not written - provided this row survives the request (the middleware
    # checks; see audit.context). A rejected or failed write records nothing.
    record_stored_event(event.pk)
    _emit_alertable_metrics(action, outcome, fields)
    return event


# ---------------------------------------------------------------------------


def _safe_label(action) -> str:
    """The action as it may appear in a log line."""
    text = getattr(action, "value", action)
    if isinstance(text, str) and _SAFE_ACTION.fullmatch(text):
        return text
    return "<invalid-action>"


def _alert(kind, label, detail):
    logger.error(
        "audit event %s: action=%s detail=%s",
        kind,
        label,
        detail,
        extra={"audit_kind": kind, "audit_action": label, "audit_detail": detail},
    )


def _build(
    action,
    *,
    actor,
    target_type,
    target_id,
    outcome,
    error_class,
    reason_code,
    request,
    school_id,
    department_id,
    before,
    after,
    metadata,
    touches_student_record,
):
    try:
        action = AuditAction(action)
    except ValueError:
        raise AuditValidationError("action: not a known action") from None

    if not isinstance(target_type, str) or not _TARGET_TYPE.fullmatch(target_type):
        raise AuditValidationError("target_type: must be a model name")

    try:
        outcome = AuditOutcome(outcome)
    except ValueError:
        raise AuditValidationError("outcome: not a valid outcome") from None
    if error_class is not None:
        try:
            error_class = ErrorClass(error_class)
        except ValueError:
            raise AuditValidationError("error_class: not a valid class") from None
    if outcome == AuditOutcome.SUCCESS and error_class is not None:
        raise AuditValidationError("error_class: a success has no error class")
    if outcome != AuditOutcome.SUCCESS and error_class is None:
        raise AuditValidationError("error_class: a non-success needs one")

    if reason_code is not None and not (
        isinstance(reason_code, str) and _REASON_CODE.fullmatch(reason_code)
    ):
        raise AuditValidationError("reason_code: not a valid code")

    role, actor_id, actor_email, actor_school = _actor_fields(actor)
    is_student = role == ActorRole.STUDENT
    request_fields = _request_fields(request, is_student)

    clean_metadata, dropped = sanitise_metadata_for_action(action, metadata)
    clean_before, dropped_before = sanitise(before)
    clean_after, dropped_after = sanitise(after)
    dropped = dropped + dropped_before + dropped_after

    student_record = touches_student_record or action in STUDENT_RECORD_ACTIONS
    fields = dict(
        actor_id=actor_id,
        actor_role=role,
        actor_email=actor_email,
        school_id=(
            _uuid_or_none(school_id, "school_id")
            if school_id is not None
            else actor_school
        ),
        department_id=_uuid_or_none(department_id, "department_id"),
        action=action.value,
        target_type=target_type,
        target_id=_uuid_or_none(target_id, "target_id"),
        outcome=outcome.value,
        error_class=error_class.value if error_class else None,
        reason_code=reason_code,
        trace_id=_resolve_trace_id(),
        retention_class=str(
            RetentionClass.STUDENT_RECORD if student_record else RetentionClass.GENERAL
        ),
        before=clean_before if before is not None else None,
        after=clean_after if after is not None else None,
        metadata=clean_metadata,
        **request_fields,
    )
    return fields, dropped


def _emit_alertable_metrics(action, outcome, fields):
    """BE-A-09 #1 / #2 / #4: fired once per successfully STORED event, from
    the validated (not caller-supplied) `action`/`outcome`/`metadata` - so a
    rejected or malformed write can't feed a false metric. #3 (credit ledger
    anomalies) and #5 (this function's own store-failure sibling) are not
    here: #5 lives in the except block above it, and #3 is detected at the
    billing call sites that already compute the reconciliation math this
    module must not duplicate (see billing/models.py, billing/services.py,
    billing/tasks.py).
    """
    if action in _GRADING_ACTIONS:
        audit_metrics.distribution(
            "grading_failure_rate",
            0.0 if outcome == AuditOutcome.SUCCESS else 1.0,
        )
        if action == AuditAction.GRADING_COMPLETED:
            model = (fields.get("metadata") or {}).get("model")
            if model:
                # Local import: ai_processor.services pulls in the OpenAI
                # client and a large module surface this chokepoint has no
                # other reason to import at module load time.
                from ai_processor.services import GRADING_FALLBACK_MODELS

                audit_metrics.distribution(
                    "model_fallback_rate",
                    1.0 if model in GRADING_FALLBACK_MODELS else 0.0,
                )

    reason_code = fields.get("reason_code")
    if reason_code:
        audit_metrics.count("reason_code_rate", tags={"code": reason_code})


def _uuid_or_none(value, field):
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            pass
    raise AuditValidationError(f"{field}: not a UUID")


def _actor_fields(actor):
    """(role, actor_id, actor_email, school_id), captured as values."""
    if actor is None:
        return ActorRole.SYSTEM, None, None, None
    if not getattr(actor, "is_authenticated", True):
        # A request by someone not signed in. Never SYSTEM: the audit must not
        # say the app did what an unauthenticated caller did.
        return ActorRole.ANONYMOUS, None, None, None
    try:
        role = ActorRole(actor.user_type)
    except ValueError:
        raise AuditValidationError("actor: unknown user type") from None
    actor_id = _uuid_or_none(actor.id, "actor_id")
    email = None
    if role != ActorRole.STUDENT:  # data minimisation, X-4
        email = (getattr(actor, "email", None) or "")[:MAX_EMAIL] or None
    school = getattr(actor, "school_id", None)
    return role, actor_id, email, _uuid_or_none(school, "school_id")


def _request_fields(request, is_student):
    """Address, browser and the client's own correlation id, from `request`.

    A student's address and browser are never recorded. Everything is optional:
    a missing or odd request just yields nulls."""
    fields: dict[str, Optional[str]] = {
        "source_ip": None,
        "user_agent": None,
        "client_correlation_id": None,
    }
    meta = getattr(request, "META", None)
    if not isinstance(meta, dict):
        return fields

    # The client's own id (a UUID, or nothing), never the server's request
    # id, which is this event's trace id (S5 part 0).
    client_id = getattr(
        request, "client_request_id", None
    ) or client_request_id_from_header(meta.get("HTTP_X_REQUEST_ID"))
    if isinstance(client_id, str) and _CLIENT_ID.fullmatch(client_id):
        fields["client_correlation_id"] = client_id

    if is_student:
        return fields

    fields["source_ip"] = _client_ip(request)
    agent = meta.get("HTTP_USER_AGENT")
    if isinstance(agent, str):
        agent = agent.replace("\x00", "").strip()[:MAX_USER_AGENT]
        fields["user_agent"] = agent or None
    return fields


def _client_ip(request):
    """The address the edge proxy observed, by the same rule the rate limiter
    uses (settings `NUM_PROXIES`), so a client cannot forge its own."""
    from rest_framework.throttling import BaseThrottle

    try:
        return str(ipaddress.ip_address(BaseThrottle().get_ident(request)))
    except (ValueError, TypeError):
        return None
