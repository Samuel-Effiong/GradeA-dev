"""Epic B1a step 2: the settlement key (plan 08 §3.2 step 2, §6.3 D-1…D-4).

The key is (item_id, retry_count): the task row id whenever one exists, the
job id (retry 0) only for paths with no task row.
"""

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
        second = holds().settle(job, 40, item=fresh(item))
        self.assertTrue(first.settled)
        self.assertFalse(second.settled)
        self.assertEqual(SettlementRecord.objects.filter(item_id=item.id).count(), 1)
        self.assertEqual(fresh(job).consumed_credits, 40)
        self.assertEqual(fresh(wallet).total_remaining_credits(), 460)

    def test_a_retry_in_place_settles_again_under_its_new_key(self):
        # D-2: the same task row, retry_count moved on.
        wallet = funded_wallet("s.retry@example.com", 500)
        job = make_job(wallet, "BATCH_GRADING", total_items=1)
        item = make_item(job)
        holds().reserve(wallet, 50, job, item=item)
        holds().settle(job, 30, item=item)
        type(item).objects.filter(pk=item.pk).update(retry_count=1)
        item = fresh(item)
        holds().reserve(wallet, 50, job, item=item)
        self.assertTrue(holds().settle(job, 20, item=item).settled)
        keys = set(
            SettlementRecord.objects.filter(item_id=item.id).values_list(
                "retry_count", flat=True
            )
        )
        self.assertEqual(keys, {0, 1})
        records = SettlementRecord.objects.filter(item_id=item.id)
        for record in records:
            self.assertTrue(
                CreditUsageLog.objects.filter(task_id=str(record.id)).exists()
            )

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
