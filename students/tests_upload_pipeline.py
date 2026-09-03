"""Tests covering the fix for scanned-PDF upload overload: prepare_ai_content
(file rasterization/compression) must run inside the Celery task, using a
raw-bytes payload, rather than synchronously in the view before dispatch."""

from unittest.mock import patch
from uuid import uuid4

from django.test import TestCase

from assignments.models import Assignment, AssignmentStatus
from assignments.services import AssignmentProcessingService
from assignments.tasks import upload_answers_engine_async
from classrooms.models import Course, Session
from users.models import CustomUser, UserTypes


class UploadAnswersEngineAsyncPreparesContentInTaskTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="upload-pipeline-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Upload",
            last_name="Teacher",
        )
        self.student = CustomUser.objects.create_user(
            email="upload-pipeline-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Upload",
            last_name="Student",
        )
        self.session = Session.objects.create(
            name="Upload Session", teacher=self.teacher
        )
        self.course = Course.objects.create(
            name="Upload Course",
            teacher=self.teacher,
            session=self.session,
        )
        self.assignment = Assignment.objects.create(
            title="Scanned Homework",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions="1. What is 2+2?",
        )
        self.file_payload = {
            "name": "scan.pdf",
            "content_type": "application/pdf",
            "content_b64": "ZmFrZS1wZGYtYnl0ZXM=",
        }

    @patch("assignments.tasks.upload_answers_engine")
    @patch("assignments.tasks.AssignmentProcessingService.prepare_ai_content")
    @patch("assignments.tasks.AssignmentProcessingService.rebuild_uploaded_file")
    def test_task_rebuilds_file_and_prepares_content_before_engine_call(
        self, mock_rebuild, mock_prepare, mock_engine
    ):
        fake_uploaded_file = object()
        fake_content = [{"type": "text", "text": "prompt"}]
        mock_rebuild.return_value = fake_uploaded_file
        mock_prepare.return_value = fake_content

        class FakeSubmission:
            id = uuid4()

        mock_engine.return_value = FakeSubmission()

        upload_answers_engine_async.apply(
            args=(
                str(self.assignment.id),
                self.file_payload,
                "Analyze the submission",
                str(self.student.id),
            )
        )

        # The task, not the view, is responsible for turning the raw-bytes
        # payload into AI-ready content.
        mock_rebuild.assert_called_once_with(self.file_payload)
        mock_prepare.assert_called_once_with(
            fake_uploaded_file, "Analyze the submission"
        )

        # The prepared content (not the raw payload) is what reaches the
        # existing submission-creation engine, unchanged.
        _, kwargs = mock_engine.call_args
        self.assertEqual(kwargs["content"], fake_content)

    @patch("assignments.tasks.upload_answers_engine")
    def test_task_end_to_end_compresses_real_pdf_payload(self, mock_engine):
        """No mocking of prepare_ai_content itself: verify the real
        rebuild -> prepare_ai_content path runs inside the task and produces
        compressed JPEG image_url content, using a tiny real PDF."""
        import fitz

        pdf_document = fitz.open()
        page = pdf_document.new_page()
        page.insert_text((72, 72), "Sample scanned answer")
        pdf_bytes = pdf_document.tobytes()
        pdf_document.close()

        class FakeUploadedFile:
            name = "scan.pdf"
            content_type = "application/pdf"

            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

            def seek(self, pos):
                pass

        payload = AssignmentProcessingService.build_async_upload_payload(
            FakeUploadedFile(pdf_bytes)
        )

        class FakeSubmission:
            id = uuid4()

        mock_engine.return_value = FakeSubmission()

        upload_answers_engine_async.apply(
            args=(
                str(self.assignment.id),
                payload,
                "Analyze the submission",
                str(self.student.id),
            )
        )

        _, kwargs = mock_engine.call_args
        content = kwargs["content"]
        image_parts = [part for part in content if part["type"] == "image_url"]
        self.assertEqual(len(image_parts), 1)
        self.assertTrue(
            image_parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        )
