"""The per-call usage record (estimate step 1, H-201): what it writes, and
that it can never change a call or its charge.

Every AI call that goes through `AIProcessor.execute_graded_task` leaves one
row in `AICallRecord` after the call and its charge are finished: the estimate
the gate used, the token counts the reply carried, the model that answered,
the provider's reported cost if there is one, counts of what was sent, and
what became of the charge. Numbers and ids only. Calls that were NOT charged
(refused, failed, cancelled, super admin) are recorded too.

What these tests hold:

  * the rows: every outcome, the usage numbers when present and NULL when
    absent, the billed teacher on a student's upload, the kind only where the
    task type proves it;
  * the call is unchanged: the same response, the same exception (the very
    object, with its traceback), the same balance, usage rows and ledger rows
    with recording on, off and failing;
  * the record cannot fail or delay the charge: it is written after the
    charge, inside its own savepoint, with one INSERT and no reads, and every
    failure of the write or of the reading of the reply is swallowed and
    counted, but SoftTimeLimitExceeded on the success path and
    SystemExit/KeyboardInterrupt are not;
  * nothing but ids and numbers is stored (a canary scan).

Every provider reply has a real string model, or none on purpose (rule 14).
"""

import logging
import math
import threading
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from celery.exceptions import SoftTimeLimitExceeded
from django.db import (
    DataError,
    IntegrityError,
    OperationalError,
    ProgrammingError,
    connection,
    transaction,
)
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from ai_processor import call_record
from ai_processor.models import AICallRecord, CallOutcome
from ai_processor.services import MAIN_MODEL, AIProcessor
from billing.access_control import AIFeatureNotAvailableError
from billing.errors import InsufficientCreditsError
from billing.models import CreditLedger, CreditUsageLog, CreditWallet
from billing.tests.test_execute_graded_task import ExecuteGradedTaskTestBase
from students.exceptions import TaskCancelledError
from students.tests_post_grading_submission_lock import _classroom
from users.models import UserTypes

AI_MODEL = "_AIProcessor__ai_model"
REFUSALS = (AIFeatureNotAvailableError, InsufficientCreditsError)
FEATURE = "Formatted Grade"
TASK_TYPE = "formatted_grade"


def reply(
    total=500,
    model="answer/model",
    prompt=400,
    completion=100,
    reasoning=30,
    cached=250,
    cost=0.0123,
    finish="stop",
    provider="StreamLake",
):
    """A provider reply with the fields execute_graded_task and the record
    read. `None` for a field leaves it off."""
    usage = {"total_tokens": total}
    if prompt is not None:
        usage["prompt_tokens"] = prompt
    if completion is not None:
        usage["completion_tokens"] = completion
    if reasoning is not None:
        usage["completion_tokens_details"] = SimpleNamespace(reasoning_tokens=reasoning)
    if cached is not None:
        usage["prompt_tokens_details"] = SimpleNamespace(cached_tokens=cached)
    if cost is not None:
        usage["cost"] = cost
    fields = {
        "choices": [
            SimpleNamespace(
                message=SimpleNamespace(content='{"result": "ok"}'),
                finish_reason=finish,
            )
        ],
        "usage": SimpleNamespace(**usage),
    }
    if model is not None:
        fields["model"] = model
    if provider is not None:
        fields["provider"] = provider
    return SimpleNamespace(**fields)


class Base(ExecuteGradedTaskTestBase):
    def setUp(self):
        super().setUp()
        self.teacher = self._make_teacher_with_credits()
        call_record.reset_failure_count()

    def run_call(self, user=None, **kwargs):
        arguments = {
            "user": user or self.teacher,
            "feature": FEATURE,
            "task_type": TASK_TYPE,
            "user_prompt": "short prompt",
        }
        arguments.update(kwargs)
        return self.processor.execute_graded_task(**arguments)

    def only_row(self):
        rows = list(AICallRecord.objects.all())
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def balance(self):
        return CreditWallet.objects.get(user=self.teacher).total_remaining_credits()


class TheRowOfACharge(Base):
    @patch.object(AIProcessor, AI_MODEL)
    def test_a_charged_call_leaves_one_row_with_what_was_known(self, ai_model):
        ai_model.return_value = reply()
        with patch.object(
            AIProcessor, "estimate_total_token", return_value=20321
        ) as estimate:
            self.run_call()
        row = self.only_row()
        self.assertEqual(estimate.call_count, 1)
        self.assertEqual(row.outcome, CallOutcome.CHARGED)
        self.assertEqual(row.task_type, TASK_TYPE)
        self.assertEqual(row.feature, FEATURE)
        self.assertEqual(row.kind, "formatting")
        self.assertEqual(row.estimate, 20321)
        self.assertEqual(row.total_tokens, 500)
        self.assertEqual(row.prompt_tokens, 400)
        self.assertEqual(row.completion_tokens, 100)
        self.assertEqual(row.reasoning_tokens, 30)
        self.assertEqual(row.cached_tokens, 250)
        self.assertEqual(float(row.cost_usd), 0.0123)
        self.assertEqual(row.finish_reason, "stop")
        self.assertEqual(row.model_answered, "answer/model")
        self.assertEqual(row.upstream_provider, "StreamLake")
        self.assertEqual(row.model_asked, MAIN_MODEL)
        self.assertEqual(row.billed_user_id, self.teacher.id)
        self.assertEqual(row.charged_credits, 500)
        self.assertEqual(row.prompt_version, "unknown")
        self.assertIsNotNone(row.created_at)
        self.assertIsNotNone(row.duration_ms)

    @patch.object(AIProcessor, AI_MODEL)
    def test_the_row_reconciles_with_the_charge(self, ai_model):
        ai_model.return_value = reply(total=777)
        self.run_call()
        row = self.only_row()
        logs = CreditUsageLog.objects.filter(task_id=row.task_id)
        self.assertTrue(logs.exists())
        self.assertEqual(sum(log.amount for log in logs), row.charged_credits)
        self.assertEqual(row.charged_credits, row.total_tokens)
        self.assertEqual(row.charged_credits, 777)

    @patch.object(AIProcessor, AI_MODEL)
    def test_the_model_asked_for_is_the_override_when_there_is_one(self, ai_model):
        ai_model.return_value = reply()
        self.run_call(override_model="pinned/model")
        self.assertEqual(self.only_row().model_asked, "pinned/model")

    @patch.object(AIProcessor, AI_MODEL)
    def test_a_field_the_reply_does_not_carry_is_null_never_zero(self, ai_model):
        ai_model.return_value = reply(
            model=None,
            prompt=None,
            completion=None,
            reasoning=None,
            cached=None,
            cost=None,
            finish=None,
            provider=None,
        )
        self.run_call()
        row = self.only_row()
        self.assertEqual(row.outcome, CallOutcome.CHARGED)
        self.assertIsNone(row.model_answered)
        self.assertIsNone(row.upstream_provider)
        self.assertIsNone(row.prompt_tokens)
        self.assertIsNone(row.completion_tokens)
        self.assertIsNone(row.reasoning_tokens)
        self.assertIsNone(row.cached_tokens)
        self.assertIsNone(row.cost_usd)
        self.assertIsNone(row.finish_reason)
        self.assertEqual(row.total_tokens, 500)

    @patch.object(AIProcessor, AI_MODEL)
    def test_a_weird_value_in_the_reply_becomes_null_and_the_call_goes_on(
        self, ai_model
    ):
        weird: list[dict[str, Any]] = [
            {"prompt": "many"},
            {"prompt": -5},
            {"prompt": math.nan},
            {"prompt": 10**30},
            {"cost": "free"},
            {"cost": math.nan},
            {"cost": -1.0},
            {"finish": "x" * 500},
        ]
        for change in weird:
            with self.subTest(change=change):
                AICallRecord.objects.all().delete()
                ai_model.return_value = reply(**change)
                response = self.run_call()
                self.assertIsNotNone(response)
                row = self.only_row()
                if "prompt" in change:
                    self.assertIsNone(row.prompt_tokens)
                if "cost" in change:
                    self.assertIsNone(row.cost_usd)
                if "finish" in change:
                    self.assertEqual(row.finish_reason, "other")

    @patch.object(AIProcessor, AI_MODEL)
    def test_the_upstream_provider_is_the_name_in_the_reply_and_nothing_else(
        self, ai_model
    ):
        """Read from the reply only (no extra request): a name is kept as given
        and capped at 100 characters; anything that is not a non-empty string
        is NULL."""
        cases = {
            "StreamLake": "StreamLake",
            "Relace": "Relace",
            "x" * 300: "x" * 100,
            "": None,
            "   ": None,
            123: None,
            ("tuple",): None,
        }
        for given, expected in cases.items():
            with self.subTest(given=repr(given)[:30]):
                AICallRecord.objects.all().delete()
                ai_model.return_value = reply(provider=given)
                self.run_call()
                self.assertEqual(self.only_row().upstream_provider, expected)

    @patch.object(AIProcessor, AI_MODEL)
    def test_a_call_that_raised_has_no_upstream_provider(self, ai_model):
        ai_model.side_effect = TimeoutError("provider down")
        with self.assertRaises(TimeoutError):
            self.run_call()
        self.assertIsNone(self.only_row().upstream_provider)

    @patch.object(AIProcessor, AI_MODEL)
    def test_a_reply_with_no_usage_object_at_all_still_leaves_a_row(self, ai_model):
        response = reply()
        response.usage.completion_tokens_details = None
        response.usage.prompt_tokens_details = None
        ai_model.return_value = response
        self.run_call()
        row = self.only_row()
        self.assertIsNone(row.reasoning_tokens)
        self.assertIsNone(row.cached_tokens)

    @patch.object(AIProcessor, AI_MODEL)
    def test_the_kind_is_set_only_where_the_task_type_proves_it(self, ai_model):
        ai_model.return_value = reply()
        cases = {
            ("Formatted Grade", "formatted_grade"): "formatting",
            ("Grading Assignment", "grade_assignment"): None,
            ("Answer Extraction", "extract_answer"): None,
        }
        for (feature, task_type), kind in cases.items():
            with self.subTest(task_type=task_type):
                AICallRecord.objects.all().delete()
                self.run_call(feature=feature, task_type=task_type)
                row = self.only_row()
                self.assertEqual(row.task_type, task_type)
                self.assertEqual(row.kind, kind)

    @patch.object(AIProcessor, AI_MODEL)
    def test_an_unlisted_task_type_and_feature_are_recorded_as_other(self, ai_model):
        ai_model.return_value = reply()
        with patch.object(AIProcessor, "estimate_total_token", return_value=10):
            self.run_call(feature="Dashboard Analytics", task_type="dash_made_up")
        row = self.only_row()
        self.assertEqual(row.task_type, "other")
        self.assertEqual(row.feature, "other")
        self.assertIsNone(row.kind)

    @patch.object(AIProcessor, AI_MODEL)
    def test_counts_of_what_was_sent_come_from_the_request_not_the_bytes(
        self, ai_model
    ):
        ai_model.return_value = reply()
        image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,A"}}
        with patch.object(AIProcessor, "estimate_total_token", return_value=10):
            self.run_call(
                user_prompt=[{"type": "text", "text": "x"}, image, dict(image)],
                system_prompt=[{"type": "text", "text": "s"}],
            )
        row = self.only_row()
        self.assertEqual(row.images, 2)
        self.assertEqual(row.pdf_items, 0)
        self.assertIsNone(row.questions)

    @patch.object(AIProcessor, AI_MODEL)
    def test_the_question_count_is_taken_from_the_assignment_when_given(self, ai_model):
        ai_model.return_value = reply()
        _teacher, student, _course, assignment = _classroom("cr1")
        with patch.object(AIProcessor, "estimate_total_token", return_value=10):
            self.run_call(user=assignment.course.teacher, assignment=assignment)
        row = self.only_row()
        self.assertEqual(row.questions, len(assignment.questions))
        self.assertEqual(row.assignment_id, assignment.id)
        self.assertNotIn(str(student.id), repr(row.__dict__))

    @patch.object(AIProcessor, AI_MODEL)
    def test_images_and_pdfs_inside_messages_are_counted(self, ai_model):
        ai_model.return_value = reply()
        image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,A"}}
        pdf = {"type": "pdf_url", "pdf_url": {"url": "data:application/pdf;base64,A"}}
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "x"}, image, pdf]},
            {"role": "assistant", "content": None},
        ]
        with patch.object(AIProcessor, "estimate_total_token", return_value=10):
            self.run_call(user_prompt=None, messages=messages)
        row = self.only_row()
        self.assertEqual(row.images, 1)
        self.assertEqual(row.pdf_items, 1)

    @patch.object(AIProcessor, AI_MODEL)
    def test_a_pdf_part_in_the_prompt_is_counted_as_a_pdf_not_an_image(self, ai_model):
        ai_model.return_value = reply()
        pdf = {"type": "pdf_url", "pdf_url": {"url": "data:application/pdf;base64,A"}}
        with patch.object(AIProcessor, "estimate_total_token", return_value=10):
            self.run_call(user_prompt=[{"type": "text", "text": "x"}, pdf])
        row = self.only_row()
        self.assertEqual(row.pdf_items, 1)
        self.assertEqual(row.images, 0)


class TheBilledTeacherOnAStudentsUpload(Base):
    @patch.object(AIProcessor, AI_MODEL)
    def test_the_row_carries_the_teacher_and_never_the_student(self, ai_model):
        ai_model.return_value = reply()
        teacher, student, _course, assignment = _classroom("cr2")
        with patch.object(AIProcessor, "estimate_total_token", return_value=10):
            self.run_call(
                user=student,
                assignment=assignment,
                feature="Answer Extraction",
                task_type="extract_answer",
            )
        row = self.only_row()
        self.assertEqual(row.billed_user_id, teacher.id)
        self.assertEqual(row.outcome, CallOutcome.CHARGED)
        text = " ".join(str(v) for v in row.__dict__.values())
        self.assertNotIn(str(student.id), text)
        self.assertNotIn(student.email, text)


class TheRowOfACallThatWasNotCharged(Base):
    @patch.object(AIProcessor, AI_MODEL)
    def test_a_super_admin_call_is_recorded_as_unmetered(self, ai_model):
        ai_model.return_value = reply(total=900)
        admin = self._make_user(
            UserTypes.SUPER_ADMIN, "rec-super@example.com", is_superuser=True
        )
        self.run_call(user=admin)
        row = self.only_row()
        self.assertEqual(row.outcome, CallOutcome.UNMETERED)
        self.assertEqual(row.charged_credits, 0)
        self.assertIsNone(row.task_id)
        self.assertEqual(row.total_tokens, 900)
        self.assertEqual(row.billed_user_id, admin.id)
        self.assertEqual(CreditUsageLog.objects.count(), 0)

    @patch.object(AIProcessor, AI_MODEL)
    def test_a_refusal_at_the_gate_is_recorded_with_the_estimate_and_no_usage(
        self, ai_model
    ):
        poor = self._make_teacher_with_credits(credits=10)
        with self.assertRaises(InsufficientCreditsError):
            self.run_call(user=poor)
        ai_model.assert_not_called()
        row = self.only_row()
        self.assertEqual(row.outcome, CallOutcome.REFUSED_GATE)
        self.assertIsNotNone(row.estimate)
        self.assertIsNone(row.total_tokens)
        self.assertEqual(row.charged_credits, 0)
        self.assertIsNone(row.task_id)
        self.assertEqual(CreditUsageLog.objects.count(), 0)

    @patch.object(AIProcessor, AI_MODEL)
    def test_a_refusal_before_the_estimate_has_no_estimate(self, ai_model):
        teacher = self._make_user(UserTypes.TEACHER, "rec-nosub@example.com")
        with self.assertRaises((AIFeatureNotAvailableError, InsufficientCreditsError)):
            self.run_call(user=teacher)
        ai_model.assert_not_called()
        row = self.only_row()
        self.assertEqual(row.outcome, CallOutcome.REFUSED_ACCESS)
        self.assertIsNone(row.estimate)
        self.assertIsNone(row.total_tokens)

    @patch.object(AIProcessor, AI_MODEL)
    def test_a_provider_error_is_recorded_with_its_class_and_the_estimate(
        self, ai_model
    ):
        ai_model.side_effect = TimeoutError("provider text that must not be kept")
        with self.assertRaises(TimeoutError):
            self.run_call()
        row = self.only_row()
        self.assertEqual(row.outcome, CallOutcome.PROVIDER_ERROR)
        self.assertEqual(row.error_class, "timeout")
        self.assertIsNotNone(row.estimate)
        self.assertIsNotNone(row.task_id)
        self.assertIsNone(row.total_tokens)
        self.assertEqual(row.charged_credits, 0)
        self.assertEqual(CreditUsageLog.objects.count(), 0)
        self.assertNotIn("provider text", repr(row.__dict__))

    @patch.object(AIProcessor, AI_MODEL)
    def test_a_cancellation_after_the_answer_keeps_the_usage_and_no_charge(
        self, ai_model
    ):
        armed = []

        def answer(*args, **kwargs):
            armed.append(1)
            return reply(total=640)

        ai_model.side_effect = answer

        def cancel(processing_task_id, **kwargs):
            if armed:
                raise TaskCancelledError("Task cancelled by user.")

        with patch("ai_processor.services.ensure_task_not_cancelled", cancel):
            with self.assertRaises(TaskCancelledError):
                self.run_call()
        row = self.only_row()
        self.assertEqual(row.outcome, CallOutcome.CANCELLED_AFTER_REPLY)
        self.assertEqual(row.total_tokens, 640)
        self.assertEqual(row.model_answered, "answer/model")
        self.assertEqual(row.charged_credits, 0)
        self.assertEqual(CreditUsageLog.objects.count(), 0)

    @patch.object(AIProcessor, AI_MODEL)
    def test_a_balance_that_changed_after_the_answer_is_recorded(self, ai_model):
        ai_model.return_value = reply(total=640)
        with patch.object(
            CreditWallet,
            "consume_credits",
            side_effect=InsufficientCreditsError("balance changed"),
        ):
            with self.assertRaises(InsufficientCreditsError):
                self.run_call()
        row = self.only_row()
        self.assertEqual(row.outcome, CallOutcome.BALANCE_CHANGED)
        self.assertEqual(row.total_tokens, 640)
        self.assertEqual(row.charged_credits, 0)

    @patch.object(AIProcessor, AI_MODEL)
    def test_any_other_failure_of_the_charge_is_recorded_as_charge_failed(
        self, ai_model
    ):
        ai_model.return_value = reply(total=640)
        with patch.object(
            CreditWallet, "consume_credits", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                self.run_call()
        row = self.only_row()
        self.assertEqual(row.outcome, CallOutcome.CHARGE_FAILED)
        self.assertEqual(row.error_class, "other")
        self.assertEqual(row.total_tokens, 640)
        self.assertEqual(row.charged_credits, 0)

    @patch.object(AIProcessor, AI_MODEL)
    def test_each_try_is_its_own_row(self, ai_model):
        ai_model.return_value = reply()
        self.run_call()
        self.run_call()
        self.assertEqual(AICallRecord.objects.count(), 2)
        self.assertEqual(AICallRecord.objects.values("task_id").distinct().count(), 2)

    @patch.object(AIProcessor, AI_MODEL)
    def test_a_call_that_noted_nothing_carries_nothing_over_from_the_last(
        self, ai_model
    ):
        ai_model.return_value = reply()
        self.run_call()
        teacher = self._make_user(UserTypes.TEACHER, "rec-nosub2@example.com")
        with self.assertRaises((AIFeatureNotAvailableError, InsufficientCreditsError)):
            self.run_call(user=teacher)
        refused = AICallRecord.objects.order_by("-created_at").first()
        assert refused is not None
        self.assertIsNone(refused.estimate)
        self.assertIsNone(refused.task_id)
        self.assertIsNone(refused.total_tokens)
        self.assertIsNone(call_record.current_state())


class TheCallIsUnchanged(Base):
    """The same response, the same exception object, the same money."""

    def _state(self):
        return {
            "balance": self.balance(),
            "usage": set(CreditUsageLog.objects.values_list("id", flat=True)),
            "ledger": set(CreditLedger.objects.values_list("id", flat=True)),
        }

    def _effect_of(self, before):
        after = self._state()
        new_usage = CreditUsageLog.objects.filter(
            id__in=after["usage"] - before["usage"]
        )
        return {
            "spent": before["balance"] - after["balance"],
            "usage": sorted(new_usage.values_list("amount", "feature", "task_type")),
            "ledger": len(after["ledger"] - before["ledger"]),
        }

    @patch.object(AIProcessor, AI_MODEL)
    def test_the_charge_is_the_same_with_recording_on_off_and_failing(self, ai_model):
        effects, responses = {}, {}
        for state in ("on", "off", "failing"):
            ai_model.return_value = reply(total=500)
            before = self._state()
            if state == "off":
                with override_settings(AI_CALL_RECORD_ENABLED=False):
                    responses[state] = self.run_call()
            elif state == "failing":
                with patch.object(
                    AICallRecord.objects, "create", side_effect=OperationalError("x")
                ):
                    responses[state] = self.run_call()
            else:
                responses[state] = self.run_call()
            effects[state] = self._effect_of(before)
        for state in ("off", "failing"):
            self.assertIs(responses[state], ai_model.return_value)
            self.assertEqual(effects[state], effects["on"], state)
        self.assertEqual(effects["on"]["spent"], 500)
        self.assertEqual(AICallRecord.objects.count(), 1)  # only the "on" run

    @patch.object(AIProcessor, AI_MODEL)
    def test_the_provider_exception_is_the_very_object_with_its_traceback(
        self, ai_model
    ):
        def fail(*args, **kwargs):
            raise TimeoutError("the original")

        ai_model.side_effect = fail
        with self.assertRaises(TimeoutError) as caught:
            self.run_call()
        self.assertEqual(caught.exception.args, ("the original",))
        names = []
        tb = caught.exception.__traceback__
        while tb:
            names.append(tb.tb_frame.f_code.co_name)
            tb = tb.tb_next
        self.assertIn("fail", names)
        self.assertIn("execute_graded_task", names)
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)

    @patch.object(AIProcessor, AI_MODEL)
    def test_each_exception_class_the_callers_branch_on_comes_out_unchanged(
        self, ai_model
    ):
        ai_model.return_value = reply()
        cases = {
            "InsufficientCreditsError": InsufficientCreditsError("no credits"),
            "AIFeatureNotAvailableError": AIFeatureNotAvailableError("not allowed"),
            "TaskCancelledError": TaskCancelledError("cancelled"),
            "ValueError": ValueError("bad value"),
            "RuntimeError": RuntimeError("runtime"),
            "OSError": OSError("io"),
        }
        for name, error in cases.items():
            with self.subTest(error=name):
                with patch.object(CreditWallet, "consume_credits", side_effect=error):
                    with self.assertRaises(type(error)) as caught:
                        self.run_call()
                self.assertIs(caught.exception, error)

    @patch.object(AIProcessor, AI_MODEL)
    def test_the_gate_and_access_refusals_are_the_same_with_recording_off(
        self, ai_model
    ):
        poor = self._make_teacher_with_credits(credits=10)
        nobody = self._make_user(UserTypes.TEACHER, "rec-nosub3@example.com")
        for user in (poor, nobody):
            with self.subTest(user=user.email):
                with self.assertRaises(REFUSALS) as on:
                    self.run_call(user=user)
                with override_settings(AI_CALL_RECORD_ENABLED=False):
                    with self.assertRaises(REFUSALS) as off:
                        self.run_call(user=user)
                self.assertIs(type(on.exception), type(off.exception))
                self.assertEqual(str(on.exception), str(off.exception))


class TheRecordCannotFailTheCall(Base):
    def _fails_with(self, error):
        return patch.object(AICallRecord.objects, "create", side_effect=error)

    @patch.object(AIProcessor, AI_MODEL)
    def test_a_failing_insert_of_any_database_kind_leaves_the_charge_and_return(
        self, ai_model
    ):
        for error in (
            IntegrityError("duplicate secret-row"),
            OperationalError("could not connect secret-row"),
            ProgrammingError("relation missing secret-row"),
            DataError("value too long secret-row"),
            RuntimeError("anything secret-row"),
        ):
            with self.subTest(error=type(error).__name__):
                usage_before = CreditUsageLog.objects.count()
                ai_model.return_value = reply(total=500)
                before = call_record.failure_count()
                with self._fails_with(error):
                    response = self.run_call()
                self.assertIs(response, ai_model.return_value)
                self.assertEqual(CreditUsageLog.objects.count(), usage_before + 1)
                self.assertEqual(call_record.failure_count(), before + 1)

    @patch.object(AIProcessor, AI_MODEL)
    def test_a_missing_table_is_survived_and_counted(self, ai_model):
        ai_model.return_value = reply(total=500)
        with connection.schema_editor() as editor:
            editor.delete_model(AICallRecord)
        response = self.run_call()
        self.assertIs(response, ai_model.return_value)
        self.assertEqual(CreditUsageLog.objects.count(), 1)
        self.assertEqual(call_record.failure_count(), 1)

    @patch.object(AIProcessor, AI_MODEL)
    def test_a_failure_is_logged_with_ids_and_the_class_never_the_message(
        self, ai_model
    ):
        ai_model.return_value = reply()
        with self.assertLogs("ai_processor.call_record", level="WARNING") as logs:
            with self._fails_with(IntegrityError("secret-row-value-123")):
                self.run_call()
        text = "\n".join(logs.output)
        self.assertIn("ai_call_record_failed", text)
        self.assertIn("IntegrityError", text)
        self.assertIn(str(CreditUsageLog.objects.get().task_id), text)
        self.assertNotIn("secret-row-value-123", text)

    @patch.object(AIProcessor, AI_MODEL)
    def test_a_recorder_error_in_a_failure_path_never_replaces_the_original(
        self, ai_model
    ):
        ai_model.side_effect = TimeoutError("the original")
        with self._fails_with(IntegrityError("recorder error")):
            with self.assertRaises(TimeoutError) as caught:
                self.run_call()
        self.assertEqual(caught.exception.args, ("the original",))

    @patch.object(AIProcessor, AI_MODEL)
    def test_the_time_limit_is_not_swallowed_after_a_charge_but_not_replacing_an_error(
        self, ai_model
    ):
        ai_model.return_value = reply()
        with self._fails_with(SoftTimeLimitExceeded()):
            with self.assertRaises(SoftTimeLimitExceeded):
                self.run_call()
        self.assertEqual(CreditUsageLog.objects.count(), 1)
        ai_model.side_effect = TimeoutError("the original")
        with self._fails_with(SoftTimeLimitExceeded()):
            with self.assertRaises(TimeoutError):
                self.run_call()

    @patch.object(AIProcessor, AI_MODEL)
    def test_exit_requests_are_never_swallowed(self, ai_model):
        ai_model.return_value = reply()
        for error in (SystemExit(3), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                with self._fails_with(error):
                    with self.assertRaises(type(error)):
                        self.run_call()

    @patch.object(AIProcessor, AI_MODEL)
    def test_reading_the_reply_failing_is_swallowed_too(self, ai_model):
        ai_model.return_value = reply()
        with patch.object(call_record, "usage_numbers", side_effect=RuntimeError("x")):
            response = self.run_call()
        self.assertIs(response, ai_model.return_value)
        self.assertEqual(call_record.failure_count(), 1)
        self.assertEqual(CreditUsageLog.objects.count(), 1)

    @patch.object(AIProcessor, AI_MODEL)
    def test_a_failing_insert_inside_a_callers_transaction_leaves_it_usable(
        self, ai_model
    ):
        ai_model.return_value = reply(total=500)
        with transaction.atomic():
            with self._fails_with(OperationalError("x")):
                self.run_call()
            # The caller's next query still works: the failed write was in
            # its own savepoint.
            self.assertEqual(CreditUsageLog.objects.count(), 1)
            self.run_call()
            self.assertEqual(CreditUsageLog.objects.count(), 2)

    @patch.object(AIProcessor, AI_MODEL)
    def test_a_database_error_from_the_real_insert_leaves_the_transaction_usable(
        self, ai_model
    ):
        ai_model.return_value = reply(total=500)
        with patch.object(
            call_record, "usage_numbers", return_value={"total_tokens": 10**40}
        ):
            with transaction.atomic():
                self.run_call()
                self.assertEqual(CreditUsageLog.objects.count(), 1)
        self.assertEqual(call_record.failure_count(), 1)


class TheRecordIsAfterTheChargeAndCheap(Base):
    @patch.object(AIProcessor, AI_MODEL)
    def test_the_charge_is_done_and_no_savepoint_is_open_when_the_recorder_runs(
        self, ai_model
    ):
        ai_model.return_value = reply()
        seen = {}
        real = call_record.write_row

        def spy(**fields):
            seen["depth"] = len(connection.savepoint_ids)
            seen["usage_rows"] = CreditUsageLog.objects.count()
            return real(**fields)

        entered = {}
        original = self.processor.estimate_total_token

        def estimate(*args, **kwargs):
            entered["depth"] = len(connection.savepoint_ids)
            return original(*args, **kwargs)

        with patch.object(call_record, "write_row", spy), patch.object(
            AIProcessor, "estimate_total_token", side_effect=estimate
        ):
            self.run_call()
        self.assertEqual(seen["depth"], entered["depth"])
        self.assertEqual(seen["usage_rows"], 1)

    @patch.object(AIProcessor, AI_MODEL)
    def test_the_recorder_adds_one_insert_in_one_savepoint_and_reads_nothing(
        self, ai_model
    ):
        ai_model.return_value = reply()

        def statements(enabled):
            with override_settings(AI_CALL_RECORD_ENABLED=enabled):
                with CaptureQueriesContext(connection) as captured:
                    self.run_call()
            return [q["sql"] for q in captured.captured_queries]

        statements(False)  # a warm-up: caches filled before anything is compared
        on = statements(True)
        off = statements(False)
        # a savepoint, the insert, the release: three statements and no more
        self.assertEqual(len(on) - len(off), 3, on)
        touching = [s for s in on if "ai_processor_aicallrecord" in s]
        self.assertEqual(len(touching), 1, touching)
        self.assertTrue(touching[0].lstrip().upper().startswith("INSERT"))
        self.assertFalse(any("FOR UPDATE" in s.upper() for s in on if s not in off))
        self.assertEqual(
            sum(1 for s in on if s.lstrip().upper().startswith("SAVEPOINT")),
            sum(1 for s in off if s.lstrip().upper().startswith("SAVEPOINT")) + 1,
        )

    @patch.object(AIProcessor, AI_MODEL)
    def test_the_estimate_is_computed_once_per_call_with_recording_on(self, ai_model):
        ai_model.return_value = reply()
        with patch.object(
            AIProcessor, "estimate_total_token", return_value=20000
        ) as estimate:
            self.run_call()
        self.assertEqual(estimate.call_count, 1)


class TheSwitch(Base):
    @patch.object(AIProcessor, AI_MODEL)
    def test_off_writes_no_row_and_runs_no_query(self, ai_model):
        ai_model.return_value = reply()
        with override_settings(AI_CALL_RECORD_ENABLED=False):
            with CaptureQueriesContext(connection) as captured:
                self.run_call()
        self.assertEqual(AICallRecord.objects.count(), 0)
        self.assertFalse(
            [
                q
                for q in captured.captured_queries
                if "ai_processor_aicallrecord" in q["sql"]
            ]
        )

    @patch.object(AIProcessor, AI_MODEL)
    def test_on_is_the_default(self, ai_model):
        from django.conf import settings

        self.assertTrue(settings.AI_CALL_RECORD_ENABLED)

    @patch.object(AIProcessor, AI_MODEL)
    def test_off_also_leaves_no_state_behind(self, ai_model):
        ai_model.return_value = reply()
        with override_settings(AI_CALL_RECORD_ENABLED=False):
            self.run_call()
        self.assertIsNone(call_record.current_state())


class TheStateIsPerCall(Base):
    def test_a_thread_starts_with_no_state(self):
        seen = []
        thread = threading.Thread(
            target=lambda: seen.append(call_record.current_state())
        )
        thread.start()
        thread.join()
        self.assertEqual(seen, [None])

    def test_notes_made_with_no_call_open_do_nothing(self):
        call_record.note_estimate(5)
        call_record.note_task_id("x")
        call_record.note_response(reply())
        call_record.note_billed(self.teacher)
        self.assertIsNone(call_record.current_state())


class TheCanary(Base):
    SENTINEL = "SENTINEL-4f2a9c"

    @patch.object(AIProcessor, AI_MODEL)
    def test_no_text_from_the_call_reaches_a_column_or_a_log(self, ai_model):
        self.teacher.first_name = self.SENTINEL
        self.teacher.save()
        content = f"answer text {self.SENTINEL}"
        response = reply()
        response.choices[0].message.content = content
        ai_model.return_value = response
        with self.assertLogs(level="DEBUG") as logs:
            logging.getLogger("canary").debug("start")
            self.run_call(user_prompt=f"prompt {self.SENTINEL}")
            # A call whose row cannot be written: here the recorder really logs.
            with patch.object(
                AICallRecord.objects,
                "create",
                side_effect=IntegrityError(f"{self.SENTINEL} row text"),
            ):
                self.run_call(user_prompt=f"prompt {self.SENTINEL}")
            # The sentinel comes first: a column cut at 24 characters still holds it.
            ai_model.side_effect = TimeoutError(f"{self.SENTINEL} provider says")
            with self.assertRaises(TimeoutError):
                self.run_call(user_prompt=f"prompt {self.SENTINEL}")
        for row in AICallRecord.objects.all():
            for name, value in row.__dict__.items():
                self.assertNotIn(self.SENTINEL, str(value), name)
        record_logs = [m for m in logs.output if "call_record" in m]
        self.assertTrue(
            [m for m in record_logs if "ai_call_record_failed" in m],
            "the recorder logged nothing, so the log check below would be empty",
        )
        self.assertNotIn(self.SENTINEL, " ".join(record_logs))
        self.assertEqual(AICallRecord.objects.count(), 2)


class TheRowsOutliveTheirRecords(Base):
    @patch.object(AIProcessor, AI_MODEL)
    def test_deleting_the_teacher_or_the_assignment_leaves_the_rows(self, ai_model):
        ai_model.return_value = reply()
        teacher, _student, _course, assignment = _classroom("cr3")
        with patch.object(AIProcessor, "estimate_total_token", return_value=10):
            self.run_call(user=teacher, assignment=assignment)
        before = AICallRecord.objects.count()
        assignment.delete()
        teacher.delete()
        self.assertEqual(AICallRecord.objects.count(), before)
        first = AICallRecord.objects.first()
        assert first is not None
        self.assertIsInstance(uuid.UUID(str(first.billed_user_id)), uuid.UUID)


class TheWriteOutsideATransaction(TransactionTestCase):
    def test_a_row_made_outside_a_transaction_does_not_leave_one_open(self):
        self.assertFalse(connection.in_atomic_block)
        call_record.write_row(
            task_type="other", feature="other", outcome=CallOutcome.UNMETERED
        )
        self.assertFalse(connection.in_atomic_block)
        self.assertEqual(connection.savepoint_ids, [])
        self.assertEqual(AICallRecord.objects.count(), 1)
