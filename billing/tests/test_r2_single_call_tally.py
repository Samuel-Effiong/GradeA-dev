"""R2 on staging: a single held call adds to the beta tally what it charged.

_SingleCallHold.finish recorded analytics with the call's `actual` usage,
but settle charges only chargeable = min(actual, held + free), and charges
nothing when it does not settle (a deleted wallet, a duplicate key). The
tally then ran ahead of the ledger, and the 80%/cap flags and the score
with it, until the nightly reconcile. The batch path (_UnitCallHold) already
records outcome.chargeable, only when settled; the single call now does the
same.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings

from ai_processor.services import AIProcessor
from billing import holds
from billing.charges import charged_q
from billing.models import BetaProfile, CreditUsageLog
from billing.testing.holds import ai_ready_teacher, ai_response, call_ai, estimate_for

PROVIDER = "_AIProcessor__ai_model"


def charged(user):
    return sum(
        CreditUsageLog.objects.filter(charged_q(user=user.pk)).values_list(
            "amount", flat=True
        )
    )


def tally(user):
    profile = BetaProfile.objects.filter(user=user).first()
    return profile.total_credits_used if profile else 0


@override_settings(BILLING_HOLDS_ENABLED=True)
class SingleCallTallyTests(TestCase):
    def test_a_call_that_used_more_than_it_could_be_charged_tallies_the_charge(self):
        teacher = ai_ready_teacher("r2.sc.over@example.com", estimate_for() + 100)
        actual = estimate_for() + 10_000
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(actual)):
            call_ai(teacher)
        self.assertEqual(charged(teacher), estimate_for() + 100)
        self.assertEqual(tally(teacher), charged(teacher))

    def test_a_call_within_its_hold_tallies_its_usage(self):
        teacher = ai_ready_teacher("r2.sc.ok@example.com", estimate_for() + 10_000)
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(500)):
            call_ai(teacher)
        self.assertEqual(charged(teacher), 500)
        self.assertEqual(tally(teacher), 500)

    def test_a_call_that_does_not_settle_adds_nothing_to_the_tally(self):
        teacher = ai_ready_teacher("r2.sc.unsettled@example.com", estimate_for() + 10)
        with patch.object(holds, "settle", return_value=holds.NOT_SETTLED):
            with patch.object(AIProcessor, PROVIDER, return_value=ai_response(500)):
                call_ai(teacher)
        self.assertEqual(charged(teacher), 0)
        self.assertEqual(tally(teacher), 0)

    def test_finish_single_call_still_returns_the_billing_task_id(self):
        # Its default return is unchanged for its other callers.
        teacher = ai_ready_teacher("r2.sc.id@example.com", estimate_for() + 10)
        job = holds.begin_single_call(
            wallet=teacher.credit_wallet,
            target_teacher=teacher,
            estimate=estimate_for(),
        )
        returned = holds.finish_single_call(
            job,
            5,
            feature="Grading Assignment",
            task_type="t",
            course=None,
            school=None,
        )
        self.assertIsInstance(returned, str)
        self.assertTrue(CreditUsageLog.objects.filter(task_id=returned).exists())
