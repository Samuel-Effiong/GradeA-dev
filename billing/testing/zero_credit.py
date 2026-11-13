"""
Test helper for the zero-credit scope (billing/zero_credit.py).

Each Part 1 epic that declares a zero-credit operation writes its FR-B-10 test
as one call:

    assert_operation_is_zero_credit(self, "assignment_copy", lambda: copy(...))

The helper proves three things about the callable, without the caller
re-implementing them per operation:

  1. it entered ``zero_credit_scope(<operation>)`` (so removing the scope from
     the production code fails the test: FR-B-10 "a mutation removing the scope
     fails the test");
  2. it wrote no CreditLedger / CreditUsageLog row and changed no bucket
     balance;
  3. it made no model-provider call.
"""

from unittest.mock import patch

from django.db.models import Sum

from billing import zero_credit
from billing.models import CreditBucket, CreditLedger, CreditUsageLog


def _credit_state():
    return (
        CreditLedger.objects.count(),
        CreditUsageLog.objects.count(),
        CreditBucket.objects.aggregate(
            used=Sum("used_credits"), total=Sum("total_credits")
        ),
    )


def assert_operation_is_zero_credit(testcase, operation, run):
    """Run ``run()``; fail ``testcase`` unless it is provably zero-credit.
    Returns whatever ``run()`` returned."""
    from ai_processor.services import AIProcessor

    if operation not in zero_credit.ZERO_CREDIT_OPERATIONS:
        testcase.fail(f"{operation!r} is not a registered zero-credit operation")

    before = _credit_state()
    previous = zero_credit._entry_recorder
    entered = []
    zero_credit._entry_recorder = entered
    try:
        with patch.object(AIProcessor, "_AIProcessor__ai_model") as provider:
            result = run()
    finally:
        zero_credit._entry_recorder = previous

    testcase.assertIn(
        operation,
        entered,
        f"{operation!r} never entered zero_credit_scope(); the operation is not "
        "enforced as zero-credit",
    )
    testcase.assertEqual(
        provider.call_count, 0, f"{operation!r} called the model provider"
    )
    testcase.assertEqual(
        _credit_state(), before, f"{operation!r} changed the credit ledger or buckets"
    )
    return result
