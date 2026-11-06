"""Epic B1a step 2: barrier races on real Postgres (plan 08 §6.3 R-1, S-8).

32 threads, not the plan's 200: each thread holds its own connection and
the run must stay well under max_connections alongside other sessions.
"""

import threading
from datetime import timedelta

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from billing.models import AIJob, CreditLedger, CreditUsageLog
from billing.testing.holds import fresh, funded_wallet, holds, make_item, make_job

THREADS = 32


def race(n, work):
    barrier = threading.Barrier(n)
    results: list = [None] * n

    def run(i):
        try:
            barrier.wait()
            results[i] = work(i)
        except Exception as exc:  # recorded, asserted by the test
            results[i] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


class ReserveRaceTests(TransactionTestCase):
    def test_concurrent_reserves_grant_exactly_what_fits(self):
        # R-1: a wallet that fits exactly 7 reserves of 10.
        wallet = funded_wallet("c.r1@example.com", 70)
        jobs = [make_job(wallet) for _ in range(THREADS)]
        results = race(THREADS, lambda i: holds().reserve(wallet, 10, jobs[i]))
        errors = [r for r in results if isinstance(r, Exception)]
        self.assertEqual(errors, [])
        self.assertEqual(results.count(True), 7)
        self.assertEqual(fresh(wallet).reserved_credits, 70)
        self.assertEqual(holds().check_invariants(wallet), [])


class StartClaimSweepRaceTests(TransactionTestCase):
    def test_a_start_claim_and_the_sweep_have_exactly_one_winner(self):
        # S-8, ten rounds.
        wallet = funded_wallet("c.s8@example.com", 1000)
        for round_no in range(10):
            job = make_job(wallet, "BATCH_GRADING", status="RUNNING")
            started = (
                timezone.now() - holds().item_sweep_window() - timedelta(minutes=5)
            )
            item = make_item(job, started_at=started)
            holds().reserve(wallet, 10, job, item=item)

            def work(i, item=item):
                if i == 0:
                    return ("claim", holds().claim_item_start(item))
                return ("sweep", holds().sweep_stale_holds()["items_swept"])

            results = dict(race(2, work))
            claimed, swept = results["claim"], results["sweep"]
            with self.subTest(round=round_no):
                self.assertNotEqual(bool(claimed), bool(swept), results)
                state = fresh(item).hold_state
                self.assertEqual(state, "HELD" if claimed else "RETURNED")
        self.assertEqual(holds().check_invariants(wallet), [])


class SweepOverlapTests(TransactionTestCase):
    def test_two_overlapping_sweeps_release_a_stale_hold_once(self):
        # SM condition 2026-11-06: a sweep that outlives its 3-min lock may
        # overlap the next one; each hold is still released exactly once.
        wallet = funded_wallet("c.overlap@example.com", 1000)
        for round_no in range(5):
            batch = make_job(wallet, "BATCH_GRADING", status="RUNNING")
            started = (
                timezone.now() - holds().item_sweep_window() - timedelta(minutes=5)
            )
            item = make_item(batch, started_at=started)
            holds().reserve(wallet, 30, batch, item=item)
            single = make_job(wallet, "SINGLE_CALL", status="RUNNING")
            holds().reserve(wallet, 20, single)
            AIJob.objects.filter(pk=single.pk).update(
                created_at=timezone.now()
                - holds().single_sweep_window()
                - timedelta(minutes=5)
            )

            results = race(2, lambda i: holds().sweep_stale_holds())
            errors = [r for r in results if isinstance(r, Exception)]
            with self.subTest(round=round_no):
                self.assertEqual(errors, [])
                self.assertEqual(sum(r["items_swept"] for r in results), 1)
                self.assertEqual(sum(r["jobs_released"] for r in results), 1)
                self.assertEqual(fresh(item).released_credits, 30)
                self.assertEqual(fresh(batch).failed_items, 1)
                self.assertEqual(fresh(single).released_credits, 20)
                self.assertEqual(fresh(wallet).reserved_credits, 0)
        self.assertFalse(CreditUsageLog.objects.exists())
        self.assertFalse(CreditLedger.objects.filter(user_id=wallet.user_id).exists())
        self.assertEqual(holds().check_invariants(wallet), [])
