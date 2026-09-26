"""
Tests for the Stripe Test Clock half of billing/qa_time_travel.py.

THE REGRESSION THESE LOCK
--------------------------
`advance_test_clock_for_subscription` used to anchor its advance on
`stripe_sub["current_period_end"]`. Stripe REMOVED that field from the
top-level Subscription object in API version 2025-03-31 (it lives on each
subscription item now), and this project pins stripe==14.4.1, which is
well past that cutover. So the anchor was always None, silently degrading
to "advance 1 hour from wherever the clock already is" — which never
crosses a period boundary, so Stripe never generates the renewal invoice
and `invoice.payment_succeeded` is never fired. QA still saw a renewal,
because the nightly reconcile_subscription_renewals sweep reads
`latest_invoice` (the PREVIOUS cycle's, still status="paid") and renews
locally. Green result, wrong code path, and a local billing_cycle_end a
month ahead of Stripe's real period.

Two guarantees are locked here:
  1. The boundary is read from items.data[].current_period_end, with
     legacy top-level and trial_end fallbacks.
  2. An advance that would NOT cross a boundary is reported as an
     explicit error with advanced=False — never issued and reported as a
     success.

MOCKING CONVENTION
-------------------
Patch attributes ON the real `stripe` module (`stripe.Subscription`,
`stripe.Customer`, `stripe.test_helpers`) rather than the module itself.
Patching the whole module would replace `stripe.error.StripeError` with a
MagicMock, and `except <MagicMock>` raises TypeError at runtime. Same
convention as test_subscription_upgrade.py / test_subscription_cycle_integrity.py.
"""

from unittest.mock import MagicMock, patch

import stripe as real_stripe
from django.test import SimpleTestCase

from billing.qa_time_travel import QATimeTravelService

HOUR = 3600
DAY = 24 * HOUR

# Fixed, arbitrary "now" for the clock. Nothing here reads the wall clock.
CLOCK_NOW = 1_800_000_000
PERIOD_END = CLOCK_NOW + 30 * DAY


def _sub(
    *,
    customer="cus_test123",
    item_period_ends=(PERIOD_END,),
    top_level_period_end=None,
    trial_end=None,
):
    """
    A Stripe Subscription payload. `item_period_ends=None` omits the
    items block entirely (legacy/expanded shapes); pass a tuple to build
    one item per timestamp.
    """
    payload = {"id": "sub_test123", "customer": customer}
    if item_period_ends is not None:
        payload["items"] = {
            "data": [
                {"id": f"si_{i}", "current_period_end": ts}
                for i, ts in enumerate(item_period_ends)
            ]
        }
    if top_level_period_end is not None:
        payload["current_period_end"] = top_level_period_end
    if trial_end is not None:
        payload["trial_end"] = trial_end
    return payload


def _clock(frozen_time=CLOCK_NOW, status="ready", clock_id="clock_test123"):
    return {"id": clock_id, "frozen_time": frozen_time, "status": status}


class BillingBoundaryExtractionTests(SimpleTestCase):
    """Pure extraction logic — no Stripe calls involved."""

    def test_reads_current_period_end_from_subscription_item(self):
        """The post-2025-03-31 shape. This is the actual bug fix."""
        boundary, source = QATimeTravelService._resolve_billing_boundary(
            _sub(item_period_ends=(PERIOD_END,)), CLOCK_NOW
        )

        self.assertEqual(boundary, PERIOD_END)
        self.assertEqual(source, "items.data[0].current_period_end")

    def test_falls_back_to_legacy_top_level_current_period_end(self):
        """Pre-2025-03-31 shape, in case the stripe pin is ever rolled back."""
        boundary, source = QATimeTravelService._resolve_billing_boundary(
            _sub(item_period_ends=None, top_level_period_end=PERIOD_END), CLOCK_NOW
        )

        self.assertEqual(boundary, PERIOD_END)
        self.assertEqual(source, "current_period_end")

    def test_prefers_item_period_end_when_both_shapes_present(self):
        earlier_item_end = PERIOD_END - DAY
        boundary, source = QATimeTravelService._resolve_billing_boundary(
            _sub(item_period_ends=(earlier_item_end,), top_level_period_end=PERIOD_END),
            CLOCK_NOW,
        )

        self.assertEqual(boundary, earlier_item_end)
        self.assertEqual(source, "items.data[0].current_period_end")

    def test_picks_earliest_future_boundary_across_multiple_items(self):
        """
        A multi-item subscription bills at the EARLIEST boundary, so a
        single advance must target that one and not skip whole cycles.
        """
        boundary, source = QATimeTravelService._resolve_billing_boundary(
            _sub(item_period_ends=(PERIOD_END + 10 * DAY, PERIOD_END)), CLOCK_NOW
        )

        self.assertEqual(boundary, PERIOD_END)
        self.assertEqual(source, "items.data[1].current_period_end")

    def test_ignores_item_boundaries_already_behind_the_clock(self):
        stale, upcoming = CLOCK_NOW - DAY, CLOCK_NOW + DAY
        boundary, _ = QATimeTravelService._resolve_billing_boundary(
            _sub(item_period_ends=(stale, upcoming)), CLOCK_NOW
        )

        self.assertEqual(boundary, upcoming)

    def test_trial_end_is_used_when_it_precedes_the_period_end(self):
        """mode='trial_expiry' needs the clock to cross trial_end."""
        trial_end = CLOCK_NOW + 3 * DAY
        boundary, source = QATimeTravelService._resolve_billing_boundary(
            _sub(item_period_ends=(PERIOD_END,), trial_end=trial_end), CLOCK_NOW
        )

        self.assertEqual(boundary, trial_end)
        self.assertEqual(source, "trial_end")

    def test_returns_none_when_no_boundary_present_anywhere(self):
        boundary, source = QATimeTravelService._resolve_billing_boundary(
            _sub(item_period_ends=None), CLOCK_NOW
        )

        self.assertIsNone(boundary)
        self.assertIsNone(source)

    def test_returns_latest_boundary_when_all_are_behind_the_clock(self):
        """Caller compares against frozen_time and reports 'already past'."""
        older, newer = CLOCK_NOW - 10 * DAY, CLOCK_NOW - DAY
        boundary, _ = QATimeTravelService._resolve_billing_boundary(
            _sub(item_period_ends=(older, newer)), CLOCK_NOW
        )

        self.assertEqual(boundary, newer)

    def test_ignores_malformed_and_placeholder_timestamps(self):
        """
        None/0/strings/bools/MagicMocks must count as absent rather than
        be trusted — a bogus anchor is precisely how the original defect
        stayed invisible.
        """
        for junk in (None, 0, -1, "1800000000", True, False, MagicMock()):
            with self.subTest(junk=junk):
                self.assertIsNone(QATimeTravelService._coerce_timestamp(junk))

        boundary, _ = QATimeTravelService._resolve_billing_boundary(
            {
                "items": {"data": [{"current_period_end": None}]},
                "current_period_end": 0,
            },
            CLOCK_NOW,
        )
        self.assertIsNone(boundary)

    def test_tolerates_attribute_style_and_malformed_payloads(self):
        """Stripe objects support attribute access; nothing may raise."""

        class AttrSub:
            customer = "cus_1"
            items = {"data": [{"current_period_end": PERIOD_END}]}

        boundary, _ = QATimeTravelService._resolve_billing_boundary(
            AttrSub(), CLOCK_NOW
        )
        self.assertEqual(boundary, PERIOD_END)

        for malformed in (
            {},
            {"items": None},
            {"items": {"data": None}},
            {"items": {"data": 5}},
            {"items": {}},
        ):
            with self.subTest(malformed=malformed):
                self.assertEqual(
                    QATimeTravelService._resolve_billing_boundary(malformed, CLOCK_NOW),
                    (None, None),
                )


@patch.object(real_stripe, "test_helpers")
@patch.object(real_stripe, "Customer")
@patch.object(real_stripe, "Subscription")
class AdvanceTestClockTests(SimpleTestCase):
    """The advance path end to end, with Stripe stubbed."""

    def _wire(
        self,
        mock_sub,
        mock_customer,
        mock_helpers,
        *,
        subscription=None,
        clock=None,
        test_clock_id="clock_test123",
    ):
        mock_sub.retrieve.return_value = (
            subscription if subscription is not None else _sub()
        )
        mock_customer.retrieve.return_value = {
            "id": "cus_test123",
            "test_clock": test_clock_id,
        }
        current = clock if clock is not None else _clock()
        mock_helpers.TestClock.retrieve.return_value = current
        mock_helpers.TestClock.advance.return_value = {
            **current,
            "status": "advancing",
            "frozen_time": PERIOD_END + HOUR,
        }
        return mock_helpers

    # -- the fix -------------------------------------------------------

    def test_advances_one_hour_past_item_period_end(
        self, mock_sub, mock_customer, mock_helpers
    ):
        """
        The regression test. Target must be period_end + 1h — roughly a
        month of movement — not frozen_time + 1h.
        """
        self._wire(mock_sub, mock_customer, mock_helpers)

        result = QATimeTravelService.advance_test_clock_for_subscription("sub_test123")

        mock_helpers.TestClock.advance.assert_called_once_with(
            "clock_test123", frozen_time=PERIOD_END + HOUR
        )
        self.assertTrue(result["advanced"])
        self.assertTrue(result["crossed_billing_boundary"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["billing_boundary"], PERIOD_END)
        self.assertEqual(
            result["billing_boundary_source"], "items.data[0].current_period_end"
        )
        self.assertEqual(result["target_frozen_time"], PERIOD_END + HOUR)
        self.assertEqual(result["previous_frozen_time"], CLOCK_NOW)
        # Distance actually moved is reported, and it clears the boundary.
        self.assertEqual(result["advanced_seconds"], (PERIOD_END + HOUR) - CLOCK_NOW)
        self.assertGreater(result["advanced_seconds"], 29 * DAY)

    def test_never_issues_a_short_advance_that_misses_the_boundary(
        self, mock_sub, mock_customer, mock_helpers
    ):
        """
        The old failure mode: no boundary determinable. It must now be an
        explicit error with NO advance issued, rather than a 1-hour hop
        reported as advanced=True.
        """
        self._wire(
            mock_sub,
            mock_customer,
            mock_helpers,
            subscription=_sub(item_period_ends=None),
        )

        result = QATimeTravelService.advance_test_clock_for_subscription("sub_test123")

        mock_helpers.TestClock.advance.assert_not_called()
        self.assertFalse(result["advanced"])
        self.assertFalse(result["crossed_billing_boundary"])
        self.assertIn("Could not determine the next billing boundary", result["error"])
        self.assertTrue(result["attempted"])

    def test_errors_when_clock_already_past_the_boundary(
        self, mock_sub, mock_customer, mock_helpers
    ):
        """Advancing again cannot produce a second renewal invoice."""
        self._wire(
            mock_sub,
            mock_customer,
            mock_helpers,
            subscription=_sub(item_period_ends=(CLOCK_NOW - DAY,)),
        )

        result = QATimeTravelService.advance_test_clock_for_subscription("sub_test123")

        mock_helpers.TestClock.advance.assert_not_called()
        self.assertFalse(result["advanced"])
        self.assertFalse(result["crossed_billing_boundary"])
        self.assertIn("already at or past", result["error"])

    def test_errors_when_clock_reports_no_usable_frozen_time(
        self, mock_sub, mock_customer, mock_helpers
    ):
        self._wire(
            mock_sub, mock_customer, mock_helpers, clock=_clock(frozen_time=None)
        )

        result = QATimeTravelService.advance_test_clock_for_subscription("sub_test123")

        mock_helpers.TestClock.advance.assert_not_called()
        self.assertFalse(result["advanced"])
        self.assertIn("no usable frozen_time", result["error"])

    def test_advances_to_trial_end_for_a_trialing_subscription(
        self, mock_sub, mock_customer, mock_helpers
    ):
        trial_end = CLOCK_NOW + 3 * DAY
        self._wire(
            mock_sub,
            mock_customer,
            mock_helpers,
            subscription=_sub(item_period_ends=(PERIOD_END,), trial_end=trial_end),
        )

        result = QATimeTravelService.advance_test_clock_for_subscription("sub_test123")

        mock_helpers.TestClock.advance.assert_called_once_with(
            "clock_test123", frozen_time=trial_end + HOUR
        )
        self.assertEqual(result["billing_boundary_source"], "trial_end")

    def test_legacy_top_level_shape_still_advances(
        self, mock_sub, mock_customer, mock_helpers
    ):
        self._wire(
            mock_sub,
            mock_customer,
            mock_helpers,
            subscription=_sub(item_period_ends=None, top_level_period_end=PERIOD_END),
        )

        result = QATimeTravelService.advance_test_clock_for_subscription("sub_test123")

        mock_helpers.TestClock.advance.assert_called_once_with(
            "clock_test123", frozen_time=PERIOD_END + HOUR
        )
        self.assertTrue(result["crossed_billing_boundary"])

    # -- preserved behaviour -------------------------------------------

    def test_no_subscription_id_is_skipped_without_calling_stripe(
        self, mock_sub, mock_customer, mock_helpers
    ):
        result = QATimeTravelService.advance_test_clock_for_subscription(None)

        mock_sub.retrieve.assert_not_called()
        self.assertFalse(result["attempted"])
        self.assertIn("No stripe_subscription_id", result["note"])

    def test_customer_without_test_clock_is_a_note_not_an_error(
        self, mock_sub, mock_customer, mock_helpers
    ):
        self._wire(mock_sub, mock_customer, mock_helpers, test_clock_id=None)

        result = QATimeTravelService.advance_test_clock_for_subscription("sub_test123")

        mock_helpers.TestClock.advance.assert_not_called()
        self.assertFalse(result["advanced"])
        self.assertIn("not attached to a Test Clock", result["note"])

    def test_subscription_without_customer_is_skipped(
        self, mock_sub, mock_customer, mock_helpers
    ):
        self._wire(
            mock_sub, mock_customer, mock_helpers, subscription=_sub(customer=None)
        )

        result = QATimeTravelService.advance_test_clock_for_subscription("sub_test123")

        self.assertFalse(result["advanced"])
        self.assertIn("no customer", result["note"])

    def test_already_advancing_clock_is_rejected(
        self, mock_sub, mock_customer, mock_helpers
    ):
        """Stripe forbids overlapping advances."""
        self._wire(
            mock_sub, mock_customer, mock_helpers, clock=_clock(status="advancing")
        )

        result = QATimeTravelService.advance_test_clock_for_subscription("sub_test123")

        mock_helpers.TestClock.advance.assert_not_called()
        self.assertIn("already advancing", result["error"])
        self.assertEqual(result["previous_status"], "advancing")

    def test_stripe_errors_are_captured_never_raised(
        self, mock_sub, mock_customer, mock_helpers
    ):
        mock_sub.retrieve.side_effect = real_stripe.error.InvalidRequestError(
            "No such subscription", param="id"
        )

        result = QATimeTravelService.advance_test_clock_for_subscription("sub_test123")

        self.assertFalse(result["advanced"])
        self.assertIn("Stripe error", result["error"])

    def test_unexpected_errors_are_captured_never_raised(
        self, mock_sub, mock_customer, mock_helpers
    ):
        mock_sub.retrieve.side_effect = RuntimeError("kaboom")

        result = QATimeTravelService.advance_test_clock_for_subscription("sub_test123")

        self.assertFalse(result["advanced"])
        self.assertIn("Unexpected error", result["error"])

    @patch("billing.qa_time_travel.time.sleep")
    def test_wait_for_ready_polls_and_reports_observed_position(
        self, mock_sleep, mock_sub, mock_customer, mock_helpers
    ):
        self._wire(mock_sub, mock_customer, mock_helpers)
        mock_helpers.TestClock.retrieve.side_effect = [
            _clock(),  # pre-advance read
            {"status": "advancing", "frozen_time": CLOCK_NOW},
            {"status": "ready", "frozen_time": PERIOD_END + HOUR},
        ]

        result = QATimeTravelService.advance_test_clock_for_subscription(
            "sub_test123", wait_for_ready=True
        )

        self.assertTrue(result["waited"])
        self.assertEqual(result["new_status"], "ready")
        self.assertEqual(result["observed_frozen_time"], PERIOD_END + HOUR)
        # Observed position confirms the boundary really was crossed.
        self.assertGreater(result["observed_frozen_time"], result["billing_boundary"])


class SimulationWarningTests(SimpleTestCase):
    """
    The response-level guard: a local rewind with no Stripe boundary
    crossing must never read as a clean success, because the renewal QA
    then observes comes from the Celery fallback sweep.
    """

    APPLICABLE = {"stripe_advancement_applicable": True}

    def test_no_warning_when_boundary_crossed(self):
        warnings = QATimeTravelService.build_simulation_warnings(
            self.APPLICABLE, {"crossed_billing_boundary": True}
        )

        self.assertEqual(warnings, [])

    def test_warns_when_boundary_not_crossed(self):
        warnings = QATimeTravelService.build_simulation_warnings(
            self.APPLICABLE,
            {"crossed_billing_boundary": False, "error": "no boundary found"},
        )

        self.assertEqual(len(warnings), 1)
        self.assertIn("did NOT cross a billing boundary", warnings[0])
        self.assertIn("Celery fallback sweep", warnings[0])
        self.assertIn("no boundary found", warnings[0])

    def test_warns_when_clock_advancement_was_declined(self):
        warnings = QATimeTravelService.build_simulation_warnings(self.APPLICABLE, None)

        self.assertEqual(len(warnings), 1)
        self.assertIn("advance_stripe_test_clock=false", warnings[0])

    def test_silent_for_local_only_modes(self):
        """
        mid_cycle_grant is Celery-driven by design — no Stripe signal
        exists at any clock position, so there is nothing to warn about.
        """
        warnings = QATimeTravelService.build_simulation_warnings(
            {"stripe_advancement_applicable": False}, None
        )

        self.assertEqual(warnings, [])
