"""Epic B1a step 2: the stale-hold sweep (plan 08 §3.2, §6.3 S-1, S-3, S-5).

Only items that have STARTED are swept, and only once started_at is older
than visibility_timeout + GRADING_TASK_TIME_LIMIT_SECONDS + a margin with no
settlement. A job with no task row (SINGLE_CALL) is released after the time
limit plus the margin.
"""

import importlib
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from billing.models import AIJob, BillingSettings, CreditLedger, CreditUsageLog
from billing.testing.holds import fresh, funded_wallet, holds, make_item, make_job


def tasks():
    return importlib.import_module("billing.tasks")


def long_ago():
    return timezone.now() - timedelta(days=3)


class ItemSweepTests(TestCase):
    def setUp(self):
        self.wallet = funded_wallet("sw.items@example.com", 500)
        self.job = make_job(
            self.wallet, "BATCH_GRADING", total_items=2, status="RUNNING"
        )

    def held_item(self, **over):
        item = make_item(self.job, **over)
        holds().reserve(self.wallet, 80, self.job, item=item)
        return fresh(item)

    def test_an_unstarted_item_keeps_its_hold_however_old(self):
        # S-1 / S-5 (sweep half): a broker backlog is normal.
        item = self.held_item()
        type(item).objects.filter(pk=item.pk).update(created_at=long_ago())
        AIJob.objects.filter(pk=self.job.pk).update(updated_at=long_ago())
        result = holds().sweep_stale_holds()
        self.assertEqual(result["items_swept"], 0)
        self.assertEqual(fresh(item).hold_state, "HELD")
        self.assertEqual(fresh(self.wallet).reserved_credits, 80)

    def test_a_started_item_inside_the_window_keeps_its_hold(self):
        started = timezone.now() - holds().item_sweep_window() + timedelta(minutes=1)
        item = self.held_item(started_at=started)
        self.assertEqual(holds().sweep_stale_holds()["items_swept"], 0)
        self.assertEqual(fresh(item).hold_state, "HELD")

    def test_a_started_item_past_the_window_is_failed_counted_and_released(self):
        # S-3
        started = timezone.now() - holds().item_sweep_window() - timedelta(minutes=1)
        item = self.held_item(started_at=started)
        result = holds().sweep_stale_holds()
        self.assertEqual(result["items_swept"], 1)
        item, job = fresh(item), fresh(self.job)
        self.assertEqual(item.hold_state, "RETURNED")
        self.assertEqual(item.released_credits, 80)
        self.assertEqual(item.status, "FAILURE")
        self.assertEqual(item.reason_code, "PROVIDER_FAILURE")
        self.assertEqual(job.failed_items, 1)
        self.assertEqual(job.released_credits, 80)
        self.assertEqual(fresh(self.wallet).reserved_credits, 0)
        self.assertEqual(holds().check_invariants(self.wallet), [])
        # SM ruling (7): "No credits were charged." must be true.
        self.assertFalse(CreditUsageLog.objects.exists())
        self.assertFalse(
            CreditLedger.objects.filter(user_id=self.wallet.user_id).exists()
        )

    def test_the_sweep_margin_is_an_editable_setting(self):
        # SM ruling (6): the margin lives in BillingSettings (default 15 min).
        settings_row = BillingSettings.load()
        self.assertEqual(settings_row.hold_sweep_margin_minutes, 15)
        before = holds().item_sweep_window()
        settings_row.hold_sweep_margin_minutes = 45
        settings_row.save()
        self.assertEqual(holds().item_sweep_window() - before, timedelta(minutes=30))

    def test_a_settled_item_is_never_swept(self):
        started = timezone.now() - holds().item_sweep_window() - timedelta(minutes=1)
        item = self.held_item(started_at=started)
        holds().settle(self.job, 30, item=item)
        self.assertEqual(holds().sweep_stale_holds()["items_swept"], 0)
        self.assertEqual(fresh(self.job).failed_items, 0)


class SingleJobSweepTests(TestCase):
    def setUp(self):
        self.wallet = funded_wallet("sw.single@example.com", 500)

    def test_a_single_call_job_past_its_window_is_released(self):
        job = make_job(self.wallet, "SINGLE_CALL", status="RUNNING")
        holds().reserve(self.wallet, 50, job)
        AIJob.objects.filter(pk=job.pk).update(
            created_at=timezone.now()
            - holds().single_sweep_window()
            - timedelta(minutes=1)
        )
        result = holds().sweep_stale_holds()
        self.assertEqual(result["jobs_released"], 1)
        self.assertEqual(fresh(job).status, "FAILED")
        self.assertEqual(fresh(self.wallet).reserved_credits, 0)

    def test_a_single_call_job_inside_its_window_is_kept(self):
        job = make_job(self.wallet, "SINGLE_CALL", status="RUNNING")
        holds().reserve(self.wallet, 50, job)
        self.assertEqual(holds().sweep_stale_holds()["jobs_released"], 0)
        self.assertEqual(fresh(job).status, "RUNNING")
        self.assertEqual(fresh(self.wallet).reserved_credits, 50)


class SweepTaskTests(TestCase):
    def stale_item(self):
        wallet = funded_wallet("sw.task@example.com", 500)
        job = make_job(wallet, "BATCH_GRADING", status="RUNNING")
        started = timezone.now() - holds().item_sweep_window() - timedelta(minutes=1)
        item = make_item(job, started_at=started)
        holds().reserve(wallet, 40, job, item=item)
        return item

    @override_settings(BILLING_HOLDS_ENABLED=False)
    def test_the_sweep_task_does_nothing_with_the_flag_off(self):
        sweep_stale_credit_holds = tasks().sweep_stale_credit_holds

        item = self.stale_item()
        sweep_stale_credit_holds.apply()
        self.assertEqual(fresh(item).hold_state, "HELD")

    @override_settings(BILLING_HOLDS_ENABLED=True)
    def test_the_sweep_task_sweeps_with_the_flag_on(self):
        sweep_stale_credit_holds = tasks().sweep_stale_credit_holds

        item = self.stale_item()
        sweep_stale_credit_holds.apply()
        self.assertEqual(fresh(item).hold_state, "RETURNED")

    def test_the_invariant_task_reports_drift_as_a_ledger_anomaly(self):
        check_credit_hold_invariants = tasks().check_credit_hold_invariants

        wallet = funded_wallet("sw.drift@example.com", 100)
        holds().reserve(wallet, 30, make_job(wallet))
        type(wallet).objects.filter(pk=wallet.pk).update(reserved_credits=31)
        with patch("billing.tasks.audit_metrics.count") as count:
            check_credit_hold_invariants.apply()
        count.assert_any_call("credit_ledger_anomaly", tags={"kind": "hold_drift"})

    def test_the_invariant_task_is_silent_when_nothing_is_held(self):
        # SM ruling: flag off, no holds - no log, no metric.
        check_credit_hold_invariants = tasks().check_credit_hold_invariants
        funded_wallet("sw.quiet@example.com", 100)
        with patch("billing.tasks.audit_metrics.count") as count:
            check_credit_hold_invariants.apply()
        count.assert_not_called()
