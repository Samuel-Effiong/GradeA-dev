"""Upload quote, slice 1 (plan rev 3, section 4; BE-B-03/B-04): the door's
estimate split into two pure steps.

`page_tokens_of(content)` is the image tokens of each page, from the door's
own prepared content; `estimate_from_page_tokens(task_type, page_tokens,
texts, assignment)` is the chunking, headroom and text tokens. The door is
their composition and keeps its exact number: a frozen copy of the function as
it was before the split is compared on every fixture, for both kinds.
"""

import importlib
import io
from typing import Any

import fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from ai_processor.tests_real_chunked_extraction import make_teacher_with_credits
from assignments.models import Assignment
from assignments.services import AssignmentProcessingService
from assignments.upload_prompts import BATCH_UPLOAD_PROMPT
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from users.models import CustomUser, UserTypes


def call_estimates() -> Any:
    return importlib.import_module("ai_processor.call_estimates")


def legacy_upload_item_estimate(task_type, content, *, assignment=None):
    """The door's estimate as it was before the split (frozen copy)."""
    ce = call_estimates()
    from ai_processor.services import (
        ANSWERS_EXTRACTION_PAGES_PER_CHUNK,
        ASSIGNMENT_EXTRACTION_PROMPT_FROM_UPLOADS,
        ASSIGNMENT_UPLOAD_PAGES_PER_CHUNK,
        ai_processor,
        assignment_chunk_note,
    )

    images = [
        item
        for item in content
        if isinstance(item, dict) and item.get("type") == "image_url"
    ]
    texts = [item for item in content if item not in images]

    if task_type == ce.EXTRACT_ANSWER:
        from students.services import answer_assignment_context

        context = answer_assignment_context(assignment)
        if len(images) < ANSWERS_EXTRACTION_PAGES_PER_CHUNK:
            chunks = [images]
        else:
            chunks = ce._split(images, ANSWERS_EXTRACTION_PAGES_PER_CHUNK)
        total = 0
        for index, chunk in enumerate(chunks):
            messages = ai_processor.answer_extraction_messages(
                [*texts, *chunk], context, assignment
            )
            total += ai_processor.estimate_messages_cost(
                None, None, messages, task_type=task_type
            )
            if index:
                total += ce.call_headroom(task_type)
        return total

    if len(images) <= ASSIGNMENT_UPLOAD_PAGES_PER_CHUNK:
        calls = [content]
    else:
        # The chunked path sends each chunk with its note in place of the
        # prompt; the note's question number is counted at its widest.
        pages = len(images)
        calls = [
            [
                {
                    "type": "text",
                    "text": assignment_chunk_note(
                        start + 1,
                        start + len(chunk),
                        pages,
                        ce.MANY_QUESTIONS,
                    ),
                },
                *chunk,
            ]
            for start, chunk in zip(
                range(0, pages, ASSIGNMENT_UPLOAD_PAGES_PER_CHUNK),
                ce._split(images, ASSIGNMENT_UPLOAD_PAGES_PER_CHUNK),
                strict=True,
            )
        ]
    return sum(
        ai_processor.estimate_messages_cost(
            call,
            ASSIGNMENT_EXTRACTION_PROMPT_FROM_UPLOADS,
            None,
            task_type=ce.EXTRACT_ASSIGNMENT,
        )
        for call in calls
    )


def pdf_bytes(sizes, *, rotate=0, crop=None):
    document = fitz.open()
    for width, height in sizes:
        page = document.new_page(width=width, height=height)
        page.insert_text((20, 40), "answer text", fontsize=12)
        if rotate:
            page.set_rotation(rotate)
        if crop:
            page.set_cropbox(fitz.Rect(*crop))
    data = document.tobytes()
    document.close()
    return data


def image_bytes(width, height, fmt):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, fmt)
    return buffer.getvalue()


LETTER = (612, 792)
A4 = (595.276, 841.89)


def fixtures():
    """(label, bytes, content type) for the shapes the plan names."""
    pdf = "application/pdf"
    out = []
    for pages in (1, 2, 3, 4, 6, 7, 12):
        out.append((f"letter-{pages}", pdf_bytes([LETTER] * pages), pdf))
        out.append((f"a4-{pages}", pdf_bytes([A4] * pages), pdf))
    out.append(("mixed", pdf_bytes([LETTER, A4, (300, 300), (1000, 400)]), pdf))
    out.append(("rotated", pdf_bytes([LETTER] * 2, rotate=90), pdf))
    out.append(("cropped", pdf_bytes([LETTER], crop=(50, 50, 300, 400)), pdf))
    out.append(("tiny", pdf_bytes([(40, 40)]), pdf))
    out.append(("huge", pdf_bytes([(3000, 4000)]), pdf))
    for shape in ((800, 600), (600, 800), (3000, 1200), (200, 200)):
        out.append(
            (f"jpeg-{shape[0]}x{shape[1]}", image_bytes(*shape, "JPEG"), "image/jpeg")
        )
        out.append(
            (f"png-{shape[0]}x{shape[1]}", image_bytes(*shape, "PNG"), "image/png")
        )
    return out


def content_of(data, content_type):
    upload = SimpleUploadedFile("f", data, content_type=content_type)
    return AssignmentProcessingService.prepare_ai_content(upload, BATCH_UPLOAD_PROMPT)


class Base(TestCase):
    def setUp(self):
        self.teacher = make_teacher_with_credits("quote-s1@example.com")
        session = Session.objects.create(name="Q1", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Quote", teacher=self.teacher, session=session
        )
        self.assignment = Assignment.objects.create(
            title="Quote Paper",
            course=self.course,
            total_points=10,
            questions=[
                {
                    "question_number": n,
                    "question_text": f"Explain idea {n}.",
                    "question_type": "SHORT-ANSWER",
                    "question_image": "",
                    "points": 5,
                    "options": [],
                    "rubric": [],
                    "model_answer": f"Idea {n}.",
                }
                for n in (1, 2)
            ],
        )

    def enroll(self, count):
        for n in range(count):
            student = CustomUser.objects.create_user(
                email=f"quote-s1-{n}-{count}@example.com",
                password="password123",  # pragma: allowlist secret
                user_type=UserTypes.STUDENT,
                first_name=f"Roster{n}",
                last_name="Student",
            )
            StudentCourse.objects.create(
                student=student,
                course=self.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )


class SplitEqualsTheDoorBefore(Base):
    """Test 1 (first half): old == new on every fixture, both kinds."""

    def kinds(self):
        module = call_estimates()
        return (
            (module.EXTRACT_ANSWER, self.assignment),
            (module.EXTRACT_ASSIGNMENT, None),
        )

    def test_composition_equals_the_frozen_function(self):
        module = call_estimates()
        for label, data, content_type in fixtures():
            content = content_of(data, content_type)
            for task_type, assignment in self.kinds():
                with self.subTest(fixture=label, task=task_type):
                    self.assertEqual(
                        module.upload_item_estimate(
                            task_type, content, assignment=assignment
                        ),
                        legacy_upload_item_estimate(
                            task_type, content, assignment=assignment
                        ),
                    )

    def test_two_steps_by_hand_equal_the_door(self):
        module = call_estimates()
        for label, data, content_type in fixtures():
            content = content_of(data, content_type)
            tokens = module.page_tokens_of(content)
            texts = [item for item in content if item.get("type") != "image_url"]
            for task_type, assignment in self.kinds():
                with self.subTest(fixture=label, task=task_type):
                    self.assertEqual(
                        module.estimate_from_page_tokens(
                            task_type, tokens, texts, assignment
                        ),
                        legacy_upload_item_estimate(
                            task_type, content, assignment=assignment
                        ),
                    )

    def test_page_tokens_are_one_int_per_page(self):
        module = call_estimates()
        content = content_of(pdf_bytes([LETTER] * 4), "application/pdf")
        tokens = module.page_tokens_of(content)
        self.assertEqual(len(tokens), 4)
        self.assertTrue(all(isinstance(t, int) and t > 0 for t in tokens))

    def test_the_text_part_moves_with_the_roster_not_the_pages(self):
        """The measure stores page tokens; the text part is counted again
        live, so a roster change between measure and read keeps the figure
        equal to the door's."""
        module = call_estimates()
        content = content_of(pdf_bytes([LETTER] * 2), "application/pdf")
        tokens = module.page_tokens_of(content)
        texts = [item for item in content if item.get("type") != "image_url"]
        before = module.estimate_from_page_tokens(
            module.EXTRACT_ANSWER, tokens, texts, self.assignment
        )
        self.enroll(40)
        after = module.estimate_from_page_tokens(
            module.EXTRACT_ANSWER, tokens, texts, self.assignment
        )
        self.assertGreater(after, before)
        self.assertEqual(
            after,
            legacy_upload_item_estimate(
                module.EXTRACT_ANSWER, content, assignment=self.assignment
            ),
        )
