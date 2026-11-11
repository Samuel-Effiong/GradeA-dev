"""Epic B1b-6 Gate 6 (08b §8; SM ruling Q6, NOTES §6.3): the start of a
300-paper batch with the flag on, per estimate tier, on B1b-4's 100,000
call rows (90 days, 50 assignments, 10 teachers).

Every estimate reads the whole 90-day window once (estimates.paper_costs()
with no filter, B1b-4's "all_papers" read) and picks its tier from it, so
each tier's start includes the all-papers read; the tiers differ in which
papers they keep.

G6b (NOTES §6.6.15) times promote_job on the same fixture: 300 papers queued
on a drained wallet, a top-up, then one promotion per tier.

It reports the batch-start time (create 300 items, estimate, hold, claim and
send after commit, with the sends mocked) and its query count per tier. The
numbers are reported, not a STOP threshold: the SM judges them at the
checkpoint. The test asserts only that each tier is the one intended and
that all 300 papers were held and sent.

Opt-in like the repo's other load tests: set RUN_LOAD_TESTS=1.
"""

import time
import unittest
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from billing.models import AIJob, CreditBucket, CreditBucketType
from billing.testing.holds import ai_ready_teacher
from billing.tests import test_estimate_gate6 as b1b4_gate6
from billing.tests.testing_estimates import make_assignment
from classrooms.models import EnrollmentStatusType, StudentCourse
from students.models import BatchUploadSession, BatchUploadType, StudentSubmission
from users.models import CustomUser, UserTypes

PAPERS = 300


def ms(seconds):
    return round(seconds * 1000, 1)


@unittest.skipUnless(b1b4_gate6.LOAD_TESTS_ENABLED, "load test: set RUN_LOAD_TESTS=1")
@override_settings(BILLING_HOLDS_ENABLED=True)
class BatchStartGate6(b1b4_gate6.EstimateGate6):
    """Reuses EstimateGate6's 100,000-row fixture; its own test is not run
    here (it is B1b-4's evidence)."""

    def test_the_estimate_on_ninety_days_of_rows(self):
        self.skipTest("B1b-4's test; run in billing.tests.test_estimate_gate6")

    def papers(self, assignment, tag):
        for index in range(PAPERS):
            student = CustomUser.objects.create_user(
                email=f"g6b6.{tag}.{index}@example.com",
                password="testpass123",  # pragma: allowlist secret  # nosec B106
                user_type=UserTypes.STUDENT,
            )
            StudentCourse.objects.create(
                student=student,
                course=assignment.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            StudentSubmission.objects.create(
                assignment=assignment, student=student, answers={}
            )

    def fund(self, teacher):
        from billing.models import CreditWallet

        CreditBucket.objects.create(
            wallet=CreditWallet.objects.get(user=teacher),
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=10_000_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )

    def start(self, teacher, assignment):
        from assignments.tasks import _dispatch_tracked_grading

        session = BatchUploadSession.objects.create(
            teacher=teacher,
            course=assignment.course,
            task_type=BatchUploadType.GRADE,
            total_files=PAPERS,
        )
        sent = []

        def delay(*args, **kwargs):
            sent.append(kwargs["processing_task_id"])
            return SimpleNamespace(id=str(uuid.uuid4()))

        with patch(
            "assignments.tasks.grade_engine_async.delay", side_effect=delay
        ), CaptureQueriesContext(connection) as queries:
            started = time.perf_counter()
            with self.captureOnCommitCallbacks(execute=True):
                _dispatch_tracked_grading(
                    teacher,
                    assignment,
                    assignment.submissions.order_by("pk"),
                    session,
                )
            elapsed = time.perf_counter() - started
        job = AIJob.objects.filter(assignment=assignment).latest("created_at")
        return elapsed, len(queries.captured_queries), job, sent

    def test_a_300_paper_batch_start_per_tier(self):
        own = self.assignments[7]
        owner = own.course.teacher
        # The fixture's call rows record no images, so a paper of the same
        # shape as the history has the same question count and no image.
        shaped = make_assignment(
            owner, "G6B6 shape", questions=len(own.questions or []), images=0
        )
        stranger = ai_ready_teacher("g6b6.stranger@example.com", 10_000)
        odd = make_assignment(stranger, "G6B6 odd", questions=60, images=1)
        cases = (
            ("assignment", owner, own),
            ("teacher_shape", owner, shaped),
            ("per_question", stranger, odd),
        )
        for tier, teacher, assignment in cases:
            with self.subTest(tier=tier):
                self.papers(assignment, tier)
                self.fund(teacher)
                elapsed, queries, job, sent = self.start(teacher, assignment)
                print(
                    f"GATE6_B6 tier={tier} papers={PAPERS} "
                    f"source={job.estimate_source} start_ms={ms(elapsed)} "
                    f"queries={queries} held_per_item={job.held_per_item}"
                )
                self.assertEqual(job.estimate_source, tier)
                self.assertEqual(len(sent), PAPERS)

    # G6b (SM ruling on the B1b-6 checkpoint, NOTES §6.6.15): the promotion.

    def drain(self, teacher):
        """Spend every bucket, so nothing fits and the whole batch queues."""
        from django.db.models import F

        CreditBucket.objects.filter(wallet__user=teacher).update(
            used_credits=F("total_credits")
        )

    def promote(self, job):
        from billing import batch_queue

        sent = []

        def delay(*args, **kwargs):
            sent.append(kwargs["processing_task_id"])
            return SimpleNamespace(id=str(uuid.uuid4()))

        with patch(
            "assignments.tasks.grade_engine_async.delay", side_effect=delay
        ), CaptureQueriesContext(connection) as queries:
            started = time.perf_counter()
            with self.captureOnCommitCallbacks(execute=True):
                promoted = batch_queue.promote_job(job)
            elapsed = time.perf_counter() - started
        return elapsed, queries.captured_queries, promoted, sent

    def test_a_300_paper_promotion_per_tier(self):
        from ai_processor.models import AICallRecord
        from billing.models import AIJobStatus

        history = AICallRecord._meta.db_table
        own = self.assignments[7]
        owner = own.course.teacher
        shaped = make_assignment(
            owner, "G6B6b shape", questions=len(own.questions or []), images=0
        )
        stranger = ai_ready_teacher("g6b6b.stranger@example.com", 10_000)
        odd = make_assignment(stranger, "G6B6b odd", questions=60, images=1)
        cases = (
            ("assignment", owner, own),
            ("teacher_shape", owner, shaped),
            ("per_question", stranger, odd),
        )
        for tier, teacher, assignment in cases:
            with self.subTest(tier=tier):
                self.papers(assignment, f"b{tier}")
                self.drain(teacher)
                _, _, job, sent = self.start(teacher, assignment)
                self.assertEqual(job.estimate_source, tier)
                self.assertEqual(job.status, AIJobStatus.QUEUED_PENDING_CREDITS)
                self.assertEqual(sent, [], "a paper was sent before the top-up")
                self.fund(teacher)  # a bucket row: no ledger hook, no promotion
                elapsed, queries, promoted, sent = self.promote(job)
                print(
                    f"GATE6B_B6 tier={tier} papers={PAPERS} "
                    f"promote_ms={ms(elapsed)} queries={len(queries)} "
                    f"promoted={promoted} held_per_item={job.held_per_item}"
                )
                self.assertEqual(promoted, PAPERS, "not every paper was promoted")
                self.assertEqual(len(sent), PAPERS)
                # The promotion reads no call history, so its queries cannot
                # grow with it.
                self.assertEqual(
                    [q["sql"] for q in queries if history in q["sql"]],
                    [],
                    "the promotion read the call history",
                )
