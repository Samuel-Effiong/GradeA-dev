"""
PRE-FIX REPRODUCTION (G1) of the intermittent
ConcurrentOverageDeliveryTests.test_concurrent_purchases_by_different_teachers_stay_separate
failure "0 != 500".

Not named test_*.py, so the suite never discovers it; run it by label:

    python manage.py test billing.tests.repro_overage_concurrency_flake \
        --settings=settings_worktree

Mechanism under test: each worker thread calls resolve_stripe_receipt_url
INSIDE handle_checkout_completed's transaction, after the grant is written
but before commit. _run() joins with timeout=60 and never checks whether
the thread finished. When one live Stripe lookup outlasts the join timeout
the assertions run against a wallet whose grant is still uncommitted.

This reproduction makes that deterministic without changing the test body:
threads 0-2 get an instant lookup, thread 3's lookup blocks longer than the
join timeout (the live call measured up to 91 s on 2026-09-17).
"""

import threading
from unittest.mock import patch

from billing.tests.test_overage_purchase_integrity import ConcurrentOverageDeliveryTests

SLOW_PAYMENT_INTENT = "pi_multi_3"
SLOW_LOOKUP_SECONDS = 75  # > the 60 s join timeout in _run()


class ReproSlowReceiptLookup(ConcurrentOverageDeliveryTests):
    def setUp(self):
        super().setUp()
        release = threading.Event()

        def lookup(*, invoice_id=None, payment_intent_id=None, **_):
            if payment_intent_id == SLOW_PAYMENT_INTENT:
                release.wait(SLOW_LOOKUP_SECONDS)
            return None

        patcher = patch(
            "billing.stripe_service.resolve_stripe_receipt_url", side_effect=lookup
        )
        patcher.start()
        # Cleanups run before TransactionTestCase flushes the tables, so the
        # slow thread commits and closes its connection before the flush.
        self.addCleanup(patcher.stop)
        self.addCleanup(release.set)


# Only the target test is reproduced; the inherited ones are not re-run here.
for _name in [
    "test_simultaneous_duplicate_deliveries_grant_exactly_once",
    "test_two_genuinely_different_purchases_both_land",
    "test_concurrent_purchases_cannot_exceed_the_cap",
]:
    setattr(ReproSlowReceiptLookup, _name, None)
del ConcurrentOverageDeliveryTests
