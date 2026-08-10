"""
billing/live_qa/harness.py
==========================
LiveQAHarness with its event draining rewired through the shared bus.

The seam is deliberately narrow: only create_customer (to register the
customer with the bus) and drain_events (to read from its queue instead
of polling Stripe directly) are overridden. Everything else — clock
creation, card attachment, subscription creation, local user setup, and
crucially CLEANUP — is inherited unchanged.

That is what lets the existing five scenarios run concurrently with no
modification at all: they call harness.drain_events(customer_id=...) and
get the same [(event_type, status_code), ...] back, without knowing a bus
exists.

The single-threaded LiveQAHarness stays the supported path for
--tier smoke and for the existing unit tests, so this adds a mode rather
than replacing one.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from billing.stripe_live_qa import LiveQAHarness, LiveQAInfrastructureError

from .events import EventBus

logger = logging.getLogger(__name__)


class ConcurrentLiveQAHarness(LiveQAHarness):
    """Shared by every actor in a run.

    One harness, one bus, one poller — because cleanup must be able to
    destroy everything the run created regardless of which worker created
    it, and because the Stripe event stream is a single account-wide
    resource.

    Thread-safety: the inherited tracking containers are a list and a set,
    and `list.append` / `set.add` are atomic under CPython's GIL, so the
    hot paths need no locking. The one place that does a read-modify-write
    (merging a stream's dispatched ids) takes an explicit lock.
    """

    def __init__(self, run_id: Optional[str] = None, bus: Optional[EventBus] = None):
        super().__init__(run_id=run_id)
        self.bus = bus or EventBus(log_prefix=f"[LIVE QA {self.run_id}]")
        self._merge_lock = threading.Lock()

    def create_customer(self, *, email: str, clock_id: str):
        customer = super().create_customer(email=email, clock_id=clock_id)
        # Register BEFORE the caller can create a subscription, so no
        # event this customer generates is ever seen as foreign.
        self.bus.register(customer["id"])
        return customer

    def drain_events(self, *, customer_id: str) -> list:
        stream = self.bus.stream_for(customer_id)
        if stream is None:
            raise LiveQAInfrastructureError(
                f"customer {customer_id} was never registered with the event "
                "bus, so its events are being discarded as foreign. It was "
                "probably created outside harness.create_customer()."
            )

        dispatched = stream.drain()

        # Merge into the inherited set so the inherited cleanup() deletes
        # this run's StripeEvent ledger rows. Those must go: a QA event
        # left FAILED makes sweep_stale_stripe_events report "a customer
        # may have paid without receiving anything" three days later —
        # about a customer who never existed.
        with self._merge_lock:
            self.dispatched_event_ids |= stream.dispatched_event_ids

        return dispatched

    def cleanup(self) -> list:
        # Catch ids from any stream whose drain never ran (a scenario that
        # raised part-way through), so cleanup is complete even on the
        # failure path.
        with self._merge_lock:
            self.dispatched_event_ids |= self.bus.all_dispatched_event_ids()
        return super().cleanup()
