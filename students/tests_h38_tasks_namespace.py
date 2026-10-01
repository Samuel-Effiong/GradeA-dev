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

import logging
import traceback
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
    make_user,
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
from users.models import UserTypes

SENTINEL = "Vfsentinelpupil"


class AllLogs:
    """Every record from every logger, at every level (1a's Q6 harness)."""

    def __enter__(self):
        self.lines: list = []
        original = logging.Logger.handle
        lines = self.lines

        def handle(logger_self, record):
            text = record.getMessage()
            if record.exc_info:
                text += "".join(traceback.format_exception(*record.exc_info))
            lines.append(f"{record.name}:{text}")
            return original(logger_self, record)

        self.patches = [
            patch.object(logging.Logger, "handle", handle),
            patch.object(logging.Logger, "isEnabledFor", lambda s, lvl: True),
        ]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


def body(response):
    """The JSON body, without the per-request fields."""
    data = response.json()
    for key in ("request_id", "trace_id", "timestamp"):
        data.pop(key, None)
    return data


class TasksFixture(TeacherRemovalBase):
    """The teacher's school work; no tests of its own."""

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
            # Epic A: a one-level marking guide, so S6d's rubric gate lets
            # the auto-grade beat dispatch (merge-down of bundle 4).
            questions=[
                {
                    "question_number": 1,
                    "question_text": "Q1?",
                    "points": 10,
                    "rubric": [
                        {"level": "Full", "description": "Complete", "points": 10}
                    ],
                }
            ],
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

    def routes(self, client, missing=False):
        """(label, response) for every tasks/ route on this work, or on ids
        that don't exist."""
        sid = str(uuid.uuid4()) if missing else str(self.session.id)
        cid = str(uuid.uuid4()) if missing else self.task.celery_task_id
        with patch("celery.app.control.Control.revoke"):
            return [
                (
                    "T1 session-results",
                    client.get(
                        reverse("task-session-results", kwargs={"session_id": sid})
                    ),
                ),
                ("T2 status", client.get(f"/api/v1/tasks/status/{cid}")),
                ("T3 cancel", client.post(f"/api/v1/tasks/cancel/{cid}")),
                (
                    "T4 cancel-session",
                    client.post(f"/api/v1/tasks/cancel-session/{sid}"),
                ),
            ]

    def grade(self):
        """Run the queued grading as the teacher; returns the mocked
        grade_engine."""
        from assignments.tasks import grade_engine_async

        with patch("assignments.tasks.grade_engine") as grade_engine:
            grade_engine.side_effect = lambda user, submission, **k: submission
            grade_engine_async.apply(
                args=(str(self.teacher.id), str(self.submission.id)),
                kwargs={"processing_task_id": str(self.task.id)},
            )
        return grade_engine


class TasksAfterRemovalTests(TasksFixture):
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

    def test_every_route_answers_an_unreachable_id_like_a_missing_one(self):
        """1a's Q1: byte-identical bodies, on all four routes."""
        self.remove_teacher()
        client = jwt_client(self.teacher.email)
        unreachable = self.routes(client)
        missing = self.routes(client, missing=True)
        for (label, got), (_, expected) in zip(unreachable, missing, strict=True):
            with self.subTest(route=label):
                self.assertEqual(got.status_code, 404)
                self.assertEqual(expected.status_code, 404)
                self.assertEqual(body(got), body(expected))

    def test_t5_the_auto_grade_beat_grades_nothing_after_removal(self):
        from assignments.tasks import COURSE_NOT_FOUND, auto_grade_due_assignment

        self.remove_teacher()
        with patch("assignments.tasks.grade_engine_async.delay") as delay:
            outcome = auto_grade_due_assignment(str(self.assignment.id))

        delay.assert_not_called()
        self.assertEqual(outcome, COURSE_NOT_FOUND)
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
        it before grading - so nothing is graded and nothing is charged.

        Epic A (merge-down of bundle 4, SM ruling): the refusal is S7b's
        CourseNotReachableError, not beta's soft-return dict."""
        from assignments.tasks import COURSE_NOT_FOUND, grade_engine_async
        from students.exceptions import CourseNotReachableError

        self.remove_teacher()
        ledger_before = CreditLedger.objects.count()
        with patch("assignments.tasks.grade_engine") as grade_engine:
            result = grade_engine_async.apply(
                args=(str(self.teacher.id), str(self.submission.id)),
                kwargs={"processing_task_id": str(self.task.id)},
            ).result

        grade_engine.assert_not_called()
        self.assertIsInstance(result, CourseNotReachableError)
        self.assertEqual(str(result), COURSE_NOT_FOUND)
        self.assertEqual(CreditLedger.objects.count(), ledger_before)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, BackgroundTaskStatus.FAILURE)
        self.assertEqual(self.task.error, COURSE_NOT_FOUND)
        self.submission.refresh_from_db()
        self.assertIsNone(self.submission.graded_at)

    def test_a_batch_grading_run_is_refused_after_removal(self):
        from assignments.tasks import COURSE_NOT_FOUND, grade_batch_async

        self.remove_teacher()
        with patch("assignments.tasks.grade_engine_async.delay") as delay:
            result = grade_batch_async.apply(
                args=(str(self.teacher.id), str(self.assignment.id))
            ).result
        delay.assert_not_called()
        self.assertEqual(result, COURSE_NOT_FOUND)


class ReassignedCourseTests(TasksFixture):
    """1a's N1 / Q7: after the removal a super admin reassigns the school
    course to a colleague (Django admin). The ex-owner still owns the task
    and the batch session (`requested_by`, `session.teacher`), but not the
    course, so none of it may come back to them."""

    def setUp(self):
        super().setUp()
        self.remove_teacher()
        self.colleague = make_user("colleague@h38.test", UserTypes.TEACHER, self.school)
        Course.objects.filter(pk=self.course_id).update(teacher=self.colleague)

    def test_the_ex_owner_regains_no_route(self):
        client = jwt_client(self.teacher.email)
        for label, response in self.routes(client):
            with self.subTest(route=label):
                self.assertEqual(response.status_code, 404, response.content[:200])
                self.assertNotIn(SENTINEL, response.content.decode())
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, BackgroundTaskStatus.STARTED)

    def test_queued_grading_does_not_run_as_the_ex_owner(self):
        ledger_before = CreditLedger.objects.count()
        self.grade().assert_not_called()
        self.assertEqual(CreditLedger.objects.count(), ledger_before)

    def test_the_beat_now_grades_as_the_new_owner(self):
        """Control: the course isn't blocked, only the ex-owner is."""
        from assignments.tasks import auto_grade_due_assignment

        with patch("assignments.tasks.grade_engine_async.delay") as delay:
            auto_grade_due_assignment(str(self.assignment.id))
        self.assertEqual(delay.call_count, 1)
        self.assertEqual(delay.call_args.args[0], str(self.colleague.id))


class NothingLeaksToTheLogsTests(TasksFixture):
    def test_routes_the_refused_run_and_the_beat_log_ids_only(self):
        """1a's Q6: no student name or email in any record, from any
        logger, at any level."""
        from assignments.tasks import auto_grade_due_assignment

        self.remove_teacher()
        client = jwt_client(self.teacher.email)
        with AllLogs() as logs:
            self.routes(client)
            self.grade()
            with patch("assignments.tasks.grade_engine_async.delay"):
                auto_grade_due_assignment(str(self.assignment.id))
        text = "\n".join(logs.lines)
        for secret in (SENTINEL, self.student.email, self.teacher.email):
            self.assertNotIn(secret, text)
        self.assertIn(str(self.submission.id), text)


class AdminsOwnTasksTests(TasksFixture):
    def test_a_non_teacher_polling_their_own_task_is_not_blocked(self):
        """The rule is for teachers. A super admin's own task on the school
        course is judged by its ownership check, as before."""
        root = make_user("root@h38.test", UserTypes.SUPER_ADMIN)
        BackgroundProcessingTask.objects.filter(pk=self.task.pk).update(
            requested_by=root
        )
        self.remove_teacher()
        response = jwt_client(root.email).get(
            f"/api/v1/tasks/status/{self.task.celery_task_id}"
        )
        self.assertEqual(response.status_code, 200, response.content[:200])
