"""H-209: what the assignment tasks print and return carries no error text.

The result of a Celery task is stored in the result backend, and whatever a task
prints goes to the worker's output. Three scheduled tasks swallowed any failure
and RETURNED "Error: <message> <traceback>" as their result; the legacy
grade-all task PRINTED its traceback and stored the message and the traceback
in the failure meta. An exception's text is whatever the code that raised it
wrote (a file name, a name, an answer), so none of it belongs in a result or in
a print. The policy is ids and the error's TYPE.

Where the error is swallowed (the three scheduled tasks) the log line is the
only report of it, so it carries the stack FRAMES too (AutoGrader.safe_logging,
H-208). Where it is re-raised (grade-all) the error-reporting service gets it
from the task machinery, so the result carries the class and ids only.

Every test raises an error whose text is a made-up marker, and asserts first
that what it inspects is non-empty and carries the class name (a negative
assertion with nothing to look at proves nothing).
"""

import ast
import io
import logging
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from assignments import tasks as tasks_module
from assignments.tasks import (
    auto_grade_due_assignment,
    grade_all_submissions,
    send_assignment_due_reminder,
    send_new_assignment_posted_notification,
)

MARKER = "H209-RESULT-TEXT-5d2e8b"
SECRET_TEXT = f"cannot open student_answers_Ada_Lovelace.pdf {MARKER}"


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def rendered(self):
        formatter = logging.Formatter()
        return "\n".join(formatter.format(r) for r in self.records)


class _Logs:
    """The project's own loggers ("assignments", ...) have propagate=False in
    settings, so the handler goes on every logger that does not propagate as
    well as on the root."""

    def __enter__(self):
        self.handler = _Collect()
        names = [
            name
            for name, lg in logging.root.manager.loggerDict.items()
            if isinstance(lg, logging.Logger) and not lg.propagate
        ]
        self.attached = [logging.getLogger()] + [logging.getLogger(n) for n in names]
        self.old_levels = {}
        for lg in self.attached:
            lg.addHandler(self.handler)
            self.old_levels[lg] = lg.level
            lg.setLevel(logging.DEBUG)
        return self.handler

    def __exit__(self, *exc):
        for lg in self.attached:
            lg.removeHandler(self.handler)
            lg.setLevel(self.old_levels[lg])


def _assignment_that_raises():
    """A stand-in for the Assignment model whose lookups raise the made-up
    error, however the task reaches it."""
    stand_in = MagicMock()
    error = RuntimeError(SECRET_TEXT)
    stand_in.objects.get.side_effect = error
    stand_in.objects.select_related.return_value.get.side_effect = error
    return stand_in


class TheSwallowingTasksReturnNoText(SimpleTestCase):
    """auto_grade_due_assignment, send_assignment_due_reminder and
    send_new_assignment_posted_notification return a string; that string is
    stored. It names the class and nothing else, and the log line (the only
    report, the error being swallowed) names class and frames."""

    def _call(self, name):
        call = {
            "auto_grade": lambda: auto_grade_due_assignment("a-1"),
            "reminder": lambda: send_assignment_due_reminder("a-1", 24),
            "posted": lambda: send_new_assignment_posted_notification("a-1"),
        }[name]
        with patch("assignments.tasks.Assignment", _assignment_that_raises()):
            with _Logs() as logs:
                result = call()
        return result, logs

    def _check(self, name):
        result, logs = self._call(name)
        self.assertIsInstance(result, str)
        self.assertTrue(result)
        self.assertIn("RuntimeError", result)
        self.assertNotIn(MARKER, result)
        self.assertNotIn("Ada_Lovelace", result)
        self.assertNotIn("Traceback", result)
        self.assertNotIn("File ", result)
        # The log line is the one report: class and frames, never the text.
        rendered = logs.rendered()
        self.assertTrue(logs.records)
        self.assertIn("RuntimeError", rendered)
        self.assertIn("tasks.py:", rendered)
        self.assertNotIn(MARKER, rendered)
        self.assertNotIn("Ada_Lovelace", rendered)
        # Exactly one ERROR record (the level error reporting picks up), from
        # the task's own logger, carrying the class, the id and the frames.
        errors = [r for r in logs.records if r.levelno >= logging.ERROR]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].name, "assignments.tasks")
        message = errors[0].getMessage()
        self.assertIn("assignment=a-1", message)
        self.assertIn("error=RuntimeError", message)
        self.assertIn("frames=", message)
        self.assertIsNone(errors[0].exc_info)

    def test_t1_auto_grade_due_assignment(self):
        self._check("auto_grade")

    def test_t2_send_assignment_due_reminder(self):
        self._check("reminder")

    def test_t3_send_new_assignment_posted_notification(self):
        self._check("posted")


class TheGradeAllTaskKeepsTextOutOfItsMetaAndItsPrint(SimpleTestCase):
    """grade_all_submissions re-raises, so the error reaches error reporting by
    the task machinery. Its stored failure meta and its printed output carry
    the class and ids, not the text and not a traceback."""

    def _run(self):
        submission = MagicMock()
        submission.id = "sub-1"
        stand_in = MagicMock()
        stand_in.objects.filter.return_value.count.return_value = 1
        stand_in.objects.filter.return_value.__iter__.return_value = iter([submission])
        out = io.StringIO()
        with (
            patch("assignments.tasks.StudentSubmission", stand_in),
            patch("assignments.tasks.CustomUser"),
            patch(
                "assignments.tasks.grade_engine",
                side_effect=RuntimeError(SECRET_TEXT),
            ),
            patch("assignments.tasks.mark_processing_task_failure") as failure,
            patch.object(grade_all_submissions, "update_state") as update_state,
            redirect_stdout(out),
        ):
            result = grade_all_submissions.apply(args=("u-1", "a-1"))
        return result, out.getvalue(), update_state, failure

    def _failure_meta(self, update_state):
        calls = [
            c for c in update_state.call_args_list if c.kwargs.get("state") == "FAILURE"
        ]
        self.assertEqual(len(calls), 1)
        return calls[0].kwargs["meta"]

    def test_g1_the_stored_failure_meta_has_class_and_ids_only(self):
        result, _, update_state, _ = self._run()
        self.assertTrue(result.failed())
        meta = self._failure_meta(update_state)
        self.assertEqual(meta["error"], "RuntimeError")
        self.assertEqual(meta["assignment_id"], "a-1")
        self.assertEqual(meta["current_submission_id"], "sub-1")
        self.assertNotIn("detail", meta)
        self.assertNotIn(MARKER, repr(meta))
        self.assertNotIn("Traceback", repr(meta))

    def test_g2_the_traceback_is_not_printed(self):
        result, printed, update_state, _ = self._run()
        # The deciding values: the failure path really ran (a task that never
        # failed prints nothing either), so "nothing printed" is about it.
        self.assertTrue(result.failed())
        self._failure_meta(update_state)
        self.assertNotIn("Traceback", printed)
        self.assertNotIn(MARKER, printed)
        self.assertNotIn("Ada_Lovelace", printed)

    def test_g3_the_error_still_reaches_the_failure_tracker_and_is_raised(self):
        result, _, _, failure = self._run()
        self.assertTrue(result.failed())
        self.assertIsInstance(result.result, RuntimeError)
        failure.assert_called_once()
        self.assertIsInstance(failure.call_args.args[1], RuntimeError)


class TheTaskModuleDoesNotPrint(SimpleTestCase):
    """The prints in assignments/tasks.py held ids, a dict of ids and progress
    text; none held a name or text. They go to the logger (ids only), so the
    worker's output is not a second, unscrubbed log."""

    def test_p1_no_print_call_in_the_module(self):
        source = Path(tasks_module.__file__).read_text()
        calls = [
            node.lineno
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ]
        self.assertEqual(calls, [])

    def test_p2_no_traceback_text_is_built_in_the_module(self):
        source = Path(tasks_module.__file__).read_text()
        self.assertNotIn("format_exc", source)
        self.assertNotIn("import traceback", source)
