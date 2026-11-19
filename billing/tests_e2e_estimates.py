"""E2E F1, option C (owner 2026-11-19): one realistic, chunk-aware estimate.

The stress run found batches the door let in stopped mid-batch for credits
the teacher had: the door counted the file alone plus a flat 20,000, the task
counted the whole call plus the same 20,000 and grew the item's hold to it,
and real calls cost about 0.4x of either. A 40-page PDF went the other way
(door 64,241, actual 266k-340k), and a grading batch held 115,403 per paper
against about 9k real.

Pinned here:
  * each call's estimate is its input plus a per-task_type allowance kept in
    one place (ai_processor.call_estimates), 20,000 only for task types with
    no data;
  * the door's number for a file is the same function's number for the calls
    the task makes for it: equal for a one-call file, every chunk counted for
    a long PDF, so the held amount never needs to grow for the estimate gap;
  * a grading batch holds per paper the same shared estimate (chunk-aware),
    not the cold-start factor stacked on the flat allowance.
Charging is unchanged: holds and estimates only.
"""

import importlib
import math
from typing import Any
from unittest.mock import patch

import fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from ai_processor.services import AIProcessor
from ai_processor.tests_real_chunked_extraction import make_teacher_with_credits
from assignments.models import Assignment
from assignments.services import AssignmentProcessingService
from assignments.upload_door import parked_files
from assignments.upload_prompts import BATCH_UPLOAD_PROMPT
from billing.testing.holds import ai_response, funded_wallet, holds, make_item
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import BackgroundTaskType, StudentSubmission
from users.models import CustomUser, UserTypes

# Any: called with task_type, which the red step's signature lacks.
REAL_ESTIMATE: Any = AIProcessor.estimate_messages_cost
STRESS_40_PAGE_DOOR = 64_241  # the old door's number for the 40-page PDF


def call_estimates():
    return importlib.import_module("ai_processor.call_estimates")


def billing_estimates():
    return importlib.import_module("billing.estimates")


def pdf_upload(pages, name="paper.pdf"):
    document = fitz.open()
    for index in range(pages):
        page = document.new_page()  # A4-ish portrait, as a scanned script
        page.insert_text((60, 90), f"Page {index + 1}: answer text", fontsize=13)
    data = document.tobytes()
    document.close()
    return SimpleUploadedFile(name, data, content_type="application/pdf")


class Provider(Exception):
    """Raised where the provider would be reached: the estimate is taken."""


def spy_gate():
    """Patch the shared estimate to record what the REAL gate asks, and stop
    the call where the provider would be reached."""
    seen = []

    def spy(self, *args, **kwargs):
        value = REAL_ESTIMATE(self, *args, **kwargs)
        seen.append(value)
        return value

    estimate_patch = patch.object(
        AIProcessor, "estimate_messages_cost", autospec=True, side_effect=spy
    )
    provider_patch = patch.object(
        AIProcessor, "_AIProcessor__ai_model", side_effect=Provider("stop")
    )
    return seen, estimate_patch, provider_patch


def fake_calls(reply):
    """execute_graded_task replaced by one that records the estimate the gate
    takes (the shared function, with the call's task_type) and answers
    `reply`, so a chunked run goes through every chunk."""
    seen = []

    def fake(self, *args, **kwargs):
        seen.append(
            (
                kwargs.get("task_type"),
                REAL_ESTIMATE(
                    self,
                    kwargs.get("user_prompt"),
                    kwargs.get("system_prompt"),
                    kwargs.get("messages"),
                    task_type=kwargs.get("task_type"),
                ),
            )
        )
        response = ai_response(1)
        response.choices[0].message.content = reply
        return response

    return seen, patch.object(
        AIProcessor, "execute_graded_task", autospec=True, side_effect=fake
    )


class Fixture(TestCase):
    def setUp(self):
        self.teacher = make_teacher_with_credits("f1c-teacher@example.com")
        session = Session.objects.create(name="F1C", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Estimates", teacher=self.teacher, session=session
        )
        self.assignment = Assignment.objects.create(
            title="Estimate Paper",
            course=self.course,
            total_points=15,
            questions=[
                {
                    "question_number": n,
                    "question_text": f"Explain idea {n} in a sentence.",
                    "question_type": "SHORT-ANSWER",
                    "question_image": "",
                    "points": 5,
                    "options": [],
                    "rubric": [],
                    "model_answer": f"Idea {n} is explained.",
                }
                for n in (1, 2, 3)
            ],
        )
        for n in range(3):
            student = CustomUser.objects.create_user(
                email=f"f1c-student{n}@example.com",
                password="password123",  # pragma: allowlist secret
                user_type=UserTypes.STUDENT,
                first_name=f"Student{n}",
                last_name="Roster",
            )
            StudentCourse.objects.create(
                student=student,
                course=self.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )

    def door(self, upload, assignment):
        found = {}
        parked = parked_files(
            self.teacher, assignment, [upload], BATCH_UPLOAD_PROMPT, estimates_out=found
        )
        self.assertEqual(parked, {})
        upload.seek(0)
        return found[0]

    def content(self, upload):
        content = AssignmentProcessingService.prepare_ai_content(
            upload, BATCH_UPLOAD_PROMPT
        )
        upload.seek(0)
        return content


class EachCallHasItsOwnAllowance(TestCase):
    def test_the_allowance_follows_the_task_type(self):
        module = call_estimates()
        processor = AIProcessor()
        base = REAL_ESTIMATE(processor, "Grade this.", None, None, task_type="other")
        for task_type in (
            "extract_answer",
            "extract_assignment",
            "grade_assignment",
            "formatted_grade",
            "student_summary",
        ):
            with self.subTest(task_type=task_type):
                headroom = module.CALL_HEADROOM[task_type]
                self.assertLess(headroom, module.FALLBACK_CALL_HEADROOM)
                self.assertEqual(
                    REAL_ESTIMATE(
                        processor, "Grade this.", None, None, task_type=task_type
                    ),
                    base - module.FALLBACK_CALL_HEADROOM + headroom,
                )

    def test_a_task_type_with_no_data_keeps_the_conservative_20000(self):
        module = call_estimates()
        processor = AIProcessor()
        self.assertEqual(module.FALLBACK_CALL_HEADROOM, 20_000)
        self.assertEqual(
            REAL_ESTIMATE(processor, "x", None, None),
            REAL_ESTIMATE(
                processor, "x", None, None, task_type="weekly_course_summary"
            ),
        )
        self.assertEqual(module.call_headroom("generate_assignment"), 20_000)
        self.assertEqual(module.call_headroom(None), 20_000)


class DoorAndTaskAgree(Fixture):
    def test_an_answer_file_the_door_counts_is_what_the_gate_asks(self):
        from students.services import upload_answers_engine

        upload = pdf_upload(1)
        door = self.door(upload, self.assignment)
        seen, estimate_patch, provider_patch = spy_gate()
        with estimate_patch, provider_patch, self.assertRaises(Exception):
            upload_answers_engine(
                assignment=self.assignment,
                content=self.content(upload),
                request_user=self.teacher,
                is_proxy_upload=True,
            )
        self.assertTrue(seen)
        self.assertEqual(set(seen), {door})

    def test_an_assignment_file_the_door_counts_is_what_the_gate_asks(self):
        upload = pdf_upload(1, name="assignment.pdf")
        door = self.door(upload, None)
        seen, estimate_patch, provider_patch = spy_gate()
        with estimate_patch, provider_patch, self.assertRaises(Exception):
            AssignmentProcessingService.extract_assignment_data(
                self.teacher, self.content(upload), course=self.course, upload=True
            )
        self.assertTrue(seen)
        self.assertEqual(set(seen), {door})

    @override_settings(BILLING_HOLDS_ENABLED=True)
    def test_a_file_held_at_the_door_needs_nothing_more_at_the_gate(self):
        upload = pdf_upload(1)
        door = self.door(upload, self.assignment)
        wallet = funded_wallet("f1c-exact@example.com", door)
        job = holds().open_door_job(
            billed_user=wallet.user, requested_by=wallet.user, total_items=1
        )
        item = make_item(job, task_type=BackgroundTaskType.BATCH_ANSWER_UPLOAD)
        self.assertTrue(holds().hold_door_item(job, item, door))
        # The gate's number for the same file (pinned equal above): it fits
        # the door's hold with no shortfall, so nothing more is asked of a
        # wallet that held exactly the door's number.
        self.assertEqual(holds().ensure_call_fits(job, door, item=item), 0)


class LongPdfsCountEveryChunk(Fixture):
    PAGES = 40

    def test_a_long_answer_pdf(self):
        upload = pdf_upload(self.PAGES)
        door = self.door(upload, self.assignment)
        seen, calls = fake_calls(
            '{"student_name": "", "student_id": "", "answers": []}'
        )
        with calls:
            try:
                AIProcessor().extract_answer_image(
                    self.teacher,
                    self.content(upload),
                    "context",
                    assignment_model=self.assignment,
                )
            except Exception:  # noqa: BLE001 - only the chunk calls matter
                pass
        chunks = math.ceil(self.PAGES / 3)
        chunk_calls = [
            value for task_type, value in seen if task_type == "extract_answer"
        ]
        self.assertGreaterEqual(len(chunk_calls), chunks)
        task = sum(chunk_calls[:chunks])
        self.assertGreaterEqual(door, task)
        self.assertLess(door, 2 * task)
        self.assertGreater(door, 3 * STRESS_40_PAGE_DOOR)

    def test_a_long_assignment_pdf(self):
        upload = pdf_upload(self.PAGES, name="assignment.pdf")
        door = self.door(upload, None)
        seen, calls = fake_calls('{"title": "T", "questions": []}')
        with calls:
            try:
                AIProcessor().extract_assignment_with_retry(
                    self.teacher, self.content(upload), max_retries=1, upload=True
                )
            except Exception:  # noqa: BLE001 - only the chunk calls matter
                pass
        chunks = math.ceil(self.PAGES / 3)
        chunk_calls = [
            value for task_type, value in seen if task_type == "extract_assignment"
        ]
        self.assertEqual(len(chunk_calls), chunks)
        task = sum(chunk_calls)
        self.assertGreaterEqual(door, task)
        self.assertLess(door, 2 * task)
        self.assertGreater(door, 3 * STRESS_40_PAGE_DOOR)


class GradingHoldsTheSharedEstimate(Fixture):
    def representative(self):
        student = CustomUser.objects.get(email="f1c-student0@example.com")
        return StudentSubmission.objects.create(
            student=student,
            assignment=self.assignment,
            answers=[
                {"question_number": n, "answer": f"Idea {n} is this."}
                for n in (1, 2, 3)
            ],
        )

    def grade_call(self, assignment, representative):
        """The shared estimate of one grading call carrying the whole paper."""
        return billing_estimates().paper_call_estimate(assignment, representative)

    def test_a_cold_start_paper_holds_the_shared_estimate_with_its_p90_margin(self):
        paper = self.representative()
        _, held = billing_estimates().paper_hold_estimate(
            self.assignment, 3, teacher=self.teacher, representative=paper
        )
        per_paper = self.grade_call(self.assignment, paper)
        self.assertEqual(
            held, math.ceil(per_paper * billing_estimates().ROUGH_P90_FACTOR)
        )
        # One call with the grading allowance, not the flat 20,000.
        self.assertLess(per_paper, 20_000)

    def test_history_cheaper_than_the_call_still_holds_what_the_task_asks(self):
        paper = self.representative()
        cheap = [
            {
                "assignment_id": str(self.assignment.pk),
                "billed_user_id": str(self.teacher.pk),
                "questions": 3,
                "has_images": False,
                "cost": 1_000,
            }
            for _ in range(40)
        ]
        with patch("billing.estimates.paper_costs", return_value=cheap):
            result, held = billing_estimates().paper_hold_estimate(
                self.assignment, 3, teacher=self.teacher, representative=paper
            )
        self.assertEqual(result["source"], billing_estimates().SOURCE_ASSIGNMENT)
        per_paper = self.grade_call(self.assignment, paper)
        self.assertEqual(
            held, math.ceil(per_paper * billing_estimates().ROUGH_P90_FACTOR)
        )

    def test_a_paper_graded_in_batches_counts_each_call(self):
        questions = [
            {
                "question_number": n,
                "question_text": f"Q{n}",
                "question_type": "SHORT-ANSWER",
                "points": 1,
            }
            for n in range(1, 26)
        ]
        long = Assignment.objects.create(
            title="Long Paper", course=self.course, total_points=25, questions=questions
        )
        short = Assignment.objects.create(
            title="Short Paper",
            course=self.course,
            total_points=10,
            questions=questions[:10],
        )
        module = call_estimates()
        per_call = REAL_ESTIMATE(
            AIProcessor(),
            None,
            [{"type": "text", "text": str(self.grading_prompt())}],
            [],
            task_type="grade_assignment",
        )
        one_call = billing_estimates().paper_call_estimate(short, None)
        three_batches_and_summary = billing_estimates().paper_call_estimate(long, None)
        self.assertGreaterEqual(three_batches_and_summary, one_call + 3 * per_call)
        self.assertGreater(per_call, module.CALL_HEADROOM["grade_assignment"])

    @staticmethod
    def grading_prompt():
        from ai_processor.services import GRADING_ASSIGNMENT_PROMPT

        return GRADING_ASSIGNMENT_PROMPT
