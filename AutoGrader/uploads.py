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


class PayloadTooLarge(APIException):
    status_code = 413
    default_detail = "The uploaded file is too large."
    default_code = "file_too_large"


# A scanned assignment PDF is the largest legitimate upload this project
# handles; 25 MB comfortably covers a multi-page scan at print resolution
# without leaving room for the kind of upload that only makes sense as
# abuse of a paid AI-extraction endpoint.
MAX_UPLOAD_SIZE_BYTES = 50 * 1024 * 1024


def validate_upload_size(uploaded_file, max_size_bytes=MAX_UPLOAD_SIZE_BYTES):
    """Raise PayloadTooLarge if uploaded_file exceeds max_size_bytes."""
    if uploaded_file.size > max_size_bytes:
        max_mb = max_size_bytes // (1024 * 1024)
        raise PayloadTooLarge(
            detail=(
                f"{uploaded_file.name!r} is too large "
                f"({uploaded_file.size / (1024 * 1024):.1f} MB). "
                f"Files must be {max_mb} MB or smaller."
            )
        )
