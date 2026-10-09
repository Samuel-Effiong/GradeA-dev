"""
FR-A-07 S7a: the per-item model and results contract (08a §4.3, §5).

A batch is a set of items, and each item answers for itself:

* BackgroundProcessingTask carries reason_code, retry_count, item_index (the
  file's 1-based position in the upload) and trace_id (the dispatching
  request's server trace, so an item's `reference` resolves to its audit
  events).
* mark_processing_task_failure stores the failure's reason code; an
  unclassified fault stores none and reads as error_class SYSTEM, visibly.
* session-results keeps every key it had and adds, per item, the coded
  fields (reason_code, error_class, message, remediation, retryable,
  retry_count, reference, item_id, item_index, submission_id,
  replaced_existing) and, per session, failure_codes, stopped_at_item and
  resumable.
* A file that is too large fails as ITS item (413 FILE_TOO_LARGE) and the
  rest of the batch still runs, in batch-upload and in upload-async; the
  batch is never refused or half-queued (the two problems S6b left to S7a).
* The legacy grading paths (a scheduled batch, auto-grade on the due date)
  create tracked items, so their sessions answer in the same shape.
* 30/12: thirty files, twelve failing over six codes, answer with twelve
  coded failures and eighteen successes.
"""

import io
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import patch

import fitz
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

import assignments.tasks as assignment_tasks
from assignments.models import Assignment, AssignmentStatus
from audit.enums import ReasonCode
from AutoGrader.reason_codes import REASON_CODES
from billing.errors import InsufficientCreditsError
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students import task_tracking
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
    StudentSubmission,
)
from users.models import CustomUser, UserTypes

ZERO_PAGE_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
    b"trailer<</Root 1 0 R>>\n%%EOF\n"
)
GARBAGE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00garbage" * 64


def png_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (160, 90), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def pdf_bytes():
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def run_inline(task_callable, processing_task, *args, **kwargs):
    """launch_processing_task, but running the real task in-process."""
    kwargs["processing_task_id"] = str(processing_task.id)
    celery_id = str(uuid.uuid4())
    task_tracking.attach_celery_task(processing_task.id, celery_id)
    task_callable.apply(args=args, kwargs=kwargs, task_id=celery_id)
    return SimpleNamespace(id=celery_id)


class BatchFixture:
    #: Supplied by the TestCase this mixin is combined with.
    addCleanup: Callable[..., Any]
    assertEqual: Callable[..., Any]

    def build(self, students=3, door_open=True):
        cache.clear()
        self.teacher = self.user("teacher", UserTypes.TEACHER, "Tess", "Teacher")
        session = Session.objects.create(name="S7a", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Maths", teacher=self.teacher, session=session
        )
        self.assignment = Assignment.objects.create(
            title="Homework",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[
                {
                    "question_number": 1,
                    "question_text": "Q1?",
                    "points": 10,
                    # A marking guide, or S6d refuses grading (RUBRIC_MISSING).
                    "model_answer": "4",
                }
            ],
        )
        self.students = [
            self.enrol(f"Pupil{i:02d}", "Enrolled") for i in range(students)
        ]
        self.pending = self.enrol("Pending", "Pupil", EnrollmentStatusType.PENDING)
        self.api = APIClient()
        self.api.force_authenticate(user=self.teacher)
        for target in (
            patch(
                "users.permissions.HasCreditBalance.has_permission", return_value=True
            ),
            patch("students.views.launch_processing_task", side_effect=run_inline),
            patch("assignments.views.launch_processing_task", side_effect=run_inline),
        ):
            target.start()
            self.addCleanup(target.stop)
        if door_open:
            # H-180's door asks the teacher's wallet before the session
            # exists; most of this file is about what the items answer, and
            # the door has its own tests (students.tests_upload_credit_door).
            for name in (
                "students.views.upload_refusal_if_unaffordable",
                "assignments.views.upload_refusal_if_unaffordable",
            ):
                door = patch(name, return_value=None)
                door.start()
                self.addCleanup(door.stop)
        else:
            # The door stays live, with a wallet that clears it, so the file
            # that is too large is met by the door first and by its own
            # item after (merge-down b16).
            wallet = CreditWallet.objects.create(user=self.teacher)
            CreditBucket.objects.create(
                wallet=wallet,
                bucket_type=CreditBucketType.MONTHLY,
                total_credits=1_000_000,
                used_credits=0,
                expires_at=timezone.now() + timedelta(days=30),
            )

    def user(self, key, user_type, first, last):
        return CustomUser.objects.create_user(
            email=f"s7a-{key}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=user_type,
            first_name=first,
            last_name=last,
        )

    def enrol(self, first, last, status=EnrollmentStatusType.ENROLLED):
        student = self.user(f"{first}-{last}".lower(), UserTypes.STUDENT, first, last)
        StudentCourse.objects.create(
            student=student, course=self.course, enrollment_status=status
        )
        return student

    def extraction(self, names):
        """Fake the answer extraction: the i-th file that reaches it reads
        names[i] (the batch runs inline, in upload order)."""
        queue = list(names)

        def extract(*args, **kwargs):
            return {
                "student_name": queue.pop(0),
                "answers": [{"question_number": 1, "answer_html": "<p>4</p>"}],
            }

        return patch(
            "students.services.ai_processor.extract_answer_with_retry",
            side_effect=extract,
        )

    def batch_upload(self, files):
        return self.api.post(
            reverse(
                "student-submission-batch-upload",
                kwargs={"assignment_id": str(self.assignment.id)},
            ),
            {"answers": files},
            format="multipart",
        )

    def session_results(self, session_id):
        response = self.api.get(
            reverse("task-session-results", kwargs={"session_id": str(session_id)})
        )
        self.assertEqual(response.status_code, 200, response.content[:400])
        return response.json()["data"]


class TheItemModel(BatchFixture, TestCase):
    def setUp(self):
        self.build()

    def test_an_item_records_its_position_and_the_requests_trace(self):
        with patch(
            "students.task_tracking.resolve_trace_id",
            return_value=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        ):
            item = task_tracking.create_processing_task(
                requested_by=self.teacher,
                task_type=BackgroundTaskType.BATCH_ANSWER_UPLOAD,
                assignment=self.assignment,
                item_index=7,
            )
        item.refresh_from_db()
        self.assertEqual(item.item_index, 7)
        self.assertEqual(str(item.trace_id), "11111111-1111-1111-1111-111111111111")
        self.assertEqual(item.retry_count, 0)
        self.assertEqual(item.reason_code, "")

    def test_a_failure_stores_its_reason_code(self):
        from assignments.exceptions import FileUnreadableError

        cases = [
            (FileUnreadableError(params={"file_name": "a.png"}), "FILE_UNREADABLE"),
            (InsufficientCreditsError("balance 3, needs 9"), "INSUFFICIENT_CREDITS"),
            (RuntimeError("an unclassified fault"), ""),
        ]
        for error, code in cases:
            with self.subTest(code=code or "none"):
                item = task_tracking.create_processing_task(
                    requested_by=self.teacher,
                    task_type=BackgroundTaskType.BATCH_ANSWER_UPLOAD,
                )
                task_tracking.mark_processing_task_failure(item.id, error)
                item.refresh_from_db()
                self.assertEqual(item.status, BackgroundTaskStatus.FAILURE)
                self.assertEqual(item.reason_code, code)


class TheSessionResultsShape(BatchFixture, TestCase):
    def setUp(self):
        self.build(students=2)

    def test_each_item_answers_for_itself_and_the_old_keys_stay(self):
        files = [
            SimpleUploadedFile("a.png", png_bytes(), content_type="image/png"),
            SimpleUploadedFile("b.png", GARBAGE_PNG, content_type="image/png"),
        ]
        with self.extraction(["Pupil00 Enrolled"]):
            response = self.batch_upload(files)
        self.assertEqual(response.status_code, 202, response.content[:400])
        data = self.session_results(response.json()["data"]["session_id"])

        [ok] = data["success_list"]
        [bad] = data["failure_list"]
        # Backward compatible: every key the list had, with its old meaning.
        for entry in (ok, bad):
            for key in ("status", "file_name", "task_id", "error", "context"):
                self.assertIn(key, entry)
        # The success.
        self.assertEqual(ok["item_index"], 1)
        self.assertIsNone(ok["reason_code"])
        self.assertIs(ok["replaced_existing"], False)
        self.assertEqual(
            ok["submission_id"],
            str(StudentSubmission.objects.get(student=self.students[0]).id),
        )
        # The failure: its own code, class, texts and reference.
        item = BackgroundProcessingTask.objects.get(file_name="b.png")
        spec = REASON_CODES[ReasonCode.FILE_UNREADABLE]
        self.assertEqual(bad["item_id"], str(item.id))
        self.assertEqual(bad["item_index"], 2)
        self.assertEqual(bad["reason_code"], "FILE_UNREADABLE")
        self.assertEqual(bad["error_class"], "USER")
        self.assertEqual(bad["message"], bad["error"])
        self.assertIn("b.png", bad["message"])
        self.assertEqual(bad["remediation"], spec.remediation)
        self.assertIs(bad["retryable"], False)
        self.assertEqual(bad["retry_count"], 0)
        assert item.trace_id is not None  # recorded at creation
        self.assertEqual(bad["reference"], item.trace_id.hex)
        self.assertEqual(bad["reference"], response["X-Request-ID"])
        self.assertIsNone(bad["submission_id"])
        # The session.
        self.assertEqual(data["failure_codes"], {"FILE_UNREADABLE": 1})
        self.assertIsNone(data["stopped_at_item"])
        self.assertIs(data["resumable"], False)

    def test_an_unclassified_failure_is_visible_as_system_with_no_code(self):
        files = [SimpleUploadedFile("a.png", png_bytes(), content_type="image/png")]
        with patch(
            "students.services.ai_processor.extract_answer_with_retry",
            side_effect=RuntimeError("an unclassified fault"),
        ), patch.object(assignment_tasks.upload_answers_engine_async, "max_retries", 0):
            response = self.batch_upload(files)
        data = self.session_results(response.json()["data"]["session_id"])

        [bad] = data["failure_list"]
        self.assertIsNone(bad["reason_code"])
        self.assertEqual(bad["error_class"], "SYSTEM")
        self.assertIs(bad["retryable"], False)
        self.assertNotIn("an unclassified fault", bad["message"])
        # Counted under a documented sentinel, never a reason code.
        self.assertEqual(data["failure_codes"], {"UNCLASSIFIED": 1})

    def test_an_overwrite_is_lifted_into_the_item_result(self):
        with self.extraction(["Pupil00 Enrolled", "Pupil00 Enrolled"]):
            self.batch_upload(
                [SimpleUploadedFile("a.png", png_bytes(), content_type="image/png")]
            )
            response = self.batch_upload(
                [SimpleUploadedFile("b.png", png_bytes(), content_type="image/png")]
            )
        data = self.session_results(response.json()["data"]["session_id"])
        [ok] = data["success_list"]
        self.assertIs(ok["replaced_existing"], True)


class EveryListIsInUploadOrder(BatchFixture, TestCase):
    """v2's N1 (SM ruling): each per-item list is sorted by item_index,
    whatever order the items were created in or finished in."""

    def setUp(self):
        self.build(students=1)

    def test_items_finishing_out_of_order_are_listed_by_item_index(self):
        session = BatchUploadSession.objects.create(
            teacher=self.teacher,
            assignment=self.assignment,
            task_type=BatchUploadType.SUBMISSION,
            total_files=6,
        )
        # Created in the order 3, 1, 2 (so newest-first would read 2, 1, 3),
        # and finished in the order 2, 3, 1.
        items = {}
        for index in (3, 1, 2):
            for outcome in ("fail", "ok"):
                items[(index, outcome)] = task_tracking.create_processing_task(
                    requested_by=self.teacher,
                    task_type=BackgroundTaskType.BATCH_ANSWER_UPLOAD,
                    batch_session=session,
                    assignment=self.assignment,
                    file_name=f"{outcome}{index}.png",
                    item_index=index if outcome == "fail" else index + 3,
                )
        for index in (2, 3, 1):
            task_tracking.mark_processing_task_failure(
                items[(index, "fail")].id, RuntimeError("x")
            )
            task_tracking.mark_processing_task_success(items[(index, "ok")].id)

        data = self.session_results(session.id)

        self.assertEqual([e["item_index"] for e in data["failure_list"]], [1, 2, 3])
        self.assertEqual([e["item_index"] for e in data["success_list"]], [4, 5, 6])


class ATooLargeFileFailsAsItsItem(BatchFixture, TestCase):
    """S6b's two batch 413 problems, fixed per item."""

    def setUp(self):
        # The upload door (H-180) is live here: an oversized file must not
        # make it refuse the batch, and its own item still answers 413.
        self.build(students=2, door_open=False)

    def test_batch_upload_runs_the_rest_of_the_batch(self):
        files = [
            SimpleUploadedFile("a.png", png_bytes(), content_type="image/png"),
            SimpleUploadedFile("big.pdf", b"%PDF-1.4" + b"0" * 4096, "application/pdf"),
            SimpleUploadedFile("c.png", png_bytes(), content_type="image/png"),
        ]
        with patch("AutoGrader.uploads.MAX_UPLOAD_SIZE_BYTES", 2048), self.extraction(
            ["Pupil00 Enrolled", "Pupil01 Enrolled"]
        ):
            response = self.batch_upload(files)

        self.assertEqual(response.status_code, 202, response.content[:400])
        tasks = response.json()["data"]["tasks"]
        self.assertEqual([t["file_name"] for t in tasks], ["a.png", "big.pdf", "c.png"])
        self.assertIsNone(tasks[1]["task_id"])
        data = self.session_results(response.json()["data"]["session_id"])
        self.assertEqual((data["success_count"], data["failure_count"]), (2, 1))
        [bad] = data["failure_list"]
        self.assertEqual(bad["file_name"], "big.pdf")
        self.assertEqual(bad["item_index"], 2)
        self.assertEqual(bad["reason_code"], "FILE_TOO_LARGE")
        self.assertIn("big.pdf", bad["message"])
        self.assertEqual(StudentSubmission.objects.count(), 2)

    def test_upload_async_is_never_half_queued(self):
        files = [
            SimpleUploadedFile("a.png", png_bytes(), content_type="image/png"),
            SimpleUploadedFile("big.pdf", b"%PDF-1.4" + b"0" * 4096, "application/pdf"),
            SimpleUploadedFile("c.png", png_bytes(), content_type="image/png"),
        ]
        dispatched = []

        def record(task_callable, processing_task, *args, **kwargs):
            dispatched.append(processing_task.file_name)
            return SimpleNamespace(id=str(uuid.uuid4()))

        with patch("AutoGrader.uploads.MAX_UPLOAD_SIZE_BYTES", 2048), patch(
            "assignments.views.launch_processing_task", side_effect=record
        ):
            response = self.api.post(
                reverse("assignment-upload-async"),
                {"course": str(self.course.id), "assignments": files},
                format="multipart",
            )

        self.assertEqual(response.status_code, 202, response.content[:400])
        self.assertEqual(dispatched, ["a.png", "c.png"])
        big = BackgroundProcessingTask.objects.get(file_name="big.pdf")
        self.assertEqual(big.status, BackgroundTaskStatus.FAILURE)
        self.assertEqual(big.reason_code, "FILE_TOO_LARGE")
        self.assertEqual(big.item_index, 2)
        self.assertIsNone(big.celery_task_id)


class TheLegacyGradingPathsAreTracked(BatchFixture, TestCase):
    """A scheduled batch and auto-grade used to fan out untracked tasks, so
    their sessions answered from the legacy results list, with no codes."""

    def setUp(self):
        self.build(students=2)
        self.submissions = [
            StudentSubmission.objects.create(
                assignment=self.assignment,
                student=student,
                answers=[{"question_number": 1, "answer_html": "<p>4</p>"}],
            )
            for student in self.students
        ]

    def assertTrackedItems(self, session_id):
        items = BackgroundProcessingTask.objects.filter(batch_session_id=session_id)
        self.assertEqual(items.count(), 2)
        self.assertEqual(sorted(items.values_list("item_index", flat=True)), [1, 2])
        self.assertTrue(
            all(
                i.task_type == BackgroundTaskType.BATCH_SUBMISSION_GRADING
                for i in items
            )
        )
        self.assertEqual(
            {i.submission_id for i in items}, {s.id for s in self.submissions}
        )

    def test_a_scheduled_batch_creates_tracked_items(self):
        with patch("assignments.tasks.launch_processing_task") as launch:
            launch.return_value = SimpleNamespace(id=str(uuid.uuid4()))
            assignment_tasks.grade_batch_async.apply(
                args=(str(self.teacher.id), str(self.assignment.id))
            ).get()
        session = BatchUploadSession.objects.get(task_type=BatchUploadType.GRADE)
        self.assertTrackedItems(session.id)
        self.assertEqual(launch.call_count, 2)

    def test_auto_grade_creates_tracked_items(self):
        Assignment.objects.filter(pk=self.assignment.pk).update(
            auto_grade_on_due_date=True
        )
        with patch("assignments.tasks.launch_processing_task") as launch:
            launch.return_value = SimpleNamespace(id=str(uuid.uuid4()))
            assignment_tasks.auto_grade_due_assignment.apply(
                args=(str(self.assignment.id),)
            ).get()
        session = BatchUploadSession.objects.get(task_type=BatchUploadType.GRADE)
        self.assertTrackedItems(session.id)
        self.assertEqual(launch.call_count, 2)


class ThirtyFilesTwelveFailures(BatchFixture, TestCase):
    """08a §4.6's 30/12: twelve engineered failures over six codes."""

    def setUp(self):
        self.build(students=18)

    def test_every_failure_is_listed_with_its_own_code(self):
        # (file, expected code or None, the name the extractor reads)
        plan: list[tuple[SimpleUploadedFile, str | None, str | None]] = []
        for i in range(18):
            plan.append(
                (
                    SimpleUploadedFile(f"ok{i:02d}.png", png_bytes(), "image/png"),
                    None,
                    f"Pupil{i:02d} Enrolled",
                )
            )
        bad = [
            (
                SimpleUploadedFile(
                    "big1.pdf", b"%PDF-1.4" + b"0" * 9000, "application/pdf"
                ),
                "FILE_TOO_LARGE",
                None,
            ),
            (
                SimpleUploadedFile(
                    "big2.pdf", b"%PDF-1.4" + b"0" * 9000, "application/pdf"
                ),
                "FILE_TOO_LARGE",
                None,
            ),
            (
                SimpleUploadedFile("junk1.png", GARBAGE_PNG, "image/png"),
                "FILE_UNREADABLE",
                None,
            ),
            (
                SimpleUploadedFile("junk2.png", GARBAGE_PNG, "image/png"),
                "FILE_UNREADABLE",
                None,
            ),
            (
                SimpleUploadedFile("notes1.txt", b"hello", "text/plain"),
                "FILE_TYPE_UNSUPPORTED",
                None,
            ),
            (
                SimpleUploadedFile("notes2.txt", b"hello", "text/plain"),
                "FILE_TYPE_UNSUPPORTED",
                None,
            ),
            (
                SimpleUploadedFile("blank1.pdf", ZERO_PAGE_PDF, "application/pdf"),
                "SUBMISSION_EMPTY",
                None,
            ),
            (
                SimpleUploadedFile("blank2.pdf", ZERO_PAGE_PDF, "application/pdf"),
                "SUBMISSION_EMPTY",
                None,
            ),
            (
                SimpleUploadedFile("anon1.png", png_bytes(), "image/png"),
                "MISSING_STUDENT_NAME",
                "",
            ),
            (
                SimpleUploadedFile("anon2.png", png_bytes(), "image/png"),
                "MISSING_STUDENT_NAME",
                "Nobody Known",
            ),
            (
                SimpleUploadedFile("pend1.png", png_bytes(), "image/png"),
                "STUDENT_NOT_ON_ROSTER",
                "Pending Pupil",
            ),
            (
                SimpleUploadedFile("pend2.png", png_bytes(), "image/png"),
                "STUDENT_NOT_ON_ROSTER",
                "Pending Pupil",
            ),
        ]
        # Spread the failures through the batch, not bunched at one end.
        for offset, entry in enumerate(bad):
            plan.insert(offset * 2 + 1, entry)
        self.assertEqual(len(plan), 30)
        expected = {f.name: code for f, code, _ in plan}
        names = [name for _, code, name in plan if name is not None]

        with patch("AutoGrader.uploads.MAX_UPLOAD_SIZE_BYTES", 8192), self.extraction(
            names
        ):
            response = self.batch_upload([f for f, _, _ in plan])
        self.assertEqual(response.status_code, 202, response.content[:400])
        data = self.session_results(response.json()["data"]["session_id"])

        self.assertEqual(data["failure_count"], 12)
        self.assertEqual(len(data["failure_list"]), 12)
        self.assertEqual(data["success_count"], 18)
        for entry in data["failure_list"]:
            with self.subTest(file=entry["file_name"]):
                self.assertIsNotNone(entry["reason_code"])
                self.assertEqual(entry["reason_code"], expected[entry["file_name"]])
                self.assertIn(entry["file_name"], entry["message"])
        self.assertEqual(
            data["failure_codes"],
            {
                "FILE_TOO_LARGE": 2,
                "FILE_UNREADABLE": 2,
                "FILE_TYPE_UNSUPPORTED": 2,
                "SUBMISSION_EMPTY": 2,
                "MISSING_STUDENT_NAME": 2,
                "STUDENT_NOT_ON_ROSTER": 2,
            },
        )
        indexes = sorted(
            e["item_index"] for e in data["failure_list"] + data["success_list"]
        )
        self.assertEqual(indexes, list(range(1, 31)))
