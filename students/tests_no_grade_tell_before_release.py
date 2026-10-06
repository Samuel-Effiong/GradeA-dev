"""
H-133: before the teacher releases a grade, a student is told nothing that
names grading.

Founder's rule (2026-10-06): a student should not know a grade exists
before the teacher releases it. The rule that a graded paper is closed to
resubmission stays; what changes is what the student is told and shown.

  * The refusal of an upload or an edit said "This assignment has already
    been graded ..." or "This submission is being graded right now ...".
    A student is now told one of two neutral sentences; a teacher (a proxy
    upload, an edit) is told what they were told before.
  * Every refusal carries a stable `code` beside its sentence, so a client
    need not match on words. A student cannot tell "being graded" from
    "an earlier upload is still being processed": same status, same code,
    same sentence.
  * On the student's list of their submissions, `grading_state` is IDLE
    until release and DONE after, never RUNNING or FAILED; the three
    scheduled-grading fields are empty; `?grading_state=` is refused.

What this does NOT hide, by the founder's choice: `remaining_attempts`
still drops to 0 when a paper is graded, and a change is still refused.

The classroom helpers are those of students/tests_post_grading_submission_lock.py.

Run with:
    python manage.py test students.tests_no_grade_tell_before_release
"""

import json
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from assignments.tasks import (
    extract_answer_background_task,
    upload_answers_engine_async,
)
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    GradingState,
    StudentSubmission,
)
from students.services import MAX_STUDENT_SUBMISSION_ATTEMPTS
from students.tests_post_grading_submission_lock import (
    PDF_BYTES,
    _classroom,
    _submission,
)

CLOSED = "This submission can no longer be changed."
BUSY = "This submission can't be changed right now. Please try again later."
CLOSED_REFUSAL = {"error": CLOSED, "code": "submission_closed"}
BUSY_REFUSAL = {"error": BUSY, "code": "submission_busy"}


def plain(value):
    return json.loads(json.dumps(value, default=str))


def pdf():
    return SimpleUploadedFile("answers.pdf", PDF_BYTES, content_type="application/pdf")


class RefusalBase(APITestCase):
    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = _classroom("h133")
        self.client.force_authenticate(user=self.student)

    def another_assignment(self, title):
        return Assignment.objects.create(
            title=title,
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )

    def upload_url(self, assignment, name="student-submission-upload-answers"):
        return reverse(name, kwargs={"assignment_id": assignment.pk})

    def detail_url(self, submission):
        return reverse("student-submission-detail", kwargs={"pk": submission.pk})

    def assert_refused(self, response, expected):
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(plain(response.data), expected)

    def assert_names_no_grading(self, response):
        """No form of the word: graded, grading, grade."""
        self.assertNotIn("grad", json.dumps(plain(response.data)).lower())


class StudentToldNothingOfAGradeTest(RefusalBase):
    """A paper that is graded, released or not: closed, in neutral words."""

    def setUp(self):
        super().setUp()
        self.submission = _submission(self.assignment, self.student, graded=True)

    @patch("students.views.AssignmentProcessingService.prepare_ai_content")
    def test_the_upload_refusal(self, mock_prepare):
        response = self.client.post(
            self.upload_url(self.assignment), {"answer": pdf()}, format="multipart"
        )
        self.assert_refused(response, CLOSED_REFUSAL)
        self.assert_names_no_grading(response)
        mock_prepare.assert_not_called()

    @patch("students.views.launch_processing_task")
    def test_the_queued_upload_refusal(self, mock_launch):
        response = self.client.post(
            self.upload_url(self.assignment, "student-submission-upload-async"),
            {"answer": pdf()},
            format="multipart",
        )
        self.assert_refused(response, CLOSED_REFUSAL)
        self.assert_names_no_grading(response)
        mock_launch.assert_not_called()

    @patch("students.services.ai_processor")
    def test_the_edit_refusal(self, mock_ai):
        response = self.client.patch(
            self.detail_url(self.submission), {"raw_input": "edited"}, format="json"
        )
        self.assert_refused(response, CLOSED_REFUSAL)
        self.assert_names_no_grading(response)
        mock_ai.extract_answer_with_retry.assert_not_called()

    @patch("students.services.ai_processor")
    def test_the_refusal_is_the_same_once_the_grade_is_released(self, mock_ai):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            is_published=True
        )
        response = self.client.patch(
            self.detail_url(self.submission), {"raw_input": "edited"}, format="json"
        )
        self.assert_refused(response, CLOSED_REFUSAL)

    @patch("students.services.ai_processor")
    @patch("assignments.tasks.AssignmentProcessingService.prepare_ai_content")
    @patch("assignments.tasks.AssignmentProcessingService.rebuild_uploaded_file")
    def test_a_queued_upload_that_runs_after_the_grade_landed(
        self, mock_rebuild, mock_prepare, mock_ai
    ):
        """The background task's result and the tracked row the student can
        poll: the neutral sentence and the code, there too."""
        mock_prepare.return_value = "content"
        tracked = BackgroundProcessingTask.objects.create(
            requested_by=self.student,
            task_type="answer_extraction",
            assignment=self.assignment,
        )
        result = upload_answers_engine_async.apply(
            args=(
                str(self.assignment.id),
                {"name": "x"},
                "prompt",
                str(self.student.id),
            ),
            kwargs={"processing_task_id": str(tracked.id)},
        ).get()

        self.assertEqual(result["status"], "FAILURE")
        self.assertEqual(result["message"], CLOSED)
        self.assertEqual(result["code"], "submission_closed")
        tracked.refresh_from_db()
        self.assertEqual(tracked.status, BackgroundTaskStatus.FAILURE)
        self.assertEqual(tracked.error, CLOSED)
        mock_ai.extract_answer_with_retry.assert_not_called()


class StudentCannotTellBusyKindsApartTest(RefusalBase):
    """A paper that is being graded, and one whose earlier upload is still
    being processed: one answer for both, in status, code and sentence."""

    def being_graded(self):
        submission = _submission(
            self.another_assignment("being graded"), self.student, graded=False
        )
        StudentSubmission.objects.filter(pk=submission.pk).update(
            grading_state=GradingState.RUNNING, grading_started_at=timezone.now()
        )
        return submission

    def being_processed(self):
        assignment = self.another_assignment("being processed")
        BackgroundProcessingTask.objects.create(
            requested_by=self.student,
            task_type="answer_extraction",
            assignment=assignment,
            status=BackgroundTaskStatus.PENDING,
        )
        return assignment

    @patch("students.views.AssignmentProcessingService.prepare_ai_content")
    def test_an_upload_while_the_paper_is_being_graded(self, mock_prepare):
        submission = self.being_graded()
        response = self.client.post(
            self.upload_url(submission.assignment),
            {"answer": pdf()},
            format="multipart",
        )
        self.assert_refused(response, BUSY_REFUSAL)
        self.assert_names_no_grading(response)
        mock_prepare.assert_not_called()

    @patch("students.services.ai_processor")
    def test_an_edit_while_the_paper_is_being_graded(self, mock_ai):
        submission = self.being_graded()
        response = self.client.patch(
            self.detail_url(submission), {"raw_input": "edited"}, format="json"
        )
        self.assert_refused(response, BUSY_REFUSAL)
        self.assert_names_no_grading(response)

    @patch("students.views.launch_processing_task")
    def test_the_two_busy_cases_get_one_and_the_same_answer(self, mock_launch):
        graded_now = self.being_graded()
        processed_now = self.being_processed()

        while_grading = self.client.post(
            self.upload_url(graded_now.assignment, "student-submission-upload-async"),
            {"answer": pdf()},
            format="multipart",
        )
        while_processing = self.client.post(
            self.upload_url(processed_now, "student-submission-upload-async"),
            {"answer": pdf()},
            format="multipart",
        )

        self.assert_refused(while_grading, BUSY_REFUSAL)
        self.assert_refused(while_processing, BUSY_REFUSAL)
        self.assertEqual(while_grading.status_code, while_processing.status_code)
        self.assertEqual(plain(while_grading.data), plain(while_processing.data))
        mock_launch.assert_not_called()


class TheOtherRefusalsTest(RefusalBase):
    @patch("students.views.AssignmentProcessingService.prepare_ai_content")
    def test_attempts_used_keeps_its_sentence_and_gains_a_code(self, mock_prepare):
        _submission(
            self.assignment,
            self.student,
            graded=False,
            attempts=MAX_STUDENT_SUBMISSION_ATTEMPTS,
        )
        response = self.client.post(
            self.upload_url(self.assignment), {"answer": pdf()}, format="multipart"
        )
        self.assert_refused(
            response,
            {
                "error": "You have reached the maximum of "
                f"{MAX_STUDENT_SUBMISSION_ATTEMPTS} submissions for this assignment",
                "code": "submission_attempts_used",
            },
        )

    @patch("students.services.ai_processor")
    def test_the_teacher_is_still_told_that_the_paper_is_graded(self, mock_ai):
        submission = _submission(self.assignment, self.student, graded=True)
        self.client.force_authenticate(user=self.teacher)
        response = self.client.patch(
            self.detail_url(submission), {"raw_input": "edited"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data["error"],
            "This assignment has already been graded, so it can no longer "
            "be submitted again.",
        )
        self.assertEqual(response.data["code"], "submission_closed")

    @patch("students.services.ai_processor")
    def test_the_teacher_is_still_told_that_the_paper_is_being_graded(self, mock_ai):
        submission = _submission(self.assignment, self.student, graded=False)
        StudentSubmission.objects.filter(pk=submission.pk).update(
            grading_state=GradingState.RUNNING, grading_started_at=timezone.now()
        )
        self.client.force_authenticate(user=self.teacher)
        response = self.client.patch(
            self.detail_url(submission), {"raw_input": "edited"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            response.data["error"],
            "This submission is being graded right now, so it cannot be "
            "replaced. Please try again once grading has finished.",
        )
        self.assertEqual(response.data["code"], "submission_busy")


class StudentListShowsNoGradingStateTest(APITestCase):
    """GET /submissions/ as the student. One submission, put in each state
    in turn; the list caches its answer per caller, so the cache is cleared
    between readings."""

    SCHEDULED = {
        "scheduled_grading_at": timezone.now() + timedelta(days=1),
        "grading_task_name": "scheduled-grading-task-name",
    }

    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = _classroom("h133l")
        self.submission = _submission(self.assignment, self.student, graded=False)
        self.url = reverse("student-submission-list")
        self.client.force_authenticate(user=self.student)

    def put(self, **fields):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(**fields)

    def row(self, user=None, query=None):
        cache.clear()
        if user is not None:
            self.client.force_authenticate(user=user)
        response = self.client.get(self.url, query or {})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = [
            row
            for row in response.data["results"]
            if str(row["id"]) == str(self.submission.pk)
        ]
        self.assertEqual(len(rows), 1)
        return plain(rows[0])

    def test_a_submitted_paper(self):
        row = self.row()
        self.assertEqual(row["grading_state"], "IDLE")
        self.assertIsNone(row["scheduled_grading_at"])
        self.assertIsNone(row["grading_task_name"])
        self.assertIs(row["is_grading_scheduled"], False)

    def test_scheduled_being_graded_and_failed_look_like_submitted(self):
        submitted = self.row()
        for state in (
            dict(self.SCHEDULED),
            {
                "grading_state": GradingState.RUNNING,
                "grading_started_at": timezone.now(),
            },
            {"grading_state": GradingState.FAILED},
        ):
            with self.subTest(state=sorted(state)):
                self.put(**state)
                self.assertEqual(self.row(), submitted)

    def test_graded_but_unreleased_looks_like_submitted_but_for_the_attempts(self):
        """The whole row, compared. The one difference is the founder's
        choice: the attempts left drop to 0 when a paper is graded."""
        submitted = self.row()
        self.put(
            graded_at=timezone.now(),
            score=8,
            score_percentage=80,
            grading_state=GradingState.DONE,
            needs_review=True,
            review_tier="critical",
            **self.SCHEDULED,
        )
        graded = self.row()

        self.assertGreater(submitted.pop("remaining_attempts"), 0)
        self.assertEqual(graded.pop("remaining_attempts"), 0)
        self.assertEqual(graded, submitted)

    def test_a_released_grade_shows_done(self):
        self.put(
            graded_at=timezone.now(),
            score=8,
            score_percentage=80,
            grading_state=GradingState.DONE,
            is_published=True,
        )
        row = self.row()
        self.assertEqual(row["grading_state"], "DONE")
        self.assertIsNone(row["scheduled_grading_at"])
        self.assertIsNone(row["grading_task_name"])
        self.assertIs(row["is_grading_scheduled"], False)

    def test_the_teacher_still_sees_the_state_and_the_schedule(self):
        self.put(grading_state=GradingState.FAILED, **self.SCHEDULED)
        row = self.row(user=self.teacher)
        self.assertEqual(row["grading_state"], "FAILED")
        self.assertIsNotNone(row["scheduled_grading_at"])
        self.assertEqual(row["grading_task_name"], "scheduled-grading-task-name")
        self.assertIs(row["is_grading_scheduled"], True)

    def test_a_student_cannot_filter_on_the_grading_state(self):
        self.put(grading_state=GradingState.DONE, graded_at=timezone.now())
        for value in ("DONE", "IDLE", "RUNNING", "FAILED"):
            with self.subTest(grading_state=value):
                cache.clear()
                response = self.client.get(self.url, {"grading_state": value})
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_a_student_can_still_ask_for_released_grades(self):
        """`is_published` is false both before grading and before release,
        so it tells nothing of an unreleased grade."""
        self.put(graded_at=timezone.now(), grading_state=GradingState.DONE)
        cache.clear()
        response = self.client.get(self.url, {"is_published": "true"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["results"], [])
        self.assertEqual(
            self.row(query={"is_published": "false"})["id"], str(self.submission.pk)
        )

    def test_the_teacher_can_still_filter_on_the_grading_state(self):
        self.put(grading_state=GradingState.FAILED)
        row = self.row(user=self.teacher, query={"grading_state": "FAILED"})
        self.assertEqual(row["grading_state"], "FAILED")


EXTRACTED = {
    "answers": [{"question_number": 1, "answer_html": "re-upload"}],
    "extraction_confidence": 80,
}


class StudentPollsARefusedTaskTest(APITestCase):
    """A queued upload or edit that is refused inside the background task.
    The student learns of it by asking the task-status route, which serves
    the tracked row, not the task's return value. Each case runs the real
    task, then reads that route AS THE STUDENT: the neutral sentence, the
    stable code, and no form of the word "grade" anywhere in the answer.

    The three checks of an upload (before the extraction, after it, and
    under the row lock) and the two of an edit are each reached once."""

    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = _classroom("h133p")
        self.submission = _submission(self.assignment, self.student, graded=False)
        self.client.force_authenticate(user=self.student)

    # -- what can land on the row while the extraction is running
    def a_grade_lands(self, *args, **kwargs):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            graded_at=timezone.now(), score=8, grading_state=GradingState.DONE
        )
        return EXTRACTED

    def a_grading_claim_lands(self, *args, **kwargs):
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            grading_state=GradingState.RUNNING, grading_started_at=timezone.now()
        )
        return EXTRACTED

    # -- the two real tasks, run for the student, with a tracked row
    def tracked(self, **extra):
        return BackgroundProcessingTask.objects.create(
            requested_by=self.student,
            task_type="answer_extraction",
            assignment=self.assignment,
            celery_task_id=str(uuid.uuid4()),
            **extra,
        )

    def run_upload(self, tracked):
        with patch(
            "assignments.tasks.AssignmentProcessingService.prepare_ai_content",
            return_value="content",
        ), patch("assignments.tasks.AssignmentProcessingService.rebuild_uploaded_file"):
            return upload_answers_engine_async.apply(
                args=(
                    str(self.assignment.id),
                    {"name": "x"},
                    "prompt",
                    str(self.student.id),
                ),
                kwargs={"processing_task_id": str(tracked.id)},
            ).get()

    def run_edit(self, tracked):
        return extract_answer_background_task.apply(
            args=(str(self.submission.id), "edited text", str(self.student.id)),
            kwargs={"processing_task_id": str(tracked.id)},
            task_id=str(uuid.uuid4()),
        ).get()

    def assert_student_reads(self, tracked, result, sentence, code):
        # the task's own result
        self.assertEqual(result["status"], "FAILURE")
        self.assertEqual(result["message"], sentence)
        self.assertEqual(result["code"], code)
        # the tracked row
        tracked.refresh_from_db()
        self.assertEqual(tracked.status, BackgroundTaskStatus.FAILURE)
        self.assertEqual(tracked.error, sentence)
        self.assertEqual(tracked.meta.get("code"), code)
        # what the student is answered when they ask
        response = self.client.get(
            reverse("task-task-status", kwargs={"task_id": tracked.celery_task_id})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "failed")
        self.assertIn(sentence, response.data["meta"])
        self.assertIn(f"'code': {code!r}", response.data["meta"])
        self.assertNotIn("grad", json.dumps(plain(response.data)).lower())

    # -- the upload task
    @patch("students.services.ai_processor")
    def test_upload_refused_before_the_extraction(self, mock_ai):
        self.a_grade_lands()
        tracked = self.tracked()
        result = self.run_upload(tracked)
        self.assert_student_reads(tracked, result, CLOSED, "submission_closed")
        mock_ai.extract_answer_with_retry.assert_not_called()

    @patch("students.services.ai_processor")
    def test_upload_when_a_grade_lands_during_the_extraction(self, mock_ai):
        """Caught by the check after the AI call."""
        mock_ai.extract_answer_with_retry.side_effect = self.a_grade_lands
        tracked = self.tracked()
        result = self.run_upload(tracked)
        self.assert_student_reads(tracked, result, CLOSED, "submission_closed")
        self.assertEqual(mock_ai.extract_answer_with_retry.call_count, 1)

    @patch("students.services.ensure_student_may_submit")
    @patch("students.services.ai_processor")
    def test_upload_when_only_the_check_under_the_row_lock_can_catch_it(
        self, mock_ai, mock_earlier_checks
    ):
        """The two earlier checks are taken out, so the answer is the one
        the check under the row lock gives."""
        mock_ai.extract_answer_with_retry.side_effect = self.a_grade_lands
        tracked = self.tracked()
        result = self.run_upload(tracked)
        self.assert_student_reads(tracked, result, CLOSED, "submission_closed")
        self.assertTrue(mock_earlier_checks.called)

    @patch("students.services.ai_processor")
    def test_upload_when_a_grading_claim_lands_during_the_extraction(self, mock_ai):
        mock_ai.extract_answer_with_retry.side_effect = self.a_grading_claim_lands
        tracked = self.tracked()
        result = self.run_upload(tracked)
        self.assert_student_reads(tracked, result, BUSY, "submission_busy")

    @patch("students.services.ensure_student_may_submit")
    @patch("students.services.ai_processor")
    def test_upload_claim_caught_only_by_the_check_under_the_row_lock(
        self, mock_ai, mock_earlier_checks
    ):
        mock_ai.extract_answer_with_retry.side_effect = self.a_grading_claim_lands
        tracked = self.tracked()
        result = self.run_upload(tracked)
        self.assert_student_reads(tracked, result, BUSY, "submission_busy")

    # -- the edit task
    @patch("students.services.ai_processor")
    def test_edit_refused_before_the_extraction(self, mock_ai):
        self.a_grade_lands()
        tracked = self.tracked(submission=self.submission)
        result = self.run_edit(tracked)
        self.assert_student_reads(tracked, result, CLOSED, "submission_closed")
        mock_ai.extract_answer_with_retry.assert_not_called()

    @patch("students.services.ai_processor")
    def test_edit_when_a_grade_lands_during_the_extraction(self, mock_ai):
        """Caught by the check under the row lock, the edit's second."""
        mock_ai.extract_answer_with_retry.side_effect = self.a_grade_lands
        tracked = self.tracked(submission=self.submission)
        result = self.run_edit(tracked)
        self.assert_student_reads(tracked, result, CLOSED, "submission_closed")
        self.assertEqual(mock_ai.extract_answer_with_retry.call_count, 1)

    @patch("students.services.ai_processor")
    def test_edit_when_a_grading_claim_lands_during_the_extraction(self, mock_ai):
        mock_ai.extract_answer_with_retry.side_effect = self.a_grading_claim_lands
        tracked = self.tracked(submission=self.submission)
        result = self.run_edit(tracked)
        self.assert_student_reads(tracked, result, BUSY, "submission_busy")

    @patch("students.services.ai_processor")
    def test_edit_refused_while_the_paper_is_being_graded(self, mock_ai):
        self.a_grading_claim_lands()
        tracked = self.tracked(submission=self.submission)
        result = self.run_edit(tracked)
        self.assert_student_reads(tracked, result, BUSY, "submission_busy")
        mock_ai.extract_answer_with_retry.assert_not_called()


class TheRefusalCodesAreAClosedListTest(TestCase):
    def test_each_closing_refusal_has_its_code(self):
        from students.exceptions import (
            SubmissionAlreadyGradedError,
            SubmissionBeingGradedError,
            SubmissionLimitReachedError,
            SubmissionProcessingInProgressError,
        )

        self.assertEqual(
            {
                SubmissionAlreadyGradedError: "submission_closed",
                SubmissionBeingGradedError: "submission_busy",
                SubmissionProcessingInProgressError: "submission_busy",
                SubmissionLimitReachedError: "submission_attempts_used",
            },
            {
                error: getattr(error, "code", None)
                for error in (
                    SubmissionAlreadyGradedError,
                    SubmissionBeingGradedError,
                    SubmissionProcessingInProgressError,
                    SubmissionLimitReachedError,
                )
            },
        )
