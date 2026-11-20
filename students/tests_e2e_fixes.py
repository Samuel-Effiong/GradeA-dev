"""Batch upload fixes from the end-to-end run of 2026-11-19 (SM -11).

The run (real server, worker, AI and storage, at 2efe1f872) found:

* F2: grading a course with no session (legacy rows) saved and charged the
  grade, then crashed: Course.__str__ read session.name for the formatter
  prompt. The item ended FAILURE and a retry would charge again.
* F3: the mid-batch credit message was frozen when the item failed
  ("before any of the 10 items were finished") and stayed after 3 of 10
  succeeded. It is now built when read, from the batch's counts, with the
  same two approved texts.
* F4: the batch 202 answer did not say which files were parked. Each entry
  now carries `status` (ACCEPTED, PARKED or REFUSED) and `reason_code`.
* F5: after the lifetime purge, session-results still showed the old
  failure text. It now shows the retry 409's own message ("deleted after N
  days") and remediation.
* F6: a 0-byte file was sent to storage, failed, and was counted as
  "storage unavailable" (kept_upload_fallback). It is a user error.
* F8: expected user errors (FileUnreadable, FileTypeUnsupported, credit
  refusals) were logged at ERROR, and Celery logged them as "raised
  unexpected". They are WARNING/INFO now; real errors stay ERROR.

F7 (the sessions list's task_type filter) is in users.tests_task_sessions_list.

    python manage.py test students.tests_e2e_fixes
"""

import logging
from unittest.mock import patch

from celery.app.trace import get_log_policy, log_policy_expected, log_policy_unexpected
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from assignments.exceptions import FileTypeUnsupportedError, FileUnreadableError
from assignments.models import Assignment
from assignments.tasks import (
    grade_engine_async,
    upload_answers_engine_async,
    upload_assignment_async,
)
from audit.enums import ReasonCode
from AutoGrader.reason_codes import REASON_CODES
from billing.errors import InsufficientCreditsError
from classrooms.models import Course, Session
from students import kept_files
from students.exceptions import InsufficientCreditsMidBatchError
from students.item_retry import deleted_after_days
from students.kept_uploads import DoorFile
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
    KeptUpload,
    StudentSubmission,
)
from students.task_tracking import create_processing_task, mark_processing_task_failure
from students.tests_parked_files_kept import ANSWERS, ASSIGNMENTS, ParkedFixture
from users.models import CustomUser, UserTypes

MID_BATCH = ReasonCode.INSUFFICIENT_CREDITS_MID_BATCH.value


# -- F2 ---------------------------------------------------------------------


def _grading_result():
    return {
        "grading_summary": {
            "total_score": 8,
            "max_total_points": 10,
            "percentage": 80.0,
        },
        "grading_confidence": 90,
        "question_evaluations": [
            {
                "question_number": 1,
                "max_points": 10,
                "score_awarded": 8,
                "feedback_for_student": "Good.",
            }
        ],
    }


class CourseWithoutSessionNamesItself(SimpleTestCase):
    def test_a_course_without_a_session_is_its_name(self):
        self.assertEqual(str(Course(name="Biology")), "Biology")

    def test_a_course_with_a_session_is_unchanged(self):
        course = Course(name="Biology", session=Session(name="2026 Term 1"))
        self.assertEqual(str(course), "2026 Term 1 - Biology")


class GradingACourseWithoutSession(TransactionTestCase):
    """The grade-all item for a legacy course with session=NULL completes,
    with its formatted grade and summary sent once each, after one grading
    call. TransactionTestCase: the follow-ups wait for the grade's commit."""

    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="f2-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        student = CustomUser.objects.create_user(
            email="f2-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Ada",
            last_name="Lovelace",
        )
        self.course = Course.objects.create(name="Legacy", teacher=self.teacher)
        self.assertIsNone(self.course.session_id)
        assignment = Assignment.objects.create(
            title="A",
            course=self.course,
            total_points=10,
            questions=[
                {"question_number": 1, "points": 10, "model_answer": "An answer."}
            ],
        )
        self.submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "An answer."}],
        )
        session = BatchUploadSession.objects.create(
            teacher=self.teacher, course=self.course, task_type=BatchUploadType.GRADE
        )
        self.item = create_processing_task(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.BATCH_SUBMISSION_GRADING,
            batch_session=session,
            assignment=assignment,
            submission=self.submission,
            item_index=1,
        )

    @patch("students.services._formatted_grade_task")
    @patch("students.services.student_summary_async")
    @patch("students.services.ai_processor")
    def test_grade_all_item_completes_with_its_follow_ups(
        self, mock_ai, mock_summary, mock_formatted
    ):
        mock_ai.extract_grade_with_retry.return_value = _grading_result()
        mock_formatted.return_value.delay.return_value.id = "formatted-task"

        result = grade_engine_async.apply(
            args=(str(self.teacher.id), str(self.submission.id)),
            kwargs={"processing_task_id": str(self.item.id)},
        )

        self.assertTrue(result.successful(), result.traceback)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, BackgroundTaskStatus.SUCCESS)
        self.assertEqual(self.item.reason_code, "")
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.score, 8)
        self.assertEqual(mock_ai.extract_grade_with_retry.call_count, 1)
        mock_formatted.return_value.delay.assert_called_once()
        prompt = next(
            value
            for value in mock_formatted.return_value.delay.call_args.args
            if isinstance(value, str) and "Grading Result:" in value
        )
        self.assertIn("Course: Legacy\n", prompt)
        self.assertEqual(mock_summary.delay.call_count, 1)


# -- F3 ---------------------------------------------------------------------


class MidBatchMessageIsBuiltWhenRead(ParkedFixture, TestCase):
    def setUp(self):
        self.build(10_000, tag="f3")
        self.session = BatchUploadSession.objects.create(
            teacher=self.teacher,
            assignment=self.assignment,
            task_type=BatchUploadType.SUBMISSION,
            total_files=10,
        )

    def row(self, index, status, **fields):
        return BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=ANSWERS,
            batch_session=self.session,
            assignment=self.assignment,
            status=status,
            item_index=index,
            file_name=f"f{index}.png",
            **fields,
        )

    def stopped(self, index):
        # As the worker stores it at the moment of failure: nothing finished.
        frozen = str(
            InsufficientCreditsMidBatchError(params={"completed": 0, "total": 10})
        )
        return self.row(
            index, BackgroundTaskStatus.FAILURE, reason_code=MID_BATCH, error=frozen
        )

    def entry(self, item):
        data = self.session_results(self.session.id)
        return next(e for e in data["failure_list"] if e["item_id"] == str(item.id))

    def test_the_count_is_the_batch_s_current_one(self):
        item = self.stopped(4)
        for index in (1, 2, 3):
            self.row(index, BackgroundTaskStatus.SUCCESS)

        entry = self.entry(item)

        expected = "Credits ran out after 3 of 10 items. The finished items are saved."
        self.assertEqual(entry["message"], expected)
        self.assertEqual(entry["error"], expected)

    def test_with_nothing_finished_it_is_the_approved_none_finished_text(self):
        item = self.stopped(1)
        self.assertEqual(
            self.entry(item)["message"],
            "Credits ran out before any of the 10 items were finished.",
        )

    def test_both_texts_are_the_catalogue_s(self):
        spec = REASON_CODES[ReasonCode.INSUFFICIENT_CREDITS_MID_BATCH]
        self.assertEqual(
            spec.message,
            "Credits ran out after {completed} of {total} items. The finished "
            "items are saved.",
        )
        self.assertEqual(
            spec.alternative_messages,
            {
                "none_finished": "Credits ran out before any of the {total} items were finished."
            },
        )


# -- F4 ---------------------------------------------------------------------


class BatchAnswerSaysWhatHappenedToEachFile(ParkedFixture, TestCase):
    def setUp(self):
        self.build(10_000, tag="f4")

    def post(self, files, estimates, route=ANSWERS):
        if route == ANSWERS:
            url = reverse(
                "student-submission-batch-upload",
                kwargs={"assignment_id": self.assignment.pk},
            )
            data = {"answers": files}
        else:
            url = reverse("assignment-upload-async")
            data = {"course": str(self.course.id), "assignments": files}
        with patch(
            "ai_processor.services.AIProcessor.estimate_messages_cost",
            side_effect=list(estimates),
        ), override_settings(
            MAX_UPLOAD_FILE_SIZE_BYTES=2048
        ), self.captureOnCommitCallbacks(
            execute=True
        ):
            response = self.api.post(url, data, format="multipart")
        self.assertEqual(response.status_code, 202, response.content[:400])
        return response.json()["data"]["tasks"]

    def big(self):
        return SimpleUploadedFile(
            "big.pdf", b"%PDF-1.4" + b"0" * 4096, "application/pdf"
        )

    def check(self, route):
        files = self.files(2)
        tasks = self.post(
            [files[0], files[1], self.big()], [1_000, 20_000], route=route
        )
        self.assertEqual(
            [(t["file_name"], t["status"], t["reason_code"]) for t in tasks],
            [
                ("s0.png", "ACCEPTED", None),
                ("s1.png", "PARKED", "INSUFFICIENT_CREDITS"),
                ("big.pdf", "REFUSED", "FILE_TOO_LARGE"),
            ],
        )
        self.assertIsNotNone(tasks[0]["task_id"])
        self.assertIsNone(tasks[1]["task_id"])

    def test_answer_batch(self):
        self.check(ANSWERS)

    def test_assignment_batch(self):
        self.check(ASSIGNMENTS)

    @override_settings(BILLING_HOLDS_ENABLED=True)
    def test_a_file_whose_door_hold_lost_the_race_is_parked(self):
        with patch(
            "assignments.upload_door.credit_holds.hold_door_item", return_value=False
        ):
            [entry] = self.post(self.files(1), [1_000])
        self.assertEqual(
            (entry["status"], entry["reason_code"]), ("PARKED", "INSUFFICIENT_CREDITS")
        )


# -- F5 ---------------------------------------------------------------------


class LifetimePurgedItemReadsLikeTheRetry(ParkedFixture, TestCase):
    def setUp(self):
        self.build(10_000, tag="f5")

    def test_session_results_shows_the_retry_409_s_text(self):
        data = self.upload(1_000, 20_000)
        parked = self.items(data)[1]
        KeptUpload.objects.filter(processing_task=parked).update(
            purged_at=timezone.now(), purge_reason=KeptUpload.PurgeReason.LIFETIME
        )
        refusal = self.retry(parked)
        self.assertEqual(refusal.status_code, 409)
        body = refusal.json()["error"]["field_errors"]

        results = self.session_results(data["session_id"])
        entry = next(
            e for e in results["failure_list"] if e["item_id"] == str(parked.id)
        )

        self.assertEqual(entry["message"], deleted_after_days())
        self.assertEqual(entry["message"], body["error"])
        self.assertEqual(entry["remediation"], body["remediation"])
        self.assertIs(entry["retryable"], False)

    def test_a_still_kept_parked_item_is_unchanged(self):
        data = self.upload(1_000, 20_000)
        parked = self.items(data)[1]
        entry = next(
            e
            for e in self.session_results(data["session_id"])["failure_list"]
            if e["item_id"] == str(parked.id)
        )
        self.assertNotEqual(entry["message"], deleted_after_days())
        self.assertIs(entry["retryable"], True)


# -- F6 ---------------------------------------------------------------------


class EmptyFileIsNotAStorageFault(SimpleTestCase):
    def test_a_0_byte_file_is_not_stored_counted_or_warned_about(self):
        empty = SimpleUploadedFile("empty.pdf", b"", content_type="application/pdf")
        with patch("students.kept_files.store") as store, patch(
            "students.kept_uploads.audit_metrics.count"
        ) as count, self.assertNoLogs("students.kept_uploads", level=logging.WARNING):
            door_file = DoorFile(empty)
        store.assert_not_called()
        count.assert_not_called()
        self.assertIsNone(door_file.stored)
        self.assertEqual(door_file.payload()["content_b64"], "")

    def test_storage_down_for_a_real_file_still_counts(self):
        real = SimpleUploadedFile(
            "a.pdf", b"%PDF-1.4 x", content_type="application/pdf"
        )
        with patch(
            "students.kept_files.store",
            side_effect=kept_files.KeptFileUnavailable("down"),
        ), patch("students.kept_uploads.audit_metrics.count") as count, self.assertLogs(
            "students.kept_uploads", level=logging.WARNING
        ):
            DoorFile(real)
        count.assert_called_once_with("kept_upload_fallback")


# -- F8 ---------------------------------------------------------------------


class ExpectedUserErrorsAreNotLoggedAsFaults(TestCase):
    def setUp(self):
        teacher = CustomUser.objects.create_user(
            email="f8-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.item = create_processing_task(
            requested_by=teacher, task_type=BackgroundTaskType.BATCH_ANSWER_UPLOAD
        )

    def levels(self, error):
        with self.assertLogs("students.task_tracking", level=logging.DEBUG) as logs:
            mark_processing_task_failure(self.item.id, error)
        return {record.levelno for record in logs.records}

    def test_user_errors_are_warnings(self):
        for error in (
            FileUnreadableError(params={"file_name": "a.pdf"}),
            FileTypeUnsupportedError(
                params={"file_name": "a.txt", "detected_type": "text/plain"}
            ),
        ):
            with self.subTest(error=type(error).__name__):
                levels = self.levels(error)
                self.assertIn(logging.WARNING, levels)
                self.assertNotIn(logging.ERROR, levels)

    def test_a_real_fault_stays_an_error(self):
        self.assertIn(logging.ERROR, self.levels(RuntimeError("boom")))

    def test_the_marker_class_is_the_catalogue_s_user_class(self):
        from AutoGrader import reason_codes

        ExpectedUserError = reason_codes.ExpectedUserError

        for error in (
            FileUnreadableError(params={"file_name": "a.pdf"}),
            FileTypeUnsupportedError(
                params={"file_name": "a.txt", "detected_type": "text/plain"}
            ),
            InsufficientCreditsError("short"),
            InsufficientCreditsMidBatchError(params={"completed": 1, "total": 2}),
        ):
            self.assertIsInstance(error, ExpectedUserError)
        for fault in (RuntimeError("x"), AttributeError("x"), ValueError("x")):
            self.assertNotIsInstance(fault, ExpectedUserError)

    def test_celery_logs_them_as_expected_and_real_faults_as_unexpected(self):
        einfo = type("EInfo", (), {"internal": False})()
        for task in (
            upload_answers_engine_async,
            upload_assignment_async,
            grade_engine_async,
        ):
            with self.subTest(task=task.name):
                self.assertIs(
                    get_log_policy(
                        task, einfo, FileUnreadableError(params={"file_name": "a.pdf"})
                    ),
                    log_policy_expected,
                )
                self.assertIs(
                    get_log_policy(task, einfo, InsufficientCreditsError("short")),
                    log_policy_expected,
                )
                self.assertIs(
                    get_log_policy(task, einfo, AttributeError("x")),
                    log_policy_unexpected,
                )
