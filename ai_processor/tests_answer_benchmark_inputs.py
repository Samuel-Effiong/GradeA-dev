"""Invalid input to answer extraction is refused before it can do harm.

Found by probing the real pipeline rather than assumed:

* Unreadable and unsupported uploads were already refused by the
  rasterizer. Pinned here so that stays true.

* `_split_into_chunks` with a NEGATIVE size returned [] - every page
  silently dropped - and with 0 raised an opaque range() error. No caller
  passes either today; the splitter now rejects both, so a future
  misconfiguration fails loudly instead of extracting nothing.

* An empty or missing submission still made a billed provider call and
  returned an empty answer list as a success. It is now refused before
  any call. A typed, text-only submission is not empty and still works.
"""

import io
import json
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from pdf2image.exceptions import PDFPageCountError
from PIL import Image
from rest_framework.exceptions import ParseError

from ai_processor import services
from ai_processor.benchmark.answers.provider import _Response
from assignments.services import AssignmentProcessingService


def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (200, 120), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def prepare(data, content_type, name):
    return AssignmentProcessingService.prepare_ai_content(
        SimpleUploadedFile(name, data, content_type=content_type), "prompt"
    )


class UnreadableUploadTest(SimpleTestCase):
    def test_unreadable_or_unsupported_uploads_are_refused(self):
        cases = {
            "zero-byte pdf": (b"", "application/pdf", "empty.pdf"),
            "corrupt pdf": (
                b"%PDF-1.4\nnot a real pdf body\n%%EOF",
                "application/pdf",
                "corrupt.pdf",
            ),
            "image bytes named .pdf": (png_bytes(), "application/pdf", "liar.pdf"),
            "plain text": (b"hello world", "text/plain", "notes.txt"),
            "no extension": (b"%PDF-1.4 x", "application/octet-stream", "upload"),
        }
        for label, (data, content_type, name) in cases.items():
            with self.subTest(upload=label):
                # The two types the rasterizer raises today: its own ParseError
                # for anything it can classify, pdf2image's for bytes that
                # only pretend to be a PDF.
                with self.assertRaises((ParseError, PDFPageCountError)):
                    prepare(data, content_type, name)

    def test_a_real_image_is_one_page(self):
        content = prepare(png_bytes(), "image/png", "scan.png")
        self.assertEqual([block["type"] for block in content].count("image_url"), 1)


class ChunkSizeContractTest(SimpleTestCase):
    def setUp(self):
        self.split = services.AIProcessor.__new__(
            services.AIProcessor
        )._split_into_chunks

    def test_sizes_below_one_are_rejected(self):
        for size in (0, -1, -3):
            with self.subTest(size=size):
                with self.assertRaises(ValueError):
                    self.split([1, 2, 3], size)

    def test_non_integer_sizes_are_rejected(self):
        for size in (None, "3", 3.0, True):
            with self.subTest(size=size):
                with self.assertRaises(ValueError):
                    self.split([1, 2, 3], size)  # type: ignore[arg-type]

    def test_a_size_larger_than_the_input_is_one_chunk(self):
        self.assertEqual(self.split(list(range(1, 8)), 100), [list(range(1, 8))])

    def test_every_item_lands_in_exactly_one_chunk_in_order(self):
        for count in range(0, 13):
            for size in range(1, 7):
                items = list(range(1, count + 1))
                with self.subTest(items=count, size=size):
                    chunks = self.split(items, size)
                    self.assertEqual([i for chunk in chunks for i in chunk], items)
                    self.assertTrue(all(len(chunk) == size for chunk in chunks[:-1]))
                    self.assertTrue(all(1 <= len(chunk) <= size for chunk in chunks))


class EmptySubmissionTest(SimpleTestCase):
    def _extract(self, content):
        calls = []

        def provider(**kwargs):
            calls.append(kwargs)
            return _Response(
                json.dumps(
                    {
                        "student_name": "",
                        "student_name_raw": None,
                        "student_id": "",
                        "answers": [],
                        "extraction_confidence": 0,
                        "feedback": "",
                    }
                )
            )

        processor = services.AIProcessor.__new__(services.AIProcessor)
        with patch.object(processor, "execute_graded_task", provider):
            try:
                processor.extract_answer_with_retry(None, content, "[]", max_retries=3)
            except Exception as exc:  # noqa: B902 - asserted by the caller
                return exc, len(calls)
        return None, len(calls)

    def test_no_content_is_refused_before_any_billed_call(self):
        for content in (None, [], ""):
            with self.subTest(content=content):
                error, calls = self._extract(content)
                self.assertIsInstance(error, ValueError)
                self.assertEqual(calls, 0)

    def test_a_typed_text_only_submission_still_extracts(self):
        error, calls = self._extract([{"type": "text", "text": "1. Reykjavik"}])
        self.assertIsNone(error)
        self.assertEqual(calls, 1)
