"""Epic B1a step 3a: the four credit-hold audit actions (SM ruling
2026-11-06). All FINANCIAL, target AIJob, metadata ids and amounts only.
RELEASED only when an amount actually goes back."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from audit.enums import FINANCIAL_ACTIONS, AuditAction, RetentionClass
from audit.metadata import METADATA_ALLOWLIST
from audit.models import AuditEvent
from billing.testing.holds import fresh, funded_wallet, holds, make_item, make_job

ACTION_NAMES = (
    "CREDIT_HOLD_RESERVED",
    "CREDIT_HOLD_SETTLED",
    "CREDIT_HOLD_RELEASED",
    "CREDIT_HOLD_SWEPT",
)


def rows(name, job):
    return AuditEvent.objects.filter(action=name, target_id=job.id)


class VocabularyTests(TestCase):
    def test_the_four_actions_exist_are_financial_and_have_allowlists(self):
        for name in ACTION_NAMES:
            with self.subTest(action=name):
                action = getattr(AuditAction, name)
                self.assertIn(action, FINANCIAL_ACTIONS)
                self.assertIn(name, METADATA_ALLOWLIST)


class EmitTests(TestCase):
    def setUp(self):
        self.wallet = funded_wallet("a.emit@example.com", 500)

    def test_reserve_records_one_financial_reserved_row(self):
        job = make_job(self.wallet)
        holds().reserve(self.wallet, 30, job)
        row = rows("CREDIT_HOLD_RESERVED", job).get()
        self.assertEqual(row.retention_class, RetentionClass.FINANCIAL)
        self.assertEqual(row.target_type, "AIJob")
        self.assertEqual(row.metadata["credits"], 30)
        self.assertEqual(row.metadata["job_type"], "SINGLE_CALL")

    def test_settle_records_the_charge_and_the_actual(self):
        job = make_job(self.wallet)
        holds().reserve(self.wallet, 100, job)
        holds().settle(job, 60)
        row = rows("CREDIT_HOLD_SETTLED", job).get()
        self.assertEqual(row.metadata["credits"], 60)
        self.assertEqual(row.metadata["credits_actual"], 60)

    def test_released_is_recorded_only_when_something_goes_back(self):
        job = make_job(self.wallet, "BATCH_GRADING", status="RUNNING")
        item = make_item(job)
        holds().reserve(self.wallet, 40, job, item=item)
        holds().fail_item(job, item=item)
        self.assertEqual(rows("CREDIT_HOLD_RELEASED", job).count(), 1)
        self.assertEqual(
            rows("CREDIT_HOLD_RELEASED", job).get().metadata["credits"], 40
        )
        holds().finish_job(job, "FAILED")  # nothing left to return
        self.assertEqual(rows("CREDIT_HOLD_RELEASED", job).count(), 1)

    def test_the_sweep_records_one_swept_row_per_item(self):
        job = make_job(self.wallet, "BATCH_GRADING", status="RUNNING")
        started = timezone.now() - holds().item_sweep_window() - timedelta(minutes=1)
        item = make_item(job, started_at=started)
        holds().reserve(self.wallet, 25, job, item=item)
        holds().sweep_stale_holds()
        row = rows("CREDIT_HOLD_SWEPT", job).get()
        self.assertEqual(row.metadata["credits"], 25)
        self.assertEqual(row.metadata["item_id"], str(fresh(item).id))
