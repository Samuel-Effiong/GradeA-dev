"""BE-I-04 slice C: the label through the real entry points, and when
things go wrong.

`tests_grading_label_written` holds the save itself. This module goes
through the callers and the awkward cases the Senior Manager named
(2026-10-06):

  * the background task and the immediate route, each read back from the
    STORED audit entry and from the six columns, which must agree: the
    audit entry is emitted by those two callers, so they are where the
    lists could fail to reach it;
  * the formatting job, run for real with its AI call replaced: a score
    and a label saved while that call is in flight are not written back
    over when the job saves;
  * a re-grade that FAILS leaves the first score with the first label; a
    re-grade that SUCCEEDS replaces the whole label;
  * a submission created and graded on the same in-memory instance;
  * an audit failure does not fail the grade, and the label is on the row.

The provider is never called. Every model name in a stand-in is a real
string, or None on purpose (rule 14).
"""

from contextlib import contextmanager
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from ai_processor import services as ai_services
from assignments.tasks import format_grade, grade_engine_async
from assignments.tests_grading_audit_events import (
    make_classroom,
    make_submission,
    valid_grading_result,
)
from audit.enums import AuditAction
from audit.models import AuditEvent
from students import grading_label
from students.exceptions import TaskCancelledError
from students.grading_label import LABEL_FIELDS, UNLABELLED
from students.models import BackgroundProcessingTask, StudentSubmission
from students.services import grade_engine

MAIN = ai_services.MAIN_MODEL
BACKUP = ai_services.GRADING_FALLBACK_MODELS[0]
TABLE = StudentSubmission._meta.db_table


def _a_grading_that_kept(*kept):
    def grade(*args, **kwargs):
        run = kwargs["run"]
        for method, arguments in kept:
            getattr(run, method)(*arguments)
        return valid_grading_result()

    return grade


def _row(submission, *columns):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT %s FROM %s WHERE id = %%s" % (", ".join(columns), TABLE),
            [submission.pk],
        )
        return dict(zip(columns, cursor.fetchone(), strict=True))


def _label(submission):
    return _row(submission, *LABEL_FIELDS)


class _Stubs(TestCase):
    """The follow-up dispatches and the grading service, replaced. It has
    no test of its own."""

    def start_stubs(self):
        for target in (
            "students.services.student_summary_async",
            "students.services.launch_processing_task",
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("students.services.ai_processor")
        self.ai = patcher.start()
        self.addCleanup(patcher.stop)

    def keeping(self, *kept):
        self.ai.extract_grade_with_retry.side_effect = _a_grading_that_kept(*kept)

    def assert_entry_agrees_with_the_row(self, submission, served, fresh, reused=()):
        events = AuditEvent.objects.filter(
            action=AuditAction.GRADING_COMPLETED, target_id=submission.id
        )
        self.assertEqual(events.count(), 1)
        metadata = events.get().metadata
        label = _label(submission)
        self.assertNotIn(UNLABELLED, label.values())
        self.assertEqual(metadata["models_served"], sorted(served))
        self.assertEqual(metadata["models_reused"], sorted(reused))
        self.assertEqual(metadata["models_second_opinion"], [])
        self.assertEqual(metadata["fresh_backup_used"], fresh)
        self.assertEqual(
            metadata["grading_config_version"], label["grading_config_version"]
        )
        self.assertEqual(metadata["strictness"], label["grading_strictness"])
        self.assertEqual(metadata["prompt_version"], label["grading_prompt_version"])
        self.assertEqual(metadata["model"], label["grading_model"])
        return label


class TheBackgroundTaskTest(_Stubs, TestCase):
    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = make_classroom(
            "label-background"
        )
        self.submission = make_submission(self.assignment, self.student)
        self.processing_task = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            assignment=self.assignment,
            submission=self.submission,
            task_type="submission_grading",
        )
        self.start_stubs()

    def run_task(self):
        with self.captureOnCommitCallbacks(execute=True):
            return grade_engine_async.apply(
                args=(str(self.teacher.id), str(self.submission.id)),
                kwargs={"processing_task_id": str(self.processing_task.id)},
            )

    def test_the_stored_entry_and_the_six_columns_agree(self):
        self.keeping(
            ("keep_answers", (MAIN, 2)),
            ("keep_call", (BACKUP,)),
            ("keep_reused", (MAIN,)),
        )
        outcome = self.run_task()
        self.assertTrue(outcome.successful(), outcome.result)
        label = self.assert_entry_agrees_with_the_row(
            self.submission, served=[BACKUP, MAIN], fresh="yes", reused=[MAIN]
        )
        self.assertEqual(label["grading_model"], MAIN)
        self.assertEqual(label["grading_fallback_used"], grading_label.FALLBACK_YES)

    def test_an_audit_failure_does_not_fail_the_grade_and_the_label_is_on_the_row(
        self,
    ):
        self.keeping(("keep_answers", (MAIN, 1)))
        with patch.object(
            AuditEvent.objects, "create", side_effect=RuntimeError("audit store down")
        ):
            outcome = self.run_task()
        self.assertTrue(outcome.successful(), outcome.result)
        self.assertEqual(
            AuditEvent.objects.filter(action=AuditAction.GRADING_COMPLETED).count(), 0
        )
        row = _row(self.submission, "score", *LABEL_FIELDS)
        self.assertEqual(float(row["score"]), 8.0)
        self.assertEqual(row["grading_model"], MAIN)
        self.assertNotIn(UNLABELLED, [row[name] for name in LABEL_FIELDS])


class TheImmediateRouteTest(_Stubs, APITestCase):
    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = make_classroom(
            "label-immediate"
        )
        self.submission = make_submission(self.assignment, self.student)
        self.url = reverse(
            "student-submission-grade", kwargs={"pk": self.submission.pk}
        )
        self.client.force_authenticate(user=self.teacher)
        self.start_stubs()

    def test_the_stored_entry_and_the_six_columns_agree(self):
        self.keeping(("keep_answers", (BACKUP, 1)))
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200, response.content)
        label = self.assert_entry_agrees_with_the_row(
            self.submission, served=[BACKUP], fresh="yes"
        )
        self.assertEqual(label["grading_model"], BACKUP)
        self.assertEqual(label["grading_fallback_used"], grading_label.FALLBACK_YES)

    def test_the_response_does_not_carry_the_label(self):
        self.keeping(("keep_answers", (MAIN, 1)))
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.content.decode()
        for name in LABEL_FIELDS:
            with self.subTest(name=name):
                self.assertNotIn(name, body)


class _EngineCase(_Stubs, TestCase):
    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = make_classroom(
            "label-engine"
        )
        self.submission = make_submission(self.assignment, self.student)
        self.start_stubs()

    def grade(self, *kept):
        self.keeping(*kept)
        with self.captureOnCommitCallbacks(execute=True):
            return grade_engine(self.teacher, self.submission)

    def ready_for_another_grading(self):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            grading_state="IDLE"
        )
        self.submission.refresh_from_db()


class AReGradeThatFailsTest(_EngineCase):
    """The first score and the first label stay together."""

    def first_grading(self):
        self.grade(("keep_answers", (BACKUP, 1)))
        first = _row(self.submission, "score", *LABEL_FIELDS)
        self.assertEqual(first["grading_model"], BACKUP)
        self.ready_for_another_grading()
        return first

    def test_a_provider_failure_leaves_the_first_score_with_the_first_label(self):
        first = self.first_grading()
        self.ai.extract_grade_with_retry.side_effect = RuntimeError("provider down")
        with self.assertRaises(RuntimeError):
            grade_engine(self.teacher, self.submission)
        self.assertEqual(_row(self.submission, "score", *LABEL_FIELDS), first)

    def test_a_cancel_at_the_final_save_leaves_the_first_score_with_the_first_label(
        self,
    ):
        first = self.first_grading()
        self.ai.extract_grade_with_retry.side_effect = _a_grading_that_kept(
            ("keep_answers", (MAIN, 1))
        )

        @contextmanager
        def cancelled(*args, **kwargs):
            raise TaskCancelledError("cancelled at the final save")
            yield  # pragma: no cover

        with patch("students.services.cancellable_final_save", cancelled):
            with self.assertRaises(TaskCancelledError):
                grade_engine(self.teacher, self.submission)
        self.assertEqual(_row(self.submission, "score", *LABEL_FIELDS), first)


class AReGradeThatSucceedsTest(_EngineCase):
    """It replaces the whole label, not part of it."""

    def test_a_backup_grading_then_an_all_main_one_reads_no(self):
        self.grade(("keep_answers", (MAIN, 1)), ("keep_call", (BACKUP,)))
        self.assertEqual(
            _label(self.submission)["grading_fallback_used"], grading_label.FALLBACK_YES
        )
        self.ready_for_another_grading()
        self.grade(("keep_answers", (MAIN, 1)), ("keep_call", (MAIN,)))
        label = _label(self.submission)
        self.assertEqual(label["grading_model"], MAIN)
        self.assertEqual(label["grading_fallback_used"], grading_label.FALLBACK_NO)

    def test_an_ai_grading_then_a_fixed_rule_one_reads_deterministic(self):
        self.grade(("keep_answers", (BACKUP, 1)))
        self.ready_for_another_grading()
        self.grade()
        label = _label(self.submission)
        self.assertEqual(label["grading_model"], grading_label.MODEL_DETERMINISTIC)
        self.assertEqual(
            label["grading_fallback_used"], grading_label.FALLBACK_NOT_APPLICABLE
        )
        self.assertNotIn(BACKUP, label.values())


class CreatedAndGradedOnTheSameInstanceTest(_Stubs, TestCase):
    """A row made by `objects.create` and graded without being read back:
    no database-default placeholder object may reach the save or the
    label."""

    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = make_classroom(
            "label-same-instance"
        )
        self.start_stubs()

    def test_the_label_columns_are_text_before_and_after_the_grading(self):
        submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers=[{"question_number": 1, "answer_html": "An answer."}],
        )
        for name in LABEL_FIELDS:
            with self.subTest(when="before", name=name):
                self.assertEqual(getattr(submission, name), UNLABELLED)
        self.keeping(("keep_answers", (MAIN, 1)))
        with self.captureOnCommitCallbacks(execute=True):
            graded = grade_engine(self.teacher, submission)
        for name in LABEL_FIELDS:
            with self.subTest(when="after", name=name):
                self.assertIsInstance(getattr(graded, name), str)
        self.assertEqual({n: getattr(graded, n) for n in LABEL_FIELDS}, _label(graded))
        self.assertEqual(_label(graded)["grading_model"], MAIN)


class TheFormattingJobRunTest(_Stubs, TestCase):
    """The job reads the row, makes a slow AI call, then saves. A grading
    that lands during that call must not be written back over."""

    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = make_classroom(
            "label-format-job"
        )
        self.submission = make_submission(self.assignment, self.student)
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            score=5,
            max_points=10,
            score_percentage=50,
            feedback=valid_grading_result(5),
        )

    def test_a_score_and_label_saved_meanwhile_are_not_written_back_over(self):
        meanwhile = {name: f"meanwhile-{i}" for i, name in enumerate(LABEL_FIELDS)}

        def a_slow_call_during_which_a_grading_lands(*args, **kwargs):
            StudentSubmission.objects.filter(pk=self.submission.pk).update(
                score=9, score_percentage=90, **meanwhile
            )
            return "<p>Formatted grade.</p>"

        with patch("assignments.tasks.ai_processor") as formatter:
            formatter.formatted_grade.side_effect = (
                a_slow_call_during_which_a_grading_lands
            )
            outcome = format_grade.apply(
                args=(str(self.submission.id), "Format this grade.")
            )
        self.assertTrue(outcome.successful(), outcome.result)
        self.assertEqual(formatter.formatted_grade.call_count, 1)
        row = _row(self.submission, "score", "formatted_grade", *LABEL_FIELDS)
        self.assertEqual(float(row["score"]), 9.0)
        self.assertEqual({n: row[n] for n in LABEL_FIELDS}, meanwhile)
        self.assertTrue(row["formatted_grade"])
