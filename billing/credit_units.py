"""AI credits for the super admin's analytics (Step 1, owner go 2026-11-25).

The wallet shows AI credits: raw units / CONVERSION_FACTOR. Every analytics
number that is a credit amount goes through display_credits, so every
endpoint converts the same way. Rounding is to the nearest, half up (these
are statistics, not a balance, so not the wallet's round-down): whole credits
for totals and per-user figures, 2 decimals for per-action averages.
Ratios (percentages, the 80% and cap flags) are taken raw over raw and never
pass through here.
"""

from decimal import ROUND_HALF_UP, Decimal

from .models import CONVERSION_FACTOR

#: Places for a per-action average (avg_tokens_*): one action is about 1 to
#: 20 credits, so whole credits would hide the differences.
AVERAGE_PLACES = 2


def display_credits(raw, places=0):
    """`raw` units as AI credits, rounded half up to `places` decimals: an
    int for whole credits, a float otherwise. None counts as 0."""
    value = Decimal(str(raw or 0)) / CONVERSION_FACTOR
    rounded = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return int(rounded) if places == 0 else float(rounded)
