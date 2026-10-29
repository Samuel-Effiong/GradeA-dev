"""
H-256: the Stripe SDK's network retries are set by us, to 2, where the key is
set (`billing/imports.py`), instead of left to the SDK's default (2 in stripe
14.4.1, 0 in the 2.x line). The SDK's own timeout is kept: its default HTTP
client (`stripe._http_client.RequestsClient`) waits 80 s per request.

Run with:
    python manage.py test billing.tests.test_h256_stripe_network_retries
"""

import importlib
from unittest.mock import patch

import stripe
from django.test import SimpleTestCase

import billing.imports


class StripeNetworkRetriesTests(SimpleTestCase):
    def test_the_retries_are_ours_not_the_sdks_default(self):
        # A different SDK default, then the module that configures Stripe
        # loaded again: the number must come from the module.
        with patch.object(stripe, "max_network_retries", 0):
            importlib.reload(billing.imports)
            self.assertEqual(stripe.max_network_retries, 2)
        importlib.reload(billing.imports)

    def test_the_sdks_timeout_is_80_seconds(self):
        """A control: the timeout is the SDK's own, recorded in the row."""
        client = stripe.RequestsClient()
        self.assertEqual(client._timeout, 80)
