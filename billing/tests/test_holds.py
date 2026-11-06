"""Epic B1a step 2: the hold service, billing/holds.py (plan 08 §3.2, §3.4).

Accounting rules under test (plan §3.2):
  item_hold_left = hold_credits - hold_drawn - released_credits
  settle: chargeable = min(actual, item_hold_left + free); drawn =
  min(item_hold_left, chargeable); the leftover is returned in the same
  transaction. A job with no task row is its own single item.
"""

from django.test import TestCase

from billing.errors import InsufficientCreditsError, is_individual_teacher
from billing.models import AIJob, CreditUsageLog, SettlementRecord
from billing.testing.holds import (
    fresh,
    funded_wallet,
    holds,
    make_item,
    make_job,
    remaining,
)


class SpendableAndReserveTests(TestCase):
    def test_spendable_is_total_minus_reserved(self):
        wallet = funded_wallet("h.spend@example.com", 100)
        self.assertEqual(holds().spendable_credits(wallet), 100)
        self.assertTrue(holds().reserve(wallet, 30, make_job(wallet)))
        self.assertEqual(holds().spendable_credits(fresh(wallet)), 70)

    def test_reserve_records_the_hold_on_wallet_and_job(self):
        wallet = funded_wallet("h.res@example.com", 100)
        job = make_job(wallet)
        self.assertTrue(holds().reserve(wallet, 60, job))
        job = fresh(job)
        self.assertEqual(fresh(wallet).reserved_credits, 60)
        self.assertEqual(job.reserved_credits, 60)
        self.assertIsNotNone(job.reservation_claimed_at)

    def test_reserve_beyond_spendable_is_refused_and_changes_nothing(self):
        wallet = funded_wallet("h.refuse@example.com", 100)
        first, second = make_job(wallet), make_job(wallet)
        self.assertTrue(holds().reserve(wallet, 60, first))
        self.assertFalse(holds().reserve(wallet, 50, second))
        self.assertEqual(fresh(wallet).reserved_credits, 60)
        self.assertEqual(fresh(second).reserved_credits, 0)

    def test_reserve_on_an_item_extends_that_items_hold(self):
        wallet = funded_wallet("h.item@example.com", 100)
        job = make_job(wallet, "BATCH_GRADING", total_items=2)
        item = make_item(job)
        self.assertTrue(holds().reserve(wallet, 40, job, item=item))
        item = fresh(item)
        self.assertEqual(item.hold_credits, 40)
        self.assertEqual(item.hold_state, "HELD")
        self.assertEqual(fresh(job).reserved_credits, 40)

    def test_reserve_refuses_a_non_positive_amount(self):
        wallet = funded_wallet("h.zero@example.com", 100)
        with self.assertRaises(ValueError):
            holds().reserve(wallet, 0, make_job(wallet))

    def test_create_job_starts_estimated_with_a_trace(self):
        wallet = funded_wallet("h.create@example.com", 100)
        job = holds().create_job(
            wallet=wallet, requested_by=wallet.user, job_type="SINGLE_CALL"
        )
        job = fresh(job)
        self.assertEqual(job.status, "ESTIMATED")
        self.assertEqual(job.total_items, 1)
        self.assertIsNotNone(job.trace_id)


class SettleAndFailTests(TestCase):
    def test_settle_charges_from_the_hold_and_returns_the_leftover(self):
        wallet = funded_wallet("h.settle@example.com", 1000)
        job = make_job(wallet)
        holds().reserve(wallet, 100, job)
        outcome = holds().settle(job, 60)
        self.assertTrue(outcome.settled)
        self.assertEqual(
            (outcome.chargeable, outcome.drawn, outcome.leftover), (60, 60, 40)
        )
        job = fresh(job)
        self.assertEqual(fresh(wallet).reserved_credits, 0)
        self.assertEqual(remaining(wallet), 940)
        self.assertEqual(
            (job.consumed_credits, job.hold_drawn, job.released_credits),
            (60, 60, 40),
        )
        self.assertEqual(job.completed_items, 1)
        record = SettlementRecord.objects.get(job=job)
        self.assertTrue(CreditUsageLog.objects.filter(task_id=str(record.id)).exists())

    def test_settle_draws_only_the_items_own_hold(self):
        # Wallet 100, two items holding 50 each: no free balance. Item A's
        # 70 can only take its own 50; item B keeps its whole hold (U-3).
        wallet = funded_wallet("h.own@example.com", 100)
        job = make_job(wallet, "BATCH_GRADING", total_items=2)
        item_a, item_b = make_item(job), make_item(job)
        holds().reserve(wallet, 50, job, item=item_a)
        holds().reserve(wallet, 50, job, item=item_b)
        outcome = holds().settle(job, 70, item=item_a)
        self.assertEqual((outcome.chargeable, outcome.unbilled), (50, 20))
        self.assertEqual(holds().item_hold_left(job, item=fresh(item_b)), 50)
        self.assertEqual(fresh(wallet).reserved_credits, 50)

    def test_fail_item_returns_the_whole_hold_and_charges_nothing(self):
        wallet = funded_wallet("h.fail@example.com", 100)
        job = make_job(wallet, "BATCH_GRADING", total_items=1)
        item = make_item(job)
        holds().reserve(wallet, 80, job, item=item)
        self.assertEqual(holds().fail_item(job, item=item), 80)
        item, job = fresh(item), fresh(job)
        self.assertEqual(item.released_credits, 80)
        self.assertEqual(item.hold_state, "RETURNED")
        self.assertEqual(job.failed_items, 1)
        self.assertEqual(fresh(wallet).reserved_credits, 0)
        self.assertEqual(remaining(wallet), 100)
        self.assertFalse(CreditUsageLog.objects.exists())

    def test_finish_job_releases_the_remainder_exactly_once(self):
        wallet = funded_wallet("h.finish@example.com", 100)
        job = make_job(wallet, status="RESERVED")
        holds().reserve(wallet, 100, job)
        self.assertEqual(holds().finish_job(job, "CANCELLED"), 100)
        self.assertIsNone(holds().finish_job(job, "CANCELLED"))
        self.assertEqual(fresh(wallet).reserved_credits, 0)
        self.assertEqual(fresh(job).status, "CANCELLED")
        self.assertEqual(fresh(job).released_credits, 100)

    def test_finish_job_refuses_a_job_already_terminal(self):
        wallet = funded_wallet("h.term@example.com", 100)
        job = make_job(wallet, status="SETTLED")
        self.assertIsNone(holds().finish_job(job, "FAILED"))
        self.assertEqual(fresh(job).status, "SETTLED")


class PreCallCheckTests(TestCase):
    def setUp(self):
        self.wallet = funded_wallet("h.precall@example.com", 60)
        self.job = make_job(self.wallet, "BATCH_GRADING", total_items=1)
        self.item = make_item(self.job)
        holds().reserve(self.wallet, 50, self.job, item=self.item)

    def test_a_call_within_the_hold_reserves_nothing(self):
        extra = holds().ensure_call_fits(self.job, 30, item=self.item, tally=0)
        self.assertEqual(extra, 0)
        self.assertEqual(fresh(self.wallet).reserved_credits, 50)

    def test_a_call_beyond_the_hold_reserves_the_shortfall(self):
        # Hold 50, tally 40: 10 left; a 15 call reserves the 5 shortfall.
        extra = holds().ensure_call_fits(self.job, 15, item=self.item, tally=40)
        self.assertEqual(extra, 5)
        self.assertEqual(fresh(self.item).hold_credits, 55)
        self.assertEqual(fresh(self.wallet).reserved_credits, 55)

    def test_a_call_that_cannot_be_covered_is_refused(self):
        with self.assertRaises(InsufficientCreditsError) as caught:
            holds().ensure_call_fits(self.job, 30, item=self.item, tally=50)
        self.assertEqual(
            caught.exception.individual, is_individual_teacher(self.wallet.user)
        )
        self.assertEqual(fresh(self.wallet).reserved_credits, 50)

    def test_the_check_counts_what_the_unit_already_used(self):
        # Hold 50 fully used by the tally; spendable is 10; a 20 call fails.
        with self.assertRaises(InsufficientCreditsError):
            holds().ensure_call_fits(self.job, 20, item=self.item, tally=50)


class StartClaimTests(TestCase):
    def test_the_claim_starts_a_held_item(self):
        wallet = funded_wallet("h.claim@example.com", 100)
        job = make_job(wallet, "BATCH_GRADING")
        item = make_item(job)
        holds().reserve(wallet, 10, job, item=item)
        self.assertTrue(holds().claim_item_start(item))
        self.assertIsNotNone(fresh(item).started_at)

    def test_the_claim_is_refused_once_the_hold_is_returned(self):
        wallet = funded_wallet("h.claim2@example.com", 100)
        job = make_job(wallet, "BATCH_GRADING")
        item = make_item(job)
        holds().reserve(wallet, 10, job, item=item)
        holds().fail_item(job, item=item)
        self.assertFalse(holds().claim_item_start(item))
        self.assertIsNone(fresh(item).started_at)


class LateDoorTests(TestCase):
    def setUp(self):
        self.wallet = funded_wallet("h.door@example.com", 100)
        self.old = make_job(self.wallet, "UPLOAD", total_items=1)
        self.item = make_item(self.old)
        holds().reserve(self.wallet, 30, self.old, item=self.item)

    def test_already_settled_reads_the_items_settlement_key(self):
        self.assertFalse(holds().already_settled(self.item))
        holds().settle(self.old, 10, item=self.item)
        self.assertTrue(holds().already_settled(fresh(self.item)))
        self.item.retry_count += 1
        self.assertFalse(holds().already_settled(self.item))

    def test_a_late_door_item_runs_under_a_new_upload_job(self):
        holds().fail_item(self.old, item=self.item)
        before = fresh(self.old)
        new = holds().open_late_door_job(self.old, fresh(self.item), 40)
        self.assertNotEqual(new.pk, self.old.pk)
        self.assertEqual((new.job_type, fresh(new).reserved_credits), ("UPLOAD", 40))
        holds().settle(new, 25, key=(self.item.id, self.item.retry_count))
        old = fresh(self.old)
        self.assertEqual(
            (old.reserved_credits, old.hold_drawn, old.released_credits),
            (before.reserved_credits, before.hold_drawn, before.released_credits),
        )
        self.assertEqual(fresh(self.item).job_id, self.old.pk)
        self.assertEqual(fresh(new).consumed_credits, 25)
        self.assertEqual(fresh(self.wallet).reserved_credits, 0)

    def test_a_late_door_item_is_refused_when_its_new_job_cannot_reserve(self):
        holds().fail_item(self.old, item=self.item)
        holds().reserve(self.wallet, 100, make_job(self.wallet))  # spend it all
        with self.assertRaises(InsufficientCreditsError):
            holds().open_late_door_job(self.old, fresh(self.item), 40)
        self.assertFalse(
            AIJob.objects.filter(job_type="UPLOAD", reserved_credits__gt=0)
            .exclude(pk=self.old.pk)
            .exists()
        )
        self.assertEqual(fresh(self.wallet).reserved_credits, 100)


class InvariantTests(TestCase):
    def test_invariants_hold_across_reserve_settle_fail_and_finish(self):
        wallet = funded_wallet("h.inv@example.com", 500)
        batch = make_job(wallet, "BATCH_GRADING", total_items=3, status="RUNNING")
        items = [make_item(batch) for _ in range(3)]
        for item in items:
            holds().reserve(wallet, 50, batch, item=item)
        holds().settle(batch, 40, item=items[0])
        holds().settle(batch, 70, item=items[1])
        holds().fail_item(batch, item=items[2])
        single = make_job(wallet, status="RESERVED")
        holds().reserve(wallet, 20, single)
        self.assertEqual(holds().check_invariants(wallet), [])
        holds().finish_job(batch, "SETTLED")
        holds().settle(single, 5)
        holds().finish_job(single, "SETTLED")
        self.assertEqual(holds().check_invariants(wallet), [])
        self.assertEqual(fresh(wallet).reserved_credits, 0)

    def test_wallet_drift_is_reported(self):
        wallet = funded_wallet("h.drift@example.com", 100)
        holds().reserve(wallet, 30, make_job(wallet))
        type(wallet).objects.filter(pk=wallet.pk).update(reserved_credits=35)
        problems = holds().check_invariants(wallet)
        self.assertTrue(any("hold_drift" in p for p in problems), problems)

    def test_an_item_sum_mismatch_is_reported(self):
        wallet = funded_wallet("h.itemdrift@example.com", 100)
        job = make_job(wallet, "BATCH_GRADING", total_items=1)
        item = make_item(job)
        holds().reserve(wallet, 30, job, item=item)
        type(item).objects.filter(pk=item.pk).update(hold_credits=25)
        problems = holds().check_invariants(wallet)
        self.assertTrue(any("item_sum" in p for p in problems), problems)
