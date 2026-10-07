"""A formatting task that has been overtaken writes nothing (H-145).

The formatted grade is worded by a background task. The task is queued
with a prompt built from the grading result AS IT WAS when it was queued,
then loads the row, makes an AI call that takes seconds, and saves its
text. A newer result saved meanwhile (a regrade, a teacher's manual
grade) was overwritten by the older task's wording when that task
finished last, in two ways:

1. the task loaded the row before the newer result was saved: old
   wording and the old score sentence;
2. the task was queued before the newer result and started after it: old
   wording around the new number.

The rule (SM, 2026-10-06): every queuer passes a stamp of the result it
asks to have worded. At save time the task locks the row and writes only
if the row's stamp still equals the one it was given; otherwise it writes
nothing and ends as a success that says it was superseded. A message
with no stamp (one queued before this change) behaves as before.

A second task, `format_grade`, is queued nowhere in the application. It
saved the WHOLE row from the copy it loaded before its AI call, so it
could put an old score back. It is narrowed to save its text alone; it
is not removed here.
"""

import copy
from decimal import Decimal
from unittest.mock import patch

from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework import status

from assignments.tasks import format_grade, formatted_grade_async
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    StudentSubmission,
)
from students.services import grade_engine, grading_result_stamp
from students.task_tracking import create_processing_task
from students.tests_formatter_input import grading_result as a_grading_result
from students.tests_formatter_input import make_people_and_submission
from students.tests_manual_grade_formatted_grade import (
    FROM_THE_FORMATTER,
    OLD_FORMATTED,
    ManualGradeBase,
)

FORMATTER = "assignments.tasks.ai_processor.formatted_grade"
#: A sentence only this module's stand-in formatter says. The task rewrites
#: the score sentence from the row, and for a paper still graded 7 that
#: sentence is letter for letter the fixture's: "the task wrote" can be told
#: from "it did not" only by something the fixture's text does not hold.
WORDED_BY_THIS_RUN = "worded by this run"


def the_formatters_reply() -> dict:
    return {**copy.deepcopy(FROM_THE_FORMATTER), "closing_note": WORDED_BY_THIS_RUN}


class SupersededBase(ManualGradeBase):
    """A released paper graded 7, with the wording of that result stored."""

    def row(self):
        return StudentSubmission.objects.get(pk=self.submission.pk)

    def stamp_now(self):
        return grading_result_stamp(self.row())

    def run_task(self, stamp, *, meanwhile=None, processing_task_id=None):
        """The task for the result stamped `stamp`. `meanwhile` happens
        while its AI call is in flight, after it has loaded the row."""

        def the_ai_call(*args, **kwargs):
            if meanwhile:
                meanwhile()
            return the_formatters_reply()

        with patch(FORMATTER, side_effect=the_ai_call):
            return formatted_grade_async(
                str(self.submission.pk),
                "a prompt",
                processing_task_id=processing_task_id,
                result_stamp=stamp,
            )


class AnOlderTaskDoesNotWriteOverAManualGrade(SupersededBase):
    def test_one_that_loaded_the_row_before_the_manual_grade(self):
        """Way 1. The manual grade clears the text (H-144); the older
        task must not put text back."""
        self.run_task(self.stamp_now(), meanwhile=self.override)

        self.assertIsNone(self.stored_formatted())

    def test_one_queued_before_and_started_after_the_manual_grade(self):
        """Way 2."""
        before = self.stamp_now()
        self.override()

        self.run_task(before)

        self.assertIsNone(self.stored_formatted())

    def test_the_manual_grades_own_task_fills_the_text(self):
        _, queued = self.override()

        self.assertEqual(queued.kwargs["result_stamp"], self.stamp_now())
        self.run_the_task(queued, return_value=the_formatters_reply())
        self.assertIn("You scored 9 out of 10 points", self.stored_formatted())

    def test_a_task_whose_result_is_still_the_rows_writes(self):
        # The fixture's text does not hold the marker, so finding it
        # stored means this task wrote.
        self.assertNotIn(WORDED_BY_THIS_RUN, self.stored_formatted())

        self.run_task(self.stamp_now())

        stored = self.stored_formatted()
        self.assertIn(WORDED_BY_THIS_RUN, stored)
        self.assertIn("You scored 7 out of 10 points", stored)

    def test_a_message_with_no_stamp_behaves_as_before(self):
        """One queued before this change is still in the queue when it is
        deployed. It writes, as it always did."""
        self.override()

        self.run_task(None)

        self.assertIn("You scored 9 out of 10 points", self.stored_formatted())


class ARegradeSupersedesTheFirstGradingsTask(SupersededBase):
    def test_the_first_gradings_task_writes_nothing_after_a_regrade(self):
        first = self.stamp_now()
        self.grade_by_ai(self.submission, 3)

        self.run_task(first)

        # grading does not clear the text; what is stored is the fixture's
        self.assertEqual(self.stored_formatted(), str(OLD_FORMATTED))

    def test_the_regrades_own_task_writes(self):
        self.grade_by_ai(self.submission, 3)

        self.run_task(self.stamp_now())

        self.assertIn("You scored 3 out of 10 points", self.stored_formatted())


class HowASupersededTaskEnds(SupersededBase):
    def setUp(self):
        super().setUp()
        self.record = create_processing_task(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.FORMATTED_GRADE,
            assignment=self.assignment,
            submission=self.submission,
            meta={"step": "Queued for formatted grade generation"},
        )
        before = self.stamp_now()
        self.override()
        self.result = self.run_task(before, processing_task_id=str(self.record.pk))
        self.record = BackgroundProcessingTask.objects.get(pk=self.record.pk)

    def test_as_a_success_not_a_failure(self):
        self.assertEqual(self.record.status, BackgroundTaskStatus.SUCCESS)
        self.assertFalse(self.record.error)
        self.assertEqual(self.result["status"], "SUCCESS")

    def test_its_step_says_superseded_and_names_no_score(self):
        for text in (self.record.meta["step"], self.result["message"]):
            with self.subTest(text=text):
                self.assertIn("uperseded", text)
                self.assertFalse(any(character.isdigit() for character in text))


class TheTeacherFeedbackRoutePassesTheStamp(SupersededBase):
    def test_the_route_queues_the_task_with_the_rows_stamp(self):
        self.override()  # leaves no formatted grade, so the route queues
        self.client.force_authenticate(self.teacher)
        with patch("students.views.formatted_grade_async") as task:
            task.delay.return_value.id = "fake-task-id"
            response = self.client.get(
                reverse(
                    "student-submission-teacher-feedback",
                    kwargs={"pk": self.submission.pk},
                )
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        task.delay.assert_called_once()
        self.assertEqual(task.delay.call_args.kwargs["result_stamp"], self.stamp_now())


class GradingPassesTheStamp(TransactionTestCase):
    """TransactionTestCase, because the follow-up waits for the grade's
    commit (see students/tests_grading_followup_dispatch.py)."""

    @patch("students.services._formatted_grade_task")
    @patch("students.services.student_summary_async")
    @patch("students.services.ai_processor")
    def test_the_follow_up_is_queued_with_the_saved_results_stamp(
        self, mock_ai, mock_summary, mock_formatted
    ):
        teacher, submission = make_people_and_submission()
        mock_ai.extract_grade_with_retry.return_value = a_grading_result()
        mock_formatted.return_value.delay.return_value.id = "fake-task-id"

        grade_engine(teacher, submission)

        mock_formatted.return_value.delay.assert_called_once()
        saved = StudentSubmission.objects.get(pk=submission.pk)
        self.assertIsNotNone(saved.graded_at)
        self.assertEqual(
            mock_formatted.return_value.delay.call_args.kwargs["result_stamp"],
            grading_result_stamp(saved),
        )


class TheStamp(SupersededBase):
    def test_it_changes_with_a_regrade_and_with_a_manual_grade(self):
        first = self.stamp_now()
        self.grade_by_ai(self.submission, 7)
        second = self.stamp_now()
        self.override(7)
        third = self.stamp_now()

        self.assertEqual(len({first, second, third}), 3)

    def test_it_is_the_same_for_the_row_in_memory_and_from_the_database(self):
        self.grade_by_ai(self.submission, 7)

        self.assertEqual(grading_result_stamp(self.submission), self.stamp_now())

    def test_it_holds_no_score(self):
        self.grade_by_ai(self.submission, 7)
        self.override(9)
        row = self.row()

        stamp = grading_result_stamp(row)
        self.assertEqual(
            stamp, f"{row.graded_at.isoformat()}|{row.regraded_at.isoformat()}"
        )


class FormatGradeSavesOnlyItsText(SupersededBase):
    """The task nothing queues. While its AI call is in flight a teacher
    grades by hand; the copy it loaded holds the old score."""

    def run_format_grade(self, **formatter):
        # Called as a function there is no worker to report progress to.
        with (
            patch(FORMATTER, **formatter),
            patch.object(format_grade, "update_state"),
        ):
            format_grade(str(self.submission.pk), "a prompt")

    def test_a_score_saved_during_its_ai_call_is_not_written_back(self):
        before = self.row()

        def the_ai_call(*args, **kwargs):
            self.override(9)
            return the_formatters_reply()

        self.run_format_grade(side_effect=the_ai_call)

        after = self.row()
        self.assertEqual(after.score, Decimal("9"))
        self.assertEqual(after.score_percentage, Decimal("90"))
        self.assertTrue(after.was_regraded)
        self.assertIsNotNone(after.regraded_at)
        self.assertNotEqual(after.raw_input, before.raw_input)
        self.assertEqual(after.feedback["grading_summary"]["total_score"], 9)

    def test_it_still_writes_its_text(self):
        self.assertNotIn(WORDED_BY_THIS_RUN, self.stored_formatted())

        self.run_format_grade(return_value=the_formatters_reply())

        stored = self.stored_formatted()
        self.assertIn(WORDED_BY_THIS_RUN, stored)
        self.assertIn("You scored 7 out of 10 points", stored)
