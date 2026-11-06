"""Epic B1a step 3a: the metered unit core (plan 08 §3.3, §3.4).

Flag on: calls inside `metered_unit()` draw on the unit's job. Before each
call the pre-call fit check reserves that call's estimate (Q1: no up-front
amount in B1a); the actual usage goes on the unit's tally; the unit settles
ONCE when it exits normally and charges nothing when it raises. Flag off:
`metered_unit()` is billing_refund_scope.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings

from ai_processor.models import AICallRecord
from ai_processor.services import AIProcessor
from billing.errors import InsufficientCreditsError
from billing.models import AIJob, CreditUsageLog, CreditWallet, SettlementRecord
from billing.testing.holds import (
    ai_ready_teacher,
    ai_response,
    call_ai,
    estimate_for,
    holds,
)

PROVIDER = "_AIProcessor__ai_model"


def wallet_of(user):
    return CreditWallet.objects.get(user=user)


@override_settings(BILLING_HOLDS_ENABLED=True)
class UnitTests(TestCase):
    def test_two_calls_in_a_unit_settle_once(self):
        teacher = ai_ready_teacher("u.two@example.com", 3 * estimate_for())
        responses = [ai_response(150), ai_response(250)]
        with patch.object(AIProcessor, PROVIDER, side_effect=responses):
            with holds().metered_unit(reason="test"):
                call_ai(teacher)
                call_ai(teacher)
        job = AIJob.objects.get()
        self.assertEqual(job.job_type, "SINGLE_ITEM")
        self.assertEqual(job.status, "SETTLED")
        self.assertEqual(job.consumed_credits, 400)
        record = SettlementRecord.objects.get(job=job)
        self.assertEqual(
            set(CreditUsageLog.objects.values_list("task_id", flat=True)),
            {str(record.id)},
        )
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)
        # FR-B-09 join: both call rows reach the usage rows by task id.
        usage_ids = CreditUsageLog.objects.values_list("task_id", flat=True)
        self.assertEqual(AICallRecord.objects.filter(task_id__in=usage_ids).count(), 2)

    def test_a_unit_that_raises_charges_nothing_and_returns_its_hold(self):
        teacher = ai_ready_teacher("u.fail@example.com", 3 * estimate_for())
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(150)):
            with self.assertRaises(ValueError):
                with holds().metered_unit(reason="test"):
                    call_ai(teacher)
                    raise ValueError("pipeline failed")
        self.assertFalse(CreditUsageLog.objects.exists())
        job = AIJob.objects.get()
        self.assertEqual(job.status, "FAILED")
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)
        self.assertEqual(
            wallet_of(teacher).total_remaining_credits(), 3 * estimate_for()
        )

    def test_a_call_the_unit_cannot_hold_is_refused_before_the_provider(self):
        # U-1: the first call uses its whole hold; the second call's
        # estimate cannot be reserved: 1 provider call, not 2.
        estimate = estimate_for()
        teacher = ai_ready_teacher("u.refuse@example.com", estimate + 1_000)
        with patch.object(
            AIProcessor, PROVIDER, return_value=ai_response(estimate)
        ) as provider:
            with self.assertRaises(InsufficientCreditsError):
                with holds().metered_unit(reason="test"):
                    call_ai(teacher)
                    call_ai(teacher)
        self.assertEqual(provider.call_count, 1)
        self.assertFalse(CreditUsageLog.objects.exists())
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)

    def test_an_inner_unit_passes_its_tally_to_the_outer_on_success(self):
        teacher = ai_ready_teacher("u.nest@example.com", 3 * estimate_for())
        responses = [ai_response(100), ai_response(200)]
        with patch.object(AIProcessor, PROVIDER, side_effect=responses):
            with holds().metered_unit(reason="outer"):
                call_ai(teacher)
                with holds().metered_unit(reason="inner"):
                    call_ai(teacher)
        self.assertEqual(SettlementRecord.objects.count(), 1)
        self.assertEqual(AIJob.objects.get().consumed_credits, 300)

    def test_a_failed_inner_unit_is_not_charged_when_the_outer_goes_on(self):
        teacher = ai_ready_teacher("u.innerfail@example.com", 3 * estimate_for())
        responses = [ai_response(100), ai_response(200)]
        with patch.object(AIProcessor, PROVIDER, side_effect=responses):
            with holds().metered_unit(reason="outer"):
                call_ai(teacher)
                try:
                    with holds().metered_unit(reason="inner"):
                        call_ai(teacher)
                        raise ValueError("inner step failed")
                except ValueError:
                    pass
        self.assertEqual(AIJob.objects.get().consumed_credits, 100)
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)


class FlagOffUnitTests(TestCase):
    @override_settings(BILLING_HOLDS_ENABLED=False)
    def test_with_the_flag_off_a_unit_is_the_refund_scope(self):
        teacher = ai_ready_teacher("u.off@example.com", 3 * estimate_for())
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(150)):
            with self.assertRaises(ValueError):
                with holds().metered_unit(reason="test"):
                    call_ai(teacher)
                    raise ValueError("pipeline failed")
        self.assertFalse(AIJob.objects.exists())
        self.assertTrue(CreditUsageLog.objects.filter(amount=150).exists())
        self.assertFalse(CreditUsageLog.objects.filter(is_refunded=False).exists())
