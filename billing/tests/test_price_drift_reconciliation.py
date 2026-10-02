"""
billing/tests/test_price_drift_reconciliation.py
================================================
F9 (detection half) — a customer billed one price while our records say
another, with nothing noticing.

THE FAILURE BEING DETECTED
--------------------------
Every Stripe webhook handler is decorated `@transaction.atomic` and makes
outbound Stripe calls from inside the transaction. The sharp case is
`_handle_individual_upgrade_checkout_completed`: it runs
`stripe.Subscription.modify(...)` and THEN does substantial database work
(activate_subscription / apply_immediate_plan_change plus saves). If that
later database work raises, the transaction rolls back — but the Stripe
call is not undone. Stripe now bills the new price; the local row still
says the old plan.

WHY A SEPARATE TASK
-------------------
`reconcile_subscription_renewals` filters `billing_cycle_end__lte=now`, so
it only ever inspects subscriptions that are already OVERDUE. Drift sits on
subscriptions that are perfectly CURRENT. Adding the check to that loop
would have examined precisely the rows that cannot exhibit the problem —
`test_the_renewals_reconciler_cannot_see_this` pins that, so nobody
"simplifies" the two tasks back into one.

The task is DETECTION ONLY. Deciding whether to charge the customer what we
recorded or record what they were charged is a money decision for a human,
so the task deliberately makes no correcting write. That is asserted too.
"""

from datetime import timedelta
from unittest.mock import patch

import stripe as real_stripe
from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    BillingInterval,
    CreditWallet,
    PlanCategory,
    PlanTier,
    PlanType,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.tasks import reconcile_subscription_prices
from users.models import UserTypes

CustomUser = get_user_model()


def make_plan(name, tier, price_id, credits=10_000):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=str(name),
        category=PlanCategory.INDIVIDUAL,
        tier=tier,
        interval=BillingInterval.MONTHLY,
        monthly_credits=credits,
        stripe_price_id=price_id,
        is_active=True,
    )


class _FakeListing:
    """
    Stands in for a Stripe ListObject: the task walks it with
    auto_paging_iter(), so that is the only surface that matters.
    """

    def __init__(self, rows):
        self._rows = rows

    def auto_paging_iter(self):
        return iter(self._rows)


def stripe_sub_row(sub_id, price_id, status="active"):
    return {
        "id": sub_id,
        "status": status,
        "items": {"data": [{"id": f"si_{sub_id}", "price": {"id": price_id}}]},
    }


class PriceDriftDetectionTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="drift@gmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        CreditWallet.objects.get_or_create(user=self.user)
        self.standard = make_plan(
            PlanType.STANDARD, PlanTier.STANDARD, "price_standard"
        )
        self.pro = make_plan(PlanType.PRO, PlanTier.PRO, "price_pro", credits=50_000)

        now = timezone.now()
        # Deliberately CURRENT, not overdue: this is the state in which
        # drift actually occurs, and the state the renewals sweep ignores.
        self.sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.standard,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - timedelta(days=2),
            billing_cycle_end=now + relativedelta(months=1),
            next_credit_grant_at=now + relativedelta(months=1),
            stripe_subscription_id="sub_drift_1",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )

    def _run(self, rows):
        with patch.object(real_stripe, "Subscription") as mock_sub:
            mock_sub.list.return_value = _FakeListing(rows)
            return reconcile_subscription_prices()

    def test_drift_is_detected_and_logged_as_an_error(self):
        """
        The regression: Stripe bills price_pro, we recorded the Standard
        plan, and before this task nothing in the system said a word.
        """
        rows = [stripe_sub_row("sub_drift_1", "price_pro")]

        with self.assertLogs("billing.tasks", level="ERROR") as logs:
            summary = self._run(rows)

        self.assertIn("1 drifted", summary)
        drift_lines = [line for line in logs.output if "PRICE DRIFT" in line]
        self.assertEqual(len(drift_lines), 1, logs.output)
        # The alarm has to carry enough to act on.
        self.assertIn("price_pro", drift_lines[0])
        self.assertIn("price_standard", drift_lines[0])
        self.assertIn("drift@gmail.com", drift_lines[0])
        self.assertIn("sub_drift_1", drift_lines[0])

    def test_a_matching_price_is_silent(self):
        rows = [stripe_sub_row("sub_drift_1", "price_standard")]

        summary = self._run(rows)

        self.assertIn("1 checked", summary)
        self.assertIn("0 drifted", summary)

    def test_detection_makes_no_corrective_write(self):
        """
        Deliberate: reconciling this automatically would either charge the
        customer for something they did not buy or hand them a plan they
        did not pay for. Both are human decisions.
        """
        rows = [stripe_sub_row("sub_drift_1", "price_pro")]

        with self.assertLogs("billing.tasks", level="ERROR"):
            self._run(rows)

        self.sub.refresh_from_db()
        self.assertEqual(self.sub.plan_id, self.standard.id)
        self.assertTrue(self.sub.is_active)

    def test_a_pending_scheduled_change_warns_instead_of_alarming(self):
        """
        Around the cycle boundary Stripe can legitimately show the new price
        a moment before the webhook records it locally. That is a race, not
        a lost customer charge, so it must not cry wolf on the ERROR channel.
        """
        self.sub.pending_plan = self.pro
        self.sub.stripe_schedule_id = "sub_sched_1"
        self.sub.save(update_fields=["pending_plan", "stripe_schedule_id"])
        rows = [stripe_sub_row("sub_drift_1", "price_pro")]

        with self.assertLogs("billing.tasks", level="WARNING") as logs:
            summary = self._run(rows)

        self.assertIn("1 pending-change mismatches", summary)
        self.assertIn("0 drifted", summary)
        self.assertFalse(
            any("PRICE DRIFT" in line for line in logs.output), logs.output
        )

    def test_a_subscription_missing_from_stripe_is_counted_not_ignored(self):
        """
        Cancelled/incomplete subscriptions are absent from the active
        listing. Reporting them as "checked, no drift" would overstate
        coverage, so they get their own counter.
        """
        summary = self._run([])

        self.assertIn("0 checked", summary)
        self.assertIn("1 not found in Stripe's active list", summary)

    def test_a_plan_with_no_stripe_price_is_skipped(self):
        """Free/manual/offline plans have no remote price to diverge from."""
        self.standard.stripe_price_id = ""
        self.standard.save(update_fields=["stripe_price_id"])
        rows = [stripe_sub_row("sub_drift_1", "price_pro")]

        summary = self._run(rows)

        self.assertIn("1 skipped (no Stripe price)", summary)
        self.assertIn("0 drifted", summary)

    def test_trials_and_inactive_rows_are_not_examined(self):
        self.sub.is_trial = True
        self.sub.save(update_fields=["is_trial"])
        rows = [stripe_sub_row("sub_drift_1", "price_pro")]

        summary = self._run(rows)

        self.assertIn("no subscriptions to check", summary)

    def test_only_the_drifted_subscription_is_reported(self):
        other = CustomUser.objects.create_user(
            email="fine@gmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        CreditWallet.objects.get_or_create(user=other)
        now = timezone.now()
        UserSubscription.objects.create(
            user=other,
            plan=self.pro,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - timedelta(days=2),
            billing_cycle_end=now + relativedelta(months=1),
            next_credit_grant_at=now + relativedelta(months=1),
            stripe_subscription_id="sub_fine_1",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )
        rows = [
            stripe_sub_row("sub_drift_1", "price_pro"),
            stripe_sub_row("sub_fine_1", "price_pro"),
        ]

        with self.assertLogs("billing.tasks", level="ERROR") as logs:
            summary = self._run(rows)

        self.assertIn("2 checked", summary)
        self.assertIn("1 drifted", summary)
        drift_lines = [line for line in logs.output if "PRICE DRIFT" in line]
        self.assertEqual(len(drift_lines), 1)
        self.assertIn("drift@gmail.com", drift_lines[0])


class PriceDriftListingFailureTests(TestCase):
    """
    Without the listing there is nothing to compare against. Returning a
    clean "0 drifted" would read as an all-clear, which is worse than no
    check at all.
    """

    def setUp(self):
        user = CustomUser.objects.create_user(
            email="listfail@gmail.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        CreditWallet.objects.get_or_create(user=user)
        plan = make_plan(PlanType.STANDARD, PlanTier.STANDARD, "price_standard")
        now = timezone.now()
        UserSubscription.objects.create(
            user=user,
            plan=plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - timedelta(days=2),
            billing_cycle_end=now + relativedelta(months=1),
            next_credit_grant_at=now + relativedelta(months=1),
            stripe_subscription_id="sub_listfail_1",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )

    def test_a_stripe_listing_failure_raises_rather_than_reporting_all_clear(self):
        with patch.object(real_stripe, "Subscription") as mock_sub:
            mock_sub.list.side_effect = real_stripe.error.APIConnectionError("down")

            with self.assertLogs("billing.tasks", level="ERROR") as logs:
                with self.assertRaises(real_stripe.error.StripeError):
                    reconcile_subscription_prices()

        self.assertTrue(
            any("NO drift check was performed" in line for line in logs.output),
            logs.output,
        )


class PriceDriftScopeTests(TestCase):
    """
    Pins WHY this is a separate task, so the two sweeps are not merged back
    together by a later tidy-up.
    """

    def test_the_renewals_reconciler_cannot_see_this(self):
        import inspect

        from billing.tasks import reconcile_subscription_renewals  # noqa: F401

        source = inspect.getsource(reconcile_subscription_renewals)
        # It selects only subscriptions whose cycle has already lapsed...
        self.assertIn("billing_cycle_end__lte=now", source)
        # ...and never looks at price, which is the whole point.
        self.assertNotIn("stripe_price_id", source)


class PriceDriftScheduleTests(TestCase):
    def test_the_task_is_registered_in_the_beat_schedule(self):
        entry = settings.CELERY_BEAT_SCHEDULE["reconcile-subscription-prices-daily"]
        self.assertEqual(entry["task"], "billing.tasks.reconcile_subscription_prices")

    def test_it_runs_after_the_renewals_sweep(self):
        prices = settings.CELERY_BEAT_SCHEDULE["reconcile-subscription-prices-daily"][
            "schedule"
        ]
        renewals = settings.CELERY_BEAT_SCHEDULE["reconcile-subscriptions-daily"][
            "schedule"
        ]
        self.assertEqual(prices.hour, renewals.hour)
        self.assertGreater(min(prices.minute), min(renewals.minute))
