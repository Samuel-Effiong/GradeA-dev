"""
A PDF upload is accepted only if its bytes really are a PDF.

The upload endpoints choose the PDF branch from the client's Content-Type,
which is a claim, not a fact; PDFService.extract is where that claim meets
the bytes. A PNG or JPEG labelled application/pdf used to open fine in
PyMuPDF (it sniffs the content and ignores the filetype hint), pass the
page-count checks, and then crash pdftoppm with an uncaught
PDFPageCountError - a 500 for the student or teacher instead of a message
telling them what was wrong with their file.
"""

import io
from unittest.mock import patch

import fitz
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.urls import reverse
from pdf2image.exceptions import (
    PDFInfoNotInstalledError,
    PDFPageCountError,
    PDFPopplerTimeoutError,
    PDFSyntaxError,
)
from PIL import Image
from rest_framework import status
from rest_framework.exceptions import ParseError
from rest_framework.test import APITestCase

from ai_processor.services import PDFService
from assignments.models import Assignment
from assignments.services import AssignmentProcessingService
from assignments.tests_security import TenancyAttackFixture


def image_bytes(image_format):
    buffer = io.BytesIO()
    Image.new("RGB", (200, 120), "white").save(buffer, format=image_format)
    return buffer.getvalue()


def real_pdf_bytes(pages=1):
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def labelled_pdf(data, name="upload.pdf"):
    return SimpleUploadedFile(name, data, content_type="application/pdf")


def image_parts(content):
    return [block for block in content if block["type"] == "image_url"]


class ImageLabelledAsPdfTest(SimpleTestCase):
    def test_an_image_labelled_pdf_is_refused_before_pdftoppm_runs(self):
        for image_format in ("PNG", "JPEG"):
            with self.subTest(image_format=image_format), patch(
                "ai_processor.services.convert_from_path",
                side_effect=AssertionError("pdftoppm must never see a non-PDF"),
            ):
                with self.assertRaises(ValueError) as caught:
                    PDFService(labelled_pdf(image_bytes(image_format))).extract()

                self.assertIn("not a PDF", str(caught.exception))

    def test_the_refusal_reaches_the_caller_as_a_parse_error(self):
        """Unmocked: the real PyMuPDF and the real pdftoppm binary."""
        for image_format in ("PNG", "JPEG"):
            with self.subTest(image_format=image_format):
                with self.assertRaises(ParseError) as caught:
                    AssignmentProcessingService.prepare_ai_content(
                        labelled_pdf(image_bytes(image_format), "photo.pdf"), "p"
                    )

                self.assertIn("not a PDF", str(caught.exception.detail))

    def test_a_real_pdf_is_still_rasterized_page_by_page(self):
        """The check must scope, not refuse every PDF."""
        content = AssignmentProcessingService.prepare_ai_content(
            labelled_pdf(real_pdf_bytes(pages=2)), "p"
        )

        self.assertEqual(len(image_parts(content)), 2)


class RasterizerFaultOwnershipTest(SimpleTestCase):
    """
    A file poppler cannot read is the uploader's problem and must be a 400.
    A poppler that is missing or too slow is ours, and must stay a server
    error - relabelling it as a bad file would tell every user their upload
    is corrupt while the real fault goes unnoticed.
    """

    def test_a_pdf_poppler_cannot_read_is_a_client_error(self):
        for fault in (PDFPageCountError("bad xref"), PDFSyntaxError("bad syntax")):
            with self.subTest(fault=type(fault).__name__), patch(
                "ai_processor.services.convert_from_path", side_effect=fault
            ):
                with self.assertRaises(ParseError) as caught:
                    AssignmentProcessingService.prepare_ai_content(
                        labelled_pdf(real_pdf_bytes()), "p"
                    )

                # The original error stays on the exception chain, which is
                # what the background-task error classifier walks to still
                # recognise an unreadable file.
                self.assertIs(caught.exception.__cause__.__cause__, fault)

    def test_server_side_poppler_faults_are_not_blamed_on_the_file(self):
        for fault in (PDFInfoNotInstalledError("no pdfinfo"), PDFPopplerTimeoutError()):
            with self.subTest(fault=type(fault).__name__), patch(
                "ai_processor.services.convert_from_path", side_effect=fault
            ):
                with self.assertRaises(type(fault)):
                    AssignmentProcessingService.prepare_ai_content(
                        labelled_pdf(real_pdf_bytes()), "p"
                    )


class ImageLabelledAsPdfOverHttpTest(TenancyAttackFixture, APITestCase):
    """The same file through both real upload endpoints, end to end."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.build_world()

    @patch(
        "assignments.views.AssignmentProcessingService.extract_assignment_data",
        side_effect=AssertionError("no AI work may run for a refused file"),
    )
    def test_teacher_assignment_upload_is_a_400_not_a_500(self, _extract):
        before = Assignment.objects.count()
        self.as_user(self.teacher_a)

        response = self.client.post(
            reverse("assignment-upload"),
            {
                "course": str(self.course_a.id),
                "assignments": labelled_pdf(image_bytes("PNG"), "photo.pdf"),
            },
            format="multipart",
        )

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content[:300]
        )
        self.assertIn("not a PDF", response.content.decode())
        self.assertEqual(Assignment.objects.count(), before)

    @patch(
        "students.views.upload_answers_engine",
        side_effect=AssertionError("no AI work may run for a refused file"),
    )
    def test_student_answer_upload_is_a_400_not_a_500(self, _engine):
        self.as_user(self.student_a)

        with patch(
            "users.permissions.HasCreditBalance.has_permission", return_value=True
        ):
            response = self.client.post(
                reverse(
                    "student-submission-upload-answers",
                    kwargs={"assignment_id": str(self.assignment_a.id)},
                ),
                {"answer": labelled_pdf(image_bytes("JPEG"), "answers.pdf")},
                format="multipart",
            )

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content[:300]
        )
        self.assertIn("not a PDF", response.content.decode())
