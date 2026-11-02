"""Shared upload-size guard for assignment/submission file uploads.

Django's DATA_UPLOAD_MAX_MEMORY_SIZE only caps what's buffered in memory --
a multipart file part larger than that simply spills to temp disk and is
still accepted (see django.http.multipartparser). Nothing else in the
request pipeline bounds how large an uploaded PDF/image can be, and every
upload endpoint feeds the file straight into a synchronous AI extraction
call. This module is the single place that cap is enforced, so it can't
drift between the five call sites that accept a file upload.
"""

from rest_framework.exceptions import APIException

from audit.enums import ReasonCode
from AutoGrader.reason_codes import CodedError


class PayloadTooLarge(CodedError, APIException):
    """FR-A-06 FILE_TOO_LARGE (413), for bytes, pages or pixels.

    Coded: its params are the file name, `actual` and `limit` as numbers in
    the unit `dimension` names, and its message shows them formatted (the
    `display` argument), and a view
    that lets it propagate answers with the coded body. Still an
    APIException, so `.detail` is that same display message and the
    existing `except PayloadTooLarge` callers keep working."""

    reason_code = ReasonCode.FILE_TOO_LARGE
    status_code = 413
    default_code = "file_too_large"


class PayloadEmpty(CodedError, APIException):
    """FILE_EMPTY (400): an upload whose size is exactly 0 bytes."""

    reason_code = ReasonCode.FILE_EMPTY
    status_code = 400
    default_code = "file_empty"


# A scanned assignment PDF is the largest legitimate upload this project
# handles; 50 MB comfortably covers a multi-page scan at print resolution
# without leaving room for the kind of upload that only makes sense as
# abuse of a paid AI-extraction endpoint.
MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024


def human_size(size_bytes):
    """A byte count as a user reads it: "50 MB" for a whole number of units,
    "63.2 MB" otherwise, falling back to KB and then bytes below 1 MB."""
    for unit, name in ((1024 * 1024, "MB"), (1024, "KB")):
        if size_bytes >= unit:
            if size_bytes % unit == 0:
                return f"{size_bytes // unit} {name}"
            return f"{size_bytes / unit:.1f} {name}"
    return f"{size_bytes} bytes"


def file_name_of(uploaded_file):
    """The name to show for an upload; never None (params are scalars)."""
    return str(getattr(uploaded_file, "name", "") or "The file")


def validate_upload_size(uploaded_file, max_size_bytes=None, *, allow_empty=False):
    """Raise PayloadEmpty or PayloadTooLarge based on uploaded_file's size.

    allow_empty=True leaves a 0-byte file to the caller's own rules (the
    student answer route answers 415 or 422 SUBMISSION_EMPTY, per S6)."""
    if uploaded_file.size == 0 and not allow_empty:
        raise PayloadEmpty(params={"file_name": file_name_of(uploaded_file)})
    if max_size_bytes is None:
        max_size_bytes = MAX_UPLOAD_SIZE_BYTES
    if uploaded_file.size > max_size_bytes:
        raise PayloadTooLarge(
            params={
                "file_name": file_name_of(uploaded_file),
                "actual": int(uploaded_file.size),
                "limit": int(max_size_bytes),
                "dimension": "bytes",
            },
            display={
                "actual": human_size(uploaded_file.size),
                "limit": human_size(max_size_bytes),
            },
        )
