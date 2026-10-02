"""
Smoke coverage for billing Celery tasks' invocation signatures.

reconcile_subscription_renewals was declared `def ...(self)` but decorated
with a bare @shared_task (no bind=True), so every scheduled beat
invocation — which passes no arguments — raised
`TypeError: missing 1 required positional argument: 'self'`. The daily
individual-renewal safety net therefore never ran. `.apply()` here invokes
the task exactly the way beat does (no args), so this fails on the broken
signature and passes with bind=True.

Run with:
    python manage.py test billing.tests.test_billing_tasks
"""

from django.test import TestCase

from billing.tasks import reconcile_subscription_renewals


class ReconcileSubscriptionRenewalsSignatureTest(TestCase):
    def test_scheduled_no_arg_invocation_does_not_crash(self):
        result = reconcile_subscription_renewals.apply()

        self.assertTrue(
            result.successful(),
            f"task raised instead of running: {result.result!r}",
        )
