"""
billing/tests/test_cancellation_visibility.py
===============================================
Everything that lets the frontend know a subscription is scheduled to
stop renewing — so it can show a "Resume" button and an accurate
explanation, the same way it already does for a scheduled plan change.

Four moving parts, locked down here:

1. UserSubscriptionSerializer's derived cancellation fields
   (has_pending_cancellation / cancellation_effective_date /
   cancellation_message), plus the persisted cancelled_at.
2. The cancel endpoint stamping cancelled_at.
3. The resume path clearing it again.
4. customer.subscription.updated mirroring Stripe's
   cancel_at_period_end onto auto_renew / cancelled_at — the gap that
   made a cancellation performed in the Stripe dashboard invisible to
   this app entirely.

THE TRAP THIS EXISTS TO PREVENT
--------------------------------
`auto_renew` was already exposed to the frontend, so the obvious
implementation is `auto_renew === false -> show Resume`. That is wrong:
EVERY free trial has auto_renew=False from birth (a trial must never
convert to paid without an explicit action), so keying off the raw flag
shows a bogus "your subscription is cancelled" to every trial user.
has_pending_cancellation exists precisely to be the safe flag, and
several tests below exist only to keep it that way.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

import stripe as real_stripe
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
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
from billing.serializers import UserSubscriptionSerializer
from billing.services import SubscriptionService
from billing.stripe_service import StripeWebhookHandler
from users.models import UserTypes

CustomUser = get_user_model()

STRIPE_SUB_ID = "sub_cancelvis_1"


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


def stripe_sub_payload(status="active", **extra):
    """
    Builds a customer.subscription.updated payload. cancel_at_period_end
    is only included when passed, so the "payload omits the flag" case
    stays expressible.
    """
    payload = {"id": STRIPE_SUB_ID, "status": status}
    payload.update(extra)
    return payload


class CancellationSerializerFieldTests(TestCase):
    """
    The three derived fields. These are what the frontend actually
    reads, so each state a real subscription can be in gets a case.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="cancelvis@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = make_plan(
            "PRO", PlanTier.PRO, 2999, 30_000_000, "price_cancelvis_pro"
        )
        self.cheaper_plan = make_plan(
            "STANDARD", PlanTier.STANDARD, 999, 10_000_000, "price_cancelvis_std"
        )
        CreditWallet.objects.get_or_create(user=self.user)

    def _make_sub(self, **overrides):
        now = timezone.now()
        defaults = {
            "user": self.user,
            "plan": self.plan,
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

    def _data(self, sub):
        return UserSubscriptionSerializer(sub).data

    # --- the normal, renewing case -----------------------------------

    def test_renewing_subscription_reports_no_cancellation(self):
        data = self._data(self._make_sub())

        self.assertFalse(data["has_pending_cancellation"])
        self.assertIsNone(data["cancellation_effective_date"])
        self.assertIsNone(data["cancellation_message"])
        self.assertIsNone(data["cancelled_at"])

    # --- the cancelled case ------------------------------------------

    def test_cancelled_subscription_reports_pending_cancellation(self):
        sub = self._make_sub(auto_renew=False, cancelled_at=timezone.now())

        data = self._data(sub)

        self.assertTrue(data["has_pending_cancellation"])
        self.assertIsNotNone(data["cancellation_effective_date"])
        self.assertIsNotNone(data["cancellation_message"])

    def test_effective_date_is_the_end_of_the_paid_period(self):
        """
        Access runs to the end of what was already paid for — never to
        the moment of cancellation.
        """
        sub = self._make_sub(auto_renew=False, cancelled_at=timezone.now())

        data = self._data(sub)

        # cancellation_effective_date is a SerializerMethodField, which
        # returns the raw datetime rather than the pre-rendered ISO
        # string billing_cycle_end gets from its DateTimeField — both
        # render identically once actually encoded to JSON, so compare
        # against the model attribute directly rather than the
        # already-serialized sibling field.
        self.assertEqual(data["cancellation_effective_date"], sub.billing_cycle_end)

    def test_message_includes_both_the_cancel_date_and_the_end_date(self):
        cancelled_on = timezone.now() - timedelta(days=2)
        sub = self._make_sub(auto_renew=False, cancelled_at=cancelled_on)

        message = self._data(sub)["cancellation_message"]

        self.assertIn(cancelled_on.date().isoformat(), message)
        self.assertIn(sub.billing_cycle_end.date().isoformat(), message)

    def test_message_degrades_gracefully_without_a_cancelled_at(self):
        """
        Rows cancelled before cancelled_at existed, and any row whose
        auto_renew was set outside the cancel flow, have no date. The
        message must drop the clause rather than render "None".
        """
        sub = self._make_sub(auto_renew=False, cancelled_at=None)

        data = self._data(sub)

        self.assertTrue(data["has_pending_cancellation"])
        message = data["cancellation_message"]
        self.assertNotIn("None", message)
        self.assertNotIn("You cancelled", message)
        self.assertIn(sub.billing_cycle_end.date().isoformat(), message)

    # --- the trial trap ----------------------------------------------

    def test_trial_is_never_reported_as_cancelled(self):
        """
        THE regression this whole field exists to prevent. A trial's
        auto_renew is False by design; a frontend keying off the raw
        flag would tell every trial user their subscription was
        cancelled and offer them a Resume button that does nothing.
        """
        trial = SubscriptionService.activate_free_trial(self.user, self.plan)
        self.assertFalse(trial.auto_renew, "precondition: trials are auto_renew=False")

        data = self._data(trial)

        self.assertFalse(data["has_pending_cancellation"])
        self.assertIsNone(data["cancellation_effective_date"])
        self.assertIsNone(data["cancellation_message"])

    def test_trial_with_a_stray_cancelled_at_is_still_not_reported(self):
        """Belt-and-braces: is_trial wins over any stray date."""
        trial = SubscriptionService.activate_free_trial(self.user, self.plan)
        trial.cancelled_at = timezone.now()
        trial.save(update_fields=["cancelled_at"])

        self.assertFalse(self._data(trial)["has_pending_cancellation"])

    # --- states where resume would fail, so the button must not show --

    def test_expired_period_is_not_resumable(self):
        """
        Once billing_cycle_end has passed there is nothing to resume —
        the resume endpoint rejects it and points at select-plan. The
        flag must agree, or the button 400s when clicked.
        """
        sub = self._make_sub(
            auto_renew=False,
            cancelled_at=timezone.now() - relativedelta(months=2),
            billing_cycle_start=timezone.now() - relativedelta(months=2),
            billing_cycle_end=timezone.now() - timedelta(days=1),
        )

        data = self._data(sub)

        self.assertFalse(data["has_pending_cancellation"])
        self.assertIsNone(data["cancellation_message"])

    def test_inactive_subscription_is_not_reported_as_cancelled(self):
        sub = self._make_sub(
            is_active=False, auto_renew=False, cancelled_at=timezone.now()
        )

        self.assertFalse(self._data(sub)["has_pending_cancellation"])

    # --- must not be confused with a scheduled plan change ------------

    def test_scheduled_downgrade_is_not_a_cancellation(self):
        """
        A downgrade still renews — just onto a different plan — so
        auto_renew stays True and this must report a pending CHANGE,
        not a pending cancellation. Conflating the two would offer
        "Resume" to a user who never cancelled.
        """
        sub = self._make_sub(
            pending_plan=self.cheaper_plan,
            pending_change_type=PendingChangeType.DOWNGRADE,
            pending_change_note="You'll move to STANDARD at cycle end.",
            stripe_schedule_id="sub_sched_cancelvis",
        )

        data = self._data(sub)

        self.assertTrue(data["has_pending_change"])
        self.assertFalse(data["has_pending_cancellation"])
        self.assertIsNone(data["cancellation_message"])

    def test_cancelled_subscription_reports_no_pending_change(self):
        """The mirror image — cancel discards any scheduled change."""
        sub = self._make_sub(auto_renew=False, cancelled_at=timezone.now())

        data = self._data(sub)

        self.assertTrue(data["has_pending_cancellation"])
        self.assertFalse(data["has_pending_change"])

    # --- contract ----------------------------------------------------

    def test_cancellation_fields_are_present_and_read_only(self):
        serializer = UserSubscriptionSerializer()

        for field in (
            "cancelled_at",
            "has_pending_cancellation",
            "cancellation_effective_date",
            "cancellation_message",
        ):
            self.assertIn(field, serializer.fields, f"{field} missing from payload")
            self.assertTrue(
                serializer.fields[field].read_only,
                f"{field} must be read-only — it is owned by the "
                f"cancel/resume/webhook paths, not by API writes.",
            )


class CancelEndpointStampsCancelledAtTests(APITestCase):
    """The cancel endpoint's half of the cancelled_at contract."""

    def setUp(self):
        cache.clear()
        self.user = CustomUser.objects.create_user(
            email="cancelvis-endpoint@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = make_plan(
            "PRO", PlanTier.PRO, 2999, 30_000_000, "price_cancelvis_ep"
        )
        CreditWallet.objects.get_or_create(user=self.user)
        self.client.force_authenticate(user=self.user)
        self.url = reverse("subscription-cancel")

    def _make_sub(self, **overrides):
        now = timezone.now()
        defaults = {
            "user": self.user,
            "plan": self.plan,
            "is_active": True,
            "auto_renew": True,
            "billing_cycle_start": now,
            "billing_cycle_end": now + relativedelta(months=1),
            "stripe_subscription_id": STRIPE_SUB_ID,
            "stripe_status": StripeSubscriptionStatus.ACTIVE,
        }
        defaults.update(overrides)
        return UserSubscription.objects.create(**defaults)

    @patch("stripe.Subscription")
    def test_cancel_stamps_cancelled_at(self, mock_subscription):
        sub = self._make_sub()
        before = timezone.now()

        self.client.post(self.url)

        sub.refresh_from_db()
        self.assertIsNotNone(sub.cancelled_at)
        self.assertGreaterEqual(sub.cancelled_at, before)
        self.assertLessEqual(sub.cancelled_at, timezone.now())

    @patch("stripe.Subscription")
    def test_repeat_cancel_does_not_slide_the_date(self, mock_subscription):
        """
        The date must record when the user ACTUALLY cancelled. Stamping
        on every call would keep resetting it to "now" each time the
        page re-posted.
        """
        sub = self._make_sub()
        self.client.post(self.url)
        sub.refresh_from_db()
        original = sub.cancelled_at

        self.client.post(self.url)

        sub.refresh_from_db()
        self.assertEqual(sub.cancelled_at, original)

    @patch("stripe.Subscription")
    def test_failed_stripe_cancel_leaves_cancelled_at_unset(self, mock_subscription):
        """
        If Stripe refused the cancellation, nothing was cancelled — the
        date must not be written either, or the frontend would show a
        cancellation banner for a subscription that still renews.
        """
        mock_subscription.modify.side_effect = real_stripe.error.APIConnectionError(
            "network blip"
        )
        sub = self._make_sub()

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        sub.refresh_from_db()
        self.assertIsNone(sub.cancelled_at)
        self.assertTrue(sub.auto_renew)

    @patch("stripe.Subscription")
    def test_cancelling_a_trial_does_not_fabricate_a_date(self, mock_subscription):
        """
        A trial is already auto_renew=False, so there is no True->False
        transition and nothing was really "cancelled". Stamping a date
        here would be inventing an event that never happened.
        """
        trial = SubscriptionService.activate_free_trial(self.user, self.plan)

        self.client.post(self.url)

        trial.refresh_from_db()
        self.assertIsNone(trial.cancelled_at)

    @patch("stripe.Subscription")
    def test_serializer_reports_the_cancellation_right_after_the_call(
        self, mock_subscription
    ):
        """The end-to-end point of the whole feature."""
        sub = self._make_sub()

        self.client.post(self.url)

        sub.refresh_from_db()
        data = UserSubscriptionSerializer(sub).data
        self.assertTrue(data["has_pending_cancellation"])
        self.assertIsNotNone(data["cancellation_effective_date"])
        self.assertIn("You cancelled", data["cancellation_message"])


class ResumeClearsCancelledAtTests(APITestCase):
    """The resume half — the banner must disappear again."""

    def setUp(self):
        cache.clear()
        self.user = CustomUser.objects.create_user(
            email="cancelvis-resume@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = make_plan(
            "PRO", PlanTier.PRO, 2999, 30_000_000, "price_cancelvis_res"
        )
        CreditWallet.objects.get_or_create(user=self.user)
        self.client.force_authenticate(user=self.user)
        self.url = reverse("subscription-resume")

    def _make_sub(self, **overrides):
        now = timezone.now()
        defaults = {
            "user": self.user,
            "plan": self.plan,
            "is_active": True,
            "auto_renew": False,
            "cancelled_at": now - timedelta(days=1),
            "billing_cycle_start": now,
            "billing_cycle_end": now + relativedelta(months=1),
            "stripe_subscription_id": STRIPE_SUB_ID,
            "stripe_status": StripeSubscriptionStatus.ACTIVE,
        }
        defaults.update(overrides)
        return UserSubscription.objects.create(**defaults)

    @patch("stripe.Subscription")
    def test_resume_clears_cancelled_at(self, mock_subscription):
        sub = self._make_sub()
        mock_subscription.retrieve.return_value = {
            "id": STRIPE_SUB_ID,
            "status": "active",
            "cancel_at_period_end": True,
        }

        self.client.post(self.url)

        sub.refresh_from_db()
        self.assertTrue(sub.auto_renew)
        self.assertIsNone(sub.cancelled_at)

    @patch("stripe.Subscription")
    def test_resume_clears_a_stale_date_even_when_auto_renew_already_true(
        self, mock_subscription
    ):
        """
        Drift case: auto_renew got back to True by some other path while
        cancelled_at was left behind. Resume must shed the stale date,
        or the frontend keeps rendering "you cancelled on ..." for a
        subscription that is renewing perfectly normally.
        """
        sub = self._make_sub(auto_renew=True)
        mock_subscription.retrieve.return_value = {
            "id": STRIPE_SUB_ID,
            "status": "active",
            "cancel_at_period_end": True,
        }

        self.client.post(self.url)

        sub.refresh_from_db()
        self.assertTrue(sub.auto_renew)
        self.assertIsNone(sub.cancelled_at)

    @patch("stripe.Subscription")
    def test_banner_is_gone_after_resume(self, mock_subscription):
        sub = self._make_sub()
        mock_subscription.retrieve.return_value = {
            "id": STRIPE_SUB_ID,
            "status": "active",
            "cancel_at_period_end": True,
        }

        self.client.post(self.url)

        sub.refresh_from_db()
        data = UserSubscriptionSerializer(sub).data
        self.assertFalse(data["has_pending_cancellation"])
        self.assertIsNone(data["cancellation_message"])
        self.assertIsNone(data["cancelled_at"])


class StripeDashboardCancellationSyncTests(TestCase):
    """
    customer.subscription.updated mirroring cancel_at_period_end.

    This closes a real gap: setting cancel_at_period_end on Stripe does
    NOT change the subscription's `status` (it stays "active"), so a
    cancellation made in the Stripe dashboard produced no status change
    and never reached the database at all. The local row kept claiming
    auto_renew=True right up until the subscription silently stopped
    renewing, and the user was never offered the resume flow.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="cancelvis-webhook@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = make_plan(
            "PRO", PlanTier.PRO, 2999, 30_000_000, "price_cancelvis_wh"
        )
        CreditWallet.objects.get_or_create(user=self.user)
        now = timezone.now()
        self.sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            auto_renew=True,
            billing_cycle_start=now,
            billing_cycle_end=now + relativedelta(months=1),
            stripe_subscription_id=STRIPE_SUB_ID,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )

    def _reload(self):
        return UserSubscription.objects.get(pk=self.sub.pk)

    # --- the gap itself ----------------------------------------------

    def test_dashboard_cancellation_is_mirrored_locally(self):
        StripeWebhookHandler.handle_subscription_updated(
            stripe_sub_payload("active", cancel_at_period_end=True)
        )

        updated = self._reload()
        self.assertFalse(updated.auto_renew)
        self.assertIsNotNone(updated.cancelled_at)
        self.assertTrue(updated.is_active, "cancel-at-period-end must not deactivate")

    def test_status_stays_active_which_is_why_status_sync_alone_missed_this(self):
        """Pins the premise: the event carries no status change at all."""
        StripeWebhookHandler.handle_subscription_updated(
            stripe_sub_payload("active", cancel_at_period_end=True)
        )

        updated = self._reload()
        self.assertEqual(updated.stripe_status, StripeSubscriptionStatus.ACTIVE)
        self.assertFalse(updated.auto_renew)

    def test_frontend_sees_the_banner_after_a_dashboard_cancellation(self):
        StripeWebhookHandler.handle_subscription_updated(
            stripe_sub_payload("active", cancel_at_period_end=True)
        )

        data = UserSubscriptionSerializer(self._reload()).data
        self.assertTrue(data["has_pending_cancellation"])
        self.assertIsNotNone(data["cancellation_message"])

    # --- the recorded date -------------------------------------------

    def test_uses_stripes_own_canceled_at_timestamp(self):
        """
        Stripe knows when the cancellation was actually requested; the
        webhook may land much later. Recording "now" would misreport it.
        """
        cancelled_on = datetime(2026, 3, 3, 12, 0, tzinfo=dt_timezone.utc)

        StripeWebhookHandler.handle_subscription_updated(
            stripe_sub_payload(
                "active",
                cancel_at_period_end=True,
                canceled_at=int(cancelled_on.timestamp()),
            )
        )

        self.assertEqual(self._reload().cancelled_at, cancelled_on)

    def test_falls_back_to_now_when_stripe_sends_no_canceled_at(self):
        before = timezone.now()

        StripeWebhookHandler.handle_subscription_updated(
            stripe_sub_payload("active", cancel_at_period_end=True)
        )

        cancelled_at = self._reload().cancelled_at
        self.assertGreaterEqual(cancelled_at, before)
        self.assertLessEqual(cancelled_at, timezone.now())

    def test_malformed_canceled_at_falls_back_instead_of_raising(self):
        """
        A bad timestamp must never be the reason a webhook 500s — that
        would send Stripe into a multi-day retry loop over a cosmetic
        field.
        """
        before = timezone.now()

        StripeWebhookHandler.handle_subscription_updated(
            stripe_sub_payload(
                "active", cancel_at_period_end=True, canceled_at="not-a-timestamp"
            )
        )

        updated = self._reload()
        self.assertFalse(updated.auto_renew)
        self.assertGreaterEqual(updated.cancelled_at, before)

    def test_our_own_cancel_date_wins_over_the_echoed_event(self):
        """
        Our cancel endpoint sets Stripe first, so the resulting webhook
        echoes back a change we just made. It must not overwrite the
        date we already recorded.
        """
        ours = timezone.now() - timedelta(days=3)
        self.sub.auto_renew = False
        self.sub.cancelled_at = ours
        self.sub.save(update_fields=["auto_renew", "cancelled_at"])

        StripeWebhookHandler.handle_subscription_updated(
            stripe_sub_payload(
                "active",
                cancel_at_period_end=True,
                canceled_at=int(timezone.now().timestamp()),
            )
        )

        self.assertEqual(self._reload().cancelled_at, ours)

    def test_backfills_a_missing_date_on_an_already_cancelled_row(self):
        """
        A row cancelled before cancelled_at existed still gets a date
        the next time Stripe reports on it.
        """
        self.sub.auto_renew = False
        self.sub.save(update_fields=["auto_renew"])

        StripeWebhookHandler.handle_subscription_updated(
            stripe_sub_payload("active", cancel_at_period_end=True)
        )

        self.assertIsNotNone(self._reload().cancelled_at)

    # --- the un-cancel direction -------------------------------------

    def test_dashboard_uncancel_is_mirrored_locally(self):
        self.sub.auto_renew = False
        self.sub.cancelled_at = timezone.now()
        self.sub.save(update_fields=["auto_renew", "cancelled_at"])

        StripeWebhookHandler.handle_subscription_updated(
            stripe_sub_payload("active", cancel_at_period_end=False)
        )

        updated = self._reload()
        self.assertTrue(updated.auto_renew)
        self.assertIsNone(updated.cancelled_at)

    def test_uncancel_clears_a_stale_date_even_if_auto_renew_is_already_true(self):
        self.sub.cancelled_at = timezone.now()
        self.sub.save(update_fields=["cancelled_at"])

        StripeWebhookHandler.handle_subscription_updated(
            stripe_sub_payload("active", cancel_at_period_end=False)
        )

        self.assertIsNone(self._reload().cancelled_at)

    # --- replay / partial payload safety ------------------------------

    def test_replay_of_an_already_synced_cancellation_writes_nothing(self):
        """
        This event fires for changes our own code just made, and Stripe
        redelivers. An in-sync replay must not touch the row.
        """
        self.sub.auto_renew = False
        self.sub.cancelled_at = timezone.now()
        self.sub.save(update_fields=["auto_renew", "cancelled_at"])

        with patch.object(UserSubscription, "save") as mock_save:
            StripeWebhookHandler.handle_subscription_updated(
                stripe_sub_payload("active", cancel_at_period_end=True)
            )

        mock_save.assert_not_called()

    def test_replay_of_a_normal_renewing_subscription_writes_nothing(self):
        with patch.object(UserSubscription, "save") as mock_save:
            StripeWebhookHandler.handle_subscription_updated(
                stripe_sub_payload("active", cancel_at_period_end=False)
            )

        mock_save.assert_not_called()

    def test_payload_without_the_flag_never_uncancels(self):
        """
        The critical safety rule: a thin or partial payload that OMITS
        cancel_at_period_end must not be read as False. Doing so would
        silently un-cancel a subscription the user genuinely ended.
        """
        self.sub.auto_renew = False
        self.sub.cancelled_at = timezone.now()
        self.sub.save(update_fields=["auto_renew", "cancelled_at"])

        StripeWebhookHandler.handle_subscription_updated(stripe_sub_payload("active"))

        updated = self._reload()
        self.assertFalse(updated.auto_renew)
        self.assertIsNotNone(updated.cancelled_at)

    def test_explicit_null_flag_is_also_ignored(self):
        self.sub.auto_renew = False
        self.sub.save(update_fields=["auto_renew"])

        StripeWebhookHandler.handle_subscription_updated(
            stripe_sub_payload("active", cancel_at_period_end=None)
        )

        self.assertFalse(self._reload().auto_renew)

    # --- interaction with the existing status sync --------------------

    def test_status_and_cancellation_sync_together(self):
        StripeWebhookHandler.handle_subscription_updated(
            stripe_sub_payload("past_due", cancel_at_period_end=True)
        )

        updated = self._reload()
        self.assertEqual(updated.stripe_status, StripeSubscriptionStatus.PAST_DUE)
        self.assertFalse(updated.auto_renew)
        self.assertTrue(updated.is_active)

    def test_unmapped_status_still_syncs_the_cancellation(self):
        """
        An unmapped status ("paused") leaves stripe_status alone, but
        the cancellation intent alongside it is still real and must not
        be dropped with it.
        """
        StripeWebhookHandler.handle_subscription_updated(
            stripe_sub_payload("paused", cancel_at_period_end=True)
        )

        updated = self._reload()
        self.assertEqual(
            updated.stripe_status,
            StripeSubscriptionStatus.ACTIVE,
            "unmapped status must still be left alone",
        )
        self.assertFalse(updated.auto_renew)

    def test_terminal_status_still_deactivates(self):
        StripeWebhookHandler.handle_subscription_updated(
            stripe_sub_payload("canceled", cancel_at_period_end=False)
        )

        updated = self._reload()
        self.assertEqual(updated.stripe_status, StripeSubscriptionStatus.CANCELED)
        self.assertFalse(updated.is_active)

    # --- rows that must not be touched --------------------------------

    def test_trial_is_never_mirrored(self):
        """
        A trial's auto_renew=False means "won't convert without an
        explicit action", not "cancelled". Mirroring Stripe onto it
        would either fabricate a cancellation date or, in the un-cancel
        direction, flip the trial into looking like a renewing paid plan.
        """
        self.sub.delete()
        trial = SubscriptionService.activate_free_trial(self.user, self.plan)
        trial.stripe_subscription_id = STRIPE_SUB_ID
        trial.save(update_fields=["stripe_subscription_id"])

        StripeWebhookHandler.handle_subscription_updated(
            stripe_sub_payload("trialing", cancel_at_period_end=False)
        )

        trial.refresh_from_db()
        self.assertFalse(trial.auto_renew, "a trial must never be flipped to renewing")
        self.assertIsNone(trial.cancelled_at)

    def test_inactive_row_is_not_matched(self):
        self.sub.is_active = False
        self.sub.save(update_fields=["is_active"])

        StripeWebhookHandler.handle_subscription_updated(
            stripe_sub_payload("active", cancel_at_period_end=True)
        )

        updated = self._reload()
        self.assertTrue(updated.auto_renew)
        self.assertIsNone(updated.cancelled_at)

    def test_unknown_subscription_id_does_not_raise(self):
        StripeWebhookHandler.handle_subscription_updated(
            {"id": "sub_never_seen", "status": "active", "cancel_at_period_end": True}
        )

        self.assertTrue(self._reload().auto_renew)


class CancelResumeRoundTripTests(APITestCase):
    """
    The full loop the frontend drives, including the Stripe-side
    variant, proving the flag flips in both directions.
    """

    def setUp(self):
        cache.clear()
        self.user = CustomUser.objects.create_user(
            email="cancelvis-roundtrip@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = make_plan(
            "PRO", PlanTier.PRO, 2999, 30_000_000, "price_cancelvis_rt"
        )
        CreditWallet.objects.get_or_create(user=self.user)
        self.client.force_authenticate(user=self.user)
        now = timezone.now()
        self.sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            auto_renew=True,
            billing_cycle_start=now,
            billing_cycle_end=now + relativedelta(months=1),
            stripe_subscription_id=STRIPE_SUB_ID,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )

    def _flag(self):
        self.sub.refresh_from_db()
        return UserSubscriptionSerializer(self.sub).data["has_pending_cancellation"]

    @patch("stripe.Subscription")
    def test_cancel_then_resume_round_trip(self, mock_subscription):
        self.assertFalse(self._flag())

        self.client.post(reverse("subscription-cancel"))
        self.assertTrue(self._flag())

        mock_subscription.retrieve.return_value = {
            "id": STRIPE_SUB_ID,
            "status": "active",
            "cancel_at_period_end": True,
        }
        self.client.post(reverse("subscription-resume"))
        self.assertFalse(self._flag())
        self.sub.refresh_from_db()
        self.assertIsNone(self.sub.cancelled_at)

    @patch("stripe.Subscription")
    def test_dashboard_cancel_then_in_app_resume(self, mock_subscription):
        """
        The cross-surface case the gap fix enables: cancelled in the
        Stripe dashboard, resumed from the app.
        """
        StripeWebhookHandler.handle_subscription_updated(
            stripe_sub_payload("active", cancel_at_period_end=True)
        )
        self.assertTrue(self._flag())

        mock_subscription.retrieve.return_value = {
            "id": STRIPE_SUB_ID,
            "status": "active",
            "cancel_at_period_end": True,
        }
        response = self.client.post(reverse("subscription-resume"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "resumed")
        self.assertFalse(self._flag())
