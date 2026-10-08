"""A teacher's manual grade must never fail to save because the answer
document could not be rebuilt (H-142's rebuild runs inside `update-grade`).

Found by the first regression of the score-printing row: the old test
`StudentSubmissionGradeUpdateTest.test_teacher_can_update_grade` stores
`answers` as a dict, the document builder raised on it, and the route
answered 500 instead of saving the grade. Two separate defences now hold:

- THE BUILDER (H-165): it never raises on a shape of `answers`; the
  document carries one fixed line instead. The first class holds that THROUGH
  THE ROUTE, one test per shape. These tests do not depend on the guard: the
  builder is the real one, so they stay green when the guard is removed.
- THE GUARD (this row, the Senior Manager's ruling of 2026-10-08): a second
  line, small: the build, and only the build, is guarded in the route. If a
  builder fault ever comes back, the grade is still saved, the stored
  document is LEFT AS IT WAS (not cleared: it is the paper the student reads,
  and a stale printed score is a lesser harm than a paper that vanishes; the
  fault is seen in the log), an ERROR is logged with the submission id and the
  exception type and nothing else, and the answer carries the old document
  (it does not claim a refresh). The second class holds that with the builder
  REPLACED by one that raises, so it is red only without the guard.
"""

from decimal import Decimal
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status

from students.models import StudentSubmission
from students.tests_answer_document_before_release import AnswerDocumentBase

OLD_DOCUMENT = "the paper as it was stored before the manual grade"
SHAPES = {
    "a dict": {"q1": "2"},
    "a list of strings": ["first answer", "second answer"],
    "a string": "an answer typed as one string",
}


class ManualGradeCase(AnswerDocumentBase):
    def setUp(self):
        super().setUp()
        self.grade_by_ai(self.submission, 7)

    def store(self, answers):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            answers=answers, raw_input=OLD_DOCUMENT
        )

    def override(self, score=9):
        self.client.force_authenticate(self.teacher)
        return self.client.patch(
            reverse(
                "student-submission-update-grade", kwargs={"pk": self.submission.pk}
            ),
            {"score": score},
            format="json",
        )

    def row(self):
        return StudentSubmission.objects.get(pk=self.submission.pk)


class TheBuilderMakesTheGradeSaveForEveryShape(ManualGradeCase):
    """Defence 1 (H-165's builder). The builder is the real one here."""

    def check(self, shape):
        answers = SHAPES[shape]
        self.assertTrue(answers)
        self.store(answers)

        response = self.override(9)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        row = self.row()
        self.assertEqual(row.score, Decimal("9"))
        self.assertTrue(row.raw_input)
        self.assertNotEqual(row.raw_input, OLD_DOCUMENT)
        self.assertIn("could not be displayed", row.raw_input)
        self.assertEqual(response.data["raw_input"], row.raw_input)

    def test_a_dict_of_answers(self):
        self.check("a dict")

    def test_a_list_of_strings(self):
        self.check("a list of strings")

    def test_a_string_of_answers(self):
        self.check("a string")


class TheGuardKeepsTheSaveSafeFromTheNextBuilderFault(ManualGradeCase):
    """Defence 2 (the guard). The builder is REPLACED by one that raises."""

    def override_with_a_broken_builder(self, message="boom-text-of-the-fault"):
        self.store([{"question_number": 1, "answer_html": "<p>fine</p>"}])
        with (
            patch(
                "students.views.student_submission_to_html",
                side_effect=RuntimeError(message),
            ),
            self.assertLogs("students.views", level="ERROR") as logged,
        ):
            response = self.override(9)
        return response, logged

    def test_the_grade_is_saved_and_the_old_document_is_left_as_it_was(self):
        response, _ = self.override_with_a_broken_builder()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        row = self.row()
        self.assertEqual(row.score, Decimal("9"))
        self.assertTrue(row.was_regraded)
        self.assertEqual(row.raw_input, OLD_DOCUMENT)

    def test_the_answer_does_not_claim_a_refresh(self):
        response, _ = self.override_with_a_broken_builder()

        self.assertEqual(response.data["raw_input"], OLD_DOCUMENT)

    def test_the_fault_is_logged_with_the_id_and_the_type_and_nothing_else(self):
        _, logged = self.override_with_a_broken_builder("boom-text-of-the-fault")

        text = "\n".join(logged.output)
        self.assertTrue(text)
        self.assertIn(str(self.submission.pk), text)
        self.assertIn("RuntimeError", text)
        self.assertNotIn("boom-text-of-the-fault", text)
        self.assertNotIn(self.student.get_full_name(), text)
        self.assertNotIn(self.student.email, text)

    def test_a_fault_in_the_save_is_not_swallowed(self):
        """The guard is around the build only. A save that fails still fails
        the request."""
        self.store([{"question_number": 1, "answer_html": "<p>fine</p>"}])
        self.client.raise_request_exception = False

        with patch("students.views.StudentSubmission.save", side_effect=OSError("x")):
            response = self.override(9)

        self.assertGreaterEqual(response.status_code, 500)
        self.assertEqual(self.row().score, Decimal("7"))
