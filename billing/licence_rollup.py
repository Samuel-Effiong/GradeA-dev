"""
The school licence's consumption rollup, applied after the charge commits (H-182).

`LicenseSubscription.total_credits_consumed` is rolled up from each licensed
teacher's charges and refunds. It used to be written INSIDE the charge's
transaction, after the wallet and bucket locks: the opposite of the order the
licence paths take (licence row, then wallets, then buckets), so a charge and
such a path could deadlock. It is now written by a callback that runs after the
outermost transaction commits, in its own statement, holding no wallet or bucket
lock. A charge that rolls back registers nothing, so it rolls up nothing.

LIMIT, STATED: a process that dies between the commit and the callback loses
that one increment silently. The figure's only reader caps a newly enrolled
teacher's first-month grant, so a lost increment errs in the customer's favour
by at most part of one allocation. A callback that FAILS is logged here, with
the licence id and the amount, and never raised into the caller. The exact-once
version is Epic B's proposal.
"""

import logging

from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Greatest
from django.utils import timezone

logger = logging.getLogger(__name__)


def roll_up_after_commit(license_id, delta):
    """Add `delta` (negative for a refund, clamped at zero) to the licence's
    consumption figure once the surrounding transaction commits. With no
    transaction open the callback runs at once."""

    def apply():
        from .models import LicenseSubscription

        try:
            if delta >= 0:
                figure = F("total_credits_consumed") + delta
            else:
                figure = Greatest(F("total_credits_consumed") + delta, Value(0))
            LicenseSubscription.objects.filter(pk=license_id).update(
                total_credits_consumed=figure, updated_at=timezone.now()
            )
        except Exception as exc:  # noqa: BLE001 - never raised into the caller
            # Ids, a number and the error's class only: no name, no address and
            # no exception text (a database error can quote what it was given).
            logger.error(
                "Licence consumption roll-up FAILED after commit: licence %s, "
                "amount %s, error %s. The figure is short by this amount until "
                "its window resets.",
                license_id,
                delta,
                type(exc).__name__,
            )

    transaction.on_commit(apply)
