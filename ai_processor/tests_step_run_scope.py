"""AI-call record, slice 0: the scope that carries a `StepRun` to the
provider call.

The seam rule (Senior Manager, 8 October 2026): no slice edits
`AIProcessor.execute_graded_task`. The two marks ("the call left", "a reply
was received") are made where the call leaves, in `__ai_model`. A StepRun
reaches it through a context variable the step's own function opens around
its call (`with run.scope():`), the idiom `billing_refund_scope` uses.

The conditions the Senior Manager set:
  * a call made with NO scope open fails loudly for a task type that needs
    one, never a silent unlabelled step;
  * the scope is restored after an exception;
  * it does not leak between two steps of one run, or between two Celery
    tasks on one worker.

Slice 0 only provides the machinery; `ENFORCED_STEP_TASK_TYPES` is where
each later slice switches a task type on, once all of that task type's
callers open the scope. Nothing here touches the provider or the database.
"""

import contextvars
import threading

from django.test import SimpleTestCase

from ai_processor import step_run
from ai_processor.step_run import (
    ENFORCED_STEP_TASK_TYPES,
    STEP_TASK_TYPES,
    StepRun,
    StepScopeMissingError,
    current_step_run,
    require_step_scope,
)


class TheScopeTest(SimpleTestCase):
    def test_no_scope_is_open_by_default(self):
        self.assertIsNone(current_step_run())

    def test_inside_the_scope_the_run_is_current(self):
        run = StepRun("answers")
        with run.scope():
            self.assertIs(current_step_run(), run)

    def test_after_the_scope_nothing_is_current(self):
        with StepRun("answers").scope():
            pass
        self.assertIsNone(current_step_run())

    def test_the_scope_is_restored_after_an_exception(self):
        run = StepRun("answers")
        with self.assertRaises(ValueError):
            with run.scope():
                raise ValueError("boom")
        self.assertIsNone(current_step_run())

    def test_the_scope_is_restored_after_an_exception_that_is_not_an_exception(self):
        # BaseException (a Celery time limit, KeyboardInterrupt) unwinds too.
        run = StepRun("answers")
        with self.assertRaises(KeyboardInterrupt):
            with run.scope():
                raise KeyboardInterrupt
        self.assertIsNone(current_step_run())

    def test_a_nested_scope_restores_the_outer_one(self):
        outer, inner = StepRun("answers"), StepRun("recheck")
        with outer.scope():
            with inner.scope():
                self.assertIs(current_step_run(), inner)
            self.assertIs(current_step_run(), outer)
        self.assertIsNone(current_step_run())

    def test_an_exception_in_the_inner_scope_restores_the_outer_one(self):
        outer, inner = StepRun("answers"), StepRun("recheck")
        with outer.scope():
            with self.assertRaises(ValueError):
                with inner.scope():
                    raise ValueError
            self.assertIs(current_step_run(), outer)

    def test_two_steps_one_after_the_other_do_not_leak(self):
        first, second = StepRun("answers"), StepRun("recheck")
        with first.scope():
            first.keep("a/model", 1)
        self.assertIsNone(current_step_run())
        with second.scope():
            self.assertIs(current_step_run(), second)
            self.assertEqual(dict(second.votes()), {})
        self.assertIsNone(current_step_run())

    def test_the_same_run_can_be_entered_again(self):
        run = StepRun("answers")
        with run.scope():
            pass
        with run.scope():
            self.assertIs(current_step_run(), run)
        self.assertIsNone(current_step_run())

    def test_another_thread_does_not_see_this_threads_scope(self):
        seen = []
        with StepRun("answers").scope():
            thread = threading.Thread(target=lambda: seen.append(current_step_run()))
            thread.start()
            thread.join()
        self.assertEqual(seen, [None])

    def test_a_task_that_starts_in_a_fresh_context_does_not_see_a_scope(self):
        # What a second Celery task on the same worker process is: the same
        # thread, a new context. A scope that was left open (a bug) in the
        # first must not be seen by the second.
        run = StepRun("answers")
        token_holder = {}

        def first_task():
            token_holder["token"] = step_run._CURRENT.set(run)  # left open

        contextvars.Context().run(first_task)
        second = contextvars.Context().run(current_step_run)
        self.assertIsNone(second)

    def test_a_scope_opened_in_one_context_is_not_seen_in_a_sibling(self):
        run = StepRun("answers")
        seen = []

        def task_a():
            with run.scope():
                seen.append(("a", current_step_run()))

        def task_b():
            seen.append(("b", current_step_run()))

        contextvars.copy_context().run(task_a)
        contextvars.copy_context().run(task_b)
        self.assertEqual(seen, [("a", run), ("b", None)])


class ACallWithNoScopeFailsLoudlyTest(SimpleTestCase):
    def test_a_task_type_that_needs_a_scope_raises_when_none_is_open(self):
        with self.assertRaises(StepScopeMissingError) as caught:
            require_step_scope("extract_answer", enforced=frozenset({"extract_answer"}))
        self.assertIn("extract_answer", str(caught.exception))

    def test_it_passes_when_a_scope_is_open(self):
        run = StepRun("answers")
        with run.scope():
            self.assertIs(
                require_step_scope(
                    "extract_answer", enforced=frozenset({"extract_answer"})
                ),
                run,
            )

    def test_a_task_type_that_is_not_enforced_passes_without_a_scope(self):
        self.assertIsNone(
            require_step_scope(
                "grade_assignment", enforced=frozenset({"extract_answer"})
            )
        )

    def test_a_task_type_that_is_not_enforced_returns_the_open_run_if_any(self):
        run = StepRun("answers")
        with run.scope():
            self.assertIs(
                require_step_scope("summary", enforced=frozenset({"extract_answer"})),
                run,
            )

    def test_the_error_is_a_runtime_error_with_a_fixed_phrase(self):
        self.assertTrue(issubclass(StepScopeMissingError, RuntimeError))
        try:
            require_step_scope("extract_answer", enforced=frozenset({"extract_answer"}))
        except StepScopeMissingError as error:
            self.assertNotIn("Traceback", str(error))
            self.assertLess(len(str(error)), 200)

    def test_the_default_set_is_a_subset_of_the_known_step_task_types(self):
        self.assertIsInstance(ENFORCED_STEP_TASK_TYPES, frozenset)
        self.assertTrue(ENFORCED_STEP_TASK_TYPES <= STEP_TASK_TYPES)

    def test_the_known_step_task_types_are_the_ones_of_the_design_note(self):
        self.assertEqual(
            STEP_TASK_TYPES,
            frozenset(
                {
                    "extract_assignment",
                    "generate_assignment",
                    "extract_answer",
                    "formatted_grade",
                }
            ),
        )

    def test_grading_is_not_a_step_task_type(self):
        # Grading travels by an explicit `run=` (BE-I-04's GradingRun).
        self.assertNotIn("grade_assignment", STEP_TASK_TYPES)

    def test_in_slice_0_nothing_is_enforced_yet(self):
        # Slice 0 changes no behaviour: a later slice switches a task type
        # on, in its own tests-first commit, once every caller of that task
        # type opens the scope.
        self.assertEqual(ENFORCED_STEP_TASK_TYPES, frozenset())
        for task_type in sorted(STEP_TASK_TYPES):
            with self.subTest(task_type=task_type):
                self.assertIsNone(require_step_scope(task_type))
