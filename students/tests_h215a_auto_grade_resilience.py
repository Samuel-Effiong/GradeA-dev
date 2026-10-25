"""H-215a: the automatic grading at the due date never grades a paper twice,
retries a short outage, and resends only the papers it missed.

1. A paper queued by the automatic grading (or by the teacher's scheduled
   batch) that was graded meanwhile is skipped when its run starts: no
   provider call, no charge, the grade already given stands. The guard is
   the grading claim itself: one atomic UPDATE that, on these two paths,
   also requires graded_at to be empty. The teacher's own grading of a
   graded paper is a deliberate re-grade and is unchanged.
2. auto_grade_due_assignment retries a database or broker outage, at most
   five times, after 60 s doubling to 15 min, plus up to 30 s. Nothing else
   is retried: a missing assignment, a course its teacher can no longer
   reach, a missing rubric and any other error keep today's answer.
3. A retry reuses the batch row and sends only the papers whose dispatch
   never reached the broker.

The grading tests run the real grade_engine_async with only the provider
call replaced and count CreditUsageLog rows. The dispatch is captured at
Task.apply_async, so the real launcher still makes each tracked item. New
names are read with getattr, so this module loads against the old code.

Run with:
    python manage.py test students.tests_h215a_auto_grade_resilience
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from celery.exceptions import Retry
from django.db.utils import InterfaceError
from django.db.utils import OperationalError as DatabaseOperationalError
from django.test import override_settings
from kombu.exceptions import OperationalError as BrokerOperationalError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from ai_processor.services import AIProcessor
from assignments import tasks
from assignments.models import Assignment, AssignmentStatus
from assignments.tasks import (
    COURSE_NOT_FOUND,
    auto_grade_due_assignment,
    grade_batch_async,
    grade_engine_async,
)
from AutoGrader.dispatch import ProcessingTemporarilyUnavailable
from billing.models import CreditUsageLog, PlanType
from billing.tests.test_execute_graded_task import ExecuteGradedTaskTestBase
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BatchUploadSession,
    StudentSubmission,
)
from users.models import UserTypes

GRADE_TASK = "assignments.tasks.grade_engine_async"
#: Read by name: the helper is new in H-215a (tests first).
COUNTDOWN = "_auto_grade_retry_countdown"
ANSWER = "The water cycle moves water between the sea, the air and the land."
QUESTION = {
    "question_number": 1,
    "question_text": "Describe the water cycle.",
    "question_type": "ESSAY",
    "points": 10,
    "options": [],
    "rubric": [
        {"level": "excellent", "description": "Full", "points": 10},
        {"level": "good", "description": "Most", "points": 8},
        {"level": "poor", "description": "None", "points": 0},
    ],
    "model_answer": "Evaporation, condensation, precipitation.",
}
GRADING_REPLY = {
    "question_evaluations": [
        {
            "question_number": 1,
            "score_awarded": 8,
            "max_points": 10,
            "level_achieved": "good",
            "evaluation_rationale": "Names the movement of water.",
            "evidence_quotes": [ANSWER],
        }
    ],
    "grading_summary": {"total_score": 8, "max_total_points": 10, "percentage": 80},
    "grading_confidence": 90,
    "overall_performance_analysis": "ok",
    "recommendations": [],
}
#: Every class the retry is for (one shared tuple in the task).
TRANSIENT = (
    DatabaseOperationalError,
    InterfaceError,
    ProcessingTemporarilyUnavailable,
    RedisConnectionError,
    RedisTimeoutError,
    BrokerOperationalError,
    ConnectionError,
    TimeoutError,
)


def _reply(*args, **kwargs):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=json.dumps(GRADING_REPLY)),
                finish_reason="stop",
            )
        ],
        usage=SimpleNamespace(total_tokens=500),
        model="x-ai/grok-4.3",
    )


class Classroom(ExecuteGradedTaskTestBase):
    """A PRO teacher with credits, a course, an essay assignment with a
    rubric that grades itself at the due date, and enrolled students."""

    def setUp(self):
        super().setUp()
        self.teacher = self._make_teacher_with_credits(
            plan=self._make_plan(PlanType.PRO), credits=500_000
        )
        session = Session.objects.create(name="H215a", teacher=self.teacher)
        self.course = Course.objects.create(
            name="H215a", teacher=self.teacher, session=session
        )
        self.assignment = Assignment.objects.create(
            title="Water",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[QUESTION],
            auto_grade_on_due_date=True,
        )

    def paper(self):
        student = self._make_user(
            UserTypes.STUDENT, f"s-{uuid4().hex[:10]}@example.com"
        )
        StudentCourse.objects.create(
            student=student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        return StudentSubmission.objects.create(
            assignment=self.assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": f"<p>{ANSWER}</p>"}],
        )

    def capture_dispatch(self, fail_at=None):
        """Task.apply_async replaced: each grading message is kept, and the
        `fail_at`-th one (1-based) raises a broker outage instead."""
        sent = []

        def apply_async(task, args=None, kwargs=None, **options):
            if task.name == GRADE_TASK:
                if fail_at is not None and len(sent) + 1 == fail_at:
                    sent.append(None)
                    raise RedisConnectionError("broker down")
                sent.append((tuple(args or ()), dict(kwargs or {})))
            return SimpleNamespace(id=f"celery-{uuid4().hex[:8]}")

        return sent, patch(
            "celery.app.task.Task.apply_async", autospec=True, side_effect=apply_async
        )


@override_settings(
    # One provider call per grading: no second grader, and no answer reuse
    # (a reused answer would skip the call this module counts).
    GRADING_SECOND_OPINION_ENABLED=False,
    GRADING_ANSWER_CACHE_ENABLED=False,
)
class APaperGradedMeanwhileIsNotGradedAgainTest(Classroom):
    def setUp(self):
        super().setUp()
        provider = patch.object(
            AIProcessor, "_AIProcessor__ai_model", side_effect=_reply
        )
        self.provider = provider.start()
        self.addCleanup(provider.stop)
        self.submission = self.paper()

    def queue(self, start):
        sent, dispatch = self.capture_dispatch()
        with dispatch:
            start()
        self.assertEqual(len(sent), 1)
        return sent[0]

    def run_message(self, message):
        args, kwargs = message
        return grade_engine_async.apply(args=args, kwargs=kwargs)

    def teacher_grades(self):
        outcome = grade_engine_async.apply(
            args=(str(self.teacher.id), str(self.submission.id))
        )
        self.assertTrue(outcome.successful(), outcome.result)
        self.submission.refresh_from_db()
        self.assertIsNotNone(self.submission.graded_at)

    def test_an_auto_paper_graded_by_the_teacher_meanwhile_is_skipped(self):
        message = self.queue(lambda: auto_grade_due_assignment(str(self.assignment.id)))
        self.teacher_grades()
        graded_at = self.submission.graded_at
        charges = CreditUsageLog.objects.count()

        outcome = self.run_message(message)

        self.assertEqual(
            self.provider.call_count,
            1,
            "the auto run reached the provider for a paper already graded",
        )
        self.assertEqual(CreditUsageLog.objects.count(), charges)
        self.assertTrue(outcome.successful(), outcome.result)
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.graded_at, graded_at)
        item = BackgroundProcessingTask.objects.get(id=message[1]["processing_task_id"])
        self.assertEqual(item.status, BackgroundTaskStatus.SUCCESS)
        self.assertIs(item.meta.get("skipped"), True)
        [session] = BatchUploadSession.objects.filter(course=self.course)
        self.assertEqual(session.results[-1].get("status"), "SUCCESS", session.results)
        self.assertEqual(session.results[-1].get("note"), "already graded")

    def test_two_deliveries_of_one_auto_paper_charge_once(self):
        message = self.queue(lambda: auto_grade_due_assignment(str(self.assignment.id)))
        before = CreditUsageLog.objects.count()

        first = self.run_message(message)
        self.assertTrue(first.successful(), first.result)
        after_first = CreditUsageLog.objects.count()
        self.assertGreater(after_first, before, "the first delivery charged nothing")

        self.run_message(message)

        self.assertEqual(
            CreditUsageLog.objects.count(),
            after_first,
            "the second delivery charged again",
        )
        self.assertEqual(self.provider.call_count, 1)

    def test_a_scheduled_batch_paper_graded_meanwhile_is_skipped(self):
        message = self.queue(
            lambda: grade_batch_async.run(str(self.teacher.id), str(self.assignment.id))
        )
        self.teacher_grades()
        charges = CreditUsageLog.objects.count()

        self.run_message(message)

        self.assertEqual(
            self.provider.call_count,
            1,
            "the scheduled batch run reached the provider for a paper already graded",
        )
        self.assertEqual(CreditUsageLog.objects.count(), charges)

    def test_the_teachers_own_regrade_still_grades_and_charges(self):
        self.teacher_grades()
        charges = CreditUsageLog.objects.count()

        self.teacher_grades()

        self.assertEqual(self.provider.call_count, 2)
        self.assertGreater(CreditUsageLog.objects.count(), charges)


class TheFlagIsOnEveryAutomaticDispatchTest(Classroom):
    def flags(self, start):
        self.paper()
        self.paper()
        sent, dispatch = self.capture_dispatch()
        with dispatch:
            start()
        return [kwargs.get("only_if_ungraded") for _args, kwargs in sent]

    def test_every_auto_grade_dispatch_carries_the_flag(self):
        self.assertEqual(
            self.flags(lambda: auto_grade_due_assignment(str(self.assignment.id))),
            [True, True],
        )

    def test_every_scheduled_batch_dispatch_carries_the_flag(self):
        self.assertEqual(
            self.flags(
                lambda: grade_batch_async.run(
                    str(self.teacher.id), str(self.assignment.id)
                )
            ),
            [True, True],
        )


def _assignment_that_raises(error):
    stand_in = MagicMock()
    stand_in.objects.get.side_effect = error
    return stand_in


class AnOutageIsRetriedTest(Classroom):
    def run_with_retry_caught(self, **call):
        retry = patch.object(
            auto_grade_due_assignment, "retry", side_effect=Retry("retrying")
        )
        with retry as caught:
            outcome = auto_grade_due_assignment.apply(**call)
        return caught, outcome

    def test_a_database_outage_is_retried_once_with_the_first_countdown(self):
        error = DatabaseOperationalError("database down")
        with patch("assignments.tasks.Assignment", _assignment_that_raises(error)):
            retry, _outcome = self.run_with_retry_caught(
                args=(str(self.assignment.id),)
            )
        self.assertEqual(retry.call_count, 1, "a database outage was not retried")
        call = retry.call_args.kwargs
        self.assertIs(call["exc"], error)
        self.assertEqual(call["max_retries"], 5)
        self.assertGreaterEqual(call["countdown"], 60)
        self.assertLessEqual(call["countdown"], 90)
        self.assertEqual(
            call["kwargs"],
            {"assignment_id": str(self.assignment.id), "session_id": None},
        )

    def test_each_transient_class_is_retried(self):
        for error_class in TRANSIENT:
            with self.subTest(error=error_class.__name__):
                error = error_class("outage")
                with patch(
                    "assignments.tasks.Assignment", _assignment_that_raises(error)
                ):
                    retry, _outcome = self.run_with_retry_caught(
                        args=(str(self.assignment.id),)
                    )
                self.assertEqual(retry.call_count, 1, "an outage was not retried")

    def test_the_countdown_doubles_to_fifteen_minutes_plus_up_to_thirty_seconds(self):
        countdown = getattr(tasks, COUNTDOWN)
        with patch("assignments.tasks.random.randint", return_value=0):
            low = [countdown(retries) for retries in range(6)]
        with patch("assignments.tasks.random.randint", return_value=30):
            high = [countdown(retries) for retries in range(6)]
        self.assertEqual(low, [60, 120, 240, 480, 900, 900])
        self.assertEqual(high, [90, 150, 270, 510, 930, 930])

    def test_a_broker_outage_mid_dispatch_resends_only_the_missed_papers(self):
        papers = [self.paper(), self.paper(), self.paper()]
        sent, dispatch = self.capture_dispatch(fail_at=2)
        with dispatch:
            retry, _outcome = self.run_with_retry_caught(
                args=(str(self.assignment.id),)
            )
        self.assertEqual(retry.call_count, 1, "a broker outage was not retried")
        [session] = BatchUploadSession.objects.filter(course=self.course)
        self.assertEqual(
            retry.call_args.kwargs["kwargs"],
            {"assignment_id": str(self.assignment.id), "session_id": str(session.id)},
        )
        reached = {args[1] for args, _kwargs in [m for m in sent if m is not None]}
        self.assertEqual(len(reached), 1)

        again, dispatch = self.capture_dispatch()
        with dispatch:
            auto_grade_due_assignment.apply(
                kwargs={
                    "assignment_id": str(self.assignment.id),
                    "session_id": str(session.id),
                }
            )

        resent = {args[1] for args, _kwargs in again}
        self.assertEqual(resent, {str(p.id) for p in papers} - reached)
        self.assertEqual(
            BatchUploadSession.objects.filter(course=self.course).count(), 1
        )
        new_items = BackgroundProcessingTask.objects.filter(
            batch_session=session, item_index__gt=2
        )
        self.assertEqual(sorted(i.item_index for i in new_items), [3, 4])


class NothingElseIsRetriedTest(Classroom):
    def outcome_without_retry(self, **call):
        with patch.object(
            auto_grade_due_assignment, "retry", side_effect=Retry("retrying")
        ) as retry:
            outcome = auto_grade_due_assignment.apply(**call)
        self.assertEqual(retry.call_count, 0)
        return outcome.result

    def test_any_other_error_keeps_todays_answer(self):
        with patch(
            "assignments.tasks.Assignment",
            _assignment_that_raises(RuntimeError("boom")),
        ):
            result = self.outcome_without_retry(args=(str(self.assignment.id),))
        self.assertEqual(result, "Error: RuntimeError")

    def test_a_missing_assignment_is_not_retried(self):
        self.assertEqual(
            self.outcome_without_retry(args=(str(uuid4()),)), "Error: DoesNotExist"
        )

    def test_a_course_the_teacher_cannot_reach_is_not_retried(self):
        self.paper()
        with patch("assignments.tasks.teacher_can_reach_course", return_value=False):
            result = self.outcome_without_retry(args=(str(self.assignment.id),))
        self.assertEqual(result, COURSE_NOT_FOUND)

    def test_a_missing_rubric_is_not_retried(self):
        self.paper()
        Assignment.objects.filter(pk=self.assignment.pk).update(questions=[])
        with patch("assignments.tasks.launch_processing_task"):
            result = self.outcome_without_retry(args=(str(self.assignment.id),))
        self.assertEqual(result, "Refused: RUBRIC_MISSING")

    def test_the_task_keeps_its_registered_name(self):
        self.assertEqual(
            auto_grade_due_assignment.name,
            "assignments.tasks.auto_grade_due_assignment",
        )
