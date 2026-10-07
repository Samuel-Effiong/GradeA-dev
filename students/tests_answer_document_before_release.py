"""H-130, part B: a student's answer document says nothing of an unreleased grade.

The founder's rule (2026-10-06): a student should not know a grade exists
before the teacher releases it.

`raw_input` is the answer document, built by `student_submission_to_html`.
Its header carries "Graded At" and "Score". Grading rebuilds the document
with both filled in and stores it, and the student's own serializers
returned the stored text: every other grade-bearing field was withheld
until release, and the score stood in the document.

Until release a student now always reads the document rebuilt from the row
in its ungraded form, the header exactly as a newly submitted row has it.
After release, the stored document. Nothing stored changes and staff read
what they read before. Because the rebuild happens before grading as well
as after it, grading changes nothing the student reads.

The submission here is made the way production makes one, through the
upload engine, not by a bare `create()`: the score column has a default of
zero, and what a submitted document's header says follows from that.
"""

import ast
import inspect
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from rest_framework import serializers, status

import assignments.serializers
import classrooms.serializers
import dashboard.serializers
import students.serializers
from assignments.models import Assignment
from assignments.serializers import AssignmentDetailStudentSerializer
from classrooms.models import EnrollmentStatusType, StudentCourse
from classrooms.tests_final_grade_zero_score import FinalGradeZeroScoreBase
from students.models import StudentSubmission
from students.services import upload_answers_engine

EXTRACTED = {
    "answers": [
        {
            "question_number": 1,
            "question_text": "<p>Name the narrator.</p>",
            "answer_html": "<p>It is Scout.</p>",
        }
    ]
}


class AnswerDocumentBase(FinalGradeZeroScoreBase):
    def setUp(self):
        super().setUp()
        # The base enrols by e-mail, which leaves the student PENDING, and a
        # pending student may not read a course's assignments at all: the
        # assignment route would answer 404 and test nothing. (It did, in
        # the first run of this module.) A student who can read their
        # assignment is ENROLLED.
        StudentCourse.objects.filter(student=self.student, course=self.course).update(
            enrollment_status=EnrollmentStatusType.ENROLLED
        )
        # A due date, so that the header's "Due Date" line is printed from a
        # real value in every document these tests compare; read back from
        # the database, as the routes read it.
        Assignment.objects.filter(pk=self.assignments[0].pk).update(
            due_date=timezone.now() + timedelta(days=7)
        )
        self.assignment = Assignment.objects.get(pk=self.assignments[0].pk)
        # The base's row for this assignment came from a bare create().
        # Replace it with one the upload engine makes.
        self.submission.delete()
        with (
            patch(
                "students.services.ai_processor.extract_answer_with_retry",
                return_value=EXTRACTED,
            ),
            patch("students.services.send_email_task.delay"),
        ):
            upload_answers_engine(
                self.assignment, [{"type": "text", "text": "an answer"}], self.student
            )
        self.submission = StudentSubmission.objects.get(
            student=self.student, assignment=self.assignment
        )

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

    def release_all(self):
        self.client.force_authenticate(self.teacher)
        response = self.client.post(
            reverse("assignment-publish-all-grades", kwargs={"pk": self.assignment.pk})
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
            reverse("assignment-detail", kwargs={"pk": self.assignment.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data["student_submission_raw_input"]


class AStudentWhoIsOnlyInvitedReadsNoAssignment(AnswerDocumentBase):
    """The shape this module's fixture had by mistake, kept on purpose: a
    PENDING student is refused the assignment route, so a test that reads
    that route as such a student tests nothing but the refusal."""

    def test_the_assignment_route_answers_not_found(self):
        StudentCourse.objects.filter(student=self.student, course=self.course).update(
            enrollment_status=EnrollmentStatusType.PENDING
        )
        cache.clear()
        self.client.force_authenticate(self.student)

        response = self.client.get(
            reverse("assignment-detail", kwargs={"pk": self.assignment.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class ASubmittedDocumentIsUnchangedByThisRow(AnswerDocumentBase):
    """Before any grading the student reads what production returns today."""

    def test_the_upload_engine_stored_a_document(self):
        self.assertTrue(self.stored())
        self.assertIsNone(self.submission.graded_at)
        self.assertFalse(self.submission.is_published)

    def test_on_the_students_own_submission(self):
        self.assertEqual(self.on_the_submission(self.student), self.stored())

    def test_on_the_students_view_of_the_assignment(self):
        self.assertEqual(self.on_the_assignment(self.student), self.stored())


class GradingChangesNothingTheStudentReads(AnswerDocumentBase):
    def setUp(self):
        super().setUp()
        self.submitted = self.stored()

    def test_on_the_students_own_submission(self):
        self.grade_by_ai(self.submission, 7)

        self.assertEqual(self.on_the_submission(self.student), self.submitted)

    def test_on_the_students_view_of_the_assignment(self):
        self.grade_by_ai(self.submission, 7)

        self.assertEqual(self.on_the_assignment(self.student), self.submitted)

    def test_a_regrade_changes_nothing_either(self):
        self.grade_by_ai(self.submission, 7)
        self.grade_by_ai(self.submission, 3)

        self.assertEqual(self.on_the_submission(self.student), self.submitted)

    def test_a_grade_of_zero_changes_nothing_either(self):
        """A genuine zero. The header prints nothing for a zero score, in
        memory or from the database, so only the grading date could tell."""
        self.grade_by_ai(self.submission, 0)

        self.assertEqual(self.on_the_submission(self.student), self.submitted)
        self.assertEqual(self.on_the_assignment(self.student), self.submitted)

    def test_a_half_graded_row_reads_the_same_too(self):
        """A run that died between the score and the grading time, either
        way round. Nothing is decided from those columns before release."""
        halves = {
            "a score and no grading time": {"graded_at": None},
            "a grading time and no score": {"score": None},
        }
        for name, columns in halves.items():
            with self.subTest(row=name):
                self.grade_by_ai(self.submission, 7)
                StudentSubmission.objects.filter(pk=self.submission.pk).update(
                    **columns
                )

                self.assertEqual(self.on_the_submission(self.student), self.submitted)

    def test_a_graded_row_whose_stored_document_was_lost(self):
        """The read itself rebuilds and stores the document (an existing
        behaviour). What it stores is a graded document; what the student
        gets is not."""
        self.grade_by_ai(self.submission, 7)
        StudentSubmission.objects.filter(pk=self.submission.pk).update(raw_input="")

        self.assertEqual(self.on_the_submission(self.student), self.submitted)
        # The stored document is not compared with the one grading stored.
        # (When this was written the two differed: grading printed "7.0"
        # and this rebuild "7.00". Since H-139 both print "7.00"; the test
        # for that is in tests_answer_document_score_printing.) What
        # matters here: a document was stored, and it is not the ungraded
        # form the student was given.
        rebuilt = self.stored()
        self.assertTrue(rebuilt)
        self.assertNotEqual(rebuilt, self.submitted)


class WhatIsGivenUp(AnswerDocumentBase):
    """Before release the header is today's, not the one at upload time."""

    def test_a_rename_shows_when_it_happens_and_not_when_grading_happens(self):
        submitted = self.on_the_submission(self.student)

        Assignment.objects.filter(pk=self.assignment.pk).update(title="Essay, revised")
        renamed = self.on_the_submission(self.student)
        self.grade_by_ai(self.submission, 7)
        graded = self.on_the_submission(self.student)

        self.assertNotEqual(renamed, submitted)
        self.assertEqual(graded, renamed)

    def test_a_rename_the_teacher_saves_refreshes_the_students_cached_document(self):
        """The two tests around this one rename by a queryset update and
        clear the cache themselves. A real rename is a save, and what
        refreshes the student's cached response then is the assignment's
        own save signal, which bumps every enrolled student. No cache
        clear here."""
        before = self.on_the_submission(self.student)

        self.assignment.title = "Essay, revised"
        self.assignment.save()

        self.assertNotEqual(self.on_the_submission(self.student, fresh=False), before)

    def test_the_same_on_the_students_view_of_the_assignment(self):
        submitted = self.on_the_assignment(self.student)

        Assignment.objects.filter(pk=self.assignment.pk).update(title="Essay, revised")
        renamed = self.on_the_assignment(self.student)
        self.grade_by_ai(self.submission, 7)
        graded = self.on_the_assignment(self.student)

        self.assertNotEqual(renamed, submitted)
        self.assertEqual(graded, renamed)


class NothingElseChanges(AnswerDocumentBase):
    def setUp(self):
        super().setUp()
        self.submitted = self.stored()
        self.grade_by_ai(self.submission, 7)
        self.graded = self.stored()

    def test_grading_stores_a_different_document_and_a_students_read_leaves_it(self):
        self.assertNotEqual(self.graded, self.submitted)

        self.on_the_submission(self.student)
        self.on_the_assignment(self.student)

        self.assertEqual(self.stored(), self.graded)

    def test_the_teacher_reads_the_graded_document_before_release(self):
        self.assertEqual(self.on_the_submission(self.teacher), self.graded)

    def test_after_release_the_student_reads_what_the_teacher_reads(self):
        self.release()

        self.assertEqual(self.on_the_submission(self.student), self.graded)
        self.assertEqual(self.on_the_assignment(self.student), self.graded)
        self.assertEqual(self.on_the_submission(self.teacher), self.graded)

    def test_a_row_with_no_stored_document_is_served_as_it_is(self):
        """Nothing is made up where there is no document: a submission
        still being processed looks as it did."""
        for nothing in (None, ""):
            with self.subTest(stored=repr(nothing)):
                StudentSubmission.objects.filter(pk=self.submission.pk).update(
                    raw_input=nothing
                )

                self.assertEqual(self.on_the_assignment(self.student), nothing)


class TheCachedResponse(AnswerDocumentBase):
    def setUp(self):
        super().setUp()
        self.submitted = self.on_the_submission(self.student)

    def test_a_response_cached_before_grading_is_not_the_graded_form_after_it(self):
        self.grade_by_ai(self.submission, 7)

        self.assertEqual(
            self.on_the_submission(self.student, fresh=False), self.submitted
        )

    def test_a_response_cached_before_release_is_not_served_after_it(self):
        self.grade_by_ai(self.submission, 7)
        self.assertEqual(self.on_the_submission(self.student), self.submitted)

        self.release()

        after = self.on_the_submission(self.student, fresh=False)
        self.assertEqual(after, self.stored())
        self.assertNotEqual(after, self.submitted)

    def test_nor_after_the_release_of_the_whole_assignment(self):
        """That route updates the rows with no save signal."""
        self.grade_by_ai(self.submission, 7)
        self.assertEqual(self.on_the_submission(self.student), self.submitted)
        self.assertEqual(
            self.on_the_assignment(self.student, fresh=False), self.submitted
        )

        self.release_all()

        self.assertEqual(
            self.on_the_submission(self.student, fresh=False), self.stored()
        )
        self.assertEqual(
            self.on_the_assignment(self.student, fresh=False), self.stored()
        )
        self.assertNotEqual(self.stored(), self.submitted)


class TheUploadRoutesOwnAnswer(AnswerDocumentBase):
    """The student's own upload route answers with the STAFF serializer,
    stored document and all (students/views.py, upload_answers). That is
    safe only because the upload refuses a row that is graded or being
    graded before any answer is built. These two tests hold that."""

    def upload(self, assignment):
        self.client.force_authenticate(self.student)
        with (
            patch(
                "students.views.AssignmentProcessingService.prepare_ai_content",
                return_value="content",
            ),
            patch(
                "students.services.ai_processor.extract_answer_with_retry",
                return_value=EXTRACTED,
            ),
            patch("students.services.send_email_task.delay"),
        ):
            return self.client.post(
                reverse(
                    "student-submission-upload-answers",
                    kwargs={"assignment_id": assignment.pk},
                ),
                {
                    "answer": SimpleUploadedFile(
                        "answers.pdf",
                        b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n",
                        content_type="application/pdf",
                    )
                },
                format="multipart",
            )

    def test_a_first_upload_answers_with_the_submitted_document(self):
        second = self.assignments[1]
        self.ungraded[0].delete()  # the base's bare row for that assignment

        response = self.upload(second)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        made = StudentSubmission.objects.get(student=self.student, assignment=second)
        self.assertIsNone(made.graded_at)
        self.assertTrue(made.raw_input)
        self.assertEqual(response.data["raw_input"], made.raw_input)
        # ...and it is the form the student reads afterwards
        cache.clear()
        read = self.client.get(
            reverse("student-submission-detail", kwargs={"pk": made.pk})
        )
        self.assertEqual(read.status_code, status.HTTP_200_OK)
        self.assertEqual(read.data["raw_input"], made.raw_input)

    def test_an_upload_on_a_graded_unreleased_row_is_refused_with_no_document(self):
        self.grade_by_ai(self.submission, 7)
        graded = self.stored()

        response = self.upload(self.assignment)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        # H-133 added "code" beside the sentence. The point of this test: a
        # refused upload carries no document and no grade. (Agreed with d5,
        # 2026-10-06.)
        for absent in (
            "raw_input",
            "score",
            "score_percentage",
            "feedback",
            "formatted_grade",
            "graded_at",
        ):
            self.assertNotIn(absent, response.data)
        self.assertLessEqual(set(response.data), {"error", "code"})
        self.assertEqual(self.stored(), graded)


#: Every serializer of a submission that carries the document, and who can
#: receive it. A new one must be added here, which is the point: whoever
#: adds it has to say whether a student can receive it. Each line was read
#: from the routes in students/views.py (2026-10-06), not assumed.
STAFF_AND_THE_UPLOAD_ANSWER = (
    "staff (retrieve, grade, feedback, release); AND the student's own "
    "upload route answers with it, for a row the upload has just refused "
    "to accept if it is graded or being graded (TheUploadRoutesOwnAnswer)"
)
STUDENT = "a student reads it: the document comes from answer_document_for_student"
BUILT_BY_NO_ROUTE = (
    "no route builds a response from it today: it is named in schema "
    "annotations and as the viewset's fallback serializer class"
)
READERS_OF_THE_DOCUMENT = {
    "StudentSubmissionSerializer": BUILT_BY_NO_ROUTE,
    "StudentSubmissionUpdateSerializer": BUILT_BY_NO_ROUTE,
    "StudentSubmissionDetailSerializer": STAFF_AND_THE_UPLOAD_ANSWER,
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
        tree = ast.parse(inspect.getsource(AssignmentDetailStudentSerializer))
        reads = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr == "raw_input"
            and isinstance(node.value, ast.Name)
            and node.value.id == "submission"
        ]
        self.assertEqual(reads, [])
