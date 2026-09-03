"""
Tests for billing.qa_time_travel._time_travel_enabled(). Locks in that
ENABLE_BILLING_TIME_TRAVEL + a Stripe test key are the ONLY two
conditions gating this endpoint — no DEBUG/ENVIRONMENT dependency.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings

from billing.qa_time_travel import _time_travel_enabled


class TimeTravelGuardrailTests(TestCase):
    @override_settings(ENABLE_BILLING_TIME_TRAVEL=True, DEBUG=False)
    @patch("billing.qa_time_travel.stripe")
    def test_enabled_with_flag_and_test_key_regardless_of_debug(self, mock_stripe):
        mock_stripe.api_key = "sk_test_abc123"

        self.assertTrue(_time_travel_enabled())

    @override_settings(ENABLE_BILLING_TIME_TRAVEL=False)
    @patch("billing.qa_time_travel.stripe")
    def test_disabled_when_flag_is_false(self, mock_stripe):
        mock_stripe.api_key = "sk_test_abc123"

        self.assertFalse(_time_travel_enabled())

    @override_settings(ENABLE_BILLING_TIME_TRAVEL=True)
    @patch("billing.qa_time_travel.stripe")
    def test_disabled_with_live_stripe_key_even_if_flag_is_true(self, mock_stripe):
        mock_stripe.api_key = "sk_live_abc123"

        self.assertFalse(_time_travel_enabled())

    @override_settings(ENABLE_BILLING_TIME_TRAVEL=True)
    @patch("billing.qa_time_travel.stripe")
    def test_disabled_with_no_stripe_key(self, mock_stripe):
        mock_stripe.api_key = ""

        self.assertFalse(_time_travel_enabled())
