"""Describe an error for a log line without its text, its values or its traceback.

H-208. A broker error's own text can carry the broker's address, and an
exception's text in general is whatever the code that raised it wrote. The log
policy is ids and the error's TYPE. This module builds the part of a line that
is about the error:

* a broker outage (`BROKER_UNAVAILABLE_ERRORS`, also when the error that
  reaches the line is a wrapper raised EXPLICITLY `from` one; an error that
  only has the broker error as its implicit context is described as itself)
  is named by the class of the
  broker error and nothing else;
* any other error is named by its class and by its stack FRAMES only
  (file:line:function, from the traceback, without the message and without any
  local value), so a real programming fault stays locatable when this line is
  the only report of it.
"""

from __future__ import annotations

import logging
import os
import traceback
from collections.abc import Iterator


def _chain(error: BaseException) -> Iterator[BaseException]:
    """The error and its explicit causes (`raise ... from`). The implicit
    __context__ is NOT followed: an error raised WHILE a broker error was being
    handled is a different fault and must not be hidden behind "broker
    unavailable"."""
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__


def _frames(error: BaseException) -> str:
    return " <- ".join(
        f"{os.path.basename(frame.f_code.co_filename)}:{lineno}:{frame.f_code.co_name}"
        for frame, lineno in traceback.walk_tb(error.__traceback__)
    )


def describe_error_for_log(error: BaseException) -> str:
    """`error=<Class>` for a broker outage, `error=<Class> frames=<f:l:fn <- ...>`
    for anything else. Never the message, never a traceback, never a value."""
    # Imported here: AutoGrader.dispatch uses this module for its own log line.
    from AutoGrader.dispatch import BROKER_UNAVAILABLE_ERRORS

    for link in _chain(error):
        if isinstance(link, BROKER_UNAVAILABLE_ERRORS):
            return f"error={type(link).__name__}"
    frames = _frames(error)
    if frames:
        return f"error={type(error).__name__} frames={frames}"
    return f"error={type(error).__name__}"


# H-218: the keys of Celery's trace context that are kept; `exc` is rewritten,
# and `traceback`, `args` and `kwargs` (the task's arguments) are left out.
_CELERY_TRACE_KEPT = ("hostname", "id", "name", "description", "internal")
RETRY_TEXT_OMITTED = "error text omitted"


class CeleryTraceFilter(logging.Filter):
    """H-218. Celery's own task lines (logger `celery.app.trace`, Celery 5.5.3)
    log the error's repr, its traceback and the task's arguments: a failure as
    "Task <name>[<id>] raised unexpected: <repr>" with `exc_info`, and the
    traceback text and arguments again in the argument dict (also
    `record.data`); a retry as "Task <name>[<id>] retry: <text>".

    For a record whose argument dict has `exc`, this keeps the line, its level,
    the task's name and id, names the error by `describe_error_for_log` (or
    says RETRY_TEXT_OMITTED when only the text is there, as on a retry line),
    and drops the traceback and the arguments. Any other record passes
    untouched. It never raises: a filter's exception would come out of
    Celery's tracer."""

    def filter(self, record: logging.LogRecord) -> bool:
        context = record.args
        if not (isinstance(context, dict) and "exc" in context):
            return True
        error = record.exc_info[1] if record.exc_info else None
        if error is None:
            exc = RETRY_TEXT_OMITTED
        else:
            try:
                exc = describe_error_for_log(error)
            except Exception:
                exc = f"error={type(error).__name__}"
        kept = {key: context[key] for key in _CELERY_TRACE_KEPT if key in context}
        kept["exc"] = exc
        record.args = kept
        record.__dict__["data"] = kept
        record.exc_info = None
        record.exc_text = None
        return True
