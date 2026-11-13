"""Epic B1a step 2: overrun (plan 08 §3.5, §6.3 H-1, R-3)."""

from unittest.mock import patch

from django.db.models import F
from django.test import TestCase

from billing.models import CreditBucket
from billing.testing.holds import fresh, funded_wallet, holds, make_job, remaining


class OverrunTests(TestCase):
    def test_overrun_then_release_returns_the_wallet_to_its_start(self):
        # H-1 (a): hold 50, actual 80, free balance 200.
        wallet = funded_wallet("o.h1a@example.com", 250)
        job = make_job(wallet, status="RESERVED")
        holds().reserve(wallet, 50, job)
        outcome = holds().settle(job, 80)
        self.assertEqual(
            (outcome.chargeable, outcome.drawn, outcome.overrun_charged),
            (80, 50, 30),
        )
        self.assertEqual(outcome.unbilled, 0)
        self.assertEqual(holds().finish_job(job, "SETTLED"), 0)
        job = fresh(job)
        self.assertEqual(fresh(wallet).reserved_credits, 0)
        self.assertEqual((job.hold_drawn, job.overrun_charged), (50, 30))
        self.assertEqual(holds().check_invariants(wallet), [])

    def test_with_no_free_balance_the_leftover_returns_in_the_settle(self):
        # H-1 (b): hold 100, actual 80, free balance 0.
        wallet = funded_wallet("o.h1b@example.com", 100)
        job = make_job(wallet)
        holds().reserve(wallet, 100, job)
        outcome = holds().settle(job, 80)
        self.assertEqual((outcome.chargeable, outcome.unbilled), (80, 0))
        self.assertEqual(outcome.leftover, 20)
        self.assertEqual(fresh(wallet).reserved_credits, 0)
        self.assertEqual(remaining(wallet), 20)

    def test_beyond_hold_and_free_balance_the_excess_is_absorbed(self):
        # Hold 60, free 40, actual 150: charge 100, absorb 50, never negative.
        wallet = funded_wallet("o.absorb@example.com", 100)
        job = make_job(wallet)
        holds().reserve(wallet, 60, job)
        with patch("billing.holds.audit_metrics.count") as count:
            outcome = holds().settle(job, 150)
        self.assertEqual(
            (outcome.chargeable, outcome.overrun_charged, outcome.unbilled),
            (100, 40, 50),
        )
        self.assertEqual(fresh(job).unbilled_credits, 50)
        self.assertEqual(remaining(wallet), 0)
        self.assertFalse(
            CreditBucket.objects.filter(
                wallet=wallet, used_credits__gt=F("total_credits")
            ).exists()
        )
        names = [call.args[0] for call in count.call_args_list]
        self.assertIn("credit_overrun", names)
        self.assertIn("credit_overrun_charged", names)

    def test_an_overrun_never_takes_another_jobs_hold(self):
        # R-3: job A holds 100; job B holds 50 with 50 free; B's 200 can take
        # only its own 50 plus the free 50.
        wallet = funded_wallet("o.r3@example.com", 200)
        job_a, job_b = make_job(wallet), make_job(wallet)
        holds().reserve(wallet, 100, job_a)
        holds().reserve(wallet, 50, job_b)
        outcome = holds().settle(job_b, 200)
        self.assertEqual((outcome.chargeable, outcome.unbilled), (100, 100))
        self.assertEqual(holds().item_hold_left(fresh(job_a)), 100)
        self.assertEqual(fresh(wallet).reserved_credits, 100)
        self.assertEqual(remaining(wallet), 100)
