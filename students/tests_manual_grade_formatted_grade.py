"""The formatted grade after a teacher's manual grade (H-144).

The formatted grade is the worded version of a grading result that a
student reads. Its first sentence states the score. A manual grade
(`update-grade`) saved the new score and queued a background task to
word the result again; until that task finished the old text stood beside
the new score, and if the task failed it stood there for good.

The rule (SM, 2026-10-06): the route clears the formatted grade in the
same save as the new score. A student then reads the right score and no
formatted text, never a wrong one, until the task fills it in. And a
grade that went through is not answered with a failure because the task
could not be queued: the route answers success, the formatted grade stays
empty, and the failure is logged with ids only.

Not done here: the task is not retried, and whether the teacher's screen
shows a failed background task is not read.
"""

from decimal import Decimal
from unittest.mock import patch

from django.urls import reverse
from kombu.exceptions import OperationalError
from rest_framework import status

from assignments.tasks import formatted_grade_async
from students.models import StudentSubmission
from students.tests_answer_document_before_release import AnswerDocumentBase

OLD_STATEMENT = "You scored 7 out of 10 points, giving you a final grade of 70.00%."
OLD_FORMATTED = {"overall_performance_summary": {"score_statement": OLD_STATEMENT}}
#: what the formatter returns; the task rewrites the statement from the row
FROM_THE_FORMATTER = {"overall_performance_summary": {"score_statement": "anything"}}
BROKER_SAID = "the-broker-said-this"


class ManualGradeBase(AnswerDocumentBase):
    released = True

    def setUp(self):
        super().setUp()
        self.grade_by_ai(self.submission, 7)
        # As the formatting task leaves it: the dictionary assigned to a
        # text column, which stores its text form.
        row = StudentSubmission.objects.get(pk=self.submission.pk)
        row.formatted_grade = str(OLD_FORMATTED)
        row.save(update_fields=["formatted_grade"])
        if self.released:
            self.release()

    def stored_formatted(self):
        return StudentSubmission.objects.get(pk=self.submission.pk).formatted_grade

    def patch_grade(self, score=9):
        self.client.force_authenticate(self.teacher)
        return self.client.patch(
            reverse(
                "student-submission-update-grade", kwargs={"pk": self.submission.pk}
            ),
            {"score": score},
            format="json",
        )

    def override(self, score=9):
        """The manual grade with its task queued and not yet run. Returns
        the response and the arguments the task was queued with."""
        with patch("students.views.formatted_grade_async") as task:
            task.delay.return_value.id = "fake-task-id"
            response = self.patch_grade(score)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        task.delay.assert_called_once()
        return response, task.delay.call_args

    def student_reads(self):
        self.client.force_authenticate(self.student)
        response = self.client.get(
            reverse("student-submission-detail", kwargs={"pk": self.submission.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data

    def run_the_task(self, queued, **formatter):
        """The queued task, run here with the formatter's AI call replaced."""
        args, kwargs = queued
        with patch("assignments.tasks.ai_processor.formatted_grade", **formatter):
            return formatted_grade_async(
                *args, **{**kwargs, "processing_task_id": None}
            )


class TheOldTextDoesNotStandBesideTheNewScore(ManualGradeBase):
    def test_before_the_manual_grade_the_student_reads_the_old_statement(self):
        """The control: the fixture's text does reach the student."""
        self.assertIn(OLD_STATEMENT, str(self.student_reads()["formatted_grade"]))

    def test_the_route_clears_the_stored_formatted_grade(self):
        self.override()

        self.assertIsNone(self.stored_formatted())

    def test_the_routes_own_answer_has_the_new_score_and_no_formatted_grade(self):
        response, _ = self.override()

        self.assertEqual(Decimal(str(response.data["score"])), Decimal("9"))
        self.assertIsNone(response.data["formatted_grade"])

    def test_a_released_student_reads_the_new_score_and_no_formatted_grade(self):
        self.student_reads()  # cached, as a student who had the page open

        self.override()

        after = self.student_reads()
        self.assertEqual(Decimal(str(after["score"])), Decimal("9"))
        self.assertIsNone(after["formatted_grade"])

    def test_the_task_is_still_queued_and_the_notice_still_sent(self):
        with patch("students.views.notify_student_of_graded_submission") as notice:
            _, queued = self.override()

        notice.assert_called_once()
        self.assertEqual(queued.args[0], str(self.submission.pk))


class BeforeReleaseItIsClearedToo(ManualGradeBase):
    """A student reads no formatted grade before release whatever is
    stored. The teacher does, and the old text is as wrong for them."""

    released = False

    def test_the_route_clears_the_stored_formatted_grade(self):
        response, _ = self.override()

        self.assertIsNone(self.stored_formatted())
        self.assertIsNone(response.data["formatted_grade"])


class WhenTheTaskEnds(ManualGradeBase):
    def test_a_finished_task_gives_the_student_the_new_statement(self):
        _, queued = self.override()

        self.run_the_task(queued, return_value=dict(FROM_THE_FORMATTER))

        text = str(self.student_reads()["formatted_grade"])
        self.assertIn("You scored 9 out of 10 points", text)
        self.assertNotIn(OLD_STATEMENT, text)

    def test_a_failed_task_leaves_nothing_stale(self):
        _, queued = self.override()

        with self.assertRaises(RuntimeError):
            self.run_the_task(queued, side_effect=RuntimeError("the formatter failed"))

        self.assertIsNone(self.stored_formatted())
        after = self.student_reads()
        self.assertEqual(Decimal(str(after["score"])), Decimal("9"))
        self.assertIsNone(after["formatted_grade"])


class WhenTheTaskCannotBeQueued(ManualGradeBase):
    """The score is saved and the notice sent before the task is queued.
    The grade went through; the route says so."""

    def refused(self, error):
        with patch("students.views.formatted_grade_async") as task:
            task.delay.side_effect = error
            with self.assertLogs("students.views", level="ERROR") as logs:
                response = self.patch_grade()
        return response, "\n".join(logs.output)

    def assert_the_grade_went_through(self, response):
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(Decimal(str(response.data["score"])), Decimal("9"))
        row = StudentSubmission.objects.get(pk=self.submission.pk)
        self.assertEqual(row.score, Decimal("9"))
        self.assertIsNone(row.formatted_grade)

    def test_an_unreachable_queue_does_not_fail_the_grade(self):
        response, _ = self.refused(OperationalError(BROKER_SAID))

        self.assert_the_grade_went_through(response)

    def test_nor_does_any_other_failure_to_queue(self):
        response, _ = self.refused(RuntimeError(BROKER_SAID))

        self.assert_the_grade_went_through(response)

    def test_the_failure_is_logged_with_ids_and_nothing_else(self):
        _, logged = self.refused(OperationalError(BROKER_SAID))

        self.assertIn(str(self.submission.pk), logged)
        self.assertIn("OperationalError", logged)
        self.assertNotIn(BROKER_SAID, logged)
        self.assertNotIn(self.student.email, logged)
        self.assertNotIn(self.student.get_full_name(), logged)

    def test_the_student_reads_the_new_score_and_no_formatted_grade(self):
        self.refused(OperationalError(BROKER_SAID))

        after = self.student_reads()
        self.assertEqual(Decimal(str(after["score"])), Decimal("9"))
        self.assertIsNone(after["formatted_grade"])
