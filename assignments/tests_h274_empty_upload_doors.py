"""H-274 (e.1a): which door answers a 0-byte upload, and with which code.

SM ruling (A), 2026-11-02: the student answer route keeps the S6 contract
(``assignments.tests_file_reason_codes.StudentUploadAnswersWithItsOwnCode``:
an unsupported type is refused first with 415 FILE_TYPE_UNSUPPORTED, then a
0-byte answer with 422 SUBMISSION_EMPTY). The teacher doors keep (e.1)'s
FILE_EMPTY, per file: one empty file fails as its own entry or item, the
others still run, and nothing is left half-queued (as for 413). The caller
opts out of the empty check; validate_upload_size knows no route names.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from assignments.tests_upload_batch_billing import (
    BatchUploadBillingFixture,
    charges,
    upload_file,
)
from AutoGrader.uploads import PayloadEmpty, validate_upload_size
from students.models import BackgroundProcessingTask, BackgroundTaskStatus
from students.tests_batch_item_results import BatchFixture, png_bytes

#: (e.1)'s FILE_EMPTY text, unchanged.
EMPTY_PDF_TEXT = "'blank.pdf' is empty. Choose another file."


def _empty(name="blank.pdf", content_type="application/pdf"):
    return SimpleUploadedFile(name, b"", content_type=content_type)


class TheEmptyCheckIsTheCallersChoice(SimpleTestCase):
    def test_by_default_a_zero_byte_file_is_file_empty(self):
        with self.assertRaises(PayloadEmpty):
            validate_upload_size(_empty())

    def test_a_caller_may_leave_the_empty_check_to_its_own_rules(self):
        validate_upload_size(_empty(), allow_empty=True)  # must not raise

    def test_the_byte_cap_still_applies_when_empty_is_allowed(self):
        from AutoGrader.uploads import PayloadTooLarge

        big = SimpleUploadedFile("x.pdf", b"0" * 11, content_type="application/pdf")
        with self.assertRaises(PayloadTooLarge):
            validate_upload_size(big, max_size_bytes=10, allow_empty=True)


def _too_large(name):
    return SimpleUploadedFile(name, b"%PDF-1.4" + b"0" * 64, "application/pdf")


def _shape(value):
    """The response's structure with every scalar replaced by its type."""
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_shape(item) for item in value]
    return type(value).__name__


class TheSyncTeacherUploadFailsAnEmptyFileAsItsOwnEntry(BatchUploadBillingFixture):
    """assignment-upload (assignments/views.py:942)."""

    def post(self, files):
        client = APIClient()
        client.force_authenticate(user=self.teacher)
        return client.post(
            reverse("assignment-upload"),
            {"course": str(self.course.id), "assignments": files},
            format="multipart",
        )

    def test_one_empty_file_fails_only_itself(self):
        response = self.post(
            [upload_file("a.png"), _empty("blank.pdf"), upload_file("c.png")]
        )

        self.assertEqual(
            response.status_code, status.HTTP_207_MULTI_STATUS, response.content
        )
        successful, failed = self.outcomes(response)
        self.assertEqual(
            sorted(entry["file_name"] for entry in successful), ["a.png", "c.png"]
        )
        self.assertEqual(failed, [{"file_name": "blank.pdf", "error": EMPTY_PDF_TEXT}])
        self.assertEqual(charges(self.teacher), ([1100, 3300], []))

    def test_an_all_empty_request_answers_like_an_all_too_large_one(self):
        empty = self.post([_empty("blank.pdf"), _empty("blank2.pdf")])
        with patch("AutoGrader.uploads.MAX_UPLOAD_SIZE_BYTES", 16):
            large = self.post([_too_large("big.pdf"), _too_large("big2.pdf")])

        self.assertEqual(empty.status_code, large.status_code, empty.content)
        self.assertEqual(_shape(empty.json()), _shape(large.json()))
        self.assertIn(EMPTY_PDF_TEXT, empty.content.decode())
        self.assertEqual(charges(self.teacher), ([], []))


class TheAsyncTeacherUploadIsNeverHalfQueued(BatchFixture, TestCase):
    """assignment-upload-async (assignments/views.py:1134)."""

    def setUp(self):
        self.build(students=1)
        self.dispatched = []

        def record(task_callable, processing_task, *args, **kwargs):
            self.dispatched.append(processing_task.file_name)
            return SimpleNamespace(id=str(uuid.uuid4()))

        target = patch("assignments.views.launch_processing_task", side_effect=record)
        target.start()
        self.addCleanup(target.stop)

    def post(self, files):
        return self.api.post(
            reverse("assignment-upload-async"),
            {"course": str(self.course.id), "assignments": files},
            format="multipart",
        )

    def refused(self, name):
        return BackgroundProcessingTask.objects.get(file_name=name)

    def test_one_empty_file_fails_as_its_item_and_the_rest_are_queued(self):
        # B1b-2 (NOTES §2.3 B2): run the on_commit launch, as production does.
        with self.captureOnCommitCallbacks(execute=True):
            response = self.post(
                [
                    SimpleUploadedFile("a.png", png_bytes(), content_type="image/png"),
                    _empty("blank.pdf"),
                    SimpleUploadedFile("c.png", png_bytes(), content_type="image/png"),
                ]
            )

        self.assertEqual(response.status_code, 202, response.content[:400])
        self.assertEqual(self.dispatched, ["a.png", "c.png"])
        blank = self.refused("blank.pdf")
        self.assertEqual(blank.status, BackgroundTaskStatus.FAILURE)
        self.assertEqual(blank.reason_code, "FILE_EMPTY")
        self.assertEqual(blank.item_index, 2)
        self.assertIsNone(blank.celery_task_id)

    def test_an_all_empty_request_answers_like_an_all_too_large_one(self):
        empty = self.post([_empty("blank.pdf"), _empty("blank2.pdf")])
        with patch("AutoGrader.uploads.MAX_UPLOAD_SIZE_BYTES", 16):
            large = self.post([_too_large("big.pdf"), _too_large("big2.pdf")])

        self.assertEqual(empty.status_code, large.status_code, empty.content)
        self.assertEqual(_shape(empty.json()), _shape(large.json()))
        self.assertEqual(self.dispatched, [])
        for blank, big in (("blank.pdf", "big.pdf"), ("blank2.pdf", "big2.pdf")):
            self.assertEqual(self.refused(blank).status, self.refused(big).status)
            self.assertEqual(self.refused(blank).reason_code, "FILE_EMPTY")
            self.assertEqual(self.refused(big).reason_code, "FILE_TOO_LARGE")
