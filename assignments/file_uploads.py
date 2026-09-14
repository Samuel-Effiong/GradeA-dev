"""
Turning one uploaded file into a saved, billed assignment.

Shared by the synchronous upload view (AssignmentViewSet.upload_assignment)
and upload_assignment_async. A teacher who uploads a batch, sees part of it
fail, and uploads the whole batch again gets the same three guarantees from
both:

* A file that ends as failed is not charged. Extraction and the save run
  inside billing_refund_scope, so a provider error, an unusable response or
  a failed save refunds every charge the file made.
* A file that already produced an assignment is not extracted or charged
  again. The SHA-256 of its bytes is claimed per course before any AI call,
  and a later upload of the same bytes gets the existing assignment back.
* The same file arriving in two requests at once is extracted once. The
  claim is a unique row, so the second request is refused while the first
  is still working.
"""

import base64
import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import ParseError

from billing.refunds import billing_refund_scope
from students.task_tracking import (
    ensure_task_not_cancelled,
    lock_processing_task_for_final_save,
    merge_task_meta,
    update_processing_task,
)

from .exceptions import InvalidUploadFileError, UploadAlreadyInProgressError
from .models import Assignment, AssignmentUploadFingerprint
from .serializers import AssignmentSerializer
from .services import AssignmentProcessingService

logger = logging.getLogger(__name__)

# A PROCESSING claim older than this was left by a request or worker that
# died without releasing it, and may be taken over. It must stay longer than
# upload_assignment_async's hard time_limit (2100s): taking over a claim that
# is still being worked on would extract - and charge for - the file twice.
CLAIM_STALE_AFTER = timedelta(seconds=2400)

IN_PROGRESS_MESSAGE = (
    "This file is already being turned into an assignment by another upload. "
    "It will appear in the course when that finishes, so there is no need to "
    "upload it again."
)


@dataclass(frozen=True)
class UploadOutcome:
    assignment: Assignment
    already_uploaded: bool


@dataclass(frozen=True)
class _Claim:
    fingerprint_id: int
    token: uuid.UUID


def sha256_of_uploaded_file(uploaded_file):
    digest = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        digest.update(chunk)
    uploaded_file.seek(0)
    return digest.hexdigest()


def sha256_of_upload_payload(file_payload):
    """The same digest, from the payload the async upload view hands to
    Celery (AssignmentProcessingService.build_async_upload_payload)."""
    return hashlib.sha256(base64.b64decode(file_payload["content_b64"])).hexdigest()


def upload_assignment_file(
    user,
    uploaded_file,
    prompt_text,
    *,
    course,
    sha256,
    topic=None,
    processing_task_id=None,
):
    """
    Return an UploadOutcome for this file: the assignment it produced, or
    the one an earlier upload of the same bytes already produced.

    Raises InvalidUploadFileError for an unreadable file (before any AI
    call), UploadAlreadyInProgressError while another request holds the
    file's claim, and whatever extraction or the save raised otherwise -
    in every failure case with the file's charges refunded and its claim
    released, so the same file can simply be uploaded again.
    """
    claim = _claim(course, sha256)
    if isinstance(claim, UploadOutcome):
        return claim

    try:
        try:
            content = AssignmentProcessingService.prepare_ai_content(
                uploaded_file, prompt_text
            )
        except ParseError as exc:
            raise InvalidUploadFileError(exc.detail) from exc

        update_processing_task(
            processing_task_id, meta={"step": "Extracting assignment"}
        )
        ensure_task_not_cancelled(processing_task_id)
        with billing_refund_scope(reason="assignment upload failed"):
            assignment_questions = AssignmentProcessingService.extract_assignment_data(
                user,
                content,
                course=course,
                topic=topic,
                generate_raw_input=True,
                upload=True,
                processing_task_id=processing_task_id,
            )

            update_processing_task(
                processing_task_id, meta={"step": "Saving assignment"}
            )
            with transaction.atomic():
                processing_task = lock_processing_task_for_final_save(
                    processing_task_id
                )
                serializer = AssignmentSerializer(data=assignment_questions)
                serializer.is_valid(raise_exception=True)
                assignment = serializer.save()
                _complete(claim, assignment)

                if processing_task:
                    processing_task.assignment = assignment
                    processing_task.meta = merge_task_meta(
                        processing_task.meta,
                        {"assignment_id": str(assignment.id)},
                    )
                    processing_task.save(
                        update_fields=["assignment", "meta", "updated_at"]
                    )
    except BaseException:
        _release(claim)
        raise

    return UploadOutcome(assignment=assignment, already_uploaded=False)


def _claim(course, sha256):
    """Claim this file for this course: a _Claim to go ahead with, or an
    UploadOutcome when an earlier upload already produced its assignment."""
    now = timezone.now()
    fingerprints = AssignmentUploadFingerprint.objects.select_for_update(
        of=("self",)
    ).select_related("assignment")

    with transaction.atomic():
        row = fingerprints.filter(course=course, sha256=sha256).first()
        if row is None:
            try:
                with transaction.atomic():
                    row = AssignmentUploadFingerprint.objects.create(
                        course=course, sha256=sha256, claimed_at=now
                    )
                return _Claim(fingerprint_id=row.id, token=row.claim_token)
            except IntegrityError:
                # A concurrent request inserted this claim first; the insert
                # waited for it to commit, so its row is readable now.
                row = fingerprints.get(course=course, sha256=sha256)

        if row.status == AssignmentUploadFingerprint.Status.COMPLETED:
            return UploadOutcome(assignment=row.assignment, already_uploaded=True)

        if row.claimed_at > now - CLAIM_STALE_AFTER:
            raise UploadAlreadyInProgressError(IN_PROGRESS_MESSAGE)

        row.claim_token = uuid.uuid4()
        row.claimed_at = now
        row.save(update_fields=["claim_token", "claimed_at"])
        return _Claim(fingerprint_id=row.id, token=row.claim_token)


def _complete(claim, assignment):
    completed = AssignmentUploadFingerprint.objects.filter(
        id=claim.fingerprint_id,
        claim_token=claim.token,
        status=AssignmentUploadFingerprint.Status.PROCESSING,
    ).update(status=AssignmentUploadFingerprint.Status.COMPLETED, assignment=assignment)

    if completed != 1:
        # The claim was taken over as stale while this extraction ran, so the
        # newer claimant is saving its own copy. Raising rolls this save back
        # - and, inside the refund scope, refunds its charges - rather than
        # leaving two assignments for one file.
        raise UploadAlreadyInProgressError(IN_PROGRESS_MESSAGE)


def _release(claim):
    # Only this claim, and only while unfinished: a claim since taken over
    # by another request carries a different token and is left alone.
    try:
        AssignmentUploadFingerprint.objects.filter(
            id=claim.fingerprint_id,
            claim_token=claim.token,
            status=AssignmentUploadFingerprint.Status.PROCESSING,
        ).delete()
    except Exception:
        logger.exception(
            "Could not release upload claim %s; the file stays refused as "
            "in progress until the claim goes stale.",
            claim.fingerprint_id,
        )
