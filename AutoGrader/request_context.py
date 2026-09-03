"""
Per-request/per-task correlation id.

A single id is generated (or read from an inbound `X-Request-ID` header) at
the top of each web request, and propagated to three places so a support
ticket ("my grading failed around 2pm") can be traced end to end:

  1. Every log line emitted while handling that request (via `RequestIDLogFilter`
     below, wired into LOGGING in settings.py).
  2. The Sentry event/tag for that request (set in AutoGrader.middleware).
  3. Any Celery task dispatched from that request — including tasks a worker
     dispatches while processing an earlier task, so a chain of dispatches
     keeps the same id (see AutoGrader.celery_signals).

`REQUEST_ID` is a contextvar rather than a global because Django's WSGI
workers and Celery's prefork workers both reuse a single OS process/thread
across many requests/tasks: a plain module-level variable would leak the
previous request's id into the next one. contextvars.ContextVar is safe
across threads and asyncio tasks (each gets its own value), which a global
is not.
"""

from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar, Token
from typing import Optional

# The header used both to accept an inbound id (e.g. from a proxy/CDN that
# already assigns one) and to echo the id back on the response, so a client
# can surface it in a support request.
REQUEST_ID_HEADER = "X-Request-ID"

# Celery message-header / log key. Kept identical to the HTTP header's
# meaning (same id) but separate constants since one is HTTP-cased and the
# other is a plain dict key used by the Celery signal handlers.
CELERY_HEADER_KEY = "request_id"

# A generated id is a bare uuid4 hex (32 lowercase hex chars, no dashes) -
# short, URL-safe, and cheap to generate. An inbound, client- or
# proxy-supplied id is accepted too, but only if it is a plausible token:
# this value is logged verbatim and echoed back on the response, so it must
# never be allowed to contain characters that could break log parsing or be
# used to inject its way into anything (headers, log lines are newline-
# delimited; commas/pipes are common log-field separators in this codebase's
# aggregation tooling).
_VALID_INBOUND_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

_request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


def generate_request_id() -> str:
    return uuid.uuid4().hex


def is_valid_request_id(value: Optional[str]) -> bool:
    if not value:
        return False
    return bool(_VALID_INBOUND_ID_RE.match(value))


def get_request_id() -> Optional[str]:
    """The current request/task's correlation id, or None outside any."""
    return _request_id_var.get()


def set_request_id(value: str) -> Token:
    """Set the current context's request id. Returns a token for reset()."""
    return _request_id_var.set(value)


def reset_request_id(token: Token) -> None:
    """Undo a set_request_id() call, restoring the prior value (usually None).

    Always call this in a `finally` block. Without it, a thread-reused WSGI
    worker or a prefork Celery worker process would leak one request's id
    into whatever unrelated request/task it handles next.
    """
    _request_id_var.reset(token)


class RequestIDLogFilter(logging.Filter):
    """Injects the current request id into every LogRecord as `request_id`.

    Wired into LOGGING["handlers"]["console"]["filters"] in settings.py.
    Logging filters run on every record regardless of which logger emitted
    it, so this covers Django's own loggers, this project's `ai_processor`/
    `students` loggers, and third-party library loggers alike - not just
    code that explicitly opts in.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id() or "-"
        return True
