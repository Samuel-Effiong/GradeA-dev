"""Epic B1b step 1 (FB-1, 08b §5.1): what a 1,000-item cascade costs.

The FB-1 receiver is a pre_delete on BackgroundProcessingTask. Any receiver
turns off Django's fast delete for the model, so a cascade loads the rows
and sends one signal per row. This measures one assignment delete that
cascades 1,000 task rows, on two shapes:

- "no_job": 1,000 rows with no AIJob (every row written with the flag off);
- "one_job": 1,000 rows in one UPLOAD job, 900 RETURNED and 100 HELD.

It runs on today's code (BEFORE) and on the fix (AFTER), and the numbers
go in NOTES. It asserts nothing about the time: the SM decides on the
numbers (08b §5.1 "the cost to weigh").

Opt-in like the repo's other load tests: set RUN_LOAD_TESTS=1.
"""

import os
import statistics
import time
import unittest
import uuid

from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from assignments.models import Assignment, AssignmentStatus
from billing.models import AIJob, AIJobType, CreditWallet, HoldState
from billing.testing.holds import funded_wallet
from classrooms.models import Course, Session
from students.models import BackgroundProcessingTask

LOAD_TESTS_ENABLED = os.environ.get("RUN_LOAD_TESTS") == "1"
ITEMS = 1000
HELD_ITEMS = 100
HOLD = 50
DRAWN = 30
RUNS = 3


@unittest.skipUnless(LOAD_TESTS_ENABLED, "load test: set RUN_LOAD_TESTS=1")
@override_settings(BILLING_HOLDS_ENABLED=True)
class FB1CascadeTimingTests(TransactionTestCase):
    def build(self, tag, *, with_job):
        wallet = funded_wallet(f"fb1.timing.{tag}@example.com", 10**7)
        teacher = wallet.user
        session = Session.objects.create(name="S", teacher=teacher)
        course = Course.objects.create(name="C", teacher=teacher, session=session)
        assignment = Assignment.objects.create(
            title="A", course=course, status=AssignmentStatus.PUBLISHED, questions=[]
        )
        job = None
        if with_job:
            settled = ITEMS - HELD_ITEMS
            job = AIJob.objects.create(
                job_type=AIJobType.UPLOAD,
                wallet=wallet,
                requested_by=teacher,
                trace_id=uuid.uuid4(),
                total_items=ITEMS,
                reserved_credits=ITEMS * HOLD,
                hold_drawn=settled * DRAWN,
                released_credits=settled * (HOLD - DRAWN),
            )
            CreditWallet.objects.filter(pk=wallet.pk).update(
                reserved_credits=HELD_ITEMS * HOLD
            )
        rows = []
        for index in range(ITEMS):
            held = with_job and index < HELD_ITEMS
            fields = {
                "requested_by": teacher,
                "task_type": "answer_extraction",
                "assignment": assignment,
            }
            if with_job:
                fields.update(
                    job=job,
                    hold_credits=HOLD,
                    hold_state=HoldState.HELD if held else HoldState.RETURNED,
                    hold_drawn=0 if held else DRAWN,
                    released_credits=0 if held else HOLD - DRAWN,
                )
            rows.append(BackgroundProcessingTask(**fields))
        BackgroundProcessingTask.objects.bulk_create(rows)
        return assignment

    def measure(self, shape, with_job):
        seconds, queries = [], []
        for run in range(RUNS):
            assignment = self.build(f"{shape}.{run}", with_job=with_job)
            with CaptureQueriesContext(connection) as captured:
                start = time.perf_counter()
                assignment.delete()
                seconds.append(time.perf_counter() - start)
            queries.append(len(captured.captured_queries))
            self.assertFalse(
                BackgroundProcessingTask.objects.filter(
                    assignment_id=assignment.pk
                ).exists()
            )
        print(
            f"\nFB1-TIMING shape={shape} items={ITEMS} runs={RUNS} "
            f"seconds={[round(s, 4) for s in seconds]} "
            f"median_s={statistics.median(seconds):.4f} queries={queries}"
        )

    def test_assignment_cascade_of_1000_items(self):
        self.measure("no_job", with_job=False)
        self.measure("one_job", with_job=True)
