"""
Regression coverage for H4: grade_engine used to dispatch
formatted_grade_async (and student_summary_async) *before* its own final
submission.save(). formatted_grade_async runs in a separate worker and
writes submission.formatted_grade back to the same row; if it finished
before grade_engine's own (full) save ran, that save — built from a
stale in-memory object that never saw formatted_grade_async's write —
would silently overwrite formatted_grade back to blank. A second,
entirely redundant submission.save() in assignments.tasks.grade_engine_async
doubled the exposure to the same race.

The fix moves both dispatches to run only after the final save has
actually committed, via transaction.on_commit — not just placed
textually afterwards, so the guarantee survives if a future caller ever
wraps grade_engine in its own outer transaction. The redundant second
save in grade_engine_async was removed outright.

Run with:
    python manage.py test students.tests_grading_followup_dispatch

Uses TransactionTestCase (not TestCase): on_commit callbacks registered
inside a plain TestCase's wrapping transaction never fire during the test
(the wrapper rolls back rather than commits at teardown), which would
make these assertions pass for the wrong reason.
"""

from unittest.mock import MagicMock, patch

from django.test import TransactionTestCase
from django.utils import timezone

from assignments.models import Assignment
from classrooms.models import Course, Session
from students.exceptions import TaskCancelledError
from students.models import BackgroundTaskType, GradingState, StudentSubmission
from students.services import grade_engine
from students.task_tracking import cancel_processing_task, create_processing_task
from users.models import CustomUser, UserTypes


def _valid_grading_result(score=8, max_points=10):
    return {
        "grading_summary": {
            "total_score": score,
            "max_total_points": max_points,
            "percentage": round(score / max_points * 100, 2),
        },
        "grading_confidence": 90,
        "question_evaluations": [],
    }


class GradingFollowupDispatchTest(TransactionTestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email=f"followup-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        student = CustomUser.objects.create_user(
            email=f"followup-student-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        session = Session.objects.create(name="Test Session", teacher=self.teacher)
        course = Course.objects.create(
            name="Test Course", teacher=self.teacher, session=session
        )
        assignment = Assignment.objects.create(
            title="Test Assignment",
            course=course,
            questions=[{"question_number": 1, "points": 10}],
        )
        self.submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "An answer."}],
        )

    @patch("assignments.tasks.formatted_grade_async")
    @patch("students.services.student_summary_async")
    @patch("students.services.ai_processor")
    def test_followups_dispatch_only_after_the_grade_is_actually_committed(
        self, mock_ai, mock_summary, mock_formatted
    ):
        mock_ai.extract_grade_with_retry.return_value = _valid_grading_result()

        seen = {}

        def capture_committed_state(*args, **kwargs):
            # A fresh read from the DB, at the moment of dispatch — proves
            # the commit is visible, not just that Python has moved past
            # the `with` block in this same process.
            fresh = StudentSubmission.objects.get(pk=self.submission.pk)
            seen["graded_at_is_set"] = fresh.graded_at is not None
            seen["grading_state"] = fresh.grading_state
            result = MagicMock()
            result.id = "fake-celery-task-id"
            return result

        mock_formatted.delay.side_effect = capture_committed_state

        grade_engine(self.teacher, self.submission)

        mock_formatted.delay.assert_called_once()
        mock_summary.delay.assert_called_once()
        self.assertTrue(
            seen["graded_at_is_set"],
            "formatted_grade_async was dispatched before the grading "
            "submission.save() had committed",
        )
        self.assertEqual(seen["grading_state"], GradingState.DONE)

    @patch("assignments.tasks.formatted_grade_async")
    @patch("students.services.student_summary_async")
    @patch("students.services.ai_processor")
    def test_followups_are_not_dispatched_if_cancelled_before_final_save(
        self, mock_ai, mock_summary, mock_formatted
    ):
        # Under the old code, formatted_grade_async was dispatched right
        # after the AI call succeeded — well before the final save. This
        # test cancels the run in that exact window (AI result available,
        # final save not yet reached) to prove the new code has not
        # dispatched anything by then either: dispatch now only happens
        # from inside the on_commit callback registered *after* a
        # successful final save, which this run never reaches.
        processing_task = create_processing_task(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            submission=self.submission,
        )

        def cancel_then_return(*args, **kwargs):
            cancel_processing_task(processing_task)
            return _valid_grading_result()

        mock_ai.extract_grade_with_retry.side_effect = cancel_then_return

        with self.assertRaises(TaskCancelledError):
            grade_engine(
                self.teacher,
                self.submission,
                processing_task_id=str(processing_task.id),
            )

        mock_formatted.delay.assert_not_called()
        mock_summary.delay.assert_not_called()

    @patch("assignments.tasks.formatted_grade_async")
    @patch("students.services.student_summary_async")
    @patch("students.services.ai_processor")
    def test_followups_are_not_dispatched_when_the_ai_call_itself_fails(
        self, mock_ai, mock_summary, mock_formatted
    ):
        mock_ai.extract_grade_with_retry.side_effect = RuntimeError("model exploded")

        with self.assertRaises(RuntimeError):
            grade_engine(self.teacher, self.submission)

        mock_formatted.delay.assert_not_called()
        mock_summary.delay.assert_not_called()
