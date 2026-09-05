"""
billing/tests/test_broker_outage_resilience.py
==============================================
What billing does when Redis (the Celery broker) is unreachable.

WHY THIS FILE EXISTS
--------------------
Billing dispatches Celery work from inside financial code paths. Nothing in
the suite covered the broker being down, even though that is a routine
production event (Redis restart, failover, network blip) and `.delay()`
raises `kombu.exceptions.OperationalError` when it happens.

There are six dispatch sites in billing production code. Five are already
defensive — wrapped in `transaction.on_commit(_dispatch)` with an internal
try/except, so a broker outage costs an email and nothing else:

    services.py:1957                 (manual credit grant notification)
    license_service.py:1195          (teacher invitation)
    license_service.py:3060/3111/3158 (offline overage request mails)

The sixth was NOT, and has been fixed:

    tasks.py  sync_user_to_mailerlite.delay(str(sub.user_id))

It sat bare inside `reconcile_subscription_renewals`' per-subscription
`try`, immediately AFTER `sub.save()` had already deactivated the
subscription. On a broker outage the deactivation committed, the MailerLite
sync was lost, and the `OperationalError` was then caught by the generic
per-subscription handler — which counted that subscription as a
*reconciliation failure*. A Redis blip therefore produced an ERROR and a
"1 failed" summary for a subscription that had reconciled perfectly,
sending someone to investigate a billing problem that did not exist.

It is now wrapped like its five siblings, with a log line that says
explicitly that the billing side succeeded and only the marketing-list sync
was lost.

WHAT IS PINNED
--------------
  * a failure to enqueue a NON-CRITICAL notification does not make a
    SUCCESSFUL billing operation look failed ("0 failed", not "1 failed");
  * the local deactivation is durable — it is NOT rolled back;
  * swallowed is not silent: the lost sync is still logged for follow-up,
    with wording that does not read as a billing failure;
  * one subscription's broker failure does not stop the others;
  * the guarded credit-grant path completes with the broker down.
"""

from datetime import timedelta
from unittest.mock import patch

import stripe as real_stripe
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from kombu.exceptions import OperationalError

from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    PlanCategory,
    PlanTier,
    PlanType,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.services import ManualCreditService
from billing.tasks import reconcile_subscription_renewals
from users.models import UserTypes

CustomUser = get_user_model()

BROKER_DOWN = OperationalError(
    "Error 111 connecting to redis:6379. Connection refused."
)


def make_plan(name=PlanType.STANDARD, tier=PlanTier.STANDARD, price_id="price_std"):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=str(name),
        category=PlanCategory.INDIVIDUAL,
        tier=tier,
        interval=BillingInterval.MONTHLY,
        monthly_credits=10_000,
        # top_up_credits prices a grant in BLOCKS against the recipient's
        # resolved plan and raises without this.
        overage_block_size=500,
        overage_block_price=10,
        max_overage_blocks=10,
        stripe_price_id=price_id,
        is_active=True,
    )


def cancelled_stripe_sub(sub_id):
    """Drives the reconciler down its deactivation branch."""
    return {
        "id": sub_id,
        "status": "canceled",
        "latest_invoice": None,
        "items": {"data": [{"id": "si_1", "price": {"id": "price_std"}}]},
    }


class ReconcilerBrokerOutageTests(TestCase):
    def setUp(self):
        self.plan = make_plan()
        self.user = CustomUser.objects.create_user(
            email="broker.outage@gmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        CreditWallet.objects.get_or_create(user=self.user)
        now = timezone.now()
        self.sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - relativedelta(months=1),
            billing_cycle_end=now - timedelta(minutes=5),
            next_credit_grant_at=now - timedelta(minutes=5),
            stripe_subscription_id="sub_broker_1",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )

    def _run_with_broker_down(self):
        with patch.object(real_stripe, "Subscription") as mock_sub, patch(
            "users.tasks.sync_user_to_mailerlite.delay", side_effect=BROKER_DOWN
        ) as delay:
            mock_sub.retrieve.return_value = cancelled_stripe_sub("sub_broker_1")
            summary = reconcile_subscription_renewals()
        return summary, delay

    def test_the_deactivation_survives_a_broker_outage(self):
        """
        The financial fact — Stripe says cancelled, so we deactivate — must
        NOT be lost because a notification could not be queued. The save
        precedes the dispatch and is not inside a transaction that the
        raised OperationalError would roll back.
        """
        self._run_with_broker_down()

        self.sub.refresh_from_db()
        self.assertFalse(
            self.sub.is_active,
            "a broker outage rolled back a Stripe-confirmed cancellation",
        )
        self.assertEqual(self.sub.stripe_status, "CANCELED")

    def test_the_dispatch_was_actually_attempted(self):
        """Guards the test itself: if the patch target drifts, this fails
        rather than the suite silently proving nothing."""
        _, delay = self._run_with_broker_down()
        delay.assert_called_once()

    def test_a_broker_outage_is_not_reported_as_a_reconciliation_failure(self):
        """
        THE FIX. `sync_user_to_mailerlite.delay()` used to be bare, so a
        Redis blip propagated to the generic per-subscription handler and
        was counted as a RECONCILIATION failure — for a subscription that
        had in fact reconciled correctly. That sent someone to investigate
        a billing problem that did not exist.

        A failure to queue a NON-CRITICAL notification must never make a
        SUCCESSFUL billing operation look failed.
        """
        summary, _ = self._run_with_broker_down()

        self.assertIn(
            "0 failed",
            summary,
            "a lost marketing-list sync was reported as a billing failure",
        )
        # And the work it was reporting on genuinely happened.
        self.assertIn("1 skipped (past due)", summary)

    def test_the_lost_sync_is_still_logged_for_follow_up(self):
        """
        Swallowed must not mean silent — the MailerLite list is now out of
        date and somebody has to be able to find that out.
        """
        with self.assertLogs("billing.tasks", level="ERROR") as logs:
            self._run_with_broker_down()

        self.assertTrue(
            any("Could not queue the MailerLite sync" in line for line in logs.output),
            logs.output,
        )
        self.assertTrue(
            any("deactivation itself succeeded" in line for line in logs.output),
            "the log must say the billing side was fine, or it reads as a "
            "billing failure to whoever finds it",
        )

    def test_one_subscriptions_outage_does_not_stop_the_others(self):
        """Per-subscription isolation must hold under a broker outage too."""
        other_user = CustomUser.objects.create_user(
            email="broker.second@gmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        CreditWallet.objects.get_or_create(user=other_user)
        now = timezone.now()
        second = UserSubscription.objects.create(
            user=other_user,
            plan=self.plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - relativedelta(months=1),
            billing_cycle_end=now - timedelta(minutes=5),
            next_credit_grant_at=now - timedelta(minutes=5),
            stripe_subscription_id="sub_broker_2",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )

        with patch.object(real_stripe, "Subscription") as mock_sub, patch(
            "users.tasks.sync_user_to_mailerlite.delay", side_effect=BROKER_DOWN
        ):
            mock_sub.retrieve.side_effect = (
                lambda sub_id, *a, **k: cancelled_stripe_sub(sub_id)
            )
            reconcile_subscription_renewals()

        # BOTH were processed; the first one's failure did not abort the loop.
        self.sub.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(self.sub.is_active)
        self.assertFalse(second.is_active)

    def test_a_healthy_broker_leaves_no_failures(self):
        """
        The control case. Without it, "1 failed" above could be caused by
        something other than the outage and the test would prove nothing.
        """
        with patch.object(real_stripe, "Subscription") as mock_sub, patch(
            "users.tasks.sync_user_to_mailerlite.delay"
        ) as delay:
            mock_sub.retrieve.return_value = cancelled_stripe_sub("sub_broker_1")
            summary = reconcile_subscription_renewals()

        delay.assert_called_once()
        self.assertIn("0 failed", summary)


class GuardedDispatchSiteTests(TestCase):
    """
    The five `transaction.on_commit(_dispatch)` sites must swallow a broker
    outage entirely. Verified on the manual-credit-grant path, which is the
    one that moves credits.
    """

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            email="grant.admin@gmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )
        self.teacher = CustomUser.objects.create_user(
            email="grant.recipient@gmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)

        # top_up_credits prices a block against the recipient's resolved
        # plan, so the recipient needs a live subscription for the grant to
        # be priceable at all.
        plan = make_plan()
        now = timezone.now()
        UserSubscription.objects.create(
            user=self.teacher,
            plan=plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - timedelta(days=1),
            billing_cycle_end=now + relativedelta(months=1),
            next_credit_grant_at=now + relativedelta(months=1),
        )

    def test_a_manual_credit_grant_completes_while_the_broker_is_down(self):
        """
        The credits must land even though the "you got credits" email could
        not be queued. Asserts the actual bucket that was created, not just
        the absence of an exception.
        """
        with patch("billing.services.send_email_task.delay", side_effect=BROKER_DOWN):
            ManualCreditService.top_up_credits(
                target_user=self.teacher,
                blocks=2,
                reason="broker outage regression test",
                granted_by=self.admin,
            )

        bucket = CreditBucket.objects.filter(
            wallet=self.wallet, bucket_type=CreditBucketType.MANUAL_GRANT
        ).first()
        self.assertIsNotNone(bucket, "the grant did not survive a broker outage")
        self.assertGreater(bucket.total_credits, 0)
