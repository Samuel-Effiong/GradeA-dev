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
* X-5 - the trace id is the server's (`audit.context`). The client's
  `X-Request-ID` is kept as untrusted `client_correlation_id`.
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

from .context import current_trace_id
from .enums import (
    STUDENT_RECORD_ACTIONS,
    ActorRole,
    AuditAction,
    AuditOutcome,
    ErrorClass,
    RetentionClass,
)
from .metadata import sanitise
from .models import AuditEvent

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
    license_id=None,
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
            license_id=license_id,
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
            return AuditEvent.objects.create(**fields)
    except Exception as exc:  # noqa: BLE001 - FR-A-11: never fail the user action
        _alert("store_failed", label, type(exc).__name__)
        return None


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
    license_id,
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

    role, actor_id, actor_email, actor_license = _actor_fields(actor)
    is_student = role == ActorRole.STUDENT
    request_fields = _request_fields(request, is_student)

    clean_metadata, dropped = sanitise(metadata)
    clean_before, dropped_before = sanitise(before)
    clean_after, dropped_after = sanitise(after)
    dropped = dropped + dropped_before + dropped_after

    student_record = touches_student_record or action in STUDENT_RECORD_ACTIONS
    fields = dict(
        actor_id=actor_id,
        actor_role=role,
        actor_email=actor_email,
        license_id=(
            _uuid_or_none(license_id, "license_id")
            if license_id is not None
            else actor_license
        ),
        department_id=_uuid_or_none(department_id, "department_id"),
        action=action.value,
        target_type=target_type,
        target_id=_uuid_or_none(target_id, "target_id"),
        outcome=outcome.value,
        error_class=error_class.value if error_class else None,
        reason_code=reason_code,
        trace_id=current_trace_id() or uuid.uuid4(),
        retention_class=str(
            RetentionClass.STUDENT_RECORD if student_record else RetentionClass.GENERAL
        ),
        before=clean_before if before is not None else None,
        after=clean_after if after is not None else None,
        metadata=clean_metadata,
        **request_fields,
    )
    return fields, dropped


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
    """(role, actor_id, actor_email, license_id), captured as values."""
    if actor is None or not getattr(actor, "is_authenticated", True):
        return ActorRole.SYSTEM, None, None, None
    try:
        role = ActorRole(actor.user_type)
    except ValueError:
        raise AuditValidationError("actor: unknown user type") from None
    actor_id = _uuid_or_none(actor.id, "actor_id")
    email = None
    if role != ActorRole.STUDENT:  # data minimisation, X-4
        email = (getattr(actor, "email", None) or "")[:MAX_EMAIL] or None
    licence = getattr(actor, "school_id", None)
    return role, actor_id, email, _uuid_or_none(licence, "license_id")


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

    client_id = getattr(request, "request_id", None) or meta.get("HTTP_X_REQUEST_ID")
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
