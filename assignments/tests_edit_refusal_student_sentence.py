"""H-211: a credit refusal of an ANSWER EDIT reads the fixed student sentence
when the one who started the edit is a student.

H-180 gave a student whose UPLOAD is refused for the teacher's wallet the fixed
sentence ("Your answers were not submitted. Your teacher's account can't
process uploads right now. ..."). The answer-EDIT task
(`extract_answer_background_task`, started by the update_async action) keeps
the old generic text for the same refusal, which names "the credit wallet" and
is read by the student on the status route. The task is started by a student
(their own submission) or by a teacher (a submission in their course): only
the student's text changes; a teacher keeps the generic text.

The gate is replaced by the refusal itself (`update_submission_from_raw_text`
raises InsufficientCreditsError with the gate's own text, which carries the
balance and the estimate, so what is stored can be told from it).
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.test import TestCase
from rest_framework.test import APIClient

from assignments.tasks import extract_answer_background_task
from billing.errors import INSUFFICIENT_CREDITS_MESSAGE, InsufficientCreditsError
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    StudentSubmission,
)
from students.tests_upload_credit_door import (
    GATE_TEXT,
    POOR,
    STUDENT_SENTENCE,
    _classroom,
)


class EditRefusalTest(TestCase):
    def setUp(self):
        self.teacher, self.student, _, self.assignment = _classroom("h211", POOR)
        self.submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers=[{"question_number": 1, "answer_html": "An answer."}],
        )

    def _refused_edit(self, requested_by, **patches):
        tracked = BackgroundProcessingTask.objects.create(
            requested_by=requested_by,
            task_type=BackgroundTaskType.ANSWER_EXTRACTION,
            assignment=self.assignment,
            submission=self.submission,
            celery_task_id=str(uuid4()),
        )
        with patch(
            "assignments.tasks.update_submission_from_raw_text",
            side_effect=InsufficientCreditsError(GATE_TEXT),
        ):
            result = extract_answer_background_task.apply(
                args=(str(self.submission.id), "edited text", str(requested_by.id)),
                kwargs={"processing_task_id": str(tracked.id)},
            )
        tracked.refresh_from_db()
        return tracked, result

    def _status(self, user, tracked):
        client = APIClient()
        client.force_authenticate(user=user)
        return client.get(f"/api/v1/tasks/status/{tracked.celery_task_id}")

    def test_a_student_who_started_the_edit_reads_the_fixed_sentence(self):
        tracked, result = self._refused_edit(self.student)

        self.assertEqual(tracked.status, BackgroundTaskStatus.FAILURE)
        self.assertTrue(tracked.error)
        self.assertEqual(tracked.error, STUDENT_SENTENCE)
        self.assertEqual(result.result["message"], STUDENT_SENTENCE)
        response = self._status(self.student, tracked)
        self.assertEqual(response.status_code, 200)
        text = response.data["meta"]
        self.assertTrue(text)
        self.assertIn(STUDENT_SENTENCE, text)
        for fragment in ("5000", "25000", "wallet", "refill"):
            self.assertNotIn(fragment, text.lower())

    def test_a_teacher_who_started_the_edit_keeps_the_generic_text(self):
        tracked, result = self._refused_edit(self.teacher)

        self.assertEqual(tracked.status, BackgroundTaskStatus.FAILURE)
        self.assertTrue(tracked.error)
        self.assertEqual(tracked.error, INSUFFICIENT_CREDITS_MESSAGE)
        self.assertEqual(result.result["message"], INSUFFICIENT_CREDITS_MESSAGE)
        self.assertNotIn(STUDENT_SENTENCE, tracked.error)

    def test_a_refusal_before_the_user_is_loaded_does_not_break_the_handler(self):
        # `user` is read from the database inside the task's try; a refusal
        # raised before it exists must still be recorded, with the generic
        # text (no user to say it to).
        tracked = BackgroundProcessingTask.objects.create(
            requested_by=self.student,
            task_type=BackgroundTaskType.ANSWER_EXTRACTION,
            assignment=self.assignment,
            submission=self.submission,
            celery_task_id=str(uuid4()),
        )
        users = MagicMock()
        users.objects.get.side_effect = InsufficientCreditsError(GATE_TEXT)
        with patch("assignments.tasks.CustomUser", users):
            result = extract_answer_background_task.apply(
                args=(str(self.submission.id), "edited text", str(self.student.id)),
                kwargs={"processing_task_id": str(tracked.id)},
            )
        tracked.refresh_from_db()

        self.assertFalse(result.failed(), repr(result.result))
        self.assertEqual(tracked.status, BackgroundTaskStatus.FAILURE)
        self.assertTrue(tracked.error)
        self.assertEqual(tracked.error, INSUFFICIENT_CREDITS_MESSAGE)
