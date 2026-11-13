"""Epic B1a step 2: the settlement key (plan 08 §3.2 step 2, §6.3 D-1…D-4).

The key is (item_id, retry_count): the task row id whenever one exists, the
job id (retry 0) only for paths with no task row.
"""

import uuid

from django.test import TestCase

from billing.models import CreditUsageLog, SettlementRecord
from billing.testing.holds import fresh, funded_wallet, holds, make_item, make_job


class SettlementKeyTests(TestCase):
    def test_a_redelivered_settlement_settles_once(self):
        # D-1 (service level): the same item settled twice.
        wallet = funded_wallet("s.dup@example.com", 500)
        job = make_job(wallet, "BATCH_GRADING", total_items=1)
        item = make_item(job)
        holds().reserve(wallet, 50, job, item=item)
        first = holds().settle(job, 40, item=item)
        second_run = uuid.uuid4()
        second = holds().settle(job, 40, item=fresh(item), record_id=second_run)
        self.assertTrue(first.settled)
        self.assertFalse(second.settled)
        # The refused run's fixed usage id names nothing (NOTES §15i).
        self.assertFalse(SettlementRecord.objects.filter(pk=second_run).exists())
        self.assertFalse(
            CreditUsageLog.objects.filter(task_id=str(second_run)).exists()
        )
        self.assertEqual(SettlementRecord.objects.filter(item_id=item.id).count(), 1)
        self.assertEqual(fresh(job).consumed_credits, 40)
        self.assertEqual(fresh(wallet).total_remaining_credits(), 460)

    def test_a_retry_runs_under_a_new_single_item_job_with_its_own_key(self):
        # D-2 (SM ruling 2026-11-06, plan §3.4 E-2): the retry of a task row
        # runs under a NEW SINGLE_ITEM job, keyed (task.id, new retry_count);
        # the old job's counts and the task's hold columns stay untouched.
        wallet = funded_wallet("s.retry@example.com", 500)
        old = make_job(wallet, "BATCH_GRADING", total_items=1)
        item = make_item(old)
        holds().reserve(wallet, 50, old, item=item)
        holds().fail_item(old, item=item)
        before_old, before_item = fresh(old), fresh(item)
        type(item).objects.filter(pk=item.pk).update(retry_count=1)
        item = fresh(item)

        retry = holds().open_retry_job(old, item, 50)
        self.assertEqual(retry.job_type, "SINGLE_ITEM")
        self.assertEqual(retry.trace_id, old.trace_id)
        self.assertTrue(
            holds().settle(retry, 20, key=(item.id, item.retry_count)).settled
        )

        keys = set(
            SettlementRecord.objects.filter(item_id=item.id).values_list(
                "retry_count", flat=True
            )
        )
        self.assertEqual(keys, {1})
        record = SettlementRecord.objects.get(item_id=item.id)
        self.assertEqual(record.job_id, retry.pk)
        self.assertTrue(CreditUsageLog.objects.filter(task_id=str(record.id)).exists())
        after_old, after_item = fresh(old), fresh(item)
        self.assertEqual(
            (
                after_old.completed_items,
                after_old.failed_items,
                after_old.released_credits,
            ),
            (
                before_old.completed_items,
                before_old.failed_items,
                before_old.released_credits,
            ),
        )
        self.assertEqual(
            (
                after_item.hold_credits,
                after_item.hold_drawn,
                after_item.released_credits,
            ),
            (
                before_item.hold_credits,
                before_item.hold_drawn,
                before_item.released_credits,
            ),
        )
        self.assertEqual(fresh(retry).completed_items, 1)
        self.assertEqual(fresh(wallet).reserved_credits, 0)
        self.assertEqual(holds().check_invariants(wallet), [])

    def test_a_single_paper_settles_on_its_task_row_not_its_job(self):
        # D-3: an E-1 paper has a task row; the key is that row.
        wallet = funded_wallet("s.paper@example.com", 500)
        job = make_job(wallet, "SINGLE_ITEM", total_items=1)
        item = make_item(job)
        holds().reserve(wallet, 50, job, item=item)
        holds().settle(job, 30, item=item)
        record = SettlementRecord.objects.get(job=job)
        self.assertEqual((record.item_id, record.retry_count), (item.id, 0))
        self.assertNotEqual(record.item_id, job.id)

    def test_two_files_under_one_door_job_each_settle_once(self):
        # D-4
        wallet = funded_wallet("s.door@example.com", 500)
        job = make_job(wallet, "UPLOAD", total_items=2)
        file_a, file_b = make_item(job), make_item(job)
        holds().reserve(wallet, 50, job, item=file_a)
        holds().reserve(wallet, 50, job, item=file_b)
        self.assertTrue(holds().settle(job, 20, item=file_a).settled)
        self.assertTrue(holds().settle(job, 30, item=file_b).settled)
        self.assertFalse(holds().settle(job, 20, item=fresh(file_a)).settled)
        self.assertEqual(SettlementRecord.objects.filter(job=job).count(), 2)
        self.assertEqual(fresh(job).consumed_credits, 50)

    def test_a_job_with_no_task_row_settles_on_the_job_id(self):
        wallet = funded_wallet("s.single@example.com", 500)
        job = make_job(wallet, "SINGLE_CALL")
        holds().reserve(wallet, 50, job)
        holds().settle(job, 10)
        record = SettlementRecord.objects.get(job=job)
        self.assertEqual((record.item_id, record.retry_count), (job.id, 0))
