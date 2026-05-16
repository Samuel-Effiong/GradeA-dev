from __future__ import annotations

from celery.result import AsyncResult
from django.db import transaction
from django.utils import timezone

from AutoGrader.celery import app as celery_app

from .exceptions import TaskCancelledError
from .models import BackgroundProcessingTask, BackgroundTaskStatus

TERMINAL_TASK_STATUSES = {
    BackgroundTaskStatus.CANCELLED,
    BackgroundTaskStatus.SUCCESS,
    BackgroundTaskStatus.FAILURE,
}


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
        celery_task_id=str(celery_task_id)
    )


def launch_processing_task(task_callable, processing_task, *args, **kwargs):
    kwargs["processing_task_id"] = str(processing_task.id)
    async_result = task_callable.delay(*args, **kwargs)
    attach_celery_task(processing_task.id, async_result.id)
    return async_result


def get_processing_task(task_id, requested_by=None):
    queryset = BackgroundProcessingTask.objects.all()
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

        if task.status == BackgroundTaskStatus.CANCELLED and status not in {
            None,
            BackgroundTaskStatus.CANCELLED,
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

        if finished:
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


def mark_processing_task_failure(processing_task_id, error, meta=None):
    return update_processing_task(
        processing_task_id,
        status=BackgroundTaskStatus.FAILURE,
        meta=meta,
        error=str(error),
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


def cancel_processing_task(processing_task):
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

    if processing_task.celery_task_id:
        AsyncResult(processing_task.celery_task_id).revoke(
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

    state = AsyncResult(processing_task.celery_task_id).state

    if state == "REVOKED":
        processing_task = mark_processing_task_cancelled(
            processing_task.id, meta={"celery_state": state}
        )
        return processing_task.status

    if state == "FAILURE":
        processing_task = mark_processing_task_failure(
            processing_task.id,
            error="Task failed",
            meta={"celery_state": state},
        )
        return processing_task.status

    if state == "SUCCESS":
        processing_task = mark_processing_task_success(
            processing_task.id, meta={"celery_state": state}
        )
        return processing_task.status

    return processing_task.status
