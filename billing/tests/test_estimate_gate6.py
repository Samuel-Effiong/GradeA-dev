"""B1b-4 estimate Gate 6 (plan §6.3 "the estimate endpoint's cost on 90 days
of AICallRecord"; SM ruling Q7, 2026-11-09).

100,000 call rows over 90 days (20,000 papers of 5 calls; 50 assignments;
10 teachers). It times the estimate (p50/p95 of 50 runs per tier) with the
two B1b-4 indexes, then with them dropped (inside the test's transaction, so
the drop rolls back), and EXPLAINs the two queries the indexes serve: the
assignment tier and the teacher tier. STOP only if either still plans a
sequential scan of ai_processor_aicallrecord with the indexes in place.

Opt-in like the repo's other load tests: set RUN_LOAD_TESTS=1.
"""

import importlib
import os
import random
import statistics
import time
import unittest
import uuid

from django.db import connection
from django.test import TestCase
from django.utils import timezone

from ai_processor.models import AICallRecord
from billing.testing.holds import ai_ready_teacher
from billing.tests.testing_estimates import make_assignment
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
)

LOAD_TESTS_ENABLED = os.environ.get("RUN_LOAD_TESTS") == "1"
ROWS = 100_000
CALLS_PER_PAPER = 5
TEACHERS = 10
ASSIGNMENTS = 50
RUNS = 50
INDEXES = (
    "aicall_assignment_created",
    "aicall_billed_user_created",
)

# importlib (the B1a precedent, NOTES §21 step 3): mypy stays quiet about
# names that do not exist until the product commit; the import still fails
# at the red.
estimates = importlib.import_module("billing.estimates")


def ms(seconds):
    return round(seconds * 1000, 3)


def p95(values):
    ordered = sorted(values)
    return ordered[max(0, int(round(0.95 * len(ordered))) - 1)]


@unittest.skipUnless(LOAD_TESTS_ENABLED, "load test: set RUN_LOAD_TESTS=1")
class EstimateGate6(TestCase):
    teachers: list
    assignments: list

    @classmethod
    def setUpTestData(cls):
        rng = random.Random(4)
        now = timezone.now()
        cls.teachers = [
            ai_ready_teacher(f"g6.est.{index}@example.com", 10_000)
            for index in range(TEACHERS)
        ]
        cls.assignments = [
            make_assignment(
                cls.teachers[index % TEACHERS],
                f"G6 {index}",
                questions=1 + index % 25,
                images=index % 2,
            )
            for index in range(ASSIGNMENTS)
        ]
        tasks = []
        for index in range(ROWS // CALLS_PER_PAPER):
            assignment = cls.assignments[index % ASSIGNMENTS]
            tasks.append(
                BackgroundProcessingTask(
                    requested_by=assignment.course.teacher,
                    task_type=BackgroundTaskType.SUBMISSION_GRADING,
                    assignment=assignment,
                    status=BackgroundTaskStatus.SUCCESS,
                )
            )
        BackgroundProcessingTask.objects.bulk_create(tasks, batch_size=5_000)
        questions = {a.pk: len(a.questions or []) for a in cls.assignments}
        records = []
        for task in tasks:
            for _ in range(CALLS_PER_PAPER):
                records.append(
                    AICallRecord(
                        task_id=str(uuid.uuid4()),
                        task_type="grade_assignment",
                        feature="Grading Assignment",
                        outcome="charged",
                        charged_credits=rng.randint(20, 400),
                        questions=questions[task.assignment_id],
                        images=0,
                        billed_user_id=task.requested_by_id,
                        assignment_id=task.assignment_id,
                        processing_task_id=task.pk,
                    )
                )
        AICallRecord.objects.bulk_create(records, batch_size=5_000)
        # auto_now_add stamps every row "now": spread them over 89 days, one
        # day per paper, so the 90-day window holds all of them.
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE ai_processor_aicallrecord SET created_at = %s - "
                "((abs(hashtext(processing_task_id::text)) %% 89) * interval '1 day')",
                [now],
            )
        with connection.cursor() as cursor:
            cursor.execute("ANALYZE ai_processor_aicallrecord")
            cursor.execute("ANALYZE students_backgroundprocessingtask")

    def time_tiers(self):
        assignment = self.assignments[7]
        teacher = assignment.course.teacher
        timings = {"assignment": [], "teacher": [], "all_papers": []}
        for _ in range(RUNS):
            started = time.perf_counter()
            estimates.paper_costs()
            timings["all_papers"].append(time.perf_counter() - started)
            started = time.perf_counter()
            estimates.paper_costs(assignment_id=assignment.pk)
            timings["assignment"].append(time.perf_counter() - started)
            started = time.perf_counter()
            estimates.paper_costs(billed_user_id=teacher.pk)
            timings["teacher"].append(time.perf_counter() - started)
        return {
            tier: (ms(statistics.median(values)), ms(p95(values)))
            for tier, values in timings.items()
        }

    def plans(self):
        assignment = self.assignments[7]
        teacher = assignment.course.teacher
        out = {}
        for tier, sql_params in (
            ("assignment", estimates.paper_costs_sql(assignment_id=assignment.pk)),
            ("teacher", estimates.paper_costs_sql(billed_user_id=teacher.pk)),
        ):
            sql, params = sql_params
            with connection.cursor() as cursor:
                cursor.execute("EXPLAIN " + sql, params)
                out[tier] = "\n".join(row[0] for row in cursor.fetchall())
        return out

    def test_the_estimate_on_ninety_days_of_rows(self):
        self.assertEqual(AICallRecord.objects.count(), ROWS)
        with_indexes = self.time_tiers()
        plans_with = self.plans()
        with connection.cursor() as cursor:
            for name in INDEXES:
                cursor.execute(f"DROP INDEX {name}")
            cursor.execute("ANALYZE ai_processor_aicallrecord")
        without_indexes = self.time_tiers()
        plans_without = self.plans()
        for tier in ("assignment", "teacher", "all_papers"):
            print(
                f"GATE6_EST tier={tier} rows={ROWS} runs={RUNS} "
                f"with_p50_ms={with_indexes[tier][0]} with_p95_ms={with_indexes[tier][1]} "
                f"without_p50_ms={without_indexes[tier][0]} "
                f"without_p95_ms={without_indexes[tier][1]}"
            )
        for tier in ("assignment", "teacher"):
            print(f"GATE6_EST_PLAN_WITH tier={tier}\n{plans_with[tier]}")
            print(f"GATE6_EST_PLAN_WITHOUT tier={tier}\n{plans_without[tier]}")
        for tier, plan in plans_with.items():
            with self.subTest(tier=tier):
                self.assertNotIn("Seq Scan on ai_processor_aicallrecord", plan)
