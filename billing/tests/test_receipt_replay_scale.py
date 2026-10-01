"""
Gate 6 (stress / scale) for the two hourly tasks H-23 adds.

Both run unattended against tables that only ever grow:
BillingTransaction gains a row per purchase, and StripeEvent a row per
webhook, and StripeEvent rows are never deleted. A task whose cost grows
with the table would get slower every hour until it overlapped itself, so
each is measured at two sizes 10x apart and must do the SAME number of
queries at both, using an index rather than a sequential scan.

The background volume is bulk-inserted with exactly the fields production
writes (BillingTransactionService.record's defaults; the webhook claim's
StripeEvent fields), because creating tens of thousands of rows one
service call at a time would take minutes and prove nothing extra. The
rows the tasks actually act on are created through the production paths.

Measurements (query count, wall time p50/p95, peak Python memory, query
plan) are printed so the evidence log records them, and the query-count
and index-use properties are asserted.
"""

import statistics
import time
import tracemalloc
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from billing import event_replay, receipts
from billing.billing_transaction_service import BillingTransactionService
from billing.models import (
    BillingTransaction,
    BillingTransactionMethod,
    BillingTransactionSource,
    BillingTransactionStatus,
    BillingTransactionType,
    StripeEvent,
    StripeEventStatus,
)
from billing.tests.testing_fake_stripe import fake_stripe

SMALL = 2_000
LARGE = 20_000
ACTIONABLE = 25
REPEATS = 15


def _background_transactions(count, now):
    """Filled-in historical purchases: the table the sweep has to ignore."""
    BillingTransaction.objects.bulk_create(
        [
            BillingTransaction(
                source=BillingTransactionSource.INDIVIDUAL,
                transaction_type=BillingTransactionType.INDIVIDUAL_OVERAGE_PURCHASE,
                status=BillingTransactionStatus.PAID,
                billing_method=BillingTransactionMethod.STRIPE,
                amount_cents=1000,
                currency="usd",
                stripe_payment_intent_id=f"pi_bg_{i}",
                receipt_url=f"https://pay.stripe.test/receipts/bg_{i}",
                description="background",
                metadata={},
                # Two years of history: the sweep's 3-day window is a small
                # slice of the table, as it is in production.
                occurred_at=now - timedelta(days=i % 730, minutes=10),
            )
            for i in range(count)
        ],
        batch_size=2_000,
    )


def _actionable_transactions(now):
    """Purchases still missing a link, recorded the way production records them."""
    for i in range(ACTIONABLE):
        BillingTransactionService.record(
            source=BillingTransactionSource.INDIVIDUAL,
            transaction_type=BillingTransactionType.INDIVIDUAL_OVERAGE_PURCHASE,
            status=BillingTransactionStatus.PAID,
            billing_method=BillingTransactionMethod.STRIPE,
            amount_cents=1000,
            stripe_payment_intent_id=f"pi_need_{i}",
            description="needs a link",
            occurred_at=now - timedelta(minutes=30),
        )


def _background_events(count):
    StripeEvent.objects.bulk_create(
        [
            StripeEvent(
                stripe_event_id=f"evt_bg_{i}",
                event_type="checkout.session.completed",
                payload={"object": {"metadata": {"flow": "individual_checkout"}}},
                status=StripeEventStatus.SUCCEEDED,
                claimed_at=timezone.now(),
                completed_at=timezone.now(),
                attempts=1,
            )
            for i in range(count)
        ],
        batch_size=2_000,
    )


def _analyze(table):
    # Plans come from statistics; a freshly bulk-loaded table has none.
    with connection.cursor() as cur:
        cur.execute(f"ANALYZE {table}")


def _explain(sql, params):
    with connection.cursor() as cur:
        cur.execute("EXPLAIN " + sql, params)
        return "\n".join(row[0] for row in cur.fetchall())


def _timed(fn):
    samples = []
    for _ in range(REPEATS):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return statistics.median(samples), samples[int(len(samples) * 0.95) - 1]


class SweepMissingReceiptUrlsScaleTests(TestCase):
    def measure(self, background):
        now = timezone.now()
        _background_transactions(background, now)
        _actionable_transactions(now)
        _analyze("billing_billingtransaction")

        candidate_sql, params = (
            BillingTransaction.objects.filter(receipts._MISSING_RECEIPT)
            .filter(receipts._HAS_STRIPE_REFERENCE)
            .filter(
                occurred_at__gte=now - receipts.RECEIPT_SWEEP_WINDOW,
                occurred_at__lte=now - receipts.RECEIPT_SWEEP_MIN_AGE,
            )
            .order_by("-occurred_at")
            .values_list("pk", flat=True)[: receipts.RECEIPT_SWEEP_BATCH_SIZE]
            .query.sql_with_params()
        )
        plan = _explain(candidate_sql, params)

        tracemalloc.start()
        with fake_stripe(), CaptureQueriesContext(connection) as queries:
            counts = receipts.sweep_missing_receipt_urls(now=now)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # Timing: selection only, repeated, now that everything is filled.
        p50, p95 = _timed(lambda: receipts.sweep_missing_receipt_urls(now=now))

        print(
            f"\n[G6 sweep] background={background} actionable={ACTIONABLE} "
            f"filled={counts['filled']} queries={len(queries)} "
            f"peak_mem_kb={peak // 1024} idle_p50_ms={p50:.2f} idle_p95_ms={p95:.2f}"
            f"\n[G6 sweep plan background={background}]\n{plan}"
        )
        return counts, len(queries), plan

    def test_query_count_does_not_grow_with_the_table(self):
        small_counts, small_queries, _ = self.measure(SMALL)
        BillingTransaction.objects.all().delete()
        large_counts, large_queries, large_plan = self.measure(LARGE)

        self.assertEqual(small_counts["filled"], ACTIONABLE)
        self.assertEqual(large_counts["filled"], ACTIONABLE)
        self.assertEqual(
            small_queries,
            large_queries,
            f"the sweep's query count grew with the table: "
            f"{small_queries} at {SMALL} rows, {large_queries} at {LARGE}",
        )
        self.assertNotIn(
            "Seq Scan on billing_billingtransaction",
            large_plan,
            "the hourly sweep scans the whole BillingTransaction table",
        )


class ReplaySafeFailedEventsScaleTests(TestCase):
    def measure(self, background):
        _background_events(background)
        for i in range(5):
            StripeEvent.objects.create(
                stripe_event_id=f"evt_failed_{background}_{i}",
                event_type="charge.refunded",
                payload={"object": {}},
                status=StripeEventStatus.FAILED,
            )
        _analyze("billing_stripeevent")

        selection = (
            StripeEvent.objects.filter(
                status=StripeEventStatus.FAILED,
                event_type__in={t for t, _ in event_replay.AUTO_REPLAYABLE},
                auto_replay_attempts__lt=event_replay.MAX_AUTO_REPLAY_ATTEMPTS,
            )
            .order_by("processed_at")
            .values_list("pk", flat=True)[: event_replay.AUTO_REPLAY_BATCH_SIZE]
        )
        plan = _explain(*selection.query.sql_with_params())

        with CaptureQueriesContext(connection) as queries:
            event_replay.replay_safe_failed_events()
        p50, p95 = _timed(event_replay.replay_safe_failed_events)

        print(
            f"\n[G6 replay] background={background} queries={len(queries)} "
            f"p50_ms={p50:.2f} p95_ms={p95:.2f}"
            f"\n[G6 replay plan background={background}]\n{plan}"
        )
        return len(queries), plan

    def test_selection_cost_does_not_grow_with_the_table(self):
        small_queries, _ = self.measure(SMALL)
        StripeEvent.objects.all().delete()
        large_queries, large_plan = self.measure(LARGE)

        self.assertEqual(small_queries, large_queries)
        self.assertNotIn(
            "Seq Scan on billing_stripeevent",
            large_plan,
            "the hourly replay selection scans the whole StripeEvent table",
        )
