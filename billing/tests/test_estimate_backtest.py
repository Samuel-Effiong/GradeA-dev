"""B1b-4 (NOTES §4): the backtest, the monthly drift check and its beat
entry (plan §3.6, §9 item 6(b)/(c); SM rulings Q1, Q4)."""

import importlib
from io import StringIO
from typing import Any
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from ai_processor.models import AICallRecord
from AutoGrader.beat_locks import declared_lock
from billing.models import BillingSettings
from billing.testing.holds import ai_ready_teacher
from billing.tests.testing_estimates import (
    grading_session,
    make_assignment,
    paper,
    papers,
)
from users.models import CustomUser, UserTypes

# importlib (the B1a precedent, NOTES §21 step 3): mypy stays quiet about
# names that do not exist until the product commit; the import still fails
# at the red.
estimates = importlib.import_module("billing.estimates")
EstimateDriftRun = importlib.import_module("billing.models").EstimateDriftRun
check_credit_estimate_drift = importlib.import_module(
    "billing.tasks"
).check_credit_estimate_drift


def report(coverage, cells=()):
    return {
        "batches": 100,
        "coverage": coverage,
        "cells": list(cells),
        "suggested_factor": 2.5,
    }


def cell(coverage, batches, size_band="1"):
    return {
        "bucket": "1-5",
        "has_images": False,
        "size_band": size_band,
        "batches": batches,
        "coverage": coverage,
    }


class SizeBandTests(TestCase):
    def test_the_size_bands(self):
        for n, band in (
            (1, "1"),
            (2, "2-9"),
            (9, "2-9"),
            (10, "10-29"),
            (29, "10-29"),
            (30, "30+"),
        ):
            with self.subTest(n=n):
                self.assertEqual(estimates.size_band(n), band)


class PastBatchTests(TestCase):
    """Q1: a past batch is the batch-grading task rows sharing one
    batch_session; a paper graded on its own is a batch of 1."""

    def setUp(self):
        self.teacher = ai_ready_teacher("est.bt@example.com", 10_000)
        self.assignment = make_assignment(self.teacher, "Backtest")

    def test_a_session_is_one_batch_and_a_lone_paper_is_a_batch_of_one(self):
        session = grading_session(self.teacher, self.assignment)
        papers(self.teacher, self.assignment, [100, 200, 300], batch=session)
        paper(self.teacher, self.assignment, 50)
        batches = sorted((b["n"], b["actual"]) for b in estimates.past_batches(days=90))
        self.assertEqual(batches, [(1, 50), (3, 600)])

    def test_a_failed_paper_is_left_out_of_its_batch(self):
        session = grading_session(self.teacher, self.assignment)
        papers(self.teacher, self.assignment, [100, 200], batch=session)
        paper(self.teacher, self.assignment, 900, batch=session, status="FAILURE")
        self.assertEqual(
            [(b["n"], b["actual"]) for b in estimates.past_batches(days=90)],
            [(2, 300)],
        )


class BacktestTests(TestCase):
    def setUp(self):
        self.teacher = ai_ready_teacher("est.bt2@example.com", 10_000)
        self.assignment = make_assignment(self.teacher, "Replay")

    def test_a_batch_is_replayed_against_the_history_before_it(self):
        papers(self.teacher, self.assignment, [100] * 10, days_ago=30)
        session = grading_session(self.teacher, self.assignment)
        papers(self.teacher, self.assignment, [100, 100], batch=session, days_ago=5)
        over = grading_session(self.teacher, self.assignment)
        papers(self.teacher, self.assignment, [900, 900], batch=over, days_ago=4)
        result = estimates.backtest(days=90)
        # The 10 history papers are batches of 1 with no history before
        # them (rough); the two 2-paper batches are estimated from tier 1.
        two = [c for c in result["cells"] if c["size_band"] == "2-9"]
        self.assertEqual(len(two), 1)
        self.assertEqual(two[0]["batches"], 2)
        self.assertEqual(two[0]["coverage"], 0.5)

    def test_the_command_prints_the_report(self):
        papers(self.teacher, self.assignment, [100] * 3)
        out = StringIO()
        call_command("backtest_credit_estimates", stdout=out)
        text = out.getvalue()
        self.assertIn("batches=3", text)
        self.assertIn("coverage=", text)

    def test_the_cold_start_ratio_is_the_median_actual_over_estimate(self):
        for cost, estimate in ((200, 100), (300, 100), (900, 100)):
            task = paper(self.teacher, self.assignment, cost)
            AICallRecord.objects.filter(processing_task_id=task.pk).update(
                estimate=estimate
            )
        self.assertEqual(estimates.cold_start_ratio(days=90), 3.0)
        out = StringIO()
        call_command("backtest_credit_estimates", "--cold-start", stdout=out)
        self.assertIn("cold_start_ratio=3.0", out.getvalue())


class DriftDecisionTests(TestCase):
    """Below 85% overall, or below 80% in a cell with at least 30 batches."""

    def test_eighty_five_percent_overall_is_not_drift(self):
        self.assertEqual(estimates.drift_reasons(report(0.85)), [])

    def test_below_eighty_five_percent_overall_is_drift(self):
        self.assertEqual(len(estimates.drift_reasons(report(0.849))), 1)

    def test_a_cell_at_eighty_percent_is_not_drift(self):
        self.assertEqual(estimates.drift_reasons(report(0.9, [cell(0.80, 30)])), [])

    def test_a_cell_below_eighty_percent_with_thirty_batches_is_drift(self):
        self.assertEqual(
            len(estimates.drift_reasons(report(0.9, [cell(0.799, 30)]))), 1
        )

    def test_a_cell_below_eighty_percent_with_twenty_nine_batches_is_not(self):
        self.assertEqual(estimates.drift_reasons(report(0.9, [cell(0.1, 29)])), [])

    def test_no_batches_is_not_drift(self):
        self.assertEqual(estimates.drift_reasons(report(None)), [])


class DriftTaskTests(TestCase):
    def setUp(self):
        self.admin = CustomUser.objects.create_superuser(
            email="est.drift.admin@example.com",
            password="pw-12345678",  # pragma: allowlist secret  # nosec B106
            first_name="Drift",
            last_name="Admin",
        )
        self.admin.user_type = UserTypes.SUPER_ADMIN
        self.admin.save()

    def run_task(self, result):
        with patch.object(estimates, "backtest", return_value=result), patch(
            "AutoGrader.tasks.send_email_task.delay"
        ) as send, patch.object(estimates, "emit_drift_metric") as metric:
            check_credit_estimate_drift.run()
        return send, metric

    def test_every_run_writes_a_summary_row(self):
        self.run_task(report(0.95, [cell(0.9, 40)]))
        row = EstimateDriftRun.objects.get()
        self.assertEqual(row.coverage, 0.95)
        self.assertEqual(row.batches, 100)
        self.assertEqual(row.cells, [cell(0.9, 40)])
        self.assertEqual(row.suggested_factor, 2.5)
        self.assertEqual(
            row.current_factor, BillingSettings.load().cold_start_cost_factor
        )
        self.assertFalse(row.drifted)

    def test_drift_emits_the_metric(self):
        send, metric = self.run_task(report(0.5))
        metric.assert_called_once()
        self.assertTrue(EstimateDriftRun.objects.get().drifted)

    def test_no_drift_emits_nothing(self):
        send, metric = self.run_task(report(0.95))
        metric.assert_not_called()
        send.assert_not_called()

    def test_drift_sends_no_email_while_the_text_is_not_approved(self):
        self.assertFalse(estimates.DRIFT_EMAIL_APPROVED)
        send, _ = self.run_task(report(0.5))
        send.assert_not_called()

    def test_once_approved_drift_emails_the_super_admin(self):
        with patch.object(estimates, "DRIFT_EMAIL_APPROVED", True):
            send, _ = self.run_task(report(0.5))
        send.assert_called_once()
        self.assertIn(self.admin.email, str(send.call_args))

    def test_the_email_is_approved_text_17_verbatim(self):
        result = report(
            0.5,
            [cell(0.75, 40, size_band="2-9"), cell(0.95, 40, size_band="30+")],
        )
        result["suggested_factor"] = 2.4
        with patch.object(estimates, "DRIFT_EMAIL_APPROVED", True):
            send, _ = self.run_task(result)
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["subject"], "Credit estimates need a look")
        self.assertEqual(
            kwargs["message"],
            "This month's check of credit estimates found that fewer batches "
            "than expected stayed within the high estimate.\n\n"
            "Overall: 50% of 100 batches stayed within the high estimate. "
            "The target is at least 85%.\n\n"
            "Groups below 80% (only groups with at least 30 batches are "
            "checked):\n"
            "1-5 questions, without images, batches of 2 to 9: 75% of 40 "
            "batches\n\n"
            "Suggested cold-start factor: 2.4 (now 3.0).\n\n"
            "Nothing has been changed. Estimates only change when someone "
            "changes the factor.",
        )

    def test_the_task_runs_one_at_a_time(self):
        self.assertIsNotNone(declared_lock(check_credit_estimate_drift))

    def test_the_task_is_scheduled_monthly(self):
        entry = settings.CELERY_BEAT_SCHEDULE["check-credit-estimate-drift-monthly"]
        schedule: Any = entry["schedule"]
        self.assertEqual(entry["task"], "billing.tasks.check_credit_estimate_drift")
        self.assertEqual(schedule.day_of_month, {1})

    def test_it_never_changes_the_factor(self):
        before = BillingSettings.load().cold_start_cost_factor
        self.run_task(report(0.5))
        self.assertEqual(BillingSettings.load().cold_start_cost_factor, before)
