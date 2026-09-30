"""
FR-A-07 (S7b): retrying a failed batch item in place (08a §4.4; F4).

Only a FAILED GRADE item whose code is retryable as it is (the spec's
`retryable`: PROVIDER_FAILURE, INSUFFICIENT_CREDITS_MID_BATCH) is retried.
It is the same row: retry_count + 1, back to PENDING, relaunched with a new
Celery id, and its trace becomes the retrying request's. Everything else is
refused with NOT_RETRYABLE (409), and nothing is launched.

An UPLOAD item is never retried in place, whatever its code: its file isn't
kept (F4), so the answer is "upload the file again", and
params.resolution = "replace_file" tells a client which action to offer.
`resolve` is deferred (F2/F3).

Two retries of one item race on a conditional UPDATE claim: exactly one
wins, and the other is refused.
"""

from __future__ import annotations

from django.db.models import F
from django.utils import timezone

from audit.emitter import emit, resolve_trace_id
from audit.enums import AuditAction, AuditOutcome, ReasonCode
from AutoGrader.reason_codes import REASON_CODES, CodedError

from .models import BackgroundProcessingTask, BackgroundTaskStatus, BackgroundTaskType
from .task_tracking import launch_processing_task

#: Items a retry re-runs: grading reads its submission, which is stored.
GRADE_ITEM_TYPES = frozenset({BackgroundTaskType.BATCH_SUBMISSION_GRADING})

#: Items whose input was an uploaded file, which is not kept (F4).
UPLOAD_ITEM_TYPES = frozenset(
    {BackgroundTaskType.BATCH_ANSWER_UPLOAD, BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD}
)

#: The "why" of NOT_RETRYABLE for an upload item (a server constant).
REUPLOAD = (
    "This upload can't be retried as it is, because its file isn't kept. "
    "Upload the file again."
)


class ItemNotRetryable(CodedError):
    """NOT_RETRYABLE (409): this item can't be retried as it is."""

    reason_code = ReasonCode.NOT_RETRYABLE


def _retryable_as_it_is(code):
    try:
        spec = REASON_CODES.get(ReasonCode(code))
    except ValueError:
        return False
    return bool(spec and spec.retryable)


def refusal_for(item):
    """Why `item` can't be retried in place, as the error to raise; or None
    when it can."""
    if item.task_type in UPLOAD_ITEM_TYPES:
        return ItemNotRetryable(
            params={"resolution": "replace_file"}, display={"why": REUPLOAD}
        )
    if (
        item.task_type not in GRADE_ITEM_TYPES
        or item.status != BackgroundTaskStatus.FAILURE
        or not _retryable_as_it_is(item.reason_code)
        or not item.submission_id
    ):
        return ItemNotRetryable()
    return None


def retry_item(item, requested_by, request=None):
    """Retry `item` in place and return it, reloaded; or raise
    ItemNotRetryable. A broker outage raises ProcessingTemporarilyUnavailable
    (503) with the item marked FAILED again (launch_processing_task)."""
    refusal = refusal_for(item)
    if refusal is not None:
        raise refusal

    # The claim: only a row still in the state that was judged retryable.
    claimed = BackgroundProcessingTask.objects.filter(
        pk=item.pk,
        status=BackgroundTaskStatus.FAILURE,
        reason_code=item.reason_code,
        retry_count=item.retry_count,
    ).update(
        status=BackgroundTaskStatus.PENDING,
        retry_count=F("retry_count") + 1,
        reason_code="",
        error="",
        started_at=None,
        finished_at=None,
        trace_id=resolve_trace_id(),
        updated_at=timezone.now(),
    )
    if not claimed:
        # Another retry (or the item's own worker) got there first.
        raise ItemNotRetryable()
    item.refresh_from_db()

    from assignments.tasks import grade_engine_async

    launch_processing_task(
        grade_engine_async,
        item,
        str(requested_by.id),
        str(item.submission_id),
        batch_id=str(item.batch_session_id),
    )
    emit(
        AuditAction.GRADING_REQUESTED,
        actor=requested_by,
        request=request,
        target_type="StudentSubmission",
        target_id=item.submission_id,
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "assignment_id": str(item.assignment_id),
            "submission_id": str(item.submission_id),
            "task_id": str(item.id),
            "task_type": item.task_type,
        },
    )
    item.refresh_from_db()
    return item


def retry_failed(session, requested_by, reason_codes=None, request=None):
    """Retry every failed item of `session` that can be retried as it is
    (only those with one of `reason_codes`, when given). Returns
    (retried item ids, skipped [{item_id, reason_code}]), in item order."""
    retried, skipped = [], []
    failures = session.processing_tasks.filter(
        status=BackgroundTaskStatus.FAILURE
    ).order_by("item_index", "created_at")
    for item in failures:
        wanted = not reason_codes or item.reason_code in reason_codes
        try:
            if not wanted:
                raise ItemNotRetryable()
            retry_item(item, requested_by, request=request)
        except ItemNotRetryable:
            skipped.append(
                {"item_id": str(item.id), "reason_code": item.reason_code or None}
            )
        else:
            retried.append(str(item.id))
    return retried, skipped
