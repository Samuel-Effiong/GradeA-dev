"""A regrade does not leave the old formatted grade beside the new score
(H-146).

The formatted grade is the worded version of a grading result that a
student reads; its first sentence states the score. A teacher's manual
grade clears it with the new score (H-144). Grading did not: after a
regrade of a paper that already had a formatted grade, the old wording
stood beside the new score until the regrade's own formatting task had
written, and for good if that task failed. Found by v2 while reading
H-145, which stops an older task from writing: after it, nothing else
would replace the old text.

The rule (SM, 2026-10-06): grading clears the formatted grade in its own
save, the one UPDATE that writes the score. A first grading has nothing
to clear.
"""

from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext

from assignments.tasks import formatted_grade_async
from students.models import StudentSubmission
from students.services import held_result_stamp
from students.tests_answer_document_before_release import AnswerDocumentBase
from students.tests_formatting_task_superseded import FORMATTER, SupersededBase
from students.tests_manual_grade_formatted_grade import OLD_STATEMENT


class ARegradeClearsTheOldWording(SupersededBase):
    """A released paper graded 7, with the wording of that result stored.

    H-279 (owner, 2026-11-02): an AI re-grade of a released paper is held for
    the teacher. Until the teacher approves it (here: the publish route) the
    released score and its wording stand; the approval releases the new
    score, and with it no old wording (H-146's rule, at the approval)."""

    def held_stamp(self):
        return held_result_stamp(self.row())

    def test_before_the_regrade_the_student_reads_the_old_statement(self):
        """The control: the fixture's text does reach the student."""
        self.assertIn(OLD_STATEMENT, str(self.student_reads()["formatted_grade"]))

    def test_the_regrade_clears_the_stored_formatted_grade_once_approved(self):
        self.grade_by_ai(self.submission, 3)

        # held: the released wording stands
        self.assertIn(OLD_STATEMENT, self.stored_formatted())

        self.release()

        self.assertIsNone(self.stored_formatted())

    def test_between_the_save_and_the_task_the_student_reads_no_old_wording(self):
        self.student_reads()  # cached, as a student who had the page open

        self.grade_by_ai(self.submission, 3)

        # held: the student reads the released score and its wording
        held = self.student_reads()
        self.assertEqual(Decimal(str(held["score"])), Decimal("7"))
        self.assertIn(OLD_STATEMENT, str(held["formatted_grade"]))

        self.release()

        after = self.student_reads()
        self.assertEqual(Decimal(str(after["score"])), Decimal("3"))
        self.assertIsNone(after["formatted_grade"])

    def test_the_regrades_own_task_fills_it(self):
        self.grade_by_ai(self.submission, 3)

        self.run_task(self.held_stamp())

        # held: the task worded the held result; the student reads the old
        self.assertIn(OLD_STATEMENT, str(self.student_reads()["formatted_grade"]))

        self.release()

        text = str(self.student_reads()["formatted_grade"])
        self.assertIn("You scored 3 out of 10 points", text)
        self.assertNotIn(OLD_STATEMENT, text)

    def test_a_failed_task_leaves_it_empty(self):
        self.grade_by_ai(self.submission, 3)

        with (
            patch(FORMATTER, side_effect=RuntimeError("the formatter failed")),
            self.assertRaises(RuntimeError),
        ):
            formatted_grade_async(
                str(self.submission.pk), "a prompt", result_stamp=self.held_stamp()
            )

        # held: the released wording stands
        self.assertIn(OLD_STATEMENT, self.stored_formatted())

        self.release()

        self.assertIsNone(self.stored_formatted())
        self.assertIsNone(self.student_reads()["formatted_grade"])

    def test_the_first_gradings_task_does_not_put_text_back(self):
        first = self.stamp_now()
        self.grade_by_ai(self.submission, 3)

        # held: the first grading's result is still the released one, so
        # its task still words it
        self.run_task(first)
        self.assertIn("You scored 7 out of 10 points", self.stored_formatted())

        self.release()
        self.run_task(first)

        self.assertIsNone(self.stored_formatted())

    def test_it_is_cleared_by_the_same_update_as_the_score(self):
        """Not a second write after the grade's: one UPDATE, so no moment
        has the new score and the old wording. H-279: that UPDATE is the
        approval's; the held save writes neither."""
        this_table = 'UPDATE "' + StudentSubmission._meta.db_table + '"'

        with CaptureQueriesContext(connection) as held:
            self.grade_by_ai(self.submission, 3)
        self.assertEqual(
            [
                query["sql"]
                for query in held
                if query["sql"].startswith(this_table)
                # a column written, not a key inside the held result's
                # JSON (the captured SQL carries its parameters)
                and (
                    '"formatted_grade" =' in query["sql"] or '"score" =' in query["sql"]
                )
            ],
            [],
        )

        with CaptureQueriesContext(connection) as queries:
            self.release()

        clearing = [
            query["sql"]
            for query in queries
            if query["sql"].startswith(this_table)
            and '"formatted_grade"' in query["sql"]
        ]
        self.assertEqual(len(clearing), 1, clearing)
        self.assertIn('"score"', clearing[0])
        self.assertIn('"graded_at"', clearing[0])


class BeforeReleaseItIsClearedToo(SupersededBase):
    """Graded, worded (the teacher opened the feedback), regraded, and only
    then released. A student reads nothing before release whatever is
    stored; what is stored at release is what they read after it. Asked
    for by v2's pre-read: every other test here regrades a released paper.
    """

    released = False

    def test_a_regrade_before_release_clears_the_stored_wording(self):
        self.assertIn(OLD_STATEMENT, self.stored_formatted())

        self.grade_by_ai(self.submission, 3)

        self.assertIsNone(self.stored_formatted())

    def test_released_afterwards_the_student_reads_no_old_sentence(self):
        self.grade_by_ai(self.submission, 3)

        self.release()

        after = self.student_reads()
        self.assertEqual(Decimal(str(after["score"])), Decimal("3"))
        self.assertIsNone(after["formatted_grade"])


class AFirstGradingHasNothingToClear(AnswerDocumentBase):
    def test_a_first_grading_leaves_no_formatted_grade(self):
        row = StudentSubmission.objects.get(pk=self.submission.pk)
        self.assertIsNone(row.formatted_grade)

        self.grade_by_ai(self.submission, 7)

        row = StudentSubmission.objects.get(pk=self.submission.pk)
        self.assertIsNone(row.formatted_grade)
        self.assertEqual(row.score, Decimal("7"))
