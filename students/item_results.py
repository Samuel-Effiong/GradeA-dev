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
    credit_refusal_remediation,
)

from .item_retry import UPLOAD_ITEM_TYPES, upload_retryable
from .models import BackgroundTaskStatus, BackgroundTaskType

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


def item_result(task, context, kept_ids=None):
    """The session-results entry for one tracked item. `kept_ids`: the ids
    of the session's items that still have a kept file (one query for the
    whole list); None reads it for this item alone."""
    meta = task.meta or {}
    failed = task.status == BackgroundTaskStatus.FAILURE
    code = task.reason_code or None
    spec = _spec(code) if code else None
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
        "error": task.error,
        "context": context,
        # FR-A-07.
        "item_id": str(task.id),
        "item_index": task.item_index,
        "submission_id": str(submission_id) if submission_id else None,
        "reason_code": code,
        "error_class": error_class,
        "message": task.error if failed else None,
        "remediation": _remediation(spec, code, meta, task.task_type),
        "retryable": is_retryable(task, kept_ids),
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
