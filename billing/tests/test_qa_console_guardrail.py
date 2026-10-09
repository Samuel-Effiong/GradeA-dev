"""
Tests for billing.qa_console._qa_console_enabled(). It is a thin
delegation to billing.stripe_live_qa.live_qa_enabled() (the same gate
LiveQAHarness itself requires internally, per the module docstring) --
this locks in the delegation itself, not live_qa_enabled()'s own logic,
which is already covered where it is defined.
"""

from unittest.mock import patch

from django.test import TestCase

from billing.qa_console import _qa_console_enabled


class QaConsoleGuardrailTests(TestCase):
    @patch("billing.qa_console.live_qa_enabled", return_value=True)
    def test_enabled_delegates_true(self, mock_enabled):
        self.assertTrue(_qa_console_enabled())
        mock_enabled.assert_called_once_with()

    @patch("billing.qa_console.live_qa_enabled", return_value=False)
    def test_enabled_delegates_false(self, mock_enabled):
        self.assertFalse(_qa_console_enabled())
        mock_enabled.assert_called_once_with()
