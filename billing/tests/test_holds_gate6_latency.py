"""Epic B1a step 9, Gate 6 (plan 08 §9 item 13; NOTES §21d).

M = 500 billed single calls with the provider stubbed, flag off and then on,
on one database. Reports the added wall time per call (p50, p95), reserve
and settle on their own, bytes per AIJob and SettlementRecord row, and the
storage a month of busy days would take. Pass: added p95 <= 5 ms.

Opt-in like the repo's other load tests: set RUN_LOAD_TESTS=1.
"""

import os
import statistics
import time
import unittest
from unittest.mock import patch

from django.db import connection
from django.test import TestCase, override_settings

from ai_processor.services import AIProcessor
from audit.models import AuditEvent
from audit.volume import PER_TEACHER_DAY
from billing.models import AIJob, SettlementRecord
from billing.testing.holds import (
    ai_ready_teacher,
    ai_response,
    call_ai,
    estimate_for,
    holds,
)

LOAD_TESTS_ENABLED = os.environ.get("RUN_LOAD_TESTS") == "1"
PROVIDER = "_AIProcessor__ai_model"
CALLS = 500
WARM_UP = 20
SCHOOL_DAYS_PER_MONTH = 22
TEACHERS = 100
PASS_P95_ADDED_MS = 5.0


def p50(values):
    return statistics.median(values)


def p95(values):
    return statistics.quantiles(values, n=100)[94]


def ms(seconds):
    return seconds * 1000.0


def bytes_per_row(model):
    rows = model.objects.count()
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_total_relation_size(%s)", [model._meta.db_table])
        (size,) = cursor.fetchone()
    return size / rows if rows else 0.0


def timed(real, into):
    def wrapper(*args, **kwargs):
        started = time.perf_counter()
        try:
            return real(*args, **kwargs)
        finally:
            into.append(time.perf_counter() - started)

    return wrapper


@unittest.skipUnless(LOAD_TESTS_ENABLED, "load tests are opt-in: set RUN_LOAD_TESTS=1")
class Gate6LatencyTests(TestCase):
    def calls(self, teacher):
        times = []
        for index in range(WARM_UP + CALLS):
            started = time.perf_counter()
            call_ai(teacher)
            if index >= WARM_UP:
                times.append(time.perf_counter() - started)
        return times

    def test_gate6_holds_add_at_most_5ms_per_call_at_p95(self):
        teacher = ai_ready_teacher("g6@example.com", 1200 * estimate_for())
        module = holds()
        reserve_times: list = []
        settle_times: list = []
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(150)):
            with override_settings(BILLING_HOLDS_ENABLED=False):
                off = self.calls(teacher)
            with override_settings(BILLING_HOLDS_ENABLED=True), patch.object(
                module,
                "begin_single_call",
                timed(module.begin_single_call, reserve_times),
            ), patch.object(
                module,
                "finish_single_call",
                timed(module.finish_single_call, settle_times),
            ):
                on = self.calls(teacher)

        reserve_times = reserve_times[WARM_UP:]
        settle_times = settle_times[WARM_UP:]
        added_p50 = ms(p50(on) - p50(off))
        added_p95 = ms(p95(on) - p95(off))
        job_bytes = bytes_per_row(AIJob)
        settlement_bytes = bytes_per_row(SettlementRecord)
        audit_bytes = bytes_per_row(AuditEvent)
        gradings = PER_TEACHER_DAY["CREDIT_TRANSACTION"]
        per_month = (
            TEACHERS
            * gradings
            * SCHOOL_DAYS_PER_MONTH
            * (job_bytes + settlement_bytes + 2 * audit_bytes)
        )
        print(
            "\nGATE6 calls={calls} off_p50_ms={a:.3f} off_p95_ms={b:.3f} "
            "on_p50_ms={c:.3f} on_p95_ms={d:.3f} added_p50_ms={e:.3f} "
            "added_p95_ms={f:.3f} reserve_p50_ms={g:.3f} reserve_p95_ms={h:.3f} "
            "settle_p50_ms={i:.3f} settle_p95_ms={j:.3f} aijob_bytes_per_row={k:.1f} "
            "settlement_bytes_per_row={m:.1f} audit_bytes_per_row={n:.1f} "
            "storage_per_month_per_{t}_teachers_MB={s:.1f}".format(
                calls=CALLS,
                a=ms(p50(off)),
                b=ms(p95(off)),
                c=ms(p50(on)),
                d=ms(p95(on)),
                e=added_p50,
                f=added_p95,
                g=ms(p50(reserve_times)),
                h=ms(p95(reserve_times)),
                i=ms(p50(settle_times)),
                j=ms(p95(settle_times)),
                k=job_bytes,
                m=settlement_bytes,
                n=audit_bytes,
                t=TEACHERS,
                s=per_month / 1_000_000,
            )
        )
        self.assertEqual(len(on), CALLS)
        self.assertEqual(len(reserve_times), CALLS)
        self.assertEqual(len(settle_times), CALLS)
        self.assertLessEqual(added_p95, PASS_P95_ADDED_MS)
