"""BE-B-09, the gate half: a quote-launched item carries the figure the quote
showed, and the gate (execute_graded_task) counts `upload_quote_under` when
its own figure for a call is above it. The check never raises or blocks."""

import importlib
from typing import Any
from unittest.mock import patch

from audit import metrics
from students.models import BackgroundProcessingTask, BackgroundTaskType
from students.tests_upload_quotes_confirm import Base, figures, make_quote

quotes: Any = importlib.import_module("students.upload_quotes")


def item(teacher, meta):
    return BackgroundProcessingTask.objects.create(
        requested_by=teacher,
        task_type=BackgroundTaskType.BATCH_ANSWER_UPLOAD,
        meta=meta,
    )


class Handover(Base):
    def test_confirm_records_the_quoted_figure_on_each_item(self):
        quote = make_quote(self.teacher, self.assignment)
        expected = figures(quote)
        self.confirm(quote)
        items = BackgroundProcessingTask.objects.order_by("item_index")
        self.assertEqual([i.meta["quoted_credits"] for i in items], expected)


class Gate(Base):
    def counted(self, quoted, gate, **kw):
        row = item(self.teacher, {"quoted_credits": quoted} if quoted else {})
        with patch.object(metrics, "count") as count:
            quotes.note_gate_for_task(kw.get("id", row.id), gate)
        return [c.args[0] for c in count.call_args_list]

    def test_a_gate_figure_above_the_quote_counts(self):
        self.assertEqual(self.counted(1000, 1001), ["upload_quote_under"])

    def test_at_or_below_counts_nothing(self):
        self.assertEqual(self.counted(1000, 1000), [])
        self.assertEqual(self.counted(1000, 900), [])

    def test_an_item_with_no_quote_or_an_unknown_item_counts_nothing(self):
        self.assertEqual(self.counted(None, 5000), [])
        self.assertEqual(self.counted(1000, 5000, id="no-such-id"), [])
        self.assertEqual(self.counted(1000, 5000, id=None), [])

    def test_a_failure_is_counted_and_never_raised(self):
        row = item(self.teacher, {"quoted_credits": 10})
        with patch.object(metrics, "count") as count, patch.object(
            BackgroundProcessingTask.objects,
            "filter",
            side_effect=RuntimeError("db down"),
        ):
            quotes.note_gate_for_task(row.id, 99)
        self.assertEqual(
            [c.args[0] for c in count.call_args_list], ["upload_quote_gate_note_failed"]
        )

    def test_the_gate_calls_it_with_its_own_figure(self):
        from ai_processor.services import AIProcessor

        row = item(self.teacher, {"quoted_credits": 1})
        seen = []
        with patch.object(
            quotes, "note_gate_for_task", side_effect=lambda *a: seen.append(a)
        ), patch.object(
            AIProcessor, "_AIProcessor__ai_model", return_value={"choices": []}
        ), patch.object(
            AIProcessor, "estimate_messages_cost", return_value=777
        ):
            try:
                AIProcessor().execute_graded_task(
                    user=self.teacher,
                    feature="Grading Assignment",
                    task_type="extract_answer",
                    system_prompt="s",
                    user_prompt="u",
                    processing_task_id=str(row.id),
                    prompt_version="v1",
                )
            except Exception:  # noqa: BLE001 - only the call to the note matters
                pass
        self.assertEqual(seen, [(str(row.id), 777)])
