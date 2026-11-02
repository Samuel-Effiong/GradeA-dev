"""H-274 (e.1a): which door answers a 0-byte upload, and with which code.

SM ruling (A), 2026-11-02: the student answer route keeps the S6 contract
(``assignments.tests_file_reason_codes.StudentUploadAnswersWithItsOwnCode``:
an unsupported type is refused first with 415 FILE_TYPE_UNSUPPORTED, then a
0-byte answer with 422 SUBMISSION_EMPTY). Every other door keeps (e.1)'s
FILE_EMPTY 400. The caller opts out of the empty check; validate_upload_size
knows no route names.
"""

from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.tests_file_reason_codes import envelope, tenancy_world
from audit.enums import ReasonCode
from AutoGrader.uploads import PayloadEmpty, validate_upload_size


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


class ATeacherDoorStillAnswersFileEmpty(APITestCase):
    """The guard that keeps (e.1): a non-student door is not opted out."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.world = tenancy_world()

    @patch(
        "assignments.views.AssignmentProcessingService.extract_assignment_data",
        side_effect=AssertionError("no AI work may run for a refused file"),
    )
    def test_a_zero_byte_assignment_file_is_file_empty(self, _extract):
        self.client.force_authenticate(user=self.world.teacher_a)

        response = self.client.post(
            reverse("assignment-upload"),
            {"course": str(self.world.course_a.id), "assignments": _empty("quiz.pdf")},
            format="multipart",
        )

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        body = envelope(response)
        self.assertEqual(body["reason_code"], ReasonCode.FILE_EMPTY.value, body)
        self.assertEqual(body["error"], "'quiz.pdf' is empty. Choose another file.")
