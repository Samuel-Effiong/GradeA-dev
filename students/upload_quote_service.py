"""Upload quotes: the work behind the routes (plan rev 3 sections 3 to 5;
BE-B-04/05/06).

* `create_quote` and `add_files`: a quote is made and its files are stored.
  `add_files(quote, files, positions)` is the one place files are checked and
  stored, so the one-request create calls it and an add-files route, if the
  owner wants one, is a thin view over it. Per file: type, empty and size are
  checked here, the sha256 dedupes inside the quote, the row is written with
  its random key BEFORE the bytes are stored (a dead request leaves a row the
  purge clears), and a file the store cannot keep is `NOT_KEPT`, never an
  inline fallback. One bad file never fails the others.
* `payload`: the quote object, computed against the live balance and the live
  text part, so the figure shown is the door's number for these bytes now.
* `cancel`: files deleted after commit.

Nothing here makes an AI call, takes a hold, writes the ledger or creates a
task: a quote is its own pair of tables. No file name is logged or returned.
"""

import hashlib
import logging
from datetime import timedelta
from functools import partial
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from ai_processor import call_estimates
from assignments.services import AssignmentProcessingService
from assignments.upload_door import door_balance, plan_parking
from assignments.upload_prompts import BATCH_UPLOAD_PROMPT
from audit import metrics as audit_metrics
from audit.emitter import emit
from audit.enums import AuditAction, AuditOutcome
from AutoGrader import uploads
from AutoGrader.reason_codes import CodedError
from billing import estimates
from students import kept_files, quote_store_pool
from students.models import UploadQuote, UploadQuoteFile, UploadQuoteStagedFile
from students.upload_quotes import (
    STORE_DEADLINE,
    STORE_STALE_AFTER,
    _delete_after_commit,
    _delete_key,
    lifetime,
    open_bytes,
    open_count,
    restart_clock,
)

logger = logging.getLogger(__name__)

State = UploadQuoteFile.State
ALLOWED_TYPES = (
    *AssignmentProcessingService.IMAGE_FORMATS,
    AssignmentProcessingService.PDF_FORMAT,
)
#: Files in a state that will not change any more: the quote is `ready` when
#: every file is one of these.
SETTLED = (State.MEASURED, State.REFUSED, State.NOT_KEPT)


def task_type_of(quote):
    if quote.kind == UploadQuote.Kind.ANSWER_PAPERS:
        return call_estimates.EXTRACT_ANSWER
    return call_estimates.EXTRACT_ASSIGNMENT


def enqueue_measures(file_ids):
    """Ask the measure worker for each stored file, after the request's
    transaction commits (students.upload_quote_measure)."""
    from students.upload_quote_measure import enqueue

    enqueue(file_ids)


# ----------------------------------------------------------------- create


def check_room(user, size):
    """QUOTE_STORAGE_FULL when `size` more bytes would take the teacher past
    their open bytes or every open quote past the global ceiling."""
    if open_bytes(user) + size > settings.UPLOAD_QUOTE_OPEN_BYTES:
        raise CodedError("QUOTE_STORAGE_FULL")
    if open_bytes() + size > settings.UPLOAD_QUOTE_GLOBAL_OPEN_BYTES_MAX:
        audit_metrics.count("upload_quote_global_full")
        raise CodedError("QUOTE_STORAGE_FULL")
    if staged_bytes() + size > settings.UPLOAD_QUOTE_STAGED_BYTES_MAX:
        audit_metrics.count("upload_quote_staged_full")
        raise CodedError("QUOTE_STORAGE_FULL")


def staged_bytes():
    """The bytes every quote holds in the database, waiting for the store."""
    from django.db.models import Sum

    total = UploadQuoteStagedFile.objects.aggregate(total=Sum("size_bytes"))
    return total["total"] or 0


def create_quote(user, *, kind, assignment=None, course=None, relaunch=None, key=None):
    """(quote, created). A repeat of the same Idempotency-Key returns the
    teacher's quote with created False. QUOTE_TOO_MANY_OPEN past the limit."""
    if key:
        existing = UploadQuote.objects.filter(teacher=user, idempotency_key=key).first()
        if existing is not None:
            return existing, False
    if open_count(user) >= settings.UPLOAD_QUOTE_MAX_OPEN:
        raise CodedError("QUOTE_TOO_MANY_OPEN")
    try:
        with transaction.atomic():
            quote = UploadQuote.objects.create(
                teacher=user,
                kind=kind,
                assignment=assignment,
                course=course,
                relaunch=relaunch or {},
                idempotency_key=key or None,
                expires_at=timezone.now() + lifetime(),
            )
    except IntegrityError:
        # Two requests with one key: the second finds the first's quote.
        return UploadQuote.objects.get(teacher=user, idempotency_key=key), False
    return quote, True


def _refusal_of(uploaded_file):
    """The reason code the door would refuse this file with, from what is
    cheap to read (no rasterizing), else None."""
    if uploaded_file.content_type not in ALLOWED_TYPES:
        return "FILE_TYPE_UNSUPPORTED"
    try:
        uploads.validate_upload_size(
            uploaded_file, max_size_bytes=uploads.max_upload_size_bytes()
        )
    except uploads.PayloadEmpty:
        return "FILE_EMPTY"
    except uploads.PayloadTooLarge:
        return "FILE_TOO_LARGE"
    return None


def kept_size(files):
    """The bytes of the files the check would keep: a file it refuses (type,
    empty, size) never fills the room, so it gets its own entry, not
    QUOTE_STORAGE_FULL."""
    return sum(f.size or 0 for f in files if _refusal_of(f) is None)


def _bytes_of_upload(uploaded_file):
    """One uploaded file's bytes, read from its start (one file at a time)."""
    uploaded_file.seek(0)
    try:
        return uploaded_file.read()
    finally:
        uploaded_file.seek(0)


def _prepare(quote, files, positions):
    """Make the rows, inside the caller's transaction: a REFUSED row for a
    file the check refuses; for a file to keep, a STORING row (random key)
    and its staged bytes (UploadQuoteStagedFile), one file read at a time. A
    file whose bytes are already in the quote (a NOT_KEPT row does not count:
    it holds nothing) is a (position, row) pair naming the row it repeats.
    Returns the entries in order."""
    seen: dict = {
        row.sha256: row
        for row in quote.files.exclude(sha256="")
        .exclude(state=State.NOT_KEPT)
        .order_by("position")
    }
    entries: list[Any] = []
    for position, uploaded_file in zip(positions, files, strict=True):
        name = (getattr(uploaded_file, "name", "") or "")[:255]
        reason = _refusal_of(uploaded_file)
        if reason:
            entries.append(
                UploadQuoteFile.objects.create(
                    quote=quote,
                    position=position,
                    state=State.REFUSED,
                    reason_code=reason,
                    original_name=name,
                    content_type=(uploaded_file.content_type or "")[:255],
                    size_bytes=uploaded_file.size or 0,
                    readable=False,
                )
            )
            continue
        content = _bytes_of_upload(uploaded_file)
        digest = hashlib.sha256(content).hexdigest()
        if digest in seen:
            entries.append((position, seen[digest]))
            continue
        row = UploadQuoteFile.objects.create(
            quote=quote,
            position=position,
            state=State.STORING,
            storage_key=kept_files.new_key(),
            original_name=name,
            content_type=(uploaded_file.content_type or "")[:255],
            size_bytes=len(content),
            sha256=digest,
        )
        # By id, not file=row: the row would cache the staged object (and
        # its bytes) for as long as the entries live.
        UploadQuoteStagedFile.objects.create(
            file_id=row.pk, content=content, sha256=digest, size_bytes=len(content)
        )
        del content
        seen[digest] = row
        entries.append(row)
    return entries


def _staging_ids(entries):
    return [
        e.pk
        for e in entries
        if isinstance(e, UploadQuoteFile) and e.state == State.STORING
    ]


def enqueue_store(quote_id, file_ids):
    """Ask the store task for these STORING files (students.tasks). A broker
    that does not answer never fails the request: it is logged and counted,
    the reply stays 201, and the sweeper asks again (the bytes are staged)."""
    from students.tasks import store_quote_files

    try:
        store_quote_files.apply_async(
            args=(str(quote_id), [str(i) for i in file_ids]), queue=STORE_QUEUE
        )
    except Exception as exc:  # noqa: BLE001 - the sweeper is the net
        logger.error(
            "Upload quote: the store task could not be asked for (%s).",
            type(exc).__name__,
        )
        audit_metrics.count("upload_quote_store_enqueue_failed")


def _store_after_commit(quote_id, entries):
    ids = _staging_ids(entries)
    if ids:
        transaction.on_commit(partial(enqueue_store, quote_id, ids))


def create_with_files(user, files, **kwargs):
    """(quote, created, entries): the quote and its files' rows and staged
    bytes in ONE transaction, so a staging that fails leaves no empty quote,
    and a replay of the same Idempotency-Key after a failure is a fresh
    attempt. A repeat of a key whose quote exists returns it (created False,
    entries None)."""
    with transaction.atomic():
        quote, created = create_quote(user, **kwargs)
        if not created:
            return quote, False, None
        return quote, True, add_files(quote, files)


def add_files(quote, files, positions=None):
    """Check and stage `files` (uploaded files) in `quote`, in ONE transaction,
    so a replay sees every row or none. `positions` are their 0-based places
    in the teacher's list (default 0..n-1). Returns one entry per file in
    order: the UploadQuoteFile row (REFUSED, or STORING with its bytes
    staged), or for a file whose bytes are already in this quote a
    (position, row) pair naming the row it repeats. The store task is asked
    for the STORING rows once the transaction commits; the reply does not
    wait for it. An add to an open quote goes through add_to_quote, which
    holds the lock."""
    positions = list(positions) if positions is not None else list(range(len(files)))
    with transaction.atomic():
        entries = _prepare(quote, files, positions)
        _store_after_commit(quote.pk, entries)
    return entries


# ------------------------------------------------------------------ store


#: The queue the store task runs on: declared in CELERY_TASK_QUEUES, so every
#: worker consumes it, and it never waits behind grading on `celery`.
STORE_QUEUE = "quote_measure"


class BytesChanged(kept_files.KeptFileError):
    """The bytes about to be stored are not the bytes the row was made from
    (their sha256 or size differ): nothing is stored."""


class ReplyMismatch(kept_files.KeptFileError):
    """The storage's reply names a different size or etag than the bytes
    sent: the file is not kept, and the settle deletes the asset."""


#: A store the storage did not answer (KeptFileUnavailable: an error or a
#: timeout) is tried this many times in all before the file counts as failed
#: (owner, 2026-11-26: one retry). The store task's own retries are apart.
STORE_ATTEMPTS = 2


def _is_refusal(exc):
    """Cloudinary's 400 (for example a file over its plan's size limit): the
    same bytes would be refused again, so it is not retried."""
    import cloudinary.exceptions

    return isinstance(exc.__cause__, cloudinary.exceptions.BadRequest)


def _check_reply(stored, content, key, size):
    """The storage's reply must name the size sent and (when it gives one) an
    etag equal to the md5 of the bytes; else the asset is deleted and the file
    is not kept."""
    reply_bytes = getattr(stored, "reply_bytes", None)
    reply_etag = getattr(stored, "reply_etag", None)
    wrong_size = reply_bytes is not None and int(reply_bytes) != size
    wrong_etag = (
        settings.UPLOAD_QUOTE_VERIFY_ETAG
        and reply_etag is not None
        and str(reply_etag).strip('"').lower()
        != hashlib.md5(content).hexdigest()  # nosec B324 - Cloudinary's etag is the md5
    )
    if not (wrong_size or wrong_etag):
        return
    # No delete here (a pool thread): the settle deletes the asset only if
    # the row is ours to end, never one a duplicate run already settled.
    audit_metrics.count("upload_quote_reply_mismatch")
    raise ReplyMismatch("size" if wrong_size else "etag")


def _store_one(content, key, sha256, size):
    """Runs in a pool thread: no database. Stores the bytes under the row's
    key, checking before each attempt that they are the bytes the row was made
    from (changed bytes are never retried or stored), and checks the storage's
    reply. A store the storage did not answer is tried once more."""
    for attempt in range(1, STORE_ATTEMPTS + 1):
        if len(content) != size or hashlib.sha256(content).hexdigest() != sha256:
            raise BytesChanged("sha256 or size differ from the row")
        try:
            stored = kept_files.store(content, key=key)
        except kept_files.KeptFileUnavailable as exc:
            if attempt == STORE_ATTEMPTS or _is_refusal(exc):
                raise
            audit_metrics.count("upload_quote_store_retried")
            continue
        _check_reply(stored, content, key, size)
        return stored
    raise AssertionError("unreachable")  # pragma: no cover


def _is_transient(exc):
    """A storage fault worth another try: the storage did not answer, or an
    error we do not know. A refusal (Cloudinary's 400, for example a file over
    its plan's size), changed bytes or a wrong reply are final."""
    if isinstance(exc, (BytesChanged, ReplyMismatch)):
        return False
    if isinstance(exc, kept_files.KeptFileUnavailable):
        return not _is_refusal(exc)
    return not isinstance(exc, kept_files.KeptFileError)


def _load(file_ids):
    """The STORING rows of `file_ids` that still have staged bytes, in the
    given order, with those bytes."""
    rows = {
        str(r["pk"]): r
        for r in UploadQuoteFile.objects.filter(
            pk__in=file_ids,
            state=State.STORING,
            storage_key__isnull=False,
            staged__isnull=False,
        ).values(
            "pk",
            "storage_key",
            "sha256",
            "size_bytes",
            "staged__content",
            "staged__sha256",
            "staged__size_bytes",
        )
    }
    return [rows[str(i)] for i in file_ids if str(i) in rows]


def _settle_batch(quote_id, stored, failed):
    """One transaction under the quote's lock. `stored`: [(pk, key)] whose
    bytes reached the storage; `failed`: [(pk, key, keep_key)] that end
    NOT_KEPT. While the quote is OPEN a stored row moves STORING -> STORED
    only if it still holds this key; otherwise (the quote ended, was erased,
    or the row moved on) the bytes this call stored are deleted. A failed row
    is deleted from the storage only when this call ended it or it no longer
    holds the key, never when a duplicate run settled it. `keep_key` (the
    last try timed out: the upload may still land) leaves the key on the
    NOT_KEPT row, so the purge deletes a late asset when the quote ends.
    Staged bytes of every row handled go. Returns the pks moved to STORED."""
    done: list = []
    orphans: list = []
    with transaction.atomic():
        quote = UploadQuote.objects.select_for_update().filter(pk=quote_id).first()
        if quote is not None and quote.state == UploadQuote.State.OPEN:
            now = timezone.now()
            for pk, key in stored:
                moved = UploadQuoteFile.objects.filter(
                    pk=pk, state=State.STORING, storage_key=key
                ).update(state=State.STORED, stored_at=now)
                if moved:
                    done.append(pk)
                elif not UploadQuoteFile.objects.filter(
                    pk=pk, storage_key=key
                ).exists():
                    # The row is gone or no longer holds this key (the
                    # sweeper ended it): what this call stored is ours alone.
                    orphans.append(key)
                # Else a second run of the task (a duplicate delivery)
                # settled the row first: the asset is the row's, keep it.
            for pk, key, keep_key in failed:
                ours = UploadQuoteFile.objects.filter(
                    pk=pk, state=State.STORING, storage_key=key
                )
                if keep_key:
                    ended = ours.update(
                        state=State.NOT_KEPT, reason_code="QUOTE_FILE_NOT_KEPT"
                    )
                else:
                    ended = ours.update(
                        state=State.NOT_KEPT,
                        storage_key=None,
                        reason_code="QUOTE_FILE_NOT_KEPT",
                    )
                if (
                    ended
                    or not UploadQuoteFile.objects.filter(
                        pk=pk, storage_key=key
                    ).exists()
                ):
                    # A failed store may still have left an asset (a timeout
                    # after the upload landed): deleting a key with none is
                    # not an error. A row a duplicate run settled keeps it.
                    orphans.append(key)
        else:
            # Ended (cancel, expiry) or erased: nothing this call stored may
            # stay. Cancel and the purge delete the keys too.
            orphans.extend(key for _, key in stored)
            orphans.extend(key for _, key, _ in failed)
        UploadQuoteStagedFile.objects.filter(
            file_id__in=[pk for pk, _ in stored] + [pk for pk, _, _ in failed]
        ).delete()
    for key in orphans:
        _delete_after_commit(key)
    return done


def store_staged(quote_id, file_ids, final=False):
    """The store task's body. Stores the staged bytes of `file_ids` (STORING
    rows of `quote_id`), UPLOAD_QUOTE_STORE_CONCURRENCY at a time: that many
    files' bytes are loaded, stored by the pool (only the storage call runs
    in its threads), and settled before the next ones are loaded. Then the
    measure is asked for the files STORED and the quote's clock restarts if
    nothing is left pending. Returns the ids to try again (a transient fault;
    their bytes stay staged); with `final` those end NOT_KEPT instead."""
    size = max(1, int(settings.UPLOAD_QUOTE_STORE_CONCURRENCY))
    ids = [str(i) for i in file_ids]
    pending: list = []
    for start in range(0, len(ids), size):
        batch = _load(ids[start : start + size])
        if not batch:
            continue
        calls = []
        checked = []
        failed = []
        for row in batch:
            content = bytes(row["staged__content"])
            row.pop("staged__content")
            same = (
                len(content) == row["size_bytes"] == row["staged__size_bytes"]
                and hashlib.sha256(content).hexdigest()
                == row["sha256"]
                == row["staged__sha256"]
            )
            if not same:
                audit_metrics.count("upload_quote_bytes_changed")
                logger.error(
                    "Upload quote: staged bytes changed; the file is not kept."
                )
                failed.append((row["pk"], row["storage_key"], False))
                continue
            checked.append(row)
            calls.append(
                partial(
                    _store_one,
                    content,
                    row["storage_key"],
                    row["sha256"],
                    row["size_bytes"],
                )
            )
            del content
        outcomes = quote_store_pool.run_all(calls)
        del calls
        stored = []
        for row, outcome in zip(checked, outcomes, strict=True):
            exc = outcome.error
            if exc is None:
                stored.append((row["pk"], row["storage_key"]))
                continue
            logger.warning(
                "Upload quote: a file could not be stored (%s).",
                type(exc.__cause__ or exc).__name__,
            )
            transient = _is_transient(exc)
            if transient and not final:
                pending.append(str(row["pk"]))
                continue
            audit_metrics.count("upload_quote_not_kept")
            failed.append((row["pk"], row["storage_key"], transient))
        done = _settle_batch(quote_id, stored, failed)
        if done:
            # Each batch's files go to the measure at once, not at the end.
            transaction.on_commit(partial(enqueue_measures, done))
    restart_clock(quote_id)
    note_global_alert()
    return pending


#: A sweeper re-ask of a file is not sent again within this (a cache key per
#: file), so a slow store is not met with a burst of tasks.
REASK_KEY = "upload_quote_store_asked:{}"


def _asked_lately(pk):
    """True when the sweeper asked for this file in the last STORE_STALE_AFTER;
    else marks it asked now."""
    from django.core.cache import cache

    return not cache.add(
        REASK_KEY.format(pk), 1, timeout=int(STORE_STALE_AFTER.total_seconds())
    )


def sweep_storing(now=None):
    """The store's net (beat, with requeue_stuck_quote_measures), for STORING
    files of open quotes not settled after STORE_STALE_AFTER:

    * bytes still staged: asked for again (at most once per
      STORE_STALE_AFTER), until the quote's unready ceiling
      (UPLOAD_QUOTE_UNREADY_CEILING_MINUTES); then NOT_KEPT;
    * nothing staged: NOT_KEPT after STORE_DEADLINE.

    A file it ends loses its key, which is deleted (a store still running
    then finds the row moved on and deletes what it stored). Returns
    {"store_requeued", "store_gave_up"}."""
    from django.db.models import Exists, OuterRef

    now = now or timezone.now()
    ceiling = now - timedelta(minutes=settings.UPLOAD_QUOTE_UNREADY_CEILING_MINUTES)
    rows = (
        UploadQuoteFile.objects.filter(
            quote__state=UploadQuote.State.OPEN,
            state=State.STORING,
            created_at__lt=now - STORE_STALE_AFTER,
        )
        .annotate(
            has_staged=Exists(
                UploadQuoteStagedFile.objects.filter(file_id=OuterRef("pk"))
            )
        )
        .values_list("pk", "quote_id", "storage_key", "has_staged", "created_at")
    )
    again: dict = {}
    gave_up = 0
    ended_quotes = set()
    for pk, quote_id, key, has_staged, created_at in rows:
        if has_staged and created_at >= ceiling:
            if not _asked_lately(pk):
                again.setdefault(quote_id, []).append(pk)
            continue
        if not has_staged and created_at >= now - STORE_DEADLINE:
            continue
        with transaction.atomic():
            moved = UploadQuoteFile.objects.filter(pk=pk, state=State.STORING).update(
                state=State.NOT_KEPT,
                storage_key=None,
                reason_code="QUOTE_FILE_NOT_KEPT",
            )
            UploadQuoteStagedFile.objects.filter(file_id=pk).delete()
        if moved:
            gave_up += 1
            ended_quotes.add(quote_id)
            if key:
                _delete_after_commit(key)
    for quote_id, ids in again.items():
        enqueue_store(quote_id, ids)
    for quote_id in ended_quotes:
        restart_clock(quote_id)
    if gave_up:
        audit_metrics.count("upload_quote_store_gave_up", gave_up)
        logger.error("Upload quotes: %s files were never stored.", gave_up)
    requeued = sum(len(ids) for ids in again.values())
    return {"store_requeued": requeued, "store_gave_up": gave_up}


# ------------------------------------------------------------ store slots

STORE_SLOTS_KEY = "upload_quote_store_slots"
STORE_SLOTS_TTL = 2 * 60 * 60


def acquire_store_slot():
    """Take one of UPLOAD_QUOTE_STORE_SLOTS; False when all are taken (the
    measure's cache-counter pattern, students.upload_quote_measure)."""
    from django.core.cache import cache

    cache.add(STORE_SLOTS_KEY, 0, timeout=STORE_SLOTS_TTL)
    if cache.incr(STORE_SLOTS_KEY) > settings.UPLOAD_QUOTE_STORE_SLOTS:
        cache.decr(STORE_SLOTS_KEY)
        return False
    return True


def release_store_slot():
    from django.core.cache import cache

    try:
        if cache.decr(STORE_SLOTS_KEY) < 0:
            cache.set(STORE_SLOTS_KEY, 0, timeout=STORE_SLOTS_TTL)
    except ValueError:
        cache.set(STORE_SLOTS_KEY, 0, timeout=STORE_SLOTS_TTL)


class TooManyFiles(Exception):
    """Adding these files would take the quote past UPLOAD_QUOTE_MAX_FILES."""


def expired_error(quote):
    return CodedError(
        "QUOTE_EXPIRED",
        params={
            "lifetime_minutes": settings.UPLOAD_QUOTE_LIFETIME_MINUTES,
            "expires_at": quote.expires_at.isoformat(),
        },
    )


def past_expiry(quote, rows=None):
    """An OPEN quote that is ready and past expires_at: expired now, though
    the purge (every 5 minutes) has not marked it yet. The rule confirm's 410
    and the purge use; a quote still measuring is not expired by its clock."""
    if quote.state != UploadQuote.State.OPEN or quote.expires_at > timezone.now():
        return False
    # A quote with no file expires by its clock too, as the purge does.
    return nothing_pending(list(quote.files.all()) if rows is None else rows)


def refuse_if_not_open(quote):
    """QUOTE_CLOSED for a started or cancelled quote, 410 for one that ended
    by expiry or is ready and past its expiry (the purge has not run yet)."""
    if quote.state in (UploadQuote.State.CONFIRMED, UploadQuote.State.CANCELLED):
        raise CodedError("QUOTE_CLOSED")
    if quote.state == UploadQuote.State.EXPIRED:
        raise expired_error(quote)
    if past_expiry(quote):
        raise expired_error(quote)


def add_to_quote(quote, files):
    """POST /{id}/files: the same staging as the create, with positions
    continuing after the quote's last file, under the quote's lock, so an add
    that loses a race with confirm, cancel or expiry answers QUOTE_CLOSED (or
    the 410) and writes nothing. The rows and their staged bytes commit
    together; the store task stores them after the reply and settles each
    only while the quote is still open."""
    with transaction.atomic():
        locked = UploadQuote.objects.select_for_update().get(pk=quote.pk)
        refuse_if_not_open(locked)
        last = (
            locked.files.order_by("-position")
            .values_list("position", flat=True)
            .first()
        )
        start = 0 if last is None else last + 1
        if locked.files.count() + len(files) > settings.UPLOAD_QUOTE_MAX_FILES:
            raise TooManyFiles
        check_room(locked.teacher, kept_size(files))
        entries = _prepare(locked, files, list(range(start, start + len(files))))
        _store_after_commit(quote.pk, entries)
    restart_clock(quote.pk)  # only when every file is settled (all refused)
    note_global_alert()
    return entries


def note_global_alert():
    if open_bytes() >= settings.UPLOAD_QUOTE_GLOBAL_OPEN_BYTES_ALERT:
        audit_metrics.count("upload_quote_global_alert")
        logger.error("Upload quotes: open quote bytes are over the alert level.")


# ------------------------------------------------------------------ read


def _assignment_of(quote):
    return quote.assignment if quote.kind == UploadQuote.Kind.ANSWER_PAPERS else None


def _already_uploaded(quote, rows):
    """Positions of assignment files the course already has the bytes of
    (assignments.file_uploads: answered by the existing assignment before any
    AI call)."""
    if quote.kind != UploadQuote.Kind.ASSIGNMENT_FILES or quote.course_id is None:
        return set()
    from assignments.models import AssignmentUploadFingerprint

    done = set(
        AssignmentUploadFingerprint.objects.filter(
            course_id=quote.course_id,
            status=AssignmentUploadFingerprint.Status.COMPLETED,
            sha256__in=[r.sha256 for r in rows if r.sha256],
        ).values_list("sha256", flat=True)
    )
    return {r.position for r in rows if r.sha256 in done}


def calls_of(task_type, pages):
    """How many calls the task makes for a file of `pages` pages."""
    from ai_processor.services import (
        ANSWERS_EXTRACTION_PAGES_PER_CHUNK,
        ASSIGNMENT_UPLOAD_PAGES_PER_CHUNK,
    )

    if task_type == call_estimates.EXTRACT_ANSWER:
        per_call = ANSWERS_EXTRACTION_PAGES_PER_CHUNK
        return 1 if pages < per_call else -(-pages // per_call)
    per_call = ASSIGNMENT_UPLOAD_PAGES_PER_CHUNK
    return 1 if pages <= per_call else -(-pages // per_call)


def figure_of(quote, row):
    """The door's figure for this measured file now (raw credits): its stored
    page tokens against the current prompt, assignment context and roster."""
    if row.state != State.MEASURED or not row.readable or row.page_tokens is None:
        return None
    return call_estimates.estimate_from_page_tokens(
        task_type_of(quote),
        row.page_tokens,
        [{"type": "text", "text": BATCH_UPLOAD_PROMPT}],
        _assignment_of(quote),
    )


def figures_of(quote, rows):
    """One figure per row in position order (None: left to the task, as the
    door does for a file it cannot estimate)."""
    return [figure_of(quote, row) for row in rows]


def nothing_pending(rows):
    """No file is still being stored or measured (true of no files)."""
    return all(row.state in SETTLED for row in rows)


def is_ready(rows):
    """Every file settled, and there is at least one: a quote with no file has
    nothing to quote or confirm (QUOTE_NOT_READY)."""
    return bool(rows) and nothing_pending(rows)


def payload(quote):
    """The quote object (plan rev 3 section 3). No file name."""
    rows = list(quote.files.order_by("position"))
    task_type = task_type_of(quote)
    ready = is_ready(rows)
    figures = figures_of(quote, rows)
    already = _already_uploaded(quote, rows)
    teacher = quote.teacher
    estimate = None
    would_park = []
    if ready:
        estimate = estimates.upload_advise(
            teacher, task_type, figures, already_uploaded=already
        )
        wallet = getattr(teacher, "credit_wallet", None)
        if wallet is not None:
            # An already-uploaded file holds nothing, as it counts nothing.
            held = [
                0 if r.position in already and f is not None else f
                for r, f in zip(rows, figures, strict=True)
            ]
            parked = plan_parking(door_balance(wallet), held)
            would_park = [rows[i].position for i in sorted(parked)]
    files = []
    for row, figure in zip(rows, figures, strict=True):
        estimated_cost = None
        if figure is not None:
            estimated_cost = (
                0 if row.position in already else estimates._display_cost(figure)
            )
        files.append(
            {
                "id": str(row.pk),
                "position": row.position,
                "state": row.state,
                "readable": row.readable,
                "reason_code": row.reason_code or None,
                "pages": row.pages,
                "calls": calls_of(task_type, row.pages) if row.pages else None,
                "estimated_cost": estimated_cost,
                "already_uploaded": row.position in already,
                "duplicate_of": None,
            }
        )
    return {
        "id": str(quote.pk),
        "kind": quote.kind,
        "state": (
            UploadQuote.State.EXPIRED if past_expiry(quote, rows) else quote.state
        ),
        "expires_at": quote.expires_at,
        "lifetime_minutes": settings.UPLOAD_QUOTE_LIFETIME_MINUTES,
        "ready": ready,
        "measured": sum(1 for r in rows if r.state in SETTLED),
        "total": len(rows),
        "session_id": str(quote.session_id) if quote.session_id else None,
        "estimate": estimate,
        "would_park": would_park,
        "files": files,
    }


def create_reply(quote, entries):
    """The create's reply: the quote object, with each submitted file's entry
    in submission order (a repeated file names the entry it repeats)."""
    body = payload(quote)
    by_id = {f["id"]: f for f in body["files"]}
    files = []
    for entry in entries:
        if isinstance(entry, tuple):
            position, first = entry
            repeat = dict(by_id[str(first.pk)])
            repeat.update(id=None, position=position, duplicate_of=str(first.pk))
            files.append(repeat)
        else:
            files.append(by_id[str(entry.pk)])
    body["files"] = files
    return body


def summary(quote):
    return {
        "id": str(quote.pk),
        "kind": quote.kind,
        "created_at": quote.created_at,
        "expires_at": quote.expires_at,
        "lifetime_minutes": settings.UPLOAD_QUOTE_LIFETIME_MINUTES,
        "file_count": quote.files.count(),
        "ready": is_ready(list(quote.files.all())),
    }


# ---------------------------------------------------------------- cancel


def cancel(quote):
    """Cancel an open quote: its files are deleted after commit. Repeating
    it is fine; after a confirm it is QUOTE_CLOSED."""
    with transaction.atomic():
        locked = UploadQuote.objects.select_for_update().get(pk=quote.pk)
        if locked.state == UploadQuote.State.CONFIRMED:
            raise CodedError("QUOTE_CLOSED")
        if locked.state != UploadQuote.State.OPEN:
            return locked
        locked.state = UploadQuote.State.CANCELLED
        locked.closed_at = timezone.now()
        locked.save(update_fields=["state", "closed_at"])
        rows = list(
            locked.files.filter(storage_key__isnull=False, purged_at__isnull=True)
        )
        for row in rows:
            if row.state == State.STORING:
                # Its store may still be running: the key's bytes may land
                # after this delete. The key stays on the row, so the purge
                # deletes it again until STORE_DEADLINE, and the store task
                # deletes what it stored once it sees the quote has ended.
                transaction.on_commit(partial(_delete_key, row.storage_key))
            else:
                transaction.on_commit(partial(_delete_now, row.pk, row.storage_key))
        UploadQuoteStagedFile.objects.filter(file__quote=locked).delete()
        emit_ended(locked, "cancelled", actor=locked.teacher)
    return locked


def emit_ended(quote, reason, actor=None):
    """One UPLOAD_QUOTE_ENDED event: ids and counts only, never a file name."""
    emit(
        AuditAction.UPLOAD_QUOTE_ENDED,
        actor=actor,
        target_type="UploadQuote",
        target_id=quote.pk,
        outcome=AuditOutcome.SUCCESS,
        metadata={
            "quote_id": str(quote.pk),
            "reason": reason,
            "file_count": quote.files.count(),
        },
    )


def _delete_now(row_id, storage_key):
    from students.upload_quotes import _delete_then_mark

    _delete_then_mark(row_id, storage_key)
