"""Tests covering the fix for scanned-PDF upload overload: prepare_ai_content
(file rasterization/compression) must run inside the Celery task, using a
raw-bytes payload, rather than synchronously in the view before dispatch."""

from unittest.mock import patch

from django.test import TestCase

from assignments.services import AssignmentProcessingService
from assignments.tasks import upload_assignment_async
from classrooms.models import Course, Session
from users.models import CustomUser, UserTypes


class UploadAssignmentAsyncPreparesContentInTaskTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="assignment-upload-pipeline-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Upload",
            last_name="Teacher",
        )
        self.session = Session.objects.create(
            name="Assignment Upload Session", teacher=self.teacher
        )
        self.course = Course.objects.create(
            name="Assignment Upload Course",
            teacher=self.teacher,
            session=self.session,
        )
        self.file_payload = {
            "name": "scan.pdf",
            "content_type": "application/pdf",
            "content_b64": "ZmFrZS1wZGYtYnl0ZXM=",
        }

    @patch("assignments.tasks.AssignmentProcessingService.extract_assignment_data")
    @patch("assignments.tasks.AssignmentProcessingService.prepare_ai_content")
    @patch("assignments.tasks.AssignmentProcessingService.rebuild_uploaded_file")
    def test_task_rebuilds_file_and_prepares_content_before_extraction(
        self, mock_rebuild, mock_prepare, mock_extract
    ):
        fake_uploaded_file = object()
        fake_content = [{"type": "text", "text": "prompt"}]
        mock_rebuild.return_value = fake_uploaded_file
        mock_prepare.return_value = fake_content
        mock_extract.side_effect = Exception("stop before serializer/save")

        upload_assignment_async.apply(
            kwargs={
                "user_id": str(self.teacher.id),
                "course_id": str(self.course.id),
                "topic_id": None,
                "session_id": None,
                "file_payload": self.file_payload,
                "prompt_text": "Analyze the assignment",
                "file_name": "scan.pdf",
            }
        )

        mock_rebuild.assert_called_once_with(self.file_payload)
        mock_prepare.assert_called_once_with(
            fake_uploaded_file, "Analyze the assignment"
        )
        args, _ = mock_extract.call_args
        self.assertEqual(args[1], fake_content)

    def test_build_and_rebuild_upload_payload_round_trip(self):
        class FakeUploadedFile:
            name = "scan.pdf"
            content_type = "application/pdf"

            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

            def seek(self, pos):
                pass

        original_bytes = b"%PDF-1.4 fake bytes"
        payload = AssignmentProcessingService.build_async_upload_payload(
            FakeUploadedFile(original_bytes)
        )
        rebuilt = AssignmentProcessingService.rebuild_uploaded_file(payload)

        self.assertEqual(rebuilt.name, "scan.pdf")
        self.assertEqual(rebuilt.content_type, "application/pdf")
        self.assertEqual(rebuilt.read(), original_bytes)
