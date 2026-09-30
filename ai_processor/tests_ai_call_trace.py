"""S5 (FR-A-03, NFR-OBS-04): every AI provider call is traceable.

`AIProcessor.__ai_model` is the only place a provider call leaves the app.
Each call now:
- sends the server trace id as X-Request-ID;
- writes ONE log line: trace id, model, prompt version, task type, attempt,
  latency and outcome - never prompt or answer text.
And every caller of `execute_graded_task` must name its prompt version.

The provider is a fake client returning real scalars (rule 14).
"""

import ast
import logging
import re
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from celery import Celery
from celery.contrib.testing.worker import start_worker
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase

from ai_processor import services
from ai_processor.services import AIProcessor, Prompt, prompt_version_of
from audit.context import trace_context
from AutoGrader.middleware import RequestIDMiddleware
from AutoGrader.request_context import reset_request_id, set_request_id

SYSTEM_PROMPT_TEXT = "SYSTEM PROMPT TEXT THAT MUST NOT BE LOGGED"
STUDENT_ANSWER = "STUDENT ANSWER TEXT THAT MUST NOT BE LOGGED"
LOGGER = "ai_processor.services"


class FakeCompletions:
    def __init__(self, error=None):
        self.requests = []
        self.error = error

    def create(self, **request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return SimpleNamespace(
            model="provider/served-model",
            usage=SimpleNamespace(total_tokens=10),
            choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
        )


def processor_with(completions):
    processor = AIProcessor()
    processor.client = cast(
        Any, SimpleNamespace(chat=SimpleNamespace(completions=completions))
    )
    return processor


def call(processor):
    return processor._AIProcessor__ai_model(
        system_prompt=SYSTEM_PROMPT_TEXT,
        user_prompt=STUDENT_ANSWER,
        prompt_version="GRADING_ASSIGNMENT_PROMPT_5@0123abcd",
        task_type="grade_assignment",
    )


def ai_call_lines(logs):
    return [line for line in logs.output if "ai_call " in line]


def logged_trace_id(line):
    match = re.search(r"trace_id=(\S+)", line)
    assert match, line
    return uuid.UUID(match.group(1))


class ProviderCallTests(SimpleTestCase):
    def test_the_provider_receives_the_trace_id(self):
        completions = FakeCompletions()
        with trace_context() as trace_id:
            call(processor_with(completions))

        headers = completions.requests[0]["extra_headers"]
        self.assertEqual(headers["X-Request-ID"], str(trace_id))
        self.assertEqual(headers["X-Title"], "GradeA+")

    def test_one_log_line_per_call_with_the_fields_and_no_text(self):
        with trace_context() as trace_id:
            with self.assertLogs(LOGGER, logging.INFO) as logs:
                call(processor_with(FakeCompletions()))

        lines = ai_call_lines(logs)
        self.assertEqual(len(lines), 1)
        for field in (
            f"trace_id={trace_id}",
            "model=provider/served-model",
            "prompt_version=GRADING_ASSIGNMENT_PROMPT_5@0123abcd",
            "task_type=grade_assignment",
            "attempt=1",
            "outcome=ok",
        ):
            self.assertIn(field, lines[0])
        self.assertRegex(lines[0], r"latency_ms=\d+")
        for text in (SYSTEM_PROMPT_TEXT, STUDENT_ANSWER):
            self.assertNotIn(text, "\n".join(logs.output))

    def test_a_failed_call_is_logged_with_its_error_class_and_still_raises(self):
        completions = FakeCompletions(error=TimeoutError("provider timed out"))
        with self.assertLogs(LOGGER, logging.INFO) as logs:
            with self.assertRaises(TimeoutError):
                call(processor_with(completions))

        lines = ai_call_lines(logs)
        self.assertEqual(len(lines), 1)
        self.assertIn("outcome=TimeoutError", lines[0])
        self.assertNotIn("provider timed out", lines[0])


class AttemptTests(SimpleTestCase):
    def test_a_retried_task_logs_its_attempt_number(self):
        """Celery's retry count + 1: the third try of a task is attempt 3."""
        from unittest.mock import patch

        third_try = SimpleNamespace(request=SimpleNamespace(id="task-1", retries=2))
        with patch("celery.current_task", third_try):
            with self.assertLogs(LOGGER, logging.INFO) as logs:
                call(processor_with(FakeCompletions()))

        self.assertIn("attempt=3", ai_call_lines(logs)[0])


class ClientChosenIdIsNeverTheTraceTests(SimpleTestCase):
    """X-5 (S5 part 0): a client's X-Request-ID never becomes the trace id
    on the AI call - it stays the server's."""

    def test_the_ai_call_carries_the_server_id_not_the_inbound_one(self):
        inbound = uuid.uuid4()
        completions = FakeCompletions()
        seen = {}

        def view(request):
            seen["server_id"] = uuid.UUID(request.request_id)
            call(processor_with(completions))
            return HttpResponse("ok")

        request = RequestFactory().post("/", HTTP_X_REQUEST_ID=str(inbound))
        with self.assertLogs(LOGGER, logging.INFO) as logs:
            RequestIDMiddleware(view)(request)

        trace = logged_trace_id(ai_call_lines(logs)[0])
        self.assertEqual(trace, seen["server_id"])
        self.assertNotEqual(trace, inbound)
        self.assertEqual(
            completions.requests[0]["extra_headers"]["X-Request-ID"],
            str(seen["server_id"]),
        )


class CeleryHopTests(TestCase):
    """The same trace id on the AI call inside a worker as in the request
    that dispatched it: a real (non-eager) round trip through the project's
    signal handlers, with a throwaway app and in-memory broker (the pattern
    of `AutoGrader.tests_celery_signals.EndToEndBrokerRoundTripTests`)."""

    app: Celery
    completions: FakeCompletions
    grade: Any

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.app = Celery(
            "s5_ai_call_trace", broker="memory://", backend="cache+memory://"
        )
        cls.app.conf.task_always_eager = False
        cls.completions = FakeCompletions()
        processor = processor_with(cls.completions)

        @cls.app.task(name="s5_ai_call_trace.grade")
        def grade():
            call(processor)

        cls.grade = grade

    def test_the_worker_logs_the_dispatching_requests_trace_id(self):
        dispatch_id = uuid.uuid4()
        token = set_request_id(dispatch_id.hex)
        try:
            with self.assertLogs(LOGGER, logging.INFO) as logs:
                with start_worker(self.app, perform_ping_check=False):
                    self.grade.delay().get(timeout=10)
        finally:
            reset_request_id(token)

        self.assertEqual(logged_trace_id(ai_call_lines(logs)[0]), dispatch_id)


class EveryCallerNamesItsPromptTests(SimpleTestCase):
    SOURCE = Path(services.__file__)

    def execute_graded_task_calls(self):
        tree = ast.parse(self.SOURCE.read_text(encoding="utf-8"))
        return [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute_graded_task"
        ]

    def test_every_call_passes_a_prompt_version(self):
        calls = self.execute_graded_task_calls()
        self.assertGreaterEqual(len(calls), 16)
        for node in calls:
            with self.subTest(line=node.lineno):
                self.assertIn("prompt_version", {kw.arg for kw in node.keywords})

    def test_omitting_it_is_refused(self):
        user = SimpleNamespace(user_type="TEACHER")
        with self.assertRaises(TypeError):
            AIProcessor().execute_graded_task(  # type: ignore[call-arg]
                user=user, feature="f", task_type="t"
            )
        with self.assertRaisesMessage(ValueError, "requires a prompt_version"):
            AIProcessor().execute_graded_task(
                user=user, feature="f", task_type="t", prompt_version=""
            )


class PromptVersionTests(SimpleTestCase):
    def test_every_loaded_prompt_is_versioned_by_file_and_text(self):
        prompts = {
            name: value
            for name, value in vars(services).items()
            if isinstance(value, Prompt)
        }
        self.assertIn("GRADING_ASSIGNMENT_PROMPT", prompts)
        for name, prompt in prompts.items():
            with self.subTest(prompt=name):
                self.assertRegex(prompt.version, r"^[A-Z0-9_]+@[0-9a-f]{8}$")

    def test_editing_the_text_changes_the_version(self):
        self.assertNotEqual(
            prompt_version_of("GRADING_ASSIGNMENT_PROMPT_5.txt", "rubric A"),
            prompt_version_of("GRADING_ASSIGNMENT_PROMPT_5.txt", "rubric B"),
        )
        self.assertTrue(
            prompt_version_of("GRADING_ASSIGNMENT_PROMPT_5.txt", "x").startswith(
                "GRADING_ASSIGNMENT_PROMPT_5@"
            )
        )
