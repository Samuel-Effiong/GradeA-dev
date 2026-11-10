"""Kept upload files: the rows, the door, the worker side and the purge
(Epic B1b-5, 08b §3.2-§3.3; SM rulings NOTES §5.6).

* At the door (`DoorFile`): the file is stored before its item row, and a
  `KeptUpload` row is written in the item's transaction; the task message
  carries `{"kept_upload_id": ...}` instead of the bytes. If storage is down
  the route falls back to today's inline payload (owner, 08b §9 Q3), with no
  row, and `kept_upload_fallback` is counted. "Not kept" is the absence of a
  row, for a fallback file and for any item from before F4 alike.
* In the worker (`open_upload`): the bytes are loaded and their sha256
  checked. A storage outage raises `KeptFileUnavailable` (the worker's own
  retries); a missing or corrupt file is final (SM Q3): the row is marked
  purged, `kept_upload_integrity_failure` is counted and `KeptFileLost` is
  raised.
* The file is deleted when its item succeeds or is cancelled (Q3b, Q5;
  `discard`), by the hourly purge once `kept_until` has passed
  (`purge_due`), and after commit whenever its row is deleted (`on_row_deleted`:
  erasure, an item delete, any cascade). `purged_at` is set only after a
  delete succeeded; a failed one is left for the next purge.
"""

import base64
import hashlib
import logging
from datetime import timedelta
from functools import partial

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.utils import timezone

from audit import metrics as audit_metrics
from students import kept_files
from students.models import BackgroundTaskType

logger = logging.getLogger(__name__)

#: The three upload routes' task types (08b §3.1).
KEEPABLE_TYPES = frozenset(
    {
        BackgroundTaskType.ANSWER_EXTRACTION,
        BackgroundTaskType.BATCH_ANSWER_UPLOAD,
        BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
    }
)
PURGE_BATCH = 200
#: A row this long past kept_until and still unpurged is counted (08b §3.2).
OVERDUE_AFTER = timedelta(hours=24)


def _model():
    from students.models import KeptUpload

    return KeptUpload


class KeptFileLost(Exception):
    """The kept file is gone or corrupt: the item can't run from it."""


class DoorFile:
    """One uploaded file at the door: stored now, recorded with its item."""

    def __init__(self, uploaded_file):
        self.uploaded_file = uploaded_file
        content = uploaded_file.read()
        uploaded_file.seek(0)
        self.content = content
        self.row = None
        try:
            self.stored = kept_files.store(content)
        except kept_files.KeptFileError:
            logger.warning("Kept upload: storage unavailable; sending inline.")
            audit_metrics.count("kept_upload_fallback")
            self.stored = None

    def attach(self, processing_task, relaunch=None):
        """Write the row for `processing_task` (inside its transaction)."""
        if self.stored is None:
            return None
        now = timezone.now()
        self.row = _model().objects.create(
            processing_task=processing_task,
            storage_key=self.stored.storage_key,
            original_name=(self.uploaded_file.name or "")[:255],
            content_type=self.uploaded_file.content_type or "",
            size_bytes=self.stored.size_bytes,
            sha256=self.stored.sha256,
            relaunch=relaunch or {},
            kept_until=now + timedelta(days=settings.QUEUE_LIFETIME_DAYS),
            created_at=now,
        )
        return self.row

    def payload(self):
        """What the task message carries for this file."""
        if self.row is not None:
            return {"kept_upload_id": str(self.row.id)}
        return {
            "name": self.uploaded_file.name,
            "content_type": self.uploaded_file.content_type,
            "content_b64": base64.b64encode(self.content).decode("utf-8"),
        }

    def discard(self):
        """The item was never committed: delete the stored asset now."""
        if self.stored is None:
            return
        try:
            kept_files.delete(self.stored.storage_key)
        except kept_files.KeptFileError:
            logger.error("Kept upload: an uncommitted file wasn't deleted.")
            audit_metrics.count("kept_upload_delete_failed")
        self.stored = None
        self.row = None


def _lost(row, why):
    _model().objects.filter(pk=row.pk, purged_at__isnull=True).update(
        purged_at=timezone.now()
    )
    audit_metrics.count("kept_upload_integrity_failure", tags={"why": why})
    logger.error("Kept upload %s: the file is %s.", row.pk, why)
    raise KeptFileLost(why)


def is_kept(file_payload):
    """Whether a task's payload names a kept file (else it is inline)."""
    return "kept_upload_id" in file_payload


def open_upload(file_payload):
    """`(uploaded file, sha256)` for a kept payload. An inline payload is
    rebuilt by the worker exactly as before F4."""
    row = _model().objects.get(pk=file_payload["kept_upload_id"])
    if row.purged_at is not None:
        _lost(row, "missing")
    try:
        content = kept_files.load(row.storage_key)
    except kept_files.KeptFileMissing:
        _lost(row, "missing")
    if hashlib.sha256(content).hexdigest() != row.sha256:
        _lost(row, "corrupt")
    uploaded = SimpleUploadedFile(
        name=row.original_name, content=content, content_type=row.content_type
    )
    return uploaded, row.sha256


def _delete_then_mark(row_id, storage_key):
    try:
        kept_files.delete(storage_key)
    except kept_files.KeptFileError:
        logger.warning("Kept upload %s: delete failed; the purge retries.", row_id)
        return False
    _model().objects.filter(pk=row_id, purged_at__isnull=True).update(
        purged_at=timezone.now()
    )
    return True


def discard(processing_task):
    """The item succeeded or was cancelled: its file is no longer needed.
    Due at once, deleted after commit; a failed delete is the purge's."""
    if processing_task is None or processing_task.task_type not in KEEPABLE_TYPES:
        return
    rows = list(
        _model()
        .objects.filter(processing_task_id=processing_task.pk, purged_at__isnull=True)
        .values_list("pk", "storage_key")
    )
    if not rows:
        return
    _model().objects.filter(pk__in=[pk for pk, _ in rows]).update(
        kept_until=timezone.now()
    )
    for row_id, storage_key in rows:
        transaction.on_commit(partial(_delete_then_mark, row_id, storage_key))


def purge_due(now=None):
    """Delete every file past its kept_until, in batches of PURGE_BATCH.
    Returns {"purged", "failed", "overdue"}."""
    now = now or timezone.now()
    purged = failed = 0
    tried = set()
    while True:
        batch = list(
            _model()
            .objects.filter(purged_at__isnull=True, kept_until__lte=now)
            .exclude(pk__in=tried)
            .order_by("kept_until", "pk")
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
    overdue = (
        _model()
        .objects.filter(purged_at__isnull=True, kept_until__lt=now - OVERDUE_AFTER)
        .count()
    )
    if overdue:
        audit_metrics.count("kept_upload_purge_overdue", overdue)
        logger.error("Kept uploads: %s files are a day past their purge.", overdue)
    return {"purged": purged, "failed": failed, "overdue": overdue}


def _delete_after_commit(storage_key):
    try:
        kept_files.delete(storage_key)
    except kept_files.KeptFileError:
        logger.error("Kept upload: a deleted row's file wasn't deleted.")
        audit_metrics.count("kept_upload_delete_failed")


def on_row_deleted(sender, instance, **kwargs):
    """post_delete: the row is gone (erasure, an item delete, a cascade), so
    its file goes once the delete commits."""
    if instance.purged_at is None:
        transaction.on_commit(partial(_delete_after_commit, instance.storage_key))
