from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from AutoGrader.uploads import (
    MAX_UPLOAD_SIZE_BYTES,
    PayloadTooLarge,
    validate_upload_size,
)


class ValidateUploadSizeTests(SimpleTestCase):
    def test_accepts_file_under_the_limit(self):
        small_file = SimpleUploadedFile("small.pdf", b"x" * 1024)
        validate_upload_size(small_file)  # should not raise

    def test_accepts_file_exactly_at_the_limit(self):
        exact_file = SimpleUploadedFile("exact.pdf", b"x" * MAX_UPLOAD_SIZE_BYTES)
        validate_upload_size(exact_file)  # should not raise

    def test_rejects_file_over_the_limit(self):
        oversized_file = SimpleUploadedFile(
            "huge.pdf", b"x" * (MAX_UPLOAD_SIZE_BYTES + 1)
        )
        with self.assertRaises(PayloadTooLarge) as ctx:
            validate_upload_size(oversized_file)
        self.assertEqual(ctx.exception.status_code, 413)
        self.assertIn("huge.pdf", str(ctx.exception.detail))

    def test_respects_a_custom_limit(self):
        oversized_file = SimpleUploadedFile("medium.pdf", b"x" * 2000)
        with self.assertRaises(PayloadTooLarge):
            validate_upload_size(oversized_file, max_size_bytes=1000)
