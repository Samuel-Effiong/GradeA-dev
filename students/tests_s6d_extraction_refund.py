"""
Epic A S6d, F1 (08a §6.0): always refund a failed item, including answer
extraction's partial chunk charges.

An answer upload of six pages is extracted in two chunks. Here chunk 1
succeeds and is charged on every attempt, and chunk 2 times out on every
attempt, so the upload fails with PROVIDER_FAILURE. Before S6d nothing
opened a refund scope around upload_answers_engine, so chunk 1's charges
(three of them, one per outer attempt) were kept. The ledger must now net
to zero, and the message must say so.

The provider is the answer benchmark's deterministic fake. It is wrapped
the way the real execute_graded_task behaves: a call is charged, and the
charge registered with the open refund scope, only after it succeeds (a
failed call is never charged).
"""

from unittest.mock import patch

from ai_processor.benchmark.answers import SCENARIOS_BY_ID
from ai_processor.benchmark.answers.harness import rasterize
from ai_processor.benchmark.answers.provider import (
    FakeProvider,
    ProviderBehaviour,
    timeout_error,
)
from ai_processor.exceptions import REFUNDED, ProviderFailureError
from assignments.tests_cache_matrix_g3 import question
from billing.models import CreditLedger, CreditWallet
from billing.refunds import record_billing_task_id
from students import services as student_services
from students.models import StudentSubmission
from students.tests_s6d_rubric_gate import RubricGateTestCase

SIX_PAGES = SCENARIOS_BY_ID["AE-016"]
CHUNK_TWO_FIRST_PAGE = 4
CHARGE = 1_000


class ExtractionRefundTests(RubricGateTestCase):
    def setUp(self):
        super().setUp()
        self.assignment = self.world([question()])[0]
        StudentSubmission.objects.filter(assignment=self.assignment).delete()
        self.wallet = CreditWallet.objects.get(user=self.teacher)
        self.charges = []

    def used_credits(self):
        return sum(b.used_credits for b in self.wallet.buckets.all())

    def charging(self, provider):
        """execute_graded_task's contract: charge and register only after
        the call succeeds."""

        def call(*args, **kwargs):
            result = provider(*args, **kwargs)
            task_id = f"s6d-f1-chunk-{len(self.charges) + 1}"
            self.wallet.consume_credits(
                amount=CHARGE,
                feature="Answer Extraction",
                task_type="extract_answers",
                task_id=task_id,
            )
            record_billing_task_id(task_id)
            self.charges.append(task_id)
            return result

        return call

    def test_a_mid_chunk_failure_nets_the_ledger_to_zero(self):
        def fail_the_second_chunk(recorded):
            if recorded.pages_sent[:1] == (CHUNK_TWO_FIRST_PAGE,):
                raise timeout_error()

        provider = FakeProvider(
            SIX_PAGES, ProviderBehaviour(on_call=fail_the_second_chunk)
        )
        used_before = self.used_credits()
        ledger_before = CreditLedger.objects.count()

        with patch.object(
            student_services.ai_processor,
            "execute_graded_task",
            self.charging(provider),
        ):
            with self.assertRaises(ProviderFailureError) as caught:
                student_services.upload_answers_engine(
                    self.assignment,
                    rasterize(SIX_PAGES),
                    self.teacher,
                    is_proxy_upload=True,
                    file_name="p07.pdf",
                )

        # Not vacuous: chunk 1 really was charged, once per outer attempt.
        self.assertEqual(len(self.charges), 3, self.charges)
        self.assertGreater(CreditLedger.objects.count(), ledger_before)
        # F1: every one of those charges was refunded.
        self.assertEqual(self.used_credits(), used_before)
        # ...and the message says so.
        self.assertEqual(caught.exception.params, {"credit_clause": REFUNDED})
        self.assertFalse(
            StudentSubmission.objects.filter(assignment=self.assignment).exists()
        )

    def test_a_successful_upload_keeps_its_charges(self):
        """The control: the scope refunds only a failure."""
        provider = FakeProvider(SIX_PAGES, ProviderBehaviour())
        used_before = self.used_credits()

        with patch.object(
            student_services.ai_processor,
            "execute_graded_task",
            self.charging(provider),
        ), patch.object(
            student_services, "_match_enrolled_student", return_value=self.student
        ):
            student_services.upload_answers_engine(
                self.assignment,
                rasterize(SIX_PAGES),
                self.teacher,
                is_proxy_upload=True,
                file_name="p07.pdf",
            )

        self.assertGreaterEqual(len(self.charges), 2)
        self.assertEqual(self.used_credits(), used_before + CHARGE * len(self.charges))
