"""
Console-tab dispatch: session round-trip, serializer validation, and
that a known action name reaches the matching billing.live_qa.chaos
function. Chaos action CORRECTNESS is already covered in
billing/tests/test_chaos.py -- this file only proves qa_console.py's
own plumbing (session storage, JSON marshaling, dispatch) is wired
right, against a fully-faked Stripe/harness so it costs nothing and
never touches real network, matching every other offline test in this
suite's mocking convention.
"""

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.test import TestCase, override_settings
from django.urls import reverse

from billing.live_qa.chaos import DEFAULT_ACTIONS
from billing.models import BillingInterval, PlanCategory, PlanTier, SubscriptionPlan
from billing.stripe_live_qa_scenarios import Subscriber
from users.models import CustomUser, UserTypes

TEST_KEY = "sk_test_fake"  # pragma: allowlist secret


def _make_user(user_type, is_superuser=False):
    # is_active defaults to False on CustomUser -- force_login goes
    # through the real AuthenticationMiddleware, which refuses an
    # inactive user, so this must be explicit (see
    # test_qa_console_permissions.py for the full explanation).
    return CustomUser.objects.create_user(
        email=f"{user_type.lower()}-{uuid4().hex[:10]}@example.com",
        password="testpass123",  # pragma: allowlist secret
        user_type=user_type,
        is_superuser=is_superuser,
        is_active=True,
    )


def _make_plans():
    SubscriptionPlan.objects.create(
        name="STANDARD",
        display_name="Standard",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.STANDARD,
        interval=BillingInterval.MONTHLY,
        price_cents=999,
        monthly_credits=10_000,
        stripe_price_id="price_std",
        carry_over_percent=0,
        carry_over_expiry_months=1,
        is_active=True,
    )
    SubscriptionPlan.objects.create(
        name="PRO",
        display_name="Pro",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.PRO,
        interval=BillingInterval.MONTHLY,
        price_cents=4_999,
        monthly_credits=50_000,
        stripe_price_id="price_pro",
        carry_over_percent=0,
        carry_over_expiry_months=1,
        is_active=True,
    )
    SubscriptionPlan.objects.create(
        name="STANDARD_ANNUAL",
        display_name="Standard Annual",
        category=PlanCategory.INDIVIDUAL,
        tier=PlanTier.STANDARD,
        interval=BillingInterval.ANNUAL,
        price_cents=9_999,
        monthly_credits=10_000,
        stripe_price_id="price_std_annual",
        carry_over_percent=0,
        carry_over_expiry_months=1,
        is_active=True,
    )


@override_settings(ENABLE_STRIPE_LIVE_QA=True, STRIPE_SECRET_KEY=TEST_KEY)
@patch("billing.qa_console.live_qa_enabled", return_value=True)
class QaConsoleActionTests(TestCase):
    def setUp(self):
        _make_plans()
        self.admin = _make_user(UserTypes.SUPER_ADMIN, is_superuser=True)
        self.test_user = _make_user(UserTypes.TEACHER)
        self.client.force_login(self.admin)

    def _fake_harness(self):
        harness = MagicMock()
        harness.run_id = "console-test"
        harness.started_at = 1_700_000_000
        harness.dispatched_event_ids = set()
        harness.drain_events.return_value = []
        return harness

    def _fake_subscriber(self):
        return Subscriber(
            user=self.test_user,
            clock_id="clock_fake",
            customer_id="cus_fake",
            stripe_subscription_id="sub_fake",
            plan=SubscriptionPlan.objects.get(name="STANDARD"),
        )

    def _post(self, url_name, payload=None):
        return self.client.post(
            reverse(url_name),
            data=json.dumps(payload or {}),
            content_type="application/json",
        )

    # -- new subscriber ----------------------------------------------------

    def test_new_subscriber_stores_session_state(self, _mock_enabled):
        with patch(
            "billing.qa_console.LiveQAHarness", return_value=self._fake_harness()
        ), patch(
            "billing.qa_console._establish_subscriber",
            return_value=self._fake_subscriber(),
        ):
            response = self._post("qa-console-new-subscriber")

        self.assertEqual(response.status_code, 200)
        session_data = self.client.session["qa_console_subscriber"]
        self.assertEqual(session_data["customer_id"], "cus_fake")
        self.assertEqual(session_data["local_user_id"], str(self.test_user.id))
        self.assertFalse(session_data["cancelled"])
        self.assertEqual(session_data["started_at"], 1_700_000_000)

    def test_action_restores_started_at_onto_the_fresh_harness(self, _mock_enabled):
        """A fresh LiveQAHarness() defaults started_at to "now - 60s".
        Without restoring the ORIGINAL subscriber's started_at from the
        session on every click, the event-poll floor would silently
        creep forward each request and could permanently miss a
        renewal event drained too slowly on an earlier click -- this is
        the exact bug reported against the console (state stuck while
        Stripe's own subscription kept renewing)."""
        session = self.client.session
        session["qa_console_subscriber"] = {
            "run_id": "console-test",
            "started_at": 12345,
            "local_user_id": str(self.test_user.id),
            "clock_id": "clock_fake",
            "customer_id": "cus_fake",
            "stripe_subscription_id": "sub_fake",
            "using_failing_card": False,
            "cancelled": False,
            "dispatched_event_ids": ["evt_already_seen"],
        }
        session.save()

        harness = self._fake_harness()
        harness.started_at = 999_999_999  # what a FRESH harness would default to
        seen_states = []

        def _capture(ctx):
            seen_states.append(
                (ctx.harness.started_at, set(ctx.harness.dispatched_event_ids))
            )
            return "ok"

        with patch(
            "billing.qa_console.LiveQAHarness", return_value=harness
        ), patch.dict(
            "billing.qa_console.DEFAULT_ACTIONS", {"add_payment_method": (5, _capture)}
        ):
            response = self._post("qa-console-action", {"action": "add_payment_method"})

        self.assertEqual(response.status_code, 200)
        started_at, dispatched_ids = seen_states[0]
        self.assertEqual(
            started_at,
            12345,
            "the harness's event-poll floor must be the subscriber's "
            "ORIGINAL started_at, not a freshly-recomputed one",
        )
        self.assertEqual(dispatched_ids, {"evt_already_seen"})

    def test_new_subscriber_tears_down_the_previous_one_first(self, _mock_enabled):
        session = self.client.session
        session["qa_console_subscriber"] = {
            "run_id": "old-run",
            "local_user_id": str(self.test_user.id),
            "clock_id": "clock_old",
            "customer_id": "cus_old",
            "stripe_subscription_id": "sub_old",
            "using_failing_card": False,
            "cancelled": False,
            "dispatched_event_ids": [],
        }
        session.save()

        old_harness = self._fake_harness()
        old_harness.cleanup.return_value = []
        with patch(
            "billing.qa_console.LiveQAHarness",
            side_effect=[old_harness, self._fake_harness()],
        ), patch(
            "billing.qa_console._establish_subscriber",
            return_value=self._fake_subscriber(),
        ):
            self._post("qa-console-new-subscriber")

        old_harness.cleanup.assert_called_once()

    # -- state ---------------------------------------------------------

    def test_state_with_no_subscriber_is_null(self, _mock_enabled):
        response = self.client.get(reverse("qa-console-state"))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["subscriber"])

    def test_state_does_not_touch_stripe_unless_drain_is_asked_for(self, _mock_enabled):
        """The plain read is what a manual "Refresh state" click and every
        caller that just wants numbers uses -- it must stay free of network
        calls, or merely looking at the page would cost Stripe requests."""
        self._seed_session()
        harness = self._fake_harness()

        with patch("billing.qa_console.LiveQAHarness", return_value=harness):
            response = self.client.get(reverse("qa-console-state"))

        self.assertEqual(response.status_code, 200)
        harness.drain_events.assert_not_called()
        self.assertEqual(response.json()["drained"], 0)

    def test_drain_flag_dispatches_events_and_reports_the_count(self, _mock_enabled):
        """Auto-refresh polls with ?drain=1 because local state only moves
        when a webhook is DISPATCHED -- a poll that merely re-read the DB
        would sit frozen after a clock advance while Stripe had already
        renewed."""
        self._seed_session()
        harness = self._fake_harness()
        harness.drain_events.return_value = ["evt_a", "evt_b"]

        with patch("billing.qa_console.LiveQAHarness", return_value=harness):
            response = self.client.get(reverse("qa-console-state"), {"drain": "1"})

        self.assertEqual(response.status_code, 200)
        harness.drain_events.assert_called_once_with(customer_id="cus_fake")
        self.assertEqual(response.json()["drained"], 2)

    def test_drained_event_ids_are_persisted_back_to_the_session(self, _mock_enabled):
        """Without this the next poll re-dispatches everything it already
        handled. Downstream claiming makes that harmless rather than
        double-billing, but it is wasted Stripe work on every single tick."""
        self._seed_session()
        harness = self._fake_harness()

        # _build_context RESETS dispatched_event_ids from the session on
        # every request, so seeding the mock up front would be overwritten.
        # The real drain_events ADDS to that set as it dispatches, which is
        # what has to be simulated for this to mean anything.
        def _drain(customer_id):
            harness.dispatched_event_ids.add("evt_new")
            return ["evt_new"]

        harness.drain_events.side_effect = _drain

        with patch("billing.qa_console.LiveQAHarness", return_value=harness):
            self.client.get(reverse("qa-console-state"), {"drain": "1"})

        self.assertEqual(
            self.client.session["qa_console_subscriber"]["dispatched_event_ids"],
            ["evt_new"],
        )

    def test_a_failing_drain_is_reported_in_band_not_as_a_500(self, _mock_enabled):
        """A polling loop must survive one transient Stripe blip. A 500
        here would kill the page's timer and leave it silently frozen --
        the same stale-state symptom auto-refresh exists to prevent."""
        self._seed_session()
        harness = self._fake_harness()
        harness.drain_events.side_effect = RuntimeError("stripe timed out")

        with patch("billing.qa_console.LiveQAHarness", return_value=harness):
            response = self.client.get(reverse("qa-console-state"), {"drain": "1"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("stripe timed out", body["drain_error"])
        self.assertEqual(body["drained"], 0)
        # State still comes back -- a failed drain costs you freshness,
        # not the whole reading.
        self.assertIsNotNone(body["subscriber"])

    # -- action dispatch -----------------------------------------------

    def _seed_session(self):
        session = self.client.session
        session["qa_console_subscriber"] = {
            "run_id": "console-test",
            "local_user_id": str(self.test_user.id),
            "clock_id": "clock_fake",
            "customer_id": "cus_fake",
            "stripe_subscription_id": "sub_fake",
            "using_failing_card": False,
            "cancelled": False,
            "dispatched_event_ids": [],
        }
        session.save()

    def test_action_without_a_subscriber_is_a_400(self, _mock_enabled):
        response = self._post("qa-console-action", {"action": "add_payment_method"})
        self.assertEqual(response.status_code, 400)

    def test_unknown_action_name_is_rejected(self, _mock_enabled):
        self._seed_session()
        with patch(
            "billing.qa_console.LiveQAHarness", return_value=self._fake_harness()
        ):
            response = self._post("qa-console-action", {"action": "not_a_real_action"})
        self.assertEqual(response.status_code, 400)

    def test_known_action_reaches_the_matching_chaos_function(self, _mock_enabled):
        self._seed_session()
        fake_fn = MagicMock(return_value="did the thing")

        with patch(
            "billing.qa_console.LiveQAHarness", return_value=self._fake_harness()
        ), patch.dict(
            "billing.qa_console.DEFAULT_ACTIONS",
            {"add_payment_method": (5, fake_fn)},
        ):
            response = self._post("qa-console-action", {"action": "add_payment_method"})

        self.assertEqual(response.status_code, 200)
        fake_fn.assert_called_once()
        self.assertEqual(response.json()["note"], "did the thing")

    def test_action_that_raises_is_reported_not_crashed(self, _mock_enabled):
        self._seed_session()

        def _boom(ctx):
            raise RuntimeError("stripe exploded")

        with patch(
            "billing.qa_console.LiveQAHarness", return_value=self._fake_harness()
        ), patch.dict(
            "billing.qa_console.DEFAULT_ACTIONS", {"add_payment_method": (5, _boom)}
        ):
            response = self._post("qa-console-action", {"action": "add_payment_method"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("RAISED", response.json()["note"])

    # -- reset -----------------------------------------------------------

    def test_reset_with_no_subscriber_is_a_noop(self, _mock_enabled):
        response = self._post("qa-console-reset")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cleanup_errors"], [])

    def test_reset_clears_the_session_and_calls_cleanup(self, _mock_enabled):
        self._seed_session()
        harness = self._fake_harness()
        harness.cleanup.return_value = []

        with patch("billing.qa_console.LiveQAHarness", return_value=harness):
            response = self._post("qa-console-reset")

        self.assertEqual(response.status_code, 200)
        harness.cleanup.assert_called_once()
        self.assertNotIn("qa_console_subscriber", self.client.session)


@override_settings(ENABLE_STRIPE_LIVE_QA=True, STRIPE_SECRET_KEY=TEST_KEY)
@patch("billing.qa_console.live_qa_enabled", return_value=True)
class QaConsolePageRenderTests(TestCase):
    """The console page is the ONE part of this feature with no other
    automated exercise: every other test calls the JSON endpoints
    directly. A Django template error ({% url %} to a route that moved,
    an unclosed tag) would therefore reach a browser before it reached
    CI. These assert the page renders and still contains the hooks its
    JavaScript addresses by id -- renaming one without updating the
    script silently produces a page whose controls do nothing.
    """

    def setUp(self):
        _make_plans()
        self.admin = _make_user(UserTypes.SUPER_ADMIN, is_superuser=True)
        self.client.force_login(self.admin)

    def test_the_page_renders(self, _mock_enabled):
        response = self.client.get(reverse("qa-console"))
        self.assertEqual(response.status_code, 200)

    def test_it_contains_the_element_ids_its_javascript_drives(self, _mock_enabled):
        html = self.client.get(reverse("qa-console")).content.decode()
        for element_id in (
            "state-sections",
            "by-type-body",
            "by-type-foot",
            "by-type-reconcile",
            "buckets-body",
            "auto-refresh-toggle",
            "auto-refresh-interval",
            "refresh-dot",
            "refresh-label",
            "history-table",
        ):
            self.assertIn('id="' + element_id + '"', html, element_id)

    def test_it_declares_a_viewport_so_it_is_usable_on_a_narrow_screen(
        self, _mock_enabled
    ):
        html = self.client.get(reverse("qa-console")).content.decode()
        self.assertIn('name="viewport"', html)

    def test_every_registered_action_gets_a_button(self, _mock_enabled):
        html = self.client.get(reverse("qa-console")).content.decode()
        for action in DEFAULT_ACTIONS:
            self.assertIn('data-action="' + action + '"', html, action)
