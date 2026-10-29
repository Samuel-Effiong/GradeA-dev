"""
billing/tests/test_invoice_lookback_boundary.py
=================================================
_find_new_period_paid_invoice's fallback scan (billing/tasks.py) exists
because "the newest invoice can legitimately be an upgrade proration that
landed after the cycle invoice" — proven for the realistic ONE-distractor
case by test_falls_back_to_listing_when_latest_is_not_the_renewal in
test_renewal_guards.py, and against REAL Stripe by the
invoice_lookback_finds_renewal_behind_a_proration live-QA scenario.

This file documents, rather than fixes, the scan's bounded page size:
it lists at most _INVOICE_LOOKBACK_LIMIT paid invoices and never
paginates further. If _INVOICE_LOOKBACK_LIMIT or more non-qualifying
paid invoices land on a subscription after its renewal invoice but
before the sweep next runs, the renewal invoice falls outside the
window and the sweep silently fails to find it -- exactly the same
class of miss the fallback scan was built to prevent, just past its
horizon.

Proving this against real Stripe would mean manufacturing
_INVOICE_LOOKBACK_LIMIT-plus real paid invoices on a live test-mode
subscription per run; a mock proves the exact boundary deterministically
and far more cheaply, which is why it lives here rather than as a
live-QA scenario.

MOCKING CONVENTION: patch attributes ON the real `stripe` module so
`stripe.error.*` stays a real exception class (matches
test_renewal_guards.py).
"""

from unittest.mock import patch

import stripe as real_stripe
from dateutil.relativedelta import relativedelta
from django.test import TestCase
from django.utils import timezone

from billing.tasks import _INVOICE_LOOKBACK_LIMIT, _find_new_period_paid_invoice

STRIPE_SUB_ID = "sub_lookback_boundary"


def invoice_payload(
    billing_reason="subscription_cycle",
    *,
    invoice_id,
    status="paid",
    period_end=None,
):
    payload = {
        "id": invoice_id,
        "status": status,
        "billing_reason": billing_reason,
        "subscription": STRIPE_SUB_ID,
    }
    if period_end is not None:
        payload["period_end"] = int(period_end.timestamp())
    return payload


class InvoiceLookbackBoundaryTests(TestCase):
    def setUp(self):
        self.cycle_end = timezone.now() - relativedelta(days=1)
        self.renewal = invoice_payload(
            invoice_id="in_renewal",
            period_end=self.cycle_end + relativedelta(months=1),
        )

    def _find(self, listed):
        with patch.object(real_stripe, "Invoice") as mock_invoice:
            mock_invoice.list.return_value = {"data": listed}
            return _find_new_period_paid_invoice(
                STRIPE_SUB_ID, self.cycle_end, latest_invoice=listed[0]
            )

    def test_renewal_just_inside_the_window_is_still_found(self):
        """One below the limit: the renewal invoice is the LAST entry the
        scan looks at, and must still be found."""
        distractors = [
            invoice_payload(
                "subscription_update",
                invoice_id=f"in_distractor_{i}",
                period_end=self.cycle_end + relativedelta(months=1),
            )
            for i in range(_INVOICE_LOOKBACK_LIMIT - 1)
        ]
        listed = distractors + [self.renewal]
        self.assertEqual(len(listed), _INVOICE_LOOKBACK_LIMIT)

        found = self._find(listed)

        self.assertIsNotNone(
            found,
            "the renewal invoice sits exactly at the edge of the lookback "
            "window and must still be found",
        )
        self.assertEqual(found["id"], "in_renewal")

    def test_renewal_pushed_past_the_window_is_a_known_gap(self):
        """KNOWN GAP: one distractor too many, and the scan never reaches
        the real renewal invoice at all — Stripe's own list() call is
        never asked for a second page. Documented here so this is a
        decision, not a surprise discovered from a stuck subscription."""
        distractors = [
            invoice_payload(
                "subscription_update",
                invoice_id=f"in_distractor_{i}",
                period_end=self.cycle_end + relativedelta(months=1),
            )
            for i in range(_INVOICE_LOOKBACK_LIMIT)
        ]
        listed = distractors + [self.renewal]
        self.assertGreater(len(listed), _INVOICE_LOOKBACK_LIMIT)

        # Stripe's list() call itself is bounded by `limit=`, so the real
        # API would never even RETURN the renewal invoice in this
        # scenario -- truncate exactly the way Stripe's own pagination
        # would, rather than relying on the production code to do it.
        found = self._find(listed[:_INVOICE_LOOKBACK_LIMIT])

        self.assertIsNone(
            found,
            "if this ever starts passing, either the lookback limit grew "
            "or pagination was added -- update this test's expectation "
            "deliberately, it did not start passing by accident",
        )
