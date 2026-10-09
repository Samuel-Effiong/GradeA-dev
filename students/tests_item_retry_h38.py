"""
FR-A-07 S7b, H-38: a teacher removed from a school can't retry that
school's batch items, and a grading run re-checks access when it starts.

The batch session stays the removed teacher's own, so ownership alone let a
retry through (v2's H1 on 923b2b8): 202, and a billed grading run on a
School A student's submission. Built on billing's H-38 fixture: the teacher
joins School A through the real licence route, teaches a course in the
school's session, and is removed through the real remove_teachers route.
The wallet is funded AFTER the removal, so the credit gate (402) can't
answer in place of the access check.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import patch

from django.http import Http404
from django.urls import reverse
from django.utils import timezone

import assignments.tasks as assignment_tasks
from assignments.models import Assignment, AssignmentStatus
from audit.enums import AuditAction, ErrorClass
from audit.models import AuditEvent
from billing.tests.test_h38_part2_removed_teacher_routes import (
    TeacherRemovalBase,
    fund_wallet,
    jwt_client,
)
from students import item_retry, task_tracking
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
    StudentSubmission,
)

NO_ACCESS = "This course wasn't found."


class H38RetryFixture(TeacherRemovalBase):
    def setUp(self):
        super().setUp()
        self.assignment = Assignment.objects.create(
            title="School A homework",
            course_id=self.course_id,
            status=AssignmentStatus.PUBLISHED,
            questions=[
                {
                    "question_number": 1,
                    "question_text": "Q1?",
                    "points": 10,
                    "model_answer": "4",
                }
            ],
        )
        self.session = BatchUploadSession.objects.create(
            teacher=self.teacher,
            course_id=self.course_id,
            task_type=BatchUploadType.GRADE,
            total_files=2,
        )
        self.submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers=[{"question_number": 1, "answer_html": "<p>4</p>"}],
        )
        self.grade_item = self.item(
            1,
            BackgroundTaskType.BATCH_SUBMISSION_GRADING,
            "PROVIDER_FAILURE",
            submission=self.submission,
        )
        self.upload_item = self.item(
            2, BackgroundTaskType.BATCH_ANSWER_UPLOAD, "FILE_UNREADABLE"
        )
        self.launched = []

        def launch(task_callable, processing_task, *args, **kwargs):
            self.launched.append(processing_task.id)
            return SimpleNamespace(id=str(uuid.uuid4()))

        target = patch("students.item_retry.launch_processing_task", side_effect=launch)
        target.start()
        self.addCleanup(target.stop)

    def item(self, index, task_type, code, submission=None):
        return BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=task_type,
            batch_session=self.session,
            assignment=self.assignment,
            submission=submission,
            file_name=f"item {index}",
            item_index=index,
            status=BackgroundTaskStatus.FAILURE,
            reason_code=code,
            error="This item failed.",
        )

    def removed_and_funded(self):
        self.remove_teacher()
        fund_wallet(self.teacher)

    def retry(self, item):
        return jwt_client(self.teacher.email).post(
            reverse(
                "task-retry-item",
                kwargs={"session_id": str(self.session.id), "item_id": str(item.id)},
            )
        )

    def retry_failed(self, body=None):
        return jwt_client(self.teacher.email).post(
            reverse("task-retry-failed", kwargs={"session_id": str(self.session.id)}),
            body or {},
            format="json",
        )

    def requested_count(self):
        return AuditEvent.objects.filter(action=AuditAction.GRADING_REQUESTED).count()

    def assert_untouched(self, item, code):
        item.refresh_from_db()
        self.assertEqual(
            (item.status, item.reason_code, item.retry_count),
            (BackgroundTaskStatus.FAILURE, code, 0),
        )


class ARemovedTeacherRetrying(H38RetryFixture):
    def test_before_the_removal_the_same_retry_is_accepted(self):
        fund_wallet(self.teacher)
        response = self.retry(self.grade_item)
        self.assertEqual(response.status_code, 202, response.content[:400])
        self.assertEqual(self.launched, [self.grade_item.id])

    def test_after_the_removal_the_item_is_not_found_and_nothing_runs(self):
        self.removed_and_funded()
        requested = self.requested_count()

        response = self.retry(self.grade_item)

        self.assertEqual(response.status_code, 404, response.content[:400])
        self.assertEqual(self.launched, [])
        self.assert_untouched(self.grade_item, "PROVIDER_FAILURE")
        self.assertEqual(self.requested_count(), requested)

    def test_the_service_refuses_the_item_by_its_own_course(self):
        """Behind the route's session gate (F6.2's rule, bundle 4
        merge-down), item_retry still checks the item itself, for any
        caller."""
        self.removed_and_funded()

        with self.assertRaises(Http404):
            item_retry.retry_item(self.grade_item, self.teacher)

        self.assertEqual(self.launched, [])
        self.assert_untouched(self.grade_item, "PROVIDER_FAILURE")

    def test_an_upload_item_is_not_found_either_not_told_to_re_upload(self):
        self.removed_and_funded()
        response = self.retry(self.upload_item)
        self.assertEqual(response.status_code, 404, response.content[:400])
        self.assertNotIn(b"replace_file", response.content)
        self.assert_untouched(self.upload_item, "FILE_UNREADABLE")

    def test_an_item_with_no_assignment_is_judged_by_its_batchs_course(self):
        """An assignment upload that failed before creating its assignment
        has none: the batch session's course decides."""
        orphan = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
            batch_session=self.session,
            file_name="item 3",
            item_index=3,
            status=BackgroundTaskStatus.FAILURE,
            reason_code="FILE_UNREADABLE",
            error="This item failed.",
        )
        fund_wallet(self.teacher)
        response = self.retry(orphan)
        self.assertEqual(response.status_code, 409, response.content[:400])
        self.assertIn(b"replace_file", response.content)

        self.removed_and_funded()
        self.assertEqual(self.retry(orphan).status_code, 404)
        # The route now stops at the session gate, so the item's own fallback
        # (no assignment: the batch session's course) is pinned at the
        # service, where it still decides.
        with self.assertRaises(Http404):
            item_retry.retry_item(orphan, self.teacher)

    def test_retry_failed_is_not_found_for_a_removed_teacher(self):
        """F6.2's session-level rule (SM ruling at the bundle 4 merge-down):
        the route answers like a missing session, not a 202 retrying
        nothing."""
        self.removed_and_funded()
        for body in (None, {"reason_codes": ["PROVIDER_FAILURE"]}):
            with self.subTest(body=body):
                response = self.retry_failed(body)
                self.assertEqual(response.status_code, 404, response.content[:400])
        self.assertEqual(self.launched, [])
        self.assert_untouched(self.grade_item, "PROVIDER_FAILURE")

    def test_the_service_skips_every_item_without_saying_why(self):
        """Behind the route's gate, retry_failed still skips each item the
        teacher can't reach, and gives no code: a code would tell them about
        the item."""
        self.removed_and_funded()
        for codes in (None, ["PROVIDER_FAILURE"]):
            with self.subTest(reason_codes=codes):
                retried, skipped = item_retry.retry_failed(
                    self.session, self.teacher, reason_codes=codes
                )
                self.assertEqual(retried, [])
                self.assertEqual(
                    skipped,
                    [
                        {"item_id": str(self.grade_item.id), "reason_code": None},
                        {"item_id": str(self.upload_item.id), "reason_code": None},
                    ],
                )
        self.assertEqual(self.launched, [])
        self.assert_untouched(self.grade_item, "PROVIDER_FAILURE")

    def test_a_removal_between_the_check_and_the_claim_still_refuses(self):
        """The claim re-checks access in its conditional UPDATE: the removal
        lands after the request-time check (patched to have passed)."""
        self.removed_and_funded()
        # Both request-time checks passed (the route's session gate and
        # item_retry's item check); the removal lands before the claim.
        with patch("users.views.teacher_may_reach", new=lambda user, work: True), patch(
            "students.item_retry.is_reachable", new=lambda item, user: True
        ):
            response = self.retry(self.grade_item)
        self.assertEqual(response.status_code, 409, response.content[:400])
        self.assertEqual(self.launched, [])
        self.assert_untouched(self.grade_item, "PROVIDER_FAILURE")


class TheGradingRunChecksAccessWhenItStarts(H38RetryFixture):
    """A run queued (or scheduled, or retried) before the removal, that
    starts after it, must not grade: nothing is sent to the provider."""

    def setUp(self):
        super().setUp()
        self.task = task_tracking.create_processing_task(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            assignment=self.assignment,
            submission=self.submission,
        )

    def run_grading(self):
        def grade(user, submission, processing_task_id=None):
            submission.score = 8
            submission.graded_at = timezone.now()
            submission.save(update_fields=["score", "graded_at"])
            return submission

        with patch("assignments.tasks.grade_engine", side_effect=grade) as grade_engine:
            assignment_tasks.grade_engine_async.apply(
                args=(str(self.teacher.id), str(self.submission.id)),
                kwargs={"processing_task_id": str(self.task.id)},
            )
        self.task.refresh_from_db()
        return grade_engine

    def test_a_run_for_a_removed_teacher_fails_before_grading(self):
        self.removed_and_funded()

        grade_engine = self.run_grading()

        grade_engine.assert_not_called()
        self.assertEqual(self.task.status, BackgroundTaskStatus.FAILURE)
        self.assertEqual(self.task.error, NO_ACCESS)
        self.submission.refresh_from_db()
        self.assertIsNone(self.submission.graded_at)
        failed = AuditEvent.objects.get(
            action=AuditAction.GRADING_FAILED, target_id=self.submission.id
        )
        self.assertEqual(failed.error_class, ErrorClass.USER)

    def test_an_active_teachers_run_still_grades(self):
        fund_wallet(self.teacher)
        grade_engine = self.run_grading()
        grade_engine.assert_called_once()
        self.assertEqual(self.task.status, BackgroundTaskStatus.SUCCESS)
