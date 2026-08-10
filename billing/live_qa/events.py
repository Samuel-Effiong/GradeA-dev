"""
billing/live_qa/events.py
=========================
One shared poller over the Stripe account event stream, fanned out to
per-customer queues.

WHY A SHARED POLLER
-------------------
Stripe's Event API cannot filter by customer. The single-threaded harness
therefore lists account events and filters client-side, which is fine for
one actor and quadratic-ish for twelve: each would independently pull the
same pages, burning rate limit to discard 11/12 of what it fetched.

One poller pulls the stream once and routes each event to the actor that
owns it. That is by far the largest rate-limit saving in the whole
design.

WHY DISPATCH HAPPENS ON THE WORKER, NOT THE POLLER
--------------------------------------------------
CustomerEventStream.drain() runs webhooks._record_and_dispatch on the
CALLING thread. Two reasons, both load-bearing:

  * It makes claim contention real. Several workers dispatching
    concurrently is precisely the condition the C3 idempotency ledger
    exists to survive, and nothing else in the codebase exercises it
    against real Stripe payloads.
  * A poller that dispatches is a poller that blocks. Handlers take
    database locks; one slow handler would stall event delivery for every
    other actor.

CURSOR, NOT A PAGE CAP
----------------------
The single-threaded harness scans a fixed number of pages and logs a
warning if it hits the cap. With a live cursor that is no longer a
warning-level event: hitting the cap means we genuinely fell behind the
stream and have lost events, so it raises LiveQAInfrastructureError. A
suite that silently misses events would report success it cannot vouch
for — the same failure C3 was about.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from billing.imports import stripe
from billing.stripe_live_qa import (
    LiveQAInfrastructureError,
    _event_customer_id,
    guarded_call,
)

logger = logging.getLogger(__name__)


@dataclass
class EventBusStats:
    polls: int = 0
    events_seen: int = 0
    events_routed: int = 0
    foreign_events_seen: int = 0
    max_pages_used: int = 0
    dispatched: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "polls": self.polls,
            "events_seen": self.events_seen,
            "events_routed": self.events_routed,
            # A nonzero count means another run (or another engineer) is
            # active on the same Stripe test account. That is the honest
            # signal that results may be affected by contention.
            "foreign_events_seen": self.foreign_events_seen,
            "max_pages_used": self.max_pages_used,
            "dispatched": dict(self.dispatched),
        }


class CustomerEventStream:
    """One actor's slice of the account event stream."""

    DRAIN_TIMEOUT = 120
    QUIET_POLLS = 2
    POLL_INTERVAL = 2

    def __init__(self, customer_id: str, bus: "EventBus", log_prefix: str):
        self.customer_id = customer_id
        self.bus = bus
        self.log_prefix = log_prefix
        self.queue: queue.Queue = queue.Queue()
        self.dispatched_event_ids: set = set()

    def put(self, event) -> None:
        self.queue.put(event)

    def drain(
        self,
        *,
        timeout: Optional[float] = None,
        quiet_polls: Optional[int] = None,
    ) -> list:
        """
        Dispatch everything currently queued for this customer, then wait
        for the stream to go quiet.

        Returns [(event_type, http_status), ...] — the same shape the
        single-threaded harness returns, so scenarios need no changes.
        """
        from billing.webhooks import _record_and_dispatch

        timeout = self.DRAIN_TIMEOUT if timeout is None else timeout
        quiet_polls = self.QUIET_POLLS if quiet_polls is None else quiet_polls

        deadline = time.monotonic() + timeout
        dispatched: list = []
        consecutive_quiet = 0

        while time.monotonic() < deadline and consecutive_quiet < quiet_polls:
            # A dead poller must fail fast and LOUDLY. Otherwise every
            # actor blocks here until timeout and the run degrades into a
            # slow failure that looks exactly like a billing bug.
            self.bus.raise_if_unhealthy()

            drained_any = False
            while True:
                try:
                    event = self.queue.get_nowait()
                except queue.Empty:
                    break

                drained_any = True
                event_id = event["id"]
                if event_id in self.dispatched_event_ids:
                    continue
                # Mark BEFORE dispatching: if the handler raises, the
                # event must not be retried by the next poll. The ledger
                # row already records the failure, and a silent retry
                # loop here would mask it.
                self.dispatched_event_ids.add(event_id)

                response = _record_and_dispatch(event, log_prefix=self.log_prefix)
                dispatched.append((event["type"], response.status_code))
                self.bus.stats.dispatched[event["type"]] = (
                    self.bus.stats.dispatched.get(event["type"], 0) + 1
                )
                logger.info(
                    "%s dispatched %s (%s) -> %s",
                    self.log_prefix,
                    event_id,
                    event["type"],
                    response.status_code,
                )

            consecutive_quiet = 0 if drained_any else consecutive_quiet + 1
            if consecutive_quiet < quiet_polls:
                time.sleep(self.POLL_INTERVAL)

        return dispatched


class EventBus:
    """Routes account events to per-customer streams."""

    def __init__(self, log_prefix: str = "[LIVE QA]"):
        self.log_prefix = log_prefix
        self._streams: dict = {}
        self._lock = threading.Lock()
        self.stats = EventBusStats()
        self._failure: Optional[BaseException] = None

    def register(self, customer_id: str) -> CustomerEventStream:
        with self._lock:
            stream = self._streams.get(customer_id)
            if stream is None:
                stream = CustomerEventStream(customer_id, self, self.log_prefix)
                self._streams[customer_id] = stream
            return stream

    def unregister(self, customer_id: str) -> None:
        with self._lock:
            self._streams.pop(customer_id, None)

    def stream_for(self, customer_id: str) -> Optional[CustomerEventStream]:
        with self._lock:
            return self._streams.get(customer_id)

    def all_dispatched_event_ids(self) -> set:
        with self._lock:
            ids: set = set()
            for stream in self._streams.values():
                ids |= stream.dispatched_event_ids
            return ids

    def publish(self, events) -> None:
        for event in events:
            self.stats.events_seen += 1
            customer_id = _event_customer_id(event)
            with self._lock:
                stream = self._streams.get(customer_id) if customer_id else None
            if stream is None:
                self.stats.foreign_events_seen += 1
                continue
            self.stats.events_routed += 1
            stream.put(event)

    # -- health ----------------------------------------------------------

    def mark_failed(self, exc: BaseException) -> None:
        self._failure = exc

    @property
    def healthy(self) -> bool:
        return self._failure is None

    def raise_if_unhealthy(self) -> None:
        if self._failure is not None:
            raise LiveQAInfrastructureError(
                "the Stripe event poller died, so no further events can be "
                f"delivered: {self._failure!r}"
            ) from self._failure


class AccountEventPoller(threading.Thread):
    """
    Single background reader of the Stripe account event stream.

    Never dispatches and never touches the database — it only fetches and
    routes, so it cannot be blocked by a slow handler.
    """

    POLL_INTERVAL = 2
    MAX_PAGES = 20
    # Re-scan window when advancing the cursor floor. Generous because a
    # missed event is unrecoverable while a re-seen one is free (the
    # seen-id set discards it).
    OVERLAP_SECONDS = 300

    def __init__(
        self,
        bus: EventBus,
        *,
        created_floor: int,
        poll_interval: Optional[float] = None,
        max_pages: Optional[int] = None,
    ):
        super().__init__(name="liveqa-event-poller", daemon=True)
        self.bus = bus
        self.created_floor = int(created_floor)
        self.poll_interval = (
            self.POLL_INTERVAL if poll_interval is None else poll_interval
        )
        self.max_pages = self.MAX_PAGES if max_pages is None else max_pages
        self.seen_ids: set = set()
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except BaseException as exc:  # noqa: BLE001
                logger.exception("[LIVE QA] event poller failed.")
                self.bus.mark_failed(exc)
                return
            self._stop_event.wait(self.poll_interval)

    def poll_once(self) -> list:
        """Fetch everything new and publish it oldest-first."""
        collected = self._fetch_new_events()
        if collected:
            # Ascending, with the id as a deterministic tiebreak for
            # same-second events. Handlers assume they see
            # customer.subscription.created before the invoice that
            # follows it, and Stripe returns newest-first.
            collected.sort(key=lambda e: (e.get("created") or 0, e["id"]))
            newest = collected[-1].get("created")
            if newest:
                self.created_floor = max(
                    self.created_floor, int(newest) - self.OVERLAP_SECONDS
                )
            self.bus.publish(collected)
        self.bus.stats.polls += 1
        return collected

    def _fetch_new_events(self) -> list:
        collected: list = []
        starting_after = None
        pages = 0
        caught_up = False

        while pages < self.max_pages:
            params: dict = {"created": {"gte": self.created_floor}, "limit": 100}
            if starting_after:
                params["starting_after"] = starting_after

            page = guarded_call(stripe.Event.list, **params)
            data = page.get("data") or []
            pages += 1

            if not data:
                caught_up = True
                break

            for event in data:
                if event["id"] in self.seen_ids:
                    # The list is newest-first, so the first already-seen
                    # id means everything below it is seen too.
                    caught_up = True
                    break
                self.seen_ids.add(event["id"])
                collected.append(event)

            if caught_up or not page.get("has_more"):
                caught_up = True
                break

            starting_after = data[-1]["id"]

        self.bus.stats.max_pages_used = max(self.bus.stats.max_pages_used, pages)

        if not caught_up:
            raise LiveQAInfrastructureError(
                f"the event cursor fell behind: {self.max_pages} pages scanned "
                "without catching up to previously-seen events. Events have "
                "been missed, so any result from this run is unreliable. The "
                "usual cause is another run sharing this Stripe test account."
            )

        return collected
