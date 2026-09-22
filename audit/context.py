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
