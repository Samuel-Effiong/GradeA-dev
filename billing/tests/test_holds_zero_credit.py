"""Epic B1a step 2: holds and B0's zero-credit scope (plan 08 §3.11, Z-1)."""

from django.test import TestCase

from billing.models import AIJob
from billing.testing.holds import fresh, funded_wallet, holds, make_job
from billing.zero_credit import (
    ZeroCreditViolation,
    register_zero_credit_operations,
    zero_credit_scope,
)

OPERATION = "b1a_z1_test"


class ZeroCreditHoldTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        register_zero_credit_operations(OPERATION)

    def test_reserve_is_refused_inside_a_zero_credit_scope(self):
        wallet = funded_wallet("z.reserve@example.com", 100)
        job = make_job(wallet)
        with zero_credit_scope(OPERATION):
            with self.assertRaises(ZeroCreditViolation) as caught:
                holds().reserve(wallet, 10, job)
        self.assertEqual(caught.exception.attempted, "CREDIT_RESERVE")
        self.assertEqual(fresh(wallet).reserved_credits, 0)

    def test_creating_a_job_is_refused_inside_a_zero_credit_scope(self):
        wallet = funded_wallet("z.create@example.com", 100)
        with zero_credit_scope(OPERATION):
            with self.assertRaises(ZeroCreditViolation):
                holds().create_job(
                    wallet=wallet, requested_by=wallet.user, job_type="SINGLE_CALL"
                )
        self.assertFalse(AIJob.objects.exists())

    def test_a_hold_taken_outside_does_not_let_a_zero_credit_settle_through(self):
        wallet = funded_wallet("z.settle@example.com", 100)
        job = make_job(wallet)
        holds().reserve(wallet, 10, job)
        with zero_credit_scope(OPERATION):
            with self.assertRaises(ZeroCreditViolation):
                holds().settle(job, 5)
        self.assertEqual(fresh(wallet).total_remaining_credits(), 100)
        self.assertEqual(fresh(job).consumed_credits, 0)
