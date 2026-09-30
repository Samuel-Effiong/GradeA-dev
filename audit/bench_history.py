"""Epic A S4 Gate 6 (SM note 4): what history capture costs.

Not part of the suite (the file name doesn't match the test pattern). Run by
label on the test database:

    python manage.py test audit.bench_history --settings=settings_worktree

It prints p50/p95 for one grade save (the update-grade route's own save)
and for a roster import of 30 (the real bulk-add route), with capture on and
with capture off (`history.suppressed()` around the same work).
"""

import statistics
import time
from decimal import Decimal

from django.urls import reverse
from rest_framework.test import APITestCase

from audit import history
from audit.tests_history import World

RUNS = 40
ROSTER_RUNS = 10


def _percentiles(samples):
    ordered = sorted(samples)
    p95 = ordered[max(0, int(round(0.95 * len(ordered))) - 1)]
    return statistics.median(ordered) * 1000, p95 * 1000


class HistoryCostBenchmark(APITestCase):
    def test_print_the_cost(self):
        world = World("bench")
        submission = world.submission
        results = {}

        for label, capture in (
            ("grade save, capture on", True),
            ("grade save, capture off", False),
        ):
            samples = []
            for index in range(RUNS):
                submission.score = Decimal(10 + index % 5)
                started = time.perf_counter()
                if capture:
                    submission.save(update_fields=["score", "score_percentage"])
                else:
                    with history.suppressed():
                        submission.save(update_fields=["score", "score_percentage"])
                samples.append(time.perf_counter() - started)
            results[label] = _percentiles(samples)

        self.client.force_authenticate(user=world.teacher)
        header = "First Name\tLast Name\tEmail\n"
        for label, capture in (
            ("roster import of 30, capture on", True),
            ("roster import of 30, capture off", False),
        ):
            samples = []
            for run in range(ROSTER_RUNS):
                rows = "".join(
                    f"Bench\tStudent{run}x{index}\tbench.{label[-3:].strip()}.{run}.{index}@example.com\n"
                    for index in range(30)
                )
                url = reverse(
                    "course-bulk-add-students", kwargs={"pk": world.course.pk}
                )
                started = time.perf_counter()
                if capture:
                    response = self.client.post(url, {"raw_data": header + rows})
                else:
                    with history.suppressed():
                        response = self.client.post(url, {"raw_data": header + rows})
                samples.append(time.perf_counter() - started)
                self.assertEqual(response.status_code, 200, response.content[:200])
            results[label] = _percentiles(samples)

        print("\nS4_GATE6 (ms)")
        for label, (p50, p95) in results.items():
            print(f"S4_GATE6 {label}: p50={p50:.2f} p95={p95:.2f}")
