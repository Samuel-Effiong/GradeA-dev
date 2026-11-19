"""N2 (E2E rerun 2026-11-19, SM brief): the two Cloudinary warnings name the
exception class the storage raised, so an intermittent failure can be told
apart (timeout, auth, rate limit). Class name only: never the exception's
text, the storage key or the file's name."""

from unittest.mock import patch

import cloudinary.exceptions
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from students import kept_uploads

SECRET_TEXT = (
    "api_secret=abc123 key=kept_uploads/local/deadbeef"  # pragma: allowlist secret
)


@override_settings(KEPT_UPLOADS_BACKEND="cloudinary")
class TheWarningsNameTheClass(SimpleTestCase):
    def test_storage_unavailable_names_the_class(self):
        with patch(
            "students.kept_files.cloudinary.uploader.upload",
            side_effect=cloudinary.exceptions.AuthorizationRequired(SECRET_TEXT),
        ), patch("students.kept_uploads.audit_metrics.count"), self.assertLogs(
            "students.kept_uploads", "WARNING"
        ) as logs:
            kept_uploads.DoorFile(
                SimpleUploadedFile("Ada Okafor.pdf", b"%PDF-1.4 x", "application/pdf")
            )
        [line] = logs.output
        self.assertIn("storage unavailable; sending inline", line)
        self.assertIn("AuthorizationRequired", line)
        self.assertNotIn("abc123", line)
        self.assertNotIn("kept_uploads/", line)
        self.assertNotIn("Ada", line)

    def test_delete_failed_names_the_class(self):
        with patch(
            "students.kept_files.cloudinary.uploader.destroy",
            side_effect=TimeoutError(SECRET_TEXT),
        ), self.assertLogs("students.kept_uploads", "WARNING") as logs:
            done = kept_uploads._delete_then_mark(
                "row-1", "kept_uploads/local/" + "d" * 32
            )
        self.assertFalse(done)
        [line] = logs.output
        self.assertIn("delete failed; the purge retries", line)
        self.assertIn("TimeoutError", line)
        self.assertNotIn("abc123", line)
        self.assertNotIn("kept_uploads/", line)
