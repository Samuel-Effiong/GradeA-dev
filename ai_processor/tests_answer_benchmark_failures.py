"""Answer extraction under provider failure, malformed output and cancellation.

The deterministic provider (ai_processor/benchmark/answers/provider.py)
is told to fail in the ways a real provider does, on a specific call, and
the REAL pipeline is asserted to recover, fail honestly, or stop - never
to report a success it did not earn.

Every scenario here runs through `run_scenario`, i.e. the production
rasterizer, extract_answer_with_retry, chunker and merge. Page layouts in
the assertions refer to AE-016 (six pages, three per chunk: pages 1-3 and
4-6) and AE-004 (two pages, below the threshold: one call).
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from ai_processor import services
from ai_processor.benchmark.answers import SCENARIOS_BY_ID
from ai_processor.benchmark.answers.harness import (
    check_answer_content,
    check_no_duplicates,
    check_statuses,
    describe,
    run_scenario,
)
from ai_processor.benchmark.answers.provider import (
    ProviderBehaviour,
    connection_error,
    http_error,
    timeout_error,
)
from ai_processor.benchmark.answers.scenarios import _numbering
from ai_processor.extraction_schemas import (
    ANSWER_STATUSES,
    ANSWERED,
    NOT_FOUND_IN_DOCUMENT,
)
from AutoGrader.error_messages import classify_infra_error
from students.exceptions import TaskCancelledError
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
)
from students.task_tracking import cancellable_final_save
from users.models import CustomUser, UserTypes

#: Six answered pages; chunks are pages 1-3 and 4-6.
SIX_PAGES = SCENARIOS_BY_ID["AE-016"]
#: Two answered pages; below the threshold, so one extraction call.
TWO_PAGES = SCENARIOS_BY_ID["AE-004"]
#: One page, Q1 answered and Q2 deliberately blank.
WITH_A_BLANK = SCENARIOS_BY_ID["AE-002"]

CHUNK_ONE = (1, 2, 3)
CHUNK_TWO = (4, 5, 6)


def gate_for(scenario):
    """
    An assignment stand-in carrying the declared questions, which switches
    on the answer-completeness gate. No `course`, so no roster query.
    """
    return SimpleNamespace(questions=list(scenario.questions))


def pages_per_call(run):
    return [call.pages_sent for call in run.provider.extraction_calls]


def failures_on(index):
    """Every transient failure kind, injected on call `index` only."""
    return {
        "timeout": ProviderBehaviour(raise_on={index: timeout_error()}),
        "connection-error": ProviderBehaviour(raise_on={index: connection_error()}),
        "http-429": ProviderBehaviour(raise_on={index: http_error(429)}),
        "http-500": ProviderBehaviour(raise_on={index: http_error(500)}),
        "malformed-json": ProviderBehaviour(raw_on={index: "{not json"}),
        "empty-response": ProviderBehaviour(raw_on={index: ""}),
        "truncated-json": ProviderBehaviour(raw_on={index: '{"answers": ['}),
    }


class TransientFailureRecoveryTest(SimpleTestCase):
    """A single failed attempt is retried, and the answer appears exactly once."""

    maxDiff = None

    def test_one_failed_attempt_on_either_chunk_recovers_completely(self):
        cases = (
            ("chunk 1", 1, [CHUNK_ONE, CHUNK_ONE, CHUNK_TWO]),
            ("chunk 2", 2, [CHUNK_ONE, CHUNK_TWO, CHUNK_TWO]),
        )
        for chunk, index, expected_calls in cases:
            for kind, behaviour in failures_on(index).items():
                with self.subTest(chunk=chunk, failure=kind):
                    run = run_scenario(SIX_PAGES, behaviour=behaviour)
                    self.assertIsNone(run.error, describe(run, SIX_PAGES, []))
                    problems = (
                        check_statuses(run, SIX_PAGES)
                        + check_answer_content(run, SIX_PAGES)
                        + check_no_duplicates(run)
                    )
                    self.assertEqual(problems, [], describe(run, SIX_PAGES, problems))
                    # Only the failed chunk is repeated - not the one before it.
                    self.assertEqual(pages_per_call(run), expected_calls)

    def test_one_failed_attempt_on_the_single_call_path_recovers(self):
        for kind, behaviour in failures_on(1).items():
            with self.subTest(failure=kind):
                run = run_scenario(TWO_PAGES, behaviour=behaviour)
                self.assertIsNone(run.error, describe(run, TWO_PAGES, []))
                self.assertEqual(check_statuses(run, TWO_PAGES), [])
                self.assertEqual(len(run.provider.extraction_calls), 2)


class PersistentChunkFailureTest(SimpleTestCase):
    """
    A chunk that never succeeds must fail the extraction.

    The alternative - returning the answers from the chunks that did work -
    would present Q4-Q6 as absent when they were simply never read, which
    is the misleading partial result the whole benchmark exists to rule out.
    """

    def setUp(self):
        def fail_second_chunk(call):
            if call.pages_sent[:1] == (4,):
                raise timeout_error()

        self.outcome = run_scenario(
            SIX_PAGES, behaviour=ProviderBehaviour(on_call=fail_second_chunk)
        )

    def test_no_partial_result_is_returned(self):
        self.assertIsNone(self.outcome.result)
        self.assertIsNotNone(self.outcome.error)
        self.assertIn("attempts failed", str(self.outcome.error))

    def test_the_real_cause_reaches_the_user_facing_message(self):
        message = classify_infra_error(self.outcome.error)
        self.assertIsNotNone(message)
        self.assertIn("timed out", message)

    def test_the_retry_policy_is_pinned(self):
        """
        Three outer attempts, each re-reading chunk 1 once and trying
        chunk 2 three times: twelve calls. Chunk 1 is therefore paid for
        three times over for a result that is then thrown away - recorded
        as a known cost of the current policy (see README.md, "Known
        limitations"), and pinned so any change to it is deliberate.
        """
        calls = pages_per_call(self.outcome)
        self.assertEqual(len(calls), 12)
        self.assertEqual(calls.count(CHUNK_ONE), 3)
        self.assertEqual(calls.count(CHUNK_TWO), 9)


class MalformedModelOutputTest(SimpleTestCase):
    """
    Structurally valid JSON that breaks the extraction contract, with the
    completeness gate switched on as it is in production.
    """

    maxDiff = None

    def _run(self, scenario, **behaviour):
        return run_scenario(
            scenario,
            behaviour=ProviderBehaviour(**behaviour),
            assignment_model=gate_for(scenario),
        )

    def test_an_omitted_question_becomes_not_found_never_blank(self):
        for scenario, chunks in ((TWO_PAGES, 1), (SIX_PAGES, 2)):
            with self.subTest(scenario=scenario.id):
                run = self._run(scenario, omit_questions=("Q2",))
                self.assertIsNone(run.error, describe(run, scenario, []))
                self.assertEqual(run.status_by_question["Q2"], NOT_FOUND_IN_DOCUMENT)
                self.assertEqual(run.html_by_question["Q2"], "")
                # Strict on the first two attempts, repaired on the last.
                self.assertEqual(len(run.provider.extraction_calls), 3 * chunks)

    def test_a_duplicated_entry_appears_once(self):
        for scenario in (TWO_PAGES, SIX_PAGES):
            with self.subTest(scenario=scenario.id):
                run = self._run(scenario, duplicate_questions=("Q2",))
                self.assertIsNone(run.error)
                self.assertEqual(check_no_duplicates(run), [])
                self.assertEqual(run.status_by_question["Q2"], ANSWERED)

    def test_an_answer_for_a_question_the_assignment_lacks_is_dropped(self):
        for scenario in (TWO_PAGES, SIX_PAGES):
            with self.subTest(scenario=scenario.id):
                run = self._run(scenario, inject_unknown_question="Q99")
                self.assertIsNone(run.error)
                self.assertNotIn("Q99", run.status_by_question)
                self.assertEqual(check_statuses(run, scenario), [])

    def test_an_invalid_or_missing_status_is_re_derived_from_the_text(self):
        for label, behaviour in (
            ("wrong enum", {"bad_status": "MAYBE"}),
            ("missing field", {"drop_field": "answer_status"}),
        ):
            for scenario in (TWO_PAGES, SIX_PAGES):
                with self.subTest(case=label, scenario=scenario.id):
                    run = self._run(scenario, **behaviour)
                    self.assertIsNone(run.error)
                    for status in run.status_by_question.values():
                        self.assertIn(status, ANSWER_STATUSES)
                    self.assertEqual(check_statuses(run, scenario), [])

    def test_a_null_answer_is_never_scored_as_a_blank(self):
        """
        Regression 6. Every provider call succeeded, but each ANSWERED
        entry arrived with answer_html null. That is a self-contradiction,
        not evidence of a blank: it must end in review, never a zero.
        """
        for scenario in (TWO_PAGES, SIX_PAGES):
            with self.subTest(scenario=scenario.id):
                run = self._run(scenario, null_answer_html=True)
                self.assertIsNone(run.error, describe(run, scenario, []))
                self.assertEqual(
                    set(run.status_by_question.values()), {NOT_FOUND_IN_DOCUMENT}
                )

    def test_result_order_follows_the_assignment_not_the_provider(self):
        for scenario in (TWO_PAGES, SIX_PAGES):
            with self.subTest(scenario=scenario.id):
                run = self._run(scenario, reverse_order=True)
                declared = [str(q["question_number"]) for q in scenario.questions]
                self.assertEqual(run.question_order, declared)

    def test_without_the_gate_only_the_chunked_path_sorts(self):
        """
        The documented contract where no assignment questions are known:
        the chunked merge sorts by question number; the single call keeps
        the provider's order. Pinned so the difference is never accidental.
        """
        chunked = run_scenario(
            SIX_PAGES, behaviour=ProviderBehaviour(reverse_order=True)
        )
        self.assertEqual(chunked.question_order, [f"Q{n}" for n in range(1, 7)])
        single = run_scenario(
            TWO_PAGES, behaviour=ProviderBehaviour(reverse_order=True)
        )
        self.assertEqual(single.question_order, ["Q2", "Q1"])


class QuestionNumberStyleTest(SimpleTestCase):
    """
    Live AE-912 and AE-921: the model wrote `1` in some chunks and `Q1` in
    others for the same question. The merge split them, the completeness
    gate rejected the result as numbering drift, and the whole script was
    re-read and re-billed up to three times - 12 calls for a 4-chunk script.
    """

    maxDiff = None

    def _run(self, scenario, **behaviour):
        return run_scenario(
            scenario,
            behaviour=ProviderBehaviour(**behaviour),
            assignment_model=gate_for(scenario),
        )

    def test_switching_style_between_chunks_costs_no_reread(self):
        run = self._run(SIX_PAGES, label_style={1: "bare"})
        self.assertIsNone(run.error, describe(run, SIX_PAGES, []))
        self.assertEqual(pages_per_call(run), [CHUNK_ONE, CHUNK_TWO])
        self.assertEqual(run.question_order, [f"Q{n}" for n in range(1, 7)])
        self.assertEqual(check_statuses(run, SIX_PAGES), [])

    def test_a_single_call_in_bare_numbers_keeps_every_answer(self):
        """
        Before the fix the final attempt dropped both real answers as
        numbering drift and put NOT_FOUND placeholders in their place.
        """
        run = self._run(TWO_PAGES, label_style={1: "bare"})
        self.assertIsNone(run.error, describe(run, TWO_PAGES, []))
        self.assertEqual(len(run.provider.extraction_calls), 1)
        self.assertEqual(check_statuses(run, TWO_PAGES), [])
        self.assertEqual(check_answer_content(run, TWO_PAGES), [])

    def test_an_integer_numbered_assignment_accepts_q_prefixed_answers(self):
        """Extracted assignments usually number questions 1, 2, 3."""
        scenario = _numbering(
            "AE-T920",
            "integer-numbered-assignment",
            (1, 2, 3, 4),
            "Integer labels, with the second chunk answering as Q3 and Q4.",
        )
        run = self._run(scenario, label_style={2: "prefixed"})
        self.assertIsNone(run.error, describe(run, scenario, []))
        self.assertEqual(len(run.provider.extraction_calls), 2)
        self.assertEqual(check_statuses(run, scenario), [])
        self.assertEqual(check_no_duplicates(run), [])


class BlankReReadTest(SimpleTestCase):
    def test_writing_found_on_re_read_moves_blank_to_review_without_inventing_text(
        self,
    ):
        run = run_scenario(
            WITH_A_BLANK,
            behaviour=ProviderBehaviour(blank_verification_finds=("Q2",)),
        )
        self.assertIsNone(run.error)
        self.assertEqual(len(run.provider.verification_calls), 1)
        self.assertEqual(run.status_by_question["Q2"], NOT_FOUND_IN_DOCUMENT)
        self.assertEqual(run.html_by_question["Q2"], "")
        self.assertEqual(run.status_by_question["Q1"], ANSWERED)


class CancellationTest(TestCase):
    """
    Cancellation at every point in the run, against a real tracked task.

    The call counts are the billing invariant: every provider call in the
    real pipeline is a billed call, so "no call after the cancellation" is
    the same statement as "no charge after the cancellation".
    """

    def setUp(self):
        teacher = CustomUser.objects.create_user(
            email="answer-benchmark-cancel@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Cancel",
            last_name="Teacher",
        )
        self.task = BackgroundProcessingTask.objects.create(
            requested_by=teacher,
            task_type=BackgroundTaskType.ANSWER_EXTRACTION,
            status=BackgroundTaskStatus.STARTED,
        )

    def cancel(self):
        BackgroundProcessingTask.objects.filter(id=self.task.id).update(
            status=BackgroundTaskStatus.CANCELLED
        )

    def cancel_and_raise(self, call):
        """
        What execute_graded_task does when the task is cancelled while its
        call is in flight: its own post-response check raises before the
        credits are consumed.
        """
        self.cancel()
        raise TaskCancelledError("Task cancelled by user.")

    def run_tracked(self, scenario, **kwargs):
        return run_scenario(scenario, processing_task_id=self.task.id, **kwargs)

    def assertStoppedAfter(self, run, calls):
        self.assertIsInstance(run.error, TaskCancelledError, repr(run.error))
        self.assertIsNone(run.result)
        self.assertEqual(run.provider.call_count, calls)

    def test_before_the_first_chunk(self):
        self.cancel()
        self.assertStoppedAfter(self.run_tracked(SIX_PAGES), calls=0)

    def test_during_the_first_chunk(self):
        def on_call(call):
            if call.index == 1:
                self.cancel_and_raise(call)

        run = self.run_tracked(SIX_PAGES, behaviour=ProviderBehaviour(on_call=on_call))
        self.assertStoppedAfter(run, calls=1)

    def test_during_the_only_call_of_a_short_submission(self):
        run = self.run_tracked(
            TWO_PAGES, behaviour=ProviderBehaviour(on_call=self.cancel_and_raise)
        )
        self.assertStoppedAfter(run, calls=1)

    def test_while_a_chunk_is_in_flight_the_pipeline_checks_on_return(self):
        """No help from the provider: the pipeline's own check must stop it."""

        def on_call(call):
            if call.index == 1:
                self.cancel()

        run = self.run_tracked(SIX_PAGES, behaviour=ProviderBehaviour(on_call=on_call))
        self.assertStoppedAfter(run, calls=1)

    def test_between_chunks(self):
        original = services.AIProcessor._build_answer_chunk_note
        cancel = self.cancel

        def note_then_cancel(processor, **kwargs):
            if kwargs["chunk_index"] == 1:
                cancel()
            return original(processor, **kwargs)

        with patch.object(
            services.AIProcessor, "_build_answer_chunk_note", note_then_cancel
        ):
            run = self.run_tracked(SIX_PAGES)
        self.assertStoppedAfter(run, calls=1)

    def test_during_the_final_chunk(self):
        def on_call(call):
            if call.pages_sent[:1] == (4,):
                self.cancel_and_raise(call)

        run = self.run_tracked(SIX_PAGES, behaviour=ProviderBehaviour(on_call=on_call))
        self.assertStoppedAfter(run, calls=2)

    def test_after_extraction_the_final_save_refuses(self):
        """
        Extraction finished before the cancellation arrived. The result
        exists, but the persistence guard every caller wraps its save in
        must refuse to commit it.
        """
        run = self.run_tracked(SIX_PAGES)
        self.assertIsNone(run.error)
        self.assertIsNotNone(run.result)
        self.cancel()
        with self.assertRaises(TaskCancelledError):
            with cancellable_final_save(self.task.id):
                pass
