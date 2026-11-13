"""Epic B1b-7 (plan §3.10, NFR-OBS-03; NOTES §7.2 D7-d, ruling §7.5 Q7-5,
contract §7.10): when a job settles with an estimate, credit_estimate_gap
(actual/p50, bucketed) and credit_estimate_band_hit (actual <= p90) are
emitted once. Internal metrics; no alert in B1b.

    python manage.py test billing.tests.test_b1b7_gap_metric
"""

import importlib
from unittest.mock import patch

from django.test import TestCase

from billing.models import AIJobStatus
from billing.testing.holds import funded_wallet, make_job

COUNT = "audit.metrics.count"


def holds():
    return importlib.import_module("billing.holds")


def emitted(count):
    return [
        (call.args[0], call.kwargs.get("tags"))
        for call in count.call_args_list
        if call.args
        and call.args[0] in ("credit_estimate_gap", "credit_estimate_band_hit")
    ]


class GapBucketTests(TestCase):
    def test_the_bucket_edges(self):
        cases = (
            (49, "<0.5"),
            (50, "0.5-0.8"),
            (79, "0.5-0.8"),
            (80, "0.8-1.2"),
            (120, "0.8-1.2"),
            (121, "1.2-2"),
            (200, "1.2-2"),
            (201, ">2"),
        )
        for actual, label in cases:
            with self.subTest(actual=actual):
                self.assertEqual(holds().gap_bucket(actual, 100), label)


class GapMetricTests(TestCase):
    def setUp(self):
        self.wallet = funded_wallet("b1b7.gap@example.com", 10_000)

    def job(self, **over):
        fields = {
            "job_type": "BATCH_GRADING",
            "status": AIJobStatus.RUNNING,
            "estimate_p50": 100,
            "estimate_p90": 150,
            "consumed_credits": 140,
            "unbilled_credits": 20,
        }
        fields.update(over)
        return make_job(self.wallet, **fields)

    def finish(self, job, status=AIJobStatus.SETTLED):
        with patch(COUNT) as count, self.captureOnCommitCallbacks(execute=True):
            holds().finish_job(job, status)
        return emitted(count)

    def test_a_settled_job_emits_both_once(self):
        self.assertEqual(
            self.finish(self.job()),
            [
                ("credit_estimate_gap", {"bucket": "1.2-2"}),
                ("credit_estimate_band_hit", {"hit": "false"}),
            ],
            "actual is consumed + unbilled = 160",
        )

    def test_within_the_band_is_a_hit(self):
        self.assertEqual(
            self.finish(self.job(consumed_credits=95, unbilled_credits=0)),
            [
                ("credit_estimate_gap", {"bucket": "0.8-1.2"}),
                ("credit_estimate_band_hit", {"hit": "true"}),
            ],
        )

    def test_the_band_edge_is_a_hit(self):
        metrics = self.finish(self.job(consumed_credits=150, unbilled_credits=0))
        self.assertIn(("credit_estimate_band_hit", {"hit": "true"}), metrics)

    def test_no_estimate_emits_nothing(self):
        self.assertEqual(self.finish(self.job(estimate_p50=None)), [])

    def test_a_failed_or_expired_job_emits_nothing(self):
        self.assertEqual(self.finish(self.job(), AIJobStatus.FAILED), [])
        queued = self.job(status=AIJobStatus.QUEUED_PENDING_CREDITS)
        self.assertEqual(self.finish(queued, AIJobStatus.EXPIRED), [])

    def test_a_second_finish_emits_nothing(self):
        job = self.job()
        self.finish(job)
        self.assertEqual(self.finish(job), [], "emitted twice for one job")

    def test_a_single_call_with_no_estimate_emits_nothing(self):
        job = make_job(self.wallet)
        holds().reserve(self.wallet, 50, job)
        with patch(COUNT) as count, self.captureOnCommitCallbacks(execute=True):
            holds().settle(job, 40, close_status=AIJobStatus.SETTLED)
        self.assertEqual(emitted(count), [])
