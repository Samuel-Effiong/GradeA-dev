"""Epic B1a step 3a: the single-call hold in execute_graded_task (plan 08
§3.3), behind BILLING_HOLDS_ENABLED.

Flag on, no unit open: the call opens a SINGLE_CALL job, reserves the
call's estimate BEFORE the provider is reached (or refuses), settles the
actual usage after the response and returns the rest; on an exception it
charges nothing.
"""

import ast
import inspect
import textwrap
from unittest.mock import patch

from django.test import TestCase, override_settings

from ai_processor.models import AICallRecord
from ai_processor.services import AIProcessor
from billing.errors import InsufficientCreditsError, is_individual_teacher
from billing.models import AIJob, CreditUsageLog, CreditWallet, SettlementRecord
from billing.refunds import billing_refund_scope
from billing.testing.holds import ai_ready_teacher, ai_response, call_ai, estimate_for
from users.models import CustomUser, UserTypes

PROVIDER = "_AIProcessor__ai_model"


def wallet_of(user):
    return CreditWallet.objects.get(user=user)


class FlagOffTests(TestCase):
    @override_settings(BILLING_HOLDS_ENABLED=False)
    def test_with_the_flag_off_a_call_charges_as_before_and_opens_no_job(self):
        teacher = ai_ready_teacher("sc.off@example.com", estimate_for() + 10_000)
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(500)):
            call_ai(teacher)
        self.assertFalse(AIJob.objects.exists())
        self.assertEqual(CreditUsageLog.objects.filter(amount=500).count(), 1)
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)


@override_settings(BILLING_HOLDS_ENABLED=True)
class FlagOnSingleCallTests(TestCase):
    def test_a_call_holds_settles_and_returns_the_rest(self):
        teacher = ai_ready_teacher("sc.ok@example.com", estimate_for() + 10_000)
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(500)):
            call_ai(teacher)
        job = AIJob.objects.get(job_type="SINGLE_CALL")
        self.assertEqual(job.status, "SETTLED")
        self.assertEqual(job.reserved_credits, estimate_for())
        self.assertEqual((job.consumed_credits, job.hold_drawn), (500, 500))
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)
        record = SettlementRecord.objects.get(job=job)
        usage = CreditUsageLog.objects.filter(task_id=str(record.id))
        self.assertEqual(sum(u.amount for u in usage), 500)
        # FR-B-09: the call row joins the usage rows on the billing task id.
        call = AICallRecord.objects.get()
        self.assertEqual(call.task_id, str(record.id))

    def test_a_call_that_cannot_be_held_is_refused_before_the_provider(self):
        teacher = ai_ready_teacher("sc.refuse@example.com", estimate_for() - 1)
        with patch.object(AIProcessor, PROVIDER) as provider:
            with self.assertRaises(InsufficientCreditsError) as caught:
                call_ai(teacher)
        provider.assert_not_called()
        self.assertEqual(caught.exception.individual, is_individual_teacher(teacher))
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)
        self.assertEqual(AIJob.objects.get(job_type="SINGLE_CALL").status, "FAILED")

    def test_a_provider_failure_charges_nothing_and_returns_the_hold(self):
        teacher = ai_ready_teacher("sc.fail@example.com", estimate_for() + 10_000)
        with patch.object(AIProcessor, PROVIDER, side_effect=RuntimeError("down")):
            with self.assertRaises(RuntimeError):
                call_ai(teacher)
        self.assertFalse(CreditUsageLog.objects.exists())
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)
        job = AIJob.objects.get(job_type="SINGLE_CALL")
        self.assertEqual((job.status, job.released_credits), ("FAILED", estimate_for()))

    def test_a_refund_scope_still_refunds_a_settled_single_call(self):
        # Until the unit sites move (3b), a pipeline under billing_refund_scope
        # keeps today's refund-on-failure outcome with the flag on.
        teacher = ai_ready_teacher("sc.scope@example.com", estimate_for() + 10_000)
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(500)):
            with self.assertRaises(ValueError):
                with billing_refund_scope(reason="test"):
                    call_ai(teacher)
                    raise ValueError("pipeline failed after the call")
        self.assertTrue(CreditUsageLog.objects.filter(amount=500).exists())
        self.assertFalse(CreditUsageLog.objects.filter(is_refunded=False).exists())
        self.assertEqual(
            wallet_of(teacher).total_remaining_credits(), estimate_for() + 10_000
        )

    def test_a_real_super_admin_is_unmetered_and_opens_no_job(self):
        admin = CustomUser.objects.create_user(
            email="sc.admin@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=True,
            is_staff=True,
        )
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(500)):
            call_ai(admin)
        self.assertFalse(AIJob.objects.exists())


def _call_names(node):
    return {
        getattr(c.func, "attr", getattr(c.func, "id", None))
        for c in ast.walk(node)
        if isinstance(c, ast.Call)
    }


COMPOUND_FIELDS = ("body", "orelse", "finalbody")


def _check_dominance(stmts, seen, hold_calls, found):
    """Walk a statement list in order. A hold seen earlier in this list, or
    in an enclosing one, dominates the statements after it, including those
    nested inside later blocks; a hold inside a nested block does not leak
    out of it. Appends (lineno, dominated) for each provider call."""
    for stmt in stmts:
        nested = [
            getattr(stmt, f)
            for f in COMPOUND_FIELDS
            if isinstance(getattr(stmt, f, None), list)
        ] + [h.body for h in getattr(stmt, "handlers", [])]
        if nested:
            for sub in nested:
                _check_dominance(sub, seen, hold_calls, found)
            continue
        names = _call_names(stmt)
        if names & hold_calls:
            seen = True
        if "__ai_model" in names:
            found.append((stmt.lineno, seen))


class DominanceTests(TestCase):
    """M-B4: inside execute_graded_task's `if holds_enabled()` branch, every
    statement that calls the provider is preceded, in its own statement
    list, by a hold: the single-call reserve or the unit's pre-call check."""

    HOLD_CALLS = {"begin_single_call", "before_call"}

    def test_every_held_provider_call_is_dominated_by_a_hold(self):
        source = textwrap.dedent(inspect.getsource(AIProcessor.execute_graded_task))
        tree = ast.parse(source)
        held_branches = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.If) and "holds_enabled" in _call_names(n.test)
        ]
        self.assertTrue(held_branches, "no `if holds_enabled()` branch found")

        found = []
        for branch in held_branches:
            _check_dominance(branch.body, False, self.HOLD_CALLS, found)
        for lineno, dominated in found:
            self.assertTrue(
                dominated, f"provider call at line {lineno} is not preceded by a hold"
            )
        checked = len(found)
        self.assertGreaterEqual(
            checked, 2, "expected the unit and the single-call calls"
        )
