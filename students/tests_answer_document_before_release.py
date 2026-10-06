"""H-130, part B: a student's answer document says nothing of an unreleased grade.

The founder's rule (2026-10-06): a student should not know a grade exists
before the teacher releases it.

`raw_input` is the answer document, built by `student_submission_to_html`.
Its header carries "Graded At" and "Score". Grading rebuilds the document
with both filled in and stores it, and the student's own serializers
returned the stored text: every other grade-bearing field was withheld
until release, and the score stood in the document.

For a student reader of an unreleased submission the document is now
rebuilt from the row in its ungraded form. Nothing stored changes and the
teacher reads what the teacher read before. The test of the rule is
byte-identity: graded-but-unreleased must read exactly as submitted did.
"""

import ast
import inspect

from django.core.cache import cache
from django.urls import reverse
from rest_framework import serializers, status

import assignments.serializers
import classrooms.serializers
import dashboard.serializers
import students.serializers
from assignments.serializers import AssignmentDetailStudentSerializer
from classrooms.tests_final_grade_zero_score import FinalGradeZeroScoreBase
from students.models import StudentSubmission
from students.services import answer_document_for_student

UNGRADED = "Not graded yet"


class AnswerDocumentBase(FinalGradeZeroScoreBase):
    def setUp(self):
        super().setUp()
        self.submission.answers = [
            {
                "question_number": 1,
                "question_text": "<p>Name the narrator.</p>",
                "answer_html": "<p>It is Scout.</p>",
            }
        ]
        self.submission.save(update_fields=["answers"])

    def stored(self):
        return StudentSubmission.objects.get(pk=self.submission.pk).raw_input

    def release(self):
        self.client.force_authenticate(self.teacher)
        response = self.client.post(
            reverse(
                "student-submission-publish-grade", kwargs={"pk": self.submission.pk}
            )
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def on_the_submission(self, user, *, fresh=True):
        if fresh:
            cache.clear()
        self.client.force_authenticate(user)
        response = self.client.get(
            reverse("student-submission-detail", kwargs={"pk": self.submission.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data["raw_input"]

    def on_the_assignment(self, user, *, fresh=True):
        if fresh:
            cache.clear()
        self.client.force_authenticate(user)
        response = self.client.get(
            reverse("assignment-detail", kwargs={"pk": self.assignments[0].pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data["student_submission_raw_input"]


class GradedButUnreleasedReadsLikeSubmitted(AnswerDocumentBase):
    def test_on_the_students_own_submission(self):
        submitted = self.on_the_submission(self.student)
        self.assertEqual(submitted.count(UNGRADED), 2)

        self.grade_by_ai(self.submission, 7)

        self.assertEqual(self.on_the_submission(self.student), submitted)

    def test_on_the_students_view_of_the_assignment(self):
        self.on_the_submission(self.student)  # the document is first built here
        submitted = self.on_the_assignment(self.student)
        self.assertEqual(submitted.count(UNGRADED), 2)

        self.grade_by_ai(self.submission, 7)

        self.assertEqual(self.on_the_assignment(self.student), submitted)

    def test_a_regrade_changes_nothing_either(self):
        submitted = self.on_the_submission(self.student)
        self.grade_by_ai(self.submission, 7)
        self.grade_by_ai(self.submission, 3)

        self.assertEqual(self.on_the_submission(self.student), submitted)

    def test_a_graded_row_whose_stored_document_is_empty(self):
        """An older row: the read itself rebuilds and stores the document.
        What it stores is the graded one; what the student gets is not."""
        submitted = self.on_the_submission(self.student)
        self.grade_by_ai(self.submission, 7)
        StudentSubmission.objects.filter(pk=self.submission.pk).update(raw_input="")

        self.assertEqual(self.on_the_submission(self.student), submitted)
        self.assertEqual(self.stored().count(UNGRADED), 0)

    def test_a_half_graded_row_is_treated_as_graded(self):
        """A score with no grading time (a run that died between the two):
        the stored header shows the score, so the student must not get it."""
        submitted = self.on_the_submission(self.student)
        self.grade_by_ai(self.submission, 7)
        StudentSubmission.objects.filter(pk=self.submission.pk).update(graded_at=None)

        self.assertEqual(self.on_the_submission(self.student), submitted)


class NothingElseChanges(AnswerDocumentBase):
    def setUp(self):
        super().setUp()
        self.submitted = self.on_the_submission(self.student)
        self.grade_by_ai(self.submission, 7)

    def test_the_stored_document_is_the_graded_one_and_a_students_read_leaves_it(self):
        graded = self.stored()
        self.assertEqual(graded.count(UNGRADED), 0)
        self.assertNotEqual(graded, self.submitted)

        self.on_the_submission(self.student)
        self.on_the_assignment(self.student)

        self.assertEqual(self.stored(), graded)

    def test_the_teacher_reads_the_graded_document_before_release(self):
        self.assertEqual(self.on_the_submission(self.teacher), self.stored())

    def test_after_release_the_student_reads_what_the_teacher_reads(self):
        self.release()

        self.assertEqual(self.on_the_submission(self.student), self.stored())
        self.assertEqual(self.on_the_assignment(self.student), self.stored())
        self.assertEqual(self.on_the_submission(self.teacher), self.stored())

    def test_an_ungraded_row_is_served_as_stored(self):
        """No rebuild where there is nothing to hide."""
        other = self.ungraded[0]
        StudentSubmission.objects.filter(pk=other.pk).update(raw_input="as stored")
        other.refresh_from_db()

        self.assertEqual(answer_document_for_student(other), "as stored")


class TheCachedResponse(AnswerDocumentBase):
    def test_a_response_cached_before_grading_is_not_the_graded_form_after_it(self):
        submitted = self.on_the_submission(self.student)

        self.grade_by_ai(self.submission, 7)

        self.assertEqual(self.on_the_submission(self.student, fresh=False), submitted)

    def test_a_response_cached_before_release_is_not_served_after_it(self):
        self.grade_by_ai(self.submission, 7)
        before = self.on_the_submission(self.student)
        self.assertEqual(before.count(UNGRADED), 2)

        self.release()

        after = self.on_the_submission(self.student, fresh=False)
        self.assertEqual(after, self.stored())
        self.assertEqual(after.count(UNGRADED), 0)


#: Every serializer of a submission that carries the document, and who it
#: is for. A new one must be added here, which is the point: whoever adds
#: it has to say whether a student can receive it.
STAFF = "staff only: the teacher's document"
STUDENT = "a student reads it: the document comes from answer_document_for_student"
WRITE_RESPONSE = (
    "returned to a student only from a write route (upload, edit), and a "
    "graded row refuses those writes before any response is built"
)
READERS_OF_THE_DOCUMENT = {
    "StudentSubmissionSerializer": WRITE_RESPONSE,
    "StudentSubmissionUpdateSerializer": WRITE_RESPONSE,
    "StudentSubmissionDetailSerializer": STAFF,
    "StudentSubmissionDetailStudentVersionSerializer": STUDENT,
}


def serializers_of_a_submission_with_the_document():
    found = {}
    for module in (
        students.serializers,
        assignments.serializers,
        classrooms.serializers,
        dashboard.serializers,
    ):
        for name, value in vars(module).items():
            if not (
                inspect.isclass(value)
                and issubclass(value, serializers.ModelSerializer)
                and value.__module__ == module.__name__
            ):
                continue
            meta = getattr(value, "Meta", None)
            if getattr(meta, "model", None) is StudentSubmission and (
                "raw_input" in (getattr(meta, "fields", None) or ())
            ):
                found[name] = value
    return found


class EveryReaderOfTheDocumentIsNamed(AnswerDocumentBase):
    def test_no_serializer_carries_the_document_unnamed(self):
        self.assertEqual(
            sorted(serializers_of_a_submission_with_the_document()),
            sorted(READERS_OF_THE_DOCUMENT),
            "A serializer of StudentSubmission carries raw_input and is not "
            "named in READERS_OF_THE_DOCUMENT (or one named there is gone). "
            "Before release a student must get the document from "
            "students.services.answer_document_for_student (H-130).",
        )

    def test_a_student_facing_serializer_does_not_return_the_stored_column(self):
        found = serializers_of_a_submission_with_the_document()
        for name, audience in READERS_OF_THE_DOCUMENT.items():
            if audience != STUDENT:
                continue
            with self.subTest(serializer=name):
                field = found[name]().fields["raw_input"]
                self.assertIsInstance(field, serializers.SerializerMethodField)

    def test_the_students_assignment_serializer_does_not_read_the_stored_column(self):
        """It carries the document under another name, through a method."""
        tree = ast.parse(
            inspect.cleandoc(inspect.getsource(AssignmentDetailStudentSerializer))
        )
        reads = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == "raw_input"
            and isinstance(node.value, ast.Name)
            and node.value.id == "submission"
        ]
        self.assertEqual(reads, [])
