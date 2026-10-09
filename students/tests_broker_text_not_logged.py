"""H-208: a log line about a failed broker call names the failure by its class
and carries no text and no traceback.

Verifier 2's probe on the score-printing stack showed that when the queue
refuses a task, the older line in `mark_processing_task_failure` logged the
broker's own error text and a traceback. A broker error's text can carry the
broker's address. The same shape was found in `cancel_processing_task`,
`normalize_processing_task_status`, `AutoGrader.dispatch.safe_delay` and the
grading path's `_dispatch_followups` (which logged the chained cause).

Every test uses a made-up marker as the broker error's text and renders the
captured records the way a log handler does (message plus traceback), so a
marker in the message, in `exc_info` or in a chained cause is found. Before any
"marker is absent" is read, the records are asserted to exist and to carry the
id and the class name the line is supposed to carry (a negative assertion with
nothing to look at proves nothing).

For an error that is NOT a broker outage the line also carries the stack
FRAMES only (file:line:function), never the message and never local values: the
one caller path that swallows it (the manual-grade route) has no other road to
the error-reporting service.
"""

import importlib
import logging
import re
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone
from redis.exceptions import ConnectionError as RedisConnectionError

from assignments.models import Assignment
from AutoGrader.dispatch import ProcessingTemporarilyUnavailable, safe_delay
from classrooms.models import Course, Session
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    StudentSubmission,
)
from students.services import grade_engine
from students.task_tracking import (
    cancel_processing_task,
    create_processing_task,
    launch_processing_task,
    mark_processing_task_failure,
    normalize_processing_task_status,
)
from users.models import CustomUser, UserTypes

MARKER = "H208-BROKER-TEXT-7f3a9c"
BROKER_TEXT = f"Error 111 connecting to broker-internal.example:6379. {MARKER}."

# The helper that builds the part of the line about the error. Named by a
# string: it exists only after the change, and the behaviour tests above it
# must be able to run (and fail) on the code before it.
HELPER_MODULE = "AutoGrader.safe_logging"
HELPER_NAME = "describe_error_for_log"


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


class Captured:
    """Collect the records of every logger. The project's own loggers
    ("students", ...) have propagate=False in settings, so a handler on the
    root alone sees none of their records: the handler is also put on every
    logger that does not propagate."""

    def _loggers(self):
        names = [
            name
            for name, lg in logging.root.manager.loggerDict.items()
            if isinstance(lg, logging.Logger) and not lg.propagate
        ]
        return [logging.getLogger()] + [logging.getLogger(n) for n in names]

    def __enter__(self):
        self.handler = _Collect()
        self.old_levels = {}
        self.attached = self._loggers()
        for lg in self.attached:
            lg.addHandler(self.handler)
            self.old_levels[lg] = lg.level
            lg.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *exc):
        for lg in self.attached:
            lg.removeHandler(self.handler)
            lg.setLevel(self.old_levels[lg])

    @property
    def records(self):
        return self.handler.records

    def rendered(self, logger_prefix=""):
        """Every record of the loggers named `logger_prefix*`, rendered as a
        handler would (message and traceback)."""
        formatter = logging.Formatter()
        return "\n".join(
            formatter.format(r)
            for r in self.records
            if r.name.startswith(logger_prefix)
        )


class Base(TransactionTestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email=f"h208-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        student = CustomUser.objects.create_user(
            email=f"h208-student-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        session = Session.objects.create(name="Test Session", teacher=self.teacher)
        course = Course.objects.create(
            name="Test Course", teacher=self.teacher, session=session
        )
        assignment = Assignment.objects.create(
            title="Test Assignment",
            course=course,
            questions=[{"question_number": 1, "points": 10}],
        )
        self.submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "An answer."}],
        )

    def a_tracked_task(self):
        return create_processing_task(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            submission=self.submission,
        )

    def a_running_task_with_a_celery_id(self):
        task = self.a_tracked_task()
        BackgroundProcessingTask.objects.filter(pk=task.pk).update(
            celery_task_id="celery-h208-1", status=BackgroundTaskStatus.STARTED
        )
        return BackgroundProcessingTask.objects.get(pk=task.pk)


def _refuses(error):
    def delay(*args, **kwargs):
        raise error

    return SimpleNamespace(delay=delay, name="tasks.h208_task")


class TheLauncherLine(Base):
    def test_a_refused_queue_logs_the_id_and_the_class_and_nothing_more(self):
        task = self.a_tracked_task()

        with Captured() as logs:
            with self.assertRaises(ProcessingTemporarilyUnavailable):
                launch_processing_task(
                    _refuses(RedisConnectionError(BROKER_TEXT)), task
                )

        text = logs.rendered("students.task_tracking")
        self.assertTrue(text)
        self.assertIn(str(task.id), text)
        self.assertIn("ConnectionError", text)
        self.assertNotIn(MARKER, text)
        self.assertNotIn("Traceback", text)
        self.assertTrue(all(r.exc_info is None for r in logs.records))

    def test_a_broker_error_logs_no_frames_either(self):
        task = self.a_tracked_task()

        with Captured() as logs:
            with self.assertRaises(ProcessingTemporarilyUnavailable):
                launch_processing_task(
                    _refuses(RedisConnectionError(BROKER_TEXT)), task
                )

        text = logs.rendered("students.task_tracking")
        self.assertTrue(text)
        self.assertIn(str(task.id), text)
        self.assertIsNone(re.search(r"task_tracking\.py:\d+:", text))

    def test_another_error_logs_the_id_the_class_and_frames_without_its_text(self):
        task = self.a_tracked_task()

        with Captured() as logs:
            with self.assertRaises(RuntimeError):
                launch_processing_task(_refuses(RuntimeError(BROKER_TEXT)), task)

        text = logs.rendered("students.task_tracking")
        self.assertTrue(text)
        self.assertIn(str(task.id), text)
        self.assertIn("RuntimeError", text)
        self.assertIsNotNone(
            re.search(r"task_tracking\.py:\d+:launch_processing_task", text), text
        )
        self.assertNotIn(MARKER, text)
        self.assertNotIn("Traceback", text)
        self.assertTrue(all(r.exc_info is None for r in logs.records))

    def test_a_wrapped_broker_error_is_still_a_broker_error(self):
        task = self.a_tracked_task()
        try:
            try:
                raise RedisConnectionError(BROKER_TEXT)
            except RedisConnectionError as cause:
                raise ProcessingTemporarilyUnavailable() from cause
        except ProcessingTemporarilyUnavailable as wrapped:
            error = wrapped

        with Captured() as logs:
            mark_processing_task_failure(task.id, error)

        text = logs.rendered("students.task_tracking")
        self.assertTrue(text)
        self.assertIn(str(task.id), text)
        self.assertIn("ConnectionError", text)
        self.assertNotIn(MARKER, text)
        self.assertIsNone(re.search(r"\.py:\d+:", text))


class TheCancelLine(Base):
    def test_an_unreachable_broker_at_revoke_logs_ids_and_the_class_only(self):
        task = self.a_running_task_with_a_celery_id()

        with Captured() as logs:
            with patch("students.task_tracking.celery_app") as app:
                app.control.revoke.side_effect = RedisConnectionError(BROKER_TEXT)
                cancel_processing_task(task)

        text = logs.rendered("students.task_tracking")
        self.assertTrue(text)
        self.assertIn(str(task.id), text)
        self.assertIn("celery-h208-1", text)
        self.assertIn("ConnectionError", text)
        self.assertNotIn(MARKER, text)
        self.assertNotIn("Traceback", text)
        self.assertTrue(all(r.exc_info is None for r in logs.records))


class TheStatusLine(Base):
    def test_an_unreadable_result_backend_logs_ids_and_the_class_only(self):
        task = self.a_running_task_with_a_celery_id()

        with Captured() as logs:
            with patch("students.task_tracking.AsyncResult") as result:
                type(result.return_value).state = PropertyMock(
                    side_effect=RedisConnectionError(BROKER_TEXT)
                )
                status = normalize_processing_task_status(task)

        self.assertEqual(status, task.status)
        text = logs.rendered("students.task_tracking")
        self.assertTrue(text)
        self.assertIn(str(task.id), text)
        self.assertIn("ConnectionError", text)
        self.assertNotIn(MARKER, text)
        self.assertNotIn("Traceback", text)
        self.assertTrue(all(r.exc_info is None for r in logs.records))


class TheSafeDelayLine(Base):
    def test_a_dispatch_the_broker_refused_logs_the_task_and_the_class_only(self):
        with Captured() as logs:
            returned = safe_delay(_refuses(RedisConnectionError(BROKER_TEXT)), 1)

        self.assertIsNone(returned)
        text = logs.rendered("AutoGrader.dispatch")
        self.assertTrue(text)
        self.assertIn("tasks.h208_task", text)
        self.assertIn("ConnectionError", text)
        self.assertNotIn(MARKER, text)
        self.assertNotIn("Traceback", text)
        self.assertTrue(all(r.exc_info is None for r in logs.records))


class TheGradingFollowUpLine(Base):
    @patch("students.services._formatted_grade_task")
    @patch("students.services.student_summary_async")
    @patch("students.services.ai_processor")
    def test_a_refused_follow_up_queue_leaves_the_broker_text_out_of_every_line(
        self, mock_ai, mock_summary, mock_formatted
    ):
        mock_ai.extract_grade_with_retry.return_value = {
            "grading_summary": {
                "total_score": 8,
                "max_total_points": 10,
                "percentage": 80.0,
            },
            "grading_confidence": 90,
            "question_evaluations": [],
        }
        mock_formatted.return_value.delay.side_effect = RedisConnectionError(
            BROKER_TEXT
        )

        with Captured() as logs:
            grade_engine(self.teacher, self.submission)

        mock_formatted.return_value.delay.assert_called_once()
        services_text = logs.rendered("students.services")
        everything = logs.rendered("")
        # the line of the grading path exists, names the submission and the class
        self.assertTrue(services_text)
        self.assertIn(str(self.submission.id), services_text)
        self.assertIn("ConnectionError", everything)
        # and nothing anywhere, the chained cause included, carries the text
        self.assertNotIn(MARKER, everything)
        self.assertNotIn("direct cause", everything)
        self.assertTrue(all(r.exc_info is None for r in logs.records))


class TheOneHelper(SimpleTestCase):
    def helper(self):
        return getattr(importlib.import_module(HELPER_MODULE), HELPER_NAME)

    def test_a_broker_error_is_named_by_its_class_alone(self):
        text = self.helper()(RedisConnectionError(BROKER_TEXT))

        self.assertIn("ConnectionError", text)
        self.assertNotIn(MARKER, text)
        self.assertNotIn(".py", text)

    def test_another_error_is_named_with_frames_and_without_its_text(self):
        try:
            raise ValueError(BROKER_TEXT)
        except ValueError as error:
            text = self.helper()(error)

        self.assertIn("ValueError", text)
        self.assertIn("test_another_error_is_named_with_frames", text)
        self.assertNotIn(MARKER, text)
