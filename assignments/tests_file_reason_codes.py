"""
FR-A-06 S6b: the file codes #3-#6 (08a §1, §4.6).

Each refused upload answers with its OWN reason code and the status F7
fixed for it, never the shared ParseError 400:

  FILE_UNREADABLE        422  damaged, truncated, or not what its type claims
  FILE_TYPE_UNSUPPORTED  415  a type we don't accept, with the accepted list
  FILE_TOO_LARGE         413  bytes, pages or pixels, with actual and limit
  SUBMISSION_EMPTY       422  an empty file: zero bytes or zero pages

QA-ERR-03: no library text reaches a body. The raw PyMuPDF/poppler/Pillow
message used to be embedded ("Could not read this PDF: {e}"); it now stays
on the exception's __cause__, server-side.
"""

import io
from unittest.mock import patch

import fitz
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from pdf2image.exceptions import PDFSyntaxError
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

import assignments.tasks as upload_tasks
from ai_processor.tools import ImageCompressionError
from assignments import tests_upload_task_retry_policy as retry_policy
from assignments.exceptions import InvalidUploadFileError
from assignments.models import Assignment, AssignmentStatus
from assignments.services import AssignmentProcessingService
from assignments.tests_security import TenancyAttackFixture
from audit.enums import ReasonCode
from AutoGrader.error_messages import classify_infra_error
from AutoGrader.reason_codes import ACCEPTED_FILE_TYPES
from AutoGrader.uploads import PayloadTooLarge
from classrooms.models import Course, Session
from students.models import BackgroundProcessingTask, BackgroundTaskType
from users.models import CustomUser, UserTypes

#: Stands in for a third-party library's own error text. It must never
#: reach a response body or a task's stored error (QA-ERR-03).
SENTINEL = "SENTINEL-library-internals-0x7f3a"


def image_bytes(image_format="PNG", size=(200, 120)):
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, format=image_format)
    return buffer.getvalue()


def pdf_bytes(pages=1):
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


#: A well-formed PDF whose page tree is empty. PyMuPDF opens it with
#: page_count == 0 (it refuses to WRITE such a file, so it is hand-built).
ZERO_PAGE_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)

#: A PNG signature followed by bytes no decoder can use.
GARBAGE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00garbage" * 64


def upload(name, data, content_type):
    return SimpleUploadedFile(name, data, content_type=content_type)


def envelope(response):
    """The coded body, where the renderer puts it (F7)."""
    return response.json()["error"]["field_errors"]


def tenancy_world():
    """TenancyAttackFixture's two schools, used by composition (a mixin's
    `client` annotation clashes with the test case's under mypy)."""
    world = TenancyAttackFixture()
    world.build_world()
    return world


class StudentUploadAnswersWithItsOwnCode(APITestCase):
    """The synchronous student upload: each file condition, its own code."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.world = tenancy_world()
        self.client.force_authenticate(user=self.world.student_a)
        for target in (
            patch(
                "users.permissions.HasCreditBalance.has_permission",
                return_value=True,
            ),
            patch(
                "students.views.upload_answers_engine",
                side_effect=AssertionError("no AI work may run for a refused file"),
            ),
        ):
            target.start()
            self.addCleanup(target.stop)

    def post(self, uploaded):
        return self.client.post(
            reverse(
                "student-submission-upload-answers",
                kwargs={"assignment_id": str(self.world.assignment_a.id)},
            ),
            {"answer": uploaded},
            format="multipart",
        )

    def assertCoded(self, response, code, http_status, file_name):
        self.assertEqual(response.status_code, http_status, response.content[:400])
        body = envelope(response)
        self.assertEqual(body["reason_code"], code.value, body)
        self.assertEqual(body["error_class"], "USER")
        self.assertIs(body["retryable"], False)
        self.assertEqual(body["params"]["file_name"], file_name)
        self.assertIn(file_name, body["error"])
        self.assertEqual(response.json()["message"], body["error"])
        self.assertEqual(body["reference"], response["X-Request-ID"])
        return body

    def assertNoInternals(self, response):
        text = response.content.decode()
        for leak in (SENTINEL, "Traceback", "Error:", "fitz", "PIL", "poppler"):
            self.assertNotIn(leak, text)

    # -- #3 FILE_UNREADABLE --------------------------------------------------

    def test_an_image_whose_bytes_are_garbage_is_unreadable(self):
        response = self.post(upload("scan.png", GARBAGE_PNG, "image/png"))

        body = self.assertCoded(
            response,
            ReasonCode.FILE_UNREADABLE,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "scan.png",
        )
        self.assertNotEqual(body["reason_code"], "FILE_TYPE_UNSUPPORTED")
        self.assertNoInternals(response)

    def test_a_truncated_pdf_is_unreadable(self):
        data = pdf_bytes(pages=3)
        response = self.post(
            upload("answers.pdf", data[: len(data) // 3], "application/pdf")
        )

        self.assertCoded(
            response,
            ReasonCode.FILE_UNREADABLE,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "answers.pdf",
        )
        self.assertNoInternals(response)

    def test_a_pdf_the_library_cannot_open_never_shows_the_library_text(self):
        data = pdf_bytes()  # built before fitz.open is patched
        with patch(
            "ai_processor.services.fitz.open", side_effect=RuntimeError(SENTINEL)
        ):
            response = self.post(upload("answers.pdf", data, "application/pdf"))

        self.assertCoded(
            response,
            ReasonCode.FILE_UNREADABLE,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "answers.pdf",
        )
        self.assertNoInternals(response)

    def test_a_pdf_the_rasterizer_rejects_never_shows_the_rasterizer_text(self):
        with patch(
            "ai_processor.services.convert_from_path",
            side_effect=PDFSyntaxError(SENTINEL),
        ):
            response = self.post(upload("answers.pdf", pdf_bytes(), "application/pdf"))

        self.assertCoded(
            response,
            ReasonCode.FILE_UNREADABLE,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "answers.pdf",
        )
        self.assertNoInternals(response)

    def test_a_photo_labelled_as_a_pdf_is_unreadable_as_a_pdf(self):
        response = self.post(
            upload("photo.pdf", image_bytes("JPEG"), "application/pdf")
        )

        self.assertCoded(
            response,
            ReasonCode.FILE_UNREADABLE,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "photo.pdf",
        )

    # -- #4 FILE_TYPE_UNSUPPORTED ----------------------------------------------

    def test_a_text_file_is_an_unsupported_type_listing_every_accepted_type(self):
        response = self.post(upload("notes.txt", b"my answers", "text/plain"))

        body = self.assertCoded(
            response,
            ReasonCode.FILE_TYPE_UNSUPPORTED,
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "notes.txt",
        )
        self.assertIn(ACCEPTED_FILE_TYPES, body["error"])
        for accepted in ("PDF", "JPEG", "PNG", "GIF", "WebP"):
            self.assertIn(accepted, body["error"])
        self.assertEqual(body["params"]["detected_type"], "TXT")
        self.assertIn("TXT", body["error"])

    def test_a_type_without_an_extension_names_its_declared_type(self):
        response = self.post(upload("answers", b"PK\x03\x04", "application/zip"))

        body = self.assertCoded(
            response,
            ReasonCode.FILE_TYPE_UNSUPPORTED,
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "answers",
        )
        self.assertEqual(body["params"]["detected_type"], "application/zip")

    # -- #5 FILE_TOO_LARGE -------------------------------------------------------

    def test_too_many_bytes_is_too_large_with_actual_and_limit(self):
        with patch("AutoGrader.uploads.MAX_UPLOAD_SIZE_BYTES", 1024 * 1024):
            response = self.post(
                upload(
                    "big.pdf", b"%PDF-1.4" + b"0" * (3 * 1024 * 1024), "application/pdf"
                )
            )

        body = self.assertCoded(
            response,
            ReasonCode.FILE_TOO_LARGE,
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "big.pdf",
        )
        self.assertEqual(body["params"]["dimension"], "bytes")
        self.assertEqual(body["params"]["actual"], "3.0 MB")
        self.assertEqual(body["params"]["limit"], "1 MB")
        self.assertIn("3.0 MB", body["error"])
        self.assertIn("1 MB", body["error"])

    def test_too_many_pages_is_too_large_with_actual_and_limit(self):
        with patch("ai_processor.services.PDFService.MAX_PAGE_COUNT", 2):
            response = self.post(
                upload("long.pdf", pdf_bytes(pages=3), "application/pdf")
            )

        body = self.assertCoded(
            response,
            ReasonCode.FILE_TOO_LARGE,
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "long.pdf",
        )
        self.assertEqual(body["params"]["dimension"], "pages")
        self.assertEqual(body["params"]["actual"], "3 pages")
        self.assertEqual(body["params"]["limit"], "2 pages")
        self.assertIn("3 pages", body["error"])
        self.assertIn("2 pages", body["error"])

    def test_too_many_pixels_is_too_large_with_actual_and_limit(self):
        with patch("assignments.services.MAX_IMAGE_PIXELS", 20_000):
            response = self.post(
                upload("huge.png", image_bytes("PNG", (200, 120)), "image/png")
            )

        body = self.assertCoded(
            response,
            ReasonCode.FILE_TOO_LARGE,
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "huge.png",
        )
        self.assertEqual(body["params"]["dimension"], "pixels")
        self.assertEqual(body["params"]["actual"], "200x120 px")
        self.assertIn("200x120 px", body["error"])
        self.assertIn(body["params"]["limit"], body["error"])

    def test_an_image_too_large_even_compressed_is_too_large(self):
        refusal = ImageCompressionError(
            "no size fits",
            smallest_bytes=6 * 1024 * 1024 + 512 * 1024,
            cap_bytes=5 * 1024 * 1024,
        )
        for target, name, data, content_type in (
            ("assignments.services", "scan.png", image_bytes("PNG"), "image/png"),
            ("ai_processor.services", "scan.pdf", pdf_bytes(), "application/pdf"),
        ):
            with self.subTest(path=name), patch(
                f"{target}.compress_image_for_upload", side_effect=refusal
            ):
                response = self.post(upload(name, data, content_type))

                body = self.assertCoded(
                    response,
                    ReasonCode.FILE_TOO_LARGE,
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    name,
                )
                self.assertEqual(body["params"]["dimension"], "bytes")
                self.assertEqual(
                    body["params"]["actual"], "6.5 MB even after compression"
                )
                self.assertEqual(body["params"]["limit"], "5 MB")
                self.assertNoInternals(response)

    # -- #6 SUBMISSION_EMPTY (empty FILES only: §6.1's final ruling) -------------

    def test_a_pdf_with_no_pages_is_empty(self):
        response = self.post(upload("blank.pdf", ZERO_PAGE_PDF, "application/pdf"))

        self.assertCoded(
            response,
            ReasonCode.SUBMISSION_EMPTY,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "blank.pdf",
        )

    def test_a_zero_byte_file_is_empty(self):
        response = self.post(upload("blank.png", b"", "image/png"))

        self.assertCoded(
            response,
            ReasonCode.SUBMISSION_EMPTY,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "blank.png",
        )

    def test_an_empty_file_of_an_unsupported_type_is_the_type_first(self):
        response = self.post(upload("blank.txt", b"", "text/plain"))

        self.assertCoded(
            response,
            ReasonCode.FILE_TYPE_UNSUPPORTED,
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "blank.txt",
        )


def coded_file_errors():
    """The S6b exception types (imported late, so this module loads on a
    tree without them and each test fails on its own assertion)."""
    from assignments import exceptions

    return exceptions


class TheCodesAreDistinctAndStayRefusals(SimpleTestCase):
    """The exception types behind the codes: each distinct, each still an
    upload refusal (never retried, shown to the user)."""

    def prepare(self, uploaded):
        return AssignmentProcessingService.prepare_ai_content(uploaded, "p")

    def test_each_condition_raises_its_own_coded_type(self):
        errors = coded_file_errors()
        FileUnreadableError = errors.FileUnreadableError
        FileTypeUnsupportedError = errors.FileTypeUnsupportedError
        SubmissionEmptyError = errors.SubmissionEmptyError
        cases = [
            (upload("a.png", GARBAGE_PNG, "image/png"), FileUnreadableError),
            (upload("a.txt", b"x", "text/plain"), FileTypeUnsupportedError),
            (upload("a.pdf", ZERO_PAGE_PDF, "application/pdf"), SubmissionEmptyError),
        ]
        for uploaded, expected in cases:
            with self.subTest(expected=expected.__name__):
                with self.assertRaises(expected) as caught:
                    self.prepare(uploaded)
                self.assertIsInstance(caught.exception, InvalidUploadFileError)
                self.assertEqual(caught.exception.reason_code, expected.reason_code)

    def test_every_too_large_is_a_payload_too_large_answered_413(self):
        FileTooLargeError = coded_file_errors().FileTooLargeError
        with patch("ai_processor.services.PDFService.MAX_PAGE_COUNT", 1):
            with self.assertRaises(FileTooLargeError) as caught:
                self.prepare(upload("a.pdf", pdf_bytes(pages=2), "application/pdf"))
        self.assertIsInstance(caught.exception, PayloadTooLarge)
        self.assertIsInstance(caught.exception, InvalidUploadFileError)
        self.assertEqual(caught.exception.status_code, 413)

    def test_the_library_text_stays_on_the_cause(self):
        FileUnreadableError = coded_file_errors().FileUnreadableError
        data = pdf_bytes()  # built before fitz.open is patched
        with patch(
            "ai_processor.services.fitz.open", side_effect=RuntimeError(SENTINEL)
        ):
            with self.assertRaises(FileUnreadableError) as caught:
                self.prepare(upload("a.pdf", data, "application/pdf"))

        self.assertNotIn(SENTINEL, str(caught.exception))
        self.assertNotIn(SENTINEL, str(caught.exception.detail))
        chain, cause = [], caught.exception.__cause__
        while cause is not None:
            chain.append(str(cause))
            cause = cause.__cause__
        self.assertTrue(any(SENTINEL in text for text in chain), chain)


class TheInfraClassifierNoLongerConflatesUnreadableWithUnsupported(SimpleTestCase):
    """error_messages.py's file category said "…or in an unsupported format".
    A library that fails to parse a file says the file is unreadable; an
    unsupported type is its own coded refusal now (#3 vs #4)."""

    def test_a_parser_failure_reads_as_unreadable_only(self):
        message = classify_infra_error(PDFSyntaxError("x"))

        self.assertIn("couldn't read", message)
        self.assertNotIn("unsupported", message.lower())
        self.assertNotIn("format", message.lower())


class TheBackgroundUploadKeepsTheCode(TestCase):
    """The async upload wrapped every ParseError in a fresh, uncoded
    InvalidUploadFileError. The coded refusal must reach the item as itself,
    so S7a can store its reason_code."""

    def setUp(self):
        teacher = CustomUser.objects.create_user(
            email="s6b-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.student = CustomUser.objects.create_user(
            email="s6b-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        course = Course.objects.create(
            name="S6b course",
            teacher=teacher,
            session=Session.objects.create(name="S6b session", teacher=teacher),
        )
        self.assignment = Assignment.objects.create(
            title="S6b homework",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )

    def test_the_item_fails_with_the_coded_refusal_itself(self):
        tracked = BackgroundProcessingTask.objects.create(
            requested_by=self.student,
            task_type=BackgroundTaskType.ANSWER_EXTRACTION,
            assignment=self.assignment,
        )

        with patch(
            "assignments.tasks.upload_answers_engine",
            side_effect=AssertionError("no extraction may run for a refused file"),
        ), patch(
            "assignments.tasks.mark_processing_task_failure",
            wraps=upload_tasks.mark_processing_task_failure,
        ) as mark_failure:
            result = upload_tasks.upload_answers_engine_async.apply(
                args=(
                    str(self.assignment.id),
                    retry_policy.payload("scan.png", GARBAGE_PNG, "image/png"),
                    "prompt",
                    str(self.student.id),
                ),
                kwargs={"processing_task_id": str(tracked.id)},
            ).get()

        self.assertEqual(result["status"], "FAILURE")
        [call] = mark_failure.call_args_list
        refused = call.args[1]
        self.assertIsInstance(refused, coded_file_errors().FileUnreadableError)
        self.assertEqual(refused.reason_code, ReasonCode.FILE_UNREADABLE)
        tracked.refresh_from_db()
        self.assertEqual(tracked.error, str(refused))
        self.assertIn("scan.png", str(refused))


class AnAssignmentFileWithNoPagesIsUnreadableNotAnEmptySubmission(APITestCase):
    """SUBMISSION_EMPTY's text is about student answers. A teacher's empty
    ASSIGNMENT file is refused as unreadable instead."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.world = tenancy_world()

    @patch(
        "assignments.views.AssignmentProcessingService.extract_assignment_data",
        side_effect=AssertionError("no AI work may run for a refused file"),
    )
    def test_the_teacher_sees_the_unreadable_message(self, _extract):
        self.client.force_authenticate(user=self.world.teacher_a)

        response = self.client.post(
            reverse("assignment-upload"),
            {
                "course": str(self.world.course_a.id),
                "assignments": upload("quiz.pdf", ZERO_PAGE_PDF, "application/pdf"),
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        text = response.content.decode()
        self.assertIn("couldn't read quiz.pdf", text)
        self.assertNotIn("student answers", text)


class ThePayloadTooLargeOfTheBatchIsCoded(TestCase):
    """validate_upload_size is the byte cap for every upload route."""

    def test_the_cap_is_read_at_call_time(self):
        from AutoGrader.uploads import validate_upload_size

        with patch("AutoGrader.uploads.MAX_UPLOAD_SIZE_BYTES", 10):
            with self.assertRaises(PayloadTooLarge) as caught:
                validate_upload_size(upload("x.pdf", b"0" * 11, "application/pdf"))
        self.assertEqual(caught.exception.reason_code, ReasonCode.FILE_TOO_LARGE)
        self.assertEqual(caught.exception.status_code, 413)
