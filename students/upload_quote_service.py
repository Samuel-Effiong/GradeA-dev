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
from students import kept_files
from students.models import UploadQuote, UploadQuoteFile
from students.upload_quotes import (
    _delete_after_commit,
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


def _prepare(quote, files, positions):
    """Make the rows: a REFUSED row for a file the check refuses, a STORING row
    (random key, committed before any byte is stored) for a file to keep. A
    file whose bytes are already in the quote (a NOT_KEPT row does not count:
    it holds nothing) is a (position, row) pair naming the row it repeats.
    Returns [(entry, content or None)] in order."""
    seen: dict = {
        row.sha256: row
        for row in quote.files.exclude(sha256="")
        .exclude(state=State.NOT_KEPT)
        .order_by("position")
    }
    plans: list[Any] = []
    for position, uploaded_file in zip(positions, files, strict=True):
        name = (getattr(uploaded_file, "name", "") or "")[:255]
        reason = _refusal_of(uploaded_file)
        if reason:
            plans.append(
                (
                    UploadQuoteFile.objects.create(
                        quote=quote,
                        position=position,
                        state=State.REFUSED,
                        reason_code=reason,
                        original_name=name,
                        content_type=(uploaded_file.content_type or "")[:255],
                        size_bytes=uploaded_file.size or 0,
                        readable=False,
                    ),
                    None,
                )
            )
            continue
        content = uploaded_file.read()
        uploaded_file.seek(0)
        digest = hashlib.sha256(content).hexdigest()
        if digest in seen:
            plans.append(((position, seen[digest]), None))
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
        seen[digest] = row
        plans.append((row, content))
    return plans


def _store(plans):
    """Store each STORING row's bytes; a file the storage refuses ends
    NOT_KEPT (never kept, no key)."""
    for entry, content in plans:
        if content is None:
            continue
        try:
            kept_files.store(content, key=entry.storage_key)
        except kept_files.KeptFileError as exc:
            logger.warning(
                "Upload quote: a file could not be kept (%s).",
                type(exc.__cause__ or exc).__name__,
            )
            audit_metrics.count("upload_quote_not_kept")
            UploadQuoteFile.objects.filter(pk=entry.pk).update(
                state=State.NOT_KEPT,
                storage_key=None,
                reason_code="QUOTE_FILE_NOT_KEPT",
            )


def _settle(quote_pk, plans, guard):
    """Mark the stored rows STORED and return the entries. With `guard`, the
    quote is locked and looked at again first: if it ended while the bytes
    were being stored, the rows made by this call are deleted and the reason and
    the keys of the files this call stored are returned (the caller deletes
    them once the transaction is over), never a 201."""
    ours = [e.pk for e, content in plans if content is not None]
    with transaction.atomic():
        if guard:
            locked = UploadQuote.objects.select_for_update().get(pk=quote_pk)
            try:
                refuse_if_not_open(locked)
            except CodedError as ended:
                UploadQuoteFile.objects.filter(
                    pk__in=[e.pk for e, _ in plans if not isinstance(e, tuple)]
                ).delete()
                # The quote's own delete (cancel, expiry) may have run before
                # these bytes existed and cleared the row's key, so the files
                # go by the keys this call made, not by the rows.
                keys = [e.storage_key for e, c in plans if c is not None]
                return None, (ended, keys)
        UploadQuoteFile.objects.filter(pk__in=ours, state=State.STORING).update(
            state=State.STORED
        )
    entries: list[Any] = []
    for entry, _ in plans:
        if isinstance(entry, tuple):
            entries.append(entry)
        else:
            entries.append(UploadQuoteFile.objects.get(pk=entry.pk))
    return entries, None


def add_files(quote, files, positions=None):
    """Check and store `files` (uploaded files) in `quote`. `positions` are
    their 0-based places in the teacher's list (default 0..n-1). Returns one
    entry per file in order: the UploadQuoteFile row, or for a file whose
    bytes are already in this quote a (position, row) pair naming the row it
    repeats. Each file's row commits before its bytes are stored. Used for a
    quote nobody else can reach yet (the create); an add to an open quote goes
    through add_to_quote, which holds the lock."""
    positions = list(positions) if positions is not None else list(range(len(files)))
    plans = _prepare(quote, files, positions)
    _store(plans)
    entries, _ = _settle(quote.pk, plans, guard=False)
    return entries


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
    return is_ready(list(quote.files.all()) if rows is None else rows)


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
    """POST /{id}/files: the same row-first storing as the create, with
    positions continuing after the quote's last file. The quote is locked and
    looked at again before any row is written and again after the bytes are
    stored, so an add that loses a race with confirm, cancel or expiry answers
    QUOTE_CLOSED (or the 410) and leaves nothing behind."""
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
        check_room(locked.teacher, sum(f.size or 0 for f in files))
        plans = _prepare(locked, files, list(range(start, start + len(files))))
    _store(plans)
    entries, ended = _settle(quote.pk, plans, guard=True)
    if ended is not None:
        reason, keys = ended
        for key in keys:  # the transaction is over: this is after the commit
            _delete_after_commit(key)
        raise reason
    restart_clock(quote.pk)
    after_commit_measure([e for e in entries if not isinstance(e, tuple)])
    note_global_alert()
    return entries


def after_commit_measure(rows):
    """Ask for the measure of every STORED row once the request commits."""
    ids = [
        r.pk for r in rows if isinstance(r, UploadQuoteFile) and r.state == State.STORED
    ]
    if ids:
        transaction.on_commit(partial(enqueue_measures, ids))


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


def is_ready(rows):
    return all(row.state in SETTLED for row in rows)


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
            transaction.on_commit(partial(_delete_now, row.pk, row.storage_key))
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
