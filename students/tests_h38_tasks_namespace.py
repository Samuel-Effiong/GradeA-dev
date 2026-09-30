"""H-38 for the tasks/ namespace and the grading dispatches (v2's finding,
FINDING_h38_tasks_namespace_v2.md; T1-T5 below).

The teacher joins School A through the real licence route, creates a course
in the school session and enrols a student (billing's TeacherRemovalBase).
Before removal they own a grading batch session and a STARTED grading task
for that student's submission, as their own work would leave. After
remove_teachers, the removed teacher must not read, cancel or grade any of
it - and the auto-grade beat must not grade the school's students in their
name.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone

from assignments.models import Assignment, AssignmentStatus
from billing.models import CreditLedger
from billing.tests.test_h38_part2_removed_teacher_routes import (
    TeacherRemovalBase,
    jwt_client,
)
from classrooms.models import Course
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
    StudentSubmission,
)

SENTINEL = "Vfsentinelpupil"


class TasksAfterRemovalTests(TeacherRemovalBase):
    def setUp(self):
        super().setUp()
        type(self.student).objects.filter(pk=self.student.pk).update(
            first_name=SENTINEL, last_name="Hthirtyeight"
        )
        self.student.refresh_from_db()
        course = Course.objects.get(pk=self.course_id)
        self.assignment = Assignment.objects.create(
            title="H38 tasks",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
            due_date=timezone.now() - timedelta(hours=1),
            auto_grade_on_due_date=True,
        )
        self.submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers=[{"question_number": 1, "answer_html": "<p>x</p>"}],
        )
        self.session = BatchUploadSession.objects.create(
            teacher=self.teacher,
            course=course,
            assignment=self.assignment,
            task_type=BatchUploadType.GRADE,
            total_files=1,
        )
        self.task = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.BATCH_SUBMISSION_GRADING,
            batch_session=self.session,
            assignment=self.assignment,
            submission=self.submission,
            file_name=f"{SENTINEL} Hthirtyeight",
            status=BackgroundTaskStatus.STARTED,
            celery_task_id=str(uuid.uuid4()),
            meta={"student_name": f"{SENTINEL} Hthirtyeight"},
        )

    def routes(self, client):
        """(label, response) for every tasks/ route on this work."""
        with patch("celery.app.control.Control.revoke"):
            return [
                (
                    "T1 session-results",
                    client.get(
                        reverse(
                            "task-session-results",
                            kwargs={"session_id": str(self.session.id)},
                        )
                    ),
                ),
                (
                    "T2 status",
                    client.get(f"/api/v1/tasks/status/{self.task.celery_task_id}"),
                ),
                (
                    "T3 cancel",
                    client.post(f"/api/v1/tasks/cancel/{self.task.celery_task_id}"),
                ),
                (
                    "T4 cancel-session",
                    client.post(f"/api/v1/tasks/cancel-session/{self.session.id}"),
                ),
            ]

    def test_the_owner_still_reaches_their_tasks_before_removal(self):
        """Control: the fix blocks nothing a current member may do."""
        client = jwt_client(self.teacher.email)
        for label, response in self.routes(client)[:2]:
            with self.subTest(route=label):
                self.assertEqual(response.status_code, 200, response.content[:200])

    def test_t1_to_t4_answer_404_after_removal_and_change_nothing(self):
        self.remove_teacher()
        client = jwt_client(self.teacher.email)

        for label, response in self.routes(client):
            with self.subTest(route=label):
                self.assertEqual(response.status_code, 404, response.content[:200])
                self.assertNotIn(SENTINEL, response.content.decode())
                self.assertNotIn(str(self.submission.id), response.content.decode())
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, BackgroundTaskStatus.STARTED)

    def test_an_unreachable_task_answers_exactly_like_a_missing_one(self):
        self.remove_teacher()
        client = jwt_client(self.teacher.email)
        unreachable = client.get(f"/api/v1/tasks/status/{self.task.celery_task_id}")
        missing = client.get(f"/api/v1/tasks/status/{uuid.uuid4()}")
        self.assertEqual(unreachable.status_code, missing.status_code)
        self.assertEqual(unreachable.json()["message"], missing.json()["message"])

    def test_t5_the_auto_grade_beat_grades_nothing_after_removal(self):
        from assignments.tasks import auto_grade_due_assignment

        self.remove_teacher()
        with patch("assignments.tasks.grade_engine_async.delay") as delay:
            outcome = auto_grade_due_assignment(str(self.assignment.id))

        delay.assert_not_called()
        self.assertIn("skipped", outcome)
        self.assertFalse(
            BatchUploadSession.objects.exclude(pk=self.session.pk).exists()
        )

    def test_the_auto_grade_beat_still_grades_before_removal(self):
        """Control: a member's due assignment is still auto-graded."""
        from assignments.tasks import auto_grade_due_assignment

        with patch("assignments.tasks.grade_engine_async.delay") as delay:
            auto_grade_due_assignment(str(self.assignment.id))
        self.assertEqual(delay.call_count, 1)

    def test_queued_grading_is_refused_and_never_charged_after_removal(self):
        """The chokepoint: work queued before the removal (a grade-all, a
        scheduled grade, the beat) reaches grade_engine_async, which refuses
        it before grading - so nothing is graded and nothing is charged."""
        from assignments.tasks import COURSE_NOT_REACHABLE, grade_engine_async

        self.remove_teacher()
        ledger_before = CreditLedger.objects.count()
        with patch("assignments.tasks.grade_engine") as grade_engine:
            result: dict = grade_engine_async.apply(
                args=(str(self.teacher.id), str(self.submission.id)),
                kwargs={"processing_task_id": str(self.task.id)},
            ).result  # type: ignore[assignment]

        grade_engine.assert_not_called()
        self.assertEqual(result["message"], COURSE_NOT_REACHABLE)
        self.assertEqual(CreditLedger.objects.count(), ledger_before)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, BackgroundTaskStatus.FAILURE)
        self.submission.refresh_from_db()
        self.assertIsNone(self.submission.graded_at)

    def test_a_batch_grading_run_is_refused_after_removal(self):
        from assignments.tasks import COURSE_NOT_REACHABLE, grade_batch_async

        self.remove_teacher()
        with patch("assignments.tasks.grade_engine_async.delay") as delay:
            result = grade_batch_async.apply(
                args=(str(self.teacher.id), str(self.assignment.id))
            ).result
        delay.assert_not_called()
        self.assertEqual(result, COURSE_NOT_REACHABLE)
