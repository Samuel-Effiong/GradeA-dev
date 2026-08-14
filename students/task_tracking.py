from __future__ import annotations

import logging
from contextlib import contextmanager

from celery.result import AsyncResult
from django.db import transaction
from django.utils import timezone

from AutoGrader.celery import app as celery_app
from AutoGrader.error_messages import (
    DEFAULT_ERROR_MESSAGE,
    describe_background_task_error,
)

from .exceptions import TaskCancelledError
from .models import BackgroundProcessingTask, BackgroundTaskStatus, BackgroundTaskType

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
):
    return BackgroundProcessingTask.objects.create(
        requested_by=requested_by,
        batch_session=batch_session,
        assignment=assignment,
        submission=submission,
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

        if started and not task.started_at:
            task.started_at = timezone.now()
            update_fields.append("started_at")

        if finished and not task.finished_at:
            task.finished_at = timezone.now()
            update_fields.append("finished_at")

        task.save(update_fields=update_fields)
        return task


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
    if isinstance(error, BaseException):
        logger.error(
            "Background task %s failed",
            processing_task_id,
            exc_info=error,
        )

    return update_processing_task(
        processing_task_id,
        status=BackgroundTaskStatus.FAILURE,
        meta=meta,
        error=describe_task_error(error, fallback_message),
        finished=True,
    )


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

    from assignments.models import Assignment

    assignment = (
        Assignment.objects.select_for_update()
        .filter(id=processing_task.assignment_id)
        .first()
    )
    if not assignment:
        return None

    if assignment.submissions.exists():
        logger.warning(
            "Skipping cleanup for cancelled task %s because assignment %s has submissions.",
            processing_task.id,
            assignment.id,
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
        AsyncResult(processing_task.celery_task_id, app=celery_app).revoke(
            terminate=True,
            signal="SIGTERM",
        )
        celery_app.control.revoke(
            processing_task.celery_task_id,
            terminate=True,
            signal="SIGTERM",
        )

    return processing_task


def normalize_processing_task_status(processing_task):
    if processing_task.status in TERMINAL_TASK_STATUSES:
        return processing_task.status

    if not processing_task.celery_task_id:
        return processing_task.status

    state = AsyncResult(processing_task.celery_task_id, app=celery_app).state

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
