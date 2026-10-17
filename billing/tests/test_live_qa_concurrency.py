"""
billing/tests/test_live_qa_concurrency.py
=========================================
Tests for the concurrency and event-routing layer — all mocked, no
network.

WHAT IS BEING LOCKED DOWN, AND WHY THESE THINGS
------------------------------------------------
Concurrency here is not a performance optimisation; it is what makes a
ten-year simulation possible at all. But it introduces failure modes that
are far more dangerous than slowness, because they produce WRONG RESULTS
rather than obvious breakage:

  * A poller that dies silently would leave every actor blocked, and the
    run would look like a billing failure instead of an infrastructure
    one. Hence the health flag and fail-fast drain.
  * A cursor that falls behind would silently miss events, so the suite
    would report a success it cannot vouch for — the exact class of
    problem C3 was about. Hence the hard error rather than a warning.
  * Events delivered out of order would break handlers that assume
    subscription.created precedes the invoice following it. Stripe
    returns newest-first, so ordering is something we impose.
  * A worker leaking database connections exhausts Postgres partway
    through a six-hour run.

None of that needs Stripe to verify, and all of it would be expensive to
discover during a real run.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings

from billing.live_qa.concurrency import (
    Deadline,
    LiveQAWorkerPool,
    StripeRateLimiter,
    worker_db_connections,
)
from billing.live_qa.events import AccountEventPoller, EventBus
from billing.live_qa.harness import ConcurrentLiveQAHarness
from billing.stripe_live_qa import (
    LiveQAInfrastructureError,
    LiveQARefused,
    guarded_call,
    set_rate_limiter,
)

TEST_KEY = "sk_test_fake"  # pragma: allowlist secret
LIVE_KEY = "sk_live_fake"  # pragma: allowlist secret


def enabled(**extra):
    options = {"ENABLE_STRIPE_LIVE_QA": True, "STRIPE_SECRET_KEY": TEST_KEY}
    options.update(extra)
    return override_settings(**options)


def make_event(event_id, customer, created, event_type="invoice.payment_succeeded"):
    return {
        "id": event_id,
        "type": event_type,
        "created": created,
        "data": {"object": {"customer": customer}},
    }


def page(events, has_more=False):
    return {"data": events, "has_more": has_more}


# --------------------------------------------------------------------------
# Rate limiter
# --------------------------------------------------------------------------


class StripeRateLimiterTests(TestCase):
    def test_burst_is_available_immediately(self):
        limiter = StripeRateLimiter(rate_per_second=1, burst=5)
        for _ in range(5):
            limiter.acquire()
        self.assertEqual(limiter.acquisitions, 5)
        self.assertLess(limiter.waited_seconds, 0.01)

    def test_throttles_beyond_the_burst(self):
        limiter = StripeRateLimiter(rate_per_second=50, burst=1)
        for _ in range(4):
            limiter.acquire()
        # 3 tokens beyond burst at 50/s == ~0.06s of enforced waiting.
        self.assertGreater(limiter.waited_seconds, 0.03)

    def test_rejects_a_nonsense_rate(self):
        with self.assertRaises(ValueError):
            StripeRateLimiter(rate_per_second=0)


class GuardedCallRateLimitTests(TestCase):
    """The limiter lives inside guarded_call because that is already the
    only sanctioned path to Stripe. It must not weaken the key guard."""

    def tearDown(self):
        set_rate_limiter(None)

    def test_limiter_is_consulted_on_a_permitted_call(self):
        limiter = MagicMock()
        set_rate_limiter(limiter)
        target = MagicMock(return_value="ok")

        with patch("billing.stripe_live_qa.stripe") as mock_stripe:
            mock_stripe.api_key = TEST_KEY
            with override_settings(STRIPE_SECRET_KEY=TEST_KEY):
                self.assertEqual(guarded_call(target), "ok")

        limiter.acquire.assert_called_once()

    def test_refused_call_never_consumes_a_token(self):
        """Refuse first, throttle second. A call that must not happen must
        not block on a rate limit either."""
        limiter = MagicMock()
        set_rate_limiter(limiter)
        target = MagicMock()

        with patch("billing.stripe_live_qa.stripe") as mock_stripe:
            mock_stripe.api_key = LIVE_KEY
            with override_settings(STRIPE_SECRET_KEY=LIVE_KEY):
                with self.assertRaises(LiveQARefused):
                    guarded_call(target)

        limiter.acquire.assert_not_called()
        target.assert_not_called()


# --------------------------------------------------------------------------
# Worker pool
# --------------------------------------------------------------------------


class WorkerPoolTests(TestCase):
    def test_results_are_returned_in_submission_order(self):
        pool = LiveQAWorkerPool(max_workers=4)
        results = pool.run([(f"item{i}", (lambda n=i: n)) for i in range(6)])
        self.assertEqual([r.label for r in results], [f"item{i}" for i in range(6)])
        self.assertEqual([r.value for r in results], list(range(6)))

    def test_one_failing_item_does_not_stop_the_others(self):
        """A single actor blowing up must not discard the other eleven
        actors' hours of accumulated clock advances."""

        def boom():
            raise ValueError("kaboom")

        pool = LiveQAWorkerPool(max_workers=3)
        results = pool.run([("good1", lambda: 1), ("bad", boom), ("good2", lambda: 2)])

        self.assertEqual([r.ok for r in results], [True, False, True])
        self.assertIsInstance(results[1].error, ValueError)

    def test_exceptions_are_captured_not_raised(self):
        pool = LiveQAWorkerPool(max_workers=2)
        results = pool.run([("bad", lambda: 1 / 0)])
        self.assertIsInstance(results[0].error, ZeroDivisionError)

    def test_items_not_started_before_the_deadline_are_reported_not_dropped(self):
        """Silent truncation reads as 'we covered everything' when we did
        not."""
        pool = LiveQAWorkerPool(max_workers=1, deadline=Deadline(budget_seconds=0))
        results = pool.run([("a", lambda: 1), ("b", lambda: 2)])

        self.assertEqual(len(results), 2)
        for result in results:
            self.assertIsInstance(result.error, TimeoutError)

    def test_active_count_returns_to_zero(self):
        """The quiescence barrier asserts on this before patching
        process-global state."""
        pool = LiveQAWorkerPool(max_workers=3)
        pool.run([("a", lambda: 1), ("b", lambda: 2)])
        self.assertEqual(pool.active_count, 0)

    def test_empty_work_list_is_a_no_op(self):
        self.assertEqual(LiveQAWorkerPool().run([]), [])

    def test_worker_db_connections_closes_on_the_way_out(self):
        with patch("billing.live_qa.concurrency.connections") as mock_connections:
            with worker_db_connections():
                pass
        mock_connections.close_all.assert_called_once()

    def test_worker_db_connections_closes_even_when_the_body_raises(self):
        with patch("billing.live_qa.concurrency.connections") as mock_connections:
            with self.assertRaises(ValueError):
                with worker_db_connections():
                    raise ValueError("boom")
        mock_connections.close_all.assert_called_once()


# --------------------------------------------------------------------------
# Event bus
# --------------------------------------------------------------------------


class EventBusRoutingTests(TestCase):
    def setUp(self):
        self.bus = EventBus(log_prefix="[TEST]")

    def test_routes_events_to_the_owning_customer(self):
        stream_a = self.bus.register("cus_a")
        stream_b = self.bus.register("cus_b")

        self.bus.publish(
            [make_event("evt_1", "cus_a", 100), make_event("evt_2", "cus_b", 101)]
        )

        self.assertEqual(stream_a.queue.qsize(), 1)
        self.assertEqual(stream_b.queue.qsize(), 1)

    def test_events_for_unknown_customers_are_counted_as_foreign(self):
        """A nonzero count is the honest signal that the Stripe test
        account is shared with another run."""
        self.bus.register("cus_mine")

        self.bus.publish(
            [
                make_event("evt_1", "cus_mine", 100),
                make_event("evt_2", "cus_other", 101),
            ]
        )

        self.assertEqual(self.bus.stats.events_routed, 1)
        self.assertEqual(self.bus.stats.foreign_events_seen, 1)

    def test_events_with_no_customer_are_foreign_not_crashes(self):
        self.bus.register("cus_mine")
        self.bus.publish([{"id": "evt_x", "type": "ping", "data": {"object": {}}}])
        self.assertEqual(self.bus.stats.foreign_events_seen, 1)

    def test_registering_twice_returns_the_same_stream(self):
        self.assertIs(self.bus.register("cus_a"), self.bus.register("cus_a"))

    def test_unregistered_customer_has_no_stream(self):
        self.bus.register("cus_a")
        self.bus.unregister("cus_a")
        self.assertIsNone(self.bus.stream_for("cus_a"))

    def test_unhealthy_bus_raises_infrastructure_error_not_a_billing_failure(self):
        self.bus.mark_failed(RuntimeError("poller died"))

        self.assertFalse(self.bus.healthy)
        with self.assertRaises(LiveQAInfrastructureError):
            self.bus.raise_if_unhealthy()


# --------------------------------------------------------------------------
# Poller / cursor
# --------------------------------------------------------------------------


@enabled()
class AccountEventPollerTests(TestCase):
    def setUp(self):
        patcher = patch("billing.stripe_live_qa.stripe")
        self.guard_stripe = patcher.start()
        self.addCleanup(patcher.stop)
        self.guard_stripe.api_key = TEST_KEY

        patcher2 = patch("billing.live_qa.events.stripe")
        self.mock_stripe = patcher2.start()
        self.addCleanup(patcher2.stop)

        self.bus = EventBus(log_prefix="[TEST]")
        self.poller = AccountEventPoller(self.bus, created_floor=0)

    def test_publishes_events_oldest_first(self):
        """Handlers assume subscription.created is seen before the invoice
        that follows it; Stripe returns newest-first."""
        self.bus.register("cus_a")
        self.mock_stripe.Event.list.return_value = page(
            [
                make_event("evt_c", "cus_a", 300),
                make_event("evt_a", "cus_a", 100),
                make_event("evt_b", "cus_a", 200),
            ]
        )

        published = self.poller.poll_once()

        self.assertEqual([e["id"] for e in published], ["evt_a", "evt_b", "evt_c"])

    def test_same_second_events_are_ordered_deterministically(self):
        self.bus.register("cus_a")
        self.mock_stripe.Event.list.return_value = page(
            [make_event("evt_z", "cus_a", 100), make_event("evt_a", "cus_a", 100)]
        )

        published = self.poller.poll_once()

        self.assertEqual([e["id"] for e in published], ["evt_a", "evt_z"])

    def test_already_seen_events_are_not_republished(self):
        self.bus.register("cus_a")
        self.mock_stripe.Event.list.return_value = page(
            [make_event("evt_1", "cus_a", 100)]
        )

        self.assertEqual(len(self.poller.poll_once()), 1)
        self.assertEqual(len(self.poller.poll_once()), 0)

    def test_cursor_stops_paging_at_the_first_already_seen_event(self):
        """The list is newest-first, so the first seen id means everything
        below it is seen too — paging further is wasted rate limit."""
        self.bus.register("cus_a")
        self.poller.seen_ids.add("evt_old")

        self.mock_stripe.Event.list.return_value = page(
            [make_event("evt_new", "cus_a", 200), make_event("evt_old", "cus_a", 100)],
            has_more=True,
        )

        published = self.poller.poll_once()

        self.assertEqual([e["id"] for e in published], ["evt_new"])
        self.assertEqual(self.mock_stripe.Event.list.call_count, 1)

    def test_falling_behind_the_cursor_is_an_error_not_a_warning(self):
        """With a live cursor, exhausting the page budget means events
        have genuinely been missed — so any result is unreliable and the
        run must say so rather than continue."""
        self.bus.register("cus_a")
        self.poller.max_pages = 3

        # Every page carries DISTINCT unseen events, so the cursor never
        # catches up — which is what a genuinely backlogged account looks
        # like. (Repeating one event would let it catch up on page two.)
        counter = {"n": 0}

        def endless_pages(**kwargs):
            counter["n"] += 1
            return page(
                [make_event(f"evt_{counter['n']}", "cus_a", 100 + counter["n"])],
                has_more=True,
            )

        self.mock_stripe.Event.list.side_effect = endless_pages

        with self.assertRaises(LiveQAInfrastructureError) as ctx:
            self.poller.poll_once()

        self.assertIn("fell behind", str(ctx.exception))
        self.assertEqual(self.mock_stripe.Event.list.call_count, 3)

    def test_created_floor_advances_with_overlap(self):
        """Overlap is generous on purpose: a re-seen event is free (the
        seen-id set discards it), a missed one is unrecoverable."""
        self.bus.register("cus_a")
        self.mock_stripe.Event.list.return_value = page(
            [make_event("evt_1", "cus_a", 100_000)]
        )

        self.poller.poll_once()

        self.assertEqual(
            self.poller.created_floor,
            100_000 - AccountEventPoller.OVERLAP_SECONDS,
        )

    def test_poller_death_marks_the_bus_unhealthy(self):
        self.mock_stripe.Event.list.side_effect = RuntimeError("stripe down")
        self.poller.poll_interval = 0

        self.poller.run()  # runs inline; exits on the failure

        self.assertFalse(self.bus.healthy)
        with self.assertRaises(LiveQAInfrastructureError):
            self.bus.raise_if_unhealthy()


# --------------------------------------------------------------------------
# Per-customer stream / dispatch
# --------------------------------------------------------------------------


class CustomerEventStreamTests(TestCase):
    def setUp(self):
        self.bus = EventBus(log_prefix="[TEST]")
        self.stream = self.bus.register("cus_a")
        self.stream.POLL_INTERVAL = 0
        self.stream.QUIET_POLLS = 1

    def test_dispatches_through_the_real_webhook_path(self):
        """Not by calling handlers directly — going through
        _record_and_dispatch is what exercises the C3 ledger."""
        self.stream.put(make_event("evt_1", "cus_a", 100))
        response = MagicMock(status_code=200)

        with patch(
            "billing.webhooks._record_and_dispatch", return_value=response
        ) as dispatch:
            dispatched = self.stream.drain()

        dispatch.assert_called_once()
        self.assertEqual(dispatched, [("invoice.payment_succeeded", 200)])

    def test_the_same_event_is_never_dispatched_twice(self):
        event = make_event("evt_dup", "cus_a", 100)
        self.stream.put(event)
        self.stream.put(event)

        with patch(
            "billing.webhooks._record_and_dispatch",
            return_value=MagicMock(status_code=200),
        ) as dispatch:
            self.stream.drain()

        self.assertEqual(dispatch.call_count, 1)

    def test_event_is_marked_dispatched_before_dispatch_so_a_raise_cannot_loop(self):
        self.stream.put(make_event("evt_boom", "cus_a", 100))

        with patch(
            "billing.webhooks._record_and_dispatch",
            side_effect=RuntimeError("handler exploded"),
        ):
            with self.assertRaises(RuntimeError):
                self.stream.drain()

        self.assertIn("evt_boom", self.stream.dispatched_event_ids)

    def test_drain_fails_fast_when_the_poller_is_dead(self):
        """Otherwise every actor blocks until timeout and the run looks
        like a billing bug rather than an infrastructure one."""
        self.bus.mark_failed(RuntimeError("poller died"))

        with self.assertRaises(LiveQAInfrastructureError):
            self.stream.drain()


# --------------------------------------------------------------------------
# Concurrent harness
# --------------------------------------------------------------------------


@enabled()
class ConcurrentHarnessTests(TestCase):
    def setUp(self):
        patcher = patch("billing.stripe_live_qa.stripe")
        self.mock_stripe = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_stripe.api_key = TEST_KEY
        self.mock_stripe.error.InvalidRequestError = Exception
        self.harness = ConcurrentLiveQAHarness(run_id="conc1")

    def test_create_customer_registers_with_the_bus(self):
        self.mock_stripe.Customer.create.return_value = {"id": "cus_new"}

        self.harness.create_customer(email="x@y.invalid", clock_id="clock_1")

        self.assertIsNotNone(self.harness.bus.stream_for("cus_new"))

    def test_drain_events_merges_ids_so_cleanup_removes_ledger_rows(self):
        self.mock_stripe.Customer.create.return_value = {"id": "cus_new"}
        self.harness.create_customer(email="x@y.invalid", clock_id="clock_1")

        # register() returns the already-registered stream, and unlike
        # stream_for() it is not Optional.
        stream = self.harness.bus.register("cus_new")
        stream.POLL_INTERVAL = 0
        stream.QUIET_POLLS = 1
        stream.put(make_event("evt_1", "cus_new", 100))

        with patch(
            "billing.webhooks._record_and_dispatch",
            return_value=MagicMock(status_code=200),
        ):
            self.harness.drain_events(customer_id="cus_new")

        self.assertIn("evt_1", self.harness.dispatched_event_ids)

    def test_cleanup_collects_ids_from_streams_that_never_drained(self):
        """A scenario that raised part-way through still produced ledger
        rows, and those still have to go."""
        stream = self.harness.bus.register("cus_orphan")
        stream.dispatched_event_ids.add("evt_orphan")

        self.harness.cleanup()

        self.assertIn("evt_orphan", self.harness.dispatched_event_ids)

    def test_draining_an_unregistered_customer_is_an_infrastructure_error(self):
        """Its events would be silently discarded as foreign, so failing
        loudly is the only safe answer."""
        with self.assertRaises(LiveQAInfrastructureError):
            self.harness.drain_events(customer_id="cus_never_registered")


# --------------------------------------------------------------------------
# Concurrent runner
# --------------------------------------------------------------------------


@enabled()
class RunSuiteConcurrentlyTests(TestCase):
    def setUp(self):
        patcher = patch("billing.stripe_live_qa.stripe")
        self.mock_stripe = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_stripe.api_key = TEST_KEY
        self.mock_stripe.error.InvalidRequestError = Exception

        # Keep the poller inert: these tests are about orchestration.
        poll_patcher = patch("billing.live_qa.events.stripe")
        self.poll_stripe = poll_patcher.start()
        self.addCleanup(poll_patcher.stop)
        self.poll_stripe.Event.list.return_value = page([])

        self.addCleanup(set_rate_limiter, None)

    def _run(self, scenarios, **kwargs):
        from billing import stripe_live_qa_scenarios as mod
        from billing.live_qa.runner import run_suite_concurrently

        with patch.dict(mod.SCENARIOS, scenarios, clear=True):
            return run_suite_concurrently(**kwargs)

    def test_rejects_unknown_scenario_names(self):
        from billing.live_qa.runner import run_suite_concurrently
        from billing.stripe_live_qa import LiveQAConfigurationError

        with self.assertRaises(LiveQAConfigurationError):
            run_suite_concurrently(["nope"])

    def test_runs_every_scenario_and_reports_each(self):
        from billing.stripe_live_qa import CheckRecorder

        def passing(harness):
            rec = CheckRecorder()
            rec.expect("ok", True)
            return rec

        result = self._run({"a": passing, "b": passing})

        self.assertEqual(len(result.scenarios), 2)
        self.assertTrue(result.passed)

    def test_a_raising_scenario_does_not_stop_the_others(self):
        from billing.stripe_live_qa import CheckRecorder

        def boom(harness):
            raise RuntimeError("scenario exploded")

        def fine(harness):
            return CheckRecorder()

        result = self._run({"boom": boom, "fine": fine})

        by_name = {s.name: s for s in result.scenarios}
        self.assertFalse(by_name["boom"].passed)
        self.assertIn("scenario exploded", by_name["boom"].error)
        self.assertTrue(by_name["fine"].passed)

    def test_cleanup_runs_even_when_a_scenario_raises(self):
        def boom(harness):
            harness.clock_ids.append("clock_leak")
            raise RuntimeError("boom")

        self._run({"boom": boom})

        self.mock_stripe.test_helpers.TestClock.delete.assert_called_once_with(
            "clock_leak"
        )

    def test_keep_objects_skips_cleanup(self):
        from billing.stripe_live_qa import CheckRecorder

        def scenario(harness):
            harness.clock_ids.append("clock_keep")
            return CheckRecorder()

        self._run({"s": scenario}, keep_objects=True)

        self.mock_stripe.test_helpers.TestClock.delete.assert_not_called()

    def test_rate_limiter_is_uninstalled_after_the_run(self):
        """It is process-global; leaving it installed would throttle
        every later Stripe call in the process."""
        import billing.stripe_live_qa as base
        from billing.stripe_live_qa import CheckRecorder

        self._run({"s": lambda h: CheckRecorder()})

        self.assertIsNone(base._rate_limiter)

    def test_event_bus_stats_are_reported_as_a_note(self):
        from billing.stripe_live_qa import CheckRecorder

        result = self._run({"s": lambda h: CheckRecorder()})

        self.assertTrue(any("event bus" in note for note in result.notes))

    def test_notes_do_not_make_a_passing_run_fail(self):
        """Notes are facts about how far a run got, not defects."""
        from billing.stripe_live_qa import CheckRecorder

        result = self._run({"s": lambda h: CheckRecorder()})

        self.assertTrue(result.notes)
        self.assertTrue(result.passed)
