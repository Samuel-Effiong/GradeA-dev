"""
FR-A-07 (S7b): retrying a failed batch item in place (08a §4.4; F4).

Only a FAILED GRADE item whose code is retryable as it is (the spec's
`retryable`: PROVIDER_FAILURE, INSUFFICIENT_CREDITS_MID_BATCH) is retried.
It is the same row: retry_count + 1, back to PENDING, relaunched with a new
Celery id, and its trace becomes the retrying request's. Everything else is
refused with NOT_RETRYABLE (409), and nothing is launched.

An UPLOAD item (Epic B1b-5, F4) is retried in place the same way when its
file is kept (a KeptUpload row, not purged) and its code is retryable as it
is, or INSUFFICIENT_CREDITS (the late door, 08b §3.2 step 5; upload items
only, SM Q4). It is relaunched with the kept file's id, the shared prompt
and the ids its route used. Any other upload item answers "upload the file
again", and params.resolution = "replace_file" tells a client which action
to offer. `resolve` is deferred (F2/F3).

Two retries of one item race on a conditional UPDATE claim: exactly one
wins, and the other is refused.

H-38: the item's course must still be reachable by the retrying teacher
(`teacher_course_access_q`), at the request and again in the claim. A
teacher removed from a school owns their batch sessions still, but its
items are not found (404, before anything else is said about them), and
retry-failed skips them. grade_engine_async checks again when it runs.
"""

from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.http import Http404
from django.utils import timezone

from audit.emitter import emit, resolve_trace_id
from audit.enums import AuditAction, AuditOutcome, ReasonCode
from AutoGrader.reason_codes import (
    REASON_CODES,
    UPLOAD_FILE_AGAIN_REMEDIATION,
    CodedError,
)
from classrooms.models import reachable_courses

from .models import BackgroundProcessingTask, BackgroundTaskStatus, BackgroundTaskType
from .task_tracking import clear_batch_credit_stop, launch_processing_task

#: Items a retry re-runs: grading reads its submission, which is stored.
GRADE_ITEM_TYPES = frozenset({BackgroundTaskType.BATCH_SUBMISSION_GRADING})

#: Items whose input was an uploaded file, which is not kept (F4).
UPLOAD_ITEM_TYPES = frozenset(
    {BackgroundTaskType.BATCH_ANSWER_UPLOAD, BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD}
)

#: An upload item's codes that a kept file makes retryable (SM Q4): the
#: catalogue's retryable codes, plus the late door's credit refusal.
UPLOAD_EXTRA_RETRYABLE_CODES = frozenset({ReasonCode.INSUFFICIENT_CREDITS.value})

#: The "why" of NOT_RETRYABLE for an upload item (B1b-8, SM Q8-1). Owner-
#: approved: text 2 (team/approved_wording_2026-11-08.md) when the kept file
#: reached the end of its lifetime, #16b (approved_wording_b1b_2026-11-09.md)
#: when no file was ever kept. Any other case reads the catalogue's default.
NOT_SAVED = (
    "This upload can't be retried because its file wasn't saved. "
    "Upload the file again."
)


def deleted_after_days(days=None):
    days = settings.QUEUE_LIFETIME_DAYS if days is None else days
    return (
        f"This upload can't be retried because its file was deleted after {days} "
        "days. Upload the file again."
    )


def _upload_display(item):
    """Q8-1: no row -> #16b; purged by the lifetime purge (purge_reason
    "lifetime") -> text 2; anything else (a discard after cancel, refusal or
    success; a lost file) -> the default `why`."""
    from .models import KeptUpload

    row = KeptUpload.objects.filter(processing_task_id=item.pk).first()
    if row is None:
        return {"why": NOT_SAVED}
    if (
        row.purged_at is not None
        and row.purge_reason == KeptUpload.PurgeReason.LIFETIME
    ):
        return {"why": deleted_after_days()}
    return {}


class ItemNotRetryable(CodedError):
    """NOT_RETRYABLE (409): this item can't be retried as it is."""

    reason_code = ReasonCode.NOT_RETRYABLE


def reachable_items_q(user):
    """Q for the items whose course `user` can reach now (H-38): the
    item's assignment's course, or the batch session's when the item has
    no assignment (an assignment upload that failed before creating one)."""
    courses = reachable_courses(user)
    return Q(assignment__isnull=False, assignment__course__in=courses) | Q(
        assignment__isnull=True, batch_session__course__in=courses
    )


def is_reachable(item, user):
    """Whether `user` can still reach `item`'s course (H-38)."""
    return (
        BackgroundProcessingTask.objects.filter(reachable_items_q(user))
        .filter(pk=item.pk)
        .exists()
    )


def _retryable_as_it_is(code):
    try:
        spec = REASON_CODES.get(ReasonCode(code))
    except ValueError:
        return False
    return bool(spec and spec.retryable)


def kept_file(item):
    """`item`'s kept upload file, if it still has one (B1b-5)."""
    from .models import KeptUpload

    return KeptUpload.objects.filter(
        processing_task_id=item.pk, purged_at__isnull=True
    ).first()


def upload_code_retryable(code):
    """Whether an upload item failing with `code` can be retried from its
    kept file (the catalogue's retryable codes plus the late door's)."""
    return _retryable_as_it_is(code) or code in UPLOAD_EXTRA_RETRYABLE_CODES


def upload_retryable(item, kept=None):
    """Whether upload `item` is retried in place: FAILED, a code a kept file
    can retry, and its file still kept. `kept` is whether it still has a
    kept file, when the caller has already read that (session-results, one
    query for the session: kept_item_ids); otherwise it is read here."""
    if (
        item.status != BackgroundTaskStatus.FAILURE
        or item.dismissed_at is not None
        or not upload_code_retryable(item.reason_code)
    ):
        return False
    if kept is None:
        kept = kept_file(item) is not None
    return kept


def kept_states(items):
    """`(kept, lifetime_purged)`: the ids of the failed upload `items` that
    still have a kept file, and of those whose file the lifetime purge
    removed (E2E F5), in one query (none when no item could need it)."""
    from .models import KeptUpload

    ids = [
        item.pk
        for item in items
        if item.task_type in UPLOAD_ITEM_TYPES
        and item.status == BackgroundTaskStatus.FAILURE
        and item.dismissed_at is None
    ]
    if not ids:
        return set(), set()
    kept, lifetime_purged = set(), set()
    for item_id, purged_at, reason in KeptUpload.objects.filter(
        processing_task_id__in=ids
    ).values_list("processing_task_id", "purged_at", "purge_reason"):
        if purged_at is None:
            kept.add(item_id)
        elif reason == KeptUpload.PurgeReason.LIFETIME:
            lifetime_purged.add(item_id)
    return kept, lifetime_purged


def kept_item_ids(items):
    """The ids of the failed upload `items` that still have a kept file, in
    one query (none when no item could need it)."""
    return kept_states(items)[0]


def lifetime_purged(item):
    """Whether upload `item`'s kept file was removed by the lifetime purge."""
    from .models import KeptUpload

    return KeptUpload.objects.filter(
        processing_task_id=item.pk,
        purged_at__isnull=False,
        purge_reason=KeptUpload.PurgeReason.LIFETIME,
    ).exists()


def refusal_for(item):
    """Why `item` can't be retried in place, as the error to raise; or None
    when it can."""
    if item.task_type in UPLOAD_ITEM_TYPES:
        if upload_retryable(item):
            return None
        # N3 (owner, 2026-11-19): a file the lifetime purge removed says
        # "Upload the file again." as its remediation.
        return ItemNotRetryable(
            params={"resolution": "replace_file"},
            display=_upload_display(item),
            remediation=(
                UPLOAD_FILE_AGAIN_REMEDIATION if lifetime_purged(item) else None
            ),
        )
    if (
        item.task_type not in GRADE_ITEM_TYPES
        or item.status != BackgroundTaskStatus.FAILURE
        or item.dismissed_at is not None
        or not _retryable_as_it_is(item.reason_code)
        or not item.submission_id
    ):
        return ItemNotRetryable()
    return None


def retry_item(item, requested_by, request=None):
    """Retry `item` in place and return it, reloaded; or raise
    ItemNotRetryable. An item whose course `requested_by` can't reach is
    Http404 (H-38). A broker outage raises ProcessingTemporarilyUnavailable
    (503) with the item marked FAILED again (launch_processing_task)."""
    if not is_reachable(item, requested_by):
        raise Http404()
    refusal = refusal_for(item)
    if refusal is not None:
        raise refusal

    # The claim: only a row still in the state that was judged retryable,
    # and still reachable (a removal between the check above and here).
    # Reachability is a subquery on the pk, so the state conditions stay on
    # the UPDATE's own row, which Postgres re-checks on the locked version
    # when two claims race. A join here makes Django move every condition
    # into an "id IN (SELECT ...)" read from the snapshot: both claims win.
    claimed = BackgroundProcessingTask.objects.filter(
        pk=item.pk,
        status=BackgroundTaskStatus.FAILURE,
        dismissed_at__isnull=True,
        reason_code=item.reason_code,
        retry_count=item.retry_count,
        pk__in=BackgroundProcessingTask.objects.filter(
            reachable_items_q(requested_by)
        ).values("pk"),
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
    if item.batch_session_id:
        # S7c: any retry that won its claim resumes a batch stopped for
        # credits, whatever this item failed with (a PROVIDER_FAILURE item
        # retried after a top-up must not be stopped by the old mark). A
        # real shortfall re-marks it (batch_credit_refusal) before any
        # charge.
        clear_batch_credit_stop(item.batch_session_id)
    item.refresh_from_db()

    if item.task_type in UPLOAD_ITEM_TYPES:
        _relaunch_upload(item, requested_by)
        item.refresh_from_db()
        return item

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


def _relaunch_upload(item, requested_by):
    """B1b-5 (SM Q2): the item's own worker, from its kept file, with the
    shared prompt and the ids its route used. Under the retrying teacher,
    as a grading retry. No GRADING_REQUESTED row: the request's own audit
    row stands."""
    from assignments.tasks import upload_answers_engine_async, upload_assignment_async
    from assignments.upload_prompts import BATCH_UPLOAD_PROMPT

    from .models import KeptUpload

    kept = KeptUpload.objects.get(processing_task_id=item.pk)
    payload = {"kept_upload_id": str(kept.id)}
    if item.task_type == BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD:
        launch_processing_task(
            upload_assignment_async,
            item,
            user_id=str(requested_by.id),
            course_id=str(item.batch_session.course_id),
            topic_id=kept.relaunch.get("topic_id"),
            session_id=str(item.batch_session_id),
            file_payload=payload,
            prompt_text=BATCH_UPLOAD_PROMPT,
            file_name=item.file_name,
        )
    else:
        launch_processing_task(
            upload_answers_engine_async,
            item,
            str(item.assignment_id),
            payload,
            BATCH_UPLOAD_PROMPT,
            str(requested_by.id),
            session_id=str(item.batch_session_id),
            file_name=item.file_name,
        )


def retry_failed(session, requested_by, reason_codes=None, request=None):
    """Retry every failed item of `session` that can be retried as it is
    (only those with one of `reason_codes`, when given). Returns
    (retried item ids, skipped [{item_id, reason_code}]), in item order.
    An item whose course `requested_by` can't reach (H-38) is skipped with
    no code: nothing about it is said beyond "not retried"."""
    retried, skipped = [], []
    failures = session.processing_tasks.filter(
        status=BackgroundTaskStatus.FAILURE
    ).order_by("item_index", "created_at")
    for item in failures:
        wanted = not reason_codes or item.reason_code in reason_codes
        try:
            # Reachability first: an unreachable item's code isn't reported.
            if not is_reachable(item, requested_by):
                raise Http404()
            if not wanted:
                raise ItemNotRetryable()
            retry_item(item, requested_by, request=request)
        except Http404:
            skipped.append({"item_id": str(item.id), "reason_code": None})
        except ItemNotRetryable:
            skipped.append(
                {"item_id": str(item.id), "reason_code": item.reason_code or None}
            )
        else:
            retried.append(str(item.id))
    return retried, skipped


# Dismiss: the teacher won't retry an item (owner, 2026-11-18).


def dismiss_item(item, requested_by, request=None):
    """Owner 2026-11-18: the teacher won't retry FAILED `item`. It stays
    FAILURE with its code and error; dismissed_at is set and meta says who.
    Its kept file is discarded (purge_reason "dismissed", the delete after
    commit). Returns (item, whether this call dismissed it): a second
    dismiss changes nothing. An item that did not fail is ItemNotRetryable
    (the existing 409); one whose course `requested_by` can't reach is
    Http404 (H-38). No credits move: a FAILURE item holds none."""
    from .kept_uploads import discard
    from .models import KeptUpload
    from .task_tracking import merge_task_meta

    if not is_reachable(item, requested_by):
        raise Http404()
    with transaction.atomic():
        row = (
            BackgroundProcessingTask.objects.select_for_update()
            .filter(pk=item.pk)
            .first()
        )
        if row is None:
            raise Http404()
        if row.status != BackgroundTaskStatus.FAILURE:
            raise ItemNotRetryable()
        if row.dismissed_at is not None:
            return row, False
        row.dismissed_at = timezone.now()
        row.meta = merge_task_meta(row.meta, {"dismissed_by": str(requested_by.id)})
        row.save(update_fields=["dismissed_at", "meta", "updated_at"])
        discard(row, reason=KeptUpload.PurgeReason.DISMISSED)
        emit(
            AuditAction.BATCH_ITEM_DISMISSED,
            actor=requested_by,
            request=request,
            target_type="BackgroundProcessingTask",
            target_id=row.id,
            outcome=AuditOutcome.SUCCESS,
            reason_code=row.reason_code or None,
            metadata={
                "session_id": str(row.batch_session_id),
                "item_id": str(row.id),
                "task_type": row.task_type,
            },
        )
    return row, True


def dismiss_failed(session, requested_by, reason_codes=None, request=None):
    """Dismiss every failed, not yet dismissed item of `session` (only those
    with one of `reason_codes`, when given). Returns (dismissed item ids,
    skipped [{item_id, reason_code}]), in item order, as retry_failed does;
    an unreachable item (H-38) is skipped with no code."""
    dismissed, skipped = [], []
    failures = session.processing_tasks.filter(
        status=BackgroundTaskStatus.FAILURE, dismissed_at__isnull=True
    ).order_by("item_index", "created_at")
    for item in failures:
        if not is_reachable(item, requested_by):
            skipped.append({"item_id": str(item.id), "reason_code": None})
            continue
        if reason_codes and item.reason_code not in reason_codes:
            skipped.append(
                {"item_id": str(item.id), "reason_code": item.reason_code or None}
            )
            continue
        try:
            _, changed = dismiss_item(item, requested_by, request=request)
        except (Http404, ItemNotRetryable):
            skipped.append(
                {"item_id": str(item.id), "reason_code": item.reason_code or None}
            )
            continue
        if changed:
            dismissed.append(str(item.id))
    return dismissed, skipped
