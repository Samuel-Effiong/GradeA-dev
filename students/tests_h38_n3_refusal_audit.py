"""H-38 tasks N3 (Epic A): a grading run refused because its course is no
longer reachable by the teacher it would run as leaves ONE audit event that
says so.

THE GAP
-------
Three places refuse (assignments/tasks.py):
  1. grade_engine_async raises CourseNotReachableError. Its except block
     already wrote one GRADING_FAILED for the submission, but with no reason:
     the trail could not tell it from any other user-class failure.
  2. grade_batch_async returned "This course wasn't found." and wrote nothing.
  3. auto_grade_due_assignment did the same.

THE FIX (SM rulings, 2026-10-02)
--------------------------------
One audit-only reason code, COURSE_NOT_REACHABLE. It is recorded in the
audit trail and never sent to a client: the exception stays uncoded and the
answer stays the plain "This course wasn't found.".
  1. keeps its shape (target the submission; actor the tracked task's
     requester, SYSTEM when there is none) and gains the reason.
  2. and 3. each write one GRADING_FAILED about the TEACHER (target
     CustomUser), with the assignment's id in the metadata. The batch
     records its tracked task's requester when it has one, else SYSTEM;
     the auto-grade, which Beat starts, is SYSTEM.
"""

import json
from unittest.mock import patch

from assignments.tasks import (
    COURSE_NOT_FOUND,
    auto_grade_due_assignment,
    grade_batch_async,
    grade_engine_async,
)
from audit.enums import ActorRole, AuditAction, AuditOutcome, ErrorClass, ReasonCode
from audit.models import AuditEvent
from AutoGrader.reason_codes import (
    AUDIT_ONLY_CODES,
    REASON_CODES,
    CodedError,
    reason_of,
)
from students.exceptions import CourseNotReachableError
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
)
from students.tests_h38_tasks_namespace import SENTINEL, TasksFixture

CODE = "COURSE_NOT_REACHABLE"


class RefusalAuditFixture(TasksFixture):
    """The three refusals, each run the way production runs it."""

    def failures(self):
        return list(
            AuditEvent.objects.filter(action=AuditAction.GRADING_FAILED).order_by(
                "occurred_at"
            )
        )

    def refuse_the_run(self, tracked=True):
        kwargs = {"processing_task_id": str(self.task.id)} if tracked else {}
        with patch("assignments.tasks.grade_engine") as grade_engine:
            result = grade_engine_async.apply(
                args=(str(self.teacher.id), str(self.submission.id)), kwargs=kwargs
            ).result
        grade_engine.assert_not_called()
        return result

    def refuse_the_batch(self, tracked=False):
        kwargs = {}
        if tracked:
            self.batch_task = BackgroundProcessingTask.objects.create(
                requested_by=self.teacher,
                task_type=BackgroundTaskType.BATCH_SUBMISSION_GRADING,
                assignment=self.assignment,
                file_name="Scheduled grading",
                status=BackgroundTaskStatus.PENDING,
            )
            kwargs = {"processing_task_id": str(self.batch_task.id)}
        with patch("assignments.tasks.grade_engine_async.delay") as delay:
            result = grade_batch_async.apply(
                args=(str(self.teacher.id), str(self.assignment.id)), kwargs=kwargs
            ).result
        delay.assert_not_called()
        return result

    def refuse_the_auto_grade(self):
        with patch("assignments.tasks.grade_engine_async.delay") as delay:
            result = auto_grade_due_assignment(str(self.assignment.id))
        delay.assert_not_called()
        return result

    def assertRefusal(self, event):
        self.assertEqual(event.reason_code, CODE)
        self.assertEqual(event.outcome, AuditOutcome.FAILURE)
        self.assertEqual(event.error_class, ErrorClass.USER)


class RefusedGradingRunTests(RefusalAuditFixture):
    """Refusal 1: grade_engine_async."""

    def setUp(self):
        super().setUp()
        self.remove_teacher()

    def test_a_refused_run_records_one_event_with_the_reason(self):
        self.refuse_the_run()

        [event] = self.failures()
        self.assertRefusal(event)
        # Its place in the submission's history is unchanged (S7b).
        self.assertEqual(event.target_type, "StudentSubmission")
        self.assertEqual(event.target_id, self.submission.id)
        self.assertEqual(event.actor_id, self.teacher.id)
        self.assertEqual(event.metadata["submission_id"], str(self.submission.id))
        self.assertEqual(event.metadata["task_id"], str(self.task.id))

    def test_a_refused_run_with_no_tracked_task_is_the_systems(self):
        self.refuse_the_run(tracked=False)

        [event] = self.failures()
        self.assertRefusal(event)
        self.assertEqual(event.actor_role, ActorRole.SYSTEM)
        self.assertIsNone(event.actor_id)
        self.assertEqual(event.target_id, self.submission.id)


class RefusedBatchTests(RefusalAuditFixture):
    """Refusal 2: grade_batch_async."""

    def setUp(self):
        super().setUp()
        self.remove_teacher()

    def test_a_refused_batch_records_one_event_about_the_teacher(self):
        self.assertEqual(self.refuse_the_batch(), COURSE_NOT_FOUND)

        [event] = self.failures()
        self.assertRefusal(event)
        self.assertEqual(event.target_type, "CustomUser")
        self.assertEqual(event.target_id, self.teacher.id)
        self.assertEqual(event.metadata["assignment_id"], str(self.assignment.id))
        # Scheduled with no tracked task: nobody asked for this run now.
        self.assertEqual(event.actor_role, ActorRole.SYSTEM)
        self.assertIsNone(event.actor_id)

    def test_a_refused_tracked_batch_names_its_requester(self):
        self.refuse_the_batch(tracked=True)

        [event] = self.failures()
        self.assertRefusal(event)
        self.assertEqual(event.actor_id, self.teacher.id)
        self.assertEqual(event.target_type, "CustomUser")
        self.assertEqual(event.target_id, self.teacher.id)
        self.assertEqual(event.metadata["task_id"], str(self.batch_task.id))

    def test_the_event_belongs_to_the_courses_school(self):
        """The removed teacher has no school any more; the school whose
        course it is must still find the refusal in its own trail."""
        self.refuse_the_batch()

        [event] = self.failures()
        self.assertEqual(event.school_id, self.school.id)


class RefusedAutoGradeTests(RefusalAuditFixture):
    """Refusal 3: auto_grade_due_assignment (started by Beat)."""

    def setUp(self):
        super().setUp()
        self.remove_teacher()

    def test_a_refused_auto_grade_records_one_system_event_about_the_teacher(self):
        self.assertEqual(self.refuse_the_auto_grade(), COURSE_NOT_FOUND)

        [event] = self.failures()
        self.assertRefusal(event)
        self.assertEqual(event.actor_role, ActorRole.SYSTEM)
        self.assertIsNone(event.actor_id)
        self.assertEqual(event.target_type, "CustomUser")
        self.assertEqual(event.target_id, self.teacher.id)
        self.assertEqual(event.metadata["assignment_id"], str(self.assignment.id))
        self.assertEqual(event.school_id, self.school.id)

    def test_each_refusal_writes_exactly_one_event(self):
        self.refuse_the_auto_grade()
        self.assertEqual(len(self.failures()), 1)
        self.refuse_the_batch()
        self.assertEqual(len(self.failures()), 2)
        self.refuse_the_run()
        self.assertEqual(len(self.failures()), 3)


class NoEventWithoutARefusalTests(RefusalAuditFixture):
    """Control: a member's runs are not refused and record no failure."""

    def test_a_members_runs_record_no_failure(self):
        self.grade().assert_called_once()
        with patch("assignments.tasks.grade_engine_async.delay"):
            grade_batch_async.apply(
                args=(str(self.teacher.id), str(self.assignment.id))
            )
            auto_grade_due_assignment(str(self.assignment.id))

        self.assertEqual(self.failures(), [])
        self.assertFalse(AuditEvent.objects.filter(reason_code=CODE).exists())


class IdsOnlyTests(RefusalAuditFixture):
    def test_the_three_events_carry_ids_only(self):
        self.remove_teacher()
        self.refuse_the_run()
        self.refuse_the_batch()
        self.refuse_the_auto_grade()

        events = self.failures()
        self.assertEqual(len(events), 3)
        allowed = {"assignment_id", "submission_id", "task_id", "prompt_version"}
        for event in events:
            with self.subTest(target=event.target_type):
                self.assertLessEqual(set(event.metadata), allowed)
                # Everything the call sites supply. (actor_email is the
                # emitter's own column for a staff actor, not theirs.)
                supplied = json.dumps(
                    [
                        event.metadata,
                        event.before,
                        event.after,
                        event.target_type,
                        str(event.target_id),
                    ]
                )
                self.assertNotIn("@", supplied)
                self.assertNotIn(SENTINEL, supplied)
                self.assertIsNone(event.before)
                self.assertIsNone(event.after)


class TheCodeNeverReachesAClientTests(RefusalAuditFixture):
    """SM ruling: COURSE_NOT_REACHABLE is audit-only."""

    def test_the_code_is_audit_only_and_the_refusal_stays_uncoded(self):
        code = ReasonCode(CODE)
        self.assertIn(code, AUDIT_ONLY_CODES)
        # No spec, so no coded error body can be built from it.
        self.assertNotIn(code, REASON_CODES)
        refusal = CourseNotReachableError()
        self.assertNotIsInstance(refusal, CodedError)
        self.assertIsNone(reason_of(refusal))
        self.assertEqual(str(refusal), COURSE_NOT_FOUND)

    def test_nothing_a_client_reads_carries_the_code(self):
        self.remove_teacher()
        results = [
            self.refuse_the_run(),
            self.refuse_the_batch(tracked=True),
            self.refuse_the_auto_grade(),
        ]

        # What the tasks return (Celery's result backend).
        self.assertEqual([str(r) for r in results], [COURSE_NOT_FOUND] * 3)
        # What the task and batch routes serve.
        for task in BackgroundProcessingTask.objects.all():
            with self.subTest(task=task.task_type, status=task.status):
                self.assertIsNone(task.reason_code)
                served = json.dumps([task.error, task.meta], default=str)
                self.assertNotIn(CODE, served)
        for session in BatchUploadSession.objects.all():
            self.assertNotIn(CODE, json.dumps(session.results, default=str))
        self.task.refresh_from_db()
        self.assertEqual(self.task.error, COURSE_NOT_FOUND)


class ReasonMetricTests(RefusalAuditFixture):
    def test_the_refusal_is_counted_by_its_reason(self):
        self.remove_teacher()
        with patch("audit.emitter.audit_metrics.count") as count:
            self.refuse_the_auto_grade()

        count.assert_any_call("reason_code_rate", tags={"code": CODE})
