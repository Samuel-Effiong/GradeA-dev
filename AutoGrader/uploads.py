"""Shared upload-size guard for assignment/submission file uploads.

Django's DATA_UPLOAD_MAX_MEMORY_SIZE only caps what's buffered in memory --
a multipart file part larger than that simply spills to temp disk and is
still accepted (see django.http.multipartparser). Nothing else in the
request pipeline bounds how large an uploaded PDF/image can be, and every
upload endpoint feeds the file straight into a synchronous AI extraction
call. This module is the single place that cap is enforced, so it can't
drift between the five call sites that accept a file upload.
"""

import logging

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from rest_framework.exceptions import APIException

from audit.enums import ReasonCode
from AutoGrader.reason_codes import REQUEST_VARIANT, CodedError


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


logger = logging.getLogger(__name__)


def max_upload_size_bytes():
    """The per-file limit in bytes: settings.MAX_UPLOAD_FILE_SIZE_MB (env,
    default 10, the Cloudinary Free plan's raw-file limit; owner rulings
    2026-11-20), read on every call so a changed setting applies at once."""
    return int(settings.MAX_UPLOAD_FILE_SIZE_BYTES)


def __getattr__(name):
    # The old constant's name, for callers that still import it: it reads
    # the setting when it is looked up. New code calls max_upload_size_bytes().
    if name == "MAX_UPLOAD_SIZE_BYTES":
        return max_upload_size_bytes()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def warn_if_file_cap_above_request_cap():
    """Startup check: a per-file limit above the request cap can never be
    reached through one upload. Logged as a WARNING, not refused."""
    if max_upload_size_bytes() > int(settings.UPLOAD_REQUEST_MAX_BYTES):
        logger.warning(
            "MAX_UPLOAD_FILE_SIZE_MB (%s) is above UPLOAD_REQUEST_MAX_MB (%s): "
            "no single upload request can carry a file that large.",
            settings.MAX_UPLOAD_FILE_SIZE_MB,
            settings.UPLOAD_REQUEST_MAX_MB,
        )


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
        max_size_bytes = max_upload_size_bytes()
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


def refuse_oversized_upload_request(request, remediation=None):
    """Owner Q5 A (2026-11-19): FILE_TOO_LARGE (413) when the request's
    Content-Length is over UPLOAD_REQUEST_MAX_BYTES; nothing else is read.

    Called before authentication and before anything touches the body, so
    nothing is parsed, spooled to disk, stored, held or queued. A request
    without a Content-Length (chunked) is read by Django as an empty body
    (django.core.handlers.wsgi.LimitedStream(0)), so it cannot pass the cap
    and is left to the route, which answers it as today.

    The refused request is marked anonymous first: the exception handler
    logs `request.user`, and authenticating a session-cookie user runs the
    CSRF check, which parses the body. No user, so no audit event."""
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        return
    cap = int(settings.UPLOAD_REQUEST_MAX_BYTES)
    if length <= cap:
        return
    request.user = AnonymousUser()
    request.auth = None
    raise PayloadTooLarge(
        params={"actual": length, "limit": cap, "dimension": "bytes"},
        display={"actual": human_size(length), "limit": human_size(cap)},
        variant=REQUEST_VARIANT,
        remediation=remediation,
    )


class UploadRequestCapMixin:
    """A ViewSet whose upload actions refuse a request over the cap before
    DRF authenticates it or reads its body. `capped_upload_actions` maps
    each such action to the remediation its refusal reads (None: the
    catalogue's own)."""

    capped_upload_actions: dict = {}

    def initial(self, request, *args, **kwargs):
        action = getattr(self, "action", None)
        if action in self.capped_upload_actions:
            refuse_oversized_upload_request(
                request, remediation=self.capped_upload_actions[action]
            )
        super().initial(request, *args, **kwargs)  # type: ignore[misc]
