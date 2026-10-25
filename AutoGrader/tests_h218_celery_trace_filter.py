"""H-218: Celery's own task log line (celery.app.trace) carries no error text,
no traceback and no task arguments.

Found by H-209 (docs/evidence/h209-task-results-no-text/EVIDENCE.md, line 36)
and seen red by Verifier 2's probe `test_f1`: when a task raises, Celery 5.5.3
logs "Task <name>[<id>] raised unexpected: <repr of the error>" with the
traceback, and puts the traceback text and the task's arguments in the same
record (its argument dict and `record.data`). A retry line repeats the error's
repr. An error's text is whatever the code that raised it wrote (H-208's
reason), and the arguments can hold a teacher's prompt and a file's bytes.

The rule now (the Senior Manager's rulings, 2026-10-25): a filter on that one
logger (AutoGrader.safe_logging.CeleryTraceFilter, installed by
AutoGrader/celery.py) names the error by class and code location only
(`describe_error_for_log`), says "error text omitted" on a retry line, drops
the traceback, `args` and `kwargs`, and keeps the line, its level, the task's
name and id. It never raises.

The records are read by a handler on the logger itself, so each test reads the
record as every handler receives it, after the logger's filters (Verifier 2's
capture read it before them; see NOTES.md). The new class is not imported at
module level, so at the base each test fails for its own reason.
"""

import base64
import logging
import traceback
from unittest.mock import patch

from django.test import TestCase

from assignments.tasks import grade_all_submissions, upload_assignment_async
from assignments.tests_upload_batch_billing import funded_teacher_with_course
from AutoGrader.tasks import send_email_task
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
)
from students.tests_h38_tasks_namespace import TasksFixture

TRACE_LOGGER = "celery.app.trace"
MARKER = "h218-made-up-error-text"
PROMPT_MARKER = "h218-made-up-prompt-text"
DESCRIBE_BROKE = "h218-describe-broke"


class TraceRecords(logging.Handler):
    """Every record of celery.app.trace, whole, as its handlers receive it.
    The logger's level is lowered to INFO for the block (retry and success
    lines are INFO; the logger inherits WARNING in tests)."""

    def __init__(self):
        super().__init__(level=logging.NOTSET)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def __enter__(self):
        self.logger = logging.getLogger(TRACE_LOGGER)
        self._level = self.logger.level
        self.logger.setLevel(logging.INFO)
        self.logger.addHandler(self)
        return self

    def __exit__(self, *exc):
        self.logger.removeHandler(self)
        self.logger.setLevel(self._level)

    def failures(self):
        return [r for r in self.records if "raised unexpected" in r.getMessage()]

    def retries(self):
        return [r for r in self.records if " retry: " in r.getMessage()]


def exc_text(record):
    if not record.exc_info:
        return ""
    return "".join(traceback.format_exception(*record.exc_info))


class GradeAllFailureTest(TasksFixture):
    def run_grade_all(self):
        with patch(
            "assignments.tasks.grade_engine", side_effect=RuntimeError(MARKER)
        ), patch.object(grade_all_submissions, "update_state"), TraceRecords() as cap:
            result = grade_all_submissions.apply(
                args=(str(self.teacher.id), str(self.assignment.id), str(self.task.id))
            )
        return result, cap

    def test_f1_celerys_own_failure_log_for_a_failed_grade_all_carries_no_text(self):
        """T1, adopted from Verifier 2 (verification_2/tests_vf2_h209_probe.py.txt):
        the same arrange, act and assertions; only the capture reads the records
        after the logger's filters."""
        result, cap = self.run_grade_all()

        self.assertTrue(result.failed())
        records = cap.records
        self.assertTrue(records, "premise: Celery logged the failure")
        blob = "\n".join(r.getMessage() + "\n" + exc_text(r) for r in records)
        self.assertNotIn(
            MARKER, blob, "the made-up error text reached celery's own failure log"
        )

    def test_the_failure_line_keeps_its_level_name_id_and_the_errors_class(self):
        """T4: still an ERROR line a person can act on."""
        result, cap = self.run_grade_all()

        [record] = cap.failures()
        message = record.getMessage()
        self.assertIn("error=RuntimeError", message)
        self.assertEqual(record.levelno, logging.ERROR)
        self.assertIn("assignments.tasks.grade_all_submissions", message)
        self.assertIn(result.id, message)

    def test_a_describe_failure_still_strips_and_keeps_the_line(self):
        """T5 (the Senior Manager's never-raise condition): when describing the
        error fails, the line is still written, by class only, without the
        traceback, and the task's own outcome is unchanged."""
        with patch(
            "AutoGrader.safe_logging.describe_error_for_log",
            side_effect=ValueError(DESCRIBE_BROKE),
        ):
            result, cap = self.run_grade_all()

        self.assertTrue(result.failed())
        [record] = cap.failures()
        self.assertNotIn(MARKER, record.getMessage())
        self.assertIsNone(record.exc_info)
        self.assertIn("error=RuntimeError", record.getMessage())
        self.assertEqual(record.levelno, logging.ERROR)


class WholeRecordTest(TestCase):
    def test_the_whole_record_holds_no_text_no_traceback_and_no_arguments(self):
        """T2: a failed upload, whose kwargs hold the teacher's prompt. The
        whole record (`record.__dict__`: its argument dict, `data`, `exc_info`)
        is what the console and Sentry Logs' handler receive."""
        teacher, course = funded_teacher_with_course("h218")
        session = BatchUploadSession.objects.create(
            teacher=teacher,
            course=course,
            task_type=BatchUploadType.ASSIGNMENT,
            total_files=1,
        )
        tracked = BackgroundProcessingTask.objects.create(
            requested_by=teacher,
            task_type=BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
            batch_session=session,
        )

        with patch(
            "assignments.tasks.upload_assignment_file",
            side_effect=RuntimeError(MARKER),
        ), TraceRecords() as cap:
            outcome = upload_assignment_async.apply(
                kwargs={
                    "user_id": str(teacher.id),
                    "course_id": str(course.id),
                    "session_id": str(session.id),
                    "file_payload": {
                        "name": "a.png",
                        "content_type": "image/png",
                        "content_b64": base64.b64encode(b"h218").decode("ascii"),
                    },
                    "prompt_text": PROMPT_MARKER,
                    "file_name": "a.png",
                    "processing_task_id": str(tracked.id),
                }
            )

        self.assertTrue(outcome.failed())
        [record] = cap.failures()
        whole = repr(record.__dict__)
        self.assertNotIn(MARKER, whole)
        self.assertNotIn(PROMPT_MARKER, whole)
        self.assertNotIn("Traceback", whole)
        self.assertNotIn("kwargs", record.args)


class EmailTaskTest(TestCase):
    def send(self):
        return send_email_task.apply(
            kwargs={
                "subject": "H-218",
                "message": "body",
                "from_email": "noreply@example.com",
                "recipient_list": ["teacher-h218@example.com"],
            }
        )

    def test_a_retry_line_holds_no_text(self):
        """T3: the email task retries on a send failure (AutoGrader/tasks.py);
        an eager apply runs each retry at once, so three retry lines."""
        with patch(
            "AutoGrader.tasks._send_email_impl", side_effect=RuntimeError(MARKER)
        ), TraceRecords() as cap:
            self.send()

        retries = cap.retries()
        self.assertEqual(len(retries), 3)
        for record in retries:
            self.assertNotIn(MARKER, repr(record.__dict__))
            self.assertIn("error text omitted", record.getMessage())

    def test_a_successful_send_line_is_unchanged(self):
        """K2 (control): a success line carries no error and passes untouched."""
        with patch(
            "AutoGrader.tasks._send_email_impl", return_value="h218-sent"
        ), TraceRecords() as cap:
            self.send()

        [record] = [r for r in cap.records if "succeeded in" in r.getMessage()]
        self.assertIn("'h218-sent'", record.getMessage())
        self.assertEqual(record.levelno, logging.INFO)


class OtherLoggersTest(TestCase):
    def test_a_record_on_another_logger_is_untouched(self):
        """K1 (control): the filter is on celery.app.trace only; a dict with an
        `exc` key on any other logger keeps its text."""
        with self.assertLogs("h218.other", "ERROR") as logs:
            logging.getLogger("h218.other").error(
                "other: %(exc)s", {"exc": MARKER, "traceback": "kept"}
            )

        [record] = logs.records
        self.assertIn(MARKER, record.getMessage())
        self.assertEqual(record.__dict__["args"]["traceback"], "kept")
