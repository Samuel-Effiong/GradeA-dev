"""
billing/tests/test_subscription_cancel.py
===========================================
Coverage for SubscriptionManagementViewSet.cancel (billing/views.py) —
the counterpart to `resume`, and previously covered only by the QA chaos
harness (which asserts nothing about its actual branches).

`cancel` never cancels immediately: it sets cancel_at_period_end=True on
Stripe and clears auto_renew locally, so the user keeps their plan and
credits until billing_cycle_end. On top of that it has to unwind any
previously-scheduled plan change (a downgrade or deferred upgrade held
in a Stripe SubscriptionSchedule), which is where the ordering matters:

    release the Stripe schedule  ->  set cancel_at_period_end  ->  write local state

Each step can fail independently, and the middle one failing AFTER the
schedule was already released is the reason the compensating branch at
billing/views.py:802-815 exists at all. That branch is the single most
important thing in this file: without it, a user could end up with a
released Stripe schedule but a local row still advertising a pending
plan change that will never happen.

Stripe calls are mocked by patching attributes on the real `stripe`
module, leaving `stripe.error.*` as the real exception classes (see
test_subscription_upgrade.py's module docstring for why).
"""

from unittest.mock import patch

import stripe as real_stripe
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.models import (
    BillingInterval,
    CreditWallet,
    PendingChangeType,
    PlanCategory,
    PlanTier,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from users.models import UserTypes

CustomUser = get_user_model()

STRIPE_SUB_ID = "sub_cancel_1"
STRIPE_SCHEDULE_ID = "sub_sched_cancel_1"


def make_plan(
    name,
    tier,
    price_cents,
    monthly_credits,
    stripe_price_id,
    category=PlanCategory.INDIVIDUAL,
    interval=BillingInterval.MONTHLY,
):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=name,
        category=category,
        tier=tier,
        interval=interval,
        price_cents=price_cents,
        monthly_credits=monthly_credits,
        stripe_price_id=stripe_price_id,
        is_active=True,
    )


class CancelTestBase(APITestCase):
    def setUp(self):
        # The endpoint guards itself with a cache-backed per-user lock;
        # a leaked key from another test would turn every request here
        # into a spurious 400.
        cache.clear()

        self.user = CustomUser.objects.create_user(
            email="cancel@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.pro_plan = make_plan(
            "PRO", PlanTier.PRO, 2999, 30_000_000, "price_cancel_pro"
        )
        self.standard_plan = make_plan(
            "STANDARD", PlanTier.STANDARD, 999, 10_000_000, "price_cancel_std"
        )
        CreditWallet.objects.get_or_create(
            user=self.user, defaults={"stripe_customer_id": "cus_cancel_1"}
        )
        self.client.force_authenticate(user=self.user)
        self.url = reverse("subscription-cancel")

    def _make_sub(self, **overrides):
        now = timezone.now()
        defaults = {
            "user": self.user,
            "plan": self.pro_plan,
            "is_active": True,
            "auto_renew": True,
            "is_trial": False,
            "billing_cycle_start": now,
            "billing_cycle_end": now + relativedelta(months=1),
            "stripe_subscription_id": STRIPE_SUB_ID,
            "stripe_status": StripeSubscriptionStatus.ACTIVE,
        }
        defaults.update(overrides)
        return UserSubscription.objects.create(**defaults)

    def _make_sub_with_pending_downgrade(self):
        return self._make_sub(
            pending_plan=self.standard_plan,
            pending_change_type=PendingChangeType.DOWNGRADE,
            pending_change_note="You'll move to STANDARD at cycle end.",
            stripe_schedule_id=STRIPE_SCHEDULE_ID,
        )


class CancelHappyPathTests(CancelTestBase):
    """The plain case: an ordinary renewing subscription, nothing pending."""

    @patch("stripe.Subscription")
    def test_sets_cancel_at_period_end_on_stripe(self, mock_subscription):
        self._make_sub()

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_subscription.modify.assert_called_once_with(
            STRIPE_SUB_ID, cancel_at_period_end=True
        )

    @patch("stripe.Subscription")
    def test_clears_auto_renew_locally(self, mock_subscription):
        sub = self._make_sub()

        self.client.post(self.url)

        sub.refresh_from_db()
        self.assertFalse(sub.auto_renew)

    @patch("stripe.Subscription")
    def test_does_not_deactivate_or_shorten_the_current_period(self, mock_subscription):
        """
        Cancellation is always end-of-cycle. The user keeps their plan,
        their credits, and their billing_cycle_end — anything else here
        would be taking away access they already paid for.
        """
        sub = self._make_sub()
        original_end = sub.billing_cycle_end

        self.client.post(self.url)

        sub.refresh_from_db()
        self.assertTrue(sub.is_active)
        self.assertEqual(sub.plan_id, self.pro_plan.id)
        self.assertEqual(sub.billing_cycle_end, original_end)
        self.assertEqual(sub.stripe_status, StripeSubscriptionStatus.ACTIVE)

    @patch("stripe.Subscription")
    def test_response_shape(self, mock_subscription):
        self._make_sub()

        response = self.client.post(self.url)

        self.assertEqual(response.data["status"], "cancelled")
        self.assertIn(
            "will not renew at the end of the current billing cycle",
            response.data["message"],
        )

    @patch("stripe.Subscription")
    def test_no_stripe_subscription_id_skips_stripe_entirely(self, mock_subscription):
        """
        A manually-granted subscription has no Stripe counterpart. It
        must still be cancellable locally rather than erroring.
        """
        sub = self._make_sub(stripe_subscription_id=None)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_subscription.modify.assert_not_called()
        sub.refresh_from_db()
        self.assertFalse(sub.auto_renew)


class CancelIdempotencyTests(CancelTestBase):
    """Re-cancelling an already-cancelled subscription."""

    @patch("stripe.Subscription")
    def test_already_not_renewing_reports_so(self, mock_subscription):
        self._make_sub(auto_renew=False)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "cancelled")
        self.assertIn("already set to not renew", response.data["message"])

    @patch("stripe.Subscription")
    def test_already_not_renewing_still_reasserts_on_stripe(self, mock_subscription):
        """
        Deliberate: the Stripe call is NOT skipped for an
        already-cancelled local row. Local auto_renew=False is not proof
        that Stripe agrees (it could have been changed in the Stripe
        dashboard), so re-cancelling re-converges the two.
        """
        self._make_sub(auto_renew=False)

        self.client.post(self.url)

        mock_subscription.modify.assert_called_once_with(
            STRIPE_SUB_ID, cancel_at_period_end=True
        )

    @patch("stripe.Subscription")
    def test_cancelling_twice_in_a_row_succeeds(self, mock_subscription):
        """Proves the per-user lock is released in the `finally`."""
        self._make_sub()

        first = self.client.post(self.url)
        second = self.client.post(self.url)

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertIn("already set to not renew", second.data["message"])

    @patch("stripe.Subscription")
    def test_pending_change_means_not_already_cancelled(self, mock_subscription):
        """
        auto_renew=False alone is not enough to report "already
        cancelled" — a row with a pending plan change still has real
        work to unwind, so it must take the full path and say so.
        """
        with patch("stripe.SubscriptionSchedule"):
            sub = self._make_sub_with_pending_downgrade()
            sub.auto_renew = False
            sub.save(update_fields=["auto_renew"])

            response = self.client.post(self.url)

        self.assertNotIn("already set to not renew", response.data["message"])
        self.assertIn("previously scheduled plan change", response.data["message"])


class CancelWithPendingPlanChangeTests(CancelTestBase):
    """
    Cancelling while a downgrade / deferred upgrade is scheduled. The
    Stripe SubscriptionSchedule must be released and the local pending
    fields cleared, or the user would be cancelled AND still get moved
    onto a different plan at cycle end.
    """

    @patch("stripe.SubscriptionSchedule")
    @patch("stripe.Subscription")
    def test_releases_the_stripe_schedule(self, mock_subscription, mock_schedule):
        self._make_sub_with_pending_downgrade()

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_schedule.release.assert_called_once_with(STRIPE_SCHEDULE_ID)

    @patch("stripe.SubscriptionSchedule")
    @patch("stripe.Subscription")
    def test_releases_schedule_before_setting_cancel_at_period_end(
        self, mock_subscription, mock_schedule
    ):
        """
        Order matters: Stripe documents that directly modifying a
        schedule-managed subscription can conflict with the schedule's
        own phase management, so the release has to land first.
        """
        call_order = []
        mock_schedule.release.side_effect = lambda *a, **kw: call_order.append(
            "release"
        )
        mock_subscription.modify.side_effect = lambda *a, **kw: call_order.append(
            "modify"
        )

        self._make_sub_with_pending_downgrade()
        self.client.post(self.url)

        self.assertEqual(call_order, ["release", "modify"])

    @patch("stripe.SubscriptionSchedule")
    @patch("stripe.Subscription")
    def test_clears_all_pending_fields(self, mock_subscription, mock_schedule):
        sub = self._make_sub_with_pending_downgrade()

        self.client.post(self.url)

        sub.refresh_from_db()
        self.assertIsNone(sub.pending_plan_id)
        self.assertIsNone(sub.pending_change_type)
        self.assertIsNone(sub.pending_change_note)
        self.assertIsNone(sub.stripe_schedule_id)
        self.assertFalse(sub.auto_renew)

    @patch("stripe.SubscriptionSchedule")
    @patch("stripe.Subscription")
    def test_message_mentions_the_cancelled_plan_change(
        self, mock_subscription, mock_schedule
    ):
        self._make_sub_with_pending_downgrade()

        response = self.client.post(self.url)

        self.assertIn(
            "previously scheduled plan change has also been cancelled",
            response.data["message"],
        )

    @patch("stripe.SubscriptionSchedule")
    @patch("stripe.Subscription")
    def test_schedule_id_without_pending_plan_is_still_cleared(
        self, mock_subscription, mock_schedule
    ):
        """
        stripe_schedule_id and pending_plan are cleared by two separate
        branches. A row carrying a schedule id but no pending plan (a
        partially-unwound state) must still end up with the id cleared,
        or the next plan change would try to reuse a released schedule.
        """
        sub = self._make_sub(stripe_schedule_id=STRIPE_SCHEDULE_ID)

        self.client.post(self.url)

        mock_schedule.release.assert_called_once_with(STRIPE_SCHEDULE_ID)
        sub.refresh_from_db()
        self.assertIsNone(sub.stripe_schedule_id)
        self.assertFalse(sub.auto_renew)

    @patch("stripe.SubscriptionSchedule")
    @patch("stripe.Subscription")
    def test_already_released_schedule_is_not_an_error(
        self, mock_subscription, mock_schedule
    ):
        """
        release_schedule treats InvalidRequestError ("already released /
        gone") as a no-op. Cancellation must still complete.
        """
        mock_schedule.release.side_effect = real_stripe.error.InvalidRequestError(
            "No such subscription schedule", param=None
        )

        sub = self._make_sub_with_pending_downgrade()
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertFalse(sub.auto_renew)
        self.assertIsNone(sub.stripe_schedule_id)


class CancelStripeFailureTests(CancelTestBase):
    """
    The failure paths. These decide whether Stripe and the local DB can
    end up disagreeing about what happens at cycle end.
    """

    @patch("stripe.SubscriptionSchedule")
    @patch("stripe.Subscription")
    def test_schedule_release_failure_aborts_before_touching_anything(
        self, mock_subscription, mock_schedule
    ):
        """
        If the schedule can't be released, the cancellation must NOT
        proceed — otherwise the user is told "cancelled" while Stripe
        still executes the old scheduled plan change at cycle end.
        """
        mock_schedule.release.side_effect = real_stripe.error.APIConnectionError(
            "network down"
        )

        sub = self._make_sub_with_pending_downgrade()
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Could not release", response.data["detail"])

        mock_subscription.modify.assert_not_called()
        sub.refresh_from_db()
        self.assertTrue(sub.auto_renew)
        self.assertEqual(sub.pending_plan_id, self.standard_plan.id)
        self.assertEqual(sub.stripe_schedule_id, STRIPE_SCHEDULE_ID)

    @patch("stripe.Subscription")
    def test_stripe_modify_failure_leaves_local_state_untouched(
        self, mock_subscription
    ):
        """
        No schedule involved: if Stripe refuses the cancellation, nothing
        local may change. Clearing auto_renew here would tell the user
        they're cancelled while Stripe happily renews them.
        """
        mock_subscription.modify.side_effect = real_stripe.error.CardError(
            "Your card was declined.", param=None, code="card_declined"
        )

        sub = self._make_sub()
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "Could not cancel your subscription with our payment provider",
            response.data["detail"],
        )
        sub.refresh_from_db()
        self.assertTrue(sub.auto_renew)

    @patch("stripe.SubscriptionSchedule")
    @patch("stripe.Subscription")
    def test_modify_failure_after_release_clears_the_orphaned_pending_change(
        self, mock_subscription, mock_schedule
    ):
        """
        THE compensating branch (billing/views.py:802-815).

        The schedule was already released on Stripe's side, then the
        cancellation call failed. The cancellation itself must NOT be
        reported as done — but the local pending-plan fields describe a
        Stripe schedule that no longer exists, so they have to be
        cleared anyway. Leaving them would advertise a plan change that
        can never happen, and would make the next plan change try to
        reuse a dead schedule id.
        """
        mock_subscription.modify.side_effect = real_stripe.error.APIConnectionError(
            "network blip"
        )

        sub = self._make_sub_with_pending_downgrade()
        response = self.client.post(self.url)

        # The cancellation is correctly reported as failed...
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(
            "Could not cancel your subscription with our payment provider",
            response.data["detail"],
        )

        sub.refresh_from_db()
        # ...the orphaned pending change is cleaned up to match Stripe...
        self.assertIsNone(sub.pending_plan_id)
        self.assertIsNone(sub.pending_change_type)
        self.assertIsNone(sub.pending_change_note)
        self.assertIsNone(sub.stripe_schedule_id)
        # ...but the subscription still renews, because the cancel failed.
        self.assertTrue(sub.auto_renew)
        self.assertTrue(sub.is_active)

    @patch("stripe.SubscriptionSchedule")
    @patch("stripe.Subscription")
    def test_retrying_after_a_failed_modify_succeeds(
        self, mock_subscription, mock_schedule
    ):
        """
        The state left behind by the compensating branch must be a
        RESUMABLE one: a second attempt (Stripe now reachable) has to
        complete cleanly rather than tripping over the half-unwound row.
        """
        mock_subscription.modify.side_effect = real_stripe.error.APIConnectionError(
            "network blip"
        )
        sub = self._make_sub_with_pending_downgrade()
        self.client.post(self.url)

        mock_subscription.modify.side_effect = None
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertFalse(sub.auto_renew)
        self.assertIsNone(sub.pending_plan_id)


class CancelGuardTests(CancelTestBase):
    """Preconditions: no subscription, concurrent mutation, permissions."""

    def test_no_active_subscription_returns_404(self):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["status"], "inactive")

    @patch("stripe.Subscription")
    def test_inactive_subscription_is_not_cancellable(self, mock_subscription):
        self._make_sub(is_active=False)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_subscription.modify.assert_not_called()

    @patch("stripe.Subscription")
    def test_concurrent_billing_change_is_rejected(self, mock_subscription):
        """
        cancel shares one lock key with select_plan and resume, so a
        plan change in flight blocks a cancellation rather than the two
        interleaving on the same row.
        """
        self._make_sub()
        cache.add(f"billing:planchange:{self.user.id}", "1", timeout=30)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already being processed", response.data["detail"])
        mock_subscription.modify.assert_not_called()

    @patch("stripe.Subscription")
    def test_lock_is_released_even_when_the_request_fails(self, mock_subscription):
        """
        The lock is dropped in a `finally`. If an early-return path
        leaked it, the user would be locked out of all billing changes
        for the full lock TTL.
        """
        mock_subscription.modify.side_effect = real_stripe.error.APIConnectionError(
            "network blip"
        )
        self._make_sub()

        self.client.post(self.url)

        self.assertIsNone(cache.get(f"billing:planchange:{self.user.id}"))

    def test_lock_is_released_after_a_404(self):
        self.client.post(self.url)

        self.assertIsNone(cache.get(f"billing:planchange:{self.user.id}"))

    def test_students_cannot_cancel(self):
        student = CustomUser.objects.create_user(
            email="cancel-student@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        self.client.force_authenticate(user=student)

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_cannot_cancel(self):
        self.client.force_authenticate(user=None)

        response = self.client.post(self.url)

        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    @patch("stripe.Subscription")
    def test_cancels_only_the_requesting_users_subscription(self, mock_subscription):
        other_user = CustomUser.objects.create_user(
            email="cancel-other@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        CreditWallet.objects.get_or_create(user=other_user)
        now = timezone.now()
        other_sub = UserSubscription.objects.create(
            user=other_user,
            plan=self.pro_plan,
            is_active=True,
            auto_renew=True,
            billing_cycle_start=now,
            billing_cycle_end=now + relativedelta(months=1),
            stripe_subscription_id="sub_cancel_other",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )
        mine = self._make_sub()

        self.client.post(self.url)

        mine.refresh_from_db()
        other_sub.refresh_from_db()
        self.assertFalse(mine.auto_renew)
        self.assertTrue(other_sub.auto_renew)
        mock_subscription.modify.assert_called_once_with(
            STRIPE_SUB_ID, cancel_at_period_end=True
        )


class CancelThenResumeTests(CancelTestBase):
    """
    cancel and resume are two halves of one behavior. Neither file
    alone proves they actually compose, so this chains them: cancel
    must leave state that resume can genuinely undo.
    """

    @patch("stripe.Subscription")
    def test_cancel_then_resume_restores_renewal(self, mock_subscription):
        sub = self._make_sub()

        cancel_response = self.client.post(self.url)
        self.assertEqual(cancel_response.status_code, status.HTTP_200_OK)
        sub.refresh_from_db()
        self.assertFalse(sub.auto_renew)

        # Stripe now reports what cancel just asked for.
        mock_subscription.retrieve.return_value = {
            "id": STRIPE_SUB_ID,
            "status": "active",
            "cancel_at_period_end": True,
        }

        resume_response = self.client.post(reverse("subscription-resume"))

        self.assertEqual(resume_response.status_code, status.HTTP_200_OK)
        self.assertEqual(resume_response.data["status"], "resumed")
        mock_subscription.modify.assert_any_call(
            STRIPE_SUB_ID, cancel_at_period_end=False
        )
        sub.refresh_from_db()
        self.assertTrue(sub.auto_renew)

    @patch("stripe.SubscriptionSchedule")
    @patch("stripe.Subscription")
    def test_resume_does_not_restore_a_cancelled_plan_change(
        self, mock_subscription, mock_schedule
    ):
        """
        cancel deliberately discards any scheduled plan change. resume
        undoes the cancellation only — it must not resurrect the
        pending downgrade, whose Stripe schedule is already gone.
        """
        sub = self._make_sub_with_pending_downgrade()

        self.client.post(self.url)

        mock_subscription.retrieve.return_value = {
            "id": STRIPE_SUB_ID,
            "status": "active",
            "cancel_at_period_end": True,
        }
        self.client.post(reverse("subscription-resume"))

        sub.refresh_from_db()
        self.assertTrue(sub.auto_renew)
        self.assertIsNone(sub.pending_plan_id)
        self.assertIsNone(sub.stripe_schedule_id)
