"""Confirm an upload quote (plan rev 3 section 5; owner Q2 = A; BE-B-05/06/07).

One transaction, under the quote's row lock, turns a ready quote into a batch
upload session: an item per file, a `KeptUpload` per kept file that takes over
the quote file's own storage key (no copy, no download), and the quote file's
key set to null so its delete signal leaves the asset. The files are walked in
position order with the door's own `plan_parking` against the live balance and
the live figures: a file that fits is held (`DoorHold`) and launched after
commit; one that does not is parked, kept and retryable. When nothing fits
every file parks and the answer is still 202: this never takes the door's
discard-all 402, and the inline fallback (`DoorFile`) is never reached.

A second confirm returns the stored body (the caller answers 200).
"""

import uuid
from datetime import timedelta
from functools import partial

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from assignments.tasks import upload_answers_engine_async, upload_assignment_async
from assignments.upload_door import (
    ITEM_ACCEPTED,
    ITEM_PARKED,
    ITEM_REFUSED,
    DoorHold,
    _DoorLaunchOnCommit,
    batch_entry,
    door_balance,
    launch_at_door,
    plan_parking,
)
from assignments.upload_prompts import BATCH_UPLOAD_PROMPT
from audit.emitter import emit
from audit.enums import AuditAction, AuditOutcome
from AutoGrader import uploads
from AutoGrader.reason_codes import CodedError
from billing import holds as credit_holds
from billing.errors import InsufficientCreditsError, is_individual_teacher
from students import upload_quote_service as service
from students.models import (
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
    KeptUpload,
    UploadQuote,
    UploadQuoteFile,
)
from students.task_tracking import (
    attach_celery_task,
    create_processing_task,
    launch_processing_task,
    mark_processing_task_failure,
    record_refused_item,
)

State = UploadQuoteFile.State


def _refusal_error(row):
    """The door's own refusal for a file the quote refused at the check."""
    params = {"file_name": row.original_name}
    if row.reason_code == "FILE_TOO_LARGE":
        limit = uploads.MAX_UPLOAD_SIZE_BYTES
        return CodedError(
            "FILE_TOO_LARGE",
            params={
                "actual": row.size_bytes,
                "limit": limit,
                "dimension": "bytes",
                **params,
            },
            display={
                "file_name": row.original_name,
                "actual": uploads.human_size(row.size_bytes),
                "limit": uploads.human_size(limit),
            },
        )
    if row.reason_code == "FILE_TYPE_UNSUPPORTED":
        return CodedError(
            "FILE_TYPE_UNSUPPORTED",
            params={"detected_type": row.content_type or "unknown", **params},
        )
    return CodedError(row.reason_code or "FILE_UNREADABLE", params=params)


def _launch(item, task_callable, *args, **kwargs):
    """The Celery id, with the message sent when the transaction commits. With
    holds on this is the door's own `launch_at_door`; with holds off (or
    draining) the same mechanics without a hold, because the direct call there
    would start a worker before the rows exist."""
    if credit_holds.holds_for_new_work():
        return launch_at_door(
            launch_processing_task, task_callable, item, *args, **kwargs
        )
    celery_task_id = str(uuid.uuid4())
    attach_celery_task(item.id, celery_task_id)
    send = partial(
        launch_processing_task,
        task_callable,
        item,
        *args,
        _celery_task_id=celery_task_id,
        **kwargs,
    )
    transaction.on_commit(_DoorLaunchOnCommit(send, item.id))
    return celery_task_id


def _kept_item(user, quote, session, row, index, task_type, assignment):
    """The item for a kept file: its KeptUpload takes the quote file's key."""
    item = create_processing_task(
        requested_by=user,
        task_type=task_type,
        batch_session=session,
        assignment=assignment,
        file_name=row.original_name,
        meta={"step": "Queued for upload quote"},
        item_index=index,
    )
    now = timezone.now()
    KeptUpload.objects.create(
        processing_task=item,
        storage_key=row.storage_key,
        original_name=row.original_name[:255],
        content_type=row.content_type or "",
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        relaunch={} if assignment is not None else dict(quote.relaunch or {}),
        kept_until=now + timedelta(days=settings.QUEUE_LIFETIME_DAYS),
        created_at=now,
    )
    UploadQuoteFile.objects.filter(pk=row.pk).update(storage_key=None)
    return item


def _run(quote, rows, user, request):
    answers = quote.kind == UploadQuote.Kind.ANSWER_PAPERS
    assignment = quote.assignment if answers else None
    task_type = (
        BackgroundTaskType.BATCH_ANSWER_UPLOAD
        if answers
        else BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD
    )
    session = BatchUploadSession.objects.create(
        teacher=user,
        assignment=assignment,
        course=None if answers else quote.course,
        task_type=BatchUploadType.SUBMISSION if answers else BatchUploadType.ASSIGNMENT,
        total_files=len(rows),
    )
    already = service._already_uploaded(quote, rows)
    figures = service.figures_of(quote, rows)
    # A file the course already has holds nothing, as the quote showed.
    held = [
        0 if row.position in already and figure is not None else figure
        for row, figure in zip(rows, figures, strict=True)
    ]
    wallet = getattr(user, "credit_wallet", None)
    if user.is_real_super_admin() or wallet is None:
        parked_left: dict = {}
    else:
        parked_left = plan_parking(door_balance(wallet), held)
    errors = {
        index: InsufficientCreditsError(
            f"Upload needs ~{held[index]} credits, {left} left in the batch",
            individual=is_individual_teacher(user),
        )
        for index, left in parked_left.items()
    }

    entries: dict = {}
    items: dict = {}
    # Every parked and refused file is recorded BEFORE any file is launched:
    # a park recorded after an earlier item started would read as a mid-batch
    # stop (the door's H-223 rule).
    for index, row in enumerate(rows):
        number = index + 1
        if row.state in (State.REFUSED, State.NOT_KEPT):
            error = (
                _refusal_error(row)
                if row.state == State.REFUSED
                else CodedError("QUOTE_FILE_NOT_KEPT")
            )
            item = record_refused_item(
                requested_by=user,
                task_type=task_type,
                error=error,
                batch_session=session,
                assignment=assignment,
                file_name=row.original_name,
                item_index=number,
            )
            entries[index] = batch_entry(
                row.original_name, item.id, ITEM_REFUSED, error=error
            )
        elif index in errors:
            item = _kept_item(user, quote, session, row, number, task_type, assignment)
            mark_processing_task_failure(item.id, errors[index], parked=True)
            entries[index] = batch_entry(
                row.original_name, item.id, ITEM_PARKED, error=errors[index]
            )
        else:
            continue
        items[index] = item

    runnable = [i for i in range(len(rows)) if i not in entries]
    door = DoorHold(user, assignment, len(runnable))
    for index in runnable:
        row = rows[index]
        item = _kept_item(user, quote, session, row, index + 1, task_type, assignment)
        payload = {"kept_upload_id": str(item.kept_upload.id)}
        if not door.hold(item, held[index]):
            # A lost race for the free balance parks this one file (N1).
            lost = door.refusal(held[index])
            mark_processing_task_failure(item.id, lost, parked=True)
            entries[index] = batch_entry(
                row.original_name, item.id, ITEM_PARKED, error=lost
            )
            continue
        if answers:
            task_id = _launch(
                item,
                upload_answers_engine_async,
                str(quote.assignment_id),
                payload,
                BATCH_UPLOAD_PROMPT,
                str(user.id),
                session_id=str(session.id),
                file_name=row.original_name,
            )
        else:
            task_id = _launch(
                item,
                upload_assignment_async,
                user_id=str(user.id),
                course_id=str(quote.course_id),
                topic_id=(quote.relaunch or {}).get("topic_id"),
                session_id=str(session.id),
                file_payload=payload,
                prompt_text=BATCH_UPLOAD_PROMPT,
                file_name=row.original_name,
            )
        entries[index] = batch_entry(
            row.original_name, item.id, ITEM_ACCEPTED, task_id=task_id
        )
    door.seal()

    if answers:
        emit(
            AuditAction.SUBMISSION_UPLOAD,
            actor=user,
            request=request,
            target_type="Assignment",
            target_id=quote.assignment_id,
            outcome=AuditOutcome.SUCCESS,
            metadata={
                "assignment_id": str(quote.assignment_id),
                "file_count": len(rows),
                "quote_id": str(quote.pk),
            },
        )
    tasks = []
    for index in range(len(rows)):
        entry = dict(entries[index])
        entry["item_id"] = str(entry["item_id"])
        tasks.append(entry)
    return session, {
        "quote_id": str(quote.pk),
        "session_id": str(session.id),
        "message": f"Batch processing started for {len(rows)} files",
        "tasks": tasks,
    }


def confirm(quote_id, user, request=None):
    """`(body, replayed)`. Raises QUOTE_CLOSED, 410 QUOTE_EXPIRED or 409
    QUOTE_NOT_READY (a CodedError each) with nothing changed."""
    with transaction.atomic():
        quote = (
            UploadQuote.objects.select_for_update(of=("self",))
            .select_related("assignment", "course")
            .get(pk=quote_id)
        )
        if quote.state == UploadQuote.State.CONFIRMED and quote.confirm_result:
            return quote.confirm_result, True
        service.refuse_if_not_open(quote)
        rows = list(quote.files.order_by("position"))
        if not service.is_ready(rows):
            raise CodedError("QUOTE_NOT_READY")
        session, body = _run(quote, rows, user, request)
        quote.state = UploadQuote.State.CONFIRMED
        quote.confirmed_at = timezone.now()
        quote.session = session
        quote.confirm_result = body
        quote.save(update_fields=["state", "confirmed_at", "session", "confirm_result"])
    return body, False
