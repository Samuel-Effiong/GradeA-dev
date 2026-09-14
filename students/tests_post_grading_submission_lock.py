"""
Product rule (owner, 2026-09-13): once a student's submission for an
assignment has been successfully graded, that student may not submit again
for that assignment. Enforced server-side in students.services and checked
again at every API entry point.

What is proven here, and how:

* Service layer - the rule fires BEFORE the billed extraction call, and
  again under the row lock after it (a grade can land during extraction).
* API layer - the synchronous upload, the asynchronous upload and the
  raw-text edit (which re-extracts) all refuse with 409, before creating
  any tracked task and before any AI work. Replays, different bytes and
  different filenames make no difference.
* Task replay - a queued upload that reaches a worker after the grade
  landed is refused inside the task, and the tracked task records why.
* Scope - another student on the same assignment, and the same student on
  another assignment, are unaffected.
* Concurrency - N real threads against a graded submission, at the service
  layer and over real HTTP (LiveServerTestCase + JWT), all refused, row
  untouched. An upload racing the grade's own commit cannot deadlock it or
  corrupt the claim.
* Grading in progress - NOT a new rule. A submission whose grading is
  RUNNING still accepts an upload (previously agreed behaviour); what is
  proven is that the upload cannot touch grading_state/grading_started_at
  and that the grade still lands afterwards.

Run with:
    python manage.py test students.tests_post_grading_submission_lock
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

import requests
from django.db import connection
from django.test import LiveServerTestCase, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from assignments.models import Assignment, AssignmentStatus
from assignments.tasks import upload_answers_engine_async
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.exceptions import SubmissionAlreadyGradedError
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    GradingState,
    StudentSubmission,
)
from students.services import (
    grade_engine,
    remaining_student_attempts,
    upload_answers_engine,
)
from users.models import CustomUser, UserTypes

PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
EXTRACTED = {
    "answers": [{"question_number": 1, "answer_html": "re-upload"}],
    "extraction_confidence": 80,
}
VALID_GRADE = {
    "grading_summary": {"total_score": 8, "max_total_points": 10, "percentage": 80.0},
    "grading_confidence": 90,
    "question_evaluations": [],
}


def _user(tag, user_type, **extra):
    # Names are unique per tag: enrolment refuses a second student with an
    # identical full name on one course. Active + verified so a real JWT
    # login (the live-server test) is accepted.
    return CustomUser.objects.create_user(
        email=f"{tag}@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=user_type,
        first_name=tag.replace("-", " ").title(),
        last_name="Lock",
        is_active=True,
        email_verified_at=timezone.now(),
        **extra,
    )


def _fund(teacher):
    wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
    CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        total_credits=100_000,
        used_credits=0,
        expires_at=timezone.now() + timedelta(days=30),
    )


def _classroom(tag):
    """Teacher (funded), one enrolled student, one PUBLISHED assignment."""
    teacher = _user(f"{tag}-teacher", UserTypes.TEACHER)
    _fund(teacher)
    student = _user(f"{tag}-student", UserTypes.STUDENT)
    session = Session.objects.create(name="S", teacher=teacher)
    course = Course.objects.create(name="C", teacher=teacher, session=session)
    StudentCourse.objects.create(
        student=student, course=course, enrollment_status=EnrollmentStatusType.ENROLLED
    )
    assignment = Assignment.objects.create(
        title="A",
        course=course,
        status=AssignmentStatus.PUBLISHED,
        questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
    )
    return teacher, student, course, assignment


def _submission(assignment, student, *, graded, attempts=1):
    submission = StudentSubmission.objects.create(
        assignment=assignment,
        student=student,
        answers=[{"question_number": 1, "answer_html": "original"}],
        attempt_count=attempts,
    )
    if graded:
        StudentSubmission.objects.filter(pk=submission.pk).update(
            graded_at=timezone.now(),
            score=8,
            score_percentage=80,
            max_points=10,
            feedback=VALID_GRADE,
            grading_state=GradingState.DONE,
        )
        submission.refresh_from_db()
    return submission


def _snapshot(submission):
    submission.refresh_from_db()
    return (
        submission.answers,
        submission.attempt_count,
        submission.raw_input,
        submission.extraction_confidence,
        submission.graded_at,
        submission.score,
        submission.grading_state,
    )


class PostGradingLockServiceTest(TestCase):
    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = _classroom("svc")

    @patch("students.services.ai_processor")
    def test_graded_submission_refuses_before_the_billed_extraction(self, mock_ai):
        submission = _submission(self.assignment, self.student, graded=True)
        before = _snapshot(submission)

        with self.assertRaises(SubmissionAlreadyGradedError):
            upload_answers_engine(self.assignment, "ignored", self.student)

        # No credit was spent finding out, and nothing changed.
        mock_ai.extract_answer_with_retry.assert_not_called()
        self.assertEqual(_snapshot(submission), before)

    @patch("students.services.ai_processor")
    def test_grade_landing_during_extraction_is_caught_by_the_locked_check(
        self, mock_ai
    ):
        submission = _submission(self.assignment, self.student, graded=False)

        def grade_lands_meanwhile(*args, **kwargs):
            StudentSubmission.objects.filter(pk=submission.pk).update(
                graded_at=timezone.now(), score=8, grading_state=GradingState.DONE
            )
            return EXTRACTED

        mock_ai.extract_answer_with_retry.side_effect = grade_lands_meanwhile

        with self.assertRaises(SubmissionAlreadyGradedError):
            upload_answers_engine(self.assignment, "ignored", self.student)

        submission.refresh_from_db()
        self.assertEqual(submission.answers[0]["answer_html"], "original")
        self.assertEqual(submission.attempt_count, 1)

    @patch("students.services.ai_processor")
    def test_ungraded_submission_is_still_accepted(self, mock_ai):
        submission = _submission(self.assignment, self.student, graded=False)
        mock_ai.extract_answer_with_retry.return_value = EXTRACTED

        upload_answers_engine(self.assignment, "ignored", self.student)

        submission.refresh_from_db()
        self.assertEqual(submission.answers[0]["answer_html"], "re-upload")
        self.assertEqual(submission.attempt_count, 2)

    @patch("students.services.ai_processor")
    def test_lock_is_scoped_to_the_graded_student_and_assignment(self, mock_ai):
        _submission(self.assignment, self.student, graded=True)
        mock_ai.extract_answer_with_retry.return_value = EXTRACTED

        # Another student, same assignment.
        other = _user("svc-other", UserTypes.STUDENT)
        StudentCourse.objects.create(
            student=other,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        created = upload_answers_engine(self.assignment, "ignored", other)
        self.assertEqual(created.student, other)

        # Same student, another assignment.
        other_assignment = Assignment.objects.create(
            title="B",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[{"question_number": 1, "question_text": "Q?", "points": 5}],
        )
        created = upload_answers_engine(other_assignment, "ignored", self.student)
        self.assertEqual(created.assignment, other_assignment)

    @patch("students.services.ai_processor")
    def test_upload_while_grading_is_running_is_accepted_and_leaves_the_claim_alone(
        self, mock_ai
    ):
        # Previously agreed behaviour, deliberately preserved: RUNNING is
        # not "graded". What must hold is that the upload cannot disturb
        # the active claim.
        submission = _submission(self.assignment, self.student, graded=False)
        claimed_at = timezone.now() - timedelta(seconds=30)
        StudentSubmission.objects.filter(pk=submission.pk).update(
            grading_state=GradingState.RUNNING, grading_started_at=claimed_at
        )
        mock_ai.extract_answer_with_retry.return_value = EXTRACTED

        upload_answers_engine(self.assignment, "ignored", self.student)

        submission.refresh_from_db()
        self.assertEqual(submission.answers[0]["answer_html"], "re-upload")
        self.assertEqual(submission.grading_state, GradingState.RUNNING)
        self.assertEqual(submission.grading_started_at, claimed_at)

    def test_remaining_attempts_is_zero_once_graded(self):
        submission = _submission(self.assignment, self.student, graded=True, attempts=1)
        self.assertEqual(remaining_student_attempts(submission), 0)
        ungraded = _submission(
            Assignment.objects.create(
                title="B",
                course=self.course,
                status=AssignmentStatus.PUBLISHED,
                questions=[],
            ),
            self.student,
            graded=False,
            attempts=1,
        )
        self.assertEqual(remaining_student_attempts(ungraded), 2)
        self.assertEqual(remaining_student_attempts(None), 3)

    @patch("students.services.ai_processor")
    def test_recorded_assumption_teacher_proxy_upload_is_not_a_student_submission(
        self, mock_ai
    ):
        """The rule is about the STUDENT submitting again. A teacher
        re-uploading a scan on the student's behalf is not covered by it
        and stays allowed. This is the assumption recorded for the owner;
        if the rule is meant to close the row to everyone, this is the
        one test that flips."""
        submission = _submission(self.assignment, self.student, graded=True)
        mock_ai.extract_answer_with_retry.return_value = {
            **EXTRACTED,
            "student_name": f"{self.student.first_name} {self.student.last_name}",
        }

        result = upload_answers_engine(
            self.assignment, "ignored", self.teacher, is_proxy_upload=True
        )

        self.assertEqual(result.pk, submission.pk)
        self.assertEqual(result.answers[0]["answer_html"], "re-upload")


class PostGradingLockAPITest(APITestCase):
    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = _classroom("api")
        self.submission = _submission(self.assignment, self.student, graded=True)
        self.upload_url = reverse(
            "student-submission-upload-answers",
            kwargs={"assignment_id": self.assignment.pk},
        )
        self.async_url = reverse(
            "student-submission-upload-async",
            kwargs={"assignment_id": self.assignment.pk},
        )
        self.detail_url = reverse(
            "student-submission-detail", kwargs={"pk": self.submission.pk}
        )
        self.client.force_authenticate(user=self.student)

    def _file(self, name="answers.pdf", content=PDF_BYTES):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(name, content, content_type="application/pdf")

    @patch("students.views.AssignmentProcessingService.prepare_ai_content")
    def test_sync_upload_is_refused_with_409_before_any_ai_work(self, mock_prepare):
        before = _snapshot(self.submission)

        response = self.client.post(
            self.upload_url, {"answer": self._file()}, format="multipart"
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("already been graded", response.data["error"])
        mock_prepare.assert_not_called()
        self.assertEqual(_snapshot(self.submission), before)

    @patch("students.views.launch_processing_task")
    def test_async_upload_is_refused_with_409_and_creates_no_tracked_task(
        self, mock_launch
    ):
        response = self.client.post(
            self.async_url, {"answer": self._file()}, format="multipart"
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("already been graded", response.data["error"])
        mock_launch.assert_not_called()
        self.assertFalse(BackgroundProcessingTask.objects.exists())

    @patch("students.views.ai_processor")
    def test_raw_text_edit_is_refused_with_409(self, mock_ai):
        # The edit endpoint re-extracts and overwrites `answers`, so the
        # rule applies to it. Note the permission mapping in
        # StudentSubmissionViewSet.get_permissions routes PATCH
        # (partial_update) to teacher-only - a student gets 403 before
        # reaching this rule (recorded as R-6 finding V-3) - so the guard
        # is exercised as the teacher who owns the course.
        self.client.force_authenticate(user=self.student)
        response = self.client.patch(
            self.detail_url, {"raw_input": "edited"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.teacher)
        response = self.client.patch(
            self.detail_url, {"raw_input": "edited"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        mock_ai.extract_answer_with_retry.assert_not_called()

    @patch("students.views.AssignmentProcessingService.prepare_ai_content")
    def test_replays_and_different_content_or_filenames_are_all_refused(
        self, mock_prepare
    ):
        attempts = [
            ("answers.pdf", PDF_BYTES),
            ("answers.pdf", PDF_BYTES),  # exact replay
            ("second-try.pdf", PDF_BYTES),  # different filename
            ("answers.pdf", PDF_BYTES + b"% different bytes\n"),  # different content
            ("scan.png", b"\x89PNG\r\n\x1a\n" + b"0" * 32),  # different type
        ]
        for name, content in attempts:
            with self.subTest(name=name):
                response = self.client.post(
                    self.upload_url,
                    {"answer": self._file(name, content)},
                    format="multipart",
                )
                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        mock_prepare.assert_not_called()
        self.assertEqual(StudentSubmission.objects.count(), 1)

    @patch("students.services.ai_processor")
    @patch("students.views.AssignmentProcessingService.prepare_ai_content")
    def test_grade_landing_mid_request_returns_409_and_persists_nothing(
        self, mock_prepare, mock_ai
    ):
        # An UNGRADED submission whose grade lands while the extraction is
        # in flight: the early check passes, the row-locked check refuses.
        ungraded = _submission(
            Assignment.objects.create(
                title="B",
                course=self.course,
                status=AssignmentStatus.PUBLISHED,
                questions=[{"question_number": 1, "points": 10}],
            ),
            self.student,
            graded=False,
        )
        mock_prepare.return_value = "content"

        def grade_lands(*args, **kwargs):
            StudentSubmission.objects.filter(pk=ungraded.pk).update(
                graded_at=timezone.now(), score=8, grading_state=GradingState.DONE
            )
            return EXTRACTED

        mock_ai.extract_answer_with_retry.side_effect = grade_lands

        response = self.client.post(
            reverse(
                "student-submission-upload-answers",
                kwargs={"assignment_id": ungraded.assignment_id},
            ),
            {"answer": self._file()},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        ungraded.refresh_from_db()
        self.assertEqual(ungraded.answers[0]["answer_html"], "original")
        self.assertEqual(ungraded.attempt_count, 1)

    @patch("students.services.ai_processor")
    @patch("students.views.AssignmentProcessingService.prepare_ai_content")
    def test_another_student_can_still_submit_the_same_assignment(
        self, mock_prepare, mock_ai
    ):
        other = _user("api-other", UserTypes.STUDENT)
        StudentCourse.objects.create(
            student=other,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        mock_prepare.return_value = "content"
        mock_ai.extract_answer_with_retry.return_value = EXTRACTED
        self.client.force_authenticate(user=other)

        response = self.client.post(
            self.upload_url, {"answer": self._file()}, format="multipart"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            StudentSubmission.objects.filter(
                assignment=self.assignment, student=other
            ).exists()
        )

    def test_unauthenticated_and_wrong_role_never_reach_the_rule(self):
        self.client.force_authenticate(user=None)
        response = self.client.post(
            self.upload_url, {"answer": self._file()}, format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(
            self.upload_url, {"answer": self._file()}, format="multipart"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_detail_reports_zero_remaining_attempts_once_graded(self):
        response = self.client.get(self.detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["remaining_attempts"], 0)


class PostGradingLockTaskReplayTest(TestCase):
    """A queued upload that a worker only picks up after the grade landed
    (a delayed message, a retry, a redelivery) is refused inside the task,
    with the reason on the tracked row - and the extraction is never
    billed."""

    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = _classroom("task")
        self.submission = _submission(self.assignment, self.student, graded=True)

    @patch("students.services.ai_processor")
    @patch("assignments.tasks.AssignmentProcessingService.prepare_ai_content")
    @patch("assignments.tasks.AssignmentProcessingService.rebuild_uploaded_file")
    def test_replayed_upload_task_is_refused_and_recorded(
        self, mock_rebuild, mock_prepare, mock_ai
    ):
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

        # A refusal is final: reported once, never retried, never billed.
        self.assertEqual(result["status"], "FAILURE")
        self.assertIn("already been graded", result["message"])
        mock_ai.extract_answer_with_retry.assert_not_called()
        self.assertEqual(mock_prepare.call_count, 1)
        tracked.refresh_from_db()
        self.assertEqual(tracked.status, BackgroundTaskStatus.FAILURE)
        self.assertIn("already been graded", tracked.error)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.answers[0]["answer_html"], "original")

    @patch("assignments.tasks.upload_answers_engine")
    @patch("assignments.tasks.AssignmentProcessingService.prepare_ai_content")
    @patch("assignments.tasks.AssignmentProcessingService.rebuild_uploaded_file")
    def test_transient_failure_is_retried_and_the_retrys_success_is_recorded(
        self, mock_rebuild, mock_prepare, mock_engine
    ):
        """Before this pass the task marked the tracked row FAILURE before
        every retry, and the terminal-status guard then blocked the retry's
        SUCCESS - the UI showed a failed upload for a submission that had
        been created."""
        mock_prepare.return_value = "content"
        ungraded = _submission(
            Assignment.objects.create(
                title="B",
                course=self.course,
                status=AssignmentStatus.PUBLISHED,
                questions=[],
            ),
            self.student,
            graded=False,
        )
        mock_engine.side_effect = [TimeoutError("model timed out"), ungraded]
        tracked = BackgroundProcessingTask.objects.create(
            requested_by=self.student,
            task_type="answer_extraction",
            assignment=ungraded.assignment,
        )

        result = upload_answers_engine_async.apply(
            args=(
                str(ungraded.assignment_id),
                {"name": "x"},
                "prompt",
                str(self.student.id),
            ),
            kwargs={"processing_task_id": str(tracked.id)},
        ).get()

        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(mock_engine.call_count, 2)
        tracked.refresh_from_db()
        self.assertEqual(tracked.status, BackgroundTaskStatus.SUCCESS)
        self.assertEqual(tracked.error, "")
        # The retry itself was recorded on the row (the success step then
        # replaces "Retrying (1/3)", but the classified reason stays).
        self.assertIn("timed out", tracked.meta["last_error"])
        self.assertEqual(tracked.meta["step"], "Answers extracted successfully")


class PostGradingLockConcurrencyTest(TransactionTestCase):
    WORKERS = 8

    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = _classroom("conc")

    def test_concurrent_uploads_against_a_graded_submission_are_all_refused(self):
        submission = _submission(self.assignment, self.student, graded=True)
        before = _snapshot(submission)
        outcomes = []
        barrier = threading.Barrier(self.WORKERS)

        def attempt():
            try:
                barrier.wait(timeout=10)
                with patch("students.services.ai_processor") as mock_ai:
                    mock_ai.extract_answer_with_retry.return_value = EXTRACTED
                    upload_answers_engine(self.assignment, "ignored", self.student)
                outcomes.append("accepted")
            except SubmissionAlreadyGradedError:
                outcomes.append("refused")
            except Exception as exc:  # pragma: no cover - surfaced by assert
                outcomes.append(repr(exc))
            finally:
                connection.close()

        threads = [threading.Thread(target=attempt) for _ in range(self.WORKERS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        self.assertEqual(outcomes, ["refused"] * self.WORKERS)
        self.assertEqual(_snapshot(submission), before)

    def test_upload_racing_the_grades_own_commit_neither_deadlocks_nor_corrupts_it(
        self,
    ):
        """The grade's final save and an upload's row lock contend on the
        same row. Whichever order the database picks, the grade must land,
        the claim fields must be exactly what the grader wrote, and once
        graded the row is closed."""
        submission = _submission(self.assignment, self.student, graded=False)
        grade_may_finish = threading.Event()
        errors = []

        def grade():
            try:
                with patch("students.services.ai_processor") as mock_ai:

                    def slow_grade(*args, **kwargs):
                        grade_may_finish.wait(timeout=10)
                        return VALID_GRADE

                    mock_ai.extract_grade_with_retry.side_effect = slow_grade
                    grade_engine(self.teacher, submission)
            except Exception as exc:  # pragma: no cover
                errors.append(("grade", repr(exc)))
            finally:
                connection.close()

        upload_outcome = []

        def upload():
            try:
                with patch("students.services.ai_processor") as mock_ai:
                    mock_ai.extract_answer_with_retry.return_value = EXTRACTED
                    upload_answers_engine(self.assignment, "ignored", self.student)
                upload_outcome.append("accepted")
            except SubmissionAlreadyGradedError:
                upload_outcome.append("refused")
            except Exception as exc:  # pragma: no cover
                errors.append(("upload", repr(exc)))
            finally:
                connection.close()

        grader = threading.Thread(target=grade)
        grader.start()
        # The grader now holds the claim (RUNNING) and is inside the AI call.
        deadline = timezone.now() + timedelta(seconds=10)
        while timezone.now() < deadline:
            submission.refresh_from_db()
            if submission.grading_state == GradingState.RUNNING:
                break
        self.assertEqual(submission.grading_state, GradingState.RUNNING)

        uploader = threading.Thread(target=upload)
        uploader.start()
        grade_may_finish.set()
        grader.join(timeout=30)
        uploader.join(timeout=30)

        self.assertEqual(errors, [])
        submission.refresh_from_db()
        self.assertEqual(submission.grading_state, GradingState.DONE)
        self.assertIsNotNone(submission.graded_at)
        self.assertEqual(float(submission.score), 8.0)
        # The upload either got in before the grade committed (RUNNING is
        # not closed) or was refused after it; both are correct, and neither
        # may leave the row half-written.
        self.assertIn(upload_outcome, [["accepted"], ["refused"]])
        if upload_outcome == ["accepted"]:
            self.assertEqual(submission.answers[0]["answer_html"], "re-upload")
            self.assertEqual(submission.attempt_count, 2)
        else:
            self.assertEqual(submission.answers[0]["answer_html"], "original")
            self.assertEqual(submission.attempt_count, 1)
        # And now the row is closed for good.
        with patch("students.services.ai_processor") as mock_ai:
            mock_ai.extract_answer_with_retry.return_value = EXTRACTED
            with self.assertRaises(SubmissionAlreadyGradedError):
                upload_answers_engine(self.assignment, "ignored", self.student)


class PostGradingLockLiveHTTPTest(LiveServerTestCase):
    """Real HTTP against a real server thread, real JWT auth, real
    multipart uploads, many at once - the closest thing to a student
    hammering the button (or a script replaying the request) that the
    isolated test database allows."""

    WORKERS = 12

    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = _classroom("live")
        self.submission = _submission(self.assignment, self.student, graded=True)
        self.url = self.live_server_url + reverse(
            "student-submission-upload-answers",
            kwargs={"assignment_id": self.assignment.pk},
        )
        self.token = str(RefreshToken.for_user(self.student).access_token)

    def _post(self, name="answers.pdf", content=PDF_BYTES, token=None):
        headers = {}
        if token is not False:
            headers["Authorization"] = f"Bearer {token or self.token}"
        response = requests.post(
            self.url,
            files={"answer": (name, content, "application/pdf")},
            headers=headers,
            timeout=30,
        )
        return response.status_code, response.json()

    def test_concurrent_real_http_uploads_are_all_refused(self):
        before = _snapshot(self.submission)
        with patch(
            "students.views.AssignmentProcessingService.prepare_ai_content"
        ) as mock_prepare:
            with ThreadPoolExecutor(max_workers=self.WORKERS) as pool:
                results = list(
                    pool.map(
                        lambda i: self._post(
                            name=f"try-{i}.pdf", content=PDF_BYTES + bytes([i])
                        ),
                        range(self.WORKERS),
                    )
                )
        statuses = sorted(code for code, _ in results)
        self.assertEqual(statuses, [409] * self.WORKERS, results)
        for _, body in results:
            self.assertIn("already been graded", body.get("message", "") + str(body))
        mock_prepare.assert_not_called()
        self.assertEqual(_snapshot(self.submission), before)
        self.assertEqual(StudentSubmission.objects.count(), 1)

    def test_live_unauthenticated_request_is_rejected_before_the_rule(self):
        code, _ = self._post(token=False)
        self.assertEqual(code, 401)
