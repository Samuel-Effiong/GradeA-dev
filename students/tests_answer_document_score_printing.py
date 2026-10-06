"""The score in the stored answer document: a probe, before any fix.

Row from H-130's reading (2026-10-06): after a teacher overrides a grade by
hand, does the stored answer document still print the old score?

By reading it does. The manual-grade route (`update-grade`) saves the new
score and does not rebuild `raw_input`; the document's header was written
at grading time. A released student reads the stored document, so the
paper would show one score and the score field another.

These tests state what should be true. They are expected RED until the
route rebuilds the document.
"""

from decimal import Decimal

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status

from students.tests_answer_document_before_release import AnswerDocumentBase


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
