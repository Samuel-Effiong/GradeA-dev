"""
Resilient wrappers around Celery task dispatch (.delay()/.apply_async()).

A `.delay()` call publishes a message to the broker (Redis in this project)
synchronously, in whatever request, signal handler, or task called it. If
the broker is unreachable, that raises immediately, right there — there is
no built-in distinction between "this dispatch must succeed" and "this is a
side effect, fine to lose." This module makes that distinction explicit:

- `safe_delay` is for dispatches whose failure must never break the action
  that triggered them — a notification email, a MailerLite sync. It catches
  broker-connection failures, logs them, and returns None so the caller's
  own work still completes normally.
- User-initiated processing dispatch (grading, uploads) goes through
  `students.task_tracking.launch_processing_task` instead, which is NOT
  silent — it raises ProcessingTemporarilyUnavailable so the request gets a
  clear, typed error rather than a 500 with a raw connection traceback.

Both share the same exception classification below, so "what counts as a
broker outage" can't drift between the silent and the loud path.
"""

from __future__ import annotations

import logging

from kombu.exceptions import OperationalError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)

# Every exception type a broker-unreachable .delay()/.apply_async() call can
# raise, across the layers involved (redis-py at the bottom, kombu/celery on
# top of it, plus the plain socket-level builtins either layer may wrap a
# raw connection failure in).
BROKER_UNAVAILABLE_ERRORS = (
    RedisConnectionError,
    RedisTimeoutError,
    OperationalError,
    ConnectionError,
    TimeoutError,
)


class ProcessingTemporarilyUnavailable(APIException):
    status_code = 503
    default_detail = (
        "Processing is temporarily unavailable right now. Please try again "
        "in a few minutes."
    )
    default_code = "processing_temporarily_unavailable"


def safe_delay(task, *args, **kwargs):
    """
    Dispatch a Celery task, swallowing broker-connection failures.

    Returns the AsyncResult on success, or None if the broker was
    unreachable (logged server-side, not raised). Any other exception — a
    bug in how the call was built, not an outage — still propagates
    normally, since silently swallowing those would hide real errors.
    """
    try:
        return task.delay(*args, **kwargs)
    except BROKER_UNAVAILABLE_ERRORS:
        logger.error(
            "Could not dispatch task %s - broker unavailable",
            getattr(task, "name", task),
            exc_info=True,
        )
        return None
