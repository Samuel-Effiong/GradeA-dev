"""POST billing/estimate answers in display credits (owner, 2026-11-18).

The wallet shows display credits (raw / CONVERSION_FACTOR, floored), so the
estimate does too. Costs round up (p50 and p90 never under-promise), the
spendable balance rounds down (CreditWallet.display_balance's rule), the
projected balances are display minus display, and the verdict is worked out
from the display numbers, so SUFFICIENT never sits next to a negative
projected balance. estimate() and the backtest stay raw.

    python manage.py test billing.tests.test_estimate_display_credits
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from billing import estimates


def advise_with(p50, p90, spendable):
    """advise() for a raw band and a raw spendable balance."""
    raw = {
        "p50": p50,
        "p90": p90,
        "source": "assignment",
        "shape_basis": "x",
        "sample_size": 10,
    }
    with patch.object(estimates, "estimate", return_value=raw), patch.object(
        estimates, "spendable_of", return_value=spendable
    ):
        return estimates.advise(SimpleNamespace(pk=None), None, 3)


def shown(result):
    keys = (
        "p50",
        "p90",
        "spendable",
        "projected_remaining_p50",
        "projected_remaining_p90",
        "verdict",
    )
    return {key: result[key] for key in keys}


class DisplayCreditsTests(SimpleTestCase):
    def test_the_live_beta_example(self):
        self.assertEqual(
            shown(advise_with(86_508, 120_000, 256_570_839)),
            {
                "p50": 87,
                "p90": 120,
                "spendable": 256_570,
                "projected_remaining_p50": 256_483,
                "projected_remaining_p90": 256_450,
                "verdict": "SUFFICIENT",
            },
        )

    def test_a_one_raw_cost_shows_as_one(self):
        result = advise_with(1, 1, 5_000)
        self.assertEqual((result["p50"], result["p90"]), (1, 1))

    def test_999_raw_spendable_shows_as_zero_and_is_insufficient(self):
        self.assertEqual(
            shown(advise_with(1, 1, 999)),
            {
                "p50": 1,
                "p90": 1,
                "spendable": 0,
                "projected_remaining_p50": -1,
                "projected_remaining_p90": -1,
                "verdict": "INSUFFICIENT",
            },
        )

    def test_the_verdict_at_the_p90_display_boundary(self):
        # p90 9001 raw shows as 10. 9500 raw covers it raw but shows as 9.
        below = advise_with(4_001, 9_001, 9_500)
        self.assertEqual(below["verdict"], "MARGINAL")
        self.assertEqual(below["projected_remaining_p90"], -1)
        at = advise_with(4_001, 9_001, 10_000)
        self.assertEqual(at["verdict"], "SUFFICIENT")
        self.assertEqual(at["projected_remaining_p90"], 0)

    def test_the_verdict_at_the_p50_display_boundary(self):
        # p50 4001 raw shows as 5. 4999 raw covers it raw but shows as 4.
        below = advise_with(4_001, 9_001, 4_999)
        self.assertEqual(below["verdict"], "INSUFFICIENT")
        self.assertEqual(below["projected_remaining_p50"], -1)
        at = advise_with(4_001, 9_001, 5_000)
        self.assertEqual(at["verdict"], "MARGINAL")
        self.assertEqual(at["projected_remaining_p50"], 0)

    def test_a_zero_balance_is_insufficient(self):
        result = advise_with(300, 450, 0)
        self.assertEqual(result["spendable"], 0)
        self.assertEqual(result["verdict"], "INSUFFICIENT")

    def test_the_shape_is_unchanged(self):
        self.assertEqual(
            set(advise_with(1, 2, 3)),
            {
                "p50",
                "p90",
                "verdict",
                "spendable",
                "projected_remaining_p50",
                "projected_remaining_p90",
                "source",
                "shape_basis",
                "sample_size",
                "n",
            },
        )
