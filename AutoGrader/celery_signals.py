"""
Propagates the request-id contextvar (see AutoGrader.request_context) across
the Celery client/broker/worker boundary.

contextvars don't cross a process boundary, so a task dispatched from a web
request can't just read the same ContextVar the worker will later run in -
the value has to be serialized onto the message and re-hydrated on the
worker side. Two signals do that:

  * `before_task_publish` fires in whichever process calls .delay()/
    .apply_async() (a web worker handling a request, or another Celery
    worker executing a task that itself dispatches more tasks) and stamps
    the current request id onto the outgoing message's headers, if one is
    set. This covers every dispatch in the codebase uniformly - direct
    `.delay()` calls, the `safe_delay()`/`launch_processing_task()` wrappers,
    and task-to-task chaining alike - without editing any call site.

  * `task_prerun` fires in the worker process right before the task body
    runs, reads the id back off the task's own request (Celery merges
    custom message headers onto `task.request`), and sets it in that
    worker's contextvar for the duration of the task. `task_postrun` clears
    it again.

The `task_postrun` reset matters because Celery's default (prefork) worker
pool reuses one OS process across many tasks in sequence: without resetting,
a task with no request id (e.g. a periodic/beat task) run right after a
task that had one would inherit the previous task's id in its own logs and
Sentry events.
"""

from __future__ import annotations

import logging
from contextvars import Token

from celery.signals import before_task_publish, task_postrun, task_prerun

from .request_context import (
    CELERY_HEADER_KEY,
    get_request_id,
    reset_request_id,
    set_request_id,
)

logger = logging.getLogger(__name__)

# task_prerun and task_postrun run in the same worker thread for a given
# task (Celery's prefork pool executes one task at a time per child
# process), so a plain dict keyed by task_id is sufficient to hand the
# reset() token from prerun to postrun - no locking needed.
_tokens_by_task_id: dict[str, Token] = {}


def _extract_request_id(task) -> str | None:
    """Read the request id back off a running task's request context.

    Checked two ways because Celery represents custom headers differently
    depending on how the task was invoked:
      - Normal (non-eager) dispatch through a real broker: the worker
        rebuilds `task.request` from the message headers dict, and custom
        header keys land as *both* a direct attribute and an entry in
        `task.request.headers`.
      - Eager execution (CELERY_TASK_ALWAYS_EAGER, used by some test
        setups): `Task.apply()` only nests custom headers under
        `task.request.headers`, not as top-level attributes.
    Checking both makes this correct under either execution path rather
    than silently only working in production.
    """
    request = getattr(task, "request", None)
    if request is None:
        return None

    direct = getattr(request, CELERY_HEADER_KEY, None)
    if direct:
        return direct

    headers = getattr(request, "headers", None) or {}
    return headers.get(CELERY_HEADER_KEY)


@before_task_publish.connect
def _stamp_request_id_on_publish(sender=None, headers=None, **kwargs):
    current = get_request_id()
    if current and headers is not None:
        headers[CELERY_HEADER_KEY] = current


@task_prerun.connect
def _restore_request_id_on_prerun(sender=None, task_id=None, task=None, **kwargs):
    request_id = _extract_request_id(task)
    if not request_id:
        return

    token = set_request_id(request_id)
    if task_id:
        _tokens_by_task_id[task_id] = token

    try:
        import sentry_sdk

        sentry_sdk.set_tag("request_id", request_id)
    except ImportError:
        pass


@task_postrun.connect
def _clear_request_id_on_postrun(sender=None, task_id=None, **kwargs):
    token = _tokens_by_task_id.pop(task_id, None) if task_id else None
    if token is not None:
        try:
            reset_request_id(token)
        except ValueError:
            # Defensive only: a token can't be reset outside the Context it
            # was created in. task_prerun/task_postrun for a given task_id
            # always run on the same worker thread in Celery's prefork and
            # solo pools, so this should be unreachable in practice.
            logger.warning(
                "Could not reset request_id contextvar for task %s "
                "(token from a different context)",
                task_id,
            )
