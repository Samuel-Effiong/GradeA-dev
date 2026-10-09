"""
User-facing refusals for uploaded assignment and answer files.

Both are final answers about one file, not transient faults: the upload
tasks never retry them (assignments.tasks.UPLOAD_REFUSALS), and
AutoGrader.error_messages passes their message to the teacher or student
verbatim.
"""

from rest_framework.exceptions import ParseError

from audit.enums import ReasonCode
from AutoGrader.reason_codes import REASON_CODES, CodedError
from AutoGrader.uploads import PayloadTooLarge


class InvalidUploadFileError(ParseError):
    """
    The file is not a readable image or PDF. The same bytes fail the same way
    on every attempt, so retrying can only re-run - and re-bill - work that
    cannot succeed. Subclasses ParseError so an API view that lets it
    propagate still answers 400.

    FR-A-06 (S6b): each file condition is now one of the coded subclasses
    below, answered with its own status and reason code (a view that lets
    one propagate reaches users/exceptions.py's coded branch before DRF's).
    Being subclasses, they stay upload refusals (assignments.tasks.
    UPLOAD_REFUSALS) and user-facing (AutoGrader.error_messages).
    """


class FileUnreadableError(CodedError, InvalidUploadFileError):
    """#3 FILE_UNREADABLE (422): damaged, truncated, encrypted, or not what
    its declared type says (a photo labelled application/pdf)."""

    reason_code = ReasonCode.FILE_UNREADABLE
    status_code = REASON_CODES[ReasonCode.FILE_UNREADABLE].http_status


class FileNotAPdfError(FileUnreadableError):
    """FILE_NOT_A_PDF (422, catalogue C, Epic A S7d): declared a PDF, but
    the bytes are a photo or scan. It was FILE_UNREADABLE, whose text lost
    the hint; being its subclass keeps every handler of that working."""

    reason_code = ReasonCode.FILE_NOT_A_PDF
    status_code = REASON_CODES[ReasonCode.FILE_NOT_A_PDF].http_status


class FileTypeUnsupportedError(CodedError, InvalidUploadFileError):
    """#4 FILE_TYPE_UNSUPPORTED (415): a type we don't accept at all."""

    reason_code = ReasonCode.FILE_TYPE_UNSUPPORTED
    status_code = REASON_CODES[ReasonCode.FILE_TYPE_UNSUPPORTED].http_status


class FileTooLargeError(PayloadTooLarge, InvalidUploadFileError):
    """#5 FILE_TOO_LARGE (413) found while reading the file: too many pages
    or pixels, or too large even after compression. The byte cap raises
    PayloadTooLarge itself, before the file is read."""


class SubmissionEmptyError(CodedError, InvalidUploadFileError):
    """#6 SUBMISSION_EMPTY (422) for an empty FILE: zero bytes or zero pages.
    Blank answers on a real page are not refused in Epic A (08a §6.1)."""

    reason_code = ReasonCode.SUBMISSION_EMPTY
    status_code = REASON_CODES[ReasonCode.SUBMISSION_EMPTY].http_status


class UploadAlreadyInProgressError(Exception):
    """
    The identical file is already being turned into an assignment for this
    course by another request. Refused instead of extracted a second time,
    which would charge the teacher twice for one assignment.
    """
