"""
R-1: grading-task redelivery, proven on a real Celery worker against the
real Redis broker and the real result backend, with real PostgreSQL.

The worker is Celery's own in-process test worker (celery.contrib.testing)
bound to this project's configured app - so it uses the project's actual
broker URL, acks_late, visibility_timeout and task registrations - and it
consumes from a queue created for this test run only, so a developer's
worker on the shared broker never sees these messages. Because the worker
runs in this process, the AI pipeline can be replaced with a controllable
stand-in (an Event the test releases) without touching production code.

A Redis redelivery is the SAME message reaching a second worker slot:
same Celery task id, same processing_task_id. It is reproduced here by
publishing the message twice with the same task id while the first
execution is deliberately held inside the AI call.

Scenarios:
  1. Redelivery while the original is running - duplicate skips without
     touching the shared tracked task; original completes; graded once.
  2. Redelivery, then the original FAILS - the failure is recorded on the
     tracked task (this is the bug: it used to be masked by the
     duplicate's premature SUCCESS) and the claim is released.
  3. Recovery - a later dispatch after that failure grades successfully.
  4. Stale claim left by a dead worker - a redelivered message after the
     staleness window reclaims and grades.
  5. Concurrent processing - several submissions in flight at once plus a
     redelivery of one of them: every submission graded exactly once.

Run with (real Redis and PostgreSQL required):
    python manage.py test students.tests_grading_redelivery_live
"""

import threading
import time
import uuid
from datetime import timedelta
from unittest.mock import patch

import redis
from celery.contrib.testing.worker import start_worker
from celery.signals import task_postrun, worker_shutdown
from django.conf import settings
from django.db import connections
from django.test import TransactionTestCase
from django.utils import timezone

from assignments.models import Assignment
from assignments.tasks import grade_engine_async
from AutoGrader.celery import app as celery_app
from classrooms.models import Course, Session
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    GradingState,
    StudentSubmission,
)
from students.services import GRADING_CLAIM_STALE_AFTER
from users.models import CustomUser, UserTypes

VALID_GRADE = {
    "grading_summary": {"total_score": 8, "max_total_points": 10, "percentage": 80.0},
    "grading_confidence": 90,
    "question_evaluations": [],
}
WAIT = 45  # seconds; generous for a contended host, never reached when healthy


def _wait_until(predicate, timeout=WAIT, what="condition"):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out after {timeout}s waiting for {what}")


class GradingRedeliveryLiveTest(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.queue = f"s7-redelivery-{uuid.uuid4().hex[:10]}"

    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email=f"redelivery-teacher-{uuid.uuid4().hex[:6]}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Red",
            last_name="Elivery",
        )
        session = Session.objects.create(name="S", teacher=self.teacher)
        self.course = Course.objects.create(
            name="C", teacher=self.teacher, session=session
        )
        self.assignment = Assignment.objects.create(
            title="A",
            course=self.course,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )
        # Everything a worker execution reports, in order of completion.
        self.outcomes = []
        self._postrun_lock = threading.Lock()
        task_postrun.connect(self._on_postrun, weak=False, dispatch_uid=self.id())
        worker_shutdown.connect(
            self._on_worker_shutdown, weak=False, dispatch_uid=f"{self.id()}-shutdown"
        )

    def tearDown(self):
        task_postrun.disconnect(self._on_postrun, dispatch_uid=self.id())
        worker_shutdown.disconnect(
            self._on_worker_shutdown, dispatch_uid=f"{self.id()}-shutdown"
        )
        redis.Redis.from_url(settings.CELERY_BROKER_URL).delete(self.queue)
        super().tearDown()

    def _on_postrun(self, sender=None, task_id=None, state=None, retval=None, **kw):
        # Runs in the pool thread that executed the task. Celery's Django
        # fixup only closes connections that are unusable or past
        # CONN_MAX_AGE here, so an open one outlives the worker and shows
        # up at test-database teardown as "N other sessions using the
        # database". Close this thread's connection ourselves.
        try:
            if getattr(sender, "name", None) == grade_engine_async.name:
                with self._postrun_lock:
                    self.outcomes.append((task_id, state, retval))
        finally:
            connections.close_all()

    @staticmethod
    def _on_worker_shutdown(**kw):
        # The worker thread itself touched the DB (Celery's fixup runs the
        # Django system checks in it); close that connection too.
        connections.close_all()

    # -- fixtures -------------------------------------------------------

    def _submission(self, tag):
        student = CustomUser.objects.create_user(
            email=f"redelivery-{tag}-{uuid.uuid4().hex[:6]}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name=tag.title(),
            last_name="Student",
        )
        return StudentSubmission.objects.create(
            assignment=self.assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "An answer."}],
        )

    def _tracked(self, submission, celery_task_id):
        return BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.SUBMISSION_GRADING,
            assignment=self.assignment,
            submission=submission,
            celery_task_id=celery_task_id,
        )

    def _publish(self, submission, tracked, task_id):
        """Publish exactly what the grade-async view publishes, to the
        test's private queue, with an explicit task id so a redelivery
        can be reproduced by publishing again."""
        return grade_engine_async.apply_async(
            args=(str(self.teacher.id), str(submission.id)),
            kwargs={"processing_task_id": str(tracked.id)},
            queue=self.queue,
            task_id=task_id,
        )

    def _worker(self, concurrency=3):
        return start_worker(
            celery_app,
            queues=[self.queue],
            concurrency=concurrency,
            pool="threads",
            perform_ping_check=False,
            loglevel="WARNING",
            shutdown_timeout=30,
        )

    def _pipeline_patches(self, side_effect):
        """Replace the billed AI pipeline with `side_effect`, and neutralise
        the post-grade follow-up dispatches (formatted grade, summary
        refresh) so nothing lands on the shared default queue."""
        ai = patch("students.services.ai_processor")
        followups = patch("students.services.launch_processing_task")
        summary = patch("students.services.student_summary_async")
        mocks = (ai.start(), followups.start(), summary.start())
        for p in (ai, followups, summary):
            self.addCleanup(p.stop)
        mocks[0].extract_grade_with_retry.side_effect = side_effect
        return mocks[0]

    def _outcomes_for(self, task_id):
        with self._postrun_lock:
            return [o for o in self.outcomes if o[0] == task_id]

    # -- scenarios ------------------------------------------------------

    def test_1_redelivery_while_original_runs_skips_and_original_completes(self):
        submission = self._submission("s1")
        task_id = str(uuid.uuid4())
        tracked = self._tracked(submission, task_id)
        release = threading.Event()
        calls = []

        def held_grade(*args, **kwargs):
            calls.append(1)
            release.wait(timeout=WAIT)
            return VALID_GRADE

        mock_ai = self._pipeline_patches(held_grade)

        with self._worker():
            self._publish(submission, tracked, task_id)
            _wait_until(lambda: len(calls) == 1, what="original to enter the AI call")
            submission.refresh_from_db()
            self.assertEqual(submission.grading_state, GradingState.RUNNING)

            # The redelivery: same message, same id, second worker slot.
            self._publish(submission, tracked, task_id)
            _wait_until(
                lambda: len(self._outcomes_for(task_id)) == 1,
                what="the duplicate execution to finish",
            )
            _, state, retval = self._outcomes_for(task_id)[0]
            self.assertEqual(state, "SUCCESS")
            self.assertIn("duplicate run skipped", retval["message"])

            # The load-bearing assertion: the shared tracked task is still
            # the original's, still open.
            tracked.refresh_from_db()
            self.assertEqual(tracked.status, BackgroundTaskStatus.STARTED)
            self.assertIsNone(tracked.finished_at)
            self.assertFalse(tracked.meta.get("skipped"))

            release.set()
            _wait_until(
                lambda: len(self._outcomes_for(task_id)) == 2,
                what="the original execution to finish",
            )

        submission.refresh_from_db()
        self.assertEqual(submission.grading_state, GradingState.DONE)
        self.assertIsNotNone(submission.graded_at)
        self.assertEqual(float(submission.score), 8.0)
        tracked.refresh_from_db()
        self.assertEqual(tracked.status, BackgroundTaskStatus.SUCCESS)
        self.assertIsNotNone(tracked.finished_at)
        self.assertEqual(mock_ai.extract_grade_with_retry.call_count, 1)

    def test_2_and_3_original_failure_after_redelivery_is_recorded_then_recovers(
        self,
    ):
        submission = self._submission("s2")
        task_id = str(uuid.uuid4())
        tracked = self._tracked(submission, task_id)
        release = threading.Event()
        calls = []

        def held_then_failing_grade(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                release.wait(timeout=WAIT)
                raise TimeoutError("model timed out")
            return VALID_GRADE

        mock_ai = self._pipeline_patches(held_then_failing_grade)

        with self._worker():
            self._publish(submission, tracked, task_id)
            _wait_until(lambda: len(calls) == 1, what="original to enter the AI call")
            self._publish(submission, tracked, task_id)  # redelivery
            _wait_until(
                lambda: len(self._outcomes_for(task_id)) == 1,
                what="the duplicate to skip",
            )
            tracked.refresh_from_db()
            self.assertEqual(tracked.status, BackgroundTaskStatus.STARTED)

            release.set()
            _wait_until(
                lambda: len(self._outcomes_for(task_id)) == 2,
                what="the original to fail",
            )
            _, state, _ = self._outcomes_for(task_id)[1]
            self.assertEqual(state, "FAILURE")

            # Scenario 2: the failure IS recorded (previously the duplicate's
            # SUCCESS made the tracked task terminal first, and this stayed
            # SUCCESS with an empty error forever).
            tracked.refresh_from_db()
            self.assertEqual(tracked.status, BackgroundTaskStatus.FAILURE)
            self.assertIn("timed out", tracked.error)
            submission.refresh_from_db()
            self.assertEqual(submission.grading_state, GradingState.FAILED)
            self.assertIsNone(submission.graded_at)

            # Scenario 3: recovery - a fresh dispatch (what "try again"
            # does) reclaims the FAILED row and grades it.
            retry_id = str(uuid.uuid4())
            retry_tracked = self._tracked(submission, retry_id)
            self._publish(submission, retry_tracked, retry_id)
            _wait_until(
                lambda: len(self._outcomes_for(retry_id)) == 1,
                what="the retry to finish",
            )

        submission.refresh_from_db()
        self.assertEqual(submission.grading_state, GradingState.DONE)
        self.assertEqual(float(submission.score), 8.0)
        retry_tracked.refresh_from_db()
        self.assertEqual(retry_tracked.status, BackgroundTaskStatus.SUCCESS)
        self.assertEqual(mock_ai.extract_grade_with_retry.call_count, 2)

    def test_4_stale_claim_from_a_dead_worker_is_reclaimed_by_the_redelivery(self):
        submission = self._submission("s4")
        task_id = str(uuid.uuid4())
        tracked = self._tracked(submission, task_id)
        # What a SIGKILLed worker leaves behind: RUNNING, STARTED, and a
        # claim older than the staleness window. Redis redelivers the
        # unacked message once visibility_timeout elapses.
        StudentSubmission.objects.filter(pk=submission.pk).update(
            grading_state=GradingState.RUNNING,
            grading_started_at=timezone.now()
            - GRADING_CLAIM_STALE_AFTER
            - timedelta(minutes=1),
        )
        BackgroundProcessingTask.objects.filter(pk=tracked.pk).update(
            status=BackgroundTaskStatus.STARTED, started_at=timezone.now()
        )
        mock_ai = self._pipeline_patches(lambda *a, **k: VALID_GRADE)

        with self._worker():
            self._publish(submission, tracked, task_id)
            _wait_until(
                lambda: len(self._outcomes_for(task_id)) == 1,
                what="the redelivered task to finish",
            )

        submission.refresh_from_db()
        self.assertEqual(submission.grading_state, GradingState.DONE)
        self.assertGreater(
            submission.grading_started_at,
            timezone.now() - GRADING_CLAIM_STALE_AFTER,
            "the claim was refreshed by the recovering run",
        )
        tracked.refresh_from_db()
        self.assertEqual(tracked.status, BackgroundTaskStatus.SUCCESS)
        self.assertEqual(mock_ai.extract_grade_with_retry.call_count, 1)

    def test_5_concurrent_submissions_with_one_redelivery_each_grade_exactly_once(
        self,
    ):
        submissions = [self._submission(f"s5-{i}") for i in range(4)]
        ids = [str(uuid.uuid4()) for _ in submissions]
        tracked = [self._tracked(s, t) for s, t in zip(submissions, ids, strict=True)]
        release = threading.Event()
        graded_ids = []
        lock = threading.Lock()

        def slow_grade(user, questions, answers, *, assignment_model, **kw):
            release.wait(timeout=WAIT)
            with lock:
                graded_ids.append(answers[0]["answer_html"])
            return VALID_GRADE

        mock_ai = self._pipeline_patches(slow_grade)

        with self._worker(concurrency=6):
            for s, t, tid in zip(submissions, tracked, ids, strict=True):
                self._publish(s, t, tid)
            _wait_until(
                lambda: StudentSubmission.objects.filter(
                    pk__in=[s.pk for s in submissions],
                    grading_state=GradingState.RUNNING,
                ).count()
                == len(submissions),
                what="every submission to be claimed",
            )
            # Redeliver the first two while all four are held mid-call.
            for i in range(2):
                self._publish(submissions[i], tracked[i], ids[i])
            _wait_until(
                lambda: sum(len(self._outcomes_for(t)) for t in ids[:2]) == 2,
                what="both redeliveries to skip",
            )
            for t in tracked:
                t.refresh_from_db()
                self.assertEqual(t.status, BackgroundTaskStatus.STARTED)

            release.set()
            _wait_until(
                lambda: sum(len(self._outcomes_for(t)) for t in ids) == 6,
                what="all six executions to finish",
            )

        for s, t in zip(submissions, tracked, strict=True):
            s.refresh_from_db()
            t.refresh_from_db()
            self.assertEqual(s.grading_state, GradingState.DONE)
            self.assertEqual(t.status, BackgroundTaskStatus.SUCCESS)
        self.assertEqual(mock_ai.extract_grade_with_retry.call_count, len(submissions))
