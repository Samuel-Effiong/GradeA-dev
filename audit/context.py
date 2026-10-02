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

import re
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
    __slots__ = ("stored_event_ids", "suppressed", "request")

    def __init__(self, request=None):
        self.stored_event_ids = []
        # S1b: an event this request would have written was held back by the
        # failed-auth cap (and is counted in its summary), so no fallback
        # event may stand in for it.
        self.suppressed = False
        # S3: the Django request, so a chokepoint deep in the call stack
        # (the credit ledger) can name the request's actor. DRF writes the
        # authenticated user back onto it during the view.
        self.request = request


_request_state_var: ContextVar[Optional[RequestAuditState]] = ContextVar(
    "audit_request_state", default=None
)


@contextmanager
def request_audit_state(request=None):
    """Open a fresh per-request state for the block, and always restore the
    previous one after it, even if the block raises."""
    state = RequestAuditState(request)
    token = _request_state_var.set(state)
    try:
        yield state
    finally:
        _request_state_var.reset(token)


def current_request():
    """The Django request being handled, or None outside a request. Epic A
    S4's history events pass it to the emitter for the request fields (trace
    id, source address), as the explicit call sites do."""
    state = _request_state_var.get()
    return getattr(state, "request", None)


def current_request_actor():
    """S3's actor rule: the signed-in user of the request being handled, or
    None - which the emitter records as SYSTEM - outside a request (Celery,
    Beat, management commands) or for an anonymous request."""
    state = _request_state_var.get()
    user = getattr(getattr(state, "request", None), "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


def record_stored_event(event_id) -> None:
    """Remember that `event_id` was stored for the current request, if any."""
    state = _request_state_var.get()
    if state is not None:
        state.stored_event_ids.append(event_id)


def record_suppressed_event() -> None:
    """Mark the current request, if any, as having had an event suppressed by
    the failed-auth cap (S1b)."""
    state = _request_state_var.get()
    if state is not None:
        state.suppressed = True


def a_stored_event_survives(state) -> bool:
    """Whether any event stored during the request still exists, whoever it
    names. Used for anonymous sign-in doors (S2), where the door's own event
    names the targeted account or no one. Errors answer False: a second
    event is better than none."""
    if not state.stored_event_ids:
        return False
    try:
        from .models import AuditEvent

        return AuditEvent.objects.filter(pk__in=state.stored_event_ids).exists()
    except Exception:  # noqa: BLE001 - never fail the response
        return False


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


# ---------------------------------------------------------------------------
# The operator of a management command (H-69).
#
# A command has no request, so S3's rule records everything it writes as
# SYSTEM: the trail says a row changed, not which super admin ran the command.
# `command_actor` names that operator for the block. The emitter applies it to
# any event that would otherwise be SYSTEM - which covers the history signals
# and `record_bulk`, since they pass their actor to `emit` - and every event
# stored inside the block also carries `metadata["command"]`, the command's
# name.
#
# Both values are established by the server, never taken as free text: the
# user must be an active super admin, and the name must be a management
# command that exists.

_COMMAND_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}")


class _CommandContext:
    __slots__ = ("user", "command")

    def __init__(self, user, command):
        self.user = user
        self.command = command


_command_var: ContextVar[Optional[_CommandContext]] = ContextVar(
    "audit_command_actor", default=None
)


def _is_active_super_admin(user) -> bool:
    from users.models import UserTypes

    return bool(
        user is not None
        # A row loaded from (or saved to) the database, not one built in code.
        and not getattr(getattr(user, "_state", None), "adding", True)
        and getattr(user, "is_active", False)
        and getattr(user, "is_superuser", False)
        and getattr(user, "user_type", None) == UserTypes.SUPER_ADMIN
    )


def _is_known_command(command) -> bool:
    from django.core.management import get_commands

    return (
        isinstance(command, str)
        and _COMMAND_NAME.fullmatch(command) is not None
        and command in get_commands()
    )


@contextmanager
def command_actor(user, *, command):
    """Name `user` as the actor of every audit event written inside the block,
    and `command` as the management command that wrote it.

    `user` is the super admin a command's `--by` resolved (the same rule as
    `resolve_licence_stripe_intent`: an active SUPER_ADMIN). `command` is the
    command's own module name, e.g. `Path(__file__).stem`. Anything else
    raises ValueError before the block runs, so a command cannot write under
    a name nobody checked.

    Two cases that may surprise (SM ruling, each has a test):

    - `user` replaces only an actor that would have been SYSTEM. An actor
      the code already has is kept: one a call site passes to `emit()`, and
      the signed-in user of a request, if the block somehow runs inside one.
      `metadata["command"]` is added in both cases.
    - `source` is not touched. It still says how the row was written
      (create / save / bulk / delete), never "command"."""
    if not _is_active_super_admin(user):
        raise ValueError("command_actor: user must be an active super admin")
    if not _is_known_command(command):
        raise ValueError("command_actor: command must be a management command")
    token = _command_var.set(_CommandContext(user, command))
    try:
        yield
    finally:
        _command_var.reset(token)


def current_command_actor():
    """The super admin named by the enclosing `command_actor()`, or None."""
    context = _command_var.get()
    return context.user if context is not None else None


def current_command():
    """The command named by the enclosing `command_actor()`, or None."""
    context = _command_var.get()
    return context.command if context is not None else None
