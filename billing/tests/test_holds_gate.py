"""Epic B1a step 9: gate tests on real Postgres (plan 08 §6.3 R-4, U-2, the
provider-timeout failure row; NOTES §21b).

R-4a characterizes the design §0.3 rejected: a conditional UPDATE whose
WHERE sums the buckets, with no wallet lock. R-4b races the production
reserve() the same way. U-2 races two metered units on one free balance.
T-1 times the provider out inside a unit of a two-item batch.
"""

import threading
import time
from unittest.mock import patch

import httpx
import openai
from django.db import connection, transaction
from django.test import TransactionTestCase, override_settings

from ai_processor.services import AIProcessor
from billing.errors import InsufficientCreditsError
from billing.models import AIJob, CreditBucket, CreditUsageLog, CreditWallet
from billing.testing.holds import (
    ai_ready_teacher,
    ai_response,
    call_ai,
    estimate_for,
    fresh,
    funded_wallet,
    holds,
    make_item,
    make_job,
    remaining,
)

PROVIDER = "_AIProcessor__ai_model"
#: How long the consumer keeps the wallet row locked after consuming, so the
#: other thread has certainly read the buckets before it commits.
HOLD_LOCK_SECONDS = 1.0


def in_thread(work, results, key):
    def run():
        try:
            results[key] = work()
        except Exception as exc:  # recorded, asserted by the test
            results[key] = exc
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    return thread


class ConsumeRaceBase(TransactionTestCase):
    """Thread A consumes 60 of 100 and keeps the wallet row locked for a
    second; thread B runs `contender` once A has consumed."""

    def race_against_consume(self, wallet, contender):
        consumed = threading.Event()
        results: dict = {}

        def consumer():
            with transaction.atomic():
                CreditWallet.objects.get(pk=wallet.pk).consume_credits(60)
                consumed.set()
                time.sleep(HOLD_LOCK_SECONDS)
            return "consumed"

        def second():
            if not consumed.wait(30):
                raise AssertionError("the consumer never consumed")
            return contender()

        threads = [
            in_thread(consumer, results, "a"),
            in_thread(second, results, "b"),
        ]
        for thread in threads:
            thread.join(60)
        self.assertEqual(results.get("a"), "consumed", results)
        return results.get("b")


class EpqReserveRaceTests(ConsumeRaceBase):
    def test_r4a_a_bucket_subquery_update_without_the_lock_over_reserves(self):
        # R-4a: characterization (§0.3). The EPQ re-check reads the new
        # wallet row but the subquery keeps the statement's snapshot.
        wallet = funded_wallet("g.r4a@example.com", 100)
        wallet_table = CreditWallet._meta.db_table
        bucket_table = CreditBucket._meta.db_table
        sql = (
            f"UPDATE {wallet_table} SET reserved_credits = reserved_credits + 70 "
            f"WHERE id = %s AND reserved_credits + 70 <= ("
            f"SELECT sum(total_credits - used_credits) FROM {bucket_table} "
            f"WHERE wallet_id = %s)"
        )

        def unlocked_update():
            with connection.cursor() as cursor:
                cursor.execute(sql, [wallet.pk, wallet.pk])
                return cursor.rowcount

        rowcount = self.race_against_consume(wallet, unlocked_update)
        self.assertEqual(rowcount, 1)
        self.assertEqual(fresh(wallet).reserved_credits, 70)
        self.assertEqual(remaining(wallet), 40)

    def test_r4b_the_production_reserve_is_refused_after_the_consume(self):
        # R-4b: reserve() takes lock_wallet_first, so it reads the buckets
        # only after the consume commits. M-B2's kill test.
        wallet = funded_wallet("g.r4b@example.com", 100)
        job = make_job(wallet)
        granted = self.race_against_consume(
            wallet, lambda: holds().reserve(wallet, 70, job)
        )
        self.assertFalse(granted)
        self.assertEqual(fresh(wallet).reserved_credits, 0)
        self.assertEqual(remaining(wallet), 40)
        self.assertEqual(holds().check_invariants(wallet), [])


@override_settings(BILLING_HOLDS_ENABLED=True)
class TwoUnitsOneBalanceTests(TransactionTestCase):
    def test_u2_two_units_on_one_free_balance_one_is_refused(self):
        # U-2: free balance 1.5E; each unit's call needs E beyond its (empty)
        # hold. Both pass a barrier, make their pre-call check, and wait at
        # a second barrier before either settles.
        estimate = estimate_for()
        teacher = ai_ready_teacher("g.u2@example.com", estimate + estimate // 2)
        wallet = CreditWallet.objects.get(user=teacher)
        start, checked = threading.Barrier(2), threading.Barrier(2)
        results: dict = {}

        def unit():
            start.wait(30)
            with holds().metered_unit(reason="u2"):
                try:
                    call_ai(teacher)
                finally:
                    checked.wait(30)
            return "settled"

        with patch.object(
            AIProcessor, PROVIDER, return_value=ai_response(estimate)
        ) as provider:
            threads = [in_thread(unit, results, i) for i in range(2)]
            for thread in threads:
                thread.join(120)

        refused = [
            r for r in results.values() if isinstance(r, InsufficientCreditsError)
        ]
        other = [
            r
            for r in results.values()
            if isinstance(r, Exception) and not isinstance(r, InsufficientCreditsError)
        ]
        self.assertEqual(len(refused), 1, results)
        self.assertEqual(other, [])
        self.assertEqual(provider.call_count, 1)
        charged = sum(CreditUsageLog.objects.values_list("amount", flat=True))
        self.assertEqual(charged, estimate)
        self.assertLessEqual(charged, estimate + estimate // 2)
        self.assertEqual(
            sum(AIJob.objects.values_list("unbilled_credits", flat=True)), 0
        )
        self.assertEqual(fresh(wallet).reserved_credits, 0)
        self.assertEqual(holds().check_invariants(wallet), [])


@override_settings(BILLING_HOLDS_ENABLED=True)
class ProviderTimeoutTests(TransactionTestCase):
    def test_t1_a_provider_timeout_charges_nothing_and_the_next_item_runs_on_its_hold(
        self,
    ):
        # T-1 (§6.3 Failure, amended §21f): item 1's hold is returned, item
        # 2 runs on its own. Exactly 2E in the wallet: nothing is free.
        estimate = estimate_for()
        teacher = ai_ready_teacher("g.t1@example.com", 2 * estimate)
        wallet = CreditWallet.objects.get(user=teacher)
        job = make_job(wallet, "BATCH_GRADING", total_items=2, status="RUNNING")
        item_1, item_2 = make_item(job), make_item(job)
        holds().reserve(wallet, estimate, job, item=item_1)
        holds().reserve(wallet, estimate, job, item=item_2)
        timeout = openai.APITimeoutError(
            request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat")
        )

        with patch.object(AIProcessor, PROVIDER, side_effect=timeout):
            with self.assertRaises(openai.APITimeoutError):
                with holds().metered_unit(processing_task_id=item_1.pk, reason="t1"):
                    call_ai(teacher)

        self.assertEqual(fresh(wallet).reserved_credits, estimate)
        self.assertFalse(CreditUsageLog.objects.exists())
        first = fresh(item_1)
        self.assertEqual(first.hold_state, "RETURNED")
        self.assertEqual(first.released_credits, estimate)
        self.assertEqual(holds().item_hold_left(job, item=fresh(item_2)), estimate)
        self.assertEqual(remaining(wallet), 2 * estimate)

        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(estimate)):
            with holds().metered_unit(processing_task_id=item_2.pk, reason="t1"):
                call_ai(teacher)

        self.assertEqual(fresh(item_2).hold_drawn, estimate)
        job = fresh(job)
        self.assertEqual(
            (job.overrun_charged, job.unbilled_credits, job.consumed_credits),
            (0, 0, estimate),
        )
        self.assertEqual(remaining(wallet), estimate)
        self.assertEqual(fresh(wallet).reserved_credits, 0)
        self.assertEqual(holds().check_invariants(wallet), [])
