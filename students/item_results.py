"""
FR-A-07 (S7a): one batch item's result, and a session's failure summary
(08a §4.3). Used by session-results; S7b's retry endpoints return the same
item shape.

Backward compatible: every key an entry had (status, file_name, task_id,
error, context) keeps its meaning, and the coded fields are added beside
them.
"""

from __future__ import annotations

from collections import Counter

from audit.enums import ErrorClass, ReasonCode
from AutoGrader.reason_codes import (
    MID_BATCH_GRADING_REMEDIATION,
    REASON_CODES,
    UPLOAD_FILE_AGAIN_REMEDIATION,
    credit_refusal_remediation,
)

from .item_retry import (
    UPLOAD_ITEM_TYPES,
    deleted_after_days,
    lifetime_purged,
    upload_retryable,
)
from .models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
)

#: failure_codes' key for a failure with no reason code. A documented
#: sentinel, not a ReasonCode: such a failure reads as error_class SYSTEM.
UNCLASSIFIED = "UNCLASSIFIED"


def _spec(code):
    try:
        return REASON_CODES.get(ReasonCode(code))
    except ValueError:
        return None


def _remediation(spec, code, meta, task_type=None):
    """The spec's remediation, or (H-281) the individual-teacher one when
    the stored credit refusal says so (meta["message_variant"]), or (owner,
    2026-11-12) text 1's first sentence only for a grading paper, which
    keeps no file."""
    if spec is None:
        return None
    if (
        code == ReasonCode.INSUFFICIENT_CREDITS_MID_BATCH.value
        and task_type == BackgroundTaskType.BATCH_SUBMISSION_GRADING
    ):
        return MID_BATCH_GRADING_REMEDIATION
    if code == ReasonCode.INSUFFICIENT_CREDITS.value:
        chosen = credit_refusal_remediation(meta.get("message_variant"))
        if chosen is not None:
            return chosen
    return spec.remediation


def is_retryable(task, kept_ids=None):
    """Whether a retry would retry `task` in place: session-results' and
    tasks/sessions' one rule. An upload item: item_retry's own rule (its kept
    file, owner 2026-11-18), with `kept_ids` from item_retry.kept_item_ids
    when the caller read them for many items (None reads it for this item).
    Any other item: its code's catalogue flag, as before."""
    if task.dismissed_at is not None:
        return False  # the teacher dismissed it (owner, 2026-11-18)
    if task.task_type in UPLOAD_ITEM_TYPES:
        kept = None if kept_ids is None else task.pk in kept_ids
        return upload_retryable(task, kept=kept)
    spec = _spec(task.reason_code) if task.reason_code else None
    return bool(spec.retryable) if spec is not None else False


def batch_counts(session_id, total_files=None):
    """`(completed, total)` of a batch as it stands now: its SUCCESS items,
    and its total_files (or its item count)."""
    items = BackgroundProcessingTask.objects.filter(batch_session_id=session_id)
    if total_files is None:
        total_files = (
            BatchUploadSession.objects.filter(pk=session_id)
            .values_list("total_files", flat=True)
            .first()
        )
    completed = items.filter(status=BackgroundTaskStatus.SUCCESS).count()
    return completed, total_files or items.count()


def _mid_batch_message(spec, task, counts):
    """E2E F3: the mid-batch text, from the batch's counts when it is read,
    not as they were when the item failed (the same two approved texts)."""
    if counts is None:
        counts = batch_counts(task.batch_session_id)
    params = {"completed": counts[0], "total": counts[1]}
    return spec.render(params, variant=spec.variant_from_params(params))


def item_result(task, context, kept_ids=None, *, counts=None, lifetime_purged_ids=None):
    """The session-results entry for one tracked item. `kept_ids`: the ids
    of the session's items that still have a kept file, and
    `lifetime_purged_ids` those whose file the lifetime purge removed (one
    query for the whole list: item_retry.kept_states); `counts` the batch's
    `(completed, total)` (batch_counts). None reads each for this item."""
    meta = task.meta or {}
    failed = task.status == BackgroundTaskStatus.FAILURE
    code = task.reason_code or None
    spec = _spec(code) if code else None
    message = task.error if failed else None
    remediation = _remediation(spec, code, meta, task.task_type)
    retryable = is_retryable(task, kept_ids)
    resolution = None
    if (
        failed
        and code == ReasonCode.INSUFFICIENT_CREDITS_MID_BATCH.value
        and (task.batch_session_id)
    ):
        message = _mid_batch_message(spec, task, counts)
    elif failed and not retryable and task.task_type in UPLOAD_ITEM_TYPES:
        # E2E F5: a file the lifetime purge removed reads as the retry's
        # own refusal (NOT_RETRYABLE, item_retry.deleted_after_days).
        purged = (
            lifetime_purged(task)
            if lifetime_purged_ids is None
            else task.pk in lifetime_purged_ids
        )
        if purged:
            message = deleted_after_days()
            # N3 (owner, 2026-11-19): the retry 409's remediation and
            # resolution, word for word.
            remediation = UPLOAD_FILE_AGAIN_REMEDIATION
            resolution = "replace_file"
    if spec is not None:
        error_class = spec.error_class.value
    elif failed:
        error_class = ErrorClass.SYSTEM.value
    else:
        error_class = None
    submission_id = task.submission_id or meta.get("submission_id")
    replaced = meta.get("replaced_existing")
    return {
        # The keys the list always had.
        "status": task.status,
        "file_name": task.file_name,
        "task_id": task.celery_task_id,
        "error": message if failed else task.error,
        "context": context,
        # FR-A-07.
        "item_id": str(task.id),
        "item_index": task.item_index,
        "submission_id": str(submission_id) if submission_id else None,
        "reason_code": code,
        "error_class": error_class,
        "message": message,
        "remediation": remediation,
        # N3: what a client should do instead of a retry ("replace_file"), as
        # the retry 409's params.resolution says; null otherwise.
        "resolution": resolution,
        "retryable": retryable,
        # Owner 2026-11-18: the teacher dismissed it ("don't retry").
        "dismissed": task.dismissed_at is not None,
        "retry_count": task.retry_count,
        # Dashed UUID, same form as X-Request-ID and a sync body's `reference`,
        # so a client matches them as strings. It is the item's audit trace_id
        # (uuid.UUID(reference) == AuditEvent.trace_id).
        "reference": str(task.trace_id) if task.trace_id else None,
        "replaced_existing": bool(replaced) if replaced is not None else None,
    }


def failure_summary(failures):
    """failure_codes, stopped_at_item and resumable for a session's failed
    item results."""
    codes = Counter(entry["reason_code"] or UNCLASSIFIED for entry in failures)
    mid_batch = [
        entry["item_index"]
        for entry in failures
        if entry["reason_code"] == ReasonCode.INSUFFICIENT_CREDITS_MID_BATCH.value
        and entry["item_index"] is not None
    ]
    return {
        "failure_codes": dict(codes),
        # S7c raises INSUFFICIENT_CREDITS_MID_BATCH; derived here so the
        # contract exists now (08a §4.5).
        "stopped_at_item": min(mid_batch) if mid_batch else None,
        "resumable": any(entry["retryable"] for entry in failures),
    }
