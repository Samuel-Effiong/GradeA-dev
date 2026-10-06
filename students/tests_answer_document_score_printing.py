"""The score printed in the stored answer document (H-139, H-140, H-142).

The answer document's header carries a "Score:" line. Three things were
wrong with it, found while reading for H-130 (2026-10-06):

- H-139: the same graded row printed "7.0" when grading built the
  document (the score is a float in memory) and "7.00" when a read
  rebuilt it (the score comes back from the database as a decimal).
- H-140: a genuine zero printed nothing at all, because the header's
  escape helper prints an empty string for any falsy value.
- H-142: a teacher's manual grade (`update-grade`) saved the new score
  and left the stored document as it was, so a released student read a
  paper with the old score beside a score field with the new one.

The rule (SM, 2026-10-06): a GRADED row, one that has a grading time,
prints its score with two decimals on every path. A row that is not
graded prints what it printed before; that is the form a student reads
until release (H-130), and H-130's own tests hold it unchanged.

The first class was the probe that showed H-142 before any fix.
"""

import re
from decimal import Decimal

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status

from assignments.services import AssignmentProcessingService
from students.models import StudentSubmission
from students.services import student_submission_to_html
from students.tests_answer_document_before_release import AnswerDocumentBase


def printed_score(html):
    """What stands on the header's "Score:" line."""
    (found,) = re.findall(r"<strong>Score:</strong>\s*([^<]*)</p>", html)
    return found.strip()


def rebuilt(submission):
    """The document every writer of the stored column would build now."""
    return AssignmentProcessingService.html_to_prosemirror_text(
        student_submission_to_html(submission)
    )


class AfterAManualGradeThePaperShowsTheNewScore(AnswerDocumentBase):
    def setUp(self):
        super().setUp()
        self.grade_by_ai(self.submission, 7)
        self.release()
        self.before = self.stored()

    def override(self, score):
        """The teacher's manual grade, through its route."""
        self.client.force_authenticate(self.teacher)
        response = self.client.patch(
            reverse(
                "student-submission-update-grade", kwargs={"pk": self.submission.pk}
            ),
            {"score": score},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response

    def student_reads(self):
        self.client.force_authenticate(self.student)
        response = self.client.get(
            reverse("student-submission-detail", kwargs={"pk": self.submission.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data

    def test_the_route_stores_the_new_score(self):
        """The control: the override itself works."""
        self.override(9)

        self.submission.refresh_from_db()
        self.assertEqual(self.submission.score, Decimal("9"))

    def test_the_stored_document_is_rebuilt(self):
        self.override(9)

        self.assertNotEqual(self.stored(), self.before)

    def test_the_routes_own_answer_does_not_carry_the_old_document(self):
        response = self.override(9)

        self.assertEqual(Decimal(str(response.data["score"])), Decimal("9"))
        self.assertNotEqual(response.data["raw_input"], self.before)

    def test_a_released_student_reads_a_paper_that_moved_with_the_score(self):
        cache.clear()
        first = self.student_reads()
        self.assertEqual(Decimal(str(first["score"])), Decimal("7"))
        self.assertEqual(first["raw_input"], self.before)

        self.override(9)

        # no cache.clear(): the override's save refreshes the student's read
        second = self.student_reads()
        self.assertEqual(Decimal(str(second["score"])), Decimal("9"))
        self.assertNotEqual(second["raw_input"], self.before)

    def test_an_override_to_the_same_score_leaves_the_document_as_it_is(self):
        """The other direction, so that "rebuilt" cannot be satisfied by a
        document that changes for no reason."""
        self.override(7)

        self.assertEqual(self.stored(), self.before)

    def test_the_stored_document_is_the_one_a_rebuild_gives(self):
        """Not just "different": the document of the row as it now is."""
        self.override(9)

        row = StudentSubmission.objects.get(pk=self.submission.pk)
        self.assertEqual(row.raw_input, rebuilt(row))
        self.assertEqual(printed_score(student_submission_to_html(row)), "9.00")

    def test_the_routes_own_answer_carries_the_new_document(self):
        response = self.override(7.5)

        self.assertEqual(response.data["raw_input"], self.stored())
        self.assertNotEqual(response.data["raw_input"], self.before)


class AManualGradeBeforeRelease(AnswerDocumentBase):
    """The route rebuilds the stored document whether or not the grade is
    released. What an unreleased student reads does not move (H-130)."""

    def setUp(self):
        super().setUp()
        self.submitted = self.on_the_submission(self.student)
        self.grade_by_ai(self.submission, 7)
        self.before = self.stored()
        self.client.force_authenticate(self.teacher)
        response = self.client.patch(
            reverse(
                "student-submission-update-grade", kwargs={"pk": self.submission.pk}
            ),
            {"score": 9},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def test_the_stored_document_is_rebuilt(self):
        self.assertNotEqual(self.stored(), self.before)

    def test_the_student_still_reads_the_submitted_form(self):
        # no cache.clear(): the override's save refreshes the student's read
        self.assertEqual(
            self.on_the_submission(self.student, fresh=False), self.submitted
        )
        self.assertEqual(
            self.on_the_assignment(self.student, fresh=False), self.submitted
        )


class AGradedRowPrintsItsScoreWithTwoDecimals(AnswerDocumentBase):
    """The header itself, built from the row in memory as grading leaves it
    (a float) and from the row as the database returns it (a decimal)."""

    def in_memory_and_stored(self, score):
        self.grade_by_ai(self.submission, score)
        return {
            "as grading leaves it": self.submission,
            "from the database": StudentSubmission.objects.get(pk=self.submission.pk),
        }

    def assert_prints(self, score, expected):
        for name, row in self.in_memory_and_stored(score).items():
            with self.subTest(row=name):
                self.assertEqual(
                    printed_score(student_submission_to_html(row)), expected
                )

    def test_a_whole_score(self):
        self.assert_prints(7, "7.00")

    def test_a_half_point(self):
        self.assert_prints(7.5, "7.50")

    def test_a_genuine_zero_is_printed(self):
        self.assert_prints(0, "0.00")

    def test_full_marks(self):
        self.assert_prints(10, "10.00")


class ARowThatIsNotGradedPrintsWhatItPrinted(AnswerDocumentBase):
    """Controls. None of these is a graded row, and none changes."""

    def test_a_submitted_row_prints_an_empty_score_line(self):
        self.assertEqual(printed_score(student_submission_to_html(self.submission)), "")

    def test_the_form_a_student_reads_before_release_stays_empty(self):
        self.grade_by_ai(self.submission, 7)

        html = student_submission_to_html(self.submission, show_grade=False)
        self.assertEqual(printed_score(html), "")
        self.assertIn("Not graded yet", html)

    def test_a_grading_time_and_no_score_says_not_graded_yet(self):
        self.grade_by_ai(self.submission, 7)
        StudentSubmission.objects.filter(pk=self.submission.pk).update(score=None)

        row = StudentSubmission.objects.get(pk=self.submission.pk)
        self.assertEqual(
            printed_score(student_submission_to_html(row)), "Not graded yet"
        )

    def test_a_score_and_no_grading_time_prints_the_stored_value_as_before(self):
        self.grade_by_ai(self.submission, 7)
        StudentSubmission.objects.filter(pk=self.submission.pk).update(graded_at=None)

        row = StudentSubmission.objects.get(pk=self.submission.pk)
        self.assertEqual(printed_score(student_submission_to_html(row)), "7.00")


class TheStoredDocumentIsTheSameOnEveryPath(AnswerDocumentBase):
    """H-139 at the level of what is stored: the document grading stores
    and the one a read rebuilds for the same row are the same document."""

    def lost_and_rebuilt_by_a_teachers_read(self):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(raw_input="")
        self.on_the_submission(self.teacher)
        return self.stored()

    def test_grading_and_the_rebuild_on_a_read(self):
        self.grade_by_ai(self.submission, 7)
        at_grading = self.stored()

        self.assertEqual(self.lost_and_rebuilt_by_a_teachers_read(), at_grading)

    def test_the_same_for_a_genuine_zero(self):
        self.grade_by_ai(self.submission, 0)
        at_grading = self.stored()

        self.assertEqual(self.lost_and_rebuilt_by_a_teachers_read(), at_grading)
        self.assertEqual(at_grading, rebuilt(self.submission))

    def test_a_graded_zero_is_not_the_submitted_document(self):
        submitted = self.stored()
        self.grade_by_ai(self.submission, 0)

        self.assertNotEqual(self.stored(), submitted)
