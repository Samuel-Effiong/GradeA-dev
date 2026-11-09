"""Epic B1b step 3 (NOTES §3.3): pins for the per-call query trim.

- The exact number of real queries (SAVEPOINT/RELEASE excluded) that one
  billed single call makes with holds on, so a later change cannot add the
  trimmed queries back without this test failing.
- The lock order: after the provider call, the wallet row is locked before
  the first write to any billing table.
- A wallet blocked between reserve and settle is still refused at settle,
  and nothing is charged (P3 hands settle's locked wallet to
  consume_credits; the block must still be read under that lock).
"""

from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings

from ai_processor.services import AIProcessor
from billing.errors import InsufficientCreditsError
from billing.models import AIJob, CreditUsageLog, CreditWallet
from billing.testing.holds import ai_ready_teacher, ai_response, call_ai, estimate_for

PROVIDER = "_AIProcessor__ai_model"
#: One billed single call, flag on, real queries (NOTES §3.3). It was 47 at
#: f1a28d666 (§3.2a) and the trim removes 10 (P1-P5).
QUERIES_PER_BILLED_SINGLE_CALL = 37
MARK = "-- provider call --"


class Recorder:
    def __init__(self):
        self.statements = []

    def __call__(self, execute, sql, params, many, context):
        self.statements.append(sql)
        return execute(sql, params, many, context)

    def real(self):
        return [
            sql
            for sql in self.statements
            if sql != MARK
            and not sql.lstrip().upper().startswith(("SAVEPOINT", "RELEASE"))
        ]


def is_wallet_lock(sql):
    return (
        '"billing_creditwallet"' in sql
        and "FOR UPDATE" in sql
        and sql.lstrip().startswith("SELECT")
    )


def is_billing_write(sql):
    head = sql.lstrip().upper()
    return head.startswith(("INSERT", "UPDATE", "DELETE")) and '"BILLING_' in head


@override_settings(BILLING_HOLDS_ENABLED=True)
class PerCallPinTests(TestCase):
    def setUp(self):
        self.teacher = ai_ready_teacher("b1b3.pin@example.com", 50 * estimate_for())
        self.provider = patch.object(
            AIProcessor, PROVIDER, return_value=ai_response(150)
        )
        self.provider.start()
        self.addCleanup(self.provider.stop)
        for _ in range(3):
            call_ai(self.teacher)  # warm-up, as the §3.1 listing

    def test_one_billed_single_call_makes_exactly_the_pinned_number_of_queries(self):
        recorder = Recorder()
        with connection.execute_wrapper(recorder):
            call_ai(self.teacher)
        self.assertEqual(
            len(recorder.real()),
            QUERIES_PER_BILLED_SINGLE_CALL,
            "\n".join(recorder.real()),
        )

    def test_after_the_provider_call_the_wallet_is_locked_before_any_billing_write(
        self,
    ):
        recorder = Recorder()

        def answered(*args, **kwargs):
            recorder.statements.append(MARK)
            return ai_response(150)

        with patch.object(AIProcessor, PROVIDER, side_effect=answered):
            with connection.execute_wrapper(recorder):
                call_ai(self.teacher)
        after = recorder.statements[recorder.statements.index(MARK) + 1 :]
        first_write = next(i for i, sql in enumerate(after) if is_billing_write(sql))
        self.assertTrue(
            any(is_wallet_lock(sql) for sql in after[:first_write]),
            "a billing write came before the wallet lock:\n"
            + "\n".join(after[: first_write + 1]),
        )

    def test_a_wallet_blocked_during_the_call_is_refused_at_settle(self):
        wallet = CreditWallet.objects.get(user=self.teacher)
        usage_before = CreditUsageLog.objects.filter(wallet=wallet).count()

        def block_then_answer(*args, **kwargs):
            CreditWallet.objects.filter(pk=wallet.pk).update(
                is_consumption_blocked=True
            )
            return ai_response(150)

        with patch.object(AIProcessor, PROVIDER, side_effect=block_then_answer):
            with self.assertRaises(InsufficientCreditsError):
                call_ai(self.teacher)
        self.assertEqual(
            CreditUsageLog.objects.filter(wallet=wallet).count(), usage_before
        )
        self.assertEqual(CreditWallet.objects.get(pk=wallet.pk).reserved_credits, 0)
        self.assertEqual(
            AIJob.objects.filter(wallet=wallet).latest("created_at").status,
            "FAILED",
        )
