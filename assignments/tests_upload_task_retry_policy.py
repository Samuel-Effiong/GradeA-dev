"""
Upload tasks: a deterministic bad-file failure is final, never retried, and
never costs credits; the assignment upload task keeps the same per-file
billing guarantees as the synchronous batch view.

Before this, upload_answers_engine_async retried an unreadable or
mislabelled file three more times - the same bytes failing the same way
four times over. The fix turns the file refusal from prepare_ai_content into
InvalidUploadFileError, one of UPLOAD_REFUSALS. A server-side rasterizer
fault (poppler missing or timing out) is not the file's fault and must keep
being retried, and that is pinned here too so the refusal cannot quietly
widen.

The last class runs both tasks on a real Celery worker against the real
Redis broker, with real PostgreSQL, the same way
students/tests_grading_redelivery_live.py does.
"""

import base64
import io
import threading
import time
import uuid
from unittest.mock import patch

import redis
from celery.contrib.testing.worker import start_worker
from celery.signals import task_postrun, worker_shutdown
from django.conf import settings
from django.core.cache import cache
from django.db import connections
from django.test import TestCase, TransactionTestCase
from pdf2image.exceptions import PDFInfoNotInstalledError
from PIL import Image

from ai_processor.services import AIProcessor
from assignments.models import Assignment, AssignmentStatus, AssignmentUploadFingerprint
from assignments.services import AssignmentProcessingService
from assignments.tasks import upload_answers_engine_async, upload_assignment_async
from assignments.tests_upload_batch_billing import (
    GOOD_FILES,
    TOKENS,
    Provider,
    ai_response,
    charges,
    funded_teacher_with_course,
    png,
)
from AutoGrader.celery import app as celery_app
from classrooms.models import Course, Session
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
)
from users.models import CustomUser, UserTypes

WAIT = 45  # seconds; generous for a contended host, never reached when healthy
NOT_A_PDF = "not a PDF"


def payload(name, data, content_type):
    return {
        "name": name,
        "content_type": content_type,
        "content_b64": base64.b64encode(data).decode("utf-8"),
    }


def photo_labelled_as_pdf():
    buffer = io.BytesIO()
    Image.new("RGB", (160, 90), "white").save(buffer, format="JPEG")
    return payload("photo.pdf", buffer.getvalue(), "application/pdf")


def good_assignment_payload(name="a.png"):
    return payload(name, png(GOOD_FILES[name]), "image/png")


def _wait_until(predicate, timeout=WAIT, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


class AnswerUploadTaskRefusesBadFilesTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="retry-policy-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Retry",
            last_name="Teacher",
        )
        self.student = CustomUser.objects.create_user(
            email="retry-policy-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Retry",
            last_name="Student",
        )
        session = Session.objects.create(name="Retry session", teacher=self.teacher)
        course = Course.objects.create(
            name="Retry course", teacher=self.teacher, session=session
        )
        self.assignment = Assignment.objects.create(
            title="Retry homework",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )

    def _tracked(self, requested_by, task_type, **extra):
        return BackgroundProcessingTask.objects.create(
            requested_by=requested_by,
            task_type=task_type,
            assignment=self.assignment,
            **extra,
        )

    def _run(self, file_payload, requested_by, **kwargs):
        return upload_answers_engine_async.apply(
            args=(
                str(self.assignment.id),
                file_payload,
                "prompt",
                str(requested_by.id),
            ),
            kwargs=kwargs,
        )

    @patch(
        "assignments.tasks.upload_answers_engine",
        side_effect=AssertionError("no extraction may run for an unreadable file"),
    )
    def test_an_unreadable_file_is_prepared_once_and_never_retried(self, _engine):
        tracked = self._tracked(self.student, BackgroundTaskType.ANSWER_EXTRACTION)

        with patch.object(
            AssignmentProcessingService,
            "prepare_ai_content",
            wraps=AssignmentProcessingService.prepare_ai_content,
        ) as prepare:
            result = self._run(
                photo_labelled_as_pdf(),
                self.student,
                processing_task_id=str(tracked.id),
            ).get()

        self.assertEqual(result["status"], "FAILURE")
        self.assertIn(NOT_A_PDF, result["message"])
        self.assertEqual(prepare.call_count, 1, "the bad file was retried")
        tracked.refresh_from_db()
        self.assertEqual(tracked.status, BackgroundTaskStatus.FAILURE)
        # The file's own message, not the generic fallback.
        self.assertIn(NOT_A_PDF, tracked.error)

    @patch(
        "assignments.tasks.upload_answers_engine",
        side_effect=AssertionError("no extraction may run for an unreadable file"),
    )
    def test_a_teacher_batch_file_that_is_unreadable_is_recorded_once(self, _engine):
        session = BatchUploadSession.objects.create(
            teacher=self.teacher,
            assignment=self.assignment,
            task_type=BatchUploadType.SUBMISSION,
            total_files=1,
        )
        tracked = self._tracked(
            self.teacher, BackgroundTaskType.BATCH_ANSWER_UPLOAD, batch_session=session
        )

        result = self._run(
            photo_labelled_as_pdf(),
            self.teacher,
            processing_task_id=str(tracked.id),
            session_id=str(session.id),
            file_name="photo.pdf",
        ).get()

        self.assertEqual(result["status"], "FAILURE")
        session.refresh_from_db()
        [entry] = session.results
        self.assertEqual(entry["status"], "FAILED")
        self.assertIn(NOT_A_PDF, entry["error"])

    def test_a_server_side_rasterizer_fault_is_still_retried(self):
        """Poppler missing is the server's fault, not the file's: it has to
        stay retryable, or the refusal has swallowed a real outage."""
        tracked = self._tracked(self.student, BackgroundTaskType.ANSWER_EXTRACTION)

        with patch.object(
            AssignmentProcessingService,
            "prepare_ai_content",
            side_effect=PDFInfoNotInstalledError("pdfinfo not installed"),
        ) as prepare:
            outcome = self._run(
                photo_labelled_as_pdf(),
                self.student,
                processing_task_id=str(tracked.id),
            )

        self.assertTrue(outcome.failed())
        self.assertEqual(
            prepare.call_count, 1 + upload_answers_engine_async.max_retries
        )


class AssignmentUploadTaskBillingTest(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.provider = Provider()
        patcher = patch.object(
            AIProcessor, "_AIProcessor__ai_model", side_effect=self.provider
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.teacher, self.course = funded_teacher_with_course("task")

    def _run(self, file_payload, file_name):
        session = BatchUploadSession.objects.create(
            teacher=self.teacher,
            course=self.course,
            task_type=BatchUploadType.ASSIGNMENT,
            total_files=1,
        )
        tracked = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
            batch_session=session,
        )
        outcome = upload_assignment_async.apply(
            kwargs={
                "user_id": str(self.teacher.id),
                "course_id": str(self.course.id),
                "session_id": str(session.id),
                "file_payload": file_payload,
                "prompt_text": "prompt",
                "file_name": file_name,
                "processing_task_id": str(tracked.id),
            }
        )
        session.refresh_from_db()
        tracked.refresh_from_db()
        return outcome, session, tracked

    def test_an_unreadable_file_fails_uncharged_with_its_own_message(self):
        outcome, session, tracked = self._run(photo_labelled_as_pdf(), "photo.pdf")

        self.assertTrue(outcome.failed())
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(charges(self.teacher), ([], []))
        self.assertEqual(tracked.status, BackgroundTaskStatus.FAILURE)
        self.assertIn(NOT_A_PDF, tracked.error)
        [entry] = session.results
        self.assertEqual(entry["status"], "FAILED")
        self.assertFalse(
            AssignmentUploadFingerprint.objects.filter(course=self.course).exists()
        )

    def test_a_file_already_uploaded_returns_the_existing_assignment_uncharged(self):
        first, _, first_tracked = self._run(good_assignment_payload(), "a.png")
        second, session, second_tracked = self._run(good_assignment_payload(), "a.png")

        self.assertFalse(first.result["already_uploaded"])
        self.assertTrue(second.result["already_uploaded"])
        self.assertEqual(first.result["assignment_id"], second.result["assignment_id"])
        self.assertEqual(charges(self.teacher), ([TOKENS[201]], []))
        self.assertEqual(Assignment.objects.filter(course=self.course).count(), 1)
        [entry] = session.results
        self.assertEqual(entry["status"], "SUCCESS")
        self.assertEqual(entry["assignment_id"], second.result["assignment_id"])
        # Only the task that created the assignment owns it: cancelling the
        # second task must not be able to delete the first one's assignment.
        self.assertIsNotNone(first_tracked.assignment_id)
        self.assertIsNone(second_tracked.assignment_id)

    def test_an_unusable_response_is_refunded_and_the_file_released(self):
        self.provider.behaviour[GOOD_FILES["a.png"]] = lambda width: ai_response(
            TOKENS[width], "this is not JSON"
        )

        outcome, _, tracked = self._run(good_assignment_payload(), "a.png")

        self.assertTrue(outcome.failed())
        kept, refunded = charges(self.teacher)
        self.assertEqual(kept, [])
        self.assertEqual(refunded, [TOKENS[201]] * self.provider.calls_for("a.png"))
        self.assertFalse(
            AssignmentUploadFingerprint.objects.filter(course=self.course).exists()
        )


class UploadTasksOnRealWorkerTest(TransactionTestCase):
    """Real Celery worker, real Redis broker, real PostgreSQL."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.queue = f"s9-upload-policy-{uuid.uuid4().hex[:10]}"

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.provider = Provider()
        self.teacher, self.course = funded_teacher_with_course("worker")
        self.outcomes = []
        self._postrun_lock = threading.Lock()
        task_postrun.connect(self._on_postrun, weak=False, dispatch_uid=self.id())
        worker_shutdown.connect(
            self._on_worker_shutdown, weak=False, dispatch_uid=f"{self.id()}-shutdown"
        )
        # Neutralise post-upload follow-up dispatches so nothing this test
        # triggers lands on the shared default queue.
        for target in (
            "students.services.launch_processing_task",
            "students.services.student_summary_async",
        ):
            followup = patch(target)
            followup.start()
            self.addCleanup(followup.stop)
        provider = patch.object(
            AIProcessor, "_AIProcessor__ai_model", side_effect=self.provider
        )
        provider.start()
        self.addCleanup(provider.stop)

    def tearDown(self):
        task_postrun.disconnect(self._on_postrun, dispatch_uid=self.id())
        worker_shutdown.disconnect(
            self._on_worker_shutdown, dispatch_uid=f"{self.id()}-shutdown"
        )
        redis.Redis.from_url(settings.CELERY_BROKER_URL).delete(self.queue)
        super().tearDown()

    def _on_postrun(self, sender=None, task_id=None, state=None, retval=None, **kw):
        # Runs in the pool thread that executed the task; close its own DB
        # connection so test-database teardown sees no leftover sessions.
        try:
            with self._postrun_lock:
                self.outcomes.append((task_id, state, retval))
        finally:
            connections.close_all()

    @staticmethod
    def _on_worker_shutdown(**kw):
        connections.close_all()

    def _executions(self, task_id):
        with self._postrun_lock:
            return [outcome for outcome in self.outcomes if outcome[0] == task_id]

    def _worker(self):
        return start_worker(
            celery_app,
            queues=[self.queue],
            concurrency=3,
            pool="threads",
            perform_ping_check=False,
            loglevel="WARNING",
            shutdown_timeout=30,
        )

    def _assignment_task(self, file_payload):
        session = BatchUploadSession.objects.create(
            teacher=self.teacher,
            course=self.course,
            task_type=BatchUploadType.ASSIGNMENT,
            total_files=1,
        )
        tracked = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
            batch_session=session,
        )
        result = upload_assignment_async.apply_async(
            kwargs={
                "user_id": str(self.teacher.id),
                "course_id": str(self.course.id),
                "session_id": str(session.id),
                "file_payload": file_payload,
                "prompt_text": "prompt",
                "file_name": file_payload["name"],
                "processing_task_id": str(tracked.id),
            },
            queue=self.queue,
        )
        return result.id, tracked

    def test_an_unreadable_answer_file_executes_exactly_once(self):
        student = CustomUser.objects.create_user(
            email=f"worker-student-{uuid.uuid4().hex[:6]}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Worker",
            last_name="Student",
        )
        assignment = Assignment.objects.create(
            title="Worker homework",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )
        tracked = BackgroundProcessingTask.objects.create(
            requested_by=student,
            task_type=BackgroundTaskType.ANSWER_EXTRACTION,
            assignment=assignment,
        )

        with self._worker():
            result = upload_answers_engine_async.apply_async(
                args=(
                    str(assignment.id),
                    photo_labelled_as_pdf(),
                    "prompt",
                    str(student.id),
                ),
                kwargs={"processing_task_id": str(tracked.id)},
                queue=self.queue,
            )
            _wait_until(
                lambda: self._executions(result.id), what="the upload task to run"
            )
            # A retry would be published with countdown=3; give it well over
            # that to show up before concluding it never will.
            time.sleep(8)
            executions = self._executions(result.id)

        self.assertEqual(
            len(executions), 1, f"the bad file ran {len(executions)} times"
        )
        tracked.refresh_from_db()
        self.assertEqual(tracked.status, BackgroundTaskStatus.FAILURE)
        self.assertIn(NOT_A_PDF, tracked.error)
        self.assertEqual(self.provider.calls, [])

    def test_the_same_assignment_file_in_concurrent_tasks_is_charged_once(self):
        entered = threading.Event()
        release = threading.Event()

        def held(width):
            entered.set()
            release.wait(WAIT)
            return ai_response(TOKENS[width])

        self.provider.behaviour[GOOD_FILES["a.png"]] = held

        with self._worker():
            first_id, first_tracked = self._assignment_task(good_assignment_payload())
            try:
                self.assertTrue(
                    entered.wait(WAIT), "the first task never reached the AI"
                )
                second_id, second_tracked = self._assignment_task(
                    good_assignment_payload()
                )
                _wait_until(
                    lambda: self._executions(second_id),
                    what="the duplicate task to finish",
                )
            finally:
                release.set()
            _wait_until(
                lambda: self._executions(first_id), what="the first task to finish"
            )

        first_tracked.refresh_from_db()
        second_tracked.refresh_from_db()
        self.assertEqual(first_tracked.status, BackgroundTaskStatus.SUCCESS)
        self.assertEqual(second_tracked.status, BackgroundTaskStatus.FAILURE)
        self.assertIn("already being turned into an assignment", second_tracked.error)
        self.assertEqual(self.provider.calls_for("a.png"), 1)
        self.assertEqual(charges(self.teacher), ([TOKENS[201]], []))
        self.assertEqual(Assignment.objects.filter(course=self.course).count(), 1)
