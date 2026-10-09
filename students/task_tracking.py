from __future__ import annotations

import logging
from contextlib import contextmanager

from celery.result import AsyncResult
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.emitter import resolve_trace_id
from AutoGrader.celery import app as celery_app
from AutoGrader.dispatch import (
    BROKER_UNAVAILABLE_ERRORS,
    ProcessingTemporarilyUnavailable,
)
from AutoGrader.error_messages import (
    DEFAULT_ERROR_MESSAGE,
    describe_background_task_error,
)
from AutoGrader.reason_codes import CodedError, reason_of
from AutoGrader.safe_logging import describe_error_for_log
from billing.errors import InsufficientCreditsError
from billing.refusals import is_permanent_refusal, log_refusal

from .exceptions import InsufficientCreditsMidBatchError, TaskCancelledError
from .models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
)

logger = logging.getLogger(__name__)

TERMINAL_TASK_STATUSES = {
    BackgroundTaskStatus.CANCELLED,
    BackgroundTaskStatus.SUCCESS,
    BackgroundTaskStatus.FAILURE,
}

# Kept as aliases (rather than importing AutoGrader.error_messages directly
# at every call site) so existing imports of these two names keep working.
DEFAULT_TASK_FAILURE_MESSAGE = DEFAULT_ERROR_MESSAGE
describe_task_error = describe_background_task_error


def create_processing_task(
    *,
    requested_by,
    task_type,
    batch_session=None,
    assignment=None,
    submission=None,
    file_name=None,
    meta=None,
    item_index=None,
):
    """A tracked item. `item_index` is its 1-based position in its batch
    (upload order). Its trace_id is the dispatching request's server trace
    (resolve_trace_id), so the item's `reference` resolves to its audit
    events (FR-A-07 S7a, QA-ERR-04)."""
    return BackgroundProcessingTask.objects.create(
        requested_by=requested_by,
        batch_session=batch_session,
        assignment=assignment,
        submission=submission,
        item_index=item_index,
        trace_id=resolve_trace_id(),
        task_type=task_type,
        file_name=file_name,
        meta=meta or {},
    )


def attach_celery_task(processing_task_id, celery_task_id):
    if not processing_task_id or not celery_task_id:
        return

    BackgroundProcessingTask.objects.filter(id=processing_task_id).update(
        celery_task_id=str(celery_task_id), updated_at=timezone.now()
    )


def launch_processing_task(task_callable, processing_task, *args, **kwargs):
    kwargs["processing_task_id"] = str(processing_task.id)
    try:
        async_result = task_callable.delay(*args, **kwargs)
    except BROKER_UNAVAILABLE_ERRORS as exc:
        # The broker (Redis) is unreachable. This is not the task failing -
        # it never got dispatched - so the caller gets a clean, typed error
        # instead of a raw connection traceback surfacing as a generic 500.
        mark_processing_task_failure(processing_task.id, exc)
        raise ProcessingTemporarilyUnavailable() from exc
    except Exception as exc:
        mark_processing_task_failure(processing_task.id, exc)
        raise
    attach_celery_task(processing_task.id, async_result.id)
    return async_result


def get_processing_task(task_id, requested_by=None):
    queryset = BackgroundProcessingTask.objects.select_related(
        "assignment",
        "submission",
        "batch_session",
        "submission__assignment",
        "assignment__course",
        "submission__student",
    )
    if requested_by is not None:
        queryset = queryset.filter(requested_by=requested_by)
    return queryset.filter(celery_task_id=str(task_id)).first()


def get_processing_task_by_id(processing_task_id):
    if not processing_task_id:
        return None
    return BackgroundProcessingTask.objects.filter(id=processing_task_id).first()


def merge_task_meta(current_meta, new_meta):
    merged = dict(current_meta or {})
    merged.update(new_meta or {})
    return merged


def update_processing_task(
    processing_task_id,
    *,
    status=None,
    meta=None,
    error=None,
    started=False,
    finished=False,
    reason_code=None,
):
    if not processing_task_id:
        return None

    with transaction.atomic():
        task = (
            BackgroundProcessingTask.objects.select_for_update()
            .filter(id=processing_task_id)
            .first()
        )

        if not task:
            return None

        if task.status in TERMINAL_TASK_STATUSES and status not in {
            None,
            task.status,
        }:
            if meta:
                task.meta = merge_task_meta(task.meta, meta)
                task.save(update_fields=["meta", "updated_at"])
            return task

        update_fields = ["updated_at"]

        if status and task.status != status:
            task.status = status
            update_fields.append("status")

        if meta:
            task.meta = merge_task_meta(task.meta, meta)
            update_fields.append("meta")

        if error is not None:
            task.error = error
            update_fields.append("error")

        if reason_code is not None:
            task.reason_code = reason_code
            update_fields.append("reason_code")

        if started and not task.started_at:
            task.started_at = timezone.now()
            update_fields.append("started_at")

        if finished and not task.finished_at:
            task.finished_at = timezone.now()
            update_fields.append("finished_at")

        task.save(update_fields=update_fields)
        return task


def claim_processing_task_start(processing_task_id, *, stale_after, meta=None):
    """
    Idempotency claim for tasks that have no domain-level claim of their
    own (answer extraction, unlike grading, has no RUNNING state on the
    row it writes). One conditional UPDATE: PENDING → STARTED, or a STARTED
    row whose started_at is older than `stale_after` (a worker that died
    holding it) → STARTED again with a fresh started_at. Returns True when
    this execution won. A Redis redelivery of the same message while the
    original is still running loses, and must skip the billed work rather
    than run it a second time.

    Without a processing_task_id there is nothing to claim; the caller
    proceeds (untracked flows keep today's behaviour).
    """
    if not processing_task_id:
        return True

    now = timezone.now()
    with transaction.atomic():
        won = (
            BackgroundProcessingTask.objects.filter(id=processing_task_id)
            .filter(
                Q(status=BackgroundTaskStatus.PENDING)
                | Q(
                    status=BackgroundTaskStatus.STARTED,
                    started_at__lt=now - stale_after,
                )
            )
            .update(status=BackgroundTaskStatus.STARTED, started_at=now, updated_at=now)
        )
        if won and meta:
            task = BackgroundProcessingTask.objects.get(id=processing_task_id)
            task.meta = merge_task_meta(task.meta, meta)
            task.save(update_fields=["meta", "updated_at"])
    return bool(won)


def mark_processing_task_started(processing_task_id, meta=None):
    return update_processing_task(
        processing_task_id,
        status=BackgroundTaskStatus.STARTED,
        meta=meta,
        started=True,
    )


def mark_processing_task_success(processing_task_id, meta=None):
    return update_processing_task(
        processing_task_id,
        status=BackgroundTaskStatus.SUCCESS,
        meta=meta,
        finished=True,
        error="",
    )


def mark_processing_task_failure(
    processing_task_id, error, meta=None, *, fallback_message=None
):
    # FR-A-07 (S7c): a credit refusal after part of the batch already ran is
    # the mid-batch code, and it stops the rest of the batch.
    error = batch_credit_refusal(processing_task_id, error)
    if is_permanent_refusal(error):
        log_refusal(logger, f"Background task {processing_task_id}", error)
    elif isinstance(error, BaseException):
        # H-208: the error's class (and, for a fault that is not a broker
        # outage, its frames), never its text or a traceback.
        logger.error(
            "Background task %s failed: %s",
            processing_task_id,
            describe_error_for_log(error),
        )

    return update_processing_task(
        processing_task_id,
        status=BackgroundTaskStatus.FAILURE,
        meta=meta,
        error=describe_task_error(error, fallback_message),
        finished=True,
        # FR-A-07 (S7a): the failure's own code. An unclassified fault
        # stores none and reads as error_class SYSTEM (session-results).
        reason_code=failure_reason_code(error),
    )


def failure_reason_code(error):
    """The FR-A-06 code a failure carries (a CodedError or one of the two
    refusals), or "" for an unclassified fault."""
    reason = reason_of(error) if isinstance(error, BaseException) else None
    return reason[0].value if reason else ""


def record_refused_item(*, requested_by, task_type, error, **fields):
    """An item refused before anything was dispatched for it (a file too
    large for its batch): a tracked FAILURE with the refusal's code and
    message, and no Celery task, so the rest of the batch still runs."""
    item = create_processing_task(
        requested_by=requested_by, task_type=task_type, **fields
    )
    mark_processing_task_failure(item.id, error)
    item.refresh_from_db()
    return item


def mark_processing_task_cancelled(processing_task_id, meta=None):
    return update_processing_task(
        processing_task_id,
        status=BackgroundTaskStatus.CANCELLED,
        meta=meta,
        finished=True,
    )


def ensure_task_not_cancelled(processing_task_id, *, message=None):
    if not processing_task_id:
        return

    task = (
        BackgroundProcessingTask.objects.only("status")
        .filter(id=processing_task_id)
        .first()
    )

    if task and task.status == BackgroundTaskStatus.CANCELLED:
        raise TaskCancelledError(message or "Task cancelled by user.")


def lock_processing_task_for_final_save(processing_task_id, *, message=None):
    """
    Lock the tracked task while committing a cancellable task's final artifact.

    This closes the race where a worker checks for cancellation, a user cancels,
    and the worker then saves an assignment anyway using stale in-memory data.
    """
    if not processing_task_id:
        return None

    task = (
        BackgroundProcessingTask.objects.select_for_update()
        .filter(id=processing_task_id)
        .first()
    )

    if task and task.status == BackgroundTaskStatus.CANCELLED:
        raise TaskCancelledError(message or "Task cancelled by user.")

    return task


@contextmanager
def cancellable_final_save(processing_task_id, *, message=None):
    """
    Wrap a cancellable task's final DB write in the same lock+atomic pattern
    used for assignment creation, so a cancellation observed after the last
    cooperative check can't be raced by a save using stale in-memory data.

    Usage:
        with cancellable_final_save(processing_task_id):
            submission.save()
    """
    with transaction.atomic():
        task = lock_processing_task_for_final_save(processing_task_id, message=message)
        yield task


def _is_cancellable_assignment_artifact(processing_task):
    return processing_task.task_type in {
        BackgroundTaskType.ASSIGNMENT_EXTRACTION,
        BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
    }


def cleanup_cancelled_task_artifacts(processing_task):
    """
    Remove assignment rows that exist only because a cancellable create/upload task
    had already persisted them before cancellation was observed.

    Re-extraction/update tasks are intentionally excluded: those operate on real,
    pre-existing assignments and cancellation must leave the previous assignment
    intact.
    """
    if not processing_task or not _is_cancellable_assignment_artifact(processing_task):
        return None

    if not processing_task.assignment_id:
        return None

    # The assignment row belongs to the assignments app; asking its service
    # layer (rather than locking and deleting its model from here) keeps
    # the app boundary intact. Imported lazily because assignments.services
    # imports this module.
    from assignments.services import lock_placeholder_assignment_for_cleanup

    assignment = lock_placeholder_assignment_for_cleanup(processing_task.assignment_id)
    if assignment is None:
        logger.warning(
            "Skipping cleanup for cancelled task %s: assignment %s is missing "
            "or already has submissions.",
            processing_task.id,
            processing_task.assignment_id,
        )
        return None

    assignment_id = str(assignment.id)

    # Detach tracked tasks first; deleting the assignment would otherwise cascade
    # and erase the cancellation record the frontend still needs to poll.
    BackgroundProcessingTask.objects.filter(assignment_id=assignment.id).update(
        assignment=None
    )
    assignment.delete()

    processing_task.assignment = None
    processing_task.assignment_id = None
    processing_task.meta = merge_task_meta(
        processing_task.meta,
        {
            "cancelled_assignment_deleted": True,
            "deleted_assignment_id": assignment_id,
        },
    )
    processing_task.save(update_fields=["meta", "updated_at"])
    return assignment_id


def cancel_processing_task(processing_task):
    with transaction.atomic():
        processing_task = BackgroundProcessingTask.objects.select_for_update().get(
            id=processing_task.id
        )

        if processing_task.status in TERMINAL_TASK_STATUSES:
            return processing_task

        now = timezone.now()
        update_fields = ["status", "cancel_requested_at", "updated_at"]
        processing_task.status = BackgroundTaskStatus.CANCELLED
        processing_task.cancel_requested_at = now

        if not processing_task.finished_at:
            processing_task.finished_at = now
            update_fields.append("finished_at")

        processing_task.save(update_fields=update_fields)
        cleanup_cancelled_task_artifacts(processing_task)

    if processing_task.celery_task_id:
        # The revoke is an accelerator, not the cancellation itself: the
        # CANCELLED row above is already committed, and every task observes
        # it cooperatively through ensure_task_not_cancelled /
        # cancellable_final_save. So an unreachable broker here must not
        # turn an already-recorded cancellation into a 500 for the user.
        try:
            # One broadcast. AsyncResult.revoke() is literally
            # app.control.revoke(id, ...) - this used to send both.
            celery_app.control.revoke(
                processing_task.celery_task_id,
                terminate=True,
                signal="SIGTERM",
            )
        except BROKER_UNAVAILABLE_ERRORS as exc:
            logger.error(
                "Could not revoke celery task %s for cancelled processing task %s "
                "- broker unavailable (%s); the worker will observe the "
                "cancellation at its next cooperative check",
                processing_task.celery_task_id,
                processing_task.id,
                describe_error_for_log(exc),
            )

    return processing_task


def normalize_processing_task_status(processing_task):
    if processing_task.status in TERMINAL_TASK_STATUSES:
        return processing_task.status

    if not processing_task.celery_task_id:
        return processing_task.status

    # This runs inside status-poll GET views. The result backend is Redis,
    # so a broker outage here used to surface as a raw connection traceback
    # (a 500) on every poll, even though the DB row still holds a perfectly
    # good answer. The tracked status is the source of truth; the Celery
    # state is only consulted to notice a worker that died without
    # reporting back, so when it can't be read we fall back to the row.
    try:
        state = AsyncResult(processing_task.celery_task_id, app=celery_app).state
    except BROKER_UNAVAILABLE_ERRORS as exc:
        logger.warning(
            "Could not read celery state for processing task %s (celery id %s) "
            "- result backend unavailable (%s); reporting tracked status %s",
            processing_task.id,
            processing_task.celery_task_id,
            describe_error_for_log(exc),
            processing_task.status,
        )
        return processing_task.status

    if state == "REVOKED":
        processing_task = mark_processing_task_cancelled(
            processing_task.id, meta={"celery_state": state}
        )
        return processing_task.status

    if state == "FAILURE":
        processing_task = mark_processing_task_failure(
            processing_task.id,
            error=None,
            meta={"celery_state": state},
            fallback_message=(
                "This task stopped unexpectedly before it could finish. "
                "Please try again, or contact support if this continues."
            ),
        )
        return processing_task.status

    if state == "SUCCESS":
        processing_task = mark_processing_task_success(
            processing_task.id, meta={"celery_state": state}
        )
        return processing_task.status

    return processing_task.status


# -- FR-A-07 S7c: credits running out mid-batch (08a §4.5) -------------------


def _mid_batch_error(session_id):
    items = BackgroundProcessingTask.objects.filter(batch_session_id=session_id)
    total = (
        BatchUploadSession.objects.filter(pk=session_id)
        .values_list("total_files", flat=True)
        .first()
    )
    completed = items.filter(status=BackgroundTaskStatus.SUCCESS).count()
    return InsufficientCreditsMidBatchError(
        params={"completed": completed, "total": total or items.count()},
        # QA-approved (Epic A S7d, 2026-10-01): with nothing finished yet the
        # item says credits ran out before any item finished, rather than
        # after 0 of N (the none_finished template). Both S7c raise sites
        # build the error here. The count is taken at each raise, so a later
        # item of the same batch can read 1 of N if one finished meanwhile.
        variant="none_finished" if completed == 0 else None,
    )


def batch_credit_refusal(processing_task_id, error):
    """`error` itself, unless it is a plain credit refusal inside a batch in
    which another item has already started or finished. That is the
    mid-batch refusal (not the pre-flight one): the session is marked, so
    the items still to run stop before any provider call."""
    if not isinstance(error, InsufficientCreditsError) or isinstance(error, CodedError):
        return error
    session_id = (
        BackgroundProcessingTask.objects.filter(pk=processing_task_id)
        .values_list("batch_session_id", flat=True)
        .first()
    )
    if not session_id:
        return error
    went_ahead = (
        BackgroundProcessingTask.objects.filter(
            batch_session_id=session_id,
            status__in=(BackgroundTaskStatus.STARTED, BackgroundTaskStatus.SUCCESS),
        )
        .exclude(pk=processing_task_id)
        .exists()
    )
    if not went_ahead:
        return error
    BatchUploadSession.objects.filter(
        pk=session_id, credits_exhausted_at__isnull=True
    ).update(credits_exhausted_at=timezone.now())
    mid_batch = _mid_batch_error(session_id)
    mid_batch.__cause__ = error
    return mid_batch


def ensure_batch_has_credits(processing_task_id):
    """Stop an item whose batch already ran out of credits, BEFORE any
    provider call (so it is never charged): it fails with the same
    INSUFFICIENT_CREDITS_MID_BATCH as the item that ran out."""
    session_id = (
        BackgroundProcessingTask.objects.filter(pk=processing_task_id)
        .values_list("batch_session_id", flat=True)
        .first()
    )
    if (
        session_id
        and BatchUploadSession.objects.filter(
            pk=session_id, credits_exhausted_at__isnull=False
        ).exists()
    ):
        raise _mid_batch_error(session_id)


def clear_batch_credit_stop(session_id):
    """A resume (a retry after a top-up) lets the batch run again."""
    BatchUploadSession.objects.filter(pk=session_id).update(credits_exhausted_at=None)
