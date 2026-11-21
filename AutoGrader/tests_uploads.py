import logging

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from AutoGrader import uploads
from AutoGrader.uploads import (
    PayloadEmpty,
    PayloadTooLarge,
    max_upload_size_bytes,
    validate_upload_size,
    warn_if_file_cap_above_request_cap,
)

MB = 1024 * 1024


class ValidateUploadSizeTests(SimpleTestCase):
    def test_accepts_file_under_the_limit(self):
        small_file = SimpleUploadedFile("small.pdf", b"x" * 1024)
        validate_upload_size(small_file)  # should not raise

    def test_accepts_file_exactly_at_the_limit(self):
        exact_file = SimpleUploadedFile("exact.pdf", b"x" * max_upload_size_bytes())
        validate_upload_size(exact_file)  # should not raise

    def test_rejects_file_over_the_limit(self):
        oversized_file = SimpleUploadedFile(
            "huge.pdf", b"x" * (max_upload_size_bytes() + 1)
        )
        with self.assertRaises(PayloadTooLarge) as ctx:
            validate_upload_size(oversized_file)
        self.assertEqual(ctx.exception.status_code, 413)
        self.assertIn("huge.pdf", str(ctx.exception.detail))

    def test_respects_a_custom_limit(self):
        oversized_file = SimpleUploadedFile("medium.pdf", b"x" * 2000)
        with self.assertRaises(PayloadTooLarge):
            validate_upload_size(oversized_file, max_size_bytes=1000)

    def test_rejects_zero_byte_file(self):
        empty_file = SimpleUploadedFile("empty.pdf", b"")
        with self.assertRaises(PayloadEmpty) as ctx:
            validate_upload_size(empty_file)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("empty.pdf", str(ctx.exception.detail))

    def test_zero_byte_message_names_the_file(self):
        empty_file = SimpleUploadedFile("my-essay.pdf", b"")
        with self.assertRaises(PayloadEmpty) as ctx:
            validate_upload_size(empty_file)
        self.assertIn("my-essay.pdf", str(ctx.exception.detail))
        self.assertIn("empty", str(ctx.exception.detail).lower())

    def test_zero_byte_is_not_payload_too_large(self):
        empty_file = SimpleUploadedFile("empty.pdf", b"")
        with self.assertRaises(PayloadEmpty):
            validate_upload_size(empty_file)

    def test_one_byte_file_is_accepted(self):
        tiny_file = SimpleUploadedFile("tiny.pdf", b"x")
        validate_upload_size(tiny_file)  # should not raise


class ThePerFileLimitIsASetting(SimpleTestCase):
    """Owner rulings 2026-11-20: MAX_UPLOAD_FILE_SIZE_MB (env, default 10:
    the Cloudinary Free plan's raw limit, R1 spike; Q17 C), read when a file
    is checked, so a changed setting applies at once."""

    def test_the_default_is_10_mb(self):
        self.assertEqual(getattr(settings, "MAX_UPLOAD_FILE_SIZE_MB", None), 10)
        self.assertEqual(max_upload_size_bytes(), 10 * MB)

    def test_by_default_a_file_over_10_mb_is_refused(self):
        validate_upload_size(SimpleUploadedFile("ok.pdf", b"x" * (10 * MB)))
        with self.assertRaises(PayloadTooLarge) as ctx:
            validate_upload_size(SimpleUploadedFile("scan.pdf", b"x" * (10 * MB + 1)))
        self.assertEqual(ctx.exception.params["limit"], 10 * MB)

    @override_settings(MAX_UPLOAD_FILE_SIZE_BYTES=50 * MB)
    def test_the_setting_can_raise_it_to_50_mb(self):
        self.assertEqual(max_upload_size_bytes(), 50 * MB)
        validate_upload_size(SimpleUploadedFile("scan.pdf", b"x" * int(10.5 * MB)))

    def test_the_old_name_still_reads_the_limit(self):
        with override_settings(MAX_UPLOAD_FILE_SIZE_BYTES=10 * MB):
            self.assertEqual(uploads.MAX_UPLOAD_SIZE_BYTES, 10 * MB)

    @override_settings(MAX_UPLOAD_FILE_SIZE_BYTES=10 * MB)
    def test_at_10_mb_a_10_5_mb_file_is_refused_and_the_message_says_10_mb(self):
        validate_upload_size(SimpleUploadedFile("ok.pdf", b"x" * (10 * MB)))
        big = SimpleUploadedFile("scan.pdf", b"x" * int(10.5 * MB))
        with self.assertRaises(PayloadTooLarge) as ctx:
            validate_upload_size(big)
        self.assertEqual(
            str(ctx.exception.detail), "scan.pdf is 10.5 MB and the limit is 10 MB."
        )
        self.assertEqual(ctx.exception.params["limit"], 10 * MB)
        self.assertEqual(ctx.exception.params["actual"], int(10.5 * MB))


class AFileLimitAboveTheRequestCapIsWarned(SimpleTestCase):
    def test_above_the_request_cap_logs_a_warning(self):
        with override_settings(
            MAX_UPLOAD_FILE_SIZE_MB=600,
            MAX_UPLOAD_FILE_SIZE_BYTES=600 * MB,
            UPLOAD_REQUEST_MAX_MB=500,
            UPLOAD_REQUEST_MAX_BYTES=500 * MB,
        ), self.assertLogs("AutoGrader.uploads", logging.WARNING) as logs:
            warn_if_file_cap_above_request_cap()
        self.assertEqual(len(logs.records), 1)
        self.assertIn("MAX_UPLOAD_FILE_SIZE_MB", logs.output[0])

    def test_at_or_under_the_request_cap_logs_nothing(self):
        with self.assertNoLogs("AutoGrader.uploads", logging.WARNING):
            warn_if_file_cap_above_request_cap()

    def test_the_students_app_checks_it_at_startup(self):
        from unittest.mock import patch

        from django.apps import apps

        with patch("AutoGrader.uploads.warn_if_file_cap_above_request_cap") as warn:
            apps.get_app_config("students").ready()
        warn.assert_called_once_with()
