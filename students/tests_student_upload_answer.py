"""
H-141: what a student's own answer upload answers with.

The upload route (students/views.py, `upload_answers`, students only)
answered 201 with the STAFF detail serializer, built without the request,
so none of that serializer's student branches could run. It was thought
safe because an upload is refused once the paper is graded. Two things a
student could still be shown, on a paper that is NOT graded and so not
refused (read in the code, 2026-10-07):

  * the teacher's scheduling of a grading run for that paper: the time,
    the task's name, and "is scheduled";
  * the state of grading: FAILED after a grading run that failed, RUNNING
    on a claim too old to block the upload.

Both are things H-133's rule hides on the student's list: before release
a student is shown nothing that tells a grade exists or is on its way.

The answer now comes from a serializer of its own, with the same thirty
keys in the same order. The student's own facts keep their values; every
staff field is sent as a constant, whatever the row holds. The answer does
not read the grade's columns at all, so it does not depend on the refusal.

One value changes for a paper that is not graded: `score`. The staff
serializer sent the column's default, a zero (as a number on a first
upload, as text on a later one). The student's other routes send nothing
there before release, and so does this one now.

Run with:
    python manage.py test students.tests_student_upload_answer
"""

import copy
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from students.models import GradingState, StudentSubmission
from students.services import (
    GRADING_CLAIM_STALE_AFTER,
    _mark_grading_claim_failed,
    _populate_and_save_grade,
)
from students.tests_no_grade_tell_before_release import pdf, plain
from students.tests_post_grading_submission_lock import (
    EXTRACTED,
    VALID_GRADE,
    _classroom,
    _submission,
)

#: The keys of the answer, in order: those of the staff detail serializer,
#: which the route answered with before. A page that reads this answer
#: finds every key it found before.
KEYS = [
    "id",
    "assignment",
    "student",
    "full_name",
    "first_name",
    "last_name",
    "email",
    "submission_status",
    "score",
    "remaining_attempts",
    "max_points",
    "score_percentage",
    "was_regraded",
    "regraded_at",
    "grade_status",
    "is_published",
    "submission_date",
    "raw_input",
    "formatted_grade",
    "answers",
    "grading_state",
    "needs_review",
    "review_reasons",
    "review_severity",
    "review_tier",
    "second_opinion",
    "question_breakdown",
    "scheduled_grading_at",
    "grading_task_name",
    "is_grading_scheduled",
]

#: What a student is sent in place of every staff field, whatever the row
#: holds.
CONSTANTS = {
    "score": None,
    "score_percentage": None,
    "was_regraded": False,
    "regraded_at": None,
    "grade_status": "NOT GRADED",
    "formatted_grade": None,
    "grading_state": "IDLE",
    "needs_review": False,
    "review_reasons": None,
    "review_severity": None,
    "review_tier": None,
    "second_opinion": None,
    "question_breakdown": [],
    "scheduled_grading_at": None,
    "grading_task_name": None,
    "is_grading_scheduled": False,
}

#: What may differ between two of the student's upload answers: the paper,
#: the assignment, the count of attempts, the time and the document.
OWN_TO_THE_UPLOAD = {
    "id",
    "assignment",
    "remaining_attempts",
    "submission_date",
    "raw_input",
}


class UploadAnswerBase(APITestCase):
    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = _classroom("h141")
        self.client.force_authenticate(user=self.student)

    def another_assignment(self, title):
        return Assignment.objects.create(
            title=title,
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )

    def upload(self, assignment=None):
        """The student's upload, through the route; only the file reading
        and the AI call are replaced."""
        self.client.force_authenticate(user=self.student)
        with (
            patch(
                "students.views.AssignmentProcessingService.prepare_ai_content",
                return_value="content",
            ),
            patch(
                "students.services.ai_processor.extract_answer_with_retry",
                return_value=copy.deepcopy(EXTRACTED),
            ),
            patch("students.services.send_email_task.delay"),
        ):
            response = self.client.post(
                reverse(
                    "student-submission-upload-answers",
                    kwargs={"assignment_id": (assignment or self.assignment).pk},
                ),
                {"answer": pdf()},
                format="multipart",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return plain(response.data)

    def row(self, submission):
        return StudentSubmission.objects.get(pk=submission.pk)

    def assert_constants(self, answer):
        for key, value in CONSTANTS.items():
            with self.subTest(key=key):
                self.assertEqual(answer[key], value)


class AFirstUploadTest(UploadAnswerBase):
    """A paper that did not exist before the upload."""

    def test_the_keys_are_the_thirty_the_route_always_answered_with(self):
        self.assertEqual(list(self.upload()), KEYS)

    def test_the_students_own_facts_keep_their_values(self):
        answer = self.upload()
        made = StudentSubmission.objects.get(
            student=self.student, assignment=self.assignment
        )
        self.assertEqual(answer["id"], str(made.pk))
        self.assertEqual(answer["assignment"], str(self.assignment.pk))
        self.assertEqual(answer["student"], str(self.student.pk))
        self.assertEqual(answer["first_name"], self.student.first_name)
        self.assertEqual(answer["last_name"], self.student.last_name)
        self.assertEqual(answer["full_name"], self.student.get_full_name())
        self.assertEqual(answer["submission_status"], "SUBMITTED")
        self.assertEqual(answer["remaining_attempts"], 2)
        self.assertEqual(answer["answers"], EXTRACTED["answers"])
        self.assertEqual(answer["is_published"], False)
        self.assertEqual(
            answer["max_points"],
            Assignment.objects.get(pk=self.assignment.pk).total_points,
        )
        self.assertTrue(answer["raw_input"])
        self.assertEqual(answer["raw_input"], made.raw_input)

    def test_the_document_is_the_one_the_students_page_shows(self):
        answer = self.upload()
        cache.clear()
        page = self.client.get(
            reverse("student-submission-detail", kwargs={"pk": answer["id"]})
        )
        self.assertEqual(page.status_code, status.HTTP_200_OK)
        self.assertEqual(answer["raw_input"], page.data["raw_input"])

    def test_every_staff_field_is_the_constant(self):
        self.assert_constants(self.upload())

    def test_the_assignments_total_is_the_maximum_shown(self):
        Assignment.objects.filter(pk=self.assignment.pk).update(total_points=20)
        self.assertEqual(self.upload()["max_points"], 20)


class AnUploadOnAPaperNotYetGradedTest(UploadAnswerBase):
    """A second upload, on a paper the teacher or the grader has already
    touched without grading it. The upload is accepted; the answer must
    show nothing of what was done."""

    def setUp(self):
        super().setUp()
        self.submission = _submission(self.assignment, self.student, graded=False)

    def schedule_a_grading_run(self):
        """The teacher's own route for one paper."""
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(
            reverse(
                "student-submission-schedule-grade-async",
                kwargs={"pk": self.submission.pk},
            ),
            {"schedule_time": (timezone.now() + timedelta(days=2)).isoformat()},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.data)
        row = self.row(self.submission)
        # The run really is scheduled on the row the upload will answer for.
        self.assertIsNotNone(row.scheduled_grading_at)
        self.assertTrue(row.grading_task_name)
        self.assertIsNone(row.graded_at)

    def test_a_scheduled_grading_run_is_not_shown(self):
        self.schedule_a_grading_run()

        answer = self.upload()

        self.assertEqual(answer["id"], str(self.submission.pk))
        self.assertIsNone(answer["scheduled_grading_at"])
        self.assertIsNone(answer["grading_task_name"])
        self.assertIs(answer["is_grading_scheduled"], False)
        # ...and the run is still scheduled: only the answer is silent.
        self.assertIsNotNone(self.row(self.submission).scheduled_grading_at)

    def test_a_failed_grading_run_is_not_shown(self):
        _mark_grading_claim_failed(self.submission.pk)
        self.assertEqual(self.row(self.submission).grading_state, GradingState.FAILED)

        answer = self.upload()

        self.assertEqual(answer["id"], str(self.submission.pk))
        self.assertEqual(answer["grading_state"], "IDLE")

    def test_a_grading_claim_too_old_to_block_the_upload_is_not_shown(self):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            grading_state=GradingState.RUNNING,
            grading_started_at=timezone.now()
            - GRADING_CLAIM_STALE_AFTER
            - timedelta(minutes=1),
        )

        answer = self.upload()

        self.assertEqual(answer["id"], str(self.submission.pk))
        self.assertEqual(answer["grading_state"], "IDLE")

    def test_it_answers_as_a_first_upload_does_but_for_the_uploads_own_facts(self):
        """The whole answer, not the fields someone thought of."""
        first = self.upload(self.another_assignment("untouched"))
        self.schedule_a_grading_run()
        _mark_grading_claim_failed(self.submission.pk)

        second = self.upload()

        self.assertEqual(second["id"], str(self.submission.pk))
        differing = {key for key in KEYS if first[key] != second[key]}
        self.assertLessEqual(differing, OWN_TO_THE_UPLOAD)
        self.assertEqual(second["remaining_attempts"], 1)

    def test_every_staff_field_is_the_constant_on_a_later_upload_too(self):
        """`score` among them: null, not the text "0.00" the teacher's
        serializer sent for a paper read back from the database."""
        answer = self.upload()
        self.assertEqual(answer["id"], str(self.submission.pk))
        self.assertIsNone(answer["score"])
        self.assert_constants(answer)


class OnlyTheAnswerProtectsTest(UploadAnswerBase):
    """The refusal of an upload on a graded paper is another function's
    rule. Here it is taken out, and the upload engine hands the route a
    paper that IS graded and not released: only the answer's own shape
    stands between the student and the grade."""

    def setUp(self):
        super().setUp()
        Assignment.objects.filter(pk=self.assignment.pk).update(total_points=20)
        # Made by a real upload, so the row has its stored document.
        self.submission = self.row_of(self.upload())

    def row_of(self, answer):
        return StudentSubmission.objects.get(pk=answer["id"])

    def answer_for(self, row):
        self.client.force_authenticate(user=self.student)
        with (
            patch(
                "students.views.AssignmentProcessingService.prepare_ai_content",
                return_value="content",
            ),
            patch("students.views.ensure_student_may_submit"),
            patch("students.views.upload_answers_engine", return_value=row),
        ):
            response = self.client.post(
                reverse(
                    "student-submission-upload-answers",
                    kwargs={"assignment_id": self.assignment.pk},
                ),
                {"answer": pdf()},
                format="multipart",
            )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        return plain(response.data)

    def grade(self):
        """The real grade save, with a question the grader found no answer
        to (so the review fields are written), then what a second grader,
        the formatter and a regrade leave on the row."""
        grading = copy.deepcopy(VALID_GRADE)
        grading["answers_not_found"] = [
            {
                "question_number": 1,
                "answer_status": "not_found",
                "score_awarded": 0,
                "max_points": 10,
            }
        ]
        _populate_and_save_grade(self.row(self.submission), grading, None)
        feedback = self.row(self.submission).feedback
        feedback["question_evaluations"] = [
            {"question_number": 1, "score_awarded": 8, "rationale": "for the teacher"}
        ]
        feedback["second_opinion"] = {"model": "second", "agreements": [1]}
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            feedback=feedback,
            formatted_grade="Advice to the teacher.",
            was_regraded=True,
            regraded_at=timezone.now(),
            scheduled_grading_at=timezone.now() + timedelta(days=1),
            grading_task_name="grade-submission-x",
        )
        row = self.row(self.submission)
        # The paper really is graded, with everything a student must not
        # read on it, and not released.
        self.assertEqual(row.score, 8)
        self.assertEqual(row.max_points, 10)
        self.assertIsNotNone(row.graded_at)
        self.assertEqual(row.grading_state, GradingState.DONE)
        self.assertTrue(row.needs_review)
        self.assertTrue(row.review_reasons)
        self.assertTrue(row.feedback["second_opinion"])
        self.assertTrue(row.feedback["question_evaluations"])
        self.assertFalse(row.is_published)
        return row

    def test_a_graded_paper_answers_with_every_staff_field_the_constant(self):
        answer = self.answer_for(self.grade())
        self.assertEqual(list(answer), KEYS)
        self.assert_constants(answer)

    def test_a_graded_paper_shows_the_assignments_total_not_the_graders(self):
        self.assertEqual(self.answer_for(self.grade())["max_points"], 20)

    def test_a_graded_papers_document_is_the_one_a_submitted_paper_has(self):
        before = self.answer_for(self.row(self.submission))
        graded = self.grade()

        answer = self.answer_for(graded)

        self.assertEqual(answer["raw_input"], before["raw_input"])
        self.assertNotEqual(answer["raw_input"], graded.raw_input)

    def test_grading_changes_nothing_in_the_answer_but_the_attempts(self):
        """The whole answer. `remaining_attempts` drops to 0 at grading:
        the one tell the founder chose to leave (H-133, choice A)."""
        before = self.answer_for(self.row(self.submission))

        after = self.answer_for(self.grade())

        differing = {key for key in KEYS if before[key] != after[key]}
        self.assertEqual(differing, {"remaining_attempts"})
        self.assertEqual(after["remaining_attempts"], 0)


class TheTeacherStillReadsEverythingTest(UploadAnswerBase):
    """The staff detail serializer is unchanged: same keys, real values."""

    def test_the_teachers_page_of_a_graded_paper(self):
        submission = _submission(self.assignment, self.student, graded=True)
        StudentSubmission.objects.filter(pk=submission.pk).update(
            scheduled_grading_at=timezone.now() + timedelta(days=1),
            grading_task_name="grade-submission-x",
            needs_review=True,
        )
        cache.clear()
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(
            reverse("student-submission-detail", kwargs={"pk": submission.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        page = plain(response.data)
        self.assertEqual(list(page), KEYS)
        self.assertEqual(float(page["score"]), 8.0)
        self.assertEqual(page["max_points"], 10)
        self.assertEqual(page["grading_state"], "DONE")
        self.assertIs(page["needs_review"], True)
        self.assertEqual(page["grading_task_name"], "grade-submission-x")
        self.assertIs(page["is_grading_scheduled"], True)
