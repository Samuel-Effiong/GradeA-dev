"""Upload quotes: the lifecycle that is not a route (plan rev 3 sections 6, 7;
BE-B-04/05/06).

* `open_bytes` and `open_count`: what a teacher's open quotes hold, for the
  1 GB and 3-quote limits, and every teacher's together for the global
  ceiling.
* `purge_due` (every 5 minutes): a READY open quote past `expires_at` becomes
  EXPIRED (one still measuring never does; an unready one older than the
  ceiling is purged all the same), the
  files of expired and cancelled quotes are deleted (a key whose asset never
  arrived deletes fine: the row came first), and the row of an ended quote
  goes after `UPLOAD_QUOTE_CONFIRMED_KEEP_DAYS` (the replay window of a second
  confirm). A failed delete is left for the next run.
* `on_file_deleted`: a quote file row deleted by erasure or a cascade takes
  its stored file with it after commit, unless a confirm handed the key to a
  KeptUpload and nulled it.

No file name appears in a log line or a metric here.
"""

import logging
from datetime import timedelta
from functools import partial

from django.conf import settings
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, Sum
from django.utils import timezone

from audit import metrics as audit_metrics
from students import kept_files

logger = logging.getLogger(__name__)

PURGE_BATCH = 200


def _quote_model():
    from students.models import UploadQuote

    return UploadQuote


def _file_model():
    from students.models import UploadQuoteFile

    return UploadQuoteFile


def open_bytes(teacher=None):
    """The bytes held by open quotes: the teacher's, or everyone's."""
    files = _file_model().objects.filter(
        quote__state=_quote_model().State.OPEN, purged_at__isnull=True
    )
    if teacher is not None:
        files = files.filter(quote__teacher=teacher)
    return files.aggregate(total=Sum("size_bytes"))["total"] or 0


def open_count(teacher):
    """How many open quotes the teacher has."""
    return (
        _quote_model()
        .objects.filter(teacher=teacher, state=_quote_model().State.OPEN)
        .count()
    )


def _delete_then_mark(row_id, storage_key):
    try:
        kept_files.delete(storage_key)
    except kept_files.KeptFileError as exc:
        logger.warning(
            "Upload quote file %s: delete failed; the purge retries (%s).",
            row_id,
            type(exc.__cause__ or exc).__name__,
        )
        return False
    _file_model().objects.filter(pk=row_id, purged_at__isnull=True).update(
        purged_at=timezone.now(), storage_key=None
    )
    return True


UNSETTLED = ("STORING", "STORED", "MEASURING")


def lifetime():
    return timedelta(minutes=settings.UPLOAD_QUOTE_LIFETIME_MINUTES)


def restart_clock(quote_id):
    """The quote just became ready (every file settled): its lifetime runs
    from now. Does nothing while any file is still being stored or measured."""
    file_model = _file_model()
    if file_model.objects.filter(quote_id=quote_id, state__in=UNSETTLED).exists():
        return False
    _quote_model().objects.filter(pk=quote_id, state=_quote_model().State.OPEN).update(
        expires_at=timezone.now() + lifetime()
    )
    return True


def purge_due(now=None):
    """Returns {"expired", "purged", "failed", "dropped"}."""
    now = now or timezone.now()
    quote_model = _quote_model()
    file_model = _file_model()
    state = quote_model.State

    unsettled = file_model.objects.filter(quote_id=OuterRef("pk"), state__in=UNSETTLED)
    ceiling = now - timedelta(minutes=settings.UPLOAD_QUOTE_UNREADY_CEILING_MINUTES)
    due = list(
        quote_model.objects.filter(state=state.OPEN)
        .annotate(unready=Exists(unsettled))
        .filter(
            Q(unready=False, expires_at__lte=now)
            | Q(unready=True, created_at__lte=ceiling)
        )
        .select_related("teacher")
    )
    expired = 0
    for quote in due:
        # The state is the claim: a quote another run already ended is not
        # counted or audited twice.
        if quote_model.objects.filter(pk=quote.pk, state=state.OPEN).update(
            state=state.EXPIRED, closed_at=now
        ):
            expired += 1
            from students.upload_quote_service import emit_ended

            emit_ended(quote, "expired")
    if expired:
        audit_metrics.count("upload_quote_expired", expired)

    purged = failed = 0
    tried = set()
    while True:
        batch = list(
            file_model.objects.filter(
                # Also a confirmed quote: a handed-over file's key is null, so
                # any key left on one was never handed over (a safety net).
                quote__state__in=[state.CONFIRMED, state.EXPIRED, state.CANCELLED],
                purged_at__isnull=True,
                storage_key__isnull=False,
            )
            .exclude(pk__in=tried)
            .order_by("created_at", "pk")
            .values_list("pk", "storage_key")[:PURGE_BATCH]
        )
        if not batch:
            break
        for row_id, storage_key in batch:
            tried.add(row_id)
            if _delete_then_mark(row_id, storage_key):
                purged += 1
            else:
                failed += 1

    # The replay window is over: drop ended quotes whose files are all gone.
    cutoff = now - timedelta(days=settings.UPLOAD_QUOTE_CONFIRMED_KEEP_DAYS)
    ended = quote_model.objects.filter(
        state__in=[state.CONFIRMED, state.EXPIRED, state.CANCELLED]
    )
    due = ended.filter(confirmed_at__lte=cutoff) | ended.filter(closed_at__lte=cutoff)
    # One file row decides: a quote with any file still stored is kept (a
    # multi-valued exclude would match rows of different files).
    still_stored = file_model.objects.filter(
        quote_id=OuterRef("pk"), purged_at__isnull=True, storage_key__isnull=False
    )
    due = due.annotate(held=Exists(still_stored)).filter(held=False).distinct()
    dropped = 0
    for quote_id in list(due.values_list("pk", flat=True)[:PURGE_BATCH]):
        quote_model.objects.filter(pk=quote_id).delete()
        dropped += 1
    return {"expired": expired, "purged": purged, "failed": failed, "dropped": dropped}


def note_gate_figure(*, quoted, gate):
    """BE-B-09: when the gate meters a quoted file's call, the quoted figure
    is recorded beside the gate's; a gate figure above the quoted one is
    counted (`upload_quote_under`, alert at the first). The hold is a
    reservation and the gate meters every real call, so this is the trust
    counter of the quote, not a refusal."""
    if quoted is None or gate is None or gate <= quoted:
        return
    audit_metrics.count("upload_quote_under")
    logger.error("Upload quote figure was under the gate's.")


def note_gate_for_task(processing_task_id, gate):
    """The gate's own figure for one call of a processing task, set against the
    figure the quote showed for that file (recorded on the item at confirm).
    Never raises and never blocks the gate: an error is logged and counted."""
    try:
        if not processing_task_id:
            return
        from students.models import BackgroundProcessingTask

        meta = (
            BackgroundProcessingTask.objects.filter(pk=processing_task_id)
            .values_list("meta", flat=True)
            .first()
        )
        quoted = (meta or {}).get("quoted_credits")
        if quoted:
            note_gate_figure(quoted=quoted, gate=gate)
    except Exception:  # noqa: BLE001 - the counter must never stop a real call
        logger.error("Upload quote: the gate figure could not be noted.")
        audit_metrics.count("upload_quote_gate_note_failed")


def _delete_after_commit(storage_key):
    try:
        kept_files.delete(storage_key)
    except kept_files.KeptFileError:
        logger.error("Upload quote: a deleted row's file wasn't deleted.")
        audit_metrics.count("upload_quote_delete_failed")


def on_file_deleted(sender, instance, **kwargs):
    """post_delete: the row is gone (cancel, erasure, a cascade), so its file
    goes once the delete commits, unless the key was handed over (null) or the
    purge already deleted it."""
    if instance.storage_key and instance.purged_at is None:
        transaction.on_commit(partial(_delete_after_commit, instance.storage_key))
