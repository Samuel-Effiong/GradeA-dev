"""H-179: a reply the model cut off at its length limit is counted.

Every metered AI call goes through AIProcessor.execute_graded_task. When the
reply says it stopped because of the length limit (finish_reason "length"), the
call logs ONE warning line with a fixed prefix, the task type, the model the
reply names and the finish reason - nothing else (no prompt or reply text, no
user or submission id, no e-mail) - so the count can be read from the logs.
The retry loops above the call are not changed; whether a cut-off reply should
stop the retries is a separate question for after the counts.

(The "existing metrics module" the row's brief named exists only on the
next-stage line, not on beta: this is a log line.)

The log record is READ (its level, message and arguments), not searched as a
string.
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ai_processor.services import AIProcessor
from billing.tests.test_execute_graded_task import (
    TEST_PROMPT_VERSION,
    ExecuteGradedTaskTestBase,
    make_ai_response,
)
from users.models import UserTypes

PREFIX = "AI reply cut off by the length limit:"
LOGGER = "ai_processor.services"


def reply(finish_reason, model="model-x"):
    response = make_ai_response(tokens=50)
    response.choices[0].finish_reason = finish_reason
    response.model = model
    return response


class CutOffRepliesAreCounted(ExecuteGradedTaskTestBase):
    def call(self, response, user=None, task_type="grade_assignment"):
        """One metered call with the provider replaced; returns the cut-off
        records the call logged. A sentinel line keeps assertLogs from failing
        when the call logs nothing, and is filtered out."""
        user = user or self._make_teacher_with_credits()
        with patch.object(AIProcessor, "_AIProcessor__ai_model", return_value=response):
            with self.assertLogs(LOGGER, level="WARNING") as logged:
                logging.getLogger(LOGGER).warning("sentinel")
                returned = self.processor.execute_graded_task(
                    user=user,
                    feature="Grading Assignment",
                    task_type=task_type,
                    user_prompt="prompt",
                    prompt_version=TEST_PROMPT_VERSION,
                )
        self.assertIs(returned, response)
        return [r for r in logged.records if r.msg.startswith(PREFIX)]

    def test_a_reply_cut_off_at_the_limit_is_logged_once_with_its_task_type(self):
        records = self.call(reply("length"), task_type="extract_answer")

        self.assertEqual(len(records), 1)
        (record,) = records
        self.assertEqual(record.levelno, logging.WARNING)
        self.assertEqual(record.args, ("extract_answer", "model-x", "length"))

    def test_the_line_has_exactly_the_three_fields_and_no_more(self):
        (record,) = self.call(reply("length"))

        self.assertEqual(record.getMessage().count("="), 3)
        self.assertEqual(len(record.args), 3)

    def test_a_reply_that_stopped_normally_is_not_logged(self):
        self.assertEqual(self.call(reply("stop")), [])

    def test_no_reason_at_all_is_not_logged(self):
        self.assertEqual(self.call(reply(None)), [])

    def test_a_reply_object_without_the_attribute_does_not_break_the_call(self):
        bare = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="x"))],
            usage=SimpleNamespace(total_tokens=10),
        )

        self.assertEqual(self.call(bare), [])

    def test_a_reply_with_no_choices_does_not_break_the_call(self):
        empty = SimpleNamespace(choices=[], usage=SimpleNamespace(total_tokens=10))

        self.assertEqual(self.call(empty), [])

    def test_a_mock_for_a_reason_is_treated_as_absent(self):
        response = make_ai_response(tokens=50)
        self.assertIsInstance(response.choices[0].finish_reason, MagicMock)

        self.assertEqual(self.call(response), [])

    def test_a_model_name_that_is_not_text_is_logged_as_none(self):
        response = reply("length")
        response.model = MagicMock()

        (record,) = self.call(response)

        self.assertEqual(record.args, ("grade_assignment", None, "length"))

    def test_the_unmetered_super_admin_branch_logs_nothing(self):
        admin = self._make_user(
            UserTypes.SUPER_ADMIN, "cutoff-super@example.com", is_superuser=True
        )

        self.assertEqual(self.call(reply("length"), user=admin), [])
