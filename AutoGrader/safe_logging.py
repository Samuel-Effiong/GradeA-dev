"""Describe an error for a log line without its text, its values or its traceback.

H-208. A broker error's own text can carry the broker's address, and an
exception's text in general is whatever the code that raised it wrote. The log
policy is ids and the error's TYPE. This module builds the part of a line that
is about the error:

* a broker outage (`BROKER_UNAVAILABLE_ERRORS`, also when the error that
  reaches the line is a wrapper raised `from` one) is named by the class of the
  broker error and nothing else;
* any other error is named by its class and by its stack FRAMES only
  (file:line:function, from the traceback, without the message and without any
  local value), so a real programming fault stays locatable when this line is
  the only report of it.
"""

from __future__ import annotations

import os
import traceback
from collections.abc import Iterator


def _chain(error: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


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
