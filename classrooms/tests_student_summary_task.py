"""
student_summary_async has two callers with different contracts.

  * classrooms.views.CourseViewSet.student_summary dispatches it through
    launch_processing_task and passes a processing_task_id, because the
    teacher polls and can cancel it.
  * students.services dispatches it fire-and-forget after a grade commits,
    to refresh a now-stale cached summary. Nobody polls that one, so it
    passes nothing.

Both have to keep working. These tests pin the tracked path's bookkeeping
AND the untracked path's "behaves exactly as it did before tracking
existed", so neither can regress into the other.
"""

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from classrooms.models import Course, StudentCourse
from classrooms.tasks import student_summary_async
from students.exceptions import TaskCancelledError
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
)
from users.models import CustomUser, UserTypes

SUMMARY_TEXT = "Sam is doing well but should revise quadratic equations."


class StudentSummaryTaskTests(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="task-teacher@gmail.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Task",
            last_name="Teacher",
        )
        self.student = CustomUser.objects.create_user(
            email="task-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Sam",
            last_name="Student",
        )
        self.course = Course.objects.create(
            name="Task Summary 101",
            teacher=self.teacher,
            description="Course for student summary task tests.",
        )
        self.enrollment = StudentCourse.objects.create(
            course=self.course, student=self.student
        )

    def _tracking_row(self, **overrides):
        defaults = {
            "requested_by": self.teacher,
            "task_type": BackgroundTaskType.STUDENT_SUMMARY,
            "status": BackgroundTaskStatus.PENDING,
            "meta": {
                "student_id": str(self.student.id),
                "course_id": str(self.course.id),
            },
        }
        defaults.update(overrides)
        return BackgroundProcessingTask.objects.create(**defaults)

    def _run(self, processing_task_id=None):
        return student_summary_async(
            str(self.student.id),
            str(self.teacher.id),
            str(self.course.id),
            processing_task_id=processing_task_id,
        )

    # ---------- untracked: the fire-and-forget caller ----------

    @patch("classrooms.tasks.ai_processor")
    def test_runs_and_stores_the_summary_without_a_tracking_row(self, mock_ai):
        mock_ai.generate_student_summary.return_value = SUMMARY_TEXT

        result = self._run()

        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.ai_summary, SUMMARY_TEXT)
        self.assertIsNotNone(self.enrollment.ai_summary_generated_at)
        self.assertEqual(result["ai_summary"], SUMMARY_TEXT)
        # No tracking row was asked for, so none may be invented. Scoped to
        # this task type rather than asserting the table is globally empty:
        # a TransactionTestCase elsewhere in the run commits its rows, so a
        # bare .exists() would make this fail for reasons unrelated to it.
        self.assertFalse(
            BackgroundProcessingTask.objects.filter(
                task_type=BackgroundTaskType.STUDENT_SUMMARY
            ).exists()
        )

    @patch("classrooms.tasks.ai_processor")
    def test_untracked_failure_still_propagates(self, mock_ai):
        mock_ai.generate_student_summary.side_effect = RuntimeError("model down")

        with self.assertRaises(RuntimeError):
            self._run()

        self.enrollment.refresh_from_db()
        self.assertFalse(self.enrollment.ai_summary)

    # ---------- tracked: the polled caller ----------

    @patch("classrooms.tasks.ai_processor")
    def test_tracked_run_marks_success_and_stores_the_summary(self, mock_ai):
        mock_ai.generate_student_summary.return_value = SUMMARY_TEXT
        task = self._tracking_row()

        self._run(processing_task_id=str(task.id))

        task.refresh_from_db()
        self.assertEqual(task.status, BackgroundTaskStatus.SUCCESS)
        self.assertIsNotNone(task.started_at)
        self.assertIsNotNone(task.finished_at)
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.ai_summary, SUMMARY_TEXT)

    @patch("classrooms.tasks.ai_processor")
    def test_success_meta_carries_the_summary_the_frontend_used_to_read(self, mock_ai):
        """
        Before tracking, the frontend polled this task through the
        AsyncResult fallback and read the summary out of `meta`, which was
        str(task.info). Tracked tasks report meta from the tracking row, so
        the same keys have to be there or that poll silently loses its
        payload.
        """
        mock_ai.generate_student_summary.return_value = SUMMARY_TEXT
        task = self._tracking_row()

        self._run(processing_task_id=str(task.id))

        task.refresh_from_db()
        self.assertEqual(task.meta["ai_summary"], SUMMARY_TEXT)
        self.assertEqual(task.meta["student_id"], str(self.student.id))
        self.assertEqual(task.meta["course"], self.course.name)
        self.assertEqual(task.meta["student_name"], self.student.get_full_name())
        # JSONField can't hold a datetime - it must have been serialised.
        self.assertIsInstance(task.meta["generated_at"], str)

    @patch("classrooms.tasks.ai_processor")
    def test_tracked_failure_records_a_readable_error(self, mock_ai):
        mock_ai.generate_student_summary.side_effect = RuntimeError("model down")
        task = self._tracking_row()

        with self.assertRaises(RuntimeError):
            self._run(processing_task_id=str(task.id))

        task.refresh_from_db()
        self.assertEqual(task.status, BackgroundTaskStatus.FAILURE)
        self.assertTrue(task.error)
        # The raw exception text is not what a teacher should be shown.
        self.assertNotIn("model down", task.error)

    @patch("classrooms.tasks.ai_processor")
    def test_a_cancelled_task_does_not_call_the_model_at_all(self, mock_ai):
        task = self._tracking_row(status=BackgroundTaskStatus.CANCELLED)

        with self.assertRaises(TaskCancelledError):
            self._run(processing_task_id=str(task.id))

        mock_ai.generate_student_summary.assert_not_called()
        self.enrollment.refresh_from_db()
        self.assertFalse(self.enrollment.ai_summary)

    # ---------- the billing guard ----------

    @patch("classrooms.tasks.ai_processor")
    def test_a_missing_enrollment_fails_before_the_billed_model_call(self, mock_ai):
        """
        generate_student_summary charges the teacher's wallet. Reaching it
        with nowhere to store the result meant paying for a summary and then
        dying on `None.ai_summary`.
        """
        self.enrollment.delete()
        task = self._tracking_row()

        with self.assertRaises(ValueError):
            self._run(processing_task_id=str(task.id))

        mock_ai.generate_student_summary.assert_not_called()
        task.refresh_from_db()
        self.assertEqual(task.status, BackgroundTaskStatus.FAILURE)

    @patch("classrooms.tasks.ai_processor")
    def test_summary_is_written_with_a_generated_at_timestamp(self, mock_ai):
        mock_ai.generate_student_summary.return_value = SUMMARY_TEXT
        before = timezone.now()

        self._run()

        self.enrollment.refresh_from_db()
        self.assertGreaterEqual(self.enrollment.ai_summary_generated_at, before)
