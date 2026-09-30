"""The server-generated trace id that ties one action's audit events together.

X-5: an audit trail's correlation id must not be something a client can choose.
`RequestIDMiddleware` trusts an inbound `X-Request-ID` (right for logging: a
retrying frontend shares one id), so any user could send another action's id
and land in its trail. The audit trace id is therefore a separate value, minted
here by the server. The client's id is stored beside it as untrusted context.

Outside any `trace_context()`, `audit.emitter._resolve_trace_id()` falls back
to the id `AutoGrader.request_context` already propagates across a web request
and every Celery hop it dispatches (confirmed wired end to end - see that
module), and only mints a fresh id if neither is available. So `trace_context()`
itself is for an explicit, deliberate grouping (tests, and any future caller
that wants to join a trace it doesn't otherwise have) - most real call sites
need it for neither; the request-scoped id already applies automatically. The
value passed to `trace_context()` must always come from the server, never from
a request header.
"""

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional

_trace_var: ContextVar[Optional[uuid.UUID]] = ContextVar("audit_trace_id", default=None)


def current_trace_id() -> Optional[uuid.UUID]:
    """The trace id of the enclosing `trace_context()`, or None outside one."""
    return _trace_var.get()


def _as_uuid(value) -> Optional[uuid.UUID]:
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


@contextmanager
def trace_context(trace_id=None):
    """Run a block whose audit events all share one trace id.

    With no argument a fresh id is minted. An id that is not a UUID is replaced
    by a fresh one rather than raising: this runs on the user's request path,
    and a bad value must never fail the action it is meant to record.
    """
    value = _as_uuid(trace_id) or uuid.uuid4()
    token = _trace_var.set(value)
    try:
        yield value
    finally:
        _trace_var.reset(token)


# ---------------------------------------------------------------------------
# Per-request audit state (Epic A completion S1, plan 08 §2).
#
# FR-A-01's "exactly one event per action" needs to know, when a request ends,
# whether any NAMED event (login, grading, credit, CRUD, admin ...) is still
# recorded for it; only if none is does `audit.middleware.AuditMiddleware`
# write the generic STATE_CHANGE. The emitter records the id of every stored
# event, so no call site has to remember to.
#
# Ids, not a yes/no flag (Verification Engineer's R1): an event is stored in a
# savepoint inside the caller's transaction, so if that transaction later rolls
# back the event is gone. A flag would still say "recorded" and the request
# would end with no event at all. The middleware checks the ids still exist.
#
# A ContextVar, not a request attribute, because the emitter is called from
# services and model methods that never see the request. Outside a request
# (Celery, management commands, tests without the middleware) there is no
# state and marking is a no-op.


class RequestAuditState:
    __slots__ = ("stored_event_ids",)

    def __init__(self):
        self.stored_event_ids = []


_request_state_var: ContextVar[Optional[RequestAuditState]] = ContextVar(
    "audit_request_state", default=None
)


@contextmanager
def request_audit_state():
    """Open a fresh per-request state for the block, and always restore the
    previous one after it, even if the block raises."""
    state = RequestAuditState()
    token = _request_state_var.set(state)
    try:
        yield state
    finally:
        _request_state_var.reset(token)


def record_stored_event(event_id) -> None:
    """Remember that `event_id` was stored for the current request, if any."""
    state = _request_state_var.get()
    if state is not None:
        state.stored_event_ids.append(event_id)


def a_surviving_event_names(state, user) -> bool:
    """Whether an event stored during the request still exists AND names
    `user` (the requester) as its actor.

    - One stored in an atomic block that later rolled back is gone with it
      (R1).
    - One that names someone else does not count (V1): a school admin's
      add_teachers stores the TEACHER's CREDIT_TRANSACTION (the wallet owner
      is its actor), and that must not stand in for the admin's own trace.

    On any error this answers False, so the generic event is written: a
    second event is better than none."""
    # An anonymous requester has no pk, and filtering on actor_id=None would
    # match NULL-actor events. Answer False without asking: harmless, since
    # the generic event is never written for an anonymous request
    # (request_audit.should_record), but it keeps the check honest.
    if not state.stored_event_ids or not getattr(user, "is_authenticated", False):
        return False
    try:
        from .models import AuditEvent

        return AuditEvent.objects.filter(
            pk__in=state.stored_event_ids, actor_id=getattr(user, "pk", None)
        ).exists()
    except Exception:  # noqa: BLE001 - never fail the response
        return False
