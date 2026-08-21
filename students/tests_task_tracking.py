import uuid
from unittest.mock import Mock, patch

from django.test import TestCase

from assignments.models import Assignment
from AutoGrader.dispatch import ProcessingTemporarilyUnavailable
from billing.access_control import AIFeatureNotAvailableError
from billing.errors import InsufficientCreditsError
from classrooms.models import Course
from students.exceptions import CannotAssociateStudentError, TaskCancelledError
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    StudentSubmission,
)
from students.task_tracking import (
    DEFAULT_TASK_FAILURE_MESSAGE,
    cancel_processing_task,
    cancellable_final_save,
    celery_app,
    cleanup_cancelled_task_artifacts,
    describe_task_error,
    launch_processing_task,
    mark_processing_task_cancelled,
    mark_processing_task_failure,
    mark_processing_task_started,
)
from users.models import CustomUser, UserTypes


class TerminalStatusGuardTest(TestCase):
    """A task that already reached a terminal status must not be resurrected
    by a late/duplicate/redelivered task execution."""

    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="terminal-guard-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Terminal",
            last_name="Guard",
        )

    def test_started_cannot_reopen_success(self):
        task = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            status=BackgroundTaskStatus.SUCCESS,
        )

        mark_processing_task_started(task.id, meta={"step": "Retrying"})

        task.refresh_from_db()
        self.assertEqual(task.status, BackgroundTaskStatus.SUCCESS)
        self.assertIsNone(task.started_at)
        self.assertEqual(task.meta["step"], "Retrying")

    def test_started_cannot_reopen_failure(self):
        task = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            status=BackgroundTaskStatus.FAILURE,
        )

        mark_processing_task_started(task.id, meta={"step": "Retrying"})

        task.refresh_from_db()
        self.assertEqual(task.status, BackgroundTaskStatus.FAILURE)
        self.assertIsNone(task.started_at)
        self.assertEqual(task.meta["step"], "Retrying")

    def test_cancelled_still_guarded(self):
        task = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            status=BackgroundTaskStatus.CANCELLED,
        )

        mark_processing_task_started(task.id, meta={"step": "Retrying"})

        task.refresh_from_db()
        self.assertEqual(task.status, BackgroundTaskStatus.CANCELLED)
        self.assertIsNone(task.started_at)


class FinishedAtIdempotencyTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="finished-at-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Finished",
            last_name="At",
        )

    def test_double_cancellation_does_not_overwrite_finished_at(self):
        task = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            status=BackgroundTaskStatus.STARTED,
        )

        mark_processing_task_cancelled(task.id, meta={"step": "Cancelled"})
        task.refresh_from_db()
        first_finished_at = task.finished_at
        self.assertIsNotNone(first_finished_at)

        # Simulates the worker's own except TaskCancelledError handler firing
        # after the API-triggered cancel_processing_task already finished it.
        mark_processing_task_cancelled(task.id, meta={"step": "Cancelled again"})
        task.refresh_from_db()

        self.assertEqual(task.finished_at, first_finished_at)


class RevokeAppBindingTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="revoke-app-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Revoke",
            last_name="App",
        )

    @patch("students.task_tracking.celery_app.control.revoke")
    @patch("students.task_tracking.AsyncResult")
    def test_cancel_binds_async_result_to_configured_app(
        self, mock_async_result_cls, mock_control_revoke
    ):
        task_id = str(uuid.uuid4())
        processing_task = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            celery_task_id=task_id,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            status=BackgroundTaskStatus.STARTED,
        )

        cancel_processing_task(processing_task)

        mock_async_result_cls.assert_called_once_with(task_id, app=celery_app)
        mock_async_result_cls.return_value.revoke.assert_called_once_with(
            terminate=True, signal="SIGTERM"
        )
        mock_control_revoke.assert_called_once_with(
            task_id, terminate=True, signal="SIGTERM"
        )


class CleanupCancelledTaskArtifactsTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="cleanup-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Cleanup",
            last_name="Teacher",
        )
        self.student = CustomUser.objects.create_user(
            email="cleanup-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Cleanup",
            last_name="Student",
        )
        self.course = Course.objects.create(
            name="Cleanup Course",
            teacher=self.teacher,
            description="A course for cleanup tests.",
        )

    def test_deletes_placeholder_assignment_with_no_submissions(self):
        assignment = Assignment.objects.create(
            course=self.course, title="Ghost assignment"
        )
        processing_task = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            assignment=assignment,
            task_type=BackgroundTaskType.ASSIGNMENT_EXTRACTION,
            status=BackgroundTaskStatus.CANCELLED,
        )

        result = cleanup_cancelled_task_artifacts(processing_task)

        self.assertEqual(result, str(assignment.id))
        self.assertFalse(Assignment.objects.filter(id=assignment.id).exists())
        processing_task.refresh_from_db()
        self.assertIsNone(processing_task.assignment_id)
        self.assertTrue(processing_task.meta["cancelled_assignment_deleted"])

    def test_skips_cleanup_when_assignment_has_submissions(self):
        assignment = Assignment.objects.create(
            course=self.course, title="Assignment with submissions"
        )
        StudentSubmission.objects.create(
            assignment=assignment, student=self.student, answers={"q1": "answer"}
        )
        processing_task = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            assignment=assignment,
            task_type=BackgroundTaskType.ASSIGNMENT_EXTRACTION,
            status=BackgroundTaskStatus.CANCELLED,
        )

        result = cleanup_cancelled_task_artifacts(processing_task)

        self.assertIsNone(result)
        self.assertTrue(Assignment.objects.filter(id=assignment.id).exists())
        processing_task.refresh_from_db()
        self.assertEqual(processing_task.assignment_id, assignment.id)


class LaunchProcessingTaskBrokerFailureTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="launch-failure-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Launch",
            last_name="Failure",
        )

    def test_broker_failure_marks_task_failed_instead_of_orphaning_it(self):
        processing_task = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            status=BackgroundTaskStatus.PENDING,
        )
        broken_task = Mock()
        broken_task.delay.side_effect = ConnectionError("broker unreachable")

        # A broker-connection failure is a distinct, expected condition
        # (Redis unreachable) — not a generic bug — so it surfaces as a
        # typed, clean exception a view can turn into a 503 with an
        # actionable message, rather than the raw ConnectionError leaking
        # straight out of the request.
        with self.assertRaises(ProcessingTemporarilyUnavailable) as ctx:
            launch_processing_task(broken_task, processing_task)

        self.assertEqual(ctx.exception.status_code, 503)

        processing_task.refresh_from_db()
        self.assertEqual(processing_task.status, BackgroundTaskStatus.FAILURE)
        # The raw broker exception is still an implementation detail, not
        # something a grader can act on — it must not reach the frontend
        # verbatim, whether via the response or the tracked task's stored
        # error. A bare ConnectionError is recognized as a connection
        # failure and gets its own actionable message rather than the
        # fully generic fallback (see classify_infra_error).
        self.assertIn("lost connection", processing_task.error)
        self.assertNotIn("broker unreachable", processing_task.error)
        self.assertIsNone(processing_task.celery_task_id)

    def test_non_broker_failure_still_propagates_unchanged(self):
        # Regression guard: only broker-connection failures get the typed
        # "temporarily unavailable" treatment. A bug in our own code
        # building the dispatch call (or any other unexpected exception)
        # must keep propagating as itself, exactly like before this
        # behavior was added — swallowing/rewriting it would hide real
        # bugs behind a misleading "try again later" message.
        processing_task = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            status=BackgroundTaskStatus.PENDING,
        )
        broken_task = Mock()
        broken_task.delay.side_effect = TypeError("unexpected keyword argument")

        with self.assertRaises(TypeError):
            launch_processing_task(broken_task, processing_task)

        processing_task.refresh_from_db()
        self.assertEqual(processing_task.status, BackgroundTaskStatus.FAILURE)


class CancellableFinalSaveTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="final-save-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Final",
            last_name="Save",
        )

    def test_yields_locked_task_and_allows_save_when_not_cancelled(self):
        processing_task = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            status=BackgroundTaskStatus.STARTED,
        )

        with cancellable_final_save(processing_task.id) as locked_task:
            self.assertEqual(locked_task.id, processing_task.id)
            locked_task.meta = {"saved": True}
            locked_task.save(update_fields=["meta"])

        processing_task.refresh_from_db()
        self.assertEqual(processing_task.meta, {"saved": True})

    def test_raises_and_skips_save_when_already_cancelled(self):
        processing_task = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            status=BackgroundTaskStatus.CANCELLED,
        )

        with self.assertRaises(TaskCancelledError):
            with cancellable_final_save(processing_task.id):
                self.fail("save body must not run once cancellation is observed")

    def test_no_processing_task_id_is_a_no_op(self):
        # Flows without a tracked task (processing_task_id=None) must keep
        # working exactly as before: no lock, no exception, save proceeds.
        with cancellable_final_save(None) as locked_task:
            self.assertIsNone(locked_task)


class DescribeTaskErrorTest(TestCase):
    """Unit coverage for the classifier itself, independent of the DB."""

    def test_known_user_facing_exceptions_pass_through_verbatim(self):
        cases = [
            CannotAssociateStudentError("Student not among the enrolled students"),
            AIFeatureNotAvailableError("Upgrade your plan to unlock this feature"),
            InsufficientCreditsError("Refill your wallet to continue"),
        ]
        for exc in cases:
            with self.subTest(exc_type=type(exc).__name__):
                self.assertEqual(describe_task_error(exc), str(exc))

    def test_unknown_exception_never_leaks_raw_technical_detail(self):
        exc = KeyError("grading_summary")
        message = describe_task_error(exc, fallback_message="We couldn't grade this.")
        self.assertEqual(message, "We couldn't grade this.")
        self.assertNotIn("KeyError", message)
        self.assertNotIn("grading_summary", message)

    def test_unknown_exception_falls_back_to_default_when_no_fallback_given(self):
        self.assertEqual(
            describe_task_error(ValueError("boom")), DEFAULT_TASK_FAILURE_MESSAGE
        )

    def test_plain_string_error_is_treated_as_unknown(self):
        # Pre-fix call sites passed hardcoded strings straight through; those
        # must now also route through the generic fallback rather than being
        # trusted as already-safe (a string is not evidence it's meant for a
        # UI reader).
        self.assertEqual(
            describe_task_error("Some internal code path failed"),
            DEFAULT_TASK_FAILURE_MESSAGE,
        )

    def test_empty_user_facing_exception_message_falls_back(self):
        message = describe_task_error(
            CannotAssociateStudentError(""), fallback_message="fallback text"
        )
        self.assertEqual(message, "fallback text")


class MarkProcessingTaskFailureMessageTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="failure-message-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Failure",
            last_name="Message",
        )
        self.processing_task = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            status=BackgroundTaskStatus.STARTED,
        )

    def test_generic_exception_stores_human_fallback_not_raw_exception(self):
        exc = RuntimeError(
            "Traceback (most recent call last): connection reset by peer"
        )

        mark_processing_task_failure(
            self.processing_task.id,
            exc,
            fallback_message="We couldn't grade this submission. Please try again.",
        )

        self.processing_task.refresh_from_db()
        self.assertEqual(
            self.processing_task.error,
            "We couldn't grade this submission. Please try again.",
        )
        self.assertNotIn("Traceback", self.processing_task.error)
        self.assertNotIn("connection reset", self.processing_task.error)

    def test_generic_exception_with_no_fallback_uses_default_message(self):
        mark_processing_task_failure(
            self.processing_task.id, RuntimeError("db exploded")
        )

        self.processing_task.refresh_from_db()
        self.assertEqual(self.processing_task.error, DEFAULT_TASK_FAILURE_MESSAGE)

    def test_known_user_facing_exception_is_stored_verbatim(self):
        mark_processing_task_failure(
            self.processing_task.id,
            InsufficientCreditsError("Refill your wallet to continue"),
            fallback_message="Grading failed for an unrelated reason.",
        )

        self.processing_task.refresh_from_db()
        self.assertEqual(self.processing_task.error, "Refill your wallet to continue")

    @patch("students.task_tracking.logger")
    def test_exception_instance_is_logged_server_side_with_traceback(self, mock_logger):
        exc = RuntimeError("boom")

        mark_processing_task_failure(
            self.processing_task.id, exc, fallback_message="Friendly message."
        )

        mock_logger.error.assert_called_once()
        _, kwargs = mock_logger.error.call_args
        self.assertIs(kwargs.get("exc_info"), exc)

    def test_none_error_with_fallback_used_for_celery_reported_failures(self):
        # normalize_processing_task_status has no exception object to hand
        # over (Celery only reports a bare "FAILURE" state) — must still
        # produce a human message, not "None".
        mark_processing_task_failure(
            self.processing_task.id,
            None,
            fallback_message="This task stopped unexpectedly.",
        )

        self.processing_task.refresh_from_db()
        self.assertEqual(self.processing_task.error, "This task stopped unexpectedly.")
