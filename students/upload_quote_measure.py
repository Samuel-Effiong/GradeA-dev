"""The measure of an upload quote's files (plan rev 3 sections 4 and 7;
BE-B-03/04/09).

`measure_quote_file` (students.tasks) is a ZERO-CREDIT task: it loads a quote
file's kept bytes, checks their sha256, runs the door's own preparation
(`AssignmentProcessingService.prepare_ai_content`, what `_estimate_for` runs)
and keeps the per-page image tokens (`page_tokens_of`). The figure is then
`estimate_from_page_tokens` against the live text part, so it is the door's
number for these bytes at that moment. It makes no AI call, takes no hold and
writes no ledger row.

It runs on its own queue `quote_measure`, consumed by the existing worker
service (`scripts/start-worker.sh`: `-Q celery,quote_measure`; SM ruling
2026-11-19: no second worker service in v1). One Celery worker has one
concurrency for all its queues, so "low concurrency" is enforced here: a cache
counter lets at most UPLOAD_QUOTE_MEASURE_CONCURRENCY measures run at once, so
measures never take more than that many worker slots from grading.

`requeue_stuck` (beat, every 5 minutes) asks again for files stored and
unmeasured for over 10 minutes, up to 3 attempts; after that the file is left
out of the figure as a file the door cannot estimate ("cannot estimate" is not
"certainly refused"), and counted. No file name is logged here.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ai_processor.call_estimates import page_tokens_of
from assignments.services import AssignmentProcessingService
from assignments.upload_prompts import BATCH_UPLOAD_PROMPT
from audit import metrics as audit_metrics
from AutoGrader.reason_codes import reason_of
from students import kept_files
from students.models import UploadQuote, UploadQuoteFile
from students.upload_quotes import restart_clock

logger = logging.getLogger(__name__)

QUEUE = "quote_measure"
SLOTS_KEY = "upload_quote_measure_slots"
SLOTS_TTL = 2 * 60 * 60
RETRY_COUNTDOWN = 15  # seconds, when every slot is taken
STORAGE_RETRY_COUNTDOWN = 60
STALE_AFTER = timedelta(minutes=10)
ALARM_AFTER = timedelta(minutes=30)
MAX_ATTEMPTS = 3

State = UploadQuoteFile.State


def acquire():
    """Take one measure slot; False when UPLOAD_QUOTE_MEASURE_CONCURRENCY are
    already taken."""
    cache.add(SLOTS_KEY, 0, timeout=SLOTS_TTL)
    taken = cache.incr(SLOTS_KEY)
    if taken > settings.UPLOAD_QUOTE_MEASURE_CONCURRENCY:
        cache.decr(SLOTS_KEY)
        return False
    return True


def release():
    try:
        if cache.decr(SLOTS_KEY) < 0:
            cache.set(SLOTS_KEY, 0, timeout=SLOTS_TTL)
    except ValueError:
        cache.set(SLOTS_KEY, 0, timeout=SLOTS_TTL)


def enqueue(file_ids):
    """Ask for the measure of each file, on the measure queue."""
    from students.tasks import measure_quote_file

    for file_id in file_ids:
        measure_quote_file.apply_async(args=(str(file_id),), queue=QUEUE)


def _give_up(row_id, state, **fields):
    UploadQuoteFile.objects.filter(pk=row_id).update(state=state, **fields)


def do_measure(file_id):
    """Measure one file, then restart the quote's clock if that was the last
    file it was waiting for."""
    try:
        _measure(file_id)
    finally:
        quote_id = (
            UploadQuoteFile.objects.filter(pk=file_id)
            .values_list("quote_id", flat=True)
            .first()
        )
        if quote_id is not None:
            restart_clock(quote_id)


def _measure(file_id):
    """Measure one file. Raises KeptFileUnavailable (the file goes back to
    STORED for the next try) when the storage did not answer."""
    with transaction.atomic():
        row = (
            UploadQuoteFile.objects.select_for_update(of=("self",))
            .select_related("quote")
            .filter(pk=file_id)
            .first()
        )
        if (
            row is None
            or row.quote.state != UploadQuote.State.OPEN
            or row.state not in (State.STORED, State.MEASURING)
        ):
            return
        row.state = State.MEASURING
        row.measure_attempts += 1
        row.measured_at = timezone.now()
        row.save(update_fields=["state", "measure_attempts", "measured_at"])

    try:
        content = kept_files.load(row.storage_key)
    except kept_files.KeptFileMissing:
        audit_metrics.count("upload_quote_not_kept")
        _give_up(row.pk, State.NOT_KEPT, reason_code="QUOTE_FILE_NOT_KEPT")
        return
    except kept_files.KeptFileUnavailable:
        _give_up(row.pk, State.STORED)
        raise
    import hashlib

    if hashlib.sha256(content).hexdigest() != row.sha256:
        audit_metrics.count("upload_quote_not_kept")
        _give_up(row.pk, State.NOT_KEPT, reason_code="QUOTE_FILE_NOT_KEPT")
        return

    uploaded = SimpleUploadedFile(
        name=row.original_name, content=content, content_type=row.content_type
    )
    try:
        prepared = AssignmentProcessingService.prepare_ai_content(
            uploaded, BATCH_UPLOAD_PROMPT
        )
        tokens = page_tokens_of(prepared)
    except Exception as exc:  # noqa: BLE001 - any failure is "cannot say"
        reason = reason_of(exc)
        logger.info(
            "Upload quote file %s cannot be estimated (%s).",
            row.pk,
            type(exc).__name__,
        )
        _give_up(
            row.pk,
            State.MEASURED,
            readable=None,
            reason_code=reason[0].value if reason else "",
            measured_at=timezone.now(),
        )
        return
    UploadQuoteFile.objects.filter(pk=row.pk).update(
        state=State.MEASURED,
        readable=True,
        pages=len(tokens),
        page_tokens=tokens,
        reason_code="",
        measured_at=timezone.now(),
    )


def requeue_stuck(now=None):
    """Beat, every 5 minutes. Returns {"requeued", "gave_up"}."""
    now = now or timezone.now()
    cut = now - STALE_AFTER
    stuck = UploadQuoteFile.objects.filter(quote__state=UploadQuote.State.OPEN).filter(
        Q(state=State.STORED, created_at__lt=cut)
        | Q(state=State.MEASURING, measured_at__lt=cut)
    )
    requeued = gave_up = 0
    ids = []
    for row_id, attempts, quote_id in stuck.values_list(
        "pk", "measure_attempts", "quote_id"
    ):
        if attempts >= MAX_ATTEMPTS:
            _give_up(
                row_id, State.MEASURED, readable=None, measured_at=now, reason_code=""
            )
            audit_metrics.count("upload_quote_measure_gave_up")
            restart_clock(quote_id)
            gave_up += 1
        else:
            ids.append(row_id)
            requeued += 1
    if ids:
        enqueue(ids)
    alarm_cut = now - ALARM_AFTER
    late = (
        UploadQuoteFile.objects.filter(
            quote__state=UploadQuote.State.OPEN,
            state__in=[State.STORED, State.MEASURING],
            created_at__lt=alarm_cut,
        )
        .values("quote_id")
        .distinct()
        .count()
    )
    if late:
        audit_metrics.count("upload_quote_measure_overdue", late)
        logger.error("Upload quotes: %s still measuring after 30 minutes.", late)
    return {"requeued": requeued, "gave_up": gave_up}
